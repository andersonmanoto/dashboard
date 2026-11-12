from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from loguru import logger
from pydantic import BaseModel

from app.config import Settings, get_settings
from app.dependencies import verify_redtrack_offers_key
from app.services.autopages_service import AutoPagesError
from app.services.offer_announcement import notify_offer_created
from app.services.redtrack_offer_service import RedTrackOfferService
from app.services.redtrack_offer_url_service import (
    OfferUrlUpdateError,
    RedTrackOfferUrlService,
)
from app.services.redtrack_service import RedTrackAPIError

router = APIRouter(tags=["RedTrack"])


class OfertaItem(BaseModel):
    numero_de_potes: str
    url: str
    # checkouts.id (dashboard) da linha Purchase deste pote. Opcional na
    # transição: sem ele, a linha é achada pelo checkout_code da URL.
    checkout_id: Optional[str] = None


class UpdateOfferUrlRequest(BaseModel):
    checkout_id: str
    url: str


class CreateOffersRequest(BaseModel):
    plataforma: str
    produto: str
    codigo_produto: str
    funil: str
    aff_id: str
    ofertas: list[OfertaItem]


@router.post(
    "/offers-rt-ap",
    dependencies=[Depends(verify_redtrack_offers_key)],
)
async def create_redtrack_offers(
    payload: CreateOffersRequest,
    background_tasks: BackgroundTasks,
    settings: Settings = Depends(get_settings),
):
    """
    Cria uma offer no RedTrack (POST /offers) para cada item de `ofertas`,
    usando o template de URL de checkout do BuyGoods (aff_id do payload +
    macros do RedTrack) e o título "{plataforma} | {produto} | {potes}
    Potes | Funil {funil}". Pra cada offer criada, sincroniza
    `products`/`checkout_links` no Supabase do AutoPages (find-or-create
    de produto por `código_produto`, checkout resolvido por `plataforma`).
    Depois de tudo criado, manda o aviso de produto liberado no Slack
    (#backend-geral) em background -- ver offer_announcement.py.
    O offer_id também é gravado em `checkouts.id_rt_offer` do dashboard
    (pelo `checkout_id` de cada oferta ou, sem ele, pelo checkout_code da
    URL) -- ver checkout_offer_link.py. Falha nesse vínculo não derruba a
    requisição: a oferta volta com `checkout_id: null`.
    """
    try:
        service = RedTrackOfferService(settings)
        ofertas_criadas = await service.create_offers_from_payload(
            payload.model_dump()
        )
    except (RedTrackAPIError, AutoPagesError) as e:
        logger.error(f"[redtrack] erro ao criar offers: {e}")
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(e))

    background_tasks.add_task(notify_offer_created, settings, payload.model_dump())

    return {
        "plataforma": payload.plataforma,
        "produto": payload.produto,
        "codigo_produto": payload.codigo_produto,
        "funil": payload.funil,
        "aff_id": payload.aff_id,
        "ofertas": ofertas_criadas,
    }


@router.put(
    "/offers-rt-ap/url",
    dependencies=[Depends(verify_redtrack_offers_key)],
)
async def update_redtrack_offer_url(
    payload: UpdateOfferUrlRequest,
    settings: Settings = Depends(get_settings),
):
    """
    Troca o buylink da offer do RedTrack ligada a `checkout_id`
    (checkouts.id_rt_offer). O front manda o buylink cru; os parâmetros de
    tracking são montados aqui pela rede do checkout, com o aff_id que a
    offer já tem. Também atualiza checkouts.url/checkout_code e
    checkout_links.link no AutoPages -- ver redtrack_offer_url_service.py.
    """
    try:
        service = RedTrackOfferUrlService(settings)
        return await service.update_offer_url(payload.checkout_id, payload.url)
    except OfferUrlUpdateError as e:
        logger.error(f"[redtrack] erro ao atualizar URL da offer: {e}")
        raise HTTPException(e.status_code, str(e))
    except (RedTrackAPIError, AutoPagesError) as e:
        logger.error(f"[redtrack] erro ao atualizar URL da offer: {e}")
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(e))
