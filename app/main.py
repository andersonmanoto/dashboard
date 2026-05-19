from contextlib import asynccontextmanager
from arq import create_pool
from arq.connections import RedisSettings
from fastapi import FastAPI
from loguru import logger

from app.config import get_settings
from app.routers.api import api_router
from app.repositories.database import DatabaseRepository


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Gerencia ciclo de vida (Redis + Cache de Redes)."""
    logger.info("Webhook Dashboard iniciado")
    settings = get_settings()

    try:
        # Redis
        app.state.redis_pool = await create_pool(
            RedisSettings(host=settings.redis_host, port=settings.redis_port)
        )
        logger.info(f"Redis conectado em {settings.redis_host}")

        # DB singleton — pool de conexões reusado entre requests
        app.state.db = DatabaseRepository(settings)
        app.state.db.load_networks_cache()

    except Exception as e:
        logger.critical(f"Falha na inicialização: {e}")
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