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
    logger.info(f"Job iniciado | Network={network_str} | Inbox={inbox_id}")

    db_repo: DatabaseRepository = ctx["db_repo"]
    processor: EventProcessor = ctx["processor"]
    normalizer: PayloadNormalizer = ctx["normalizer"]

    try:
        if inbox_id:
            db_repo.update_inbox_status(inbox_id, "processing")

        # Converte string → Enum
        try:
            network = NetworkType(network_str)
        except ValueError:
            raise ValueError(f"Rede desconhecida: {network_str}")

        normalized_event = normalizer.normalize(network, payload)

        success = await processor.process_event(normalized_event)

        if inbox_id:
            status = "processed" if success else "processed_with_ignored"
            msg = None if success else "Ignored/Duplicate/Cancel"
            db_repo.update_inbox_status(inbox_id, status, msg)

        logger.info(f"Job finalizado | Order={normalized_event.order_id}")

    except ValueError as e:
        logger.warning(
            f"Job abortado (Dados Inválidos) | Inbox={inbox_id} | Motivo: {e}"
        )

        if inbox_id:
            db_repo.update_inbox_status(inbox_id, "failed", str(e))

        return

    except Exception as e:
        logger.exception(f"Falha Crítica no Job | Inbox={inbox_id}")

        if inbox_id:
            db_repo.update_inbox_status(inbox_id, "failed", str(e))

        raise


async def startup(ctx):
    logger.info("Inicializando Worker ARQ...")
    settings = get_settings()

    db_repo = DatabaseRepository(settings)
    slack = SlackService(settings, db_repo)

    ctx["db_repo"] = db_repo
    ctx["normalizer"] = PayloadNormalizer()
    ctx["processor"] = EventProcessor(db_repo, slack_service=slack)

    logger.info("Worker pronto e conectado ao Redis.")


async def shutdown(ctx):
    logger.info("Desligando Worker...")

    try:
        redis = ctx.get("redis")
        if redis:
            await redis.close()
    except Exception:
        pass


class WorkerSettings:
    settings = get_settings()

    redis_settings = RedisSettings(
        host=settings.redis_host,
        port=settings.redis_port,
    )

    on_startup = startup
    on_shutdown = shutdown
    functions = [task_process_webhook]
    max_jobs = 20
    job_timeout = 60
    retry_jobs = True
    max_tries = 3
