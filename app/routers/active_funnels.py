from typing import Annotated
from uuid import UUID

from arq.jobs import Job, JobStatus
from fastapi import APIRouter, Depends, HTTPException, Request, status
from loguru import logger
from pydantic import BaseModel, field_validator

from app.dependencies import get_database_repository, verify_funnel_sync_key
from app.repositories.database import DatabaseRepository
from app.services.funnel_sync_service import FunnelSyncError

router = APIRouter(tags=["Active Funnels"])

FUNNEL_SYNC_QUEUE = "reports_queue"


class FunnelSyncRequest(BaseModel):
    product: UUID
    network: UUID
    domain: str
    user_id: UUID
    active_funnel: int
    product_network_active_funnels_id: UUID
    previous_funnel: int | None = None

    @field_validator("previous_funnel", mode="before")
    @classmethod
    def _blank_previous_funnel_as_none(cls, v):
        """Primeira ativação (sem funil anterior) — o frontend manda "" em vez de omitir/null."""
        if v == "":
            return None
        return v


@router.post(
    "/active-funnels/sync",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(verify_funnel_sync_key)],
)
async def sync_active_funnel(
    request: Request,
    payload: FunnelSyncRequest,
    db_repo: Annotated[DatabaseRepository, Depends(get_database_repository)],
):
    """
    Enfileira a sincronização dos links de checkout (switch-link) das páginas
    HTML publicadas de um produto com o que está cadastrado em `checkouts`
    no Supabase.

    Roda em background (worker ARQ) porque a varredura via SFTP de um
    domínio inteiro pode levar minutos — devolve na hora um `job_id` para
    consulta em GET /active-funnels/sync/{job_id}.

    Varre {HOSTING_BASE}/{domain}/public_html e substitui apenas o href das
    tags <a id="switchLink-..."> cujo id bate com um switch_link cadastrado
    para o product + network + active_funnel informados.

    A linha em `product_network_active_funnels` (identificada por
    `product_network_active_funnels_id`) já foi inserida por uma Edge
    Function antes dessa chamada — este endpoint só atualiza `status`
    ('success'/'error') e `error_message` nela ao final. Se o funil ativo
    mudar de fato (`previous_funnel` != `active_funnel`), a transição já
    nasce registrada em `active_funnel_history` com status 'processing'
    (o worker atualiza pro status final ao terminar).
    """
    redis_pool = getattr(request.app.state, "redis_pool", None)
    if not redis_pool:
        logger.error("Redis pool não encontrado no app.state")
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "Serviço de fila indisponível.")

    active_funnel_history_id = None
    if payload.previous_funnel != payload.active_funnel:
        try:
            history_row = (
                db_repo.client.table("active_funnel_history")
                .insert(
                    {
                        "product_id": str(payload.product),
                        "network_id": str(payload.network),
                        "active_funnel_number": payload.active_funnel,
                        "previous_active_funnel_number": payload.previous_funnel,
                        "changed_by": str(payload.user_id),
                        "status": "processing",
                    }
                )
                .execute()
            )
            active_funnel_history_id = history_row.data[0]["id"]
        except Exception as e:
            logger.error(
                f"[active-funnels] falha ao criar active_funnel_history "
                f"(status=processing): {e}"
            )

    job = await redis_pool.enqueue_job(
        "task_funnel_sync",
        str(payload.product),
        str(payload.network),
        payload.domain,
        str(payload.user_id),
        payload.active_funnel,
        payload.previous_funnel,
        str(payload.product_network_active_funnels_id),
        active_funnel_history_id,
        _queue_name=FUNNEL_SYNC_QUEUE,
    )

    logger.info(f"[active-funnels] Job enfileirado: {job.job_id} (product={payload.product})")

    return {
        "status": "queued",
        "job_id": job.job_id,
        "message": "Sincronização enfileirada. Consulte o status em GET /active-funnels/sync/{job_id}.",
    }


@router.get(
    "/active-funnels/sync/{job_id}",
    dependencies=[Depends(verify_funnel_sync_key)],
)
async def get_active_funnel_sync_status(job_id: str, request: Request):
    """Consulta o andamento/resultado de um job enfileirado por POST /active-funnels/sync."""
    redis_pool = getattr(request.app.state, "redis_pool", None)
    if not redis_pool:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "Serviço de fila indisponível.")

    job = Job(job_id, redis_pool, _queue_name=FUNNEL_SYNC_QUEUE)
    job_status = await job.status()

    if job_status == JobStatus.not_found:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Job não encontrado (ou expirado).")

    response = {"job_id": job_id, "status": job_status.value}

    info = await job.result_info()
    if info is None:
        return response

    if info.success:
        response["result"] = info.result
    else:
        error = info.result
        response["error"] = str(error) if error is not None else "Falha desconhecida"
        response["error_type"] = (
            "validation" if isinstance(error, FunnelSyncError) else "unexpected"
        )

    return response
