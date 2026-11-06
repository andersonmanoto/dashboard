"""
Pipeline isolada para Sincronização de Compras Aprovadas no SlickText.
100% Assíncrona: Utiliza create_async_client do Supabase e httpx para APIs externas,
evitando RemoteProtocolError (corrupção de sockets HTTP/2 em threads).
"""

from __future__ import annotations

import asyncio
import httpx
import phonenumbers
from enum import Enum, auto
from datetime import datetime, timezone
from functools import partial
from typing import Optional
from loguru import logger
from phonenumbers import NumberParseException, PhoneNumberFormat

# Importação do client ASSÍNCRONO do Supabase
from supabase import AsyncClient, create_async_client

from app.config import get_settings
from app.services.slicktext_accounts import (
    ACCOUNT_ERROR_STATUSES,
    active_list_targets,
    credentials_by_account,
    deactivation_fields,
    split_queue_rows,
    target_fields,
    retry_transport,
    send_with_account_check,
)

SETTINGS = get_settings()

# Instância Singleton do cliente assíncrono para ser reaproveitado de forma segura no event loop
_supabase_async: Optional[AsyncClient] = None


async def get_supabase() -> AsyncClient:
    global _supabase_async
    if _supabase_async is None:
        _supabase_async = await create_async_client(
            SETTINGS.supabase_url, SETTINGS.supabase_key
        )
    return _supabase_async


MAX_ATTEMPTS = 3
SLICKTEXT_TIMEOUT = 10.0
ABSTRACT_TIMEOUT = 10.0


class SyncResult(Enum):
    SUCCESS = auto()
    MISSING_CREDENTIALS = auto()
    API_ERROR = auto()
    UNSUPPORTED_REGION = auto()
    ACCOUNT_ERROR = auto()  # 401/404: a conta pode ter caído (ver slicktext_accounts.py)


def _status_error_result(exc: httpx.HTTPStatusError) -> SyncResult:
    if exc.response.status_code in ACCOUNT_ERROR_STATUSES:
        return SyncResult.ACCOUNT_ERROR
    return SyncResult.API_ERROR


async def _deactivate_account(account_id: str) -> bool:
    """Inativa só se ainda estiver active; True se mudou a linha."""
    db = await get_supabase()
    res = await (
        db.table("slicktext_accounts")
        .update(deactivation_fields())
        .eq("id", account_id)
        .eq("status", "active")
        .execute()
    )
    return bool(res.data)


async def _assign_targets(
    db: AsyncClient, item: dict, targets: list[tuple[str, dict]]
) -> None:
    """A linha do lead fica com a primeira lista; as outras viram linhas novas."""
    first, *others = targets
    if others:
        row = {"lead_type": "neworder", "event_id": item["event_id"]}
        await retry_transport(
            lambda: db.table("slicktext_sync_queue")
            .upsert(
                split_queue_rows(row, others),
                on_conflict="event_id,list_id",
                ignore_duplicates=True,
            )
            .execute(),
            "criar linhas por lista",
        )
    await retry_transport(
        lambda: db.table("slicktext_sync_queue")
        .update(target_fields(*first))
        .eq("id", item["id"])
        .execute(),
        "gravar lista na linha do lead",
    )


async def _mark_retry_safely(db: AsyncClient, queue_id: str, error: str) -> None:
    """Marca o item como retry; se nem isso der (Supabase fora), só loga --
    o item continua pending/retry e a próxima rodada pega de novo."""
    try:
        await (
            db.table("slicktext_sync_queue")
            .update({"status": "retry", "last_error": error})
            .eq("id", queue_id)
            .execute()
        )
    except Exception as exc:
        logger.error(f"Não consegui marcar {queue_id} como retry: {exc}")


def format_phone_local(
    raw_phone: Optional[str], country_code: str = "US"
) -> Optional[str]:
    if not raw_phone:
        return None
    try:
        parsed = phonenumbers.parse(raw_phone, country_code)
        if not phonenumbers.is_valid_number(parsed):
            return None
        return phonenumbers.format_number(parsed, PhoneNumberFormat.E164)
    except NumberParseException:
        return None


async def validate_phone_abstract(formatted_phone: str) -> Optional[bool]:
    if not SETTINGS.abstract_api_key:
        logger.error("ABSTRACT_API_KEY não configurada.")
        return None

    try:
        async with httpx.AsyncClient(timeout=ABSTRACT_TIMEOUT) as client:
            response = await client.get(
                "https://phoneintelligence.abstractapi.com/v1/",
                params={"api_key": SETTINGS.abstract_api_key, "phone": formatted_phone},
            )

        if response.status_code == 401:
            logger.error("AbstractAPI 401: Chave recusada.")
            return None

        response.raise_for_status()
        data = response.json()

        is_valid = data.get("phone_validation", {}).get("is_valid", False)
        is_active = (
            str(data.get("phone_validation", {}).get("line_status", "")).lower()
            == "active"
        )
        is_mobile = (
            str(data.get("phone_carrier", {}).get("line_type", "")).lower() == "mobile"
        )

        if is_mobile and is_valid and is_active:
            return True

        logger.warning(f"Telefone {formatted_phone} rejeitado pela AbstractAPI.")
        return False
    except httpx.RequestError as exc:
        logger.error(f"Erro na requisição da AbstractAPI: {exc}")
        return None
    except httpx.HTTPStatusError as exc:
        logger.error(f"Erro HTTP na AbstractAPI: {exc}")
        logger.error(f"Resposta de erro da AbstractAPI: {exc.response.text}")
        return None


async def sync_contact_to_slicktext(
    payload: dict,
    customer: str,
    target_list_id: int,
    *,
    api_key: str,
    brand_id: str,
) -> SyncResult:
    if not api_key or not brand_id:
        logger.error("Credenciais do SlickText não configuradas.")
        return SyncResult.MISSING_CREDENTIALS

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    base_url = f"{SETTINGS.slicktext_api_url}/brands/{brand_id}"
    mobile_number = payload.get("mobile_number")
    contact_id = None

    try:
        async with httpx.AsyncClient(
            headers=headers, timeout=SLICKTEXT_TIMEOUT
        ) as client:
            # 1. Criar
            try:
                resp_create = await client.post(f"{base_url}/contacts", json=payload)
                resp_create.raise_for_status()
                contact_id = resp_create.json().get("contact_id")
                logger.info(f"Contato {customer} criado com ID {contact_id}.")

            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code
                error_msg = exc.response.text.lower()
                is_duplicate = status == 409 or (
                    status in (400, 422)
                    and ("exists" in error_msg or "already" in error_msg)
                )

                if is_duplicate:
                    logger.info(f"Contato {mobile_number} já existe. Buscando ID...")
                    try:
                        resp_search = await client.get(
                            f"{base_url}/contacts",
                            params={"mobile_number": mobile_number},
                        )
                        resp_search.raise_for_status()

                        for contact in resp_search.json().get("data", []):
                            if contact.get("mobile_number") == mobile_number:
                                contact_id = contact.get("contact_id")
                                break

                        if contact_id:
                            resp_update = await client.put(
                                f"{base_url}/contacts/{contact_id}",
                                json=payload,
                            )
                            resp_update.raise_for_status()
                            logger.info(f"Contato {contact_id} atualizado com sucesso!")
                        else:
                            logger.error(
                                f"Falha: {mobile_number} não encontrado na busca."
                            )
                            return SyncResult.API_ERROR

                    except httpx.RequestError as update_exc:
                        logger.error(
                            f"Erro ao buscar/atualizar contato existente: {update_exc}"
                        )
                        return SyncResult.API_ERROR
                    except httpx.HTTPStatusError as update_exc:
                        logger.error(
                            f"Erro HTTP ao buscar/atualizar contato existente: "
                            f"{update_exc.response.status_code} {update_exc.response.text}"
                        )
                        return _status_error_result(update_exc)
                else:
                    if "us and ca" in error_msg:
                        logger.error(
                            f"SlickText rejeitou a região do telefone para {customer}."
                        )
                        return SyncResult.UNSUPPORTED_REGION
                    logger.error(f"Erro HTTP {status} na criação: {error_msg}")
                    return _status_error_result(exc)

            if not contact_id:
                return SyncResult.API_ERROR

            # 2. Lista
            try:
                resp_list = await client.post(
                    f"{base_url}/lists/contacts",
                    json=[{"contact_id": contact_id, "lists": [target_list_id]}],
                )
                resp_list.raise_for_status()
                logger.info(f"{customer} adicionado à lista {target_list_id}.")
                return SyncResult.SUCCESS
            except httpx.RequestError as exc:
                logger.error(f"Erro ao adicionar na lista: {exc}")
                return SyncResult.API_ERROR
            except httpx.HTTPStatusError as exc:
                logger.error(f"Erro HTTP ao adicionar na lista: {exc.response.text}")
                return _status_error_result(exc)

    except httpx.RequestError as exc:
        logger.error(f"Erro de rede SlickText: {exc}")
        return SyncResult.API_ERROR


async def process_queue_item(item: dict) -> None:
    db = await get_supabase()
    queue_id = item["id"]
    attempts = item.get("attempts", 0)
    list_id = item.get("list_id")  # preenchido = telefone já validado

    event_data = item.get("events")
    if not event_data:
        await (
            db.table("slicktext_sync_queue")
            .update(
                {"status": "failed", "last_error": "Dados do evento não encontrados"}
            )
            .eq("id", queue_id)
            .execute()
        )
        return

    customer = event_data.get("customer_name", "Desconhecido")

    if attempts >= MAX_ATTEMPTS:
        logger.error(f"{customer} atingiu o limite de tentativas.")
        await (
            db.table("slicktext_sync_queue")
            .update({"status": "failed", "last_error": "max_attempts"})
            .eq("id", queue_id)
            .execute()
        )
        return

    # Aumenta tentativa
    await (
        db.table("slicktext_sync_queue")
        .update(
            {
                "attempts": attempts + 1,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
        )
        .eq("id", queue_id)
        .execute()
    )

    # Extração
    raw_phone = event_data.get("customer_phone")
    products_data = event_data.get("products") or {}
    product_name = products_data.get("name")
    checkouts_data = event_data.get("checkouts") or {}
    quantity = checkouts_data.get("quantity")

    # Pega as contas SlickText mapeadas pro produto (pode ser mais de uma --
    # cada conta active recebe a MESMA informação na sua lista, numa linha
    # própria da fila; a api_key vem do Vault)
    raw_mappings = products_data.get("slicktext_product_lists") or []
    if isinstance(raw_mappings, dict):
        raw_mappings = [raw_mappings]

    mappings = [m for m in raw_mappings if m.get("approved_list_id")]
    if not list_id and not mappings:
        logger.warning(f"Lista não mapeada para o produto: {product_name}")
        await (
            db.table("slicktext_sync_queue")
            .update({"status": "failed", "last_error": "lista_nao_encontrada"})
            .eq("id", queue_id)
            .execute()
        )
        return

    try:
        credentials = credentials_by_account(
            (
                await retry_transport(
                    lambda: db.rpc("get_slicktext_credentials").execute(),
                    "get_slicktext_credentials",
                )
            ).data
        )
    except Exception as exc:
        # attempts já foi incrementado: volta pra retry e a próxima rodada tenta
        logger.error(f"Erro ao buscar credenciais SlickText ({queue_id}): {exc}")
        await _mark_retry_safely(db, queue_id, "credenciais_indisponiveis")
        return
    if list_id:
        account = credentials.get(item.get("slick_account_id"))
        if not account:
            logger.warning(f"Conta da lista {list_id} não está mais active.")
            await (
                db.table("slicktext_sync_queue")
                .update({"status": "skipped", "last_error": "conta_inativa"})
                .eq("id", queue_id)
                .execute()
            )
            return
        targets = [(list_id, account)]
    else:
        targets = active_list_targets(mappings, "approved_list_id", credentials)
    if not targets:
        logger.warning(
            f"Nenhuma conta active entre as {len(mappings)} com lista mapeada "
            f"para o produto: {product_name}"
        )
        await (
            db.table("slicktext_sync_queue")
            .update({"status": "failed", "last_error": "sem_conta_active"})
            .eq("id", queue_id)
            .execute()
        )
        return

    # Validação
    formatted_phone = format_phone_local(raw_phone)
    if not formatted_phone:
        await (
            db.table("slicktext_sync_queue")
            .update({"status": "failed", "last_error": "telefone_invalido"})
            .eq("id", queue_id)
            .execute()
        )
        return

    if not list_id:
        abstract_valid = await validate_phone_abstract(formatted_phone)
        if abstract_valid is None:
            await (
                db.table("slicktext_sync_queue")
                .update({"status": "retry", "last_error": "abstract_api_error"})
                .eq("id", queue_id)
                .execute()
            )
            return
        if abstract_valid is False:
            await (
                db.table("slicktext_sync_queue")
                .update({"status": "failed", "last_error": "rejeitado_abstract"})
                .eq("id", queue_id)
                .execute()
            )
            return

        try:
            await _assign_targets(db, item, targets)
        except Exception as exc:
            logger.error(f"Erro ao dividir {queue_id} por lista: {exc}")
            await _mark_retry_safely(db, queue_id, "erro_ao_dividir_listas")
            return

    # Payload
    payload = {
        "first_name": customer,
        "mobile_number": formatted_phone,
        "opt_in_status": "subscribed",
        "produto": product_name,
        "bottles": str(quantity),
    }

    # Sincronização: esta linha envia só pra primeira lista; as outras têm
    # linha própria e saem na rodada seguinte
    list_id, account = targets[0]
    result = await send_with_account_check(
        partial(
            sync_contact_to_slicktext,
            payload,
            customer,
            list_id,
            api_key=account["api_key"],
            brand_id=account["brand_id"],
        ),
        lambda r: r is SyncResult.ACCOUNT_ERROR,
        account,
        api_url=SETTINGS.slicktext_api_url,
        deactivate=_deactivate_account,
    )

    # Região não suportada é uma característica do telefone, então nenhuma
    # tentativa nova resolve. Erro de conta vai pra retry: se a conta foi
    # inativada, a próxima rodada marca a linha como skipped.
    if result is SyncResult.API_ERROR:
        await (
            db.table("slicktext_sync_queue")
            .update({"status": "retry", "last_error": "slicktext_api_error"})
            .eq("id", queue_id)
            .execute()
        )
    elif result is SyncResult.ACCOUNT_ERROR:
        await (
            db.table("slicktext_sync_queue")
            .update({"status": "retry", "last_error": "slicktext_account_error"})
            .eq("id", queue_id)
            .execute()
        )
    elif result is SyncResult.UNSUPPORTED_REGION:
        await (
            db.table("slicktext_sync_queue")
            .update({"status": "failed", "last_error": "regiao_nao_suportada"})
            .eq("id", queue_id)
            .execute()
        )
    elif result is SyncResult.MISSING_CREDENTIALS:
        await (
            db.table("slicktext_sync_queue")
            .update({"status": "synced", "last_error": "missing_credentials_simulacao"})
            .eq("id", queue_id)
            .execute()
        )
    else:
        await (
            db.table("slicktext_sync_queue")
            .update(
                {
                    "status": "synced",
                    "last_error": None,
                    "sent_at": datetime.now(timezone.utc).isoformat(),
                }
            )
            .eq("id", queue_id)
            .execute()
        )


async def fetch_pending_ids(limit: int = 15, max_retries: int = 2) -> list[str]:
    import time

    db = await get_supabase()

    for attempt in range(1, max_retries + 1):
        start = time.monotonic()
        try:
            res = await (
                db.table("slicktext_sync_queue")
                .select("id")
                .eq("lead_type", "neworder")
                .in_("status", ["pending", "retry"])
                .lt("attempts", MAX_ATTEMPTS)
                .order("created_at", desc=True)
                .limit(limit)
                .execute()
            )
            logger.info(
                f"fetch_pending_ids: concluída em {time.monotonic() - start:.2f}s"
            )
            return [row["id"] for row in (res.data or [])]

        except (httpx.ConnectTimeout, httpx.ReadTimeout, httpx.ConnectError) as exc:
            wait = 2**attempt
            logger.warning(
                f"fetch_pending_ids: tentativa {attempt}/{max_retries} falhou "
                f"após {time.monotonic() - start:.2f}s ({type(exc).__name__}). "
                f"Retentando em {wait}s..."
            )
            if attempt == max_retries:
                logger.error(f"fetch_pending_ids: esgotou {max_retries} tentativas.")
                raise
            await asyncio.sleep(wait)


async def process_single_item(queue_id: str) -> None:
    db = await get_supabase()
    res = await retry_transport(
        lambda: db.table("slicktext_sync_queue")
        .select(
            "id, event_id, attempts, list_id, slick_account_id, "
            "events(customer_name, customer_phone, "
            "products(name, slicktext_product_lists(approved_list_id, "
            "slicktext_accounts(id))), checkouts(quantity))"
        )
        .eq("id", queue_id)
        .single()
        .execute(),
        f"buscar item {queue_id} da fila",
    )

    item = res.data
    if not item:
        logger.warning(f"Item {queue_id} não encontrado na fila (já processado?).")
        return

    await process_queue_item(item)


async def run_pipeline() -> None:
    logger.info("Buscando aprovações pendentes para o SlickText...")

    pending_ids = await fetch_pending_ids(limit=15)
    if not pending_ids:
        logger.info("Nada pendente na fila de Compras Aprovadas.")
        return

    logger.info(f"Processando {len(pending_ids)} compra(s) aprovada(s)...")

    for queue_id in pending_ids:
        # um item com erro não derruba o resto da rodada (no worker cada item
        # já é um job separado; isso aqui é pra rodar o script na mão)
        try:
            await process_single_item(queue_id)
        except Exception:
            logger.exception(f"Falha ao processar item {queue_id}")

    logger.info("Rodada concluída.")


if __name__ == "__main__":
    asyncio.run(run_pipeline())
