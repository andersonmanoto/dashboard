"""
Gera o kit de VSL (preview + AutoPages) no psstock automaticamente quando um
produto chega pelo POST /offers-rt-ap -- roda em background, depois das
offers criadas e do aviso no Slack.

  - pastas: PVs/{codigo_produto}/pv1  e  pv1(ap)   (kit de PV)
            PVs/{codigo_produto}/aff/pv1(ap)       (kit de Aff Page, lido pelo
            /criar-aff-page do full_midias)
  - DTC:    products_urls (type='dtc') do produto no dashboard, preferindo a
            URL da rede da `plataforma` do payload (cada rede tem as próprias
            URLs) e caindo pra qualquer DTC do produto se essa rede não tiver
  - vídeo/pitch do preview: os padrões (os do VapoMend)

Só pra produto novo, kit a kit (por nome ou codigo_produto, em qualquer
plataforma): o de PV só se o produto não tem nada em `sales_pages` do
AutoPages; o de Aff Page só se não tem nada em `sales_page_aff`. E o
/offers-rt-ap roda uma vez por funil/plataforma, então cada kit é gerado só
na primeira vez: se a pasta já existe, não mexe (pra regerar, POST
/vsl-templates com overwrite=true). Sem as credenciais do psstock no .env,
não gera. Nunca levanta erro: as offers já foram criadas.

Em todos os casos avisa no #monitor-sites (slack_monitor_channel -- não no
#backend-geral, que é dos afiliados): kit criado com as URLs de preview e
AutoPages, não necessário (com o motivo) ou falha na geração.
"""

import asyncio
import re

from loguru import logger
from slack_sdk import WebClient
from supabase import create_async_client

from app.config import Settings
from app.services.autopages_service import AutoPagesService
from app.services.offer_announcement import NETWORK_BY_PLATAFORMA
from app.services.vsl_template_service import (
    DEFAULT_PITCH_SECONDS,
    DEFAULT_VTURB_EMBED,
    VslTemplateConfigError,
    VslTemplateConflict,
    VslTemplateService,
)


def _network_name(row: dict) -> str:
    return ((row.get("networks") or {}).get("name") or "").strip().lower()


def pick_dtc_url(rows: list[dict], plataforma: str) -> str | None:
    """DTC da rede da plataforma; senão, a primeira DTC do produto."""
    dtcs = [r for r in rows if r.get("type") == "dtc" and r.get("url")]
    network = (NETWORK_BY_PLATAFORMA.get(plataforma.strip().lower()) or "").lower()
    for r in dtcs:
        if _network_name(r) == network:
            return r["url"]
    return dtcs[0]["url"] if dtcs else None


async def fetch_product_urls(settings: Settings, produto: str) -> list[dict] | None:
    """products_urls do produto (todas as redes/tipos); None se o produto não existe."""
    db = await create_async_client(settings.supabase_url, settings.supabase_key)

    products = (
        await db.table("products").select("id").ilike("name", produto).limit(1).execute()
    ).data
    if not products:
        return None
    product: dict = products[0]  # type: ignore[assignment]

    rows = (
        await db.table("products_urls")
        .select("type, url, network_id, networks(name)")
        .eq("product_id", product["id"])
        .order("created_at")
        .execute()
    ).data or []
    return [r for r in rows if isinstance(r, dict)]


class _NaoGerado(Exception):
    """Kit não gerado porque o produto não atende os requisitos (não é erro)."""


KIT_LABEL = {"pv": "Template de PV", "aff": "Template de Aff Page"}
# Tabela do AutoPages que diz se o produto já tem página de cada kit.
KIT_TABLE = {"pv": "sales_pages", "aff": "sales_page_aff"}


def build_slack_message(
    payload: dict,
    *,
    result: dict | None = None,
    motivo: str | None = None,
    erro: str | None = None,
) -> str:
    """Mensagem do #monitor-sites: kit criado (result), não necessário (motivo) ou falha (erro)."""
    produto = payload["produto"]
    codigo = (payload.get("codigo_produto") or "").strip() or "—"
    contexto = (
        f"Plataforma: {payload.get('plataforma') or '—'} | Funil: {payload.get('funil') or '—'}"
    )

    if result is not None:
        linhas = [
            f":white_check_mark: Templates de VSL criados — *{produto}* (`{codigo}`)",
            contexto,
            f"DTC usada: {result.get('dtc_url') or '—'}",
        ]
        if result.get("preview_url"):
            linhas.append(f"Preview: {result['preview_url']}")
        if result.get("ap_url"):
            linhas.append(f"AutoPages: {result['ap_url']}")
        if result.get("aff_url"):
            linhas.append(f"Aff Page: {result['aff_url']}")
        linhas += [
            f":information_source: {KIT_LABEL[k]} não criado: {m}."
            for k, m in (result.get("nao_criados") or {}).items()
        ]
        linhas += [f":warning: {w}" for w in result.get("warnings") or []]
    elif erro is not None:
        linhas = [
            f":x: Falha ao gerar templates de VSL — *{produto}* (`{codigo}`)",
            contexto,
            f"Erro: {erro}",
            "Pra tentar de novo: POST /vsl-templates",
        ]
    else:
        linhas = [
            f":information_source: Templates de VSL não criados — *{produto}* (`{codigo}`)",
            contexto,
            f"Não foi necessário criar: {motivo}.",
        ]
    return "\n".join(linhas)


def _slack(settings: Settings, text: str) -> None:
    WebClient(token=settings.slack_bot_token).chat_postMessage(
        channel=settings.slack_monitor_channel,
        username="Backend Bot",
        text=text,
        unfurl_links=False,
    )


async def _generate(settings: Settings, payload: dict) -> dict:
    """Gera o kit; levanta _NaoGerado se o produto não atende os requisitos."""
    produto = payload["produto"]
    codigo = (payload.get("codigo_produto") or "").strip()

    service = VslTemplateService(settings)
    try:
        service._check_config()
    except VslTemplateConfigError as e:
        raise _NaoGerado(f"credenciais do psstock não configuradas ({e})")

    if not re.fullmatch(r"[A-Za-z0-9_-]{2,40}", codigo):
        raise _NaoGerado(f"codigo_produto {codigo!r} inválido pra nome de pasta")

    # Só produto novo, kit a kit: PV se não tem nada em sales_pages, Aff Page
    # se não tem nada em sales_page_aff (em qualquer plataforma).
    autopages = AutoPagesService(settings)
    nao_criados: dict[str, str] = {}
    for kit, table in KIT_TABLE.items():
        paginas = await autopages.find_sales_pages(produto, codigo, table=table)
        if paginas:
            nao_criados[kit] = (
                f"o produto já tem página em {table} no AutoPages ({paginas[0]['url']})"
            )
    kits = [k for k in KIT_TABLE if k not in nao_criados]
    if not kits:
        raise _NaoGerado("; ".join(nao_criados.values()))

    rows = await fetch_product_urls(settings, produto) or []
    dtc_url = pick_dtc_url(rows, payload["plataforma"])
    if not dtc_url:
        raise _NaoGerado(
            "produto sem URL de DTC em products_urls (type='dtc') no dashboard"
        )

    try:
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
            kits=kits,
        )
    except VslTemplateConflict as e:
        raise _NaoGerado(f"os templates já existem no psstock, mantidos ({e})")
    for kit in result.get("ja_existiam") or []:
        nao_criados[kit] = "a pasta já existe no psstock, mantida"
    return {**result, "dtc_url": dtc_url, "nao_criados": nao_criados}


async def generate_vsl_templates_for_offer(settings: Settings, payload: dict) -> None:
    """Background task do /offers-rt-ap. Nunca levanta erro.

    Sempre avisa no Slack (#monitor-sites, slack_monitor_channel): kit criado
    (com as URLs), não necessário (com o motivo) ou falha.
    """
    produto = payload["produto"]
    codigo = (payload.get("codigo_produto") or "").strip()
    try:
        result = await _generate(settings, payload)
    except _NaoGerado as e:
        logger.info(f"[vsl-templates] {produto} ({codigo}): kit não gerado -- {e}")
        text = build_slack_message(payload, motivo=str(e))
    except Exception as e:
        logger.error(f"[vsl-templates] {produto} ({codigo}): falha ao gerar kit: {e}")
        text = build_slack_message(payload, erro=str(e))
    else:
        logger.info(
            f"[vsl-templates] {codigo}: kits {result.get('kits')} gerados -- "
            f"preview {result.get('preview_url')} | AutoPages {result.get('ap_url')} "
            f"| Aff Page {result.get('aff_url')}"
        )
        for w in result["warnings"]:
            logger.warning(f"[vsl-templates] {codigo}: {w}")
        text = build_slack_message(payload, result=result)

    try:
        await asyncio.to_thread(_slack, settings, text)
    except Exception as e:
        logger.error(f"[vsl-templates] falha ao avisar no Slack: {e}")
