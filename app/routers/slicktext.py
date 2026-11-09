import asyncio
from datetime import date
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import JSONResponse
from loguru import logger
from pydantic import BaseModel, Field, model_validator

from app.config import Settings, get_settings
from app.dependencies import (
    get_database_repository,
    verify_slicktext_input_key,
    verify_slicktext_setup_key,
)
from app.repositories.database import DatabaseRepository
from app.services.slicktext_setup_service import (
    SlickTextSetupError,
    SlickTextSetupService,
)

router = APIRouter(tags=["SlickText"])


class ProductItem(BaseModel):
    product_id: UUID
    product_name: str = Field(min_length=1)


class CreateSlickTextAccountRequest(BaseModel):
    name: str = Field(min_length=1)
    api_key: str = Field(min_length=1)
    brand_id: str = Field(min_length=1)
    products: list[ProductItem] = Field(min_length=1)


@router.post(
    "/slicktext/accounts",
    dependencies=[Depends(verify_slicktext_setup_key)],
)
async def create_slicktext_account(
    payload: CreateSlickTextAccountRequest,
    settings: Settings = Depends(get_settings),
    db_repo: DatabaseRepository = Depends(get_database_repository),
):
    """
    Cadastra uma conta SlickText: valida a key, cria na SlickText as listas
    "[PRODUTO] [Compra Aprovada]" / "[PRODUTO] [Carrinho Abandonado]" de cada
    produto, a lista "rastreio" e os custom fields, e grava conta (api_key no
    Vault), shipped_list_id e slicktext_product_lists no banco.
    """
    service = SlickTextSetupService(settings.slicktext_api_url, db_repo)
    try:
        account_id = await service.create_account(
            name=payload.name.strip(),
            api_key=payload.api_key.strip(),
            brand_id=payload.brand_id.strip(),
            products=[p.model_dump(mode="json") for p in payload.products],
        )
    except SlickTextSetupError as e:
        return JSONResponse(
            status_code=e.status_code,
            content={"ok": False, "error": e.error, "message": e.message},
        )

    return JSONResponse(
        status_code=status.HTTP_201_CREATED,
        content={
            "ok": True,
            "slicktext_account_id": account_id,
            "message": "Conta, listas e custom fields criados e cadastrados com sucesso",
        },
    )


class SlickTextInputRequest(BaseModel):
    """
    Input de leads em lote (docs/sms-input-leads-contrato-backend.md). Os
    filtros valem da linha em slicktext_inputs; aqui servem só pra log e pra
    conferir o input_list_id.
    """

    input_id: UUID
    input_list_id: UUID
    start_date: date
    end_date: date
    network_id: Optional[UUID] = None
    product_id: Optional[UUID] = None
    quantity: Optional[int] = None
    funnel_number: Optional[int] = None

    @model_validator(mode="after")
    def _check_period(self):
        if self.end_date < self.start_date:
            raise ValueError("end_date antes de start_date")
        return self


def _input_error(code: int, error: str, input_id: UUID) -> JSONResponse:
    return JSONResponse(
        status_code=code,
        content={"accepted": False, "error": error, "input_id": str(input_id)},
    )


@router.post(
    "/slicktext/inputs",
    dependencies=[Depends(verify_slicktext_input_key)],
)
async def create_slicktext_input(
    payload: SlickTextInputRequest,
    request: Request,
    db_repo: DatabaseRepository = Depends(get_database_repository),
):
    """
    Aceita o input e enfileira o job que reúne os leads (task_slicktext_input_start).
    Responde 202 sem esperar a SlickText. pending -> 202 e segue;
    processing/done -> 202 sem recomeçar; error -> 409; input inexistente ou
    com outra lista -> 400.
    """
    input_id = str(payload.input_id)
    logger.info(f"[SlickText input] POST recebido: {payload.model_dump(mode='json')}")

    res = await asyncio.to_thread(
        lambda: db_repo.client.table("slicktext_inputs")
        .select("id, input_list_id, status")
        .eq("id", input_id)
        .limit(1)
        .execute()
    )
    row = (res.data or [None])[0]
    if not row:
        return _input_error(400, "input_nao_encontrado", payload.input_id)
    if row["input_list_id"] != str(payload.input_list_id):
        return _input_error(400, "input_list_id_divergente", payload.input_id)
    if row["status"] == "error":
        return _input_error(409, "input_em_erro", payload.input_id)

    if row["status"] == "pending":
        redis_pool = getattr(request.app.state, "redis_pool", None)
        try:
            await redis_pool.enqueue_job(
                "task_slicktext_input_start",
                input_id,
                _job_id=f"slicktext_input_start:{input_id}",
            )
        except Exception:
            # o input continua pending; a varredura do worker pega depois
            logger.exception(f"[SlickText input] não enfileirei o job de {input_id}")

    return JSONResponse(
        status_code=status.HTTP_202_ACCEPTED,
        content={"accepted": True, "input_id": input_id},
    )
