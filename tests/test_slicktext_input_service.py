"""Input de leads em lote (slicktext_input_queue)."""

import asyncio
import os
import sys
from types import SimpleNamespace

import pytest

# app/repositories/database.py importa "config" (sem o prefixo app.)
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../app")))

from app.scripts.sync_approved_orders import SyncResult
from app.services import slicktext_accounts
from app.services import slicktext_input_service as svc
from app.services.slicktext_input_service import (
    Destination,
    InputFailure,
    RowOutcome,
    phone_to_e164,
)

ACCOUNT = {"id": "acc-1", "name": "SMS 09", "brand_id": "77", "api_key": "k1"}
DEST = Destination(account=ACCOUNT, list_id="555")


class _Result:
    def __init__(self, data):
        self.data = data


class _Query:
    def __init__(self, db, table):
        self.db, self.table, self.filters, self.fields = db, table, [], None

    def select(self, *_a, **_k):
        return self

    def update(self, fields):
        self.fields = fields
        return self

    def eq(self, col, val):
        self.filters.append((col, val))
        return self

    def in_(self, col, val):
        self.filters.append((col, tuple(val)))
        return self

    def limit(self, _n):
        return self

    async def execute(self):
        if self.fields is not None:
            self.db.updates.append((self.table, dict(self.filters), self.fields))
            return _Result([{"ok": True}])
        return _Result(self.db.rows.get(self.table, []))


class _Rpc:
    def __init__(self, db, name, args):
        self.db, self.name, self.args = db, name, args

    async def execute(self):
        self.db.rpcs.append((self.name, self.args))
        value = self.db.rpc_results.get(self.name)
        if isinstance(value, Exception):
            raise value
        if callable(value):
            value = value()
        return _Result(value)


class FakeDB:
    def __init__(self):
        self.rows = {
            "slicktext_input_lists": [{"slick_account_id": "acc-1", "list_id": "555"}],
            "slicktext_accounts": [
                {
                    "id": "acc-1",
                    "name": "SMS 09",
                    "brand_id": "77",
                    "status": "Active",
                    "allow_input": True,
                }
            ],
            "events": [
                {
                    "id": "ev-1",
                    "customer_name": "John",
                    "customer_email": "j@x.com",
                    "products": {"name": "NailsCleanPro"},
                    "checkouts": {"quantity": 3},
                }
            ],
        }
        self.rpc_results = {"get_slicktext_account_api_key": "k1"}
        self.updates = []
        self.rpcs = []

    def table(self, name):
        return _Query(self, name)

    def rpc(self, name, args=None):
        return _Rpc(self, name, args)

    def row_updates(self):
        return [f for t, _, f in self.updates if t == "slicktext_input_queue"]

    def input_updates(self):
        return [f for t, _, f in self.updates if t == "slicktext_inputs"]


@pytest.fixture
def env(monkeypatch):
    state = {
        "db": FakeDB(),
        "abstract": True,
        "abstract_calls": 0,
        "result": SyncResult.SUCCESS,
        "sent": [],
        "brand_down": False,
    }

    async def get_supabase():
        return state["db"]

    async def validate(_phone):
        state["abstract_calls"] += 1
        return state["abstract"]

    async def send(payload, _customer, list_id, *, api_key, brand_id):
        state["sent"].append((payload, list_id, brand_id))
        return state["result"]

    async def brand_is_down(_url, _account):
        return state["brand_down"]

    monkeypatch.setattr(svc, "get_supabase", get_supabase)
    monkeypatch.setattr(svc, "validate_phone_abstract", validate)
    monkeypatch.setattr(svc, "sync_contact_to_slicktext", send)
    monkeypatch.setattr(slicktext_accounts, "brand_is_down", brand_is_down)
    monkeypatch.setattr(slicktext_accounts, "RETRY_BASE_DELAY_SECONDS", 0)
    monkeypatch.setattr(slicktext_accounts, "notify_account_deactivated", _noop)
    return state


async def _noop(*_a, **_k):
    return None


def _row(**kw):
    return {
        "input_id": "in-1",
        "event_id": "ev-1",
        "input_list_id": "list-1",
        "customer_phone": "12025550123",
        "status": "pending",
        "attempts": 0,
        **kw,
    }


# --- telefone ---


@pytest.mark.parametrize(
    "digits, expected",
    [
        ("2025550123", ("+12025550123", 1)),
        ("12025550123", ("+12025550123", 1)),
        ("5511987654321", ("+5511987654321", 55)),
        ("123", (None, None)),
        ("", (None, None)),
        (None, (None, None)),
    ],
)
def test_phone_to_e164(digits, expected):
    assert phone_to_e164(digits) == expected


# --- send_row ---


def test_envia_com_telefone_da_fila_nome_email_e_lista(env):
    outcome = asyncio.run(svc.send_row(_row(), env["db"].rows["events"][0], DEST))
    assert outcome == RowOutcome("synced", None)
    payload, list_id, brand_id = env["sent"][0]
    assert (list_id, brand_id) == (555, "77")
    assert payload["mobile_number"] == "+12025550123"
    assert payload["first_name"] == "John"
    assert payload["email"] == "j@x.com"
    assert (payload["produto"], payload["bottles"]) == ("NailsCleanPro", "3")


def test_telefone_invalido_nao_chama_abstract(env):
    outcome = asyncio.run(svc.send_row(_row(customer_phone="99"), {}, DEST))
    assert outcome == RowOutcome("failed", "telefone_invalido")
    assert env["abstract_calls"] == 0


def test_fora_de_eua_e_canada_e_regiao_nao_suportada(env):
    outcome = asyncio.run(svc.send_row(_row(customer_phone="5511987654321"), {}, DEST))
    assert outcome == RowOutcome("failed", "regiao_nao_suportada")
    assert env["abstract_calls"] == 0


def test_abstract_recusa(env):
    env["abstract"] = False
    outcome = asyncio.run(svc.send_row(_row(), {}, DEST))
    assert outcome == RowOutcome("failed", "rejeitado_abstract")
    assert env["sent"] == []


def test_abstract_fora_do_ar_vira_retry(env):
    env["abstract"] = None
    assert asyncio.run(svc.send_row(_row(), {}, DEST)).status == "retry"


def test_slicktext_rejeita_regiao(env):
    env["result"] = SyncResult.UNSUPPORTED_REGION
    outcome = asyncio.run(svc.send_row(_row(), {}, DEST))
    assert outcome == RowOutcome("failed", "regiao_nao_suportada")


def test_erro_temporario_slicktext_vira_retry(env):
    env["result"] = SyncResult.API_ERROR
    outcome = asyncio.run(svc.send_row(_row(), {}, DEST))
    assert outcome == RowOutcome("retry", "slicktext_api_error")


def test_404_com_conta_viva_e_lista_nao_encontrada_e_para_o_input(env):
    env["result"] = SyncResult.ACCOUNT_ERROR
    outcome = asyncio.run(svc.send_row(_row(), {}, DEST))
    assert outcome == RowOutcome(
        "failed", "lista_nao_encontrada", stop_input="lista_nao_encontrada"
    )


def test_conta_caiu_para_o_input_e_linha_fica_retry(env):
    env["result"] = SyncResult.ACCOUNT_ERROR
    env["brand_down"] = True
    outcome = asyncio.run(svc.send_row(_row(), {}, DEST))
    assert outcome == RowOutcome("retry", "slicktext_api_error", stop_input="conta_inativa")
    deact = [f for t, _, f in env["db"].updates if t == "slicktext_accounts"]
    assert deact and deact[0]["status"] == "inactive"


def test_retry_no_limite_vira_failed():
    out = svc._final_outcome(RowOutcome("retry", "slicktext_api_error"), svc.MAX_ATTEMPTS)
    assert out == RowOutcome("failed", "slicktext_api_error")
    out = svc._final_outcome(RowOutcome("retry", "slicktext_api_error"), svc.MAX_ATTEMPTS - 1)
    assert out.status == "retry"


# --- lote ---


def test_lote_atualiza_a_linha_e_fecha_o_input(env):
    db = env["db"]
    asyncio.run(svc._send_batch(db, [_row(attempts=2)], {}, set()))
    (fields,) = db.row_updates()
    assert fields["status"] == "synced"
    assert fields["attempts"] == 3
    assert fields["last_error"] is None
    assert fields["claimed_at"] is None
    assert ("sms_input_refresh_status", {"p_input_id": "in-1"}) in db.rpcs


def test_lote_com_conta_inativa_marca_input_error_sem_enviar(env):
    db = env["db"]
    db.rows["slicktext_accounts"][0]["status"] = "inactive"
    asyncio.run(svc._send_batch(db, [_row()], {}, set()))
    assert env["sent"] == []
    assert db.row_updates() == []
    (fields,) = db.input_updates()
    assert (fields["status"], fields["last_error"]) == ("error", "conta_inativa")
    assert not any(n == "sms_input_refresh_status" for n, _ in db.rpcs)


def test_lote_lista_sumiu_para_o_resto_do_input(env, monkeypatch):
    monkeypatch.setattr(svc, "SEND_CONCURRENCY", 1)
    env["result"] = SyncResult.ACCOUNT_ERROR
    db = env["db"]
    rows = [_row(), _row(event_id="ev-2", customer_phone="12025550199")]
    asyncio.run(svc._send_batch(db, rows, {}, set()))
    assert len(env["sent"]) == slicktext_accounts.SEND_ATTEMPTS  # só a 1ª linha
    (fields,) = db.row_updates()
    assert (fields["status"], fields["last_error"]) == ("failed", "lista_nao_encontrada")
    (inp,) = db.input_updates()
    assert (inp["status"], inp["last_error"]) == ("error", "lista_nao_encontrada")


# --- destino / start_input ---


def test_conta_ativa_em_portugues_vale(env):
    db = env["db"]
    db.rows["slicktext_accounts"][0]["status"] = " ATIVA "
    dest = asyncio.run(svc.resolve_destination(db, "list-1", check_list=False))
    assert dest.list_id == "555" and dest.account["api_key"] == "k1"


@pytest.mark.parametrize("allow_input", [False, None])
def test_conta_com_envio_desligado_e_falha_do_input(env, allow_input):
    db = env["db"]
    db.rows["slicktext_accounts"][0]["allow_input"] = allow_input
    with pytest.raises(InputFailure) as exc:
        asyncio.run(svc.resolve_destination(db, "list-1", check_list=False))
    assert exc.value.code == "envio_desligado"
    assert db.rpcs == []  # nem lê a api key


# --- prazo da rodada ---


def _fake_rounds(monkeypatch, batch_seconds: float, batches: int = 10):
    clock = {"t": 0.0}
    sent = []

    async def claim(_db):
        return [_row(event_id=f"ev-{len(sent)}")] if len(sent) < batches else []

    async def send_batch(_db, rows, _dest, _stopped):
        sent.append(rows)
        clock["t"] += batch_seconds

    monkeypatch.setattr(svc, "time", SimpleNamespace(monotonic=lambda: clock["t"]))
    monkeypatch.setattr(svc, "_claim", claim)
    monkeypatch.setattr(svc, "_send_batch", send_batch)
    return sent


def test_nao_comeca_lote_que_nao_cabe_no_prazo(env, monkeypatch):
    sent = _fake_rounds(monkeypatch, batch_seconds=15)
    # 0-15, 15-30; o 3º terminaria em 45 > 40
    assert asyncio.run(svc.send_pending_rows(time_budget_seconds=40)) == 2
    assert len(sent) == 2


def test_primeiro_lote_sempre_roda(env, monkeypatch):
    sent = _fake_rounds(monkeypatch, batch_seconds=50)
    assert asyncio.run(svc.send_pending_rows(time_budget_seconds=40)) == 1
    assert len(sent) == 1


def test_para_quando_a_fila_acaba(env, monkeypatch):
    sent = _fake_rounds(monkeypatch, batch_seconds=1, batches=3)
    assert asyncio.run(svc.send_pending_rows(time_budget_seconds=40)) == 3
    assert len(sent) == 3


def test_rpc_da_api_key_falhando_e_falha_do_input(env):
    db = env["db"]
    db.rpc_results["get_slicktext_account_api_key"] = RuntimeError("conta sem API key no vault")
    with pytest.raises(InputFailure) as exc:
        asyncio.run(svc.resolve_destination(db, "list-1", check_list=False))
    assert exc.value.code == "api_key_indisponivel"


def test_start_input_ignora_input_que_nao_esta_pending(env):
    db = env["db"]
    db.rows["slicktext_inputs"] = [{"id": "in-1", "input_list_id": "list-1", "status": "done"}]
    asyncio.run(svc.start_input("in-1"))
    assert db.rpcs == []


def test_start_input_reune_leads(env, monkeypatch):
    async def list_ok(*_a):
        return None

    monkeypatch.setattr(svc, "_check_list_exists", list_ok)
    db = env["db"]
    db.rows["slicktext_inputs"] = [{"id": "in-1", "input_list_id": "list-1", "status": "pending"}]
    db.rpc_results["sms_input_leads"] = 42
    asyncio.run(svc.start_input("in-1"))
    assert ("sms_input_leads", {"p_input_id": "in-1"}) in db.rpcs
    assert db.input_updates() == []


def test_start_input_falha_ao_reunir_marca_error(env, monkeypatch):
    async def list_ok(*_a):
        return None

    monkeypatch.setattr(svc, "_check_list_exists", list_ok)
    db = env["db"]
    db.rows["slicktext_inputs"] = [{"id": "in-1", "input_list_id": "list-1", "status": "pending"}]
    db.rpc_results["sms_input_leads"] = RuntimeError("boom")
    asyncio.run(svc.start_input("in-1"))
    (fields,) = db.input_updates()
    assert (fields["status"], fields["last_error"]) == ("error", "falha_ao_reunir_leads")


# --- endpoint ---


class _SyncQuery:
    def __init__(self, rows):
        self.rows = rows

    def select(self, *_a):
        return self

    def eq(self, *_a):
        return self

    def limit(self, _n):
        return self

    def execute(self):
        return _Result(self.rows)


class _FakePool:
    def __init__(self):
        self.jobs = []

    async def enqueue_job(self, name, *args, **kw):
        self.jobs.append((name, args, kw))


@pytest.fixture
def api(monkeypatch):
    from types import SimpleNamespace

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.config import get_settings
    from app.routers import slicktext as router_mod

    settings = SimpleNamespace(slicktext_input_endpoint_key="secret")
    state = {"rows": []}
    client_db = SimpleNamespace(table=lambda _t: _SyncQuery(state["rows"]))

    app = FastAPI()
    app.include_router(router_mod.router)
    app.state.db = SimpleNamespace(client=client_db)
    app.state.redis_pool = _FakePool()
    app.dependency_overrides[get_settings] = lambda: settings
    state["app"] = app
    state["client"] = TestClient(app)
    return state


IN_ID = "5b1f0c2e-7a11-4c2e-9d01-000000000001"
LIST_ID = "9a7d3e10-1b22-4d33-8e44-000000000002"
BODY = {"input_id": IN_ID, "input_list_id": LIST_ID, "start_date": "2026-10-01", "end_date": "2026-10-01"}


def _post(api, body=BODY, key="secret"):
    return api["client"].post("/slicktext/inputs", json=body, headers={"X-API-KEY": key})


def test_endpoint_sem_chave_403(api):
    assert _post(api, key="errada").status_code == 403


@pytest.mark.parametrize(
    "rows, code, enqueued",
    [
        ([], 400, False),
        ([{"id": IN_ID, "input_list_id": "outra", "status": "pending"}], 400, False),
        ([{"id": IN_ID, "input_list_id": LIST_ID, "status": "error"}], 409, False),
        ([{"id": IN_ID, "input_list_id": LIST_ID, "status": "done"}], 202, False),
        ([{"id": IN_ID, "input_list_id": LIST_ID, "status": "processing"}], 202, False),
        ([{"id": IN_ID, "input_list_id": LIST_ID, "status": "pending"}], 202, True),
    ],
)
def test_endpoint_status(api, rows, code, enqueued):
    api["rows"].extend(rows)
    resp = _post(api)
    assert resp.status_code == code
    jobs = api["app"].state.redis_pool.jobs
    assert bool(jobs) is enqueued
    if enqueued:
        assert resp.json() == {"accepted": True, "input_id": IN_ID}
        assert jobs[0][0] == "task_slicktext_input_start" and jobs[0][1] == (IN_ID,)


def test_endpoint_periodo_invertido_422(api):
    assert _post(api, {**BODY, "end_date": "2026-09-30"}).status_code == 422
