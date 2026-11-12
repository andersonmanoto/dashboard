import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.services.redtrack_offer_service import build_offer_url, strip_tracking_params
from app.services.redtrack_offer_url_service import extract_aff_id

# URLs reais das offers no RedTrack (GET /offers/{id} em 12/11) e o buylink
# cru correspondente.
CASES = [
    (
        "BuyGoods",
        "https://buygoods.com/secure/checkout.html?account_id=13985&product_codename=hncg_front_6b_fnn1"
        "&redirect=aHR0cHM6Ly9ob25leWNvZy5jb20vdXAxLTZiLw==",
        "38",
        "https://buygoods.com/secure/checkout.html?account_id=13985&product_codename=hncg_front_6b_fnn1"
        "&redirect=aHR0cHM6Ly9ob25leWNvZy5jb20vdXAxLTZiLw==&aff_id=38&subid={clickid}"
        "&subid2={rt_campaign}&utm_campaign={rt_campaign}&subid3={rt_ad}&subid5={sub20}",
    ),
    (
        "DigiStore24",
        "https://www.checkout-ds24.com/product/753030",
        "gestorfour",
        "https://www.checkout-ds24.com/product/753030?aff=gestorfour&cid={clickid}"
        "&sid1={rt_campaign}&sid2={utm_campaign}",
    ),
    (
        "JVZoo",
        "https://www.jvzoo.com/b/118423/453109/99",
        "3636247",
        "https://www.jvzoo.com/b/118423/453109/99?aid=3636247&tid={clickid}",
    ),
    (
        "PagAmerican",
        "https://pay.pagamerican.app/HXR9DDrj",
        None,
        "https://pay.pagamerican.app/HXR9DDrj?clickid={clickid}",
    ),
]


@pytest.mark.parametrize("plataforma,buylink,aff_id,offer_url", CASES)
def test_build_matches_live_offer(plataforma, buylink, aff_id, offer_url):
    assert build_offer_url(buylink, aff_id or "", plataforma) == offer_url


@pytest.mark.parametrize("plataforma,buylink,aff_id,offer_url", CASES)
def test_extract_aff_id_from_live_offer(plataforma, buylink, aff_id, offer_url):
    assert extract_aff_id(offer_url, plataforma) == aff_id


@pytest.mark.parametrize("plataforma,buylink,aff_id,offer_url", CASES)
def test_strip_undoes_build(plataforma, buylink, aff_id, offer_url):
    # buylink que já vem com tracking volta a ser o cru, e remontar não duplica
    assert strip_tracking_params(offer_url, plataforma) == buylink
    assert build_offer_url(strip_tracking_params(offer_url, plataforma), aff_id or "", plataforma) == offer_url


def test_strip_keeps_other_params_and_raw_url():
    url = "https://www.checkout-ds24.com/product/1?template=light&aff=x&cid=y"
    assert strip_tracking_params(url, "DigiStore24") == "https://www.checkout-ds24.com/product/1?template=light"
    assert strip_tracking_params("https://pay.pagamerican.app/abc", "PagAmerican") == "https://pay.pagamerican.app/abc"


def test_extract_aff_id_missing():
    assert extract_aff_id("https://www.checkout-ds24.com/product/1", "DigiStore24") is None
