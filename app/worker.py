import asyncio

from arq.connections import RedisSettings
from arq import cron
from loguru import logger

from app.config import get_settings
from app.models.enums import NetworkType
from app.repositories.database import DatabaseRepository
from app.services.event_processor import EventProcessor
from app.services.normalizer import PayloadNormalizer
from app.services.slack_service import SlackService

from app.scripts.sync_approved_orders import fetch_pending_ids, process_single_item


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


async def task_sync_slicktext_item(ctx, queue_id: str):
    """
    Job individual: processa UM item da fila de sincronização com o SlickText.
    Roda isolado com o timeout padrão do worker (job_timeout), então um item
    lento ou com erro não derruba o lote inteiro nem os outros itens.
    """
    logger.info(f"Sincronizando item da fila SlickText | queue_id={queue_id}")
    try:
        await asyncio.to_thread(process_single_item, queue_id)
        logger.info(f"Item {queue_id} processado.")
    except Exception:
        logger.exception(f"Falha ao processar item {queue_id} da fila SlickText")
        raise


async def startup(ctx):
    logger.info("Inicializando Worker ARQ...")
    settings = get_settings()

    db_repo = DatabaseRepository(settings)
    slack = SlackService(settings, db_repo)
    # Carrega as Networks do banco no cache
    db_repo.load_networks_cache()

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


async def cron_sync_slicktext_approved(ctx):
    """
    Executado automaticamente pelo ARQ cron.
    NÃO processa nada aqui — só busca os IDs pendentes e enfileira um job
    individual por item (task_sync_slicktext_item). Isso mantém o cron
    rápido (sem chamadas HTTP externas) e deixa o ARQ paralelizar o
    processamento real respeitando max_jobs, com timeout por item.
    """
    redis = ctx["redis"]

    pending_ids = await asyncio.to_thread(fetch_pending_ids, 15)
    if not pending_ids:
        logger.info("Nada pendente na fila de Compras Aprovadas.")
        return

    for queue_id in pending_ids:
        await redis.enqueue_job("task_sync_slicktext_item", queue_id)

    logger.info(f"{len(pending_ids)} item(ns) enfileirado(s) para sincronização com o SlickText.")


class WorkerSettings:
    settings = get_settings()

    redis_settings = RedisSettings(
        host=settings.redis_host,
        port=settings.redis_port,
    )

    on_startup = startup
    on_shutdown = shutdown
    functions = [task_process_webhook, task_sync_slicktext_item]

    # AGENDAMENTO DO CRON: só enfileira, timeout curto é suficiente
    cron_jobs = [
        cron(cron_sync_slicktext_approved, minute={0, 15, 30, 45}, timeout=30),
    ]

    max_jobs = 20        # quantos jobs (incluindo os de sync) rodam em paralelo
    job_timeout = 60     # timeout por job individual — cobre com folga o pior caso (~40s) de um item
    retry_jobs = True
    max_tries = 3
