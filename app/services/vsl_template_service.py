"""
Gera os templates de VSL de um produto (kit "2.9") a partir da página DTC e
sobe no psstock via SFTP:

  {psstock_pvs_dir}/{codigo}/{pagina}/       preview (área de edição rápida)
  {psstock_pvs_dir}/{codigo}/{pagina}(ap)/   AutoPages (variáveis Jinja2)

O esqueleto é fixo (templates/vsl_kit/, extraído do kit do TitanHoney). Da DTC
só vêm: nome/slogan (<title>), as 2 cores do produto (.card-bv), os scripts de
tracking da BuyGoods (copiados sem alteração), os preços dos potes 2b/3b/6b e
as imagens (img/*.png → images/pots/*.webp). O vídeo e o pitch do vturb só
valem pro preview e, por padrão, são os do VapoMend (DEFAULT_VTURB_EMBED /
DEFAULT_PITCH_SECONDS); o (ap) usa os do AutoPages.

Disparado automaticamente pelo POST /offers-rt-ap (ver vsl_template_auto.py)
ou à mão pelo POST /vsl-templates (pra regerar com overwrite, por exemplo).

O template usa [[ ]] / [% %] como delimitadores pra que os {{ }} / {% %} do
AutoPages saiam literais no index.html do (ap).
"""

import base64
import io
import posixpath
import re
import stat
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urljoin, urlparse

import httpx
import paramiko
from bs4 import BeautifulSoup
from jinja2 import Environment, FileSystemLoader, StrictUndefined
from loguru import logger
from PIL import Image

from app.config import Settings

KIT_DIR = Path(__file__).resolve().parents[2] / "templates" / "vsl_kit"

POTES = ("2b", "3b", "6b")
# Ordem dos /click/N do RedTrack no AutoPages (segue a quantidade de potes,
# não a posição na tela): 1=2b, 2=3b, 3=6b.
AP_CLICK = {"2b": 1, "3b": 2, "6b": 3}

# Largura máxima de cada imagem no kit (None = mantém o tamanho da DTC).
IMAGENS = {"2b": 600, "3b": 600, "6b": 600, "offer": 480, "cards": 400, "stars": None}
# Imagens genéricas (iguais em toda DTC): se a DTC não tiver, usa a do kit.
IMAGENS_PADRAO = {"stars": KIT_DIR / "stars.png"}

# VSL do preview: o mesmo vídeo/pitch do preview do VapoMend em todo produto.
# O (ap) não usa isso -- lá o vídeo e o pitch vêm do AutoPages
# (estrutura.vturbScript / estrutura.pitchTime).
DEFAULT_VTURB_EMBED = """\
<vturb-smartplayer id="vid-6a0bb9604b6ee00cb92018fc" style="display: block; margin: 0 auto; width: 100%; ">
    <div class="vturb-player-placeholder" style="position: relative; width: 100%; padding: 56.25% 0 0; z-index: 0; background-color: black;"></div>
  </vturb-smartplayer>
  <script type="text/javascript">
    var s = document.createElement("script");
    s.src = "https://scripts.converteai.net/bf3f4bad-0141-41d4-9901-1fd62546db6f/players/6a0bb9604b6ee00cb92018fc/v4/player.js", s.async = !0, document.head.appendChild(s);
  </script>"""
DEFAULT_PITCH_SECONDS = 10

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
)
# O CDN da Hostinger (hcdn) devolve 403 pra requisição sem Accept de navegador.
HTTP_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/*,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

# Cor em hex ou var(--x) (DTCs mais novas definem as cores em variáveis CSS
# do .pricing-cards-block e usam var() no .card-bv / .header-bg).
COR = r"(#[0-9a-fA-F]{3,8}|var\(\s*--[\w-]+\s*\))"
CSS_VAR_RE = r"--{nome}\s*:\s*(#[0-9a-fA-F]{{3,8}})"
CARD_BV_RE = re.compile(
    r"\.card-bv\s*\{[^}]*?radial-gradient\(\s*circle\s*,\s*" + COR + r"\s+0%\s*,\s*" + COR,
    re.DOTALL,
)
HEADER_BG_RE = re.compile(
    r"\.header-bg\s*\{[^}]*?linear-gradient\(\s*[\d.]+deg\s*,\s*" + COR + r"\s*,\s*" + COR,
    re.DOTALL,
)
# Scripts de tracking da rede copiados da DTC: BuyGoods (track + iframe de
# conversão), DigiStore24 (digistore.js + digistorePromocode) ou JVZoo
# ("JVZoo Affiliate Link Tracking & Rewriting Script", inline).
TRACKING_MARKERS = (
    "buygoods.com",
    "digistore24-scripts.com",
    "digistorePromocode",
    "__JVZ_B_LINK_INIT__",
)
SCRIPT_RE = re.compile(r"<script\b[^>]*>.*?</script>", re.IGNORECASE | re.DOTALL)
POTE_CODENAME_RE = re.compile(r"product_codename=[^&\"']*?[_-](\d+)b(?:[_-]|&|$)", re.IGNORECASE)
# Nome do arquivo varia por produto (2b.png, 2b-3.png, 6b_0326.png, ...).
POTE_IMG_RE = re.compile(r"(?:^|/)(\d+)b(?:[-_][\w-]*)?\.[a-z]+$", re.IGNORECASE)
TRADEMARKS_RE = re.compile(r"[®™©]")


class VslTemplateError(Exception):
    """Erro esperado (DTC fora do padrão, config faltando, etc.)."""


class VslTemplateConfigError(Exception):
    """Credenciais/caminho do psstock não configurados no .env."""


class VslTemplateUploadError(VslTemplateError):
    """Falha de conexão/transferência com o psstock (não é culpa da DTC)."""


class VslTemplateConflict(VslTemplateError):
    """A pasta de destino já existe no psstock e overwrite=False."""


@dataclass
class Pote:
    por_pote: str
    economia: str
    total_de: str
    total_por: str


@dataclass
class DtcData:
    nome: str
    slogan: str | None
    cor_primaria: str
    cor_escura: str
    tracking_scripts: str
    potes: dict[str, Pote]
    imagens: dict[str, str]  # stem -> URL absoluta na DTC (sem as que usam o padrão)
    warnings: list[str] = field(default_factory=list)


# --- Extração da DTC ---------------------------------------------------------


def _texto(el) -> str:
    return " ".join(el.get_text(" ", strip=True).split()) if el else ""


def _nome_e_slogan(soup: BeautifulSoup) -> tuple[str, str | None]:
    title = _texto(soup.find("title"))
    if not title:
        raise VslTemplateError("DTC sem <title> — informe `nome` no request.")
    nome, _, resto = title.partition("|")
    nome = TRADEMARKS_RE.sub("", nome).strip()
    slogan = resto.split("—")[0].strip() or None
    return nome, slogan


def _resolve_cor(valor: str, css: str) -> str | None:
    if valor.startswith("#"):
        return valor.lower()
    nome = valor[valor.index("--") + 2 : valor.rindex(")")].strip()
    m = re.search(CSS_VAR_RE.format(nome=re.escape(nome)), css)
    return m.group(1).lower() if m else None


def _cores(soup: BeautifulSoup) -> tuple[str, str]:
    """(primária, escura). .card-bv é radial(primária, escura); .header-bg é
    linear(escura, primária). Pode haver mais de uma regra (ex: a do
    .pricing-cards-block com var() e a global com hex) -- vale a 1ª que resolve."""
    css = "\n".join(s.get_text() for s in soup.find_all("style"))
    candidatos = [(m.group(1), m.group(2)) for m in CARD_BV_RE.finditer(css)]
    candidatos += [(m.group(2), m.group(1)) for m in HEADER_BG_RE.finditer(css)]
    for primaria, escura in candidatos:
        p, e = _resolve_cor(primaria, css), _resolve_cor(escura, css)
        if p and e:
            return p, e
    raise VslTemplateError(
        "Não achei as cores do produto na DTC (.card-bv / .header-bg)."
    )


def _pote_do_card(card) -> str | None:
    for a in card.find_all("a", href=True):
        if m := POTE_CODENAME_RE.search(str(a["href"])):
            return f"{m.group(1)}b"
    for img in card.find_all("img", src=True):
        if m := POTE_IMG_RE.search(urlparse(str(img["src"])).path):
            return f"{m.group(1)}b"
    return None


def _potes(soup: BeautifulSoup) -> dict[str, Pote]:
    desktop = soup.select_one("#desktop")
    if desktop is None:
        raise VslTemplateError("DTC sem o bloco de ofertas #desktop.")

    potes: dict[str, Pote] = {}
    for card in desktop.select(".pricing-card"):
        pote = _pote_do_card(card)
        if pote is None:
            continue
        preco = _texto(card.select_one(".txt-size"))
        economia = next(
            (
                m.group(1)
                for p in card.find_all("p")
                if (m := re.search(r"YOU SAVE\s*(\S+)", _texto(p), re.IGNORECASE))
            ),
            "",
        )
        total = next((p for p in card.find_all("p") if "TOTAL" in _texto(p).upper()), None)
        total_de = _texto(total.select_one(".text-decoration-line-through")) if total else ""
        total_por = _texto(total.select_one(".fw-bold")) if total else ""
        if not all((preco, economia, total_de, total_por)):
            raise VslTemplateError(
                f"Card do pote {pote} na DTC fora do padrão (preço={preco!r}, "
                f"economia={economia!r}, total={total_de!r}->{total_por!r})."
            )
        potes[pote] = Pote(preco, economia, total_de, total_por)

    faltando = [p for p in POTES if p not in potes]
    if faltando:
        raise VslTemplateError(
            f"DTC sem card dos potes {faltando} em #desktop — o kit só tem 2b/3b/6b."
        )
    return potes


def _imagens(soup: BeautifulSoup, base_url: str) -> dict[str, str]:
    """stem do kit -> URL na DTC. Aceita sufixo no nome (offer-2.png,
    2b-3.png, cards_0326.png); o nome exato tem preferência."""
    por_stem: dict[str, str] = {}
    for img in soup.find_all("img", src=True):
        src = str(img["src"])
        stem = Path(urlparse(src).path).stem.lower()
        for kit_stem in IMAGENS:
            if stem == kit_stem:
                por_stem[kit_stem] = urljoin(base_url, src)
            elif re.fullmatch(rf"{kit_stem}[-_][\w-]*", stem):
                por_stem.setdefault(kit_stem, urljoin(base_url, src))
    faltando = [s for s in IMAGENS if s not in por_stem and s not in IMAGENS_PADRAO]
    if faltando:
        raise VslTemplateError(f"DTC sem as imagens {faltando}.")
    return {s: por_stem[s] for s in IMAGENS if s in por_stem}


def parse_dtc(html: str, base_url: str) -> DtcData:
    soup = BeautifulSoup(html, "html.parser")
    nome, slogan = _nome_e_slogan(soup)
    cor_primaria, cor_escura = _cores(soup)

    warnings = []
    # Copiados como texto cru (não via soup) pra sair byte a byte igual à DTC.
    tracking = [
        s for s in SCRIPT_RE.findall(html) if any(k in s for k in TRACKING_MARKERS)
    ]
    if not tracking:
        warnings.append(
            "DTC sem scripts de tracking (BuyGoods/DigiStore24/JVZoo) — kit sai sem eles."
        )
    if any("__JVZ_B_LINK_INIT__" in t for t in tracking):
        # O script acha o produto pelos links/pixels jvzoo.com da página; no
        # kit os botões não são do JVZoo e os pixels i.jvzoo.com dos cards da
        # DTC não vêm junto, então sem FALLBACK_PRODUCT_ID ele não dispara.
        warnings.append(
            "Tracking do JVZoo copiado, mas inativo no kit: os botões não são links "
            "jvzoo.com e os pixels i.jvzoo.com dos cards não são levados."
        )

    return DtcData(
        nome=nome,
        slogan=slogan,
        cor_primaria=cor_primaria,
        cor_escura=cor_escura,
        tracking_scripts="\n\n".join(tracking),
        potes=_potes(soup),
        imagens=_imagens(soup, base_url),
        warnings=warnings,
    )


# --- Render ------------------------------------------------------------------

_env = Environment(
    loader=FileSystemLoader(KIT_DIR),
    variable_start_string="[[",
    variable_end_string="]]",
    block_start_string="[%",
    block_end_string="%]",
    comment_start_string="[#",
    comment_end_string="#]",
    undefined=StrictUndefined,
    autoescape=False,
    keep_trailing_newline=True,
)


def render_kit(
    dtc: DtcData,
    *,
    ap: bool,
    vturb_embed: str,
    vturb_speed_script: str,
    pitch_seconds: int,
) -> dict[str, bytes]:
    """Arquivos de uma pasta do kit: {caminho relativo: conteúdo}."""
    links = {
        p: (f"https://rt.{{{{ estrutura.dominio }}}}/click/{AP_CLICK[p]}" if ap else "#/")
        for p in POTES
    }
    ctx = {
        "ap": ap,
        "nome": dtc.nome,
        "nome_com_slogan": f"{dtc.nome} — {dtc.slogan}" if dtc.slogan else dtc.nome,
        "cor_primaria": dtc.cor_primaria,
        "cor_escura": dtc.cor_escura,
        "tracking_scripts": dtc.tracking_scripts,
        "potes": dtc.potes,
        "links": links,
        "vturb_embed": vturb_embed.strip(),
        "vturb_speed_script": vturb_speed_script.strip(),
        "pitch_seconds": pitch_seconds,
    }
    return {
        "index.html": _env.get_template("index.html.j2").render(ctx).encode("utf-8"),
        "assets/css/pricing-cards.css": _env.get_template("pricing-cards.css.j2")
        .render(ctx)
        .encode("utf-8"),
        "assets/css/main.css": (KIT_DIR / "main.css").read_bytes(),
    }


def to_webp(raw: bytes, max_width: int | None) -> bytes:
    img = Image.open(io.BytesIO(raw)).convert("RGBA")
    if max_width and img.width > max_width:
        img = img.resize(
            (max_width, round(img.height * max_width / img.width)), Image.Resampling.LANCZOS
        )
    out = io.BytesIO()
    img.save(out, "WEBP", quality=85, method=4)  # method=6: 30x mais lento, -4% de tamanho
    return out.getvalue()


# --- Serviço -----------------------------------------------------------------


def load_private_key_b64(b64: str, passphrase: str | None = None) -> paramiko.PKey:
    """Chave privada OpenSSH/PEM guardada em base64 numa variável de ambiente."""
    try:
        texto = base64.b64decode(b64.strip(), validate=True).decode("utf-8")
    except (ValueError, UnicodeDecodeError) as e:
        raise VslTemplateConfigError(f"PSSTOCK_SFTP_KEY_B64 não é base64 válido: {e}") from e
    erros = []
    for cls in (paramiko.Ed25519Key, paramiko.ECDSAKey, paramiko.RSAKey):
        try:
            return cls.from_private_key(io.StringIO(texto), password=passphrase)
        except (paramiko.SSHException, ValueError) as e:
            erros.append(f"{cls.__name__}: {e}")
    raise VslTemplateConfigError(
        "Não consegui ler a chave de PSSTOCK_SFTP_KEY_B64 (" + "; ".join(erros) + ")"
    )



class VslTemplateService:
    def __init__(self, settings: Settings):
        self.settings = settings

    def _fetch(self, client: httpx.Client, url: str) -> bytes:
        try:
            r = client.get(url)
            r.raise_for_status()
        except httpx.HTTPError as e:
            raise VslTemplateError(f"Falha ao baixar {url}: {e}") from e
        return r.content

    def build(
        self,
        dtc_url: str,
        *,
        nome: str | None,
        slogan: str | None,
        vturb_embed: str,
        vturb_speed_script: str,
        pitch_seconds: int,
    ) -> tuple[DtcData, dict[str, bytes], dict[str, bytes]]:
        """Baixa a DTC e devolve (dados extraídos, arquivos do preview, arquivos do ap)."""
        base_url = dtc_url if dtc_url.endswith("/") else dtc_url + "/"
        with httpx.Client(timeout=30, follow_redirects=True, headers=HTTP_HEADERS) as client:
            html = self._fetch(client, base_url).decode("utf-8", errors="replace")
            dtc = parse_dtc(html, base_url)
            if nome:
                dtc.nome = nome.strip()
            if slogan is not None:
                dtc.slogan = slogan.strip() or None
            imagens = {}
            for stem, largura in IMAGENS.items():
                url = dtc.imagens.get(stem)
                raw = self._fetch(client, url) if url else IMAGENS_PADRAO[stem].read_bytes()
                imagens[f"images/pots/{stem}.webp"] = to_webp(raw, largura)

        def render(ap: bool) -> dict[str, bytes]:
            return render_kit(
                dtc,
                ap=ap,
                vturb_embed=vturb_embed,
                vturb_speed_script=vturb_speed_script,
                pitch_seconds=pitch_seconds,
            ) | imagens

        return dtc, render(False), render(True)

    # --- SFTP ---

    def _check_config(self) -> None:
        s = self.settings
        if not (s.psstock_sftp_host and s.psstock_sftp_username and s.psstock_pvs_dir):
            raise VslTemplateConfigError(
                "PSSTOCK_SFTP_HOST / PSSTOCK_SFTP_USERNAME / PSSTOCK_PVS_DIR "
                "não definidos no .env"
            )
        if not (s.psstock_sftp_key_b64 or s.psstock_sftp_key_path or s.psstock_sftp_password):
            raise VslTemplateConfigError(
                "Defina PSSTOCK_SFTP_KEY_B64 (ou PSSTOCK_SFTP_KEY_PATH / "
                "PSSTOCK_SFTP_PASSWORD) no .env"
            )

    def _credentials(self) -> dict:
        s = self.settings
        passphrase = s.psstock_sftp_key_passphrase or None
        if s.psstock_sftp_key_b64:
            return {"pkey": load_private_key_b64(s.psstock_sftp_key_b64, passphrase)}
        if s.psstock_sftp_key_path:
            try:
                return {"pkey": paramiko.PKey.from_path(s.psstock_sftp_key_path, passphrase)}
            except (OSError, paramiko.SSHException) as e:
                raise VslTemplateConfigError(
                    f"Não consegui ler a chave PSSTOCK_SFTP_KEY_PATH "
                    f"({s.psstock_sftp_key_path}): {e}"
                ) from e
        return {"password": s.psstock_sftp_password}

    def _connect(self):
        self._check_config()
        s = self.settings
        credentials = self._credentials()
        transport = None
        try:
            transport = paramiko.Transport((s.psstock_sftp_host, s.psstock_sftp_port))
            transport.connect(username=s.psstock_sftp_username, **credentials)
            sftp = paramiko.SFTPClient.from_transport(transport)
            if sftp is None:
                raise paramiko.SSHException("sessão SFTP não abriu")
        except paramiko.AuthenticationException as e:
            if transport:
                transport.close()
            raise VslTemplateConfigError(
                f"psstock recusou o login de {s.psstock_sftp_username}@"
                f"{s.psstock_sftp_host} ({'chave' if 'pkey' in credentials else 'senha'})"
            ) from e
        except (OSError, paramiko.SSHException) as e:
            if transport:
                transport.close()
            raise VslTemplateUploadError(
                f"Falha ao conectar no psstock ({s.psstock_sftp_host}): {e}"
            ) from e
        return sftp, transport

    @staticmethod
    def _exists(sftp, path: str) -> bool:
        try:
            sftp.stat(path)
            return True
        except FileNotFoundError:
            return False

    @staticmethod
    def _makedirs(sftp, path: str) -> None:
        parts = []
        while path not in ("", "/"):
            try:
                if stat.S_ISDIR(sftp.stat(path).st_mode):
                    break
                raise VslTemplateError(f"{path} existe no psstock e não é pasta.")
            except FileNotFoundError:
                parts.append(path)
                path = posixpath.dirname(path)
        for p in reversed(parts):
            sftp.mkdir(p)

    def upload(
        self, codigo: str, pastas: dict[str, dict[str, bytes]], overwrite: bool
    ) -> None:
        """pastas: {nome_da_pasta: {caminho relativo: conteúdo}}."""
        base = posixpath.join(self.settings.psstock_pvs_dir, codigo)
        sftp, transport = self._connect()
        try:
            if not overwrite:
                existentes = [p for p in pastas if self._exists(sftp, posixpath.join(base, p))]
                if existentes:
                    raise VslTemplateConflict(
                        f"Já existe {codigo}/{', '.join(existentes)} no psstock — "
                        "use overwrite=true pra substituir."
                    )
            for pasta, arquivos in pastas.items():
                for rel, conteudo in arquivos.items():
                    remote = posixpath.join(base, pasta, rel)
                    try:
                        self._makedirs(sftp, posixpath.dirname(remote))
                        with sftp.open(remote, "wb") as f:
                            f.write(conteudo)
                    except (OSError, paramiko.SSHException) as e:
                        raise VslTemplateUploadError(
                            f"Falha ao gravar {codigo}/{pasta}/{rel} no psstock: {e}"
                        ) from e
                logger.info(
                    f"[vsl-templates] {codigo}/{pasta}: {len(arquivos)} arquivos enviados"
                )
        finally:
            sftp.close()
            transport.close()

    def generate(
        self,
        *,
        codigo: str,
        pagina: str,
        dtc_url: str,
        nome: str | None,
        slogan: str | None,
        vturb_embed: str,
        vturb_speed_script: str,
        pitch_seconds: int,
        overwrite: bool,
        dry_run: bool,
    ) -> dict:
        """Bloqueante (HTTP + Pillow + SFTP) — chamar via asyncio.to_thread."""
        if not dry_run:
            self._check_config()  # falha antes de baixar/converter tudo
        dtc, preview, ap = self.build(
            dtc_url,
            nome=nome,
            slogan=slogan,
            vturb_embed=vturb_embed,
            vturb_speed_script=vturb_speed_script,
            pitch_seconds=pitch_seconds,
        )
        pasta_ap = f"{pagina}(ap)"
        if not dry_run:
            self.upload(codigo, {pagina: preview, pasta_ap: ap}, overwrite)

        base_url = self.settings.psstock_pvs_url.rstrip("/")
        return {
            "status": "dry_run" if dry_run else "uploaded",
            "preview_url": f"{base_url}/{codigo}/{pagina}/",
            "ap_url": f"{base_url}/{codigo}/{pasta_ap}/",
            "extraido_da_dtc": {
                "nome": dtc.nome,
                "slogan": dtc.slogan,
                "cor_primaria": dtc.cor_primaria,
                "cor_escura": dtc.cor_escura,
                "potes": {p: vars(v) for p, v in dtc.potes.items()},
                "tracking": bool(dtc.tracking_scripts),
            },
            "arquivos": sorted(preview),
            "warnings": dtc.warnings,
        }
