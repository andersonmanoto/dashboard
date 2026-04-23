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
