import asyncio
from datetime import datetime, timedelta
from functools import partial
from zoneinfo import ZoneInfo

import httpx
from arq.connections import RedisSettings
from arq import cron, func
from loguru import logger
from concurrent.futures import ThreadPoolExecutor

from app.config import get_settings
from app.models.enums import PAGAMERICAN_ABANDON_EVENT, NetworkType
from app.repositories.database import DatabaseRepository
from app.services.event_processor import EventProcessor
from app.services.aweber_service import AWeberAPI, AWeberAPIError
from app.services.normalizer import PayloadNormalizer
from app.services.redtrack_creative_service import RedTrackCreativeSync
from app.services.redtrack_service import RedTrackAPI, RedTrackAPIError
from app.services.slack_service import SlackService
from app.services.slicktext_service import (
    AbandonOutcome,
    process_slicktext_sync_task,
)

from app.scripts.sync_approved_orders import fetch_pending_ids, process_single_item
from app.services import slicktext_input_service


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

        # Checkout abandonado da PagAmerican não é uma transação financeira
        # (sem action_type/order_id nossos) -- desvia do normalize()/
        # process_event() antes de chegar lá, igual ao fluxo de abandono da
        # BuyGoods (que tem rota própria em vez de passar pelo normalizer).
        if (
            network == NetworkType.PAGAMERICAN
            and payload.get("_pa_event") == PAGAMERICAN_ABANDON_EVENT
        ):
            # SMS (SlickText): o processor enfileira o carrinho na
            # slicktext_sync_queue; cron_send_slicktext_abandons envia.
            success = await processor.process_pagamerican_abandon_cart(payload)
            log_id = payload.get("abandonedSaleUcode", "")
        else:
            normalized_event = normalizer.normalize(network, payload)
            success = await processor.process_event(normalized_event)
            log_id = normalized_event.order_id

        if inbox_id:
            status = "processed" if success else "processed_with_ignored"
            msg = None if success else "Ignored/Duplicate/Cancel"
            db_repo.update_inbox_status(inbox_id, status, msg)

        logger.info(f"Job finalizado | Order={log_id}")

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
    ctx["processor"] = EventProcessor(
        db_repo,
        slack_service=slack,
        redtrack_send_from=settings.redtrack_conversions_send_from,
        redtrack_excluded_product_ids=settings.redtrack_conversions_excluded_product_id_set,
        redtrack_networks=settings.redtrack_conversions_network_set,
        aweber_enabled=settings.aweber_enabled,
        slicktext_abandon_enabled=True,
    )

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


async def cron_send_redtrack_conversions(ctx):
    """
    Lê a fila redtrack_conversion_queue (Purchases já montados pelo
    EventProcessor) e faz o POST /conversions no RedTrack. Mesmo esquema do
    Zapier: o caminho do webhook só insere na fila, a chamada externa
    acontece só aqui, com retry no próximo tick (até 5 tentativas -> failed).
    """
    db_repo: DatabaseRepository = ctx["db_repo"]
    settings = get_settings()

    if not settings.redtrack_conversions_send_from:
        return

    pending = await asyncio.to_thread(
        db_repo.fetch_pending_redtrack_conversions, limit=50
    )
    if not pending:
        return

    try:
        redtrack = RedTrackAPI(settings)
    except RedTrackAPIError as exc:
        logger.error(f"Envio de conversões RedTrack desligado: {exc}")
        return

    async with httpx.AsyncClient(timeout=15.0) as client:
        for row in pending:
            queue_id = row["id"]
            attempts = row.get("attempts", 0)
            conversion = row["payload"]
            try:
                response = await redtrack.upload_conversion(client, conversion)
                if response.status_code in (200, 201):
                    await asyncio.to_thread(
                        db_repo.mark_redtrack_conversion_sent, queue_id, response.text
                    )
                    logger.info(
                        f"Conversão enviada ao RedTrack | order_id={row.get('bg_order_id')} "
                        f"| clickid={conversion.get('clickid')} "
                        f"| payout={conversion.get('payout')} | queue_id={queue_id}"
                    )
                else:
                    error = f"HTTP {response.status_code}: {response.text[:200]}"
                    logger.warning(f"RedTrack recusou conversão {queue_id}: {error}")
                    await asyncio.to_thread(
                        db_repo.mark_redtrack_conversion_failed, queue_id, attempts, error
                    )
            except httpx.RequestError as exc:
                logger.warning(
                    f"Erro de rede ao enviar conversão RedTrack {queue_id}: {exc}"
                )
                await asyncio.to_thread(
                    db_repo.mark_redtrack_conversion_failed, queue_id, attempts, str(exc)
                )

    logger.info(f"{len(pending)} conversão(ões) processada(s) pro RedTrack.")


async def cron_sync_redtrack_creatives(ctx):
    """
    Espelha o RedTrack em redtrack_events + redtrack_cost_daily e recalcula a
    snapshot_daily_redtrack (ranking de criativos). Hoje e ontem, no fuso da
    conta do RedTrack: o custo do Facebook de ontem ainda pode ser ajustado de
    madrugada. Dias mais antigos: app/scripts/sync_redtrack_creatives.py.
    """
    settings = get_settings()
    try:
        redtrack = RedTrackAPI(settings)
    except RedTrackAPIError as exc:
        logger.error(f"Coleta de criativos RedTrack desligada: {exc}")
        return

    today = datetime.now(ZoneInfo("America/Sao_Paulo")).date()
    try:
        await RedTrackCreativeSync(redtrack, ctx["db_repo"]).sync(
            [today - timedelta(days=1), today]
        )
    except Exception:
        logger.exception("Falha na coleta de criativos do RedTrack")


async def cron_send_aweber_leads(ctx):
    """
    Lê a fila aweber_lead_queue (leads de venda/abandono da PagAmerican já
    montados pelo EventProcessor) e faz o POST /subscribers no AWeber.
    Mesmo esquema do Zapier, com retry no próximo tick (até 5 -> failed).
    Problema de OAuth (sem token, refresh recusado) não conta tentativa: os
    leads ficam pending até o token ser corrigido.
    """
    db_repo: DatabaseRepository = ctx["db_repo"]
    settings = get_settings()

    if not settings.aweber_enabled:
        return

    pending = await asyncio.to_thread(db_repo.fetch_pending_aweber_leads, limit=50)
    if not pending:
        return

    aweber = AWeberAPI(settings, db_repo)

    async with httpx.AsyncClient(timeout=15.0) as client:
        try:
            access_token = await aweber.get_access_token(client)
        except (AWeberAPIError, httpx.RequestError) as exc:
            logger.error(f"AWeber: sem access token, envio adiado: {exc}")
            return

        for row in pending:
            queue_id = row["id"]
            attempts = row.get("attempts", 0)
            try:
                response, access_token = await aweber.add_subscriber(
                    client, row["list_id"], row["payload"], access_token
                )
                if response.status_code in (200, 201):
                    await asyncio.to_thread(db_repo.mark_aweber_lead_sent, queue_id)
                    logger.info(
                        f"Lead enviado ao AWeber | lead_type={row.get('lead_type')} "
                        f"| list_id={row.get('list_id')} "
                        f"| email={row.get('email')} | queue_id={queue_id}"
                    )
                else:
                    error = f"HTTP {response.status_code}: {response.text[:200]}"
                    logger.warning(f"AWeber recusou lead {queue_id}: {error}")
                    await asyncio.to_thread(
                        db_repo.mark_aweber_lead_failed, queue_id, attempts, error
                    )
            except AWeberAPIError as exc:
                logger.error(f"AWeber: falha no refresh do token, envio adiado: {exc}")
                return
            except httpx.RequestError as exc:
                logger.warning(f"Erro de rede ao enviar lead AWeber {queue_id}: {exc}")
                await asyncio.to_thread(
                    db_repo.mark_aweber_lead_failed, queue_id, attempts, str(exc)
                )

    logger.info(f"{len(pending)} lead(s) processado(s) pro AWeber.")


async def cron_send_slicktext_abandons(ctx):
    """
    Lê os abandonos da slicktext_sync_queue (carrinhos das 3 redes, já
    salvos em abandoned_carts) e envia o SMS pro SlickText. Grava o
    resultado de cada um: synced, skipped (descarte de propósito, com motivo)
    ou retry (erro temporário -> pending de novo, até 3 -> failed).
    Sequencial: cada item faz até ~3 chamadas externas (AbstractAPI +
    SlickText por conta), e o lote é pequeno pra caber no timeout do cron.
    """
    db_repo: DatabaseRepository = ctx["db_repo"]
    settings = get_settings()

    pending = await asyncio.to_thread(
        db_repo.fetch_pending_slicktext_abandons, limit=15
    )
    if not pending:
        return

    for row in pending:
        queue_id = row["id"]
        attempts = row.get("attempts", 0)
        network = row["networks"]["name"]
        list_id = row.get("list_id")
        assign_targets = (
            None
            if list_id
            else partial(
                asyncio.to_thread, db_repo.assign_slicktext_abandon_targets, row
            )
        )
        try:
            outcome = await process_slicktext_sync_task(
                row["payload"],
                settings,
                db_repo,
                platform=network,
                list_id=list_id,
                assign_targets=assign_targets,
            )
        except Exception as exc:
            logger.exception(f"Erro ao enviar abandono SlickText {queue_id}")
            outcome = AbandonOutcome.retry(f"{type(exc).__name__}: {exc}")
        await asyncio.to_thread(
            db_repo.mark_slicktext_abandon,
            queue_id,
            outcome.status,
            attempts,
            outcome.reason,
        )
        logger.info(
            f"Abandono SlickText {outcome.status} | network={network} "
            f"| queue_id={queue_id} | {outcome.reason or ''}"
        )

    logger.info(f"{len(pending)} abandono(s) processado(s) pro SlickText.")


async def task_slicktext_input_start(ctx, input_id: str):
    """
    Input de leads (POST /slicktext/inputs): resolve conta/lista e chama
    sms_input_leads. O envio é do cron_send_slicktext_inputs.
    """
    logger.info(f"Input de leads SlickText | input_id={input_id}")
    await slicktext_input_service.start_input(input_id)


async def cron_send_slicktext_inputs(ctx):
    """
    Envia a fila slicktext_input_queue em lotes reservados (lease), terminando
    em ~40s por rodada. Um input grande leva várias rodadas. O timeout do cron
    (120s) é folga pra um lote lento; se uma rodada invadir o minuto seguinte,
    o lease impede as duas de pegarem a mesma linha.
    """
    try:
        await slicktext_input_service.send_pending_rows(time_budget_seconds=40)
    except Exception:
        logger.exception("Falha no envio da fila de input de leads SlickText")


async def cron_sweep_slicktext_inputs(ctx):
    """Rede de segurança: input pending antigo volta pro job; processing ganha refresh."""
    redis = ctx["redis"]
    stale = await slicktext_input_service.sweep_inputs()
    for input_id in stale:
        logger.warning(f"Input SlickText {input_id} parado em pending; reenfileirando.")
        await redis.enqueue_job("task_slicktext_input_start", input_id)


class WorkerSettings:
    settings = get_settings()

    redis_settings = RedisSettings(
        host=settings.redis_host,
        port=settings.redis_port,
        password=settings.redis_password,
    )

    on_startup = startup
    on_shutdown = shutdown
    functions = [
        task_process_webhook,
        task_sync_slicktext_item,
        # sms_input_leads pode demorar com período longo
        func(task_slicktext_input_start, timeout=300, max_tries=1),
    ]

    # AGENDAMENTO DO CRON: só enfileira, timeout curto é suficiente
    cron_jobs = [
        cron(cron_sync_slicktext_approved, minute={0, 15, 30, 45}, timeout=35),
        cron(
            cron_send_zapier_webhooks,
            minute={0, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55},
            timeout=60,
        ),
        cron(
            cron_send_redtrack_conversions,
            minute={2, 7, 12, 17, 22, 27, 32, 37, 42, 47, 52, 57},
            timeout=60,
        ),
        cron(
            cron_sync_redtrack_creatives,
            minute={1, 16, 31, 46},
            timeout=240,
        ),
        cron(
            cron_send_slicktext_abandons,
            minute={3, 8, 13, 18, 23, 28, 33, 38, 43, 48, 53, 58},
            timeout=240,
        ),
        cron(cron_send_slicktext_inputs, minute=set(range(60)), timeout=120),
        cron(
            cron_sweep_slicktext_inputs,
            minute={6, 16, 26, 36, 46, 56},
            timeout=60,
        ),
        cron(
            cron_send_aweber_leads,
            minute={4, 9, 14, 19, 24, 29, 34, 39, 44, 49, 54, 59},
            timeout=60,
        ),
    ]

    max_jobs = 20  # quantos jobs (incluindo os de sync) rodam em paralelo
    job_timeout = (
        60  # timeout por job individual — cobre com folga o pior caso (~40s) de um item
    )
    retry_jobs = True
    max_tries = 3
