"""
Contas SlickText vindas do banco (ver
migrations/20261027_slicktext_accounts_vault_status.sql), usadas pelos dois
fluxos: carrinho abandonado (slicktext_service.py) e compra aprovada
(scripts/sync_approved_orders.py).

  - Só conta status='active' recebe envio; a api_key vem do Vault pela RPC
    get_slicktext_credentials() (que já devolve só as active).
  - Conta que cai: a API passa a responder 401/404. Mas 404 também é o que
    volta pra list_id que não existe ({"error":"Not found"} nos dois casos,
    conferido em 27/10), então 404 sozinho não prova que a conta caiu. Depois
    de SEND_ATTEMPTS tentativas com 401/404, confirma com GET /brands/{brand_id}
    usando a key da conta: só inativa se esse GET também der 401/404. Se a
    conta responder 200, ela está viva e o problema foi outro (ex: list_id).
"""

import asyncio
from typing import Awaitable, Callable, Optional, TypeVar

import httpx
from loguru import logger

ACCOUNT_ERROR_STATUSES = frozenset({401, 404})
SEND_ATTEMPTS = 3
RETRY_BASE_DELAY_SECONDS = 2
BRAND_CHECK_TIMEOUT = 10.0

T = TypeVar("T")


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
    deactivate: Callable[[str], Awaitable[None]],
) -> T:
    """
    Chama `send` até SEND_ATTEMPTS vezes enquanto o resultado for erro de
    conta (401/404). Se todas derem erro de conta e o GET /brands confirmar a
    queda, chama `deactivate(account_id)`. Devolve o último resultado.
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
        await deactivate(account["id"])
    else:
        logger.error(
            f"[SlickText] Conta '{account['name']}' (brand_id {account['brand_id']}) "
            f"respondeu 401/404 {SEND_ATTEMPTS}x no envio, mas GET /brands não "
            f"confirmou a queda -- NÃO inativada. Conferir o list_id da conta."
        )
    return result
