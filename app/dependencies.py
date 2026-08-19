from typing import Annotated
from json import JSONDecodeError

from fastapi import Depends, HTTPException, status, Header, Security, Request
from fastapi.security import APIKeyHeader
from loguru import logger

from app.config import Settings, get_settings
from app.repositories.database import DatabaseRepository
from app.services.event_processor import EventProcessor
from app.services.normalizer import PayloadNormalizer
from app.services.slack_service import SlackService

# --- DEPENDÊNCIAS DE SERVIÇOS ---


def get_database_repository(request: Request) -> DatabaseRepository:
    return request.app.state.db


def get_slack_service(
    settings: Annotated[Settings, Depends(get_settings)],
    db_repo: Annotated[DatabaseRepository, Depends(get_database_repository)],
) -> SlackService:
    return SlackService(settings, db_repo)


def get_payload_normalizer() -> PayloadNormalizer:
    return PayloadNormalizer()


def get_event_processor(
    db_repo: Annotated[DatabaseRepository, Depends(get_database_repository)],
    slack_service: Annotated[SlackService, Depends(get_slack_service)],
) -> EventProcessor:
    return EventProcessor(db_repo, slack_service)


# --- HELPER: EXTRAÇÃO DE PAYLOAD ---


async def extract_payload(request: Request) -> dict:
    """Extrai JSON ou Form Data da requisição de forma unificada."""
    try:
        return await request.json()
    except (JSONDecodeError, ValueError):
        form_data = await request.form()
        return dict(form_data)


# --- SEGURANÇA E AUTH ---

API_KEY_NAME = "x-token"
api_key_header = APIKeyHeader(name=API_KEY_NAME, auto_error=False)


def verify_secret_token(
    secret_token: str, settings: Annotated[Settings, Depends(get_settings)]
) -> None:
    """Valida token da URL do Webhook."""
    if not settings.webhook_secret:
        logger.critical("WEBHOOK_SECRET não configurado!")
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "Config Error")

    if secret_token != settings.webhook_secret:
        logger.warning(f"Acesso negado. Token inválido: {secret_token}")
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Acesso Proibido")


async def verify_upload_key(
    x_api_key: str = Header(None), settings: Settings = Depends(get_settings)
) -> None:
    """Valida chave de API para Uploads."""
    if not settings.upload_api_key:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "Config Error")

    if x_api_key != settings.upload_api_key:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Credenciais inválidas")


async def get_api_key_scanner(
    api_key_header: str = Security(api_key_header),
    settings: Settings = Depends(get_settings),
):
    """Valida token para o Scanner."""
    if not settings.scanner_secret_token:
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR, "Scanner Token Error"
        )

    if api_key_header == settings.scanner_secret_token:
        return api_key_header

    raise HTTPException(status.HTTP_403_FORBIDDEN, "Acesso Negado")


async def verify_funnel_sync_key(
    x_api_key: str = Header(None), settings: Settings = Depends(get_settings)
) -> None:
    """Valida chave de API para o Sync de Funil (troca de links de checkout)."""
    if not settings.funnel_sync_api_key:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "Config Error")

    if x_api_key != settings.funnel_sync_api_key:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Credenciais inválidas")


async def verify_redtrack_offers_key(
    x_api_key: str = Header(None), settings: Settings = Depends(get_settings)
) -> None:
    """Valida chave de API para criação de offers no RedTrack."""
    if not settings.redtrack_offers_api_key:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "Config Error")

    if x_api_key != settings.redtrack_offers_api_key:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Credenciais inválidas")


async def verify_widget_sync_key(
    x_api_key: str = Header(None), settings: Settings = Depends(get_settings)
) -> None:
    """Valida chave de API para o Sync de Widgets (puxar script do CDN pro Supabase)."""
    if not settings.widget_sync_api_key:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "Config Error")

    if x_api_key != settings.widget_sync_api_key:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Credenciais inválidas")


async def verify_reports_key(
    x_api_key: Annotated[str, Header(alias="X-API-KEY")] = None,
    settings: Settings = Depends(get_settings),
) -> None:
    """Valida a chave de API para geração de relatórios."""
    if not settings.reports_api_key:
        logger.critical("REPORTS_API_KEY não configurado nas definições!")
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "Config Error")

    if x_api_key != settings.reports_api_key:
        logger.warning(
            f"Tentativa de acesso não autorizada ao endpoint de relatórios. Key: {x_api_key}"
        )
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "Credenciais de relatórios inválidas"
        )
