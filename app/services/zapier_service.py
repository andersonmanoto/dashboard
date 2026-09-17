from datetime import datetime, timezone
from typing import Optional

import phonenumbers
import pycountry
from loguru import logger
from phonenumbers import NumberParseException, PhoneNumberFormat

from models.schemas import NormalizedEvent


def _format_phone_e164(raw_phone: Optional[str], region: str = "US") -> str:
    if not raw_phone:
        return ""
    try:
        parsed = phonenumbers.parse(raw_phone, region)
        if not phonenumbers.is_valid_number(parsed):
            return ""
        return phonenumbers.format_number(parsed, PhoneNumberFormat.E164)
    except NumberParseException:
        return ""


def _split_name(full_name: Optional[str]) -> tuple[str, str]:
    if not full_name:
        return "", ""
    parts = full_name.strip().split(maxsplit=1)
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], parts[1]


def _country_to_alpha2(value: Optional[str]) -> str:
    """
    Resolve um país (nome ou código) pro ISO 3166-1 alpha-2 exigido pelo
    Zapier. Retorna "" se não conseguir identificar -- o endpoint aceita
    campo vazio, então isso não bloqueia o envio do lead.
    """
    if not value:
        return ""

    value = value.strip()
    if len(value) == 2:
        return value.upper()

    try:
        match = pycountry.countries.search_fuzzy(value)
        return match[0].alpha_2 if match else ""
    except LookupError:
        logger.warning(f"Zapier: país não reconhecido pra ISO2: '{value}'")
        return ""


def _order_date_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def build_order_payload(event: NormalizedEvent, product_name: Optional[str] = None) -> dict:
    """
    Monta o JSON de um novo pedido (lead_type=neworder) no formato exigido
    pelo Zapier. Uma linha de `events` (neworder) = um POST -- inclusive
    upsells/orderbumps da mesma compra saem como leads separados, cada um
    com seu próprio order_id.

    `product_name` é o nome canônico (products.name, resolvido pelo
    chamador via event.product_id) -- usado em vez do nome/variante que a
    rede manda no payload. Cai pro nome bruto do payload se não resolver.
    """
    raw_payload = event.payload or {}
    product_name = product_name or event.order_details.product_name or ""

    first_name = raw_payload.get("customer_firstname") or ""
    last_name = raw_payload.get("customer_lastname") or ""
    if not first_name and not last_name:
        first_name, last_name = _split_name(event.customer_name)

    country_raw = raw_payload.get("country_2letter") or event.shipping_details.country
    country_iso2 = _country_to_alpha2(country_raw)

    return {
        "lead_type": "neworder",
        "first_name": first_name,
        "last_name": last_name,
        "email": event.customer_email or "",
        "phone": _format_phone_e164(event.customer_phone),
        "products_purchased": product_name,
        "country": country_iso2,
        "countryabbreviation": country_iso2,
        "street": event.shipping_details.address or "",
        "city": event.shipping_details.city or "",
        "state": event.shipping_details.state or "",
        "postal_code": event.shipping_details.zip or "",
        "order_id": event.order_id,
        "order_date": _order_date_now(),
        "order_total": f"{event.sale_total:.2f}",
    }


def build_abandoned_cart_payload(cart_data: dict, product_name: Optional[str] = None) -> dict:
    """
    Monta o JSON de um carrinho abandonado (lead_type=abandon) no mesmo
    formato do Zapier. Não existe pedido ainda, então order_id/order_total
    vão vazios e order_date é o momento do abandono.

    `product_name` é o nome canônico (products.name, resolvido pelo
    chamador a partir do product_codename) -- cai pro codename cru se não
    resolver.
    """
    location = cart_data.get("location") or {}
    first_name, last_name = _split_name(cart_data.get("customer_name"))
    country_iso2 = _country_to_alpha2(location.get("country"))
    product_name = product_name or cart_data.get("product_codename") or ""

    return {
        "lead_type": "abandon",
        "first_name": first_name,
        "last_name": last_name,
        "email": cart_data.get("customer_email") or "",
        "phone": _format_phone_e164(cart_data.get("customer_phone")),
        "products_purchased": product_name,
        "country": country_iso2,
        "countryabbreviation": country_iso2,
        "street": location.get("address") or "",
        "city": location.get("city") or "",
        "state": location.get("state") or "",
        "postal_code": "",
        "order_id": "",
        "order_date": _order_date_now(),
        "order_total": "",
    }
