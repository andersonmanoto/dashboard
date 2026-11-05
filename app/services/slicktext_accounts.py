"""
Contas SlickText vindas do banco (ver
migrations/20261027_slicktext_accounts_vault_status.sql), usadas pelos dois
fluxos: carrinho abandonado (slicktext_service.py) e compra aprovada
(scripts/sync_approved_orders.py).

  - Só conta status='active' e allow_input=true recebe envio; a api_key vem
    do Vault pela RPC get_slicktext_credentials() (que já devolve só essas).
    allow_input é o liga/desliga manual do envio, independente do status.
  - Conta que cai: a API passa a responder 401/404. Mas 404 também é o que
    volta pra list_id que não existe ({"error":"Not found"} nos dois casos,
    conferido em 27/10), então 404 sozinho não prova que a conta caiu. Depois
    de SEND_ATTEMPTS tentativas com 401/404, confirma com GET /brands/{brand_id}
    usando a key da conta: só inativa se esse GET também der 401/404. Se a
    conta responder 200, ela está viva e o problema foi outro (ex: list_id).
    Ao inativar grava allow_input=false e deactivated_at = hora da queda
    (deactivation_fields()) e manda um alerta no Slack (#tech-sms, slack_slicktext_channel).
  - Chamadas ao Supabase passam por retry_transport(): o VPS já teve vários
    "timed out" em chamada pro Supabase, e sem retry o lead/compra se perdia.
"""

import asyncio
from datetime import datetime, timezone
from typing import Awaitable, Callable, Optional, TypeVar

import httpx
from loguru import logger

ACCOUNT_ERROR_STATUSES = frozenset({401, 404})
SEND_ATTEMPTS = 3
RETRY_BASE_DELAY_SECONDS = 2
BRAND_CHECK_TIMEOUT = 10.0
DB_ATTEMPTS = 3
DB_RETRY_BASE_DELAY_SECONDS = 1

T = TypeVar("T")


def deactivation_fields() -> dict:
    """Update de slicktext_accounts pra conta que caiu: status, envio desligado e hora da queda."""
    return {
        "status": "inactive",
        "allow_input": False,
        "deactivated_at": datetime.now(timezone.utc).isoformat(),
    }


async def retry_transport(
    call: Callable[[], Awaitable[T]], label: str, attempts: int = DB_ATTEMPTS
) -> T:
    """
    Executa `call` com retry e backoff (1s, 2s, ...) em httpx.TransportError
    (timeout, conexão caída, DNS). Outros erros sobem direto. Esgotadas as
    tentativas, relança o último erro.
    """
    for attempt in range(1, attempts + 1):
        try:
            return await call()
        except httpx.TransportError as exc:
            if attempt == attempts:
                logger.error(
                    f"[SlickText] {label}: esgotou {attempts} tentativas "
                    f"({type(exc).__name__}): {exc}"
                )
                raise
            wait = DB_RETRY_BASE_DELAY_SECONDS * 2 ** (attempt - 1)
            logger.warning(
                f"[SlickText] {label}: tentativa {attempt}/{attempts} falhou "
                f"({type(exc).__name__}). Retentando em {wait}s..."
            )
            await asyncio.sleep(wait)
    raise AssertionError("unreachable")


async def notify_account_deactivated(account: dict) -> None:
    """Alerta no Slack (slack_slicktext_channel). Falha no Slack só loga."""
    try:
        from slack_sdk import WebClient

        from app.config import get_settings

        settings = get_settings()
        client = WebClient(token=settings.slack_bot_token)
        await asyncio.to_thread(
            client.chat_postMessage,
            channel=settings.slack_slicktext_channel,
            username="SlickText Monitor",
            icon_url="https://tigeroffers.com/assets/img/top-tiger.png",
            text=(
                f"SlickText {account['name']} (brand_id {account['brand_id']}).\n"
                f"A comunicação foi negada, conta possivelmente desativada."
            ),
        )
    except Exception as exc:
        logger.error(
            f"[SlickText] Não consegui mandar o alerta de conta desativada "
            f"'{account['name']}' pro Slack: {exc}"
        )


def credentials_by_account(rows: Optional[list[dict]]) -> dict[str, dict]:
    """Linhas da get_slicktext_credentials() -> {account_id: {id, name, brand_id, api_key}}."""
    return {r["id"]: r for r in rows or [] if r.get("id") and r.get("api_key")}


def active_list_targets(
    raw_mappings, list_field: str, credentials: dict[str, dict]
) -> list[tuple[str, dict]]:
    """
    (list_id, conta) pra cada linha de slicktext_product_lists do produto com
    `list_field` preenchido e conta active (presente em `credentials`).
    """
    if isinstance(raw_mappings, dict):
        raw_mappings = [raw_mappings]
    targets = []
    for mapping in raw_mappings or []:
        list_id = mapping.get(list_field)
        account_id = (mapping.get("slicktext_accounts") or {}).get("id")
        if list_id and account_id in credentials:
            targets.append((list_id, credentials[account_id]))
    return targets


async def brand_is_down(api_url: str, account: dict) -> bool:
    """
    GET /brands/{brand_id} com a key da conta: 401/404 = conta caiu.
    Erro de rede ou qualquer outro status não conta como queda (na dúvida,
    não inativa).
    """
    try:
        async with httpx.AsyncClient(timeout=BRAND_CHECK_TIMEOUT) as client:
            resp = await client.get(
                f"{api_url}/brands/{account['brand_id']}",
                headers={"Authorization": f"Bearer {account['api_key']}"},
            )
    except httpx.RequestError as exc:
        logger.warning(
            f"[SlickText] Não deu pra checar a conta '{account['name']}' "
            f"(brand_id {account['brand_id']}): {exc}"
        )
        return False
    return resp.status_code in ACCOUNT_ERROR_STATUSES


async def send_with_account_check(
    send: Callable[[], Awaitable[T]],
    is_account_error: Callable[[T], bool],
    account: dict,
    *,
    api_url: str,
    deactivate: Callable[[str], Awaitable[bool]],
) -> T:
    """
    Chama `send` até SEND_ATTEMPTS vezes enquanto o resultado for erro de
    conta (401/404). Se todas derem erro de conta e o GET /brands confirmar a
    queda, chama `deactivate(account_id)` e, se ela de fato mudou a linha
    (True), manda o alerta no Slack. `deactivate` só deve mexer em conta
    ainda active, pra dois jobs simultâneos não mandarem alerta duplicado.
    Devolve o último resultado.
    """
    result = await send()
    for attempt in range(1, SEND_ATTEMPTS + 1):
        if not is_account_error(result):
            return result
        logger.warning(
            f"[SlickText] Conta '{account['name']}' (brand_id {account['brand_id']}) "
            f"respondeu 401/404 (tentativa {attempt}/{SEND_ATTEMPTS})."
        )
        if attempt == SEND_ATTEMPTS:
            break
        await asyncio.sleep(RETRY_BASE_DELAY_SECONDS * attempt)
        result = await send()

    if await brand_is_down(api_url, account):
        logger.error(
            f"[SlickText] Conta '{account['name']}' (brand_id {account['brand_id']}) "
            f"caiu: GET /brands também respondeu 401/404. Inativando."
        )
        changed = await retry_transport(
            lambda: deactivate(account["id"]), f"inativar conta '{account['name']}'"
        )
        if changed:
            await notify_account_deactivated(account)
        else:
            logger.info(
                f"[SlickText] Conta '{account['name']}' já estava inativa; "
                f"sem alerta no Slack."
            )
    else:
        logger.error(
            f"[SlickText] Conta '{account['name']}' (brand_id {account['brand_id']}) "
            f"respondeu 401/404 {SEND_ATTEMPTS}x no envio, mas GET /brands não "
            f"confirmou a queda -- NÃO inativada. Conferir o list_id da conta."
        )
    return result
