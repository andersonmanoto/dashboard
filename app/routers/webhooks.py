from typing import Annotated
from fastapi import APIRouter, Depends, HTTPException, Request, status, BackgroundTasks
from starlette.requests import ClientDisconnect
from loguru import logger

from app.config import Settings, get_settings
from app.dependencies import (
    get_database_repository,
    verify_secret_token,
    extract_payload,
    get_event_processor,
)
from app.repositories.database import DatabaseRepository
from app.models.enums import NetworkType
from app.services.event_processor import EventProcessor
from app.services.slicktext_service import process_slicktext_sync_task

# Importações para o cliente assíncrono do Supabase na Inbox
from supabase import AsyncClient, create_async_client

router = APIRouter(tags=["Webhooks"])

# Instância Singleton do cliente assíncrono dedicado para a inbox do webhook
_inbox_supabase_async: AsyncClient | None = None

async def get_inbox_supabase(settings: Settings) -> AsyncClient:
    global _inbox_supabase_async
    if _inbox_supabase_async is None:
        _inbox_supabase_async = await create_async_client(
            settings.supabase_url, 
            settings.supabase_key
        )
    return _inbox_supabase_async


@router.post("/buygoods/{secret_token}")
async def webhook_buygoods(
    secret_token: str,
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    # Validação do token
    auth: None = Depends(verify_secret_token),
) -> dict:
    """Recebe Webhooks da BuyGoods."""
    try:
        payload = await extract_payload(request)

        # 1. Salva na Inbox usando cliente assíncrono nativo (evita travamentos de pool)
        db_async = await get_inbox_supabase(settings)
        
        response = await db_async.table("webhook_inbox").insert({
            "network": NetworkType.BUYGOODS.value,
            "payload": payload,
            "status": "pending"
        }).execute()
        
        inbox_id = response.data[0]["id"] if response.data else None

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
    auth: None = Depends(verify_secret_token),
) -> dict:
    """Recebe Postbacks da DigiStore24."""
    try:
        payload = dict(request.query_params)
        order_id = payload.get("order_id")

        # 1. Salva na Inbox de forma assíncrona (Padronizado com a BuyGoods)
        db_async = await get_inbox_supabase(settings)
        
        response = await db_async.table("webhook_inbox").insert({
            "network": NetworkType.DIGISTORE24.value,
            "payload": payload,
            "status": "pending"
        }).execute()

        inbox_id = response.data[0]["id"] if response.data else None

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


@router.post("/buygoods/abandon/{secret_token}")
async def buygoods_abandon_webhook(
    secret_token: str,
    request: Request,
    processor: Annotated[EventProcessor, Depends(get_event_processor)],
    background_tasks: BackgroundTasks,
    settings: Annotated[Settings, Depends(get_settings)],
    db_repo: Annotated[DatabaseRepository, Depends(get_database_repository)],
    auth: None = Depends(verify_secret_token),
):
    """Recebe Webhooks de Carrinho Abandonado da BuyGoods."""
    try:
        raw_body = await request.body()
        decoded = raw_body.decode("iso-8859-1")

        # Parse manual do form urlencoded respeitando o charset correto
        from urllib.parse import parse_qs

        parsed = parse_qs(decoded, keep_blank_values=True)
        payload = {k: v[0] for k, v in parsed.items()}

        if not payload:
            raise HTTPException(status_code=400, detail="Empty payload")

        # Seu fluxo original que salva no Supabase continua intacto
        success = await processor.process_buygoods_abandon_cart(payload)
        if not success:
            raise HTTPException(
                status_code=500, detail="Error processing abandoned cart"
            )

        # DISPARA O SLICKTEXT EM BACKGROUND
        # Aqui o db_repo ainda é necessário porque é injetado na task em background
        background_tasks.add_task(
            process_slicktext_sync_task,
            payload=payload,
            settings=settings,
            db_repo=db_repo,
        )

        return {"status": "success"}

    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Erro no webhook de abandon cart: {e}")
        raise HTTPException(status_code=500, detail="Internal Server Error")