"""
Cria offers no RedTrack a partir do payload de "oferta nova" (plataforma,
produto, funil, aff_id + lista de potes/urls do BuyGoods) e, pra cada uma,
sincroniza `products`/`checkout_links` no Supabase do AutoPages.

Monta a URL de tracking (URL do checkout + macros do RedTrack — aff_id do
payload e macros completos pra BuyGoods, só `?clickid={clickid}` pra
PagAmerican) e o título da offer, cria a offer via POST /offers e, com o
offer_id retornado, grava a linha correspondente em `checkout_links`
(criando o produto em `products` se ainda não existir). Por fim grava o
offer_id em `checkouts.id_rt_offer` do dashboard (ver checkout_offer_link.py).
"""

from typing import Optional

from loguru import logger
from supabase import AsyncClient, create_async_client

from app.config import Settings
from app.services.autopages_service import AutoPagesError, AutoPagesService
from app.services.checkout_offer_link import link_offer_to_checkout, resolve_network_id
from app.services.redtrack_service import RedTrackAPI, RedTrackAPIError

# Offer source (program) de cada rede no RedTrack, por `plataforma`.
_OFFER_SOURCE_ID_BY_PLATAFORMA = {
    "buygoods": "6685d5cfb9b57400016a1a95",
    "pagamerican": "6a9affa8cbd0b6a371635f4b",
    "jvzoo": "6ab2aca4824bddae486b1c51",
    "digistore": "66e77b3387c90d1870088ebe",
    "digistore24": "66e77b3387c90d1870088ebe",
}


def _get_offer_source_id(plataforma: str) -> str:
    offer_source_id = _OFFER_SOURCE_ID_BY_PLATAFORMA.get(plataforma.strip().lower())
    if offer_source_id is None:
        raise RedTrackAPIError(
            f"Plataforma '{plataforma}' não tem offer source do RedTrack cadastrado."
        )
    return offer_source_id


# Macros do RedTrack — ficam literais na URL, o RedTrack substitui no clique.
_TRACKING_MACROS = (
    "&subid={clickid}&subid2={rt_campaign}&utm_campaign={rt_campaign}"
    "&subid3={rt_ad}&subid5={sub20}"
)

# PagAmerican só aceita clickid — não usa aff_id nem os demais macros da BuyGoods.
_PAGAMERICAN_TRACKING_PARAMS = "?clickid={clickid}"

# JVZoo: o aff_id do payload vai como `aid` (nome do parâmetro de afiliado
# deles), sem os macros da BuyGoods. `tid` é o mesmo nome do placeholder que
# a JVZoo devolve no Affiliate Postback (ver PayloadNormalizer._normalize_jvzoo),
# então usamos ele aqui pra fechar o loop: RedTrack substitui {clickid} no
# clique, a JVZoo repassa esse valor de volta no postback como `tid`.
# Formato: https://www.jvzoo.com/b/118423/453109/99?aid=3636247&tid={clickid}
_JVZOO_TRACKING_PARAMS = "&tid={clickid}"

# Digistore: o aff_id do payload vai como `aff` (na DS24 o id do afiliado é o
# próprio username, ex. "gestorfour"). `cid` é o campo que o IPN devolve e o
# PayloadNormalizer lê como click_id (ver _normalize_digistore24).
# Formato: https://www.checkout-ds24.com/product/609807?aff=gestorfour&cid={clickid}&sid1={rt_campaign}&sid2={utm_campaign}
_DIGISTORE_TRACKING_PARAMS = "&cid={clickid}&sid1={rt_campaign}&sid2={utm_campaign}"


def _build_offer_url(checkout_url: str, aff_id: str, plataforma: str) -> str:
    plataforma_lower = plataforma.strip().lower()
    if plataforma_lower == "pagamerican":
        return f"{checkout_url}{_PAGAMERICAN_TRACKING_PARAMS}"
    if plataforma_lower == "jvzoo":
        return f"{checkout_url}?aid={aff_id}{_JVZOO_TRACKING_PARAMS}"
    if plataforma_lower in ("digistore", "digistore24"):
        separator = "&" if "?" in checkout_url else "?"
        return f"{checkout_url}{separator}aff={aff_id}{_DIGISTORE_TRACKING_PARAMS}"
    return f"{checkout_url}&aff_id={aff_id}{_TRACKING_MACROS}"


def _build_offer_title(
    plataforma: str, produto: str, numero_de_potes: str, funil: str
) -> str:
    return f"{plataforma} | {produto} | {numero_de_potes} Potes | Funil {funil}"


def _build_checkout_link_name(
    checkout_name: str, produto: str, numero_de_potes: str, funil: str
) -> str:
    # Minúsculo ("potes") de propósito — é o padrão já usado nas linhas
    # existentes de checkout_links no AutoPages, diferente do título da
    # offer no RedTrack ("Potes", maiúsculo).
    return f"{checkout_name} | {produto} | {numero_de_potes} potes | Funil {funil}"


class RedTrackOfferService:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.redtrack = RedTrackAPI(settings)
        self.autopages = AutoPagesService(settings)

    async def _get_dashboard_db(self, plataforma: str) -> tuple[Optional[AsyncClient], Optional[str]]:
        """Client do Supabase do dashboard + network_id da plataforma (fallback).

        Falha aqui só loga: o vínculo em `checkouts` é best-effort.
        """
        try:
            db = await create_async_client(
                self.settings.supabase_url, self.settings.supabase_key
            )
            return db, await resolve_network_id(db, plataforma)
        except Exception as exc:
            logger.error(f"[redtrack] Sem acesso ao Supabase do dashboard pra checkouts: {exc}")
            return None, None

    async def create_offers_from_payload(self, payload: dict) -> list[dict]:
        plataforma = payload["plataforma"]
        produto = payload["produto"]
        codigo_produto = payload["codigo_produto"]
        funil = payload["funil"]
        aff_id = payload["aff_id"]

        # Resolvidos uma vez só — compartilhados por todas as ofertas do payload.
        checkout = await self.autopages.get_checkout(plataforma)
        product_id = await self.autopages.get_or_create_product(codigo_produto, produto)
        offer_source_id = _get_offer_source_id(plataforma)
        dashboard_db, network_id = await self._get_dashboard_db(plataforma)

        created: list[dict] = []
        for oferta in payload["ofertas"]:
            numero_de_potes = oferta["numero_de_potes"]
            checkout_url = oferta["url"]

            title = _build_offer_title(plataforma, produto, numero_de_potes, funil)
            tracking_url = _build_offer_url(checkout_url, aff_id, plataforma)

            offer_id = None
            try:
                redtrack_response = await self.redtrack.create_offer(
                    title=title, url=tracking_url, program_id=offer_source_id
                )
                offer_id = redtrack_response.get("id")

                checkout_link = await self.autopages.create_checkout_link(
                    checkout_id=checkout["id"],
                    checkout_name=checkout["name"],
                    product_id=product_id,
                    product_code=codigo_produto,
                    dtc=False,
                    quantidade_potes=int(numero_de_potes),
                    redtrack_id=offer_id,
                    link=checkout_url,
                    name=_build_checkout_link_name(
                        checkout["name"], produto, numero_de_potes, funil
                    ),
                    funnel_number=int(funil),
                )
            except (RedTrackAPIError, AutoPagesError) as e:
                orphan_note = (
                    f" (offer '{offer_id}' já foi criada no RedTrack mas não "
                    f"sincronizada no AutoPages)"
                    if offer_id
                    else ""
                )
                logger.error(
                    f"[redtrack] falha ao processar oferta '{title}'{orphan_note} "
                    f"({len(created)} já sincronizada(s) antes desta): {e}"
                )
                raise type(e)(
                    f"{e}{orphan_note} — {len(created)} oferta(s) já sincronizada(s) "
                    f"antes da falha: {[c['offer_id'] for c in created]}"
                ) from e

            checkout_id = None
            if dashboard_db is not None:
                checkout_id = await link_offer_to_checkout(
                    dashboard_db,
                    offer_id=offer_id,
                    plataforma=plataforma,
                    url=checkout_url,
                    network_id=network_id,
                    checkout_id=(oferta.get("checkout_id") or "").strip() or None,
                )

            logger.info(
                f"[redtrack] Offer criada e sincronizada: '{title}' "
                f"(id={offer_id}, checkout_id={checkout_id})"
            )
            created.append(
                {
                    "numero_de_potes": numero_de_potes,
                    "offer_id": offer_id,
                    "title": title,
                    "url": tracking_url,
                    "checkout_link_id": checkout_link["id"],
                    "checkout_id": checkout_id,
                }
            )

        return created
