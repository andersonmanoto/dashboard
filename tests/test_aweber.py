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

from tests.test_normalizer import PAYLOAD_PAGAMERICAN_PURCHASE

SODAHORSE_ID = "f0494709-01fb-4d0e-8e8c-3e200f25b13c"
SODAHORSE_LISTS = {"neworder_list_id": "6977002", "abandon_list_id": "6966538"}

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


def _order_event(**customer_overrides):
    payload = {
        **PAYLOAD_PAGAMERICAN_PURCHASE,
        "customer": {**PAYLOAD_PAGAMERICAN_PURCHASE["customer"], **customer_overrides},
    }
    return PayloadNormalizer().normalize(NetworkType.PAGAMERICAN, payload)


def test_subscriber_da_venda():
    assert build_order_subscriber(_order_event()) == {
        "email": "jane.doe@example.com",
        "update_existing": "true",
        "name": "Jane Doe",
        "tags": ["offer-cEy6BQCE"],
        # utm_source, não o src (clickid)
        "ad_tracking": "YouTube-GH",
    }


def test_ip_so_se_for_publico():
    assert build_order_subscriber(_order_event(ip="8.8.8.8"))["ip_address"] == "8.8.8.8"
    assert "ip_address" not in build_order_subscriber(_order_event(ip="10.0.0.1"))


def test_venda_sem_email_nao_gera_subscriber():
    assert build_order_subscriber(_order_event(email="")) is None


class FakeCheckout:
    def __init__(self, product_id):
        self.product_id = product_id


class FakeDB:
    def __init__(self, tokens=None, lists=None):
        self.tokens = tokens
        self.lists = lists or {}  # product_id -> listas
        self.saved = None
        self.leads = []

    def insert_abandoned_cart(self, cart_data):
        return {"id": "cart-1"}

    def get_checkout_by_code(self, code, account_id=None):
        return FakeCheckout(SODAHORSE_ID) if code == "cEy6BQCE" else None

    def get_aweber_list_ids(self, product_id):
        return self.lists.get(str(product_id))

    def enqueue_aweber_lead(self, lead_type, list_id, subscriber):
        self.leads.append((lead_type, list_id, subscriber))
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
            "6966538",
            {
                "email": "jane.doe@example.com",
                "update_existing": "true",
                "name": "Jane Doe",
                "tags": ["offer-cEy6BQCE"],
            },
        )
    ]


def test_venda_vai_pra_lista_new_order_do_produto():
    db = FakeDB(lists={SODAHORSE_ID: SODAHORSE_LISTS})
    processor = EventProcessor(db, aweber_enabled=True)
    processor._enqueue_aweber_lead(
        "neworder", SODAHORSE_ID, build_order_subscriber(_order_event()), "t"
    )
    assert [(t, lst) for t, lst, _ in db.leads] == [("neworder", "6977002")]


def test_produto_sem_listas_nao_enfileira():
    db = FakeDB(lists={})
    processor = EventProcessor(db, zapier_webhook_enabled=False, aweber_enabled=True)
    asyncio.run(processor.process_pagamerican_abandon_cart(ABANDON_BODY))
    processor._enqueue_aweber_lead(
        "neworder", "outro-produto", build_order_subscriber(_order_event()), "t"
    )
    assert db.leads == []


def test_abandono_nao_enfileira_com_aweber_desligado():
    db = FakeDB(lists={SODAHORSE_ID: SODAHORSE_LISTS})
    processor = EventProcessor(db, zapier_webhook_enabled=False)
    asyncio.run(processor.process_pagamerican_abandon_cart(ABANDON_BODY))
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
