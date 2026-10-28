from uuid import UUID

from fastapi import APIRouter, Depends, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.config import Settings, get_settings
from app.dependencies import get_database_repository, verify_slicktext_setup_key
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
