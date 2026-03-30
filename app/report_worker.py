from arq.connections import RedisSettings
from arq import cron
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


async def cron_dropoff_warning(ctx):
    """Executado automaticamente pelo ARQ cron"""
    logger.info("Executando Cron Diário: Affiliate Drop-off Warning")

    settings = get_settings()

    target_emails =[
        email.strip() 
        for email in settings.report_target_emails.split(",") 
        if email.strip()
    ]

    report_service = ctx["report_service"]

    await report_service.generate_and_send_dropoff_warning(
        target_emails=target_emails, days_limit=3
    )


async def task_generate_dropoff_warning(ctx, target_emails: list[str], days_limit: int):
    """Tarefa sob demanda disparada pela API."""
    logger.info(
        f"[Report Worker] Pedido manual de Drop-off Warning para: {target_emails}"
    )

    report_service = ctx["report_service"]

    await report_service.generate_and_send_dropoff_warning(
        target_emails=target_emails, days_limit=days_limit
    )


class ReportWorkerSettings:
    """Configurações exclusivas para este Worker de relatórios."""

    settings = get_settings()

    redis_settings = RedisSettings(
        host=settings.redis_host,
        port=settings.redis_port,
    )

    queue_name = "reports_queue"

    on_startup = startup
    on_shutdown = shutdown
    functions = [task_generate_pdf_report, task_generate_dropoff_warning]
    # AGENDAMENTO DO CRON
    cron_jobs = [
        # Dispara todos os dias às 08:00 AM - (11:00 AM horário da vps/arq)
        cron(cron_dropoff_warning, hour=11, minute=00)
    ]

    max_jobs = 5
    job_timeout = 300
    retry_jobs = True
    max_tries = 2
