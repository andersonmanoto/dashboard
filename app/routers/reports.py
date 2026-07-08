from fastapi import APIRouter, Request, status, HTTPException, Depends
from pydantic import BaseModel, EmailStr
from typing import Optional
from datetime import date
from loguru import logger

from app.dependencies import verify_reports_key

router = APIRouter(tags=["Reports"])


class DatePeriod(BaseModel):
    start_date: date
    end_date: date


class ReportRequest(BaseModel):
    email: EmailStr
    period: DatePeriod
    product_id: Optional[list[str]] = None
    network_id: Optional[str] = None
    template_name: str = "buygoods_fee_audit.html"


class DropoffReportRequest(BaseModel):
    emails: list[EmailStr]
    days_limit: int = 3


class ChargebackReportRequest(BaseModel):
    emails: list[EmailStr]


class NetRevenueReportRequest(BaseModel):
    emails: list[EmailStr]


@router.post(
    "/reports/buygoods-fees",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(verify_reports_key)],
)
async def request_fee_audit_report(request: Request, payload: ReportRequest):
    """
    Solicita a geração do relatório.
    Envia a lista completa de IDs de uma vez para gerar um único e-mail com múltiplos anexos.
    """
    # Log do Payload para debug
    logger.info(f"Payload recebido para auditoria: {payload.model_dump()}")

    try:
        redis_pool = getattr(request.app.state, "redis_pool", None)

        if not redis_pool:
            logger.error("Redis pool não encontrado no app.state")
            raise HTTPException(status_code=500, detail="Serviço de fila indisponível.")

        job = await redis_pool.enqueue_job(
            "task_generate_pdf_report",
            payload.email,
            payload.model_dump(mode="json"),
            _queue_name="reports_queue",
        )

        logger.info(f"Job criado: {job.job_id} para {payload.email}")

        return {
            "status": "queued",
            "message": "Sua auditoria está sendo processada. Você receberá um único e-mail com os anexos por produto.",
            "job_id": job.job_id,
        }

    except Exception as e:
        logger.exception(f"Erro ao enfileirar: {e}")
        raise HTTPException(status_code=500, detail="Erro interno ao processar pedido.")


@router.post(
    "/reports/affiliates-dropoff",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(verify_reports_key)],
)
async def request_dropoff_warning_report(
    request: Request, payload: DropoffReportRequest
):
    """
    Dispara manualmente a geração do relatório de afiliados sem vendas recentes.
    """
    logger.info(f"Payload recebido para Drop-off Warning: {payload.model_dump()}")

    try:
        redis_pool = getattr(request.app.state, "redis_pool", None)

        if not redis_pool:
            logger.error("Redis pool não encontrado no app.state")
            raise HTTPException(status_code=500, detail="Serviço de fila indisponível.")

        # Enfileira a nova task que criamos no worker
        job = await redis_pool.enqueue_job(
            "task_generate_dropoff_warning",
            payload.emails,
            payload.days_limit,
            _queue_name="reports_queue",
        )

        logger.info(f"Job Drop-off criado: {job.job_id} para {payload.emails}")

        return {
            "status": "queued",
            "message": "Seu relatório de Drop-off está sendo gerado.",
            "job_id": job.job_id,
        }

    except Exception as e:
        logger.exception(f"Erro ao enfileirar dropoff report: {e}")
        raise HTTPException(status_code=500, detail="Erro interno ao processar pedido.")


@router.post(
    "/reports/affiliates-chargebacks",
    summary="Request Chargeback Report (30 Days)",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(verify_reports_key)],
)
async def request_chargeback_report(request: Request, payload: ChargebackReportRequest):
    """
    Coloca na fila a geração do relatório de chargebacks dos últimos 30 dias.
    """
    logger.info(f"Payload recebido para Chargebacks: {payload.model_dump()}")

    try:
        redis_pool = getattr(request.app.state, "redis_pool", None)

        if not redis_pool:
            logger.error("Redis pool não encontrado no app.state")
            raise HTTPException(status_code=500, detail="Serviço de fila indisponível.")

        job = await redis_pool.enqueue_job(
            "task_generate_chargeback_report",
            payload.emails,
            _queue_name="reports_queue",
        )

        logger.info(f"Job Chargebacks criado: {job.job_id} para {payload.emails}")

        return {
            "status": "queued",
            "message": "Seu relatório de chargebacks (30 dias) está sendo gerado.",
            "job_id": job.job_id,
        }

    except Exception as e:
        logger.exception(f"Erro ao enfileirar chargeback report: {e}")
        raise HTTPException(status_code=500, detail="Erro interno ao processar pedido.")


@router.post(
    "/reports/affiliates-netrevenue-loss",
    summary="Request Negative Net Revenue Report (30 Days)",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(verify_reports_key)],
)
async def request_netrevenue_loss_report(
    request: Request, payload: NetRevenueReportRequest
):
    """Enfileira a geração do relatório de Net Revenue Negativo."""
    logger.info(f"Payload recebido para Net Revenue: {payload.model_dump()}")
    try:
        redis_pool = getattr(request.app.state, "redis_pool", None)
        if not redis_pool:
            raise HTTPException(status_code=500, detail="Fila indisponível.")

        job = await redis_pool.enqueue_job(
            "task_generate_netrevenue_report",
            payload.emails,
            _queue_name="reports_queue",
        )
        return {
            "status": "queued",
            "message": "Relatório de Net Revenue na fila.",
            "job_id": job.job_id,
        }
    except Exception as e:
        logger.exception(f"Erro ao enfileirar net revenue report: {e}")
        raise HTTPException(status_code=500, detail="Erro interno ao processar pedido.")
