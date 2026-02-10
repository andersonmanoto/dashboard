from pathlib import Path
from uuid import uuid4
from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    HTTPException,
    Query,
    UploadFile,
    status,
)
from loguru import logger

from app.config import Settings, get_settings
from app.dependencies import (
    get_database_repository,
    verify_upload_key,
    get_api_key_scanner,
)
from app.repositories.database import DatabaseRepository
from app.services.retro_service import run_retro_background
from app.services.web_scanner import WebScannerService

router = APIRouter(tags=["Tools"])


@router.post(
    "/retro-buygoods/upload",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(verify_upload_key)],
)
async def upload_spreadsheet(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    settings: Settings = Depends(get_settings),
    db_repo: DatabaseRepository = Depends(get_database_repository),
):
    """Upload de planilhas para importação retroativa."""
    filename = file.filename.lower()
    if not filename.endswith((".csv", ".xlsx", ".xls")):
        raise HTTPException(400, "Formato inválido. Use .csv ou .xlsx")

    temp_dir = Path(settings.temp_dir)
    if not temp_dir.exists():
        temp_dir.mkdir(parents=True, exist_ok=True)

    temp_filename = f"import_{uuid4()}_{file.filename}"
    temp_path = temp_dir / temp_filename

    try:
        content = await file.read()
        temp_path.write_bytes(content)

        background_tasks.add_task(
            run_retro_background, str(temp_path.absolute()), db_repo
        )
        return {"status": "queued", "file_id": temp_filename}

    except Exception as e:
        if temp_path.exists():
            temp_path.unlink(missing_ok=True)
        logger.error(f"Erro upload: {e}")
        raise HTTPException(500, "Falha ao salvar arquivo")


@router.get("/scanner/offers/lookup")
async def find_offer_in_funnel(
    codename: str = Query(..., description="Ex: vis3"),
    domain: str = Query(..., description="Ex: visiumpro.com"),
    debug: bool = Query(False),
    token: str = Depends(get_api_key_scanner),
):
    """Ferramenta de Diagnóstico: Web Scanner."""
    service = WebScannerService()
    results = await service.run_scan(
        codename=codename, domain_filter=domain, debug=debug
    )
    return {"message": "Scan finalizado", "data": results or []}
