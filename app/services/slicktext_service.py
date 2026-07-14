import logging
from typing import Optional
import requests
import phonenumbers
from phonenumbers import NumberParseException, PhoneNumberFormat

from app.config import get_list_id_for_product, Settings
from app.repositories.database import DatabaseRepository

logger = logging.getLogger(__name__)


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


def validate_phone_abstract(formatted_phone: str, api_key: str) -> Optional[bool]:
    if not api_key:
        logger.error("ABSTRACT_API_KEY não configurada.")
        return None

    try:
        response = requests.get(
            "https://phoneintelligence.abstractapi.com/v1/",
            params={"api_key": api_key, "phone": formatted_phone},
            timeout=10,
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
        logger.error(f"Erro AbstractAPI: {exc}")
        return None


def _sync_to_slicktext(
    payload: dict, customer: str, target_list_id: int, settings: Settings
) -> bool:
    if not settings.slicktext_api_key or not settings.slicktext_brand_id:
        logger.error("Credenciais do SlickText não configuradas.")
        return False

    headers = {
        "Authorization": f"Bearer {settings.slicktext_api_key}",
        "Content-Type": "application/json",
    }

    try:
        # 1. Criar o Contato
        resp_create = requests.post(
            f"{settings.slicktext_api_url}/brands/{settings.slicktext_brand_id}/contacts",
            json=payload,
            headers=headers,
            timeout=10,
        )
        resp_create.raise_for_status()
        contact_id = resp_create.json().get("contact_id")

        if not contact_id:
            return False

        # 2. Adicionar à Lista
        resp_list = requests.post(
            f"{settings.slicktext_api_url}/brands/{settings.slicktext_brand_id}/lists/contacts",
            json=[{"contact_id": contact_id, "lists": [target_list_id]}],
            headers=headers,
            timeout=10,
        )
        resp_list.raise_for_status()
        logger.info(f"SlickText: '{customer}' adicionado à lista {target_list_id}.")
        return True

    except requests.RequestException as exc:
        logger.error(f"Erro SlickText para '{customer}': {exc}")
        return False


async def process_slicktext_sync_task(
    payload: dict, settings: Settings, db_repo: DatabaseRepository
):
    """
    Background task para processar o fluxo do SlickText.
    """
    customer_name = payload.get("name", "")
    raw_phone = payload.get("phone", "")
    product_codename = payload.get("product_codename", "")
    country = payload.get("country", "US")

    # 1. Busca nome real do produto, a URL e o aff_id_sms no Supabase
    try:
        # Adicionamos 'aff_id_sms' no join com a tabela products
        res = (
            db_repo.client.table("checkouts")
            .select("url, products(name, aff_id_sms)")
            .eq("checkout_code", product_codename)
            .limit(1)
            .execute()
        )

        if not res.data:
            logger.warning(
                f"Checkout não encontrado para o codename: {product_codename}"
            )
            return

        checkout_data = res.data[0]
        checkout_url = checkout_data.get("url", "")

        if not checkout_data.get("products"):
            logger.warning(
                f"Produto não encontrado para o codename: {product_codename}"
            )
            return

        product_info = checkout_data["products"]
        product_name = product_info.get("name")
        aff_id_sms = product_info.get("aff_id_sms")

        # Nova Regra: Se não tiver aff_id_sms, aborta o envio silenciosamente
        if not aff_id_sms:
            logger.info(
                f"SlickText ignorado: 'aff_id_sms' está vazio para o produto '{product_name}' (codename: {product_codename})."
            )
            return

        # Concatena o aff_id na URL existente da BuyGoods
        url_abandonada_final = f"{checkout_url}&aff_id={aff_id_sms}"

    except Exception as e:
        logger.error(
            f"Erro ao buscar dados no banco para o codename '{product_codename}': {e}"
        )
        return

    # 2. Pega ID da lista
    list_id = get_list_id_for_product(product_name)
    if not list_id:
        logger.warning(f"SlickText: Lista não mapeada para o produto '{product_name}'.")
        return

    # 3. Valida telefone
    country_code = "US" if country.lower() in ("united states", "us") else country
    formatted_phone = format_phone_local(raw_phone, country_code)

    if not formatted_phone or not validate_phone_abstract(
        formatted_phone, settings.abstract_api_key
    ):
        return

    # 4. Envia para SlickText
    slicktext_payload = {
        "first_name": customer_name,
        "mobile_number": formatted_phone,
        "produto": product_name,
        "url_abandonada": url_abandonada_final,
        "opt_in_status": "subscribed",
    }

    _sync_to_slicktext(slicktext_payload, customer_name, list_id, settings)
