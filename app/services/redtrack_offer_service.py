"""
Cria offers no RedTrack a partir do payload de "oferta nova" (plataforma,
produto, funil, aff_id + lista de potes/urls do BuyGoods).

Monta a URL de tracking (URL do checkout + aff_id do payload + macros do
RedTrack) e o título da offer, e cria uma offer por item de `ofertas` via
POST /offers. Retorna os ids criados — vão alimentar o AutoPages depois.
"""

from loguru import logger

from app.config import Settings
from app.services.redtrack_service import RedTrackAPI, RedTrackAPIError

# Offer source (network) do BuyGoods no RedTrack. Fixo por enquanto — só
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


class RedTrackOfferService:
    def __init__(self, settings: Settings):
        self.client = RedTrackAPI(settings)

    async def create_offers_from_payload(self, payload: dict) -> list[dict]:
        plataforma = payload["plataforma"]
        produto = payload["produto"]
        funil = payload["funil"]
        aff_id = payload["aff_id"]

        created: list[dict] = []
        for oferta in payload["ofertas"]:
            numero_de_potes = oferta["numero_de_potes"]
            title = _build_offer_title(plataforma, produto, numero_de_potes, funil)
            url = _build_offer_url(oferta["url"], aff_id)

            try:
                response = await self.client.create_offer(
                    title=title, url=url, program_id=BUYGOODS_OFFER_SOURCE_ID
                )
            except RedTrackAPIError as e:
                logger.error(
                    f"[redtrack] falha ao criar offer '{title}' "
                    f"({len(created)} já criada(s) antes desta): {e}"
                )
                raise RedTrackAPIError(
                    f"{e} — {len(created)} offer(s) já criada(s) antes da falha: "
                    f"{[c['offer_id'] for c in created]}"
                ) from e

            logger.info(f"[redtrack] Offer criada: '{title}' (id={response.get('id')})")
            created.append(
                {
                    "numero_de_potes": numero_de_potes,
                    "offer_id": response.get("id"),
                    "title": title,
                    "url": url,
                }
            )

        return created
