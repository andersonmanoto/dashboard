import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../app")))

import pytest
from services.redtrack_creative_service import (
    CatalogLookup,
    build_event_row,
    build_snapshot_rows,
    is_backredirect,
    parse_landing_title,
    parse_offer_title,
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
    assert (row["product_id"], row["network_id"]) == (ALPHAEREC_ID, BUYGOODS_ID)
    assert row["payout"] == 240


def test_build_snapshot_rows_sums_and_skips_empty():
    landing = "G1 | FB | S233 - S233 - C14 | L2 - C14 | americandaydream.site/fb/alpherc/pv1-L2-19xkF | 19-10 | x"
    base = {"campaign_id": "c1", "landing_id": "l1", "landing": landing, "campaign": "camp"}
    rows = build_snapshot_rows(
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
    assert (ad14["product_id"], ad14["network_id"]) == (ALPHAEREC_ID, BUYGOODS_ID)

    assert by_ad[""]["clicks"] == 5
    assert by_ad[""]["cost"] == 3.0
