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


def _snapshot(rt_ad, purchases, sub12=None, **kw):
    return {**OFFER, **kw, "rt_ad": rt_ad, "click_sub_12": sub12, "purchases": purchases}


def test_sub12_por_criativo_mais_vendas_primeiro():
    rows = [
        _snapshot(RT_AD, 1, "BK36"),
        _snapshot(RT_AD, 1, "BK102"),
        _snapshot(RT_AD, 1, "BK102"),  # outro dia, mesmo sub12: soma
        _snapshot(RT_AD, 1, "AD03_meta"),
        _snapshot(RT_AD, 1, "Y", lead="L2"),  # outra oferta
        _snapshot("sem-sub12", 1),
    ]
    offers = build_offers_by_product(rows, PRODUCTS, NETWORKS)
    by_lead = {o.lead: o for o in offers["p1"]}
    by_name = {c.name: c for c in by_lead["L1"].creatives}
    assert by_name[RT_AD].conversions == 4
    assert by_name[RT_AD].sub12 == [("BK102", 2), ("AD03_meta", 1), ("BK36", 1)]
    assert by_name["sem-sub12"].sub12 == []
    assert by_lead["L2"].creatives[0].sub12 == [("Y", 1)]


def test_grafias_diferentes_sao_sub12_diferentes():
    rows = [_snapshot(RT_AD, 1, "S410EDC19FBYTR2AD19 v1"), _snapshot(RT_AD, 1, "S410EDC19FBYTR2AD19 V1")]
    (offer,) = build_offers_by_product(rows, PRODUCTS, NETWORKS)["p1"]
    assert offer.creatives[0].sub12 == [("S410EDC19FBYTR2AD19 V1", 1), ("S410EDC19FBYTR2AD19 v1", 1)]


def _sheet(snapshots):
    offers = build_offers_by_product(snapshots, PRODUCTS, NETWORKS)
    ws = load_workbook(io.BytesIO(build_validated_assets_workbook(["p1"], offers, PRODUCTS))).active
    return [list(r) for r in ws.iter_rows(values_only=True)]


def test_sem_coluna_custo_de_ic():
    assert "Custo de IC" not in COLUMNS


def test_um_sub12_por_linha_com_criativo_repetido():
    rows = _sheet(
        [
            _snapshot(RT_AD, 2, "BK102"),
            _snapshot(RT_AD, 1, "BK36"),
            _snapshot(RT_AD, 1),  # venda de RT_AD sem sub12 (ou gclid)
            _snapshot("sem-sub12", 1),
        ]
    )
    crit, sub, conv = (COLUMNS.index(c) for c in ("Criativo", "clickSub12", "Conversões"))
    assert rows[0] == COLUMNS
    assert rows[1][sub] is None and rows[1][conv] == 5  # linha da oferta
    assert [(r[crit], r[sub], r[conv]) for r in rows[2:]] == [
        (RT_AD, "BK102", 2),
        (RT_AD, "BK36", 1),
        (RT_AD, "—", 1),  # resto sem sub12, soma bate com o criativo
        ("sem-sub12", "—", 1),
    ]
