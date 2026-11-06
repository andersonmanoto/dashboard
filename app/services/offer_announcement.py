"""
Aviso no Slack (#backend-geral, slack_offers_channel) de produto liberado pra
afiliados, disparado depois que o POST /offers-rt-ap cria as offers.

Formato (o mesmo do post fixado que a galera mandava na mão):

    Novo funil cadastrado, disponível para afiliados:
    Produto: BakingSlim
    Nicho: WL
    Formato: Comprimido
    Funil: 2
    TSL: https://dg.marobrain.com/about/
    DTC: https://dg.marobrain.com/dtc/
    Plataforma: DigiStore24

Os dados vêm do Supabase do dashboard (não do AutoPages): produto por nome
(ilike), nicho por niche_id (não tem FK products -> niches, então são duas
buscas) e TSL/DTC da products_urls filtrada pela rede da `plataforma` do
payload, já que cada rede tem as próprias URLs (ex: MaroBrain tem
dg.marobrain.com na DigiStore24 e marobrain.com na BuyGoods). A linha
Plataforma é o networks.name dessa mesma rede.

O que não for encontrado sai como "—": o aviso sai mesmo assim, e quem ler
vê o que falta cadastrar. Qualquer erro aqui só loga: as offers já foram
criadas e o endpoint não pode falhar por causa do aviso.
"""

import asyncio
from typing import Optional

from loguru import logger
from slack_sdk import WebClient
from supabase import create_async_client

from app.config import Settings

MISSING = "—"

# products.type -> texto do "Formato". O type não diferencia spray tópico de
# bucal, então spray sai só "Spray".
FORMATO_BY_TYPE = {
    "capsula": "Comprimido",
    "capsula gelatinosa": "Cápsula Gelatinosa",
    "gummie": "Gummy",
    "gotas": "Gotas",
    "spray": "Spray",
    "creme": "Creme",
    "info": "Digital",
    "digital": "Digital",
}

# `plataforma` do payload -> networks.name
NETWORK_BY_PLATAFORMA = {
    "buygoods": "BuyGoods",
    "pagamerican": "PagAmerican",
    "jvzoo": "JVZoo",
    "digistore": "DigiStore24",
    "digistore24": "DigiStore24",
}


def format_formato(product_type: Optional[str]) -> str:
    if not product_type:
        return MISSING
    return FORMATO_BY_TYPE.get(product_type.strip().lower(), product_type)


def build_message(
    produto: str,
    niche_code: Optional[str],
    product_type: Optional[str],
    funil: str,
    urls: list[dict],
    network_name: Optional[str],
) -> str:
    def urls_of(kind: str) -> str:
        found = [u["url"] for u in urls if u.get("type") == kind and u.get("url")]
        return " | ".join(found) or MISSING

    return "\n".join(
        [
            "Novo funil cadastrado, disponível para afiliados:",
            f"Produto: {produto}",
            f"Nicho: {niche_code or MISSING}",
            f"Formato: {format_formato(product_type)}",
            f"Funil: {funil}",
            f"TSL: {urls_of('tsl')}",
            f"DTC: {urls_of('dtc')}",
            f"Plataforma: {network_name or MISSING}",
        ]
    )


async def _fetch_details(settings: Settings, produto: str, plataforma: str) -> dict:
    db = await create_async_client(settings.supabase_url, settings.supabase_key)

    products = (
        await db.table("products")
        .select("id, name, type, niche_id")
        .ilike("name", produto)
        .limit(1)
        .execute()
    ).data
    if not products:
        logger.warning(f"[offers] Produto '{produto}' não achado no dashboard pro aviso.")
        return {
            "name": produto,
            "niche_code": None,
            "type": None,
            "urls": [],
            "network_name": None,
        }
    product: dict = products[0]  # type: ignore[assignment]

    niche_code = None
    if product.get("niche_id"):
        niches = (
            await db.table("niches")
            .select("niche_code")
            .eq("id", product["niche_id"])
            .limit(1)
            .execute()
        ).data
        niche_code = niches[0]["niche_code"] if niches else None

    urls: list[dict] = []
    network_name = NETWORK_BY_PLATAFORMA.get(plataforma.strip().lower())
    networks = (
        (
            await db.table("networks")
            .select("id, name")
            .ilike("name", network_name)
            .limit(1)
            .execute()
        ).data
        if network_name
        else []
    )
    if networks:
        urls = (
            await db.table("products_urls")
            .select("type, url")
            .eq("product_id", product["id"])
            .eq("network_id", networks[0]["id"])
            .in_("type", ["tsl", "dtc"])
            .order("created_at")
            .execute()
        ).data or []
    else:
        logger.warning(f"[offers] Plataforma '{plataforma}' sem rede no dashboard pro aviso.")

    return {
        "name": product["name"],
        "niche_code": niche_code,
        "type": product.get("type"),
        "urls": urls,
        "network_name": networks[0]["name"] if networks else None,
    }


async def notify_offer_created(settings: Settings, payload: dict) -> None:
    """Manda o aviso no Slack. Nunca levanta erro."""
    produto = payload["produto"]
    try:
        details = await _fetch_details(settings, produto, payload["plataforma"])
        text = build_message(
            details["name"],
            details["niche_code"],
            details["type"],
            payload["funil"],
            details["urls"],
            details["network_name"],
        )
        client = WebClient(token=settings.slack_bot_token)
        await asyncio.to_thread(
            client.chat_postMessage,
            channel=settings.slack_offers_channel,
            username="Backend Bot",
            text=text,
            unfurl_links=False,
        )
        logger.info(f"[offers] Aviso de '{produto}' enviado pro Slack.")
    except Exception as exc:
        logger.error(f"[offers] Não consegui mandar o aviso de '{produto}' pro Slack: {exc}")
