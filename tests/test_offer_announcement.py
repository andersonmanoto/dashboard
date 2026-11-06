import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.services.offer_announcement import build_message, format_formato


def test_build_message_format():
    urls = [
        {"type": "tsl", "url": "https://dg.marobrain.com/about/"},
        {"type": "dtc", "url": "https://dg.marobrain.com/dtc/"},
    ]
    assert build_message("MaroBrain", "ME", "capsula", "2", urls, "DigiStore24") == (
        "Novo funil cadastrado, disponível para afiliados:\n"
        "Produto: MaroBrain\n"
        "Nicho: ME\n"
        "Formato: Comprimido\n"
        "Funil: 2\n"
        "TSL: https://dg.marobrain.com/about/\n"
        "DTC: https://dg.marobrain.com/dtc/\n"
        "Plataforma: DigiStore24"
    )


def test_build_message_missing_data():
    msg = build_message("Novo", None, None, "1", [], None)
    assert "Nicho: —" in msg
    assert "Formato: —" in msg
    assert "TSL: —" in msg
    assert "DTC: —" in msg
    assert msg.endswith("Plataforma: —")


def test_build_message_multiple_urls_same_type():
    urls = [
        {"type": "tsl", "url": "https://a.com/about/"},
        {"type": "tsl", "url": "https://b.com/about/"},
    ]
    assert "TSL: https://a.com/about/ | https://b.com/about/" in build_message(
        "X", "WL", "gummie", "1", urls, "BuyGoods"
    )


def test_build_message_urls_stay_on_their_lines():
    urls = [
        {"type": "dtc", "url": "https://a.com/dtc/"},
        {"type": "tsl", "url": "https://a.com/tsl/"},
    ]
    lines = build_message("X", "WL", "gummie", "1", urls, "BuyGoods").split("\n")
    assert lines[-3:] == [
        "TSL: https://a.com/tsl/",
        "DTC: https://a.com/dtc/",
        "Plataforma: BuyGoods",
    ]


def test_format_formato():
    assert format_formato("gummie") == "Gummy"
    assert format_formato("capsula gelatinosa") == "Cápsula Gelatinosa"
    assert format_formato("tipo_novo") == "tipo_novo"
