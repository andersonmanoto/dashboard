from fastapi import APIRouter, Request, status, HTTPException, Depends
from pydantic import BaseModel, EmailStr, Field, model_validator
from typing import Optional
from datetime import date
from uuid import UUID
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


class AffiliateXlsxReportRequest(BaseModel):
    emails: list[EmailStr]
    period: DatePeriod
    network_id: Optional[list[UUID]] = None
    product_id: Optional[list[UUID]] = None
    requesting_user_id: Optional[UUID] = None


class ValidatedAssetsXlsxReportRequest(BaseModel):
    emails: list[EmailStr] = Field(min_length=1)
    period: DatePeriod
    # vazio/ausente = todos os produtos com conversão no período (abas em ordem alfabética)
    product_id: Optional[list[UUID]] = None
    network_id: Optional[list[UUID]] = None

    @model_validator(mode="after")
    def check_period(self):
        if self.period.start_date > self.period.end_date:
            raise ValueError("period.start_date não pode ser depois de period.end_date")
        return self


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


@router.post(
    "/reports/affiliates-xlsx",
    summary="Request Affiliate Report (.xlsx)",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(verify_reports_key)],
)
async def request_affiliate_xlsx_report(
    request: Request, payload: AffiliateXlsxReportRequest
):
    """
    Enfileira a geração do Relatório de Afiliados em .xlsx (Visão Geral,
    Detalhado por Funil, Detalhado por Pote, Resumo/KPIs e Metadados) para o
    período informado. O arquivo é enviado por e-mail quando pronto.
    """
    logger.info(f"Payload recebido para Relatório de Afiliados (.xlsx): {payload.model_dump()}")
    try:
        redis_pool = getattr(request.app.state, "redis_pool", None)
        if not redis_pool:
            raise HTTPException(status_code=500, detail="Fila indisponível.")

        job = await redis_pool.enqueue_job(
            "task_generate_affiliate_xlsx_report",
            payload.emails,
            payload.model_dump(mode="json"),
            _queue_name="reports_queue",
        )
        return {
            "status": "queued",
            "message": "Relatório de Afiliados (.xlsx) na fila.",
            "job_id": job.job_id,
        }
    except Exception as e:
        logger.exception(f"Erro ao enfileirar relatório de afiliados (.xlsx): {e}")
        raise HTTPException(status_code=500, detail="Erro interno ao processar pedido.")


@router.post(
    "/reports/validated-assets-xlsx",
    summary="Request Validated Assets Report (.xlsx)",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(verify_reports_key)],
)
async def request_validated_assets_xlsx_report(
    request: Request, payload: ValidatedAssetsXlsxReportRequest
):
    """
    Enfileira o relatório Ativos Validados em .xlsx: ofertas (VSLs) com
    conversão e os criativos que venderam nelas, no período informado. Uma aba
    por produto de `product_id`; `network_id` vazio = todas as plataformas.
    O arquivo é enviado para todos os e-mails de `emails` quando pronto.
    """
    logger.info(f"Payload recebido para Ativos Validados (.xlsx): {payload.model_dump()}")
    try:
        redis_pool = getattr(request.app.state, "redis_pool", None)
        if not redis_pool:
            raise HTTPException(status_code=500, detail="Fila indisponível.")

        job = await redis_pool.enqueue_job(
            "task_generate_validated_assets_xlsx_report",
            payload.emails,
            payload.model_dump(mode="json"),
            _queue_name="reports_queue",
        )
        return {
            "status": "queued",
            "message": "Relatório de Ativos Validados (.xlsx) na fila.",
            "job_id": job.job_id,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Erro ao enfileirar relatório de Ativos Validados (.xlsx): {e}")
        raise HTTPException(status_code=500, detail="Erro interno ao processar pedido.")
