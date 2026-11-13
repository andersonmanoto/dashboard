"""
Gera o kit de VSL (preview + AutoPages) no psstock automaticamente quando um
produto chega pelo POST /offers-rt-ap -- roda em background, depois das
offers criadas e do aviso no Slack.

  - pasta:  PVs/{codigo_produto}/pv1  e  pv1(ap)
  - DTC:    products_urls (type='dtc') do produto no dashboard, preferindo a
            URL da rede da `plataforma` do payload (cada rede tem as próprias
            URLs) e caindo pra qualquer DTC do produto se essa rede não tiver
  - vídeo/pitch do preview: os padrões (os do VapoMend)

O /offers-rt-ap roda uma vez por funil/plataforma, então o kit é gerado só na
primeira vez: se as pastas já existem, não mexe (pra regerar, POST
/vsl-templates com overwrite=true). Sem as credenciais do psstock no .env,
não faz nada. Nunca levanta erro: as offers já foram criadas.
"""

import asyncio
import re

from loguru import logger
from slack_sdk import WebClient
from supabase import create_async_client

from app.config import Settings
from app.services.offer_announcement import NETWORK_BY_PLATAFORMA
from app.services.vsl_template_service import (
    DEFAULT_PITCH_SECONDS,
    DEFAULT_VTURB_EMBED,
    VslTemplateConfigError,
    VslTemplateConflict,
    VslTemplateService,
)


async def find_dtc_url(settings: Settings, produto: str, plataforma: str) -> str | None:
    db = await create_async_client(settings.supabase_url, settings.supabase_key)

    products = (
        await db.table("products").select("id").ilike("name", produto).limit(1).execute()
    ).data
    if not products:
        return None
    product: dict = products[0]  # type: ignore[assignment]
    product_id = product["id"]

    rows = (
        await db.table("products_urls")
        .select("url, network_id, networks(name)")
        .eq("product_id", product_id)
        .eq("type", "dtc")
        .order("created_at")
        .execute()
    ).data or []
    rows = [r for r in rows if isinstance(r, dict) and r.get("url")]
    if not rows:
        return None

    network_name = (NETWORK_BY_PLATAFORMA.get(plataforma.strip().lower()) or "").lower()
    for r in rows:
        if ((r.get("networks") or {}).get("name") or "").lower() == network_name:
            return r["url"]
    return rows[0]["url"]


def _slack(settings: Settings, text: str) -> None:
    WebClient(token=settings.slack_bot_token).chat_postMessage(
        channel=settings.slack_offers_channel,
        username="Backend Bot",
        text=text,
        unfurl_links=False,
    )


async def generate_vsl_templates_for_offer(settings: Settings, payload: dict) -> None:
    """Background task do /offers-rt-ap. Nunca levanta erro."""
    produto = payload["produto"]
    codigo = (payload.get("codigo_produto") or "").strip()
    try:
        service = VslTemplateService(settings)
        try:
            service._check_config()
        except VslTemplateConfigError as e:
            logger.warning(f"[vsl-templates] {produto}: kit não gerado -- {e}")
            return

        if not re.fullmatch(r"[A-Za-z0-9_-]{2,40}", codigo):
            raise ValueError(f"codigo_produto {codigo!r} inválido pra nome de pasta")

        dtc_url = await find_dtc_url(settings, produto, payload["plataforma"])
        if not dtc_url:
            raise ValueError(
                "produto sem URL de DTC em products_urls (type='dtc') no dashboard"
            )

        result = await asyncio.to_thread(
            service.generate,
            codigo=codigo,
            pagina="pv1",
            dtc_url=dtc_url,
            nome=None,
            slogan=None,
            vturb_embed=DEFAULT_VTURB_EMBED,
            vturb_speed_script="",
            pitch_seconds=DEFAULT_PITCH_SECONDS,
            overwrite=False,
            dry_run=False,
        )
    except VslTemplateConflict:
        logger.info(f"[vsl-templates] {codigo}: kit já existe no psstock, mantido.")
        return
    except Exception as e:
        logger.error(f"[vsl-templates] {produto} ({codigo}): falha ao gerar kit: {e}")
        try:
            await asyncio.to_thread(
                _slack,
                settings,
                f":warning: Templates de VSL de *{produto}* (`{codigo}`) não foram "
                f"gerados: {e}",
            )
        except Exception as slack_exc:
            logger.error(f"[vsl-templates] falha ao avisar no Slack: {slack_exc}")
        return

    logger.info(f"[vsl-templates] {codigo}: kit gerado ({result['preview_url']})")
    linhas = [
        f"Templates de VSL de *{produto}* gerados:",
        f"Preview: {result['preview_url']}",
        f"AutoPages: {result['ap_url']}",
    ]
    linhas += [f":warning: {w}" for w in result["warnings"]]
    try:
        await asyncio.to_thread(_slack, settings, "\n".join(linhas))
    except Exception as e:
        logger.error(f"[vsl-templates] falha ao avisar no Slack: {e}")
