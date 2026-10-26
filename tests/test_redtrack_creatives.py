import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../app")))

import pytest
from services.redtrack_creative_service import (
    CatalogLookup,
    build_event_row,
    build_cost_rows,
    is_backredirect,
    landings_with_revenue,
    parse_landing_title,
    parse_microlead,
    parse_offer_title,
    parse_vturb_video_id,
)

ALPHAEREC_ID = "ecfb897e-2cb8-4d52-ae21-02ede303193b"
BUYGOODS_ID = "8a051386-e188-466e-9968-bd5d48ecbc93"

CATALOG = CatalogLookup(
    products={ALPHAEREC_ID: "AlphaErec"},
    networks={BUYGOODS_ID: "BuyGoods"},
)


@pytest.mark.parametrize(
    "title, expected",
    [
        (
            "G1 | FB | OT248 - OT283 - SPY | L1 - C01 | goodtobehere.site/fb/SdHrsPk/pv1-v4-L1-7MKdH | 10-10 | 6acae0b86cde7bc69e69a374",
            ("OT248", "OT283", "L1"),
        ),
        (
            "G1 | FB | S233 - S233 - C14 | L4 - C14 | americandaydream.site/fb/alpherc/pv1-L4-19xkF | 19-10 | 6ad34d6c31f4dd38266985d1",
            ("S233", "S233", "L4"),
        ),
        # formatos fora do padrão: não chuta
        ("G3 | YT | pv1-che | gluco | pncreas | onwarddreamer.site/yt7/gluco-pncreas/pv1-che-L1-mUlgz | 19-04", (None, None, None)),
        ("Pre-lander | Facebook | Buygoods | PS-L6 | theoldnewman.online/fb-cps/ps", (None, None, None)),
        ("", (None, None, None)),
        (None, (None, None, None)),
    ],
)
def test_parse_landing_title(title, expected):
    assert parse_landing_title(title) == expected


@pytest.mark.parametrize(
    "title, expected",
    [
        (
            "G1 | FB | OT248 - OT257 - SPY | L1 - C01 | ML8 - C01 | getbackupagain.site/fb/SdHrsPk/pv1-v4-ML8-yrg6Y | 01-10 | 6abe311a5cd352c8993bf6e8",
            "6abe311a5cd352c8993bf6e8",
        ),
        # id grudado em lixo no último bloco (lander real de 12/09)
        (
            "G1 | FB | OT248 - OT249 - SPY | L1 - C01 | hopefulhorizons.site/fb/SdHrsPk/pv1-v3-L1-XOXrJ | 12-09 2F6aa4c4acfbc4117542eb1a8e",
            "6aa4c4acfbc4117542eb1a8e",
        ),
        # sem id no fim: não chuta
        ("G3 | YT | OT216 - OT216 - SPY | L1 - SPY | rolltowin.site/yt3/alpherc/ | 08-08", None),
        ("Pre-lander | Facebook | Buygoods | PS-L6 | theoldnewman.online/fb-cps/ps", None),
        ("", None),
        (None, None),
    ],
)
def test_parse_vturb_video_id(title, expected):
    assert parse_vturb_video_id(title) == expected


@pytest.mark.parametrize(
    "title, expected",
    [
        # títulos reais de campaigns (out/2026)
        ("G1 | OT248 | FB | Buygoods | che - 2b | ed | SdHrsPk | ML8 | https://twr.getbackupagain.site/49gsd8dgrw/ | 01-10", "ML8"),
        ("G3 | OT248/OT257/ML8 | YT | 737-894-3143 1144 | Buygoods | che - 2b | ed | SdHrsPk | brighterdaysnow.site | 05-10", "ML8"),
        ("G3 | S218-ML2 | YT | 639-099-2560 07 | Buygoods | che - 2b | pm | mlen | brxcdx | lastingvalues.site | 06-06", "ML2"),
        ("G1 | OT248 | ML5-6-7 | FB | Buygoods | che - 2b | ed | SdHrsPk | https://twr.daybreakliving.site/c1kdo0r7cj/ |", "ML5-6-7"),
        ("G3 | OT239/ML00 | YT | 636-820-8644 1196 | Buygoods | che - 2b | mm | maro | everydaygoodness.site | 21-10", "ML00"),
        # sem ML + número: não chuta
        ("G3 | C2 | YT | BG | CHE | 173-MCC_TOM-STAR11-4 | VSL-WL-PINKSALT-ML Sarah | blessedwave.site", None),
        ("G3 | OT248 | YT | 795-078-9198M1 | Buygoods", None),
        ("G1 | OT133 | FB | Buygoods | che - 2b | FG | nails | https://twr.heartfulliving.site/nnz57p3nyw/ | 04-10", None),
        ("", None),
        (None, None),
    ],
)
def test_parse_microlead(title, expected):
    assert parse_microlead(title) == expected


def test_parse_offer_title():
    assert parse_offer_title("Buygoods | AlphaErec | 6 Potes | Funil 3") == ("Buygoods", "AlphaErec")
    assert parse_offer_title("") == (None, None)


def test_is_backredirect():
    assert is_backredirect("BackRedirect | br1 | ED - SodaHorsePeak| thedreamaway.site - 14/09")
    assert is_backredirect("backredirect | br2 | x")
    assert not is_backredirect("G1 | OT248 | FB | Buygoods | che - 2b | ed | SdHrsPk | x | 10-10")
    assert not is_backredirect(None)


def test_catalog_case_insensitive():
    # RedTrack mistura "Buygoods" e "BuyGoods"
    assert CATALOG.ids_for_offer("Buygoods | AlphaErec | 2 Potes | Funil 3") == (ALPHAEREC_ID, BUYGOODS_ID)
    assert CATALOG.ids_for_offer("BuyGoods | alphaerec | 2 Potes | Funil 3") == (ALPHAEREC_ID, BUYGOODS_ID)
    assert CATALOG.ids_for_offer("BuyGoods | Desconhecido | 2 Potes") == (None, BUYGOODS_ID)


def test_build_event_row():
    # conversão real de 20/10 (só os campos usados)
    row = build_event_row(
        {
            "id": "6ad7af1a0000000000000000",
            "clickid": "6ad7af14b0d4e673f502edec",
            "type": "Purchase",
            "payout": 240,
            "status": "other",
            "conv_time": "2026-10-20T15:15:03-03:00",
            "rt_ad": "S233ED-C14-FB-C16-R02-AD14-V1",
            "campaign_id": "6ad58905802a8399dfb8ce1d",
            "campaign": "G1 | S233 | FB | Buygoods | che - 2b | ed | alpherc | https://twr.americandaydream.site/xajow9bjk2/ | 19-10",
            "landing_id": "6ad589038d17ff6615e58357",
            "landing": "G1 | FB | S233 - S233 - C14 | L1 - C14 | americandaydream.site/fb/alpherc/pv1-L1-19xkF | 19-10 | 6ad34c05a884ed0eb81161a7",
            "offer_id": "6a73353a3afc9693860d234f",
            "offer": "Buygoods | AlphaErec | 6 Potes | Funil 3",
        },
        CATALOG,
    )
    assert row["rt_conversion_id"] == "6ad7af1a0000000000000000"
    assert row["conversion_date"] == "2026-10-20"
    assert row["rt_ad"] == "S233ED-C14-FB-C16-R02-AD14-V1"
    assert (row["original_offer"], row["current_offer"], row["lead"]) == ("S233", "S233", "L1")
    assert row["vturb_video_id"] == "6ad34c05a884ed0eb81161a7"
    assert row["microlead"] is None
    assert (row["product_id"], row["network_id"]) == (ALPHAEREC_ID, BUYGOODS_ID)
    assert row["payout"] == 240


def test_build_cost_rows_sums_and_skips_empty():
    landing = "G1 | FB | S233 - S233 - C14 | L2 - C14 | americandaydream.site/fb/alpherc/pv1-L2-19xkF | 19-10 | x"
    base = {"campaign_id": "c1", "landing_id": "l1", "landing": landing, "campaign": "G1 | OT248 | FB | Buygoods | ML8 | x"}
    rows = build_cost_rows(
        [
            {**base, "rt_ad": "AD14", "clicks": 294, "lp_clicks": 1, "convtype1": 1, "convtype3": 1, "total_revenue": 240, "cost": 76.61},
            # rt_ad nulo e vazio caem na mesma chave: soma
            {**base, "rt_ad": None, "clicks": 3, "cost": 1.005},
            {**base, "rt_ad": "", "clicks": 2, "cost": 2.0},
            # linha sem nada fica de fora
            {**base, "rt_ad": "AD99", "clicks": 0, "cost": 0},
        ],
        {"c1": (ALPHAEREC_ID, BUYGOODS_ID)},
    )
    by_ad = {r["rt_ad"]: r for r in rows}
    assert set(by_ad) == {"AD14", ""}

    ad14 = by_ad["AD14"]
    assert (ad14["purchases"], ad14["initiate_checkouts"], ad14["total_revenue"], ad14["cost"]) == (1, 1, 240, 76.61)
    assert (ad14["original_offer"], ad14["current_offer"], ad14["lead"]) == ("S233", "S233", "L2")
    assert ad14["vturb_video_id"] is None  # título do teste termina em "x"
    assert ad14["microlead"] == "ML8"
    assert (ad14["product_id"], ad14["network_id"]) == (ALPHAEREC_ID, BUYGOODS_ID)

    assert by_ad[""]["clicks"] == 5
    assert by_ad[""]["cost"] == 3.0


def test_landings_with_revenue():
    rows = [
        # lander que vendeu: entra mesmo com criativos sem venda
        {"landing_id": "l1", "rt_ad": "AD1", "total_revenue": 240, "cost": 30},
        {"landing_id": "l1", "rt_ad": "AD2", "total_revenue": 0, "cost": 50},
        # só gastou: fica de fora
        {"landing_id": "l2", "rt_ad": "AD3", "total_revenue": 0, "cost": 80},
        # vendeu sem custo (YouTube): entra
        {"landing_id": "l3", "rt_ad": "SPY", "total_revenue": 260, "cost": 0},
        # sem landing: fica de fora
        {"landing_id": None, "rt_ad": "AD4", "total_revenue": 100, "cost": 10},
    ]
    assert landings_with_revenue(rows) == {"l1", "l3"}
