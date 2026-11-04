from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from loguru import logger
from pydantic import BaseModel

from app.config import Settings, get_settings
from app.dependencies import verify_redtrack_offers_key
from app.services.autopages_service import AutoPagesError
from app.services.offer_announcement import notify_offer_created
from app.services.redtrack_offer_service import RedTrackOfferService
from app.services.redtrack_service import RedTrackAPIError

router = APIRouter(tags=["RedTrack"])


class OfertaItem(BaseModel):
    numero_de_potes: str
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
