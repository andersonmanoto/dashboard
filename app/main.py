from contextlib import asynccontextmanager
from json import JSONDecodeError
from pathlib import Path
from typing import Annotated
from uuid import uuid4

from arq import create_pool
from arq.connections import RedisSettings
from fastapi import (
    BackgroundTasks,
    Depends,
    FastAPI,
    File,
    Header,
    HTTPException,
    Query,
    Request,
    Security,
    UploadFile,
    status,
)
from fastapi.security import APIKeyHeader
from loguru import logger

from app.config import Settings, get_settings
from app.dependencies import (
    get_database_repository,
)
from app.models.enums import NetworkType
from app.repositories.database import DatabaseRepository
from app.services.retro_service import run_retro_background
from app.services.web_scanner import WebScannerService


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Gerencia o ciclo de vida da aplicação (Startup e Shutdown).

    Agora inclui a inicialização do Pool de Conexões do Redis (ARQ)
    para enfileiramento de jobs de alta performance.
    """
    logger.info("Webhook Dashboard iniciado")

    # Inicializa conexão com Redis
    settings = get_settings()
    try:
        app.state.redis_pool = await create_pool(
            RedisSettings(host=settings.redis_host, port=settings.redis_port)
        )
        logger.info(
            f"Redis Pool conectado em {settings.redis_host}:{settings.redis_port}"
        )
    except Exception as e:
        logger.critical(f"Falha ao conectar no Redis: {e}")
        raise e

    yield

    # Fecha conexão ao desligar
    if hasattr(app.state, "redis_pool"):
        await app.state.redis_pool.close()
        logger.info("Redis Pool fechado")

    logger.info("Webhook Dashboard encerrado")


# Configuração da aplicação
app = FastAPI(
    title="Tiger Offers - Webhook Dashboard",
    description="API para normalizar e processar webhooks de redes de afiliados",
    version="2.3.0",
    lifespan=lifespan,
)

# ========== HELPERS ==========

API_KEY_NAME = "x-token"
api_key_header = APIKeyHeader(name=API_KEY_NAME, auto_error=False)


async def get_api_key(api_key_header: str = Security(api_key_header)):
    """Valida a chave de API (Token) para rotas protegidas (Scanner)."""
    settings = get_settings()

    if not settings.scanner_secret_token:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Erro de segurança: Token do scanner não configurado no servidor.",
        )

    if api_key_header == settings.scanner_secret_token:
        return api_key_header

    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Acesso Negado: Token inválido ou ausente.",
    )


def verify_secret_token(secret_token: str, settings: Settings) -> None:
    """Valida token secreto do webhook na URL."""
    if not settings.webhook_secret:
        logger.critical("WEBHOOK_SECRET não configurado no servidor!")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Configuration Error",
        )

    if secret_token != settings.webhook_secret:
        logger.warning(f"Acesso negado. Token inválido: {secret_token}")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Acesso Proibido"
        )


async def extract_payload(request: Request) -> dict:
    """Extrai JSON ou Form Data da requisição."""
    try:
        return await request.json()
    except (JSONDecodeError, ValueError):
        form_data = await request.form()
        return dict(form_data)


async def verify_upload_key(
    x_api_key: str = Header(None), settings: Settings = Depends(get_settings)
) -> None:
    """Valida a chave de API específica para uploads."""
    if not settings.upload_api_key:
        logger.critical("UPLOAD_API_KEY não configurada no servidor!")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Configuration Error",
        )

    if x_api_key != settings.upload_api_key:
        logger.warning(f"Tentativa de upload não autorizada. Key inválida: {x_api_key}")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Credenciais inválidas"
        )


# ========== ENDPOINTS ==========


@app.post("/buygoods/{secret_token}")
async def webhook_buygoods(
    secret_token: str,
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    db_repo: Annotated[DatabaseRepository, Depends(get_database_repository)],
) -> dict:
    """
    Recebe Webhooks da BuyGoods (Híbrido: DB + Redis).

    1. Salva na Inbox (PostgreSQL) para segurança/backup.
    2. Enfileira o ID no Redis para processamento imediato pelo Worker.
    """
    verify_secret_token(secret_token, settings)

    try:
        payload = await extract_payload(request)

        # 1. Salva no Banco (Segurança)
        inbox_id = db_repo.create_inbox_entry(
            network=NetworkType.BUYGOODS.value, payload=payload
        )

        # 2. Enfileira no Redis (Performance)
        await request.app.state.redis_pool.enqueue_job(
            "task_process_webhook",
            network_str=NetworkType.BUYGOODS.value,
            payload=payload,
            inbox_id=inbox_id,
        )

        logger.info(f"BuyGoods: Salvo na Inbox e enfileirado no Redis. ID: {inbox_id}")
        return {"status": "queued", "inbox_id": inbox_id}

    except Exception as e:
        logger.exception(f"Erro ao processar webhook BuyGoods: {e}")
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR)


@app.get("/digistore24/{secret_token}")
@app.post("/digistore24/{secret_token}")
async def webhook_digistore24(
    secret_token: str,
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict:
    """
    Recebe Webhooks da DigiStore24 (Redis Direct).

    Envia diretamente para a fila do Redis.
    Aceita tanto GET quanto POST conforme documentação da DS24.
    """
    verify_secret_token(secret_token, settings)

    try:
        # Extrai payload
        if request.method == "GET":
            payload = dict(request.query_params)
        else:
            payload = await extract_payload(request)

        order_id = payload.get("order_id")
        action = payload.get("transaction_type", "sale")

        logger.info(
            f"DigiStore24: Recebido Order {order_id} ({action}) via {request.method}"
        )

        # Enfileira no Redis (inbox_id=None pois não salvamos na inbox nesta rota)
        await request.app.state.redis_pool.enqueue_job(
            "task_process_webhook",
            network_str=NetworkType.DIGISTORE24.value,
            payload=payload,
            inbox_id=None,
        )

        return {"status": "received", "id": order_id}

    except Exception as e:
        logger.exception(f"Erro Digistore: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal Server Error",
        )


@app.post(
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
    """
    Upload de planilhas para importação retroativa.
    Mantido com BackgroundTasks pois é um arquivo local longo, não um evento de fila.
    """
    filename = file.filename.lower()
    if not filename.endswith((".csv", ".xlsx", ".xls")):
        raise HTTPException(400, "Formato inválido. Use .csv ou .xlsx")

    # Garante diretório temporário
    temp_dir = Path(settings.temp_dir)
    if not temp_dir.exists():
        try:
            temp_dir.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            logger.error(f"Erro criando temp dir: {e}")
            raise HTTPException(500, "Erro interno de servidor")

    temp_filename = f"import_{uuid4()}_{file.filename}"
    temp_path = temp_dir / temp_filename

    try:
        content = await file.read()
        temp_path.write_bytes(content)

        logger.info(f"Arquivo recebido: {temp_path}")

        background_tasks.add_task(
            run_retro_background, str(temp_path.absolute()), db_repo
        )

        return {
            "status": "queued",
            "message": "Importação iniciada em background.",
            "file_id": temp_filename,
        }

    except Exception as e:
        if temp_path.exists():
            temp_path.unlink(missing_ok=True)
        logger.error(f"Erro no upload: {e}")
        raise HTTPException(500, "Falha ao salvar arquivo")


@app.get("/scanner/offers/lookup")
async def find_offer_in_funnel(
    codename: str = Query(..., description="Ex: vis3"),
    domain: str = Query(..., description="Ex: visiumpro.com"),
    debug: bool = Query(False, description="Logs no terminal"),
    token: str = Depends(get_api_key),
):
    """
    Ferramenta de Diagnóstico: Web Scanner.
    """
    service = WebScannerService()

    results = await service.run_scan(
        codename=codename, domain_filter=domain, debug=debug
    )

    if not results:
        return {
            "message": "Links não encontrado.",
            "data": [],
        }

    return {
        "message": "Scan finalizado.",
        "total_found": len(results),
        "data": results,
    }
