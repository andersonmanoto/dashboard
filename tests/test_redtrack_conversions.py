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


GLYCOMELT_ID = "d30ca6b3-fd85-4327-a8ba-8a5798bfc7b4"


def test_processor_so_produtos_liberados():
    liberado = _event()
    liberado.product_id = GLYCOMELT_ID
    outro = _event()

    db = FakeDB(internal=True)
    processor = EventProcessor(
        db,
        redtrack_send_from="2026-09-28",
        redtrack_product_ids=frozenset({GLYCOMELT_ID}),
    )
    processor.enqueue_redtrack_conversion(liberado, None)
    processor.enqueue_redtrack_conversion(outro, None)

    assert len(db.enqueued) == 1
    assert db.enqueued[0]["click_id"] == "6ab9e90dedb37fb6aea7aaef"
