from arq.connections import RedisSettings
from loguru import logger

from app.config import get_settings
from app.models.enums import NetworkType
from app.repositories.database import DatabaseRepository
from app.services.event_processor import EventProcessor
from app.services.normalizer import PayloadNormalizer
from app.services.slack_service import SlackService


async def task_process_webhook(
    ctx, network_str: str, payload: dict, inbox_id: str | None = None
):
    """
    Job executado pelo Redis quando chega um webhook.
    """
    logger.info(f"Job Iniciado | Network: {network_str} | Inbox ID: {inbox_id}")

    # Recupera serviços do contexto (injetados no startup)
    db_repo: DatabaseRepository = ctx["db_repo"]
    processor: EventProcessor = ctx["processor"]
    normalizer: PayloadNormalizer = ctx["normalizer"]

    try:
        # 1. Marca como processando no banco (se tiver ID)
        if inbox_id:
            db_repo.update_inbox_status(inbox_id, "processing")

        # 2. Converte string para Enum
        if network_str == NetworkType.BUYGOODS.value:
            network = NetworkType.BUYGOODS
        elif network_str == NetworkType.DIGISTORE24.value:
            network = NetworkType.DIGISTORE24
        else:
            raise ValueError(f"Rede desconhecida: {network_str}")

        # 3. Normaliza
        normalized_event = normalizer.normalize(network, payload)

        # 4. Processa (Regras de Negócio)
        success = await processor.process_event(normalized_event)

        # 5. Atualiza Status Final
        if inbox_id:
            status = "processed" if success else "processed_with_ignored"
            msg = None if success else "Ignored/Duplicate/Cancel"
            db_repo.update_inbox_status(inbox_id, status, msg)

        logger.info(f"Job Finalizado | Order: {normalized_event.order_id}")

    except Exception as e:
        logger.error(f"Falha no Job {inbox_id}: {e}")
        if inbox_id:
            db_repo.update_inbox_status(inbox_id, "failed", str(e))

        # Raise para o ARQ tentar novamente (Retry)
        # raise e


async def startup(ctx):
    """Executado uma vez quando o container worker inicia."""
    logger.info("Inicializando Worker ARQ...")
    settings = get_settings()

    # Inicializa conexões e serviços
    db_repo = DatabaseRepository(settings)
    slack = SlackService(settings, db_repo)

    # Injeta no contexto global do worker
    ctx["db_repo"] = db_repo
    ctx["normalizer"] = PayloadNormalizer()
    ctx["processor"] = EventProcessor(db_repo, slack_service=slack)

    logger.info("Worker pronto e conectado ao Redis.")


async def shutdown(ctx):
    logger.info("Desligando Worker...")


class WorkerSettings:
    """Configuração lida pelo comando 'arq app.worker.WorkerSettings'"""

    settings = get_settings()

    redis_settings = RedisSettings(host=settings.redis_host, port=settings.redis_port)

    on_startup = startup
    on_shutdown = shutdown

    # Lista de funções habilitadas
    functions = [task_process_webhook]

    # Concorrência: Processa até 20 webhooks simultâneos
    max_jobs = 20

    # Timeout: Mata o job se demorar mais de 60s
    job_timeout = 60
