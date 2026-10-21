"""
Ranking de criativos (rt_ad) do RedTrack: alimenta `redtrack_events` e
`snapshot_daily_redtrack` (ver migrations/20261021_create_redtrack_events_and_snapshot.sql).

  redtrack_events          GET /conversions -> 1 linha por conversão (upsert pelo id)
  snapshot_daily_redtrack  GET /report?group=rt_ad,landing,campaign -> valores
                           absolutos do dia, substituindo o dia inteiro

O RedTrack mostra o valor "atualizado" do dia (custo do Facebook vai subindo
ao longo do dia), não venda a venda. Por isso a snapshot nunca soma em cima
do que já está lá: cada coleta apaga e regrava o dia.

Mapeamentos confirmados na API em 21/10 (dia 20/10: 4 Purchase, 8 IC no
/conversions = convtype1 4, convtype3 8 no /report; custo 2036,03 e receita
1000 batendo com a tela de Campaigns):
  convtype1 = Purchase, convtype3 = InitiateCheckout, receita = total_revenue
  (o campo `revenue` vem zerado).

Produto/network: título da offer ("Buygoods | AlphaErec | 6 Potes | Funil 3")
casado por nome com `networks` / `products`. Na snapshot a offer vem das
offers da campaign (o custo é por clique, antes da offer — agrupar o /report
por offer separa custo e receita em linhas diferentes).
"""

import asyncio
import re
from datetime import date, datetime, timezone
from typing import Optional

import httpx
from loguru import logger

PURCHASE_CONVTYPE = "convtype1"
INITIATE_CHECKOUT_CONVTYPE = "convtype3"
SNAPSHOT_GROUP = "rt_ad,landing,campaign"

LANDING_PREFIX_RE = re.compile(r"^G\d+$")
LEAD_RE = re.compile(r"^L\d+$")


def parse_landing_title(title: Optional[str]) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """
    (original_offer, current_offer, lead) do título da landing.

    Padrão: "G1 | FB | OT248 - OT283 - SPY | L1 - C01 | dominio/... | 10-10 | id".
    Título fora do padrão (landers antigas, YouTube "G3 | YT | pv1-che | gluco")
    devolve None no que não bater, em vez de chutar.
    """
    blocks = [b.strip() for b in (title or "").split("|")]
    if len(blocks) < 4 or not LANDING_PREFIX_RE.match(blocks[0]):
        return None, None, None

    offers = [o.strip() for o in blocks[2].split(" - ")]
    original_offer, current_offer = (offers[0], offers[1]) if len(offers) >= 2 else (None, None)

    lead = blocks[3].split(" - ")[0].strip()
    return original_offer, current_offer, lead if LEAD_RE.match(lead) else None


def parse_offer_title(title: Optional[str]) -> tuple[Optional[str], Optional[str]]:
    """(network, produto) do título da offer: "Buygoods | AlphaErec | 6 Potes | Funil 3"."""
    blocks = [b.strip() for b in (title or "").split("|")]
    if len(blocks) < 2 or not blocks[0] or not blocks[1]:
        return None, None
    return blocks[0], blocks[1]


class CatalogLookup:
    """Nome (case-insensitive) -> id em `products` / `networks` do dashboard."""

    def __init__(self, products: dict[str, str], networks: dict[str, str]):
        # get_*_lookup devolve id -> nome; aqui é o inverso, em minúsculas
        self.products = {name.strip().lower(): pid for pid, name in products.items() if name}
        self.networks = {name.strip().lower(): nid for nid, name in networks.items() if name}

    def ids_for_offer(self, offer_title: Optional[str]) -> tuple[Optional[str], Optional[str]]:
        """(product_id, network_id) pro título da offer; None no que não casar."""
        network, product = parse_offer_title(offer_title)
        return (
            self.products.get(product.lower()) if product else None,
            self.networks.get(network.lower()) if network else None,
        )


def is_backredirect(campaign_title: Optional[str]) -> bool:
    """Campaign de BackRedirect ("BackRedirect | br1 | ED - SodaHorsePeak| ...").

    Não é tráfego de criativo: não tem custo e o rt_ad vem herdado da visita
    original, então fica fora da redtrack_events.
    """
    return (campaign_title or "").strip().lower().startswith("backredirect")


def build_event_row(conversion: dict, catalog: CatalogLookup) -> dict:
    """Linha da redtrack_events a partir de um item do GET /conversions."""
    original_offer, current_offer, lead = parse_landing_title(conversion.get("landing"))
    product_id, network_id = catalog.ids_for_offer(conversion.get("offer"))
    conv_time = conversion.get("conv_time") or None

    return {
        "rt_conversion_id": conversion["id"],
        "click_id": conversion.get("clickid") or None,
        "conversion_type": conversion.get("type") or "",
        "conversion_time": conv_time,
        # conv_time já vem no fuso da conta ("2026-10-20T21:48:03-03:00"), o
        # mesmo do filtro date_from/date_to: a data é o prefixo.
        "conversion_date": (conv_time or conversion.get("created_at") or "")[:10],
        "rt_ad": conversion.get("rt_ad") or "",
        "campaign_id": conversion.get("campaign_id") or None,
        "campaign": conversion.get("campaign") or None,
        "landing_id": conversion.get("landing_id") or None,
        "landing": conversion.get("landing") or None,
        "offer_id": conversion.get("offer_id") or None,
        "offer": conversion.get("offer") or None,
        "original_offer": original_offer,
        "current_offer": current_offer,
        "lead": lead,
        "product_id": product_id,
        "network_id": network_id,
        "payout": conversion.get("payout") or 0,
        "status": conversion.get("status") or None,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


def build_snapshot_rows(
    report_rows: list[dict],
    campaign_products: dict[str, tuple[Optional[str], Optional[str]]],
) -> list[dict]:
    """
    Linhas da snapshot a partir do /report agrupado por rt_ad,landing,campaign.

    `campaign_products`: campaign_id -> (product_id, network_id).
    Linhas sem nada (0 clique, 0 custo, 0 conversão) ficam de fora. rt_ad
    vazio e nulo caem na mesma chave, então soma por chave em vez de confiar
    que o RedTrack nunca repete.
    """
    by_key: dict[tuple[str, str, str], dict] = {}
    for r in report_rows:
        clicks = r.get("clicks") or 0
        lp_clicks = r.get("lp_clicks") or 0
        ics = r.get(INITIATE_CHECKOUT_CONVTYPE) or 0
        purchases = r.get(PURCHASE_CONVTYPE) or 0
        revenue = r.get("total_revenue") or 0
        cost = r.get("cost") or 0
        if not (clicks or lp_clicks or ics or purchases or revenue or cost):
            continue

        key = (r.get("rt_ad") or "", r.get("campaign_id") or "", r.get("landing_id") or "")
        row = by_key.get(key)
        if row is None:
            original_offer, current_offer, lead = parse_landing_title(r.get("landing"))
            product_id, network_id = campaign_products.get(key[1], (None, None))
            row = by_key[key] = {
                "rt_ad": key[0],
                "campaign_id": key[1],
                "landing_id": key[2],
                "campaign": r.get("campaign") or None,
                "landing": r.get("landing") or None,
                "original_offer": original_offer,
                "current_offer": current_offer,
                "lead": lead,
                "product_id": product_id,
                "network_id": network_id,
                "clicks": 0,
                "lp_clicks": 0,
                "initiate_checkouts": 0,
                "purchases": 0,
                "total_revenue": 0,
                "cost": 0,
            }
        row["clicks"] += clicks
        row["lp_clicks"] += lp_clicks
        row["initiate_checkouts"] += ics
        row["purchases"] += purchases
        row["total_revenue"] += revenue
        row["cost"] += cost

    for row in by_key.values():
        row["total_revenue"] = round(row["total_revenue"], 2)
        row["cost"] = round(row["cost"], 2)
    return list(by_key.values())


class RedTrackCreativeSync:
    """Coleta um ou mais dias do RedTrack e grava events + snapshot."""

    def __init__(self, redtrack, db_repo):
        self.redtrack = redtrack
        self.db_repo = db_repo
        # caches por execução: várias datas compartilham campaigns/offers
        self._offer_titles: dict[str, Optional[str]] = {}
        self._campaign_products: dict[str, tuple[Optional[str], Optional[str]]] = {}

    async def _offer_title(self, client: httpx.AsyncClient, offer_id: str) -> Optional[str]:
        if offer_id not in self._offer_titles:
            offer = await self.redtrack.get_offer(client, offer_id)
            self._offer_titles[offer_id] = offer.get("title")
        return self._offer_titles[offer_id]

    async def _resolve_campaign(
        self, client: httpx.AsyncClient, campaign_id: str, catalog: CatalogLookup
    ) -> tuple[Optional[str], Optional[str]]:
        """
        (product_id, network_id) da campaign, pelas offers dos streams dela.
        Se as offers apontarem pra mais de um produto/network, deixa None —
        não tem como dividir o custo do clique entre eles.
        """
        if campaign_id in self._campaign_products:
            return self._campaign_products[campaign_id]

        campaign = await self.redtrack.get_campaign(client, campaign_id)
        offer_ids = {
            offer["id"]
            for s in campaign.get("streams") or []
            for offer in (s.get("stream") or {}).get("offers") or []
            if offer.get("id")
        }
        products, networks = set(), set()
        for offer_id in offer_ids:
            product_id, network_id = catalog.ids_for_offer(
                await self._offer_title(client, offer_id)
            )
            products.add(product_id)
            networks.add(network_id)

        product_id = products.pop() if len(products) == 1 else None
        network_id = networks.pop() if len(networks) == 1 else None
        if product_id is None:
            logger.warning(
                f"[redtrack-criativos] campaign {campaign_id} "
                f"('{campaign.get('title')}') sem produto único nas offers "
                f"({len(offer_ids)} offer(s))"
            )
        self._campaign_products[campaign_id] = (product_id, network_id)
        return product_id, network_id

    async def sync_day(
        self, client: httpx.AsyncClient, day: date, catalog: CatalogLookup
    ) -> dict:
        d = day.isoformat()
        conversions = await self.redtrack.get_conversions(client, d, d)
        report = await self.redtrack.get_report(client, SNAPSHOT_GROUP, d, d)

        event_rows = [
            build_event_row(c, catalog)
            for c in conversions
            if not is_backredirect(c.get("campaign"))
        ]

        campaign_ids = {r.get("campaign_id") for r in report if r.get("campaign_id")}
        campaign_products = {
            cid: await self._resolve_campaign(client, cid, catalog)
            for cid in sorted(campaign_ids)
        }
        snapshot_rows = build_snapshot_rows(report, campaign_products)

        await asyncio.to_thread(self.db_repo.upsert_redtrack_events, event_rows)
        await asyncio.to_thread(
            self.db_repo.replace_snapshot_daily_redtrack, d, snapshot_rows
        )

        summary = {
            "date": d,
            "events": len(event_rows),
            "snapshot_rows": len(snapshot_rows),
            "cost": round(sum(r["cost"] for r in snapshot_rows), 2),
            "revenue": round(sum(r["total_revenue"] for r in snapshot_rows), 2),
        }
        logger.info(f"[redtrack-criativos] {summary}")
        return summary

    async def sync(self, days: list[date]) -> list[dict]:
        catalog = CatalogLookup(
            await asyncio.to_thread(self.db_repo.get_products_lookup),
            await asyncio.to_thread(self.db_repo.get_networks_lookup),
        )
        if not catalog.products or not catalog.networks:
            # os lookups engolem erro e devolvem {}: sem isso a coleta gravaria
            # tudo com product_id/network_id nulo sem ninguém perceber
            raise RuntimeError("products/networks vazios — Supabase indisponível?")
        async with httpx.AsyncClient(timeout=60.0) as client:
            return [await self.sync_day(client, day, catalog) for day in days]
