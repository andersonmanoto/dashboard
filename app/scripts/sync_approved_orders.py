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
from typing import Optional
from loguru import logger
from phonenumbers import NumberParseException, PhoneNumberFormat

# Importação do client ASSÍNCRONO do Supabase
from supabase import AsyncClient, create_async_client

from app.config import get_settings, get_slicktext_api_key

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
                else:
                    if "us and ca" in error_msg:
                        logger.error(
                            f"SlickText rejeitou a região do telefone para {customer}."
                        )
                        return SyncResult.UNSUPPORTED_REGION
                    logger.error(f"Erro HTTP {status} na criação: {error_msg}")
                    return SyncResult.API_ERROR

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
                return SyncResult.API_ERROR

    except httpx.RequestError as exc:
        logger.error(f"Erro de rede SlickText: {exc}")
        return SyncResult.API_ERROR


async def process_queue_item(item: dict) -> None:
    db = await get_supabase()
    queue_id = item["event_id"]
    attempts = item.get("attempts", 0)

    event_data = item.get("events")
    if not event_data:
        await (
            db.table("slicktext_sync_queue")
            .update(
                {"status": "failed", "last_error": "Dados do evento não encontrados"}
            )
            .eq("event_id", queue_id)
            .execute()
        )
        return

    customer = event_data.get("customer_name", "Desconhecido")

    if attempts >= MAX_ATTEMPTS:
        logger.error(f"{customer} atingiu o limite de tentativas.")
        await (
            db.table("slicktext_sync_queue")
            .update({"status": "failed", "last_error": "max_attempts"})
            .eq("event_id", queue_id)
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
        .eq("event_id", queue_id)
        .execute()
    )

    # Extração
    raw_phone = event_data.get("customer_phone")
    products_data = event_data.get("products") or {}
    product_name = products_data.get("name")
    checkouts_data = event_data.get("checkouts") or {}
    quantity = checkouts_data.get("quantity")

    slicktext_mapping = products_data.get("slicktext_product_lists") or {}
    list_id = slicktext_mapping.get("approved_list_id")
    if not list_id:
        logger.warning(f"Lista não mapeada para o produto: {product_name}")
        await (
            db.table("slicktext_sync_queue")
            .update({"status": "failed", "last_error": "lista_nao_encontrada"})
            .eq("event_id", queue_id)
            .execute()
        )
        return

    account_info = slicktext_mapping.get("slicktext_accounts") or {}
    account_name = account_info.get("name")
    brand_id = account_info.get("brand_id")
    api_key = get_slicktext_api_key(account_name, SETTINGS)

    if not api_key or not brand_id:
        logger.error(
            f"Credenciais SlickText não configuradas para a conta '{account_name}' "
            f"(produto '{product_name}')."
        )
        await (
            db.table("slicktext_sync_queue")
            .update({"status": "failed", "last_error": "credenciais_nao_configuradas"})
            .eq("event_id", queue_id)
            .execute()
        )
        return

    # Validação
    formatted_phone = format_phone_local(raw_phone)
    if not formatted_phone:
        await (
            db.table("slicktext_sync_queue")
            .update({"status": "failed", "last_error": "telefone_invalido"})
            .eq("event_id", queue_id)
            .execute()
        )
        return

    abstract_valid = await validate_phone_abstract(formatted_phone)
    if abstract_valid is None:
        await (
            db.table("slicktext_sync_queue")
            .update({"status": "retry", "last_error": "abstract_api_error"})
            .eq("event_id", queue_id)
            .execute()
        )
        return
    if abstract_valid is False:
        await (
            db.table("slicktext_sync_queue")
            .update({"status": "failed", "last_error": "rejeitado_abstract"})
            .eq("event_id", queue_id)
            .execute()
        )
        return

    # Payload
    payload = {
        "first_name": customer,
        "mobile_number": formatted_phone,
        "opt_in_status": "subscribed",
        "produto": product_name,
        "bottles": str(quantity),
    }

    # Sincronização
    result = await sync_contact_to_slicktext(
        payload, customer, list_id, api_key=api_key, brand_id=brand_id
    )

    if result == SyncResult.SUCCESS:
        await (
            db.table("slicktext_sync_queue")
            .update({"status": "synced", "last_error": None})
            .eq("event_id", queue_id)
            .execute()
        )
    elif result == SyncResult.UNSUPPORTED_REGION:
        await (
            db.table("slicktext_sync_queue")
            .update({"status": "failed", "last_error": "regiao_nao_suportada"})
            .eq("event_id", queue_id)
            .execute()
        )
    elif result == SyncResult.API_ERROR:
        await (
            db.table("slicktext_sync_queue")
            .update({"status": "retry", "last_error": "slicktext_api_error"})
            .eq("event_id", queue_id)
            .execute()
        )
    elif result == SyncResult.MISSING_CREDENTIALS:
        await (
            db.table("slicktext_sync_queue")
            .update({"status": "synced", "last_error": "missing_credentials_simulacao"})
            .eq("event_id", queue_id)
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
                .select("event_id")
                .in_("status", ["pending", "retry"])
                .lt("attempts", MAX_ATTEMPTS)
                .order("created_at", desc=True)
                .limit(limit)
                .execute()
            )
            logger.info(
                f"fetch_pending_ids: concluída em {time.monotonic() - start:.2f}s"
            )
            return [row["event_id"] for row in (res.data or [])]

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
    res = await (
        db.table("slicktext_sync_queue")
        .select(
            "event_id, attempts, events(customer_name, customer_phone, "
            "products(name, slicktext_product_lists(approved_list_id, "
            "slicktext_accounts(name, brand_id))), checkouts(quantity))"
        )
        .eq("event_id", queue_id)
        .single()
        .execute()
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
        await process_single_item(queue_id)

    logger.info("Rodada concluída.")


if __name__ == "__main__":
    asyncio.run(run_pipeline())
