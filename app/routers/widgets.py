from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, HttpUrl

from app.dependencies import get_database_repository, verify_widget_sync_key
from app.config import Settings, get_settings
from app.repositories.database import DatabaseRepository
from app.services.widget_sync_service import WidgetSyncError, WidgetSyncService

router = APIRouter(tags=["Widgets"])


class WidgetSyncRequest(BaseModel):
    product_id: UUID
    network_id: UUID
    script_url: HttpUrl


@router.post(
    "/widgets/sync",
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(verify_widget_sync_key)],
)
async def sync_widget(
    payload: WidgetSyncRequest,
    settings: Annotated[Settings, Depends(get_settings)],
    db_repo: Annotated[DatabaseRepository, Depends(get_database_repository)],
):
    """
    Busca o script já publicado no CDN do tigeroffers_widget
    (https://{WIDGET_CDN_HOST}/...) e faz upsert em `widgets`
    (product_id + network_id), atualizando `script_widget`.

    Os repositórios continuam separados — isso só copia o conteúdo já
    publicado pro Supabase do dashboard.
    """
    service = WidgetSyncService(settings, db_repo)
    try:
        row = await service.sync(
            product_id=str(payload.product_id),
            network_id=str(payload.network_id),
            script_url=str(payload.script_url),
        )
    except WidgetSyncError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from e

    return {"status": "ok", "widget": row}
