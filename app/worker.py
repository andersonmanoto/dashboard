import asyncio

import httpx
from arq.connections import RedisSettings
from arq import cron
from loguru import logger
from concurrent.futures import ThreadPoolExecutor

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
        # 👉 A chamada foi corrigida para usar o await diretamente
        await process_single_item(queue_id)
        logger.info(f"Item {queue_id} processado.")
    except Exception:
        logger.exception(f"Falha ao processar item {queue_id} da fila SlickText")
        raise


async def startup(ctx):
    logger.info("Inicializando Worker ARQ...")
    settings = get_settings()

    executor = ThreadPoolExecutor(max_workers=40, thread_name_prefix="worker-io")
    asyncio.get_event_loop().set_default_executor(executor)
    ctx["executor"] = executor  # guarda pra fechar no shutdown

    db_repo = DatabaseRepository(settings)
    slack = SlackService(settings, db_repo)
    db_repo.load_networks_cache()

    ctx["db_repo"] = db_repo
    ctx["normalizer"] = PayloadNormalizer()
    ctx["processor"] = EventProcessor(db_repo, slack_service=slack)

    logger.info("Worker pronto e conectado ao Redis.")


async def shutdown(ctx):
    logger.info("Desligando Worker...")

    executor = ctx.get("executor")
    if executor:
        executor.shutdown(wait=False)

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

    pending_ids = await fetch_pending_ids(limit=15)

    if not pending_ids:
        logger.info("Nada pendente na fila de Compras Aprovadas.")
        return

    for queue_id in pending_ids:
        await redis.enqueue_job("task_sync_slicktext_item", queue_id)

    logger.info(
        f"{len(pending_ids)} item(ns) enfileirado(s) para sincronização com o SlickText."
    )


async def cron_send_zapier_webhooks(ctx):
    """
    Lê a fila zapier_webhook_queue (leads de neworder/abandon já montados
    pelo EventProcessor) e faz o POST de verdade pro Zapier. Mantém o
    caminho do webhook/carrinho rápido -- a chamada HTTP externa acontece
    só aqui, isolada, com retry via reprocessamento no próximo tick.
    """
    db_repo: DatabaseRepository = ctx["db_repo"]
    settings = get_settings()

    if not settings.zapier_webhook_url:
        return

    pending = await asyncio.to_thread(db_repo.fetch_pending_zapier_webhooks, limit=50)
    if not pending:
        return

    async with httpx.AsyncClient(timeout=15.0) as client:
        for row in pending:
            queue_id = row["id"]
            attempts = row.get("attempts", 0)
            try:
                response = await client.post(
                    settings.zapier_webhook_url, json=row["payload"]
                )
                if response.status_code == 200:
                    await asyncio.to_thread(db_repo.mark_zapier_webhook_sent, queue_id)
                    payload = row.get("payload") or {}
                    logger.info(
                        f"Lead enviado ao Zapier | lead_type={payload.get('lead_type')} "
                        f"| order_id={payload.get('order_id') or 'N/A'} "
                        f"| email={payload.get('email')} | queue_id={queue_id}"
                    )
                else:
                    error = f"HTTP {response.status_code}: {response.text[:200]}"
                    logger.warning(f"Zapier recusou webhook {queue_id}: {error}")
                    await asyncio.to_thread(
                        db_repo.mark_zapier_webhook_failed, queue_id, attempts, error
                    )
            except httpx.RequestError as exc:
                logger.warning(f"Erro de rede ao enviar webhook Zapier {queue_id}: {exc}")
                await asyncio.to_thread(
                    db_repo.mark_zapier_webhook_failed, queue_id, attempts, str(exc)
                )

    logger.info(f"{len(pending)} lead(s) processado(s) pro Zapier.")


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
        cron(cron_sync_slicktext_approved, minute={0, 15, 30, 45}, timeout=35),
        cron(
            cron_send_zapier_webhooks,
            minute={0, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55},
            timeout=60,
        ),
    ]

    max_jobs = 20  # quantos jobs (incluindo os de sync) rodam em paralelo
    job_timeout = (
        60  # timeout por job individual — cobre com folga o pior caso (~40s) de um item
    )
    retry_jobs = True
    max_tries = 3
