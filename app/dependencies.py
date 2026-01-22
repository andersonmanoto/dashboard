"""
Funções de dependency injection para FastAPI.
Gerencia instâncias de serviços e repositórios.
"""
from typing import Annotated
from fastapi import Depends

from config import Settings, get_settings
from repositories.database import DatabaseRepository
from services.normalizer import PayloadNormalizer
from services.slack_service import SlackService
from services.event_processor import EventProcessor

def get_database_repository(
    settings: Annotated[Settings, Depends(get_settings)]
) -> DatabaseRepository:
    """
    Retorna instância singleton do repositório de banco de dados.
    
    Args:
        settings: Configurações da aplicação
        
    Returns:
        DatabaseRepository: Instância do repositório
    """
    return DatabaseRepository(settings)


def get_slack_service(
    settings: Annotated[Settings, Depends(get_settings)]
) -> SlackService:
    """
    Retorna instância singleton do serviço Slack.
    
    Args:
        settings: Configurações da aplicação
        
    Returns:
        SlackService: Instância do serviço
    """
    return SlackService(settings)


def get_payload_normalizer() -> PayloadNormalizer:
    """
    Retorna instância do normalizador de payloads.
    
    Returns:
        PayloadNormalizer: Instância do normalizador
    """
    return PayloadNormalizer()


def get_event_processor(
    db_repo: Annotated[DatabaseRepository, Depends(get_database_repository)],
    slack_service: Annotated[SlackService, Depends(get_slack_service)]
) -> EventProcessor:
    """
    Retorna instância do processador de eventos.
    
    Args:
        db_repo: Repositório de banco de dados
        slack_service: Serviço de notificações Slack
        
    Returns:
        EventProcessor: Instância do processador
    """
    return EventProcessor(db_repo, slack_service)