"""
Input de leads em lote pro SlickText (docs/sms-input-leads-contrato-backend.md).

O painel cria a lista e a linha em slicktext_inputs (pending) e faz o POST
/slicktext/inputs. Daqui pra frente:

  - start_input(): passo 2 (resolve conta, brand_id, list_id, api key e
    confere a lista na SlickText) e passo 3 (sms_input_leads). Roda no job
    task_slicktext_input_start, enfileirado pelo POST.
  - send_pending_rows(): passo 4 e 5. Reserva lotes da fila com
    sms_input_claim_batch (lease em claimed_at, ver
    migrations/20261109_slicktext_input_queue_claim.sql), envia cada lead e
    chama sms_input_refresh_status depois de cada lote. Roda no cron.
  - sweep_inputs(): rede de segurança. Input pending antigo (POST ou job
    perdido) volta pro start_input; input processing ganha refresh de status.

O envio reaproveita a validação da Abstract e o sync_contact_to_slicktext do
fluxo de compra aprovada (scripts/sync_approved_orders.py).
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from functools import partial
from typing import Optional

import httpx
import phonenumbers
from loguru import logger
from phonenumbers import NumberParseException, PhoneNumberFormat

from app.config import get_settings
from app.scripts.sync_approved_orders import (
    SyncResult,
    get_supabase,
    sync_contact_to_slicktext,
    validate_phone_abstract,
)
from app.services.slicktext_accounts import (
    ACCOUNT_ERROR_STATUSES,
    brand_is_down,
    deactivation_fields,
    retry_transport,
    send_with_account_check,
)

MAX_ATTEMPTS = 5
CLAIM_BATCH_SIZE = 25
LEASE_MINUTES = 15
# Envios em paralelo dentro de um lote. Cada lead faz ~3 chamadas externas
# (Abstract + SlickText criar/buscar + lista); subir com cuidado por causa
# do rate limit das duas APIs.
SEND_CONCURRENCY = 4
STALE_PENDING_MINUTES = 5
LIST_CHECK_TIMEOUT = 10.0
ACTIVE_ACCOUNT_STATUSES = frozenset({"active", "ativa"})

# last_error da fila (mesmos códigos da slicktext_sync_queue)
ERR_ABSTRACT = "rejeitado_abstract"
ERR_ABSTRACT_API = "abstract_api_error"
ERR_INVALID_PHONE = "telefone_invalido"
ERR_REGION = "regiao_nao_suportada"
ERR_LIST_NOT_FOUND = "lista_nao_encontrada"
ERR_SLICKTEXT_API = "slicktext_api_error"

# last_error do input inteiro
INPUT_ERR_ACCOUNT = "conta_inativa"
INPUT_ERR_NO_BRAND = "conta_sem_brand_id"
INPUT_ERR_API_KEY = "api_key_indisponivel"
INPUT_ERR_LIST_ROW = "lista_input_nao_encontrada"
INPUT_ERR_LIST = "lista_nao_encontrada"
INPUT_ERR_GATHER = "falha_ao_reunir_leads"


class InputFailure(Exception):
    """O input inteiro não pode continuar (passo 6). `code` vai pro last_error."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class TemporaryFailure(Exception):
    """Erro passageiro (rede, 5xx): não marca nada, a rede de segurança tenta de novo."""


@dataclass(frozen=True)
class Destination:
    account: dict  # {id, name, brand_id, api_key}, formato do send_with_account_check
    list_id: str


@dataclass(frozen=True)
class RowOutcome:
    status: str
    last_error: Optional[str]
    stop_input: Optional[str] = None  # last_error do input se ele precisar parar


# --- telefone ---------------------------------------------------------------


def phone_to_e164(digits: Optional[str]) -> tuple[Optional[str], Optional[int]]:
    """
    customer_phone da fila vem só com dígitos (normalize_phone), sem o "+".
    Tenta como número dos EUA e, se não fechar, como número internacional.
    Devolve (E.164, código do país) ou (None, None) se não for válido.
    """
    if not digits:
        return None, None
    for raw, region in ((digits, "US"), (f"+{digits}", None)):
        try:
            parsed = phonenumbers.parse(raw, region)
        except NumberParseException:
            continue
        if phonenumbers.is_valid_number(parsed):
            return (
                phonenumbers.format_number(parsed, PhoneNumberFormat.E164),
                parsed.country_code,
            )
    return None, None


# --- banco ------------------------------------------------------------------


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _fetch_input(db, input_id: str) -> Optional[dict]:
    res = await retry_transport(
        lambda: db.table("slicktext_inputs")
        .select("id, input_list_id, status, enqueued_qty, created_at")
        .eq("id", input_id)
        .limit(1)
        .execute(),
        f"ler input {input_id}",
    )
    return (res.data or [None])[0]


async def mark_input_error(db, input_id: str, code: str) -> None:
    await retry_transport(
        lambda: db.table("slicktext_inputs")
        .update({"status": "error", "last_error": code, "finished_at": _now()})
        .eq("id", input_id)
        .in_("status", ["pending", "processing"])
        .execute(),
        f"marcar input {input_id} como error",
    )
    logger.error(f"[SlickText input] {input_id} -> error ({code})")


async def _refresh_status(db, input_id: str) -> Optional[str]:
    res = await retry_transport(
        lambda: db.rpc("sms_input_refresh_status", {"p_input_id": input_id}).execute(),
        f"sms_input_refresh_status {input_id}",
    )
    return res.data


async def _update_row(db, row: dict, outcome: RowOutcome, attempts: int) -> None:
    await retry_transport(
        lambda: db.table("slicktext_input_queue")
        .update(
            {
                "status": outcome.status,
                "attempts": attempts,
                "last_error": outcome.last_error,
                "updated_at": _now(),
                "claimed_at": None,
            }
        )
        .eq("input_id", row["input_id"])
        .eq("event_id", row["event_id"])
        .execute(),
        f"atualizar linha {row['input_id']}/{row['event_id']}",
    )


# --- passo 2: destino -------------------------------------------------------


async def _check_list_exists(api_url: str, account: dict, list_id: str) -> None:
    """GET /brands/{brand}/lists/{list}. 404 com a conta viva = lista apagada."""
    try:
        async with httpx.AsyncClient(timeout=LIST_CHECK_TIMEOUT) as client:
            resp = await client.get(
                f"{api_url}/brands/{account['brand_id']}/lists/{list_id}",
                headers={"Authorization": f"Bearer {account['api_key']}"},
            )
    except httpx.RequestError as exc:
        raise TemporaryFailure(f"conferir lista {list_id}: {exc}") from exc

    if resp.status_code in ACCOUNT_ERROR_STATUSES:
        if await brand_is_down(api_url, account):
            raise InputFailure(INPUT_ERR_ACCOUNT)
        raise InputFailure(INPUT_ERR_LIST)
    if resp.is_error:
        raise TemporaryFailure(f"conferir lista {list_id}: HTTP {resp.status_code}")


async def resolve_destination(db, input_list_id: str, *, check_list: bool) -> Destination:
    """
    input_list_id -> slicktext_input_lists (conta, list_id) -> slicktext_accounts
    (brand_id, status) -> api key do Vault. `check_list` confere a lista na
    SlickText (no começo do input; no envio, o próprio POST da lista acusa).
    """
    res = await retry_transport(
        lambda: db.table("slicktext_input_lists")
        .select("slick_account_id, list_id")
        .eq("id", input_list_id)
        .limit(1)
        .execute(),
        f"ler lista de input {input_list_id}",
    )
    input_list = (res.data or [None])[0]
    if not input_list or not input_list.get("list_id"):
        raise InputFailure(INPUT_ERR_LIST_ROW)

    account_id = input_list["slick_account_id"]
    res = await retry_transport(
        lambda: db.table("slicktext_accounts")
        .select("id, name, brand_id, status")
        .eq("id", account_id)
        .limit(1)
        .execute(),
        f"ler conta {account_id}",
    )
    account = (res.data or [None])[0]
    # Só o status decide. allow_input NÃO entra aqui de propósito: ele é o
    # liga/desliga dos envios automáticos (compra aprovada e abandono); o input
    # em lote é disparo manual do painel e vale pra conta active com envio
    # automático desligado (pedido do front, 09/11/2026).
    if not account or str(account.get("status") or "").strip().lower() not in ACTIVE_ACCOUNT_STATUSES:
        raise InputFailure(INPUT_ERR_ACCOUNT)
    if not account.get("brand_id"):
        raise InputFailure(INPUT_ERR_NO_BRAND)

    try:
        key_res = await retry_transport(
            lambda: db.rpc(
                "get_slicktext_account_api_key", {"p_account_id": account_id}
            ).execute(),
            f"api key da conta {account_id}",
        )
    except httpx.TransportError as exc:
        raise TemporaryFailure(f"api key da conta {account_id}: {exc}") from exc
    except Exception as exc:
        logger.error(f"[SlickText input] RPC da api key falhou ({account_id}): {exc}")
        raise InputFailure(INPUT_ERR_API_KEY) from exc
    api_key = key_res.data
    if not api_key or not isinstance(api_key, str):
        raise InputFailure(INPUT_ERR_API_KEY)

    dest = Destination(
        account={
            "id": account["id"],
            "name": account.get("name") or account["id"],
            "brand_id": str(account["brand_id"]),
            "api_key": api_key,
        },
        list_id=str(input_list["list_id"]),
    )
    if check_list:
        await _check_list_exists(get_settings().slicktext_api_url, dest.account, dest.list_id)
    return dest


# --- passos 2 e 3: começar o input ------------------------------------------


async def start_input(input_id: str) -> None:
    """Resolve o destino e reúne os leads. Só age em input pending."""
    db = await get_supabase()
    row = await _fetch_input(db, input_id)
    if not row:
        logger.warning(f"[SlickText input] {input_id} não existe.")
        return
    if row["status"] != "pending":
        logger.info(f"[SlickText input] {input_id} já está {row['status']}; nada a fazer.")
        return

    try:
        await resolve_destination(db, row["input_list_id"], check_list=True)
    except InputFailure as exc:
        await mark_input_error(db, input_id, exc.code)
        return
    except TemporaryFailure as exc:
        # continua pending; a varredura tenta de novo
        logger.warning(f"[SlickText input] {input_id} adiado: {exc}")
        return

    try:
        res = await db.rpc("sms_input_leads", {"p_input_id": input_id}).execute()
    except Exception as exc:
        logger.exception(f"[SlickText input] sms_input_leads falhou ({input_id})")
        if isinstance(exc, httpx.TransportError):
            # pode ter commitado do lado do banco; a função é idempotente e
            # a varredura chama de novo se o input continuar pending
            return
        await mark_input_error(db, input_id, INPUT_ERR_GATHER)
        return

    enqueued = res.data or 0
    logger.info(f"[SlickText input] {input_id}: {enqueued} lead(s) na fila.")


# --- passo 4: enviar --------------------------------------------------------


async def _fetch_events(db, event_ids: list[str]) -> dict[str, dict]:
    res = await retry_transport(
        lambda: db.table("events")
        .select("id, customer_name, customer_email, products(name), checkouts(quantity)")
        .in_("id", event_ids)
        .execute(),
        "ler eventos do lote",
    )
    return {e["id"]: e for e in res.data or []}


def _contact_payload(event: dict, e164: str) -> dict:
    payload = {
        "first_name": event.get("customer_name") or "",
        "mobile_number": e164,
        "opt_in_status": "subscribed",
    }
    if event.get("customer_email"):
        payload["email"] = event["customer_email"]
    product_name = (event.get("products") or {}).get("name")
    if product_name:
        payload["produto"] = product_name
    quantity = (event.get("checkouts") or {}).get("quantity")
    if quantity is not None:
        payload["bottles"] = str(quantity)
    return payload


async def send_row(row: dict, event: Optional[dict], dest: Destination) -> RowOutcome:
    """Abstract + SlickText pra uma linha. Não escreve no banco."""
    e164, country_code = phone_to_e164(row.get("customer_phone"))
    if not e164:
        return RowOutcome("failed", ERR_INVALID_PHONE)
    if country_code != 1:
        # SlickText só aceita EUA e Canadá; nem gasta a Abstract
        return RowOutcome("failed", ERR_REGION)

    valid = await validate_phone_abstract(e164)
    if valid is None:
        return RowOutcome("retry", ERR_ABSTRACT_API)
    if valid is False:
        return RowOutcome("failed", ERR_ABSTRACT)

    settings = get_settings()
    deactivated: list[bool] = []

    async def _deactivate(account_id: str) -> bool:
        db = await get_supabase()
        res = await (
            db.table("slicktext_accounts")
            .update(deactivation_fields())
            .eq("id", account_id)
            .eq("status", "active")
            .execute()
        )
        deactivated.append(True)
        return bool(res.data)

    result = await send_with_account_check(
        partial(
            sync_contact_to_slicktext,
            _contact_payload(event or {}, e164),
            row["customer_phone"][-4:],  # só pro log, sem o telefone inteiro
            int(dest.list_id),
            api_key=dest.account["api_key"],
            brand_id=dest.account["brand_id"],
        ),
        lambda r: r is SyncResult.ACCOUNT_ERROR,
        dest.account,
        api_url=settings.slicktext_api_url,
        deactivate=_deactivate,
    )

    if result is SyncResult.SUCCESS:
        return RowOutcome("synced", None)
    if result is SyncResult.UNSUPPORTED_REGION:
        return RowOutcome("failed", ERR_REGION)
    if result is SyncResult.ACCOUNT_ERROR:
        if deactivated:
            # conta caiu: o input para; a linha fica pra quando/se voltar
            return RowOutcome("retry", ERR_SLICKTEXT_API, stop_input=INPUT_ERR_ACCOUNT)
        # conta viva e 401/404 = a lista sumiu da SlickText
        return RowOutcome("failed", ERR_LIST_NOT_FOUND, stop_input=INPUT_ERR_LIST)
    # API_ERROR / MISSING_CREDENTIALS
    return RowOutcome("retry", ERR_SLICKTEXT_API)


def _final_outcome(outcome: RowOutcome, attempts: int) -> RowOutcome:
    """retry no limite de tentativas vira failed, mantendo o código."""
    if outcome.status == "retry" and attempts >= MAX_ATTEMPTS:
        return RowOutcome("failed", outcome.last_error, outcome.stop_input)
    return outcome


async def _claim(db) -> list[dict]:
    res = await retry_transport(
        lambda: db.rpc(
            "sms_input_claim_batch",
            {
                "p_limit": CLAIM_BATCH_SIZE,
                "p_max_attempts": MAX_ATTEMPTS,
                "p_lease_minutes": LEASE_MINUTES,
            },
        ).execute(),
        "sms_input_claim_batch",
    )
    return res.data or []


async def _send_batch(
    db,
    rows: list[dict],
    destinations: dict[str, Destination],
    stopped: set[str],
) -> None:
    by_input: dict[str, list[dict]] = {}
    for row in rows:
        by_input.setdefault(row["input_id"], []).append(row)

    events = await _fetch_events(db, list({r["event_id"] for r in rows}))
    sem = asyncio.Semaphore(SEND_CONCURRENCY)

    async def _one(row: dict, dest: Destination) -> None:
        input_id = row["input_id"]
        async with sem:
            if input_id in stopped:
                return
            try:
                outcome = await send_row(row, events.get(row["event_id"]), dest)
            except Exception:
                logger.exception(
                    f"[SlickText input] erro no envio {input_id}/{row['event_id']}"
                )
                outcome = RowOutcome("retry", ERR_SLICKTEXT_API)
            attempts = (row.get("attempts") or 0) + 1
            outcome = _final_outcome(outcome, attempts)
            await _update_row(db, row, outcome, attempts)
            if outcome.stop_input and input_id not in stopped:
                stopped.add(input_id)
                await mark_input_error(db, input_id, outcome.stop_input)

    tasks = []
    for input_id, input_rows in by_input.items():
        if input_id in stopped:
            continue
        dest = destinations.get(input_id)
        if dest is None:
            try:
                dest = await resolve_destination(
                    db, input_rows[0]["input_list_id"], check_list=False
                )
            except InputFailure as exc:
                stopped.add(input_id)
                await mark_input_error(db, input_id, exc.code)
                continue
            except TemporaryFailure as exc:
                # as linhas ficam reservadas até o lease vencer
                logger.warning(f"[SlickText input] {input_id} adiado: {exc}")
                continue
            destinations[input_id] = dest
        tasks.extend(_one(row, dest) for row in input_rows)

    await asyncio.gather(*tasks)

    for input_id in by_input:
        if input_id not in stopped:
            await _refresh_status(db, input_id)


async def send_pending_rows(time_budget_seconds: float) -> int:
    """
    Reserva e envia lotes até acabar a fila ou o tempo. Devolve quantas linhas tocou.

    `time_budget_seconds` é o prazo pra *terminar*: um lote novo só começa se o
    lote mais demorado da rodada ainda couber no que sobra (o primeiro sempre
    roda). Sem isso, um lote começado perto do fim passava do timeout do cron
    e o arq cancelava no meio: lead já enviado ficava sem update até o lease
    vencer e era reenviado (Abstract paga de novo).
    """
    db = await get_supabase()
    deadline = time.monotonic() + time_budget_seconds
    destinations: dict[str, Destination] = {}
    stopped: set[str] = set()
    total = 0
    slowest = 0.0

    while True:
        batch_start = time.monotonic()
        if total and batch_start + slowest > deadline:
            break
        rows = await _claim(db)
        if not rows:
            break
        await _send_batch(db, rows, destinations, stopped)
        slowest = max(slowest, time.monotonic() - batch_start)
        total += len(rows)

    if total:
        logger.info(f"[SlickText input] {total} linha(s) processada(s) nesta rodada.")
    return total


# --- rede de segurança ------------------------------------------------------


async def sweep_inputs() -> list[str]:
    """
    Devolve os inputs pending antigos (pra enfileirar start_input de novo) e
    chama sms_input_refresh_status nos processing (fecha input cuja fila
    terminou mas cujo refresh não rodou). As linhas pending/retry de input
    processing já voltam pelo send_pending_rows quando o lease vence.
    """
    db = await get_supabase()
    cutoff = (
        datetime.now(timezone.utc) - timedelta(minutes=STALE_PENDING_MINUTES)
    ).isoformat()

    res = await retry_transport(
        lambda: db.table("slicktext_inputs")
        .select("id")
        .eq("status", "pending")
        .lt("created_at", cutoff)
        .execute(),
        "varrer inputs pending",
    )
    stale = [r["id"] for r in res.data or []]

    res = await retry_transport(
        lambda: db.table("slicktext_inputs")
        .select("id")
        .eq("status", "processing")
        .not_.is_("enqueued_qty", "null")
        .execute(),
        "varrer inputs processing",
    )
    for r in res.data or []:
        try:
            await _refresh_status(db, r["id"])
        except Exception as exc:
            logger.error(f"[SlickText input] refresh de {r['id']} falhou: {exc}")

    return stale
