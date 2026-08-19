from fastapi import APIRouter
from app.routers import health, webhooks, tools, reports, active_funnels, redtrack, widgets

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

# Widgets (sync do script publicado no CDN pro Supabase)
api_router.include_router(widgets.router)
