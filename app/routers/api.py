from fastapi import APIRouter
from app.routers import health, webhooks, tools, reports

api_router = APIRouter()

# Health Check
api_router.include_router(health.router, tags=["Health & Monitoring"])

# Webhooks
api_router.include_router(webhooks.router, tags=["Webhooks"])

# Ferramentas (Scanner e Upload)
api_router.include_router(tools.router, tags=["Tools"])

# Relatórios e Exportações
api_router.include_router(reports.router)
