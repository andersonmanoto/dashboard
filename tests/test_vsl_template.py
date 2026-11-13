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


@pytest.mark.parametrize("produto,url,slogan", PRODUTOS)
def test_preview_reproduz_kit_do_front(produto, url, slogan):
    out = _render(produto, url, slogan, ap=False)["index.html"].decode()
    esperado = [
        l
        for l in _linhas(_read(produto, "pv1.html"))
        if not l.startswith("Valor inicial: template não tinha pitch")
    ]
    assert _linhas(out) == esperado


@pytest.mark.parametrize("produto,url,slogan", PRODUTOS)
def test_ap_reproduz_kit_do_front_com_correcoes(produto, url, slogan):
    out = _render(produto, url, slogan, ap=True)["index.html"].decode()
    esperado = (
        _read(produto, "ap.html")
        .replace("{{ estrutura.pitchTime }}", "{{ estrutura.pitchTime or 0 }}")
        .replace("{{ pv.redtrackScript }}", "{{ pv.redtrackScript | safe }}")
    )
    assert _linhas(out) == _linhas(esperado)


@pytest.mark.parametrize("produto,url,slogan", PRODUTOS)
def test_pricing_css_com_cores_da_dtc(produto, url, slogan):
    out = _render(produto, url, slogan, ap=False)["assets/css/pricing-cards.css"].decode()
    assert _linhas(out) == _linhas(_read(produto, "pricing-cards.css"))


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
