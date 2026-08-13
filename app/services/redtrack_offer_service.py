"""
Cria offers no RedTrack a partir do payload de "oferta nova" (plataforma,
produto, funil, aff_id + lista de potes/urls do BuyGoods) e, pra cada uma,
sincroniza `products`/`checkout_links` no Supabase do AutoPages.

Monta a URL de tracking (URL do checkout + aff_id do payload + macros do
RedTrack) e o título da offer, cria a offer via POST /offers e, com o
offer_id retornado, grava a linha correspondente em `checkout_links`
(criando o produto em `products` se ainda não existir).
"""

from loguru import logger

from app.config import Settings
from app.services.autopages_service import AutoPagesError, AutoPagesService
from app.services.redtrack_service import RedTrackAPI, RedTrackAPIError

# Offer source (program) do BuyGoods no RedTrack. Fixo por enquanto — só
# existe essa origem cadastrada; quando surgir uma segunda rede, isso vira
# um lookup por `plataforma`.
BUYGOODS_OFFER_SOURCE_ID = "6685d5cfb9b57400016a1a95"

# Macros do RedTrack — ficam literais na URL, o RedTrack substitui no clique.
_TRACKING_MACROS = (
    "&subid={clickid}&subid2={rt_campaign}&utm_campaign={rt_campaign}"
    "&subid3={rt_ad}&subid5={sub20}"
)


def _build_offer_url(checkout_url: str, aff_id: str) -> str:
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
        self.redtrack = RedTrackAPI(settings)
        self.autopages = AutoPagesService(settings)

    async def create_offers_from_payload(self, payload: dict) -> list[dict]:
        plataforma = payload["plataforma"]
        produto = payload["produto"]
        codigo_produto = payload["codigo_produto"]
        funil = payload["funil"]
        aff_id = payload["aff_id"]

        # Resolvidos uma vez só — compartilhados por todas as ofertas do payload.
        checkout = await self.autopages.get_checkout(plataforma)
        product_id = await self.autopages.get_or_create_product(codigo_produto, produto)

        created: list[dict] = []
        for oferta in payload["ofertas"]:
            numero_de_potes = oferta["numero_de_potes"]
            checkout_url = oferta["url"]

            title = _build_offer_title(plataforma, produto, numero_de_potes, funil)
            tracking_url = _build_offer_url(checkout_url, aff_id)

            offer_id = None
            try:
                redtrack_response = await self.redtrack.create_offer(
                    title=title, url=tracking_url, program_id=BUYGOODS_OFFER_SOURCE_ID
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

            logger.info(f"[redtrack] Offer criada e sincronizada: '{title}' (id={offer_id})")
            created.append(
                {
                    "numero_de_potes": numero_de_potes,
                    "offer_id": offer_id,
                    "title": title,
                    "url": tracking_url,
                    "checkout_link_id": checkout_link["id"],
                }
            )

        return created
