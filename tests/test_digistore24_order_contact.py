"""Telefone da venda DigiStore24 vindo do webhook "Orders by customers"."""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../app")))

from models.enums import NetworkType  # noqa: E402
from services.event_processor import EventProcessor  # noqa: E402
from services.normalizer import PayloadNormalizer  # noqa: E402

from tests.test_normalizer import PAYLOAD_BUYGOODS_REAL, PAYLOAD_DIGISTORE_SALE  # noqa: E402


class FakeDB:
    def __init__(self, phones=None, fail=False):
        self.phones = phones or {}
        self.fail = fail
        self.lookups = []

    def get_digistore24_order_phone(self, order_id):
        self.lookups.append(order_id)
        if self.fail:
            raise RuntimeError("supabase fora")
        return self.phones.get(order_id)


def _ds24_event():
    return PayloadNormalizer().normalize(NetworkType.DIGISTORE24, PAYLOAD_DIGISTORE_SALE)


def test_webhook_antes_da_venda_preenche_o_telefone():
    event = _ds24_event()
    assert not event.customer_phone  # o postback não traz
    db = FakeDB(phones={event.order_id: "+12025550123"})
    EventProcessor(db)._fill_digistore24_phone(event)
    assert event.customer_phone == "+12025550123"


def test_sem_contato_do_pedido_segue_sem_telefone():
    event = _ds24_event()
    EventProcessor(FakeDB())._fill_digistore24_phone(event)
    assert not event.customer_phone


def test_falha_na_busca_nao_derruba_a_venda():
    event = _ds24_event()
    EventProcessor(FakeDB(fail=True))._fill_digistore24_phone(event)
    assert not event.customer_phone


def test_outras_redes_nao_consultam():
    event = PayloadNormalizer().normalize(NetworkType.BUYGOODS, PAYLOAD_BUYGOODS_REAL)
    db = FakeDB()
    EventProcessor(db)._fill_digistore24_phone(event)
    assert db.lookups == []


def test_venda_que_ja_tem_telefone_nao_consulta():
    event = _ds24_event()
    event.customer_phone = "+12025550100"
    db = FakeDB(phones={event.order_id: "+12025550123"})
    EventProcessor(db)._fill_digistore24_phone(event)
    assert db.lookups == []
    assert event.customer_phone == "+12025550100"
