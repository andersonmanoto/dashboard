import asyncio
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../app")))

import httpx
from models.enums import NetworkType
from services.aweber_service import (
    AWeberAPI,
    build_order_subscriber,
)
from services.event_processor import EventProcessor
from services.normalizer import PayloadNormalizer

from tests.test_normalizer import (
    PAYLOAD_BUYGOODS_REAL,
    PAYLOAD_DIGISTORE_SALE,
    PAYLOAD_PAGAMERICAN_PURCHASE,
)

SODAHORSE_ID = "f0494709-01fb-4d0e-8e8c-3e200f25b13c"
SODAHORSE_LISTS = {"neworder_list_id": "6977002", "abandon_list_id": "6977004"}

ABANDON_BODY = {
    "_pa_event": "checkout.session.abandoned.v-1.0.0",
    "offerCode": "cEy6BQCE",
    "checkoutForm": {
        "email": "Jane.Doe@Example.com",
        "firstName": "Jane",
        "lastName": "Doe",
        "phone": "5551234567",
        "country": "US",
    },
}

# Abandono da BuyGoods como chega na rota /buygoods/abandon (form flat)
BUYGOODS_ABANDON = {
    "action_type": "abandon",
    "name": "John Smith",
    "emailaddress": "John.Smith@Example.com",
    "phone": "5559876543",
    "ipaddress": "8.8.4.4",
    "product_codename": "sohp_front_6b_fnn1",
    "account_id": "13582",
    "country": "United States",
}

# Abandono da DigiStore24 ("Input of contact details on order forms") como
# chega na rota /digistore24/abandon
DIGISTORE_ABANDON = {
    "event": "on_payment_missed",
    "order_id": "CART-123",
    "email": "Ana.Lima@Example.com",
    "first_name": "Ana",
    "last_name": "Lima",
    "phone_no": "5551112222",
    "country_code": "US",
    "product_id": "753030",
    "product_name": "SodaHorsePeak",
}


def _order_event(**customer_overrides):
    payload = {
        **PAYLOAD_PAGAMERICAN_PURCHASE,
        "customer": {**PAYLOAD_PAGAMERICAN_PURCHASE["customer"], **customer_overrides},
    }
    return PayloadNormalizer().normalize(NetworkType.PAGAMERICAN, payload)


def _buygoods_event(funnel_stage="Purchase", **overrides):
    event = PayloadNormalizer().normalize(
        NetworkType.BUYGOODS, {**PAYLOAD_BUYGOODS_REAL, **overrides}
    )
    event.funnel_stage = funnel_stage  # vem do _enrich_checkout no fluxo real
    return event


def _digistore_event(funnel_stage="Purchase", **overrides):
    payload = {
        **PAYLOAD_DIGISTORE_SALE,
        "transaction_type": "payment",
        "is_test_payment": "false",
        "product_id": "753030",
        **overrides,
    }
    event = PayloadNormalizer().normalize(NetworkType.DIGISTORE24, payload)
    event.funnel_stage = funnel_stage
    return event


def test_subscriber_da_venda():
    assert build_order_subscriber(_order_event()) == {
        "email": "jane.doe@example.com",
        "update_existing": "true",
        "name": "Jane Doe",
        "tags": ["pagamerican", "offer-cEy6BQCE"],
        # utm_source, não o src (clickid)
        "ad_tracking": "YouTube-GH",
    }


def test_subscriber_da_venda_buygoods():
    assert build_order_subscriber(_buygoods_event()) == {
        "email": "louigi272@gmail.com",
        "update_existing": "true",
        "name": "Tamara Giblin",
        "tags": ["buygoods", "offer-abc9d"],
        "ip_address": "66.249.84.142",
    }


def test_subscriber_da_venda_digistore24():
    assert build_order_subscriber(_digistore_event(utm_source="fb")) == {
        "email": "maria@email.com",
        "update_existing": "true",
        "name": "Maria Pereira",
        "tags": ["digistore24", "offer-753030"],
        "ad_tracking": "fb",
    }
    assert "ad_tracking" not in build_order_subscriber(_digistore_event(utm_source=""))


def test_digistore24_so_front_neworder_ao_vivo_vira_lead():
    ok = EventProcessor._is_aweber_order
    assert ok(_digistore_event())
    assert not ok(_digistore_event(funnel_stage="Up1"))
    # checkout não cadastrado: sem funnel_stage, não manda
    assert not ok(_digistore_event(funnel_stage=None))
    assert not ok(_digistore_event(transaction_type="refund"))
    assert not ok(_digistore_event(transaction_type="chargeback"))
    assert not ok(_digistore_event(is_test_payment="true"))


def test_ip_so_se_for_publico():
    assert build_order_subscriber(_order_event(ip="8.8.8.8"))["ip_address"] == "8.8.8.8"
    assert "ip_address" not in build_order_subscriber(_order_event(ip="10.0.0.1"))


def test_venda_sem_email_nao_gera_subscriber():
    assert build_order_subscriber(_order_event(email="")) is None
    assert build_order_subscriber(_buygoods_event(customer_emailaddress="")) is None


def test_buygoods_so_front_neworder_ao_vivo_vira_lead():
    ok = EventProcessor._is_aweber_order
    assert ok(_buygoods_event())
    # upsell/downsell: mesma pessoa do front
    assert not ok(_buygoods_event(funnel_stage="Up1"))
    # refund/chargeback chegam como linha "Purchase" com outro action_type
    assert not ok(_buygoods_event(action_type="refund"))
    assert not ok(_buygoods_event(action_type="chargeback"))
    assert not ok(_buygoods_event(is_test="1"))
    # PagAmerican segue mandando toda venda nova
    assert ok(_order_event())


class FakeCheckout:
    def __init__(self, product_id):
        self.product_id = product_id


class FakeDB:
    def __init__(self, tokens=None, lists=None):
        self.tokens = tokens
        self.lists = lists or {}  # product_id -> listas
        self.saved = None
        self.leads = []
        self.checkout_lookups = []
        self.carts = []
        self.lead_origins = []

    def insert_abandoned_cart(self, cart_data):
        self.carts.append(cart_data)
        return {"id": "cart-1"}

    def get_network_id(self, network):
        return f"net-{network.value}"

    def get_checkout_by_code(self, code, account_id=None):
        self.checkout_lookups.append((code, account_id))
        if code in ("cEy6BQCE", "sohp_front_6b_fnn1", "753030"):
            return FakeCheckout(SODAHORSE_ID)
        return None

    def get_aweber_list_ids(self, product_id):
        return self.lists.get(str(product_id))

    def enqueue_aweber_lead(
        self, lead_type, list_id, subscriber, abandoned_cart_id=None, event_id=None
    ):
        self.leads.append((lead_type, list_id, subscriber))
        self.lead_origins.append((abandoned_cart_id, event_id))
        return {"id": "q-1"}

    def get_aweber_tokens(self):
        return self.tokens

    def save_aweber_tokens(self, access_token, refresh_token, expires_at):
        self.saved = (access_token, refresh_token)
        self.tokens = {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "expires_at": expires_at.isoformat(),
        }


def test_abandono_vai_pra_lista_de_carrinho_do_produto():
    db = FakeDB(lists={SODAHORSE_ID: SODAHORSE_LISTS})
    processor = EventProcessor(db, zapier_webhook_enabled=False, aweber_enabled=True)
    assert asyncio.run(processor.process_pagamerican_abandon_cart(ABANDON_BODY))
    assert db.leads == [
        (
            "abandon",
            "6977004",
            {
                "email": "jane.doe@example.com",
                "update_existing": "true",
                "name": "Jane Doe",
                "tags": ["pagamerican", "offer-cEy6BQCE"],
            },
        )
    ]
    # carrinho salvo com produto e rede; lead da fila aponta pro carrinho
    assert db.carts[0]["product_id"] == SODAHORSE_ID
    assert db.carts[0]["network_id"] == "net-PagAmerican"
    assert db.lead_origins == [("cart-1", None)]


def test_abandono_buygoods_vai_pra_lista_de_carrinho_do_produto():
    db = FakeDB(lists={SODAHORSE_ID: SODAHORSE_LISTS})
    processor = EventProcessor(db, zapier_webhook_enabled=False, aweber_enabled=True)
    assert asyncio.run(processor.process_buygoods_abandon_cart(BUYGOODS_ABANDON))
    # checkout buscado com o account_id da BuyGoods
    assert db.checkout_lookups == [("sohp_front_6b_fnn1", "13582")]
    assert db.leads == [
        (
            "abandon",
            "6977004",
            {
                "email": "john.smith@example.com",
                "update_existing": "true",
                "name": "John Smith",
                "tags": ["buygoods", "offer-sohp_front_6b_fnn1"],
                "ip_address": "8.8.4.4",
            },
        )
    ]
    assert db.carts[0]["product_id"] == SODAHORSE_ID
    assert db.carts[0]["network_id"] == "net-BuyGoods"
    assert db.lead_origins == [("cart-1", None)]


def test_abandono_digistore24_vai_pra_lista_de_carrinho_do_produto():
    db = FakeDB(lists={SODAHORSE_ID: SODAHORSE_LISTS})
    processor = EventProcessor(db, zapier_webhook_enabled=False, aweber_enabled=True)
    assert asyncio.run(processor.process_digistore24_abandon_cart(DIGISTORE_ABANDON))
    assert db.leads == [
        (
            "abandon",
            "6977004",
            {
                "email": "ana.lima@example.com",
                "update_existing": "true",
                "name": "Ana Lima",
                "tags": ["digistore24", "offer-753030"],
            },
        )
    ]
    assert db.carts[0]["product_id"] == SODAHORSE_ID
    assert db.carts[0]["network_id"] == "net-DigiStore24"
    assert db.lead_origins == [("cart-1", None)]


def test_abandono_digistore24_nao_enfileira_com_aweber_desligado():
    db = FakeDB(lists={SODAHORSE_ID: SODAHORSE_LISTS})
    processor = EventProcessor(db, zapier_webhook_enabled=False, aweber_enabled=False)
    assert asyncio.run(processor.process_digistore24_abandon_cart(DIGISTORE_ABANDON))
    assert db.leads == []


def test_carrinho_de_codename_desconhecido_salva_sem_produto():
    db = FakeDB(lists={SODAHORSE_ID: SODAHORSE_LISTS})
    processor = EventProcessor(db, zapier_webhook_enabled=False, aweber_enabled=True)
    body = {**BUYGOODS_ABANDON, "product_codename": "nao_cadastrado"}
    assert asyncio.run(processor.process_buygoods_abandon_cart(body))
    assert "product_id" not in db.carts[0]
    assert db.carts[0]["network_id"] == "net-BuyGoods"
    assert db.leads == []


def test_venda_vai_pra_lista_new_order_do_produto():
    db = FakeDB(lists={SODAHORSE_ID: SODAHORSE_LISTS})
    processor = EventProcessor(db, aweber_enabled=True)
    processor._enqueue_aweber_lead(
        "neworder",
        SODAHORSE_ID,
        build_order_subscriber(_order_event()),
        "t",
        event_id="ev-1",
    )
    processor._enqueue_aweber_lead(
        "neworder", SODAHORSE_ID, build_order_subscriber(_buygoods_event()), "t"
    )
    assert [(t, lst) for t, lst, _ in db.leads] == [
        ("neworder", "6977002"),
        ("neworder", "6977002"),
    ]
    assert db.lead_origins == [(None, "ev-1"), (None, None)]


def test_produto_sem_listas_nao_enfileira():
    db = FakeDB(lists={})
    processor = EventProcessor(db, zapier_webhook_enabled=False, aweber_enabled=True)
    asyncio.run(processor.process_pagamerican_abandon_cart(ABANDON_BODY))
    asyncio.run(processor.process_buygoods_abandon_cart(BUYGOODS_ABANDON))
    processor._enqueue_aweber_lead(
        "neworder", "outro-produto", build_order_subscriber(_order_event()), "t"
    )
    assert db.leads == []


def test_abandono_nao_enfileira_com_aweber_desligado():
    db = FakeDB(lists={SODAHORSE_ID: SODAHORSE_LISTS})
    processor = EventProcessor(db, zapier_webhook_enabled=False)
    asyncio.run(processor.process_pagamerican_abandon_cart(ABANDON_BODY))
    asyncio.run(processor.process_buygoods_abandon_cart(BUYGOODS_ABANDON))
    assert db.leads == []


class FakeSettings:
    aweber_enabled = True
    aweber_client_id = "cid"
    aweber_client_secret = "secret"
    aweber_account_id = "acc"
    aweber_default_tags = "pagamerican"


def _tokens(expires_in_minutes):
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=expires_in_minutes)
    return {
        "access_token": "old",
        "refresh_token": "r1",
        "expires_at": expires_at.isoformat(),
    }


def _run(db, handler, subscriber):
    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            api = AWeberAPI(FakeSettings(), db)
            token = await api.get_access_token(client)
            return await api.add_subscriber(client, "6977002", subscriber, token)

    return asyncio.run(go())


def test_token_expirando_e_renovado_e_tags_padrao_mescladas():
    db = FakeDB(tokens=_tokens(expires_in_minutes=2))
    seen = {}

    def handler(request):
        if request.url.host == "auth.aweber.com":
            return httpx.Response(
                200,
                json={"access_token": "new", "refresh_token": "r2", "expires_in": 7200},
            )
        seen["url"] = str(request.url)
        seen["auth"] = request.headers["Authorization"]
        seen["body"] = request.read()
        return httpx.Response(201)

    response, token = _run(db, handler, {"email": "a@b.com", "tags": ["offer-X"]})

    assert response.status_code == 201
    assert token == "new" and db.saved == ("new", "r2")
    assert (
        seen["url"]
        == "https://api.aweber.com/1.0/accounts/acc/lists/6977002/subscribers"
    )
    assert seen["auth"] == "Bearer new"
    assert b'"tags":["pagamerican","offer-X"]' in seen["body"].replace(b" ", b"")


def test_401_renova_token_e_tenta_de_novo():
    db = FakeDB(tokens=_tokens(expires_in_minutes=60))
    calls = []

    def handler(request):
        if request.url.host == "auth.aweber.com":
            return httpx.Response(200, json={"access_token": "new", "expires_in": 7200})
        calls.append(request.headers["Authorization"])
        return httpx.Response(401 if len(calls) == 1 else 201)

    response, token = _run(db, handler, {"email": "a@b.com"})

    assert response.status_code == 201
    assert calls == ["Bearer old", "Bearer new"]
    # refresh sem refresh_token novo mantém o antigo
    assert db.saved == ("new", "r1")
