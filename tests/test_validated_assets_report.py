import io

from openpyxl import load_workbook

from app.services.validated_assets_report import (
    COLUMNS,
    build_offers_by_product,
    build_validated_assets_workbook,
)

PRODUCTS = {"p1": {"name": "BullSodaRise", "niche": "ED"}}
NETWORKS = {"n1": "BuyGoods"}
OFFER = {
    "product_id": "p1",
    "network_id": "n1",
    "original_offer": "S410",
    "current_offer": "S410",
    "lead": "L1",
    "microlead": None,
}
RT_AD = "S410EDC19FBYTR2AD19 v1"


def _snapshot(rt_ad, purchases, cost_ic=10.0):
    return {**OFFER, "rt_ad": rt_ad, "purchases": purchases, "cost_ic": cost_ic}


def _sale(sub12, rt_ad=RT_AD, campaign="G1 | S410 | FB", **kw):
    return {**OFFER, **kw, "rt_ad": rt_ad, "campaign": campaign, "click_sub_12": sub12}


def test_sub12_por_criativo_mais_vendas_primeiro():
    sales = [
        _sale("BK36"),
        _sale("BK102"),
        _sale("BK102"),
        _sale("AD03_meta"),
        _sale("X", campaign="BackRedirect | br1"),  # fora, como na snapshot
        _sale("Y", lead="L2"),  # outra oferta
        _sale("Z", rt_ad="outro"),  # outro criativo
    ]
    offers = build_offers_by_product(
        [_snapshot(RT_AD, 4), _snapshot("sem-sub12", 1)], PRODUCTS, NETWORKS, sales
    )
    (offer,) = offers["p1"]
    by_name = {c.name: c for c in offer.creatives}
    assert by_name[RT_AD].sub12 == ["BK102", "AD03_meta", "BK36"]
    assert by_name["sem-sub12"].sub12 == []


def test_sub12_opcional():
    offers = build_offers_by_product([_snapshot(RT_AD, 1)], PRODUCTS, NETWORKS)
    assert offers["p1"][0].creatives[0].sub12 == []


def test_coluna_click_sub_12_no_xlsx():
    offers = build_offers_by_product(
        [_snapshot(RT_AD, 2), _snapshot("sem-sub12", 1)],
        PRODUCTS,
        NETWORKS,
        [_sale("BK36"), _sale("BK102")],
    )
    ws = load_workbook(io.BytesIO(build_validated_assets_workbook(["p1"], offers, PRODUCTS))).active
    col = COLUMNS.index("clickSub12") + 1
    assert ws.cell(row=1, column=col).value == "clickSub12"
    assert ws.cell(row=2, column=col).value is None  # linha da oferta
    assert ws.cell(row=3, column=col).value == "BK102, BK36"
    assert ws.cell(row=4, column=col).value == "—"
