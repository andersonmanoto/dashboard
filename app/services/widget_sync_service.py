"""
Puxa o script compilado de um widget (publicado no Cloudflare Pages do
projeto tigeroffers_widget, ex: https://cdn.tigeroffers.com/Produto-FunilN-Xpotes.js)
e salva/atualiza em `public.widgets.script_widget`, associado a um
product_id + network_id.

Os dois repositórios (dashboard e tigeroffers_widget) continuam separados —
esse serviço só faz um GET no CDN e um upsert no Supabase, sem depender de
nada do outro projeto além da URL pública já publicada.
"""

from urllib.parse import urlparse

import httpx
from loguru import logger

from app.config import Settings
from app.repositories.database import DatabaseRepository


class WidgetSyncError(Exception):
    """Erro esperado (URL inválida, CDN fora do ar, host não permitido etc)."""


class WidgetSyncService:
    def __init__(self, settings: Settings, db_repo: DatabaseRepository):
        self.settings = settings
        self.db_repo = db_repo

    def _validate_script_url(self, script_url: str) -> None:
        host = urlparse(script_url).hostname or ""
        if host != self.settings.widget_cdn_host:
            raise WidgetSyncError(
                f"script_url precisa ser do host '{self.settings.widget_cdn_host}' "
                f"(recebido: '{host}')."
            )

    async def _fetch_script(self, script_url: str) -> str:
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(script_url)
        except httpx.TransportError as e:
            raise WidgetSyncError(
                f"Falha de conexão ao buscar '{script_url}': {e}"
            ) from e

        if response.status_code != 200:
            raise WidgetSyncError(
                f"CDN retornou {response.status_code} ao buscar '{script_url}'."
            )

        return response.text

    async def sync(self, product_id: str, network_id: str, script_url: str) -> dict:
        """Busca o .js publicado e faz upsert em `widgets` por (network_id, product_id)."""
        self._validate_script_url(script_url)
        script_content = await self._fetch_script(script_url)

        try:
            response = (
                self.db_repo.client.table("widgets")
                .upsert(
                    {
                        "product_id": product_id,
                        "network_id": network_id,
                        "script_widget": script_content,
                    },
                    on_conflict="network_id,product_id",
                )
                .execute()
            )
        except Exception as e:
            raise WidgetSyncError(f"Falha ao salvar widget no Supabase: {e}") from e

        if not response.data:
            raise WidgetSyncError("Upsert em `widgets` não retornou dados.")

        row = response.data[0]
        logger.info(
            f"[widget-sync] script salvo (id={row['id']}, product_id={product_id}, "
            f"network_id={network_id}, {len(script_content)} bytes)"
        )
        return row
