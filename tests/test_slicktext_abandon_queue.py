import asyncio
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../app")))

import pytest
from services.event_processor import EventProcessor

from app.services import slicktext_service
from app.services.slicktext_service import (
    AbandonOutcome,
    SendResult,
    process_slicktext_sync_task,
)
from app.repositories.database import DatabaseRepository
from tests.test_aweber import ABANDON_BODY, BUYGOODS_ABANDON


# --- Enfileiramento (EventProcessor) -----------------------------------------


class FakeDB:
    def __init__(self):
        self.queued = []

    def insert_abandoned_cart(self, cart_data):
        return {"id": "cart-1"}

    def get_network_id(self, network):
        return f"net-{network.value}"

    def get_checkout_by_code(self, code, account_id=None):
        return None

    def enqueue_slicktext_abandon(self, abandoned_cart_id, network, payload):
        self.queued.append((abandoned_cart_id, network, payload))
        return {"id": "q-1"}


def _processor(db, **kw):
    return EventProcessor(db, zapier_webhook_enabled=False, **kw)


def test_abandono_buygoods_vai_pra_fila_com_id_do_carrinho():
    db = FakeDB()
    processor = _processor(db, slicktext_abandon_enabled=True)
    assert asyncio.run(processor.process_buygoods_abandon_cart(BUYGOODS_ABANDON))
    assert db.queued == [
        (
            "cart-1",
            "BuyGoods",
            {
                "name": "John Smith",
                "phone": "5559876543",
                "product_codename": "sohp_front_6b_fnn1",
                "country": "United States",
            },
        )
    ]


def test_abandono_pagamerican_vai_pra_fila():
    db = FakeDB()
    processor = _processor(db, slicktext_abandon_enabled=True)
    body = {**ABANDON_BODY, "checkoutForm": {**ABANDON_BODY["checkoutForm"], "country": ""}}
    assert asyncio.run(processor.process_pagamerican_abandon_cart(body))
    assert db.queued == [
        (
            "cart-1",
            "PagAmerican",
            {
                "name": "Jane Doe",
                "phone": "5551234567",
                "product_codename": "cEy6BQCE",
                # país vazio -> US, como antes da fila
                "country": "US",
            },
        )
    ]


def test_abandono_digistore_vai_pra_fila():
    db = FakeDB()
    processor = _processor(db, slicktext_abandon_enabled=True)
    payload = {
        "order_id": "CART-1",
        "first_name": "Ann",
        "last_name": "Lee",
        "phone_no": "5550001111",
        "product_id": "12345",
        "country_code": "CA",
        "email": "ann@example.com",
    }
    assert asyncio.run(processor.process_digistore24_abandon_cart(payload))
    assert db.queued[0][:2] == ("cart-1", "DigiStore24")
    assert db.queued[0][2]["country"] == "CA"


def test_fila_desligada_por_padrao():
    # backfill/retro criam o processor sem a flag: não reenviam SMS
    db = FakeDB()
    asyncio.run(_processor(db).process_buygoods_abandon_cart(BUYGOODS_ABANDON))
    assert db.queued == []


# --- Envio (process_slicktext_sync_task) -------------------------------------


class _Result:
    def __init__(self, data):
        self.data = data


class _Query:
    def __init__(self, data):
        self._data = data

    def __getattr__(self, _name):
        return lambda *a, **k: self

    def execute(self):
        return _Result(self._data)


class FakeClient:
    def __init__(self, checkout_rows, credentials):
        self.checkout_rows = checkout_rows
        self.credentials = credentials

    def table(self, _name):
        return _Query(self.checkout_rows)

    def rpc(self, _name):
        return _Query(self.credentials)


class FakeRepo:
    def __init__(self, checkout_rows, credentials=None):
        self.client = FakeClient(checkout_rows, credentials or [])


class FakeSettings:
    abstract_api_key = "abs"
    slicktext_api_url = "https://slicktext.test"


CHECKOUT = {
    "url": "https://buygoods.com/secure/checkout.html?x=1",
    "quantity": 6,
    "products": {
        "name": "SodaHorsePeak",
        "aff_id_sms": "99",
        "slicktext_product_lists": [
            {"abandoned_list_id": "173009", "slicktext_accounts": {"id": "acc-1"}}
        ],
    },
}
ACCOUNT = {"id": "acc-1", "name": "SMS 09", "brand_id": "1", "api_key": "k"}
PAYLOAD = {
    "name": "John Smith",
    "phone": "2025550123",
    "product_codename": "sohp_front_6b_fnn1",
    "country": "United States",
}


def _send(payload=PAYLOAD, checkout_rows=(CHECKOUT,), credentials=(ACCOUNT,)):
    return asyncio.run(
        process_slicktext_sync_task(
            payload, FakeSettings(), FakeRepo(list(checkout_rows), list(credentials))
        )
    )


@pytest.fixture
def phone(monkeypatch):
    """Resposta fake da AbstractAPI (True/False/None)."""
    result = {"value": True}
    monkeypatch.setattr(
        slicktext_service, "validate_phone_abstract", lambda *_: result["value"]
    )
    return result


@pytest.fixture
def slicktext(monkeypatch):
    """Resultado fake do POST no SlickText, por conta."""
    calls = []
    result = {"value": SendResult.SUCCESS}

    def fake_sync(payload, customer, list_id, **kw):
        calls.append((list_id, payload["mobile_number"]))
        return result["value"]

    monkeypatch.setattr(slicktext_service, "_sync_to_slicktext", fake_sync)
    result["calls"] = calls
    return result


def test_enviado(phone, slicktext):
    assert _send() == AbandonOutcome.synced()
    assert slicktext["calls"] == [("173009", "+12025550123")]


def test_descartes_registram_o_motivo(phone, slicktext):
    assert _send(checkout_rows=()) == AbandonOutcome.skipped(
        "checkout não encontrado: sohp_front_6b_fnn1"
    )
    assert _send({**PAYLOAD, "country": "Brazil"}) == AbandonOutcome.skipped(
        "país fora do escopo: BR"
    )
    assert _send({**PAYLOAD, "phone": ""}) == AbandonOutcome.skipped(
        "telefone vazio ou inválido"
    )
    assert _send(credentials=()).status == "skipped"  # nenhuma conta active
    phone["value"] = False
    assert _send() == AbandonOutcome.skipped("telefone reprovado pela AbstractAPI")
    assert slicktext["calls"] == []


def test_erro_temporario_vira_retry(phone, slicktext):
    phone["value"] = None  # AbstractAPI fora
    assert _send() == AbandonOutcome.retry("AbstractAPI indisponível")
    phone["value"] = True
    slicktext["value"] = SendResult.FAILED
    outcome = _send()
    assert outcome.status == "retry" and "SMS 09: FAILED" in outcome.reason


# --- Gravação do resultado (DatabaseRepository) ------------------------------


class _UpdateCapture:
    def __init__(self):
        self.fields = None

    def table(self, _name):
        return self

    def update(self, fields):
        self.fields = fields
        return self

    def eq(self, *_):
        return self

    def execute(self):
        return _Result([])


@pytest.mark.parametrize(
    "status, attempts, expected",
    [
        ("synced", 0, "synced"),
        ("skipped", 0, "skipped"),
        ("retry", 0, "pending"),
        ("retry", 2, "failed"),  # 3ª tentativa
    ],
)
def test_mark_slicktext_abandon(status, attempts, expected):
    repo = DatabaseRepository.__new__(DatabaseRepository)
    repo.client = _UpdateCapture()
    repo.mark_slicktext_abandon("q-1", status, attempts, "motivo")
    fields = repo.client.fields
    assert fields["status"] == expected
    assert fields["attempts"] == attempts + 1
    assert ("sent_at" in fields) == (expected == "synced")


class _InsertCapture:
    def table(self, name):
        self.name = name
        return self

    def insert(self, row):
        self.row = row
        return self

    def execute(self):
        return _Result([{"id": "q-1"}])


def test_enqueue_slicktext_abandon_grava_na_fila_unificada():
    repo = DatabaseRepository.__new__(DatabaseRepository)
    repo.client = _InsertCapture()
    repo._networks_cache = {"BuyGoods": "net-1"}
    repo.enqueue_slicktext_abandon("cart-1", "BuyGoods", {"phone": "5551234567"})
    assert repo.client.name == "slicktext_sync_queue"
    assert repo.client.row == {
        "lead_type": "abandon",
        "network_id": "net-1",
        "phone": "5551234567",
        "payload": {"phone": "5551234567"},
        "abandoned_cart_id": "cart-1",
    }


# --- Uma linha por lista -------------------------------------------------------

ACCOUNT_2 = {"id": "acc-2", "name": "SMS 14", "brand_id": "2", "api_key": "k2"}
CHECKOUT_2_LISTS = {
    **CHECKOUT,
    "products": {
        **CHECKOUT["products"],
        "slicktext_product_lists": [
            {"abandoned_list_id": "173009", "slicktext_accounts": {"id": "acc-1"}},
            {"abandoned_list_id": "196623", "slicktext_accounts": {"id": "acc-2"}},
        ],
    },
}


def _send_lists(**kw):
    return asyncio.run(
        process_slicktext_sync_task(
            PAYLOAD,
            FakeSettings(),
            FakeRepo([CHECKOUT_2_LISTS], [ACCOUNT, ACCOUNT_2]),
            **kw,
        )
    )


def test_linha_do_lead_divide_e_envia_so_a_primeira_lista(phone, slicktext):
    assigned = []

    async def assign(targets):
        assigned.extend(list_id for list_id, _ in targets)
        return targets[:1]

    assert _send_lists(assign_targets=assign) == AbandonOutcome.synced()
    assert assigned == ["173009", "196623"]
    assert [list_id for list_id, _ in slicktext["calls"]] == ["173009"]


def test_telefone_reprovado_nao_divide_a_linha(phone, slicktext):
    phone["value"] = False

    async def assign(_targets):
        raise AssertionError("não deveria dividir")

    assert _send_lists(assign_targets=assign) == AbandonOutcome.skipped(
        "telefone reprovado pela AbstractAPI"
    )


def test_linha_com_lista_envia_so_pra_ela_sem_validar_telefone(
    monkeypatch, slicktext
):
    def fail(*_):
        raise AssertionError("não deveria validar de novo")

    monkeypatch.setattr(slicktext_service, "validate_phone_abstract", fail)
    assert _send_lists(list_id="196623") == AbandonOutcome.synced()
    assert [list_id for list_id, _ in slicktext["calls"]] == ["196623"]


def test_linha_com_lista_de_conta_inativa_vira_skipped(slicktext):
    outcome = asyncio.run(
        process_slicktext_sync_task(
            PAYLOAD,
            FakeSettings(),
            FakeRepo([CHECKOUT_2_LISTS], [ACCOUNT]),
            list_id="196623",
        )
    )
    assert outcome.status == "skipped"
    assert slicktext["calls"] == []


def test_sem_lista_e_sem_divisao_envia_pra_todas(phone, slicktext):
    """Backfill: não passa pela fila."""
    assert _send_lists() == AbandonOutcome.synced()
    assert [list_id for list_id, _ in slicktext["calls"]] == ["173009", "196623"]


class _SplitCapture:
    def __init__(self):
        self.upserts = []
        self.updates = []

    def table(self, _name):
        return self

    def upsert(self, rows, **kw):
        self.upserts.append((rows, kw))
        return self

    def update(self, fields):
        self.updates.append(fields)
        return self

    def eq(self, column, value):
        self.updates.append((column, value))
        return self

    def execute(self):
        return _Result([])


def test_assign_slicktext_abandon_targets():
    repo = DatabaseRepository.__new__(DatabaseRepository)
    repo.client = _SplitCapture()
    row = {
        "id": "q-1",
        "abandoned_cart_id": "cart-1",
        "network_id": "net-1",
        "phone": "2025550123",
        "payload": PAYLOAD,
    }
    targets = [("173009", ACCOUNT), ("196623", ACCOUNT_2)]
    assert repo.assign_slicktext_abandon_targets(row, targets) == targets[:1]
    rows, kw = repo.client.upserts[0]
    assert rows == [
        {
            "lead_type": "abandon",
            "event_id": None,
            "abandoned_cart_id": "cart-1",
            "network_id": "net-1",
            "phone": "2025550123",
            "payload": PAYLOAD,
            "list_id": "196623",
            "slick_account_id": "acc-2",
        }
    ]
    assert kw == {
        "on_conflict": "abandoned_cart_id,list_id",
        "ignore_duplicates": True,
    }
    assert repo.client.updates == [
        {"list_id": "173009", "slick_account_id": "acc-1"},
        ("id", "q-1"),
    ]
