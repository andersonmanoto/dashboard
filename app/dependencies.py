from typing import Annotated

from config import Settings, get_settings
from fastapi import Depends
from repositories.database import DatabaseRepository
from services.event_processor import EventProcessor
from services.normalizer import PayloadNormalizer
from services.slack_service import SlackService


def get_database_repository(
    settings: Annotated[Settings, Depends(get_settings)],
) -> DatabaseRepository:
    """
    Fornece uma instância configurada do repositório de banco de dados.

    Utilizada pelo sistema de injeção de dependência do FastAPI para garantir
    que a conexão com o Supabase seja inicializada corretamente usando as
    credenciais do ambiente.

    Args:
        settings (Settings): Configurações globais contendo URL e Key do Supabase.

    Returns:
        DatabaseRepository: Instância pronta para realizar operações no banco.
    """
    return DatabaseRepository(settings)


def get_slack_service(
    settings: Annotated[Settings, Depends(get_settings)],
    db_repo: Annotated[DatabaseRepository, Depends(get_database_repository)],
) -> SlackService:
    """
    Fornece o serviço de notificações do Slack e logs de erro.

    O serviço necessita do repositório de banco de dados para registrar
    falhas críticas (como 'missing codenames') na tabela de erros, além
    de enviar o alerta no canal do Slack configurado.

    Args:
        settings (Settings): Configurações contendo o Token do Bot Slack.
        db_repo (DatabaseRepository): Repositório para persistência de logs de erro.

    Returns:
        SlackService: Instância configurada do cliente Slack.
    """
    return SlackService(settings, db_repo)


def get_payload_normalizer() -> PayloadNormalizer:
    """
    Fornece o normalizador de payloads (Factory/Service).

    Este serviço é responsável por converter os JSONs heterogêneos
    (BuyGoods, DigiStore24) em um objeto padronizado `NormalizedEvent`.
    É um serviço stateless.

    Returns:
        PayloadNormalizer: Instância do normalizador.
    """
    return PayloadNormalizer()


def get_event_processor(
    db_repo: Annotated[DatabaseRepository, Depends(get_database_repository)],
    slack_service: Annotated[SlackService, Depends(get_slack_service)],
) -> EventProcessor:
    """
    Fornece o processador central de eventos (Regras de Negócio).

    O EventProcessor atua como orquestrador: ele recebe o evento normalizado,
    enriquece com dados de afiliados e produtos (via db_repo), calcula
    comissões/impostos e salva a transação final.

    Args:
        db_repo (DatabaseRepository): Acesso ao banco de dados para lookups.
        slack_service (SlackService): Serviço para alertas em caso de falhas.

    Returns:
        EventProcessor: Instância do processador de lógica de negócios.
    """
    return EventProcessor(db_repo, slack_service)
