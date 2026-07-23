"""
Pipeline isolada para Sincronização de Compras Aprovadas no SlickText.
Deve ser executada via Cron Job ou agendador (ex: de 1 em 1 hora).
"""

from __future__ import annotations

import requests
import phonenumbers
from enum import Enum, auto
from datetime import datetime, timezone
from typing import Optional
from loguru import logger
from phonenumbers import NumberParseException, PhoneNumberFormat
from supabase import Client, create_client

from app.config import get_settings, get_approved_list_id_for_product

SETTINGS = get_settings()
supabase: Client = create_client(SETTINGS.supabase_url, SETTINGS.supabase_key)

MAX_ATTEMPTS = 3
SLICKTEXT_TIMEOUT = 10
ABSTRACT_TIMEOUT = 10


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


def validate_phone_abstract(formatted_phone: str) -> Optional[bool]:
    if not SETTINGS.abstract_api_key:
        logger.error("ABSTRACT_API_KEY não configurada.")
        return None

    try:
        response = requests.get(
            "https://phoneintelligence.abstractapi.com/v1/",
            params={"api_key": SETTINGS.abstract_api_key, "phone": formatted_phone},
            timeout=ABSTRACT_TIMEOUT,
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
    except requests.RequestException as exc:
        logger.error(f"Erro na AbstractAPI: {exc}")
        return None


def sync_contact_to_slicktext(
    payload: dict, customer: str, target_list_id: int
) -> SyncResult:
    if not SETTINGS.slicktext_api_key or not SETTINGS.slicktext_brand_id:
        logger.error("Credenciais do SlickText não configuradas.")
        return SyncResult.MISSING_CREDENTIALS

    headers = {
        "Authorization": f"Bearer {SETTINGS.slicktext_api_key}",
        "Content-Type": "application/json",
    }

    base_url = f"{SETTINGS.slicktext_api_url}/brands/{SETTINGS.slicktext_brand_id}"
    mobile_number = payload.get("mobile_number")
    contact_id = None

    try:
        # 1. Criar
        resp_create = requests.post(
            f"{base_url}/contacts",
            json=payload,
            headers=headers,
            timeout=SLICKTEXT_TIMEOUT,
        )
        resp_create.raise_for_status()
        contact_id = resp_create.json().get("contact_id")
        logger.info(f"Contato {customer} criado com ID {contact_id}.")

    except requests.exceptions.HTTPError as exc:
        status = exc.response.status_code
        error_msg = exc.response.text.lower()
        is_duplicate = status == 409 or (
            status in (400, 422) and ("exists" in error_msg or "already" in error_msg)
        )

        if is_duplicate:
            logger.info(f"Contato {mobile_number} já existe. Buscando ID...")
            try:
                # Driblando a paginação pelo filtro exato
                resp_search = requests.get(
                    f"{base_url}/contacts",
                    params={"mobile_number": mobile_number},
                    headers=headers,
                    timeout=SLICKTEXT_TIMEOUT,
                )
                resp_search.raise_for_status()

                for contact in resp_search.json().get("data", []):
                    if contact.get("mobile_number") == mobile_number:
                        contact_id = contact.get("contact_id")
                        break

                if contact_id:
                    requests.put(
                        f"{base_url}/contacts/{contact_id}",
                        json=payload,
                        headers=headers,
                        timeout=SLICKTEXT_TIMEOUT,
                    ).raise_for_status()
                    logger.info(f"Contato {contact_id} atualizado com sucesso!")
                else:
                    logger.error(f"Falha: {mobile_number} não encontrado na busca.")
                    return SyncResult.API_ERROR

            except requests.RequestException as update_exc:
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

    except requests.RequestException as exc:
        logger.error(f"Erro de rede SlickText: {exc}")
        return SyncResult.API_ERROR

    if not contact_id:
        return SyncResult.API_ERROR

    # 2. Lista
    try:
        requests.post(
            f"{base_url}/lists/contacts",
            json=[{"contact_id": contact_id, "lists": [target_list_id]}],
            headers=headers,
            timeout=SLICKTEXT_TIMEOUT,
        ).raise_for_status()
        logger.info(f"{customer} adicionado à lista {target_list_id}.")
        return SyncResult.SUCCESS
    except requests.RequestException as exc:
        logger.error(f"Erro ao adicionar na lista: {exc}")
        return SyncResult.API_ERROR


def process_queue_item(item: dict) -> None:
    queue_id = item["event_id"]  # O ID da fila é o próprio event_id
    attempts = item.get("attempts", 0)

    # Tratando a estrutura aninhada do Supabase
    event_data = item.get("events")
    if not event_data:
        supabase.table("slicktext_sync_queue").update(
            {"status": "failed", "last_error": "Dados do evento não encontrados"}
        ).eq("event_id", queue_id).execute()
        return

    customer = event_data.get("customer_name", "Desconhecido")

    if attempts >= MAX_ATTEMPTS:
        logger.error(f"{customer} atingiu o limite de tentativas.")
        supabase.table("slicktext_sync_queue").update(
            {"status": "failed", "last_error": "max_attempts"}
        ).eq("event_id", queue_id).execute()
        return

    # Aumenta tentativa
    supabase.table("slicktext_sync_queue").update(
        {"attempts": attempts + 1, "updated_at": datetime.now(timezone.utc).isoformat()}
    ).eq("event_id", queue_id).execute()

    # Extração
    raw_phone = event_data.get("customer_phone")
    products_data = event_data.get("products") or {}
    product_name = products_data.get("name")
    checkouts_data = event_data.get("checkouts") or {}
    quantity = checkouts_data.get("quantity")

    list_id = get_approved_list_id_for_product(product_name)
    if not list_id:
        logger.warning(f"Lista não mapeada para o produto: {product_name}")
        supabase.table("slicktext_sync_queue").update(
            {"status": "failed", "last_error": "lista_nao_encontrada"}
        ).eq("event_id", queue_id).execute()
        return

    # Validação
    formatted_phone = format_phone_local(raw_phone)
    if not formatted_phone:
        supabase.table("slicktext_sync_queue").update(
            {"status": "failed", "last_error": "telefone_invalido"}
        ).eq("event_id", queue_id).execute()
        return

    abstract_valid = validate_phone_abstract(formatted_phone)
    if abstract_valid is None:
        supabase.table("slicktext_sync_queue").update(
            {"status": "retry", "last_error": "abstract_api_error"}
        ).eq("event_id", queue_id).execute()
        return
    if abstract_valid is False:
        supabase.table("slicktext_sync_queue").update(
            {"status": "failed", "last_error": "rejeitado_abstract"}
        ).eq("event_id", queue_id).execute()
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
    result = sync_contact_to_slicktext(payload, customer, list_id)

    if result == SyncResult.SUCCESS:
        supabase.table("slicktext_sync_queue").update(
            {"status": "synced", "last_error": None}
        ).eq("event_id", queue_id).execute()
    elif result == SyncResult.UNSUPPORTED_REGION:
        supabase.table("slicktext_sync_queue").update(
            {"status": "failed", "last_error": "regiao_nao_suportada"}
        ).eq("event_id", queue_id).execute()
    elif result == SyncResult.API_ERROR:
        supabase.table("slicktext_sync_queue").update(
            {"status": "retry", "last_error": "slicktext_api_error"}
        ).eq("event_id", queue_id).execute()
    elif result == SyncResult.MISSING_CREDENTIALS:
        supabase.table("slicktext_sync_queue").update(
            {"status": "synced", "last_error": "missing_credentials_simulacao"}
        ).eq("event_id", queue_id).execute()


def run_pipeline() -> None:
    logger.info("Buscando aprovações pendentes para o SlickText...")

    # Query que traz os dados da Fila aninhados com os dados do Evento (via FK)
    res = (
        supabase.table("slicktext_sync_queue")
        .select(
            "event_id, attempts, events(customer_name, customer_phone, products(name), checkouts(quantity))"
        )
        .in_("status", ["pending", "retry"])
        .lt("attempts", MAX_ATTEMPTS)
        .order("created_at", desc=True)
        .limit(15)
        .execute()
    )

    pending = res.data or []
    if not pending:
        logger.info("Nada pendente na fila de Compras Aprovadas.")
        return

    logger.info(f"Processando {len(pending)} compra(s) aprovada(s)...")

    for item in pending:
        process_queue_item(item)

    logger.info("Rodada concluída.")


if __name__ == "__main__":
    run_pipeline()
