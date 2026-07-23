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
        logger.error("[SlickText] Credenciais não configuradas. Abortando.")
        return False

    headers = {
        "Authorization": f"Bearer {settings.slicktext_api_key}",
        "Content-Type": "application/json",
    }

    base_url = f"{settings.slicktext_api_url}/brands/{settings.slicktext_brand_id}"
    contact_id = None
    mobile_number = payload.get("mobile_number")

    logger.info(
        f"--- [SlickText START] Sincronizando '{customer}' ({mobile_number}) para a Lista {target_list_id} ---"
    )

    try:
        # Passo 1: Tentar Criar
        logger.info(
            f"[SlickText] Passo 1: Disparando POST /contacts para criação. Payload: {payload}"
        )
        resp_create = requests.post(
            f"{base_url}/contacts",
            json=payload,
            headers=headers,
            timeout=10,
        )
        resp_create.raise_for_status()
        contact_id = resp_create.json().get("contact_id")
        logger.info(
            f"[SlickText] SUCESSO: Contato INÉDITO criado. ID retornado: {contact_id}"
        )

    except requests.exceptions.HTTPError as exc:
        status = exc.response.status_code
        error_msg = exc.response.text.lower()
        logger.warning(
            f"[SlickText] O POST retornou HTTP {status}. Resposta da API: {exc.response.text}"
        )

        # Passo 1.1: Tratar duplicidade APENAS se o erro for 409 ou se (400, 422) mencionar "exists" ou "already".
        is_duplicate = status == 409 or (
            status in (400, 422) and ("exists" in error_msg or "already" in error_msg)
        )

        if is_duplicate:
            logger.info(
                f"[SlickText] Passo 1.1: O contato {mobile_number} já existe. Buscando ID numérico para atualização..."
            )

            try:
                search_params = {"mobile_number": mobile_number}

                resp_search = requests.get(
                    f"{base_url}/contacts",
                    params=search_params,
                    headers=headers,
                    timeout=10,
                )
                resp_search.raise_for_status()

                search_data = resp_search.json()
                contacts_list = search_data.get("data", [])

                logger.info(
                    f"[SlickText] Busca retornou {len(contacts_list)} contato(s) para {mobile_number}."
                )

                for contact in contacts_list:
                    if contact.get("mobile_number") == mobile_number:
                        contact_id = contact.get("contact_id")
                        break

                if contact_id:
                    logger.info(
                        f"[SlickText] MATCH ENCONTRADO! ID do contato: {contact_id}"
                    )

                    resp_put = requests.put(
                        f"{base_url}/contacts/{contact_id}",
                        json=payload,
                        headers=headers,
                        timeout=10,
                    )
                    resp_put.raise_for_status()

                    logger.info(
                        f"[SlickText] SUCESSO: Dados do contato {contact_id} atualizados!"
                    )
                else:
                    logger.error(
                        f"[SlickText] ERRO FATAL: O telefone {mobile_number} não foi encontrado na busca de duplicados. "
                        f"Resposta bruta: {search_data}"
                    )
                    return False

            except requests.RequestException as update_exc:
                logger.error(
                    f"[SlickText] ERRO DE REDE ao buscar/atualizar contato: {update_exc}"
                )
                if update_exc.response is not None:
                    logger.error(
                        f"[SlickText] Detalhes do erro: {update_exc.response.text}"
                    )
                return False
            except Exception as unexpected_exc:
                # Pega qualquer coisa que não seja erro de rede (JSON malformado, chave
                # faltando, etc.) pra nunca mais morrer em silêncio sem logar nada.
                logger.exception(
                    f"[SlickText] ERRO INESPERADO ao buscar/atualizar contato {mobile_number}: {unexpected_exc}"
                )
                return False
        else:
            logger.error("[SlickText] Erro na criação (não é duplicidade). Abortando.")
            return False

    except requests.RequestException as exc:
        logger.error(f"[SlickText] ERRO DE REDE ao criar contato: {exc}")
        return False

    # Validação de segurança antes de adicionar na lista
    if not contact_id:
        logger.error("[SlickText] Falha na obtenção do contact_id. Abortando Passo 2.")
        return False

    # Passo 2: Adicionar à Lista
    try:
        logger.info(
            f"[SlickText] Passo 2: Vinculando contato ID {contact_id} à Lista {target_list_id}..."
        )
        resp_list = requests.post(
            f"{base_url}/lists/contacts",
            json=[{"contact_id": contact_id, "lists": [target_list_id]}],
            headers=headers,
            timeout=10,
        )
        resp_list.raise_for_status()
        logger.info(
            f"--- [SlickText END] '{customer}' adicionado com sucesso na lista {target_list_id}. ---"
        )
        return True

    except requests.RequestException as exc:
        logger.error(f"[SlickText] ERRO ao adicionar na lista: {exc}")
        if exc.response is not None:
            logger.error(f"[SlickText] Resposta de erro da lista: {exc.response.text}")
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

    # 1. Busca nome real do produto, URL, quantity e o aff_id_sms no Supabase
    try:
        res = (
            db_repo.client.table("checkouts")
            .select("url, quantity, products(name, aff_id_sms)")
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
        bottles_quantity = checkout_data.get("quantity", "")

        if not checkout_data.get("products"):
            logger.warning(
                f"Produto não encontrado para o codename: {product_codename}"
            )
            return

        product_info = checkout_data["products"]
        product_name = product_info.get("name")
        aff_id_sms = product_info.get("aff_id_sms")

        if not aff_id_sms:
            logger.info(
                f"SlickText ignorado: 'aff_id_sms' vazio para '{product_name}' (codename: {product_codename})."
            )
            return

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

    # 4. Envia para SlickText (Formato plano/flat)
    slicktext_payload = {
        "first_name": customer_name,
        "mobile_number": formatted_phone,
        "opt_in_status": "subscribed",
        "produto": product_name,
        "url_abandonada": url_abandonada_final,
        "bottles": str(bottles_quantity),
    }

    _sync_to_slicktext(slicktext_payload, customer_name, list_id, settings)
