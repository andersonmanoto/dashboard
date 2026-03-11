from arq.connections import RedisSettings
from loguru import logger

from app.config import get_settings
from app.repositories.database import DatabaseRepository
from app.services.report_service import ReportService


async def task_generate_pdf_report(ctx, user_email: str, filters: dict):
    """
    Tarefa do ARQ que recebe o pedido da API e processa o PDF.
    """
    logger.info(f"[Report Worker] Pedido de relatório recebido para: {user_email}")

    report_service: ReportService = ctx["report_service"]

    # Chama a função que orquestra tudo
    await report_service.generate_and_send_report(
        user_email=user_email, filters=filters
    )


async def startup(ctx):
    """Inicializa as ligações quando o container do report_worker sobe."""
    logger.info("A inicializar Report Worker ARQ...")
    settings = get_settings()

    db_repo = DatabaseRepository(settings)

    # Injetamos o serviço de relatórios no Contexto (ctx) do ARQ
    ctx["report_service"] = ReportService(settings, db_repo)

    logger.info("Report Worker pronto e a escutar a fila 'reports_queue'!")


async def shutdown(ctx):
    logger.info("A desligar Report Worker...")
    try:
        redis = ctx.get("redis")
        if redis:
            await redis.close()
    except Exception:
        pass


class ReportWorkerSettings:
    """Configurações exclusivas para este Worker de relatórios."""

    settings = get_settings()

    redis_settings = RedisSettings(
        host=settings.redis_host,
        port=settings.redis_port,
    )

    # ⚠️ IMPORTANTE: Diferencia a fila deste worker da fila principal!
    queue_name = "reports_queue"

    on_startup = startup
    on_shutdown = shutdown
    functions = [task_generate_pdf_report]

    # Ajustes finos: Relatórios demoram mais tempo que webhooks
    max_jobs = 5  # Poucos jobs em paralelo para não rebentar a RAM
    job_timeout = 300  # Até 5 minutos para gerar e enviar o PDF
    retry_jobs = True
    max_tries = 2
