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
    Header,
    Query,
    Security
)
from loguru import logger
from contextlib import asynccontextmanager

from app.config import Settings, get_settings
from dependencies import (
    get_payload_normalizer,
    get_event_processor,
    get_database_repository
)
from fastapi.security import APIKeyHeader
from models.enums import NetworkType
from services.normalizer import PayloadNormalizer
from services.event_processor import EventProcessor
from services.retro_service import SpreadsheetRetro, run_retro_background
from repositories.database import DatabaseRepository

from services.web_scanner import WebScannerService

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

API_KEY_NAME = "x-token"
api_key_header = APIKeyHeader(name=API_KEY_NAME, auto_error=False)

async def get_api_key(api_key_header: str = Security(api_key_header)):
    """
    Valida se o token passado no Header bate com o configurado no settings.
    """
    settings = get_settings()
    
    # 1. Fail-safe: Se a chave não estiver configurada no .env, bloqueia tudo por segurança.
    if not settings.scanner_secret_token:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Erro de segurança: Token do scanner não configurado no servidor."
        )

    # 2. Validação: Compara o token recebido com o token secreto
    if api_key_header == settings.scanner_secret_token:
        return api_key_header
    
    # 3. Se chegou aqui, a senha está errada ou o header não foi enviado
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Acesso Negado: Token inválido ou ausente."
    )


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
    

@app.get("/scanner/offers/lookup")
async def find_offer_in_funnel(
    codename: str = Query(..., description="O codename do produto (ex: vis3)"),
    domain: str = Query(..., description="O domínio do funil para varrer (ex: visiumpro.com)"),
    token: str = Depends(get_api_key)
):
    """
    Varre os diretórios do `domain` e busca o funnel_stage, funnel_number e buy_url correspondentes ao `codename`
    """
    service = WebScannerService()
    
    # Executa o scan
    results = await service.run_scan(codename=codename, domain_filter=domain)
    
    if not results:
        return {
            "message": "Nenhum link encontrado para este codename neste domínio.",
            "codename": codename,
            "domain": domain,
            "data": []
        }

    return {
        "message": "Scan finalizado.",
        "total_found": len(results),
        "data": results 
    }