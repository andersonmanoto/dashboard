import os
import sys
from uuid import uuid4

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../app")))

import pytest
from models.enums import NetworkType
from services.event_processor import EventProcessor
from services.normalizer import PayloadNormalizer
from services.redtrack_conversion_service import build_conversion_row

# Venda real de 28/09 (GlycoMelt, aff 170 interno), enviada manualmente ao
# RedTrack no teste de validação da API. Só os campos que o normalizer usa.
PAYLOAD_GLYCOMELT = {
    "sid": "6ab9e90dedb37fb6aea7aaef",
    "subid": "6ab9e90dedb37fb6aea7aaef",
    "subid2": "S231 - 1-X-X - TST - 25-09 - R01",
    "aff_id": "170",
    "aff_name": "TIGER OFFERS LTDA",
    "account_id": "13023",
    "order_id": "123398",
    "order_id_global": "AM3Z2UPN",
    "action_type": "neworder",
    "is_test": "0",
    "product_codename": "glyme6fnn1",
    "product_name": "GlycoMelt 6 Bottles",
    "product_price": "294.00",
    "total_clean": "294.00",
    "merchant_commission": "21.58",
    "aff_commission": "240.00",
    "taxes": "0",
    "currency": "USD",
    "customer_emailaddress": "cliente@example.com",
    "rr_createdate": "2026-09-28 00:13:43",
}


def _event(**payload_overrides):
    payload = {**PAYLOAD_GLYCOMELT, **payload_overrides}
    event = PayloadNormalizer().normalize(NetworkType.BUYGOODS, payload)
    # Simula o _enrich_checkout com codename achado em checkouts
    event.product_id = uuid4()
    event.checkout_id = uuid4()
    event.funnel_stage = "Purchase"
    return event


class FakeDB:
    def __init__(self, internal=True):
        self.internal = internal
        self.enqueued = []

    def is_internal_aff(self, network, product_id, aff_id):
        return self.internal

    def enqueue_redtrack_conversion(self, row):
        self.enqueued.append(row)
        return row


def test_row_venda_real_glycomelt():
    row = build_conversion_row(_event(), "evt-1")

    assert row["status"] == "pending"
    assert row["skip_reason"] is None
    assert row["bg_order_id"] == "123398"
    assert row["event_order_id"] == "AM3Z2UPN"
    assert row["network"] == "BuyGoods"
    assert row["payload"] == {
        "campaign_id": "",
        "clickid": "6ab9e90dedb37fb6aea7aaef",
        "type": "Purchase",
        "payout": 240.0,
        # 00:13:43 em Nova York (EDT, UTC-4)
        "created_at": "2026-09-28T04:13:43Z",
    }


def test_created_at_horario_de_inverno():
    row = build_conversion_row(_event(rr_createdate="2026-12-10 00:13:43"), None)
    assert row["payload"]["created_at"] == "2026-12-10T05:13:43Z"  # EST, UTC-5


@pytest.mark.parametrize(
    "subid",
    [
        "",
        "IwZXh0bgNhZW0BMABwZG9mAWZkaWQWUPP9TjLWl1JFUji0cgwP8grsdkRwE2FkaWQB_aem_x",
        "Cj0KCQjwt9jVBhDXARIsAFSP-6ewZrMIsjoOQUAtNLfMke7iUzyYpISEx9wYhB4",
        "v3_6eccb1a4-3602-4a84-bdfa-6675b25302a1_6a6126d9adf184a3504f5872_1840",
    ],
)
def test_clickid_de_outro_tracker_sai_skipped(subid):
    row = build_conversion_row(_event(subid=subid), None)
    assert row["status"] == "skipped"
    assert row["skip_reason"] == "invalid_click_id"


def test_funnel_stage_por_fallback_sai_skipped():
    event = _event()
    event.checkout_id = None  # codename não achado, Purchase veio do fallback
    row = build_conversion_row(event, None)
    assert row["status"] == "skipped"
    assert row["skip_reason"] == "funnel_stage_fallback"


def test_processor_enfileira_afiliado_interno():
    db = FakeDB(internal=True)
    EventProcessor(db, redtrack_send_from="2026-09-28").enqueue_redtrack_conversion(
        _event(), "evt-1"
    )
    assert len(db.enqueued) == 1


@pytest.mark.parametrize(
    "send_from, internal, overrides, funnel_stage",
    [
        (None, True, {}, "Purchase"),  # envio desligado (padrão)
        ("2026-09-29", True, {}, "Purchase"),  # venda antes da data de corte
        ("2026-09-28", False, {}, "Purchase"),  # afiliado externo
        ("2026-09-28", True, {}, "Up1"),  # upsell não vai
        ("2026-09-28", True, {"is_test": "1"}, "Purchase"),
        ("2026-09-28", True, {"action_type": "refund"}, "Purchase"),
    ],
)
def test_processor_nao_enfileira(send_from, internal, overrides, funnel_stage):
    db = FakeDB(internal=internal)
    event = _event(**overrides)
    event.funnel_stage = funnel_stage
    EventProcessor(db, redtrack_send_from=send_from).enqueue_redtrack_conversion(
        event, None
    )
    assert db.enqueued == []


SODAHORSEPEAK_ID = "f0494709-01fb-4d0e-8e8c-3e200f25b13c"


def test_processor_pula_produtos_excluidos():
    excluido = _event()
    excluido.product_id = SODAHORSEPEAK_ID
    outro = _event()

    db = FakeDB(internal=True)
    processor = EventProcessor(
        db,
        redtrack_send_from="2026-09-28",
        redtrack_excluded_product_ids=frozenset({SODAHORSEPEAK_ID}),
    )
    processor.enqueue_redtrack_conversion(excluido, None)
    processor.enqueue_redtrack_conversion(outro, None)

    assert len(db.enqueued) == 1
    assert db.enqueued[0]["click_id"] == "6ab9e90dedb37fb6aea7aaef"


# ========== DIGISTORE24 ==========
# Payload ILUSTRATIVO de venda de afiliado (campos que o normalizer lê).
# Ainda não validado contra um IPN real da conta de afiliado da Tiger.
PAYLOAD_DS24_AFILIADO = {
    "order_id": "DSABC123",
    "transaction_type": "payment",
    "datetime_full": "2026-11-10T14:05:00+01:00",
    "amount_brutto": "69.00",
    "amount_affiliate": "41.40",
    "currency": "USD",
    "cid": "6ab9e90dedb37fb6aea7aaef",
    "sid1": "S231 - 1-X-X - TST - 25-09 - R01",
    "affiliate_id": "tigeroffers",
    "affiliate_name": "tigeroffers",
    "product_id": "609807",
    "product_name": "Produto Teste",
    "email": "cliente@example.com",
    "is_test_payment": "0",
    "upsell_no": "0",
}

DS24 = frozenset({"BuyGoods", "DigiStore24"})


def _ds24_event(**payload_overrides):
    payload = {**PAYLOAD_DS24_AFILIADO, **payload_overrides}
    event = PayloadNormalizer().normalize(NetworkType.DIGISTORE24, payload)
    event.product_id = uuid4()
    event.checkout_id = uuid4()
    event.funnel_stage = "Purchase"
    return event


def test_row_ds24():
    row = build_conversion_row(_ds24_event(), "evt-ds")

    assert row["status"] == "pending"
    assert row["network"] == "DigiStore24"
    assert row["bg_order_id"] == "DSABC123"
    assert row["payload"] == {
        "campaign_id": "",
        "clickid": "6ab9e90dedb37fb6aea7aaef",
        "type": "Purchase",
        "payout": 41.4,
        # 14:05 em UTC+1 -> 13:05Z (usa o fuso do datetime_full)
        "created_at": "2026-11-10T13:05:00Z",
    }


@pytest.mark.parametrize("datetime_full", ["", "2026-11-10 14:05:00", "lixo"])
def test_ds24_sem_fuso_sai_skipped(datetime_full):
    row = build_conversion_row(_ds24_event(datetime_full=datetime_full), None)
    assert row["status"] == "skipped"
    assert row["skip_reason"] == "missing_created_at"


def test_ds24_clickid_invalido_sai_skipped():
    row = build_conversion_row(_ds24_event(cid="IwZXh0bgNhZW0BMABwZG9m"), None)
    assert row["skip_reason"] == "invalid_click_id"


def test_ds24_desligado_por_padrao():
    db = FakeDB(internal=True)
    EventProcessor(db, redtrack_send_from="2026-09-28").enqueue_redtrack_conversion(
        _ds24_event(), None
    )
    assert db.enqueued == []


def test_ds24_enfileira_quando_ligado():
    db = FakeDB(internal=True)
    EventProcessor(
        db, redtrack_send_from="2026-09-28", redtrack_networks=DS24
    ).enqueue_redtrack_conversion(_ds24_event(), "evt-ds")
    assert len(db.enqueued) == 1
    assert db.enqueued[0]["network"] == "DigiStore24"


@pytest.mark.parametrize(
    "internal, overrides, funnel_stage",
    [
        (False, {}, "Purchase"),  # afiliado externo
        (True, {}, "Up1"),  # upsell não vai
        (True, {"is_test_payment": "1"}, "Purchase"),
        (True, {"transaction_type": "refund"}, "Purchase"),
        (True, {"transaction_type": "chargeback"}, "Purchase"),
        (True, {"transaction_type": "rebill"}, "Purchase"),
    ],
)
def test_ds24_nao_enfileira(internal, overrides, funnel_stage):
    db = FakeDB(internal=internal)
    event = _ds24_event(**overrides)
    event.funnel_stage = funnel_stage
    EventProcessor(
        db, redtrack_send_from="2026-09-28", redtrack_networks=DS24
    ).enqueue_redtrack_conversion(event, None)
    assert db.enqueued == []


def test_buygoods_desligado_quando_fora_da_lista():
    db = FakeDB(internal=True)
    EventProcessor(
        db,
        redtrack_send_from="2026-09-28",
        redtrack_networks=frozenset({"DigiStore24"}),
    ).enqueue_redtrack_conversion(_event(), None)
    assert db.enqueued == []


def test_pagamerican_nunca_enfileira_mesmo_na_lista():
    """Rede sem created_at implementado não entra, mesmo listada na config."""
    db = FakeDB(internal=True)
    event = _event()
    event.network = NetworkType.PAGAMERICAN
    EventProcessor(
        db,
        redtrack_send_from="2026-09-28",
        redtrack_networks=frozenset({"PagAmerican"}),
    ).enqueue_redtrack_conversion(event, None)
    assert db.enqueued == []
