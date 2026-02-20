import time
from fastapi import APIRouter, Depends, Response, status, Request
from loguru import logger
import asyncio
from app.dependencies import get_database_repository
from app.repositories.database import DatabaseRepository

router = APIRouter(tags=["Health & Monitoring"])


@router.get("/health", status_code=status.HTTP_200_OK)
async def health_check(
    request: Request,
    response: Response,
    db_repo: DatabaseRepository = Depends(get_database_repository),
):
    """
    Deep Health Check: Verifica API, Banco e Redis.
    Retorna 503 se serviços estiverem fora.
    """
    start_time = time.time()

    status_data = {
        "status": "healthy",
        "latency_ms": 0,
        "services": {
            "database": "unknown",
            "redis": "unknown",
        },
    }

    # 1. Verifica Redis
    try:
        if hasattr(request.app.state, "redis_pool"):
            pool = request.app.state.redis_pool
            if pool:
                status_data["services"]["redis"] = "healthy"
            else:
                status_data["services"]["redis"] = "disconnected"
        else:
            status_data["services"]["redis"] = "not_initialized"
    except Exception as e:
        status_data["services"]["redis"] = "unhealthy"
        status_data["status"] = "unhealthy"
        logger.error(f"Health Check Redis: {e}")

    # 2. Verifica Supabase
    is_connected = await asyncio.to_thread(db_repo.check_connection)
    if is_connected:
        status_data["services"]["database"] = "healthy"
    else:
        status_data["services"]["database"] = "unhealthy"
        status_data["status"] = "unhealthy"

    # Calcula latência
    status_data["latency_ms"] = round((time.time() - start_time) * 1000, 2)

    # Se falhou, retorna 503
    if status_data["status"] == "unhealthy":
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    return status_data
