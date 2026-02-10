from typing import Annotated
from fastapi import APIRouter, Depends, HTTPException, Request, status
from starlette.requests import ClientDisconnect
from loguru import logger

from app.config import Settings, get_settings
from app.dependencies import (
    get_database_repository,
    verify_secret_token,
    extract_payload,
)
from app.repositories.database import DatabaseRepository
from app.models.enums import NetworkType

router = APIRouter(tags=["Webhooks"])


@router.post("/buygoods/{secret_token}")
async def webhook_buygoods(
    secret_token: str,
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    db_repo: Annotated[DatabaseRepository, Depends(get_database_repository)],
    # Validação do token
    auth: None = Depends(verify_secret_token),
) -> dict:
    """Recebe Webhooks da BuyGoods."""
    try:
        payload = await extract_payload(request)

        # 1. Salva na Inbox
        inbox_id = db_repo.create_inbox_entry(
            network=NetworkType.BUYGOODS.value, payload=payload
        )

        # 2. Enfileira no Redis
        await request.app.state.redis_pool.enqueue_job(
            "task_process_webhook",
            network_str=NetworkType.BUYGOODS.value,
            payload=payload,
            inbox_id=inbox_id,
        )

        return {"status": "queued", "inbox_id": inbox_id}

    except ClientDisconnect:
        logger.warning("BuyGoods: Cliente desconectou.")
        return {"status": "incomplete", "message": "Client disconnected"}
    except Exception as e:
        logger.exception(f"Erro BuyGoods: {e}")
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR)


@router.get("/digistore24/{secret_token}")
async def webhook_digistore24(
    secret_token: str,
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    db_repo: Annotated[DatabaseRepository, Depends(get_database_repository)],
    auth: None = Depends(verify_secret_token),
) -> dict:
    """Recebe Postbacks da DigiStore24."""
    try:
        payload = dict(request.query_params)
        order_id = payload.get("order_id")

        # 1. Salva na Inbox
        inbox_id = db_repo.create_inbox_entry(
            network=NetworkType.DIGISTORE24.value, payload=payload
        )

        # 2. Enfileira no Redis
        await request.app.state.redis_pool.enqueue_job(
            "task_process_webhook",
            network_str=NetworkType.DIGISTORE24.value,
            payload=payload,
            inbox_id=inbox_id,
        )

        return {"status": "received", "id": order_id}

    except Exception as e:
        logger.exception(f"Erro DigiStore: {e}")
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR)
