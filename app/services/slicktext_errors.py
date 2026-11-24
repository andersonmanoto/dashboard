"""
Códigos de last_error da slicktext_sync_queue e da slicktext_input_queue.

Venda (sync_approved_orders), abandono (slicktext_service) e input manual
(slicktext_input_service) fazem as mesmas checagens e gravam o mesmo código,
pra o dashboard agrupar sem regex. O detalhe (produto, telefone cru...) vai
pro log, não pro código.
"""

import re
from typing import Optional

import phonenumbers

ERR_EMPTY_PHONE = "telefone_vazio"  # lead chegou sem telefone
# venda sem telefone esperando o carrinho do mesmo email (retry, não é final)
ERR_WAITING_CART_PHONE = "aguardando_telefone_carrinho"
ERR_INVALID_PHONE = "telefone_invalido"  # tem telefone, mas não é número válido
ERR_ABSTRACT = "rejeitado_abstract"  # AbstractAPI reprovou o telefone
ERR_ABSTRACT_API = "abstract_api_error"  # AbstractAPI fora (retry)
ERR_LIST_NOT_MAPPED = "lista_nao_mapeada"  # produto sem lista em slicktext_product_lists
ERR_NO_ACTIVE_ACCOUNT = "sem_conta_active"  # tem lista mapeada, nenhuma conta active
ERR_PRODUCT_UNKNOWN = "produto_nao_identificado"  # venda chegou sem product_id
ERR_REGION = "regiao_nao_suportada"  # lead fora de EUA/Canadá: país do checkout ou do número (SlickText recusa)
ERR_OPTED_OUT = "contato_descadastrado"  # contato já existe na conta e pediu pra sair (STOP)

# SlickText só aceita telefone dos EUA e do Canadá. Vale o país DO NÚMERO:
# Porto Rico (787/939) é +1 e passa como "United States" no checkout.
SMS_PHONE_REGIONS = frozenset({"US", "CA"})
# trecho da resposta da SlickText quando recusa a região do telefone
SLICKTEXT_REGION_ERROR = "us and ca"


def is_empty_phone(raw_phone) -> bool:
    return not str(raw_phone or "").strip()


def is_opted_out(contact: dict) -> bool:
    """Contato da SlickText que pediu pra sair: não reinscrever (compliance)."""
    return str(contact.get("opt_in_status") or "").lower() == "unsubscribed"


def phone_region(e164: str) -> Optional[str]:
    """País do número (ISO alpha-2) a partir do E.164, ou None."""
    try:
        return phonenumbers.region_code_for_number(phonenumbers.parse(e164, None))
    except phonenumbers.NumberParseException:
        return None


def is_supported_region(e164: str) -> bool:
    return phone_region(e164) in SMS_PHONE_REGIONS


def foreign_phone_region(raw_phone, country_hint: str = "") -> Optional[str]:
    """
    País de um telefone que NÃO fechou como número dos EUA mas é válido de
    outro país, ou None. Ex.: venda da Austrália com '0418441401' (formato
    local) ou '61419646095' (internacional sem o '+').

    Usado pra separar "fora de EUA/Canadá" (ERR_REGION) de lixo digitado
    (ERR_INVALID_PHONE). country_hint (ISO alpha-2 do checkout) só é usado se
    for de fora de EUA/Canadá: com hint US/CA, só a leitura com '+' vale.
    """
    raw = str(raw_phone or "").strip()
    digits = re.sub(r"\D", "", raw)
    if not digits:
        return None
    candidates = []
    if country_hint and country_hint not in SMS_PHONE_REGIONS:
        candidates.append((raw, country_hint))
    candidates.append((f"+{digits}", None))
    for value, region in candidates:
        try:
            parsed = phonenumbers.parse(value, region)
        except phonenumbers.NumberParseException:
            continue
        if phonenumbers.is_valid_number(parsed):
            found = phonenumbers.region_code_for_number(parsed)
            if found and found not in SMS_PHONE_REGIONS:
                return found
    return None
