from contextlib import asynccontextmanager
from arq import create_pool
from arq.connections import RedisSettings
from fastapi import FastAPI
from loguru import logger

from app.config import get_settings
from app.routers.api import api_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Gerencia ciclo de vida (Redis)."""
    logger.info("Webhook Dashboard iniciado")
    settings = get_settings()

    try:
        app.state.redis_pool = await create_pool(
            RedisSettings(host=settings.redis_host, port=settings.redis_port)
        )
        logger.info(f"Redis conectado em {settings.redis_host}")
    except Exception as e:
        logger.critical(f"Falha Redis: {e}")
        raise e

    yield

    if hasattr(app.state, "redis_pool"):
        await app.state.redis_pool.close()
        logger.info("Redis desconectado")

    logger.info("Webhook Dashboard encerrado")


app = FastAPI(
    title="Tiger Offers - Webhook Dashboard",
    description="API Normalizadora de Webhooks (BuyGoods/DigiStore24)",
    version="2.4.0",
    lifespan=lifespan,
)

app.include_router(api_router)
