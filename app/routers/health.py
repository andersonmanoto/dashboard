import time
from fastapi import APIRouter, Response, status, Request


router = APIRouter(tags=["Health & Monitoring"])


@router.get("/health")
async def health_check(request: Request, response: Response):
    start_time = time.time()

    status_data = {
        "status": "healthy",
        "services": {"database": "unknown", "redis": "unknown"},
    }

    # Redis
    try:
        pool = getattr(request.app.state, "redis_pool", None)
        status_data["services"]["redis"] = "healthy" if pool else "disconnected"
    except Exception:
        status_data["services"]["redis"] = "unhealthy"
        status_data["status"] = "unhealthy"

    # DB — só verifica se o singleton existe, sem query
    try:
        db = getattr(request.app.state, "db", None)
        status_data["services"]["database"] = "healthy" if db else "not_initialized"
    except Exception:
        status_data["services"]["database"] = "unhealthy"
        status_data["status"] = "unhealthy"

    status_data["latency_ms"] = round((time.time() - start_time) * 1000, 2)

    if status_data["status"] == "unhealthy":
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    return status_data
