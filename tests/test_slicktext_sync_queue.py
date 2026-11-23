"""Vendas na slicktext_sync_queue: uma linha por lead e por lista."""

import asyncio

import pytest

from app.scripts import sync_approved_orders as sync
from app.scripts.sync_approved_orders import SyncResult, process_queue_item

ACC_1 = {"id": "acc-1", "name": "SMS 09", "brand_id": "1", "api_key": "k1"}
ACC_2 = {"id": "acc-2", "name": "SMS 14", "brand_id": "2", "api_key": "k2"}


class _Result:
    def __init__(self, data):
        self.data = data


class FakeDB:
    """Guarda os updates por id e as linhas criadas."""

    def __init__(self, credentials):
        self.credentials = credentials
        self.updates = {}
        self.upserts = []
        self.deleted = []
        self._pending = None
        self._delete = False
        self._data = []
        self.carts = []  # linhas de abandoned_carts devolvidas na busca por email
        self.cart_lookups = []

    def table(self, name):
        if name == "abandoned_carts":
            self._data = self.carts
        return self

    def select(self, *_a, **_kw):
        return self

    def in_(self, column, values):
        self.cart_lookups.append((column, sorted(values)))
        return self

    def order(self, *_a, **_kw):
        return self

    def limit(self, _n):
        return self

    def rpc(self, _name):
        self._data = self.credentials
        return self

    def update(self, fields):
        self._pending = fields
        return self

    def upsert(self, rows, **kw):
        self.upserts.append((rows, kw))
        return self

    def delete(self):
        self._delete = True
        return self

    def eq(self, column, value):
        if self._delete:
            self.deleted.append(value)
            self._delete = False
        elif self._pending is not None:
            assert column == "id"
            self.updates.setdefault(value, {}).update(self._pending)
            self._pending = None
        return self

    async def execute(self):
        data, self._data = self._data, []
        return _Result(data)


def _item(**kw):
    return {
        "id": "q-1",
        "event_id": "ev-1",
        "attempts": 0,
        "list_id": None,
        "slick_account_id": None,
        "events": {
            "customer_name": "John Smith",
            "customer_phone": "2025550123",
            "products": {
                "name": "NailsCleanPro",
                "slicktext_product_lists": [
                    {"approved_list_id": "100", "slicktext_accounts": {"id": "acc-1"}},
                    {"approved_list_id": "200", "slicktext_accounts": {"id": "acc-2"}},
                ],
            },
            "checkouts": {"quantity": 6},
        },
        **kw,
    }


@pytest.fixture
def env(monkeypatch):
    state = {
        "db": FakeDB([ACC_1, ACC_2]),
        "abstract": True,
        "abstract_calls": 0,
        "result": SyncResult.SUCCESS,
        "sent": [],
    }

    async def get_supabase():
        return state["db"]

    async def validate(_phone):
        state["abstract_calls"] += 1
        return state["abstract"]

    async def send(_payload, _customer, list_id, *, api_key, brand_id):
        state["sent"].append((list_id, brand_id))
        return state["result"]

    monkeypatch.setattr(sync, "get_supabase", get_supabase)
    monkeypatch.setattr(sync, "validate_phone_abstract", validate)
    monkeypatch.setattr(sync, "sync_contact_to_slicktext", send)
    return state


def _run(item):
    asyncio.run(process_queue_item(item))


def test_lead_divide_por_lista_e_envia_a_primeira(env):
    _run(_item())
    db = env["db"]
    assert env["abstract_calls"] == 1
    assert env["sent"] == [("100", "1")]
    rows, kw = db.upserts[0]
    assert [(r["event_id"], r["list_id"], r["slick_account_id"]) for r in rows] == [
        ("ev-1", "200", "acc-2")
    ]
    assert rows[0]["lead_type"] == "neworder"
    assert kw == {"on_conflict": "event_id,list_id", "ignore_duplicates": True}
    fields = db.updates["q-1"]
    assert (fields["list_id"], fields["slick_account_id"]) == ("100", "acc-1")
    assert fields["status"] == "synced"
    assert "sent_at" in fields


def test_produto_com_uma_conta_nao_cria_linha(env):
    env["db"].credentials = [ACC_1]
    _run(_item())
    assert env["db"].upserts == []
    assert env["sent"] == [("100", "1")]


def test_telefone_reprovado_nao_divide(env):
    env["abstract"] = False
    _run(_item())
    db = env["db"]
    assert db.upserts == []
    assert env["sent"] == []
    assert db.updates["q-1"]["status"] == "failed"
    assert db.updates["q-1"]["last_error"] == "rejeitado_abstract"
    assert "list_id" not in db.updates["q-1"]


@pytest.mark.parametrize(
    "phone, code", [(None, "telefone_vazio"), ("", "telefone_vazio"), ("99", "telefone_invalido")]
)
def test_telefone_vazio_e_invalido_tem_codigos_separados(env, phone, code):
    item = _item()
    item["events"]["customer_phone"] = phone
    _run(item)
    assert env["abstract_calls"] == 0
    assert env["sent"] == []
    assert env["db"].updates["q-1"]["status"] == "failed"
    assert env["db"].updates["q-1"]["last_error"] == code


def test_produto_sem_lista_descarta_a_linha(env):
    item = _item()
    item["events"]["products"]["slicktext_product_lists"] = []
    _run(item)
    assert env["db"].deleted == ["q-1"]
    assert "status" not in env["db"].updates.get("q-1", {})
    assert env["sent"] == []


def test_lista_que_saiu_da_tabela_descarta_a_linha(env):
    _run(_item(id="q-2", list_id="999", slick_account_id="acc-2"))
    assert env["db"].deleted == ["q-2"]
    assert env["sent"] == []


def test_venda_sem_produto_e_produto_nao_identificado(env):
    item = _item()
    item["events"]["products"] = None
    _run(item)
    assert env["db"].updates["q-1"]["status"] == "failed"
    assert env["db"].updates["q-1"]["last_error"] == "produto_nao_identificado"


def test_linha_com_lista_nao_valida_telefone_nem_divide(env):
    _run(_item(id="q-2", list_id="200", slick_account_id="acc-2"))
    db = env["db"]
    assert env["abstract_calls"] == 0
    assert db.upserts == []
    assert env["sent"] == [("200", "2")]
    assert db.updates["q-2"]["status"] == "synced"


def test_linha_de_conta_inativa_vira_skipped(env):
    env["db"].credentials = [ACC_1]
    _run(_item(id="q-2", list_id="200", slick_account_id="acc-2"))
    assert env["sent"] == []
    fields = env["db"].updates["q-2"]
    assert (fields["status"], fields["last_error"]) == ("skipped", "conta_inativa")


def test_erro_de_api_so_retenta_a_linha_da_conta(env):
    env["result"] = SyncResult.API_ERROR
    _run(_item(id="q-2", list_id="200", slick_account_id="acc-2"))
    assert env["sent"] == [("200", "2")]
    fields = env["db"].updates["q-2"]
    assert (fields["status"], fields["last_error"]) == ("retry", "slicktext_api_error")


def test_venda_com_telefone_de_porto_rico_e_regiao_nao_suportada(env):
    item = _item()
    item["events"]["customer_phone"] = "7873473234"
    _run(item)
    assert env["abstract_calls"] == 0
    assert env["sent"] == []
    assert env["db"].updates["q-1"]["last_error"] == "regiao_nao_suportada"


# --- venda sem telefone: telefone do carrinho pelo email ---------------------

from datetime import datetime, timedelta, timezone  # noqa: E402


def _sem_telefone(minutes_ago=None, **kw):
    item = _item(**kw)
    item["events"]["customer_phone"] = None
    item["events"]["customer_email"] = "John@Mail.com"
    if minutes_ago is not None:
        item["created_at"] = (
            datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
        ).isoformat()
    return item


def test_venda_sem_telefone_usa_o_telefone_do_carrinho(env):
    env["db"].carts = [{"customer_phone": ""}, {"customer_phone": "+1 202-555-0123"}]
    _run(_sem_telefone(minutes_ago=2))
    db = env["db"]
    assert db.cart_lookups == [("customer_email", ["John@Mail.com", "john@mail.com"])]
    assert env["abstract_calls"] == 1
    assert env["sent"] == [("100", "1")]
    assert db.updates["q-1"]["status"] == "synced"


def test_venda_sem_telefone_e_sem_carrinho_espera_sem_gastar_tentativa(env):
    _run(_sem_telefone(minutes_ago=5, attempts=1))
    fields = env["db"].updates["q-1"]
    assert fields["status"] == "retry"
    assert fields["last_error"] == "aguardando_telefone_carrinho"
    assert fields["attempts"] == 1
    assert env["sent"] == []


def test_venda_sem_telefone_depois_da_espera_e_telefone_vazio(env):
    _run(_sem_telefone(minutes_ago=sync.CART_PHONE_WAIT_MINUTES + 1))
    fields = env["db"].updates["q-1"]
    assert fields["status"] == "failed"
    assert fields["last_error"] == "telefone_vazio"


def test_venda_com_telefone_nao_busca_carrinho(env):
    _run(_item())
    assert env["db"].cart_lookups == []
