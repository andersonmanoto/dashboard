"""
Popula as tabelas `products` e `checkout_links` do Supabase do AutoPages —
projeto Supabase separado do dashboard — a partir das offers criadas no
RedTrack.

`checkouts` é uma lista fixa/curada de plataformas (Buygoods, Digistore24,
CartPanda, ...) — este serviço só faz lookup nela, nunca cria uma linha
nova. `products` é find-or-create: busca por `name` (case-insensitive —
já não há mais inconsistência de espaçamento, só de maiúsculas/minúsculas)
e cai pra `code` se não achar por nome.
"""

from loguru import logger
from supabase import create_async_client

from app.config import Settings


class AutoPagesError(Exception):
    """Erro esperado (config faltando, plataforma não cadastrada, etc)."""


class AutoPagesService:
    def __init__(self, settings: Settings):
        if not settings.autopages_supabase_url or not settings.autopages_supabase_service_role:
            raise AutoPagesError(
                "AUTOPAGES_SUPABASE_URL / AUTOPAGES_SUPABASE_SERVICE_ROLE "
                "não definidos no .env"
            )
        self.settings = settings
        self._client = None

    async def _get_client(self):
        if self._client is None:
            self._client = await create_async_client(
                self.settings.autopages_supabase_url,
                self.settings.autopages_supabase_service_role,
            )
        return self._client

    async def get_checkout(self, plataforma: str) -> dict:
        """Busca a plataforma em `checkouts` (case-insensitive). Não cria."""
        client = await self._get_client()
        response = (
            await client.table("checkouts")
            .select("id, name")
            .ilike("name", plataforma)
            .limit(1)
            .execute()
        )
        if not response.data:
            raise AutoPagesError(
                f"Plataforma '{plataforma}' não encontrada em `checkouts` do AutoPages."
            )
        return response.data[0]

    async def get_or_create_product(self, code: str, name: str) -> str:
        """
        Retorna products.id.

        Tenta por `name` primeiro (case-insensitive — os nomes ainda têm
        inconsistência de maiúsculas/minúsculas entre chamadas, mas o
        espaçamento já está sanado, então ilike exato resolve). Se não
        achar por nome, cai pra `code`. Só cria o produto se nenhum dos
        dois bater.
        """
        client = await self._get_client()

        response = (
            await client.table("products")
            .select("id")
            .ilike("name", name)
            .limit(1)
            .execute()
        )
        if response.data:
            return response.data[0]["id"]

        response = (
            await client.table("products")
            .select("id")
            .eq("code", code)
            .limit(1)
            .execute()
        )
        if response.data:
            return response.data[0]["id"]

        insert_response = (
            await client.table("products")
            .insert({"code": code, "name": name})
            .execute()
        )
        if not insert_response.data:
            raise AutoPagesError(f"Falha ao criar produto '{name}' ({code}) no AutoPages.")

        logger.info(f"[autopages] Produto criado: '{name}' ({code})")
        return insert_response.data[0]["id"]

    async def create_checkout_link(self, **fields) -> dict:
        client = await self._get_client()
        response = await client.table("checkout_links").insert(fields).execute()
        if not response.data:
            raise AutoPagesError("Falha ao criar checkout_link no AutoPages.")
        return response.data[0]
