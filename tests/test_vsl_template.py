import io
import os
import re
import sys
from pathlib import Path

import pytest
from jinja2 import Environment
from PIL import Image

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.services.vsl_template_service import (  # noqa: E402
    VslTemplateError,
    parse_dtc,
    render_kit,
    to_webp,
)

FIXTURES = Path(__file__).parent / "fixtures" / "vsl_kit"

# Kits feitos à mão pelo front (psstock.site/PVs/{TtnHny,bllsdrs}/pv1 e
# pv1(ap)) a partir das DTCs reais (titanhoney.com/dtc, bullsodarise.com/dtc).
# O kit do TitanHoney herdou o slogan do BullSodaRise (a DTC dele não tem).
PRODUTOS = [
    ("titanhoney", "https://titanhoney.com/dtc/", "Natural Male Performance Support"),
    ("bullsodarise", "https://bullsodarise.com/dtc/", None),
]


def _read(produto: str, nome: str) -> str:
    return (FIXTURES / produto / nome).read_text(encoding="utf-8")


def _linhas(texto: str) -> list[str]:
    """Compara ignorando indentação, CRLF e linhas em branco."""
    return [l.strip() for l in texto.replace("\r", "").split("\n") if l.strip()]


def _embed_do_kit(kit: str) -> str:
    m = re.search(
        r"o código que o VTurb te dá\.\)\n\s*=+ -->\n(.*?)<!-- =+\n\s*FIM DO SCRIPT DE EMBED",
        kit.replace("\r", ""),
        re.DOTALL,
    )
    assert m
    return m.group(1)


def _render(produto, url, slogan, ap):
    dtc = parse_dtc(_read(produto, "dtc.html"), url)
    if slogan:
        dtc.slogan = slogan
    return render_kit(
        dtc,
        ap=ap,
        vturb_embed=_embed_do_kit(_read(produto, "pv1.html")),
        vturb_speed_script="",
        pitch_seconds=1111,
    )


# O layout da página mudou pro do preview do VapoMend (psstock.site/PVs/VpMnd/pv1):
# sem a barra "Attention", o "people watching" e o cronômetro; faixa "Best Offer"
# e rodapé com disclaimer. A área de edição (<head>), o bloco dos cards e o CSS
# dos cards continuam iguais aos kits feitos à mão pelo front.
FONTE_POPPINS = ("fonts.googleapis.com", "fonts.gstatic.com")


def _head(texto: str) -> list[str]:
    head = texto.replace("\r", "").split("</head>")[0]
    return [l for l in _linhas(head) if not any(f in l for f in FONTE_POPPINS)]


def _cards(texto: str) -> list[str]:
    linhas = _linhas(texto)
    ini = linhas.index('<div class="pricing-cards-block">')
    fim = ini + 1 + linhas[ini + 1 :].index('<div class="flex mobile">')
    # bloco desktop + início do mobile; o mobile é simétrico
    return linhas[ini:fim]


def _ap_esperado(produto: str) -> str:
    return (
        _read(produto, "ap.html")
        .replace("{{ estrutura.pitchTime }}", "{{ estrutura.pitchTime or 0 }}")
        .replace("{{ pv.redtrackScript }}", "{{ pv.redtrackScript | safe }}")
    )


@pytest.mark.parametrize("produto,url,slogan", PRODUTOS)
def test_preview_mantem_area_de_edicao_e_cards_do_kit(produto, url, slogan):
    out = _render(produto, url, slogan, ap=False)["index.html"].decode()
    kit = _read(produto, "pv1.html")
    esperado_head = [
        l for l in _head(kit) if not l.startswith("Valor inicial: template não tinha pitch")
    ]
    assert _head(out) == esperado_head
    assert _cards(out) == _cards(kit)


@pytest.mark.parametrize("produto,url,slogan", PRODUTOS)
def test_ap_mantem_head_e_cards_do_kit(produto, url, slogan):
    out = _render(produto, url, slogan, ap=True)["index.html"].decode()
    esperado = _ap_esperado(produto)
    assert _head(out) == _head(esperado)
    assert _cards(out) == _cards(esperado)
    # o resto do motor do AutoPages continua lá
    for trecho in (
        "{{ estrutura.vturbScript | safe }}",
        "{{ estrutura.pitchTime or 0 }}",
        "{{ pv.redtrackScript | safe }}",
        "{{ estrutura.campoLivre }}",
    ):
        assert trecho in out


@pytest.mark.parametrize("ap", [False, True])
def test_layout_do_vapomend(ap):
    out = _render(*PRODUTOS[1], ap=ap)["index.html"].decode()
    assert 'class="header-bg"' in out
    assert "Get the <b class=\"pv-vm-highlight\">Best Offer</b> on <b>BullSodaRise</b>" in out
    assert "has not been evaluated by the Food &amp; Drug Administration" in out
    assert "Copyright &copy; 2026. All Rights Reserved." in out
    for antigo in ("people watching", "atomicat-countdown", "This presentation is only available", "Research Labs"):
        assert antigo not in out
    # faixa + cards só aparecem no pitch
    assert out.index('<div class="esconder">') < out.index('class="header-bg"')


@pytest.mark.parametrize("produto,url,slogan", PRODUTOS)
def test_pricing_css_com_cores_da_dtc(produto, url, slogan):
    dtc = parse_dtc(_read(produto, "dtc.html"), url)
    out = _render(produto, url, slogan, ap=False)["assets/css/pricing-cards.css"].decode()
    esperado = _linhas(_read(produto, "pricing-cards.css"))
    assert _linhas(out)[: len(esperado)] == esperado
    # faixa do layout novo com as cores do produto
    assert f"linear-gradient(135deg, {dtc.cor_primaria}, {dtc.cor_escura})" in out


def test_parse_dtc_titanhoney():
    dtc = parse_dtc(_read("titanhoney", "dtc.html"), "https://titanhoney.com/dtc/")
    assert dtc.nome == "TitanHoney"  # sem o ™
    assert dtc.slogan is None
    assert (dtc.cor_primaria, dtc.cor_escura) == ("#e8622e", "#7a1200")
    assert vars(dtc.potes["6b"]) == {
        "por_pote": "$49",
        "economia": "$780",
        "total_de": "$1074",
        "total_por": "$294",
    }
    assert "a=13732" in dtc.tracking_scripts and "conversion/iframe" in dtc.tracking_scripts
    assert dtc.imagens["2b"] == "https://titanhoney.com/dtc/img/2b.png"


def test_parse_dtc_sem_pote_falha():
    html = _read("titanhoney", "dtc.html").replace("_3b_", "_9b_").replace("3b.png", "9b.png")
    with pytest.raises(VslTemplateError, match="3b"):
        parse_dtc(html, "https://titanhoney.com/dtc/")


def test_ap_renderiza_no_autopages():
    """O index.html do (ap) tem que ser Jinja válido e não quebrar com pitch vazio."""
    dtc = parse_dtc(_read("titanhoney", "dtc.html"), "https://titanhoney.com/dtc/")
    ap = render_kit(
        dtc, ap=True, vturb_embed="<vturb-smartplayer>", vturb_speed_script="", pitch_seconds=0
    )["index.html"].decode()

    out = Environment(autoescape=True).from_string(ap).render(
        estrutura={
            "plataforma": "google",
            "dominio": "meusite.com",
            "vturbScript": "<vturb-smartplayer id='v'>",
            "pitchTime": "",
            "campoLivre": "",
            "paginasDeVendas": [{"redtrackScript": "<script src='rt.js'></script>"}],
        }
    )
    assert "const delaySeconds = 0;" in out
    assert "<script src='rt.js'></script>" in out  # | safe
    assert "<vturb-smartplayer id='v'>" in out
    assert out.count('href="https://rt.meusite.com/click/1" data-checkout="2b"') == 2
    assert out.count('href="https://rt.meusite.com/click/2" data-checkout="3b"') == 2
    assert out.count('href="https://rt.meusite.com/click/3" data-checkout="6b"') == 2


def test_to_webp_redimensiona():
    buf = io.BytesIO()
    Image.new("P", (800, 600)).save(buf, "PNG")
    img = Image.open(io.BytesIO(to_webp(buf.getvalue(), 600)))
    assert (img.format, img.size) == ("WEBP", (600, 450))


def test_preview_usa_vsl_padrao_do_vapomend():
    from app.services.vsl_template_service import DEFAULT_PITCH_SECONDS, DEFAULT_VTURB_EMBED

    dtc = parse_dtc(_read("bullsodarise", "dtc.html"), "https://bullsodarise.com/dtc/")
    kwargs = dict(
        vturb_embed=DEFAULT_VTURB_EMBED,
        vturb_speed_script="",
        pitch_seconds=DEFAULT_PITCH_SECONDS,
    )
    preview = render_kit(dtc, ap=False, **kwargs)["index.html"].decode()
    assert 'id="vid-6a0bb9604b6ee00cb92018fc"' in preview
    assert "window.VSL_PITCH_SECONDS = 10;" in preview

    ap = render_kit(dtc, ap=True, **kwargs)["index.html"].decode()
    assert "vid-6a0bb9604b6ee00cb92018fc" not in ap
    assert "{{ estrutura.vturbScript | safe }}" in ap


def test_parse_dtc_digistore_com_cores_em_variaveis_css():
    """DTC mais nova (dg.bakingslim.com/dtc): checkout DigiStore24, cores em
    var(--pc-orange-*) no .card-bv e sem img/stars.png."""
    dtc = parse_dtc(_read("bakingslim", "dtc.html"), "https://dg.bakingslim.com/dtc/")
    assert dtc.nome == "BakingSlim"
    assert (dtc.cor_primaria, dtc.cor_escura) == ("#d7640c", "#9b490b")
    # pote sai da imagem do card (link da DS24 não tem _2b_)
    assert {p: v.por_pote for p, v in dtc.potes.items()} == {
        "2b": "$79",
        "3b": "$69",
        "6b": "$49",
    }
    assert "digistore.js" in dtc.tracking_scripts
    assert "digistorePromocode" in dtc.tracking_scripts
    assert "stars" not in dtc.imagens  # usa a do kit
    assert dtc.warnings == []


def test_parse_dtc_jvzoo_copia_script_e_avisa_que_fica_inativo():
    dtc = parse_dtc(
        _read("bakingslim_jvzoo", "dtc.html"), "https://jvz.bakingslim.com/dtc/"
    )
    assert dtc.nome == "BakingSlim"
    assert "__JVZ_B_LINK_INIT__" in dtc.tracking_scripts
    assert "static.cloudflareinsights.com" not in dtc.tracking_scripts
    assert any("JVZoo" in w and "inativo" in w for w in dtc.warnings)


# --- escolha da DTC (vsl_template_auto) ---------------------------------

from app.services.vsl_template_auto import pick_dtc_url


def _url(tipo, rede, url="https://x"):
    return {"type": tipo, "url": url, "networks": {"name": rede}}


def test_pick_dtc_prefere_rede_da_plataforma():
    rows = [_url("dtc", "BuyGoods", "https://bg"), _url("dtc", "DigiStore24", "https://ds")]
    assert pick_dtc_url(rows, "DigiStore24") == "https://ds"
    assert pick_dtc_url(rows, "JVZoo") == "https://bg"
