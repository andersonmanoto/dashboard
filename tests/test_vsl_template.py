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


def _render(produto, url, slogan, modo):
    dtc = parse_dtc(_read(produto, "dtc.html"), url)
    if slogan:
        dtc.slogan = slogan
    return render_kit(
        dtc,
        modo=modo,
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
    out = _render(produto, url, slogan, modo="preview")["index.html"].decode()
    kit = _read(produto, "pv1.html")
    esperado_head = [
        l for l in _head(kit) if not l.startswith("Valor inicial: template não tinha pitch")
    ]
    assert _head(out) == esperado_head
    assert _cards(out) == _cards(kit)


@pytest.mark.parametrize("produto,url,slogan", PRODUTOS)
def test_ap_mantem_head_e_cards_do_kit(produto, url, slogan):
    out = _render(produto, url, slogan, modo="ap")["index.html"].decode()
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


@pytest.mark.parametrize("modo", ["preview", "ap", "aff"])
def test_layout_do_vapomend(modo):
    out = _render(*PRODUTOS[1], modo=modo)["index.html"].decode()
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
    out = _render(produto, url, slogan, modo="preview")["assets/css/pricing-cards.css"].decode()
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
        dtc, modo="ap", vturb_embed="<vturb-smartplayer>", vturb_speed_script="", pitch_seconds=0
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


def _aff_esperado() -> str:
    # Diferenças de forma do aff.html feito à mão (TtnHny/aff/pv1(ap)) que
    # não mudam nada: comentários e o `or 0` do pitch.
    return (
        _read("titanhoney", "aff.html")
        .replace("<!-- CSS ESCONDER Colocar no <Head> -->", "")
        .replace("{{ aff_page.pitchTime }}", "{{ aff_page.pitchTime or 0 }}")
    )


def _sem_scroll_event(linhas: list[str]) -> list[str]:
    # o aff.html feito à mão tem um class="smartplayer-scroll-event" a mais
    # num dos botões do 6b (sobra do VapoMend; o kit não usa)
    return [l.replace('<a class="smartplayer-scroll-event" href=', "<a href=") for l in linhas]


def test_aff_mantem_head_e_cards_do_kit_feito_a_mao():
    out = _render(*PRODUTOS[0], modo="aff")["index.html"].decode()
    esperado = _aff_esperado()
    assert _head(out) == _head(esperado)
    assert _sem_scroll_event(_cards(out)) == _sem_scroll_event(_cards(esperado))
    for trecho in (
        "{{ aff_page.vturbScript | safe }}",
        "{{ aff_page.pitchTime or 0 }}",
    ):
        assert trecho in out
    # nada do AutoPages: pixel do Meta, campoLivre, script do RedTrack, /click/N
    for trecho in ("estrutura.", "fbq(", "redtrackScript", "/click/"):
        assert trecho not in out


def test_aff_renderiza_no_criar_aff_page():
    """Mesmo render do processar_aff_pages do full_midias (aff_page=dados)."""
    dtc = parse_dtc(_read("titanhoney", "dtc.html"), "https://titanhoney.com/dtc/")
    aff = render_kit(
        dtc, modo="aff", vturb_embed="<x>", vturb_speed_script="", pitch_seconds=0
    )["index.html"].decode()

    out = Environment().from_string(aff).render(
        aff_page={
            "vturbScript": "<vturb-smartplayer id='v'>",
            "pitchTime": 0,
            "linkOferta1": "https://chk/2b",
            "linkOferta2": "https://chk/3b",
            "linkOferta3": "https://chk/6b",
        }
    )
    assert "const delaySeconds = 0;" in out
    assert "<vturb-smartplayer id='v'>" in out
    assert out.count('href="https://chk/2b" data-checkout="2b"') == 2
    assert out.count('href="https://chk/3b" data-checkout="3b"') == 2
    assert out.count('href="https://chk/6b" data-checkout="6b"') == 2
    assert "a=13732" in out  # tracking da DTC fica, como no aff feito à mão
    assert ".esconder{display:none;}" in out


# --- upload: kit com pasta já existente é mantido -------------------------

from app.services.vsl_template_service import VslTemplateConflict, VslTemplateService  # noqa: E402


class _FakeSftp:
    def __init__(self, existentes):
        self.existentes = set(existentes)
        self.gravados = []

    def stat(self, path):
        if path in self.existentes or any(e.startswith(path + "/") for e in self.existentes):
            import stat as _st
            return SimpleNamespace(st_mode=_st.S_IFDIR)
        raise FileNotFoundError(path)

    def mkdir(self, path):
        self.existentes.add(path)

    def open(self, path, mode):
        self.gravados.append(path)
        return io.BytesIO()

    def close(self):
        pass


def _upload(monkeypatch, existentes, overwrite=False):
    sftp = _FakeSftp(existentes)
    svc = VslTemplateService(SimpleNamespace(psstock_pvs_dir="/PVs"))
    monkeypatch.setattr(svc, "_connect", lambda: (sftp, SimpleNamespace(close=lambda: None)))
    kits = {
        "pv": {"pv1": {"index.html": b"p"}, "pv1(ap)": {"index.html": b"a"}},
        "aff": {"aff/pv1(ap)": {"index.html": b"f"}},
    }
    return svc.upload("Prod", kits, overwrite), sftp.gravados


def test_upload_pula_kit_existente(monkeypatch):
    pulados, gravados = _upload(monkeypatch, {"/PVs/Prod/pv1"})
    assert pulados == ["pv"]
    assert gravados == ["/PVs/Prod/aff/pv1(ap)/index.html"]


def test_upload_conflito_quando_todos_existem(monkeypatch):
    with pytest.raises(VslTemplateConflict):
        _upload(monkeypatch, {"/PVs/Prod/pv1(ap)", "/PVs/Prod/aff/pv1(ap)"})


def test_upload_overwrite_grava_tudo(monkeypatch):
    pulados, gravados = _upload(
        monkeypatch, {"/PVs/Prod/pv1", "/PVs/Prod/aff/pv1(ap)"}, overwrite=True
    )
    assert pulados == []
    assert len(gravados) == 3


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
    preview = render_kit(dtc, modo="preview", **kwargs)["index.html"].decode()
    assert 'id="vid-6a0bb9604b6ee00cb92018fc"' in preview
    assert "window.VSL_PITCH_SECONDS = 10;" in preview

    ap = render_kit(dtc, modo="ap", **kwargs)["index.html"].decode()
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


# --- aviso no #monitor-sites (vsl_template_auto) ------------------------

import asyncio as _asyncio  # noqa: E402
from types import SimpleNamespace  # noqa: E402

from app.services import vsl_template_auto  # noqa: E402

_PAYLOAD = {"produto": "TitanHoney", "codigo_produto": "TtnHny", "plataforma": "buygoods", "funil": "2"}


def test_mensagem_kit_criado_tem_urls_e_avisos():
    text = vsl_template_auto.build_slack_message(
        _PAYLOAD,
        result={
            "dtc_url": "https://titanhoney.com/dtc/",
            "preview_url": "https://psstock.site/PVs/TtnHny/pv1/",
            "ap_url": "https://psstock.site/PVs/TtnHny/pv1(ap)/",
            "warnings": ["sem slogan"],
        },
    )
    assert "Templates de VSL criados" in text
    assert "Preview: https://psstock.site/PVs/TtnHny/pv1/" in text
    assert "AutoPages: https://psstock.site/PVs/TtnHny/pv1(ap)/" in text
    assert ":warning: sem slogan" in text


def _run_with(monkeypatch, sales_pages=(), sales_page_aff=(), dtc_rows=(), generate=None):
    enviados = []
    monkeypatch.setattr(vsl_template_auto, "_slack", lambda s, t: enviados.append(t))
    monkeypatch.setattr(
        vsl_template_auto, "VslTemplateService",
        lambda s: SimpleNamespace(_check_config=lambda: None, generate=generate),
    )

    class _AP:
        def __init__(self, s):
            pass

        async def find_sales_pages(self, produto, codigo, table="sales_pages"):
            return list(sales_pages if table == "sales_pages" else sales_page_aff)

    async def _urls(s, produto):
        return list(dtc_rows)

    monkeypatch.setattr(vsl_template_auto, "AutoPagesService", _AP)
    monkeypatch.setattr(vsl_template_auto, "fetch_product_urls", _urls)
    _asyncio.run(vsl_template_auto.generate_vsl_templates_for_offer(SimpleNamespace(), _PAYLOAD))
    assert len(enviados) == 1
    return enviados[0]


def test_aviso_nao_necessario_quando_ja_tem_sales_page_e_aff(monkeypatch):
    text = _run_with(
        monkeypatch,
        sales_pages=[{"url": "https://x.com/vsl"}],
        sales_page_aff=[{"url": "https://x.com/aff"}],
    )
    assert "não criados" in text
    assert "Não foi necessário criar: o produto já tem página em sales_pages no AutoPages (https://x.com/vsl)" in text
    assert "sales_page_aff no AutoPages (https://x.com/aff)" in text


_DTC_ROWS = [{"type": "dtc", "url": "https://titanhoney.com/dtc/", "networks": {"name": "BuyGoods"}}]


def _gen_que_registra(chamadas):
    def gen(**kw):
        chamadas.append(kw)
        kits = kw["kits"]
        return {
            "kits": kits,
            "ja_existiam": [],
            "preview_url": "P" if "pv" in kits else None,
            "ap_url": "A" if "pv" in kits else None,
            "aff_url": "F" if "aff" in kits else None,
            "warnings": [],
        }

    return gen


def test_so_aff_quando_ja_tem_sales_page(monkeypatch):
    chamadas = []
    text = _run_with(
        monkeypatch,
        sales_pages=[{"url": "https://x.com/vsl"}],
        dtc_rows=_DTC_ROWS,
        generate=_gen_que_registra(chamadas),
    )
    assert chamadas[0]["kits"] == ["aff"]
    assert "Aff Page: F" in text
    assert "Preview:" not in text and "AutoPages:" not in text
    assert ":information_source: Template de PV não criado: o produto já tem página em sales_pages" in text


def test_so_pv_quando_ja_tem_sales_page_aff(monkeypatch):
    chamadas = []
    text = _run_with(
        monkeypatch,
        sales_page_aff=[{"url": "https://x.com/aff"}],
        dtc_rows=_DTC_ROWS,
        generate=_gen_que_registra(chamadas),
    )
    assert chamadas[0]["kits"] == ["pv"]
    assert "Preview: P" in text and "AutoPages: A" in text
    assert "Aff Page:" not in text
    assert "Template de Aff Page não criado" in text


def test_produto_novo_gera_os_dois_kits(monkeypatch):
    chamadas = []
    text = _run_with(monkeypatch, dtc_rows=_DTC_ROWS, generate=_gen_que_registra(chamadas))
    assert chamadas[0]["kits"] == ["pv", "aff"]
    assert "Preview: P" in text and "Aff Page: F" in text
    assert "não criado" not in text


def test_pasta_existente_no_psstock_vira_nao_criado(monkeypatch):
    def gen(**kw):
        return {"kits": ["aff"], "ja_existiam": ["pv"], "preview_url": None,
                "ap_url": None, "aff_url": "F", "warnings": []}

    text = _run_with(monkeypatch, dtc_rows=_DTC_ROWS, generate=gen)
    assert "Aff Page: F" in text
    assert "Template de PV não criado: a pasta já existe no psstock, mantida." in text


def test_aviso_nao_necessario_sem_dtc(monkeypatch):
    text = _run_with(monkeypatch)
    assert "Não foi necessário criar: produto sem URL de DTC" in text


def test_aviso_kit_criado(monkeypatch):
    rows = [{"type": "dtc", "url": "https://titanhoney.com/dtc/", "networks": {"name": "BuyGoods"}}]

    def gen(**kw):
        return {"preview_url": "P", "ap_url": "A", "warnings": []}

    text = _run_with(monkeypatch, dtc_rows=rows, generate=gen)
    assert "Templates de VSL criados" in text
    assert "Preview: P" in text
    assert "DTC usada: https://titanhoney.com/dtc/" in text


def test_aviso_falha(monkeypatch):
    rows = [{"type": "dtc", "url": "https://titanhoney.com/dtc/"}]

    def gen(**kw):
        raise RuntimeError("SFTP caiu")

    text = _run_with(monkeypatch, dtc_rows=rows, generate=gen)
    assert "Falha ao gerar templates de VSL" in text
    assert "Erro: SFTP caiu" in text
