from fastapi import APIRouter
from app.routers import (
    health,
    webhooks,
    tools,
    reports,
    active_funnels,
    redtrack,
    slicktext,
    vsl_templates,
)

api_router = APIRouter()

# Health Check
api_router.include_router(health.router, tags=["Health & Monitoring"])

# Webhooks
api_router.include_router(webhooks.router, tags=["Webhooks"])

# Ferramentas (Scanner e Upload)
api_router.include_router(tools.router, tags=["Tools"])

# Relatórios e Exportações
api_router.include_router(reports.router)

# Funil ativo por produto/rede (troca de links de checkout)
api_router.include_router(active_funnels.router)

# RedTrack (criação automática de offers)
api_router.include_router(redtrack.router)

# SlickText (cadastro de conta + listas + custom fields)
api_router.include_router(slicktext.router)

# Templates de VSL (kit 2.9 no psstock: preview + AutoPages)
api_router.include_router(vsl_templates.router)
