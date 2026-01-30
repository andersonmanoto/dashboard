from contextlib import asynccontextmanager
from json import JSONDecodeError
from pathlib import Path
from typing import Annotated
from uuid import uuid4

from dependencies import (
    get_database_repository,
    get_event_processor,
    get_payload_normalizer,
)
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
from models.enums import NetworkType
from repositories.database import DatabaseRepository
from services.event_processor import EventProcessor
from services.normalizer import PayloadNormalizer
from services.retro_service import run_retro_background
from services.web_scanner import WebScannerService

from app.config import Settings, get_settings


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Gerencia o ciclo de vida da aplicação (Startup e Shutdown).

    Este gerenciador de contexto é executado automaticamente pelo FastAPI.
    - Antes do `yield`: Executa na inicialização do servidor.
    - Depois do `yield`: Executa no desligamento (graceful shutdown).

    Args:
        app (FastAPI): A instância da aplicação FastAPI.
    """
    logger.info("Webhook Dashboard iniciado")

    yield
    logger.info("Webhook Dashboard encerrado")


# Configuração da aplicação
app = FastAPI(
    title="Tiger Offers - Webhook Dashboard",
    description="API para normalizar e processar webhooks de redes de afiliados",
    version="2.2.0",
    lifespan=lifespan,
)

# ========== HELPERS ==========

API_KEY_NAME = "x-token"
api_key_header = APIKeyHeader(name=API_KEY_NAME, auto_error=False)


async def get_api_key(api_key_header: str = Security(api_key_header)):
    """
    Valida a chave de API (Token) para rotas protegidas (ex: Scanner).

    Esta função verifica se o token enviado no cabeçalho `x-token` da
    requisição corresponde ao token secreto configurado no servidor.

    Args:
        api_key_header (str): O valor do cabeçalho 'x-token' extraído automaticamente.

    Returns:
        str: O token validado, se estiver correto.

    Raises:
        Raises:
        HTTPException (500): Se o servidor não tiver um token secreto
            configurado (falha de segurança interna).
        HTTPException (403): Se o token fornecido for inválido ou
            estiver ausente (Acesso Negado).
    """
    settings = get_settings()

    # 1. Fail-safe: Se a chave não estiver configurada no .env,
    # bloqueia tudo por segurança.
    if not settings.scanner_secret_token:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Erro de segurança: Token do scanner não configurado no servidor.",
        )

    # 2. Validação: Compara o token recebido com o token secreto
    if api_key_header == settings.scanner_secret_token:
        return api_key_header

    # 3. Se chegou aqui, a senha está errada ou o header não foi enviado
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Acesso Negado: Token inválido ou ausente.",
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
            detail="Configuration Error",
        )

    if secret_token != settings.webhook_secret:
        logger.warning(f"Acesso negado. Token inválido: {secret_token}")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Acesso Proibido"
        )


async def extract_payload(request: Request) -> dict:
    """
    Extrai os dados (payload) de uma requisição HTTP.

    Tenta ler o corpo da requisição primeiro como JSON. Se falhar
    (comum em alguns webhooks antigos que mandam form-data),
    tenta ler como dados de formulário.

    Args:
        request (Request): O objeto de requisição bruta do FastAPI.

    Returns:
        dict: Um dicionário Python contendo os dados recebidos.
    """
    try:
        return await request.json()
    except (JSONDecodeError, ValueError):
        form_data = await request.form()
        return dict(form_data)


async def process_webhook_background(
    normalizer: PayloadNormalizer,
    processor: EventProcessor,
    network: NetworkType,
    payload: dict,
) -> None:
    """
    Executa o processamento de um webhook em segundo plano.

    Esta função é chamada pelo `BackgroundTasks` do FastAPI para não travar
    a resposta HTTP imediata. Ela orquestra a normalização (limpeza)
    e o processamento (salvamento/regras de negócio) do evento.

    Args:
        normalizer (PayloadNormalizer): Serviço que padroniza o JSON.
        processor (EventProcessor): Serviço que aplica regras de negócio.
        network (NetworkType): A rede de onde veio o webhook (ex: BuyGoods).
        payload (dict): O dicionário de dados bruto recebido.
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
    x_api_key: str = Header(None), settings: Settings = Depends(get_settings)
) -> None:
    """
    Valida a chave de API específica para uploads de arquivos.

    Diferente dos webhooks, o upload manual exige uma chave
    passada no Header `x-api-key`.

    Args:
        x_api_key (str, optional): O valor do header 'x-api-key'.
        settings (Settings): Configurações da aplicação.

    Raises:
        HTTPException (500): Se a chave de upload não estiver configurada no servidor.
        HTTPException (403): Se a chave fornecida for inválida.
    """
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
    Recebe Webhooks da plataforma BuyGoods.

    Este endpoint implementa o padrão "Inbox":
    1. Valida o token da URL.
    2. Recebe o JSON/Form bruto.
    3. Salva imediatamente no banco de dados (`webhook_inbox`) sem processar.
    4. Responde "OK" para a BuyGoods.

    O processamento é feito depois por um Worker separado.

    Args:
        secret_token (str): Segredo na URL para autenticação.
        request (Request): A requisição HTTP crua.
        settings (Settings): Configurações injetadas.
        db_repo (DatabaseRepository): Conexão com o banco injetada.

    Returns:
        dict: Status de enfileiramento e ID do registro na Inbox.
    """
    verify_secret_token(secret_token, settings)

    try:
        payload = await extract_payload(request)

        # Apenas salva e responde rápido
        inbox_id = db_repo.create_inbox_entry(
            network=NetworkType.BUYGOODS.value, payload=payload
        )

        logger.info(f"BuyGoods: Webhook salvo na inbox. ID: {inbox_id}")
        return {"status": "queued", "inbox_id": inbox_id}

    except Exception as e:
        logger.exception(f"Erro ao salvar webhook BuyGoods: {e}")
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR)


@app.get("/digistore24/{secret_token}", operation_id="webhook_digistore24_get")
@app.post("/digistore24/{secret_token}", operation_id="webhook_digistore24_post")
async def webhook_digistore24(
    secret_token: str,
    request: Request,
    background_tasks: BackgroundTasks,
    settings: Annotated[Settings, Depends(get_settings)],
    normalizer: Annotated[PayloadNormalizer, Depends(get_payload_normalizer)],
    processor: Annotated[EventProcessor, Depends(get_event_processor)],
) -> dict:
    """
    Recebe Webhooks da plataforma DigiStore24.

    Diferente da BuyGoods, a DigiStore24 pode enviar dados via GET ou POST.
    Este endpoint aceita ambos.
    O processamento aqui é feito via `BackgroundTasks` do FastAPI
    (processamento assíncrono leve), em vez de salvar numa Inbox.

    Args:
        secret_token (str): Segredo na URL.
        request (Request): A requisição HTTP.
        background_tasks (BackgroundTasks): Ferramenta para agendar tarefas pós-resposta.
        settings (Settings): Configurações.
        normalizer (PayloadNormalizer): Serviço de normalização.
        processor (EventProcessor): Serviço de processamento.

    Returns:
        dict: Status de recebimento e ID do pedido (se disponível).
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
            f"DigiStore24: Recebido Order {order_id} ({action}) via {request.method}"
        )

        # Agenda processamento em background
        background_tasks.add_task(
            process_webhook_background,
            normalizer,
            processor,
            NetworkType.DIGISTORE24,
            payload,
        )

        return {"status": "received", "id": order_id}

    except Exception as e:
        logger.exception(f"Erro: {e}")
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
    Recebe upload manual de planilhas (CSV/Excel) para importação retroativa.

    Utilizado para processar vendas antigas ou recuperar dados perdidos.
    O arquivo é salvo temporariamente no disco e uma tarefa em background
    é iniciada para ler e importar linha a linha.

    Args:
        background_tasks (BackgroundTasks): Para agendar o processamento.
        file (UploadFile): O arquivo enviado pelo usuário.
        settings (Settings): Configurações.
        db_repo (DatabaseRepository): Repositório do banco.

    Returns:
        dict: Confirmação de agendamento e ID temporário do arquivo.
    """
    # 1. Validação básica de extensão
    filename = file.filename.lower()
    if not filename.endswith((".csv", ".xlsx", ".xls")):
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
        background_tasks.add_task(
            run_retro_background, str(temp_path.absolute()), db_repo
        )

        return {
            "status": "queued",
            "message": "Arquivo recebido. O processamento iniciará em breve.",
            "file_id": temp_filename,
        }

    except Exception as e:
        # Se der erro ao salvar o arquivo, limpa e retorna erro
        if temp_path.exists():
            temp_path.unlink(missing_ok=True)
        logger.error(f"Erro no upload: {e}")
        raise HTTPException(500, "Falha ao salvar arquivo")


@app.get("/scanner/offers/lookup")
async def find_offer_in_funnel(
    codename: str = Query(..., description="Ex: vis3"),
    domain: str = Query(..., description="Ex: visiumpro.com)"),
    debug: bool = Query(False, description="Logs no terminal"),
    token: str = Depends(get_api_key),
):
    """
    Ferramenta de Diagnóstico: Localizador de Ofertas no Site.

    Varre as páginas de um domínio (baseado no mapa de estrutura)
    procurando onde um determinado "Codename" de produto está sendo vendido.
    Útil para descobrir qual URL (Página de Venda, Upsell 1, etc.)
    corresponde a um código que chegou no webhook.

    Args:
        codename (str): O código do produto a ser buscado (ex: 'vis3').
        domain (str): O domínio onde buscar (ex: 'visiumpro.com').
        debug (bool): Se True, imprime logs detalhados de cada requisição no terminal.
        token (str): Token de segurança (x-token).

    Returns:
        dict: Lista de URLs encontradas e em qual etapa do funil elas estão.
    """
    service = WebScannerService()

    # Passando o debug para o serviço
    results = await service.run_scan(
        codename=codename, domain_filter=domain, debug=debug
    )

    if not results:
        return {
            "message": "Links não encontrado.",
            "codename": codename,
            "domain": domain,
            "debug_mode": debug,
            "data": [],
        }

    return {
        "message": "Scan finalizado.",
        "total_found": len(results),
        "debug_mode": debug,
        "data": results,
    }
