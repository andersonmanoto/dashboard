"""
API FastAPI para receber e processar webhooks de redes de afiliados.
"""
import json
from uuid import uuid4
from typing import Annotated
from pathlib import Path

from fastapi import (
    FastAPI, Request,
    HTTPException,
    BackgroundTasks,
    Depends,
    status,
    UploadFile,
    File, 
    Header
)
from loguru import logger
from contextlib import asynccontextmanager

from app.config import Settings, get_settings
from dependencies import (
    get_payload_normalizer,
    get_event_processor,
    get_database_repository
)
from models.enums import NetworkType
from services.normalizer import PayloadNormalizer
from services.event_processor import EventProcessor
from services.retro_service import SpreadsheetRetro, run_retro_background
from repositories.database import DatabaseRepository

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Webhook Dashboard iniciado")
    
    yield
    logger.info("Webhook Dashboard encerrado")

# Configuração da aplicação
app = FastAPI(
    title="Tiger Offers - Webhook Dashboard",
    description="API para normalizar e processar webhooks de redes de afiliados",
    version="2.2.0",
    lifespan=lifespan
)


# ========== HELPERS ==========

def verify_secret_token(secret_token: str, settings: Settings) -> None:
    """
    Valida token secreto do webhook.
    
    Args:
        secret_token: Token recebido na URL
        settings: Configurações da aplicação
        
    Raises:
        HTTPException: Se token for inválido
    """
    if not settings.webhook_secret:
        logger.critical("WEBHOOK_SECRET não configurado no servidor!")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Configuration Error"
        )
    
    if secret_token != settings.webhook_secret:
        logger.warning(f"Acesso negado. Token inválido: {secret_token}")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Acesso Proibido"
        )


async def extract_payload(request: Request) -> dict:
    """
    Extrai payload do request (JSON ou form data).
    
    Args:
        request: Request do FastAPI
        
    Returns:
        dict: Payload extraído
    """
    try:
        return await request.json()
    except:
        form_data = await request.form()
        return dict(form_data)


async def process_webhook_background(
    normalizer: PayloadNormalizer,
    processor: EventProcessor,
    network: NetworkType,
    payload: dict
) -> None:
    """
    Processa webhook em background task.
    
    Args:
        normalizer: Serviço de normalização
        processor: Processador de eventos
        network: Rede de origem
        payload: Dados do webhook
    """
    try:
        # Normaliza payload
        normalized_event = normalizer.normalize(network, payload)
        
        # Processa evento
        await processor.process_event(normalized_event)
        
    except ValueError as ve:
        logger.warning(f"Payload Inválido ({network}): {ve}")
    except Exception as e:
        logger.exception(f"Erro no processamento ({network}): {e}")

# ========== HELPERS - SECURITY ==========

async def verify_upload_key(
    x_api_key: str = Header(None),
    settings: Settings = Depends(get_settings)
) -> None:
    """
    Verifica a chave de API para uploads no header 'x-api-key'.
    """
    if not settings.upload_api_key:
        logger.critical("UPLOAD_API_KEY não configurada no servidor!")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Configuration Error"
        )

    if x_api_key != settings.upload_api_key:
        logger.warning(f"Tentativa de upload não autorizada. Key inválida: {x_api_key}")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Credenciais inválidas"
        )

# ========== ENDPOINTS ==========

@app.post("/buygoods/{secret_token}")
async def webhook_buygoods(
    secret_token: str,
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    db_repo: Annotated[DatabaseRepository, Depends(get_database_repository)]
) -> dict:
    
    verify_secret_token(secret_token, settings)
    
    try:
        payload = await extract_payload(request)
        
        # Apenas salva e responde rápido
        inbox_id = db_repo.create_inbox_entry(
            network=NetworkType.BUYGOODS.value,
            payload=payload
        )
        
        logger.info(f"BuyGoods: Webhook salvo na inbox. ID: {inbox_id}")
        return {"status": "queued", "inbox_id": inbox_id}
        
    except Exception as e:
        logger.exception("Erro ao salvar webhook BuyGoods")
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR)


@app.get("/digistore24/{secret_token}", operation_id="webhook_digistore24_get")
@app.post("/digistore24/{secret_token}", operation_id="webhook_digistore24_post")
async def webhook_digistore24(
    secret_token: str,
    request: Request,
    background_tasks: BackgroundTasks,
    settings: Annotated[Settings, Depends(get_settings)],
    normalizer: Annotated[PayloadNormalizer, Depends(get_payload_normalizer)],
    processor: Annotated[EventProcessor, Depends(get_event_processor)]
) -> dict:
    """
    Endpoint para receber webhooks da DigiStore24.
    Aceita tanto GET quanto POST.
    
    Args:
        secret_token: Token de segurança na URL
        request: Request do FastAPI
        background_tasks: Gerenciador de tarefas em background
        settings: Configurações da aplicação
        normalizer: Serviço de normalização
        processor: Processador de eventos
        
    Returns:
        dict: Resposta de confirmação
        
    Raises:
        HTTPException: Se token for inválido ou erro 500
    """
    # Valida segurança
    verify_secret_token(secret_token, settings)
    
    try:
        # Extrai payload (GET usa query params)
        if request.method == "GET":
            payload = dict(request.query_params)
        else:
            payload = await extract_payload(request)
        
        # Pega order_id para logging
        order_id = payload.get("order_id")
        action = payload.get("transaction_type", "sale")
        
        logger.info(
            f"DigiStore24: Recebido Order {order_id} ({action}) "
            f"via {request.method}"
        )
        
        # Agenda processamento em background
        background_tasks.add_task(
            process_webhook_background,
            normalizer,
            processor,
            NetworkType.DIGISTORE24,
            payload
        )
        
        return {"status": "received", "id": order_id}
        
    except Exception as e:
        logger.exception("Erro 500 no endpoint DigiStore24")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal Server Error"
        )


@app.get("/codenames")
async def get_codenames(
    db_repo: Annotated[DatabaseRepository, Depends(get_database_repository)]
) -> list[dict]:
    """
    Retorna lista de codenames não mapeados (do banco de dados).
    """
    return db_repo.get_missing_codenames()
    
    
@app.post("/retro-buygoods/upload", status_code=status.HTTP_202_ACCEPTED, dependencies=[Depends(verify_upload_key)])
async def upload_spreadsheet(
    file: UploadFile = File(...),
    background_tasks: BackgroundTasks = None,
    settings: Settings = Depends(get_settings),
    db_repo: DatabaseRepository = Depends(get_database_repository) 
):
    """
    Endpoint para upload manual de planilhas (CSV/Excel).
    """
    # 1. Validação básica de extensão
    filename = file.filename.lower()
    if not filename.endswith(('.csv', '.xlsx', '.xls')):
        raise HTTPException(400, "Formato inválido. Use .csv ou .xlsx")

    # 2. Salvar arquivo temporariamente no disco
    temp_dir = Path(settings.temp_dir)
    if not temp_dir.exists():
        # Fallback para diretório atual se /tmp não existir (Windows/Dev)
        try:
            temp_dir.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            logger.error(f"Falha ao criar diretório temporário {temp_dir}: {e}")
            raise HTTPException(500, "Erro de configuração de servidor")
        
    temp_filename = f"import_{uuid4()}_{file.filename}"
    temp_path = temp_dir / temp_filename
    
    try:
        # Lê o conteúdo e escreve
        content = await file.read()
        temp_path.write_bytes(content)

        logger.info(f"Arquivo recebido e salvo em: {temp_path}")
        
        # 3. Agendar processamento em Background
        background_tasks.add_task(run_retro_background, str(temp_path.absolute()), db_repo)
        
        return {
            "status": "queued",
            "message": "Arquivo recebido. O processamento iniciará em breve.",
            "file_id": temp_filename
        }
        
    except Exception as e:
        # Se der erro ao salvar o arquivo, limpa e retorna erro
        if temp_path.exists():
            temp_path.unlink(missing_ok=True)
        logger.error(f"Erro no upload: {e}")
        raise HTTPException(500, "Falha ao salvar arquivo")