"""
Relatório Ativos Validados (.xlsx): ofertas (VSLs) com conversão e os
criativos que venderam nelas, uma aba por produto.

Fonte: snapshot_daily_redtrack — conversões/IC vêm da redtrack_events e o custo
da redtrack_cost_daily (a events não tem custo). É a mesma tabela que a tela
Ativos Validados do dashboard lê, e os números seguem as regras da tela:

- oferta = produto + plataforma + Sprint/OT original + atual + lead + microlead
- Conversões = soma de purchases
- Custo de IC = média simples do cost_ic das linhas diárias do grupo,
  ignorando nulos (dia sem IC). Conferido em 24/11 contra a tela
  (BullSodaRise S410 L1 = $19.26, VigorTitan OT356 L1 = $33.18 — este tem
  um dia com cost_ic nulo, que não entra na média).
"""

import io
import re
from collections import defaultdict
from dataclasses import dataclass, field

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

from app.services.affiliate_xlsx_builder import (
    CURRENCY_FMT,
    INTEGER_FMT,
    TOTALS_FONT,
    _write_header,
)

COLUMNS = [
    "Produto",
    "Nicho",
    "Plataforma",
    "Sprint/OT Original",
    "Sprint/OT Atual",
    "Lead",
    "Microlead",
    "Criativo",
    "Conversões",
    "Custo de IC",
]
COLUMN_WIDTHS = {
    "Produto": 20,
    "Nicho": 16,
    "Plataforma": 14,
    "Sprint/OT Original": 19,
    "Sprint/OT Atual": 17,
    "Lead": 9,
    "Microlead": 12,
    "Criativo": 60,
    "Conversões": 13,
    "Custo de IC": 13,
}
OFFER_FILL = PatternFill("solid", fgColor="E5E7EB")
CREATIVE_FONT = Font(color="374151")
EMPTY = "—"

_INVALID_SHEET_CHARS = re.compile(r"[\[\]:*?/\\]")


@dataclass
class Creative:
    name: str
    conversions: int
    cost_ic: float | None  # None = nenhum dia com IC (tela mostra "—")


@dataclass
class Offer:
    product: str
    niche: str
    network: str
    original_offer: str
    current_offer: str
    lead: str
    microlead: str
    conversions: int
    cost_ic: float | None
    creatives: list[Creative] = field(default_factory=list)


def product_name(product_id: str | None, products: dict[str, dict]) -> str:
    """Nome do produto; conversão sem produto identificado vira "Sem produto"."""
    if not product_id:
        return "Sem produto"
    return (products.get(product_id) or {}).get("name") or str(product_id)


def _mean_cost_ic(rows: list[dict]) -> float | None:
    """
    Média do cost_ic diário ignorando nulos (dia sem IC), como a tela faz.
    Todos nulos → None (a tela mostra "—").
    """
    values = [float(r["cost_ic"]) for r in rows if r.get("cost_ic") is not None]
    if not values:
        return None
    return round(sum(values) / len(values), 2)


def build_offers_by_product(
    rows: list[dict],
    products: dict[str, dict],
    networks: dict[str, str],
) -> dict[str, list[Offer]]:
    """Agrupa as linhas diárias em ofertas → criativos, por product_id."""
    offer_rows: dict[tuple, list[dict]] = defaultdict(list)
    for row in rows:
        key = (
            row.get("product_id"),
            row.get("network_id"),
            row.get("original_offer"),
            row.get("current_offer"),
            row.get("lead"),
            row.get("microlead"),
        )
        offer_rows[key].append(row)

    by_product: dict[str, list[Offer]] = defaultdict(list)
    for key, group in offer_rows.items():
        product_id, network_id, original, current, lead, microlead = key
        conversions = sum(int(r.get("purchases") or 0) for r in group)
        if conversions <= 0:
            continue

        creative_rows: dict[str, list[dict]] = defaultdict(list)
        for r in group:
            creative_rows[r.get("rt_ad") or ""].append(r)
        creatives = [
            Creative(
                name=name or "(sem criativo)",
                conversions=sum(int(r.get("purchases") or 0) for r in c_rows),
                cost_ic=_mean_cost_ic(c_rows),
            )
            for name, c_rows in creative_rows.items()
        ]
        creatives = [c for c in creatives if c.conversions > 0]
        creatives.sort(key=lambda c: (-c.conversions, c.name))

        product = products.get(product_id) or {}
        by_product[product_id].append(
            Offer(
                product=product_name(product_id, products),
                niche=product.get("niche") or EMPTY,
                network=networks.get(network_id) or EMPTY,
                original_offer=original or EMPTY,
                current_offer=current or EMPTY,
                lead=lead or EMPTY,
                microlead=microlead or EMPTY,
                conversions=conversions,
                cost_ic=_mean_cost_ic(group),
                creatives=creatives,
            )
        )

    for offers in by_product.values():
        offers.sort(
            key=lambda o: (-o.conversions, o.original_offer, o.current_offer, o.lead)
        )
    return dict(by_product)


def _sheet_title(name: str, used: set[str]) -> str:
    """Nome de aba válido no Excel: sem []:*?/\\, até 31 chars e único."""
    base = _INVALID_SHEET_CHARS.sub("", name).strip() or "Produto"
    base = base[:31]
    title, n = base, 2
    while title.lower() in used:
        suffix = f" ({n})"
        title = base[: 31 - len(suffix)] + suffix
        n += 1
    used.add(title.lower())
    return title


def _write_cells(ws: Worksheet, row: int, values: dict, font=None, fill=None) -> None:
    for col_idx, col_name in enumerate(COLUMNS, start=1):
        cell = ws.cell(row=row, column=col_idx, value=values.get(col_name))
        if col_name == "Conversões":
            cell.number_format = INTEGER_FMT
        elif col_name == "Custo de IC":
            if cell.value is None:
                cell.value = EMPTY
            else:
                cell.number_format = CURRENCY_FMT
        if col_name != "Criativo":
            cell.alignment = Alignment(horizontal="center")
        if font:
            cell.font = font
        if fill:
            cell.fill = fill


def _write_product_sheet(ws: Worksheet, offers: list[Offer]) -> None:
    """
    Cada oferta é uma linha-resumo (nível 0, sempre visível) e os criativos
    que venderam nela ficam logo abaixo, agrupados (nível 1) e expandidos —
    dá pra recolher pelos botões de agrupamento do Excel, como o dropdown da tela.
    """
    _write_header(ws, COLUMNS, row=1)
    ws.sheet_properties.outlinePr.summaryBelow = False
    for col_idx, col_name in enumerate(COLUMNS, start=1):
        ws.column_dimensions[get_column_letter(col_idx)].width = COLUMN_WIDTHS[col_name]
    ws.freeze_panes = "A2"

    if not offers:
        ws.cell(row=2, column=1, value="Nenhuma oferta com conversão no período.")
        return

    row = 2
    for offer in offers:
        _write_cells(
            ws,
            row,
            {
                "Produto": offer.product,
                "Nicho": offer.niche,
                "Plataforma": offer.network,
                "Sprint/OT Original": offer.original_offer,
                "Sprint/OT Atual": offer.current_offer,
                "Lead": offer.lead,
                "Microlead": offer.microlead,
                "Criativo": f"{len(offer.creatives)} criativo(s)",
                "Conversões": offer.conversions,
                "Custo de IC": offer.cost_ic,
            },
            font=TOTALS_FONT,
            fill=OFFER_FILL,
        )
        row += 1
        for creative in offer.creatives:
            _write_cells(
                ws,
                row,
                {
                    "Criativo": creative.name,
                    "Conversões": creative.conversions,
                    "Custo de IC": creative.cost_ic,
                },
                font=CREATIVE_FONT,
            )
            ws.row_dimensions[row].outlineLevel = 1
            row += 1


def build_validated_assets_workbook(
    product_ids: list[str],
    offers_by_product: dict[str, list[Offer]],
    products: dict[str, dict],
) -> bytes:
    """Uma aba por produto pedido, na ordem do payload."""
    wb = Workbook()
    wb.remove(wb.worksheets[0])
    used: set[str] = set()
    for product_id in product_ids:
        ws = wb.create_sheet(_sheet_title(product_name(product_id, products), used))
        _write_product_sheet(ws, offers_by_product.get(product_id, []))

    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()
