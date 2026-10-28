"""
Cadastro de conta SlickText nova (POST /slicktext/accounts): cria na SlickText
as listas de cada produto + a lista de rastreio + os custom fields, e só
depois grava tudo no banco numa transação só (RPC create_slicktext_account,
ver migrations/20261028_slicktext_create_account_with_lists.sql).

Ordem pensada pra não deixar conta pela metade no banco:
  1. valida a key (GET /brands/{brand_id}) e os produtos
  2. SlickText: listas e custom fields
  3. banco: conta + api_key no Vault + shipped_list_id + slicktext_product_lists

Os passos na SlickText são idempotentes (lista com o mesmo nome é reaproveitada,
custom field que já existe é pulado), então se algo falhar no meio é só
mandar o mesmo payload de novo.
"""

import asyncio
from typing import Optional

import httpx
from loguru import logger

from app.repositories.database import DatabaseRepository
from app.services.slicktext_accounts import ACCOUNT_ERROR_STATUSES

REQUEST_TIMEOUT = 15.0

APPROVED_LIST_NAME = "[{product}] [Compra Aprovada]"
ABANDONED_LIST_NAME = "[{product}] [Carrinho Abandonado]"
# Lista do SMS de rastreio (sync_slicktext.py da shipoffers)
SHIPPED_LIST_NAME = "rastreio"

# Campos usados nos payloads de contato (slicktext_service.py,
# sync_approved_orders.py e o rastreio da shipoffers). Tudo string: os
# envios mandam bottles como str.
CUSTOM_FIELDS = (
    "produto",
    "url_abandonada",
    "bottles",
    "plataforma",
    "rastreio",
    "transportadora",
    "url_rastreio",
)


class SlickTextSetupError(Exception):
    def __init__(self, error: str, message: str, status_code: int):
        super().__init__(message)
        self.error = error
        self.message = message
        self.status_code = status_code


def _is_already_exists(resp: httpx.Response) -> bool:
    text = resp.text.lower()
    return resp.status_code == 409 or (
        resp.status_code in (400, 422) and ("exist" in text or "already" in text)
    )


class SlickTextSetupService:
    def __init__(self, api_url: str, db_repo: DatabaseRepository):
        self.api_url = api_url
        self.db = db_repo.client

    async def create_account(
        self, name: str, api_key: str, brand_id: str, products: list[dict]
    ) -> str:
        """Fluxo completo; devolve o id da conta criada em slicktext_accounts."""
        await asyncio.to_thread(self._check_db_preconditions, brand_id, products)

        base_url = f"{self.api_url}/brands/{brand_id}"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        async with httpx.AsyncClient(
            base_url=base_url, headers=headers, timeout=REQUEST_TIMEOUT
        ) as client:
            await self._check_api_key(client)

            product_lists = []
            for product in products:
                product_name = product["product_name"]
                approved = await self._get_or_create_list(
                    client, APPROVED_LIST_NAME.format(product=product_name)
                )
                abandoned = await self._get_or_create_list(
                    client, ABANDONED_LIST_NAME.format(product=product_name)
                )
                product_lists.append(
                    {
                        "product_id": product["product_id"],
                        "approved_list_id": approved,
                        "abandoned_list_id": abandoned,
                    }
                )

            shipped_list_id = await self._get_or_create_list(client, SHIPPED_LIST_NAME)
            await self._ensure_custom_fields(client)

        account_id = await asyncio.to_thread(
            self._save_account, name, brand_id, api_key, shipped_list_id, product_lists
        )
        logger.info(
            f"[SlickText] Conta '{name}' (brand_id {brand_id}) cadastrada: "
            f"{account_id}, {len(product_lists)} produto(s), rastreio {shipped_list_id}."
        )
        return account_id

    # --- banco ---------------------------------------------------------------

    def _check_db_preconditions(self, brand_id: str, products: list[dict]) -> None:
        existing = (
            self.db.table("slicktext_accounts")
            .select("id, name")
            .eq("brand_id", brand_id)
            .execute()
            .data
        )
        if existing:
            raise SlickTextSetupError(
                "account_already_exists",
                f"Já existe conta cadastrada com o brand_id {brand_id} "
                f"('{existing[0]['name']}')",
                409,
            )

        product_ids = {p["product_id"] for p in products}
        found = (
            self.db.table("products")
            .select("id")
            .in_("id", list(product_ids))
            .execute()
            .data
        )
        missing = product_ids - {r["id"] for r in found}
        if missing:
            raise SlickTextSetupError(
                "product_not_found",
                f"Produto(s) não encontrado(s): {', '.join(sorted(missing))}",
                400,
            )

    def _save_account(
        self,
        name: str,
        brand_id: str,
        api_key: str,
        shipped_list_id: str,
        product_lists: list[dict],
    ) -> str:
        try:
            return self.db.rpc(
                "create_slicktext_account",
                {
                    "p_name": name,
                    "p_brand_id": brand_id,
                    "p_api_key": api_key,
                    "p_shipped_list_id": shipped_list_id,
                    "p_product_lists": product_lists,
                },
            ).execute().data
        except Exception as e:
            logger.error(f"[SlickText] Erro ao gravar a conta '{name}' no banco: {e}")
            raise SlickTextSetupError(
                "database_error",
                "Listas e custom fields criados na SlickText, mas falhou ao "
                "gravar no banco. Pode reenviar: o que já existe na SlickText "
                "é reaproveitado.",
                500,
            )

    # --- SlickText -----------------------------------------------------------

    async def _request(
        self, client: httpx.AsyncClient, method: str, path: str, **kwargs
    ) -> httpx.Response:
        try:
            return await client.request(method, path, **kwargs)
        except httpx.RequestError as e:
            logger.error(f"[SlickText] {method} {path} falhou: {e}")
            raise SlickTextSetupError(
                "slicktext_unavailable", f"Falha de conexão com a SlickText: {e}", 502
            )

    def _raise_api_error(self, resp: httpx.Response, action: str) -> None:
        logger.error(
            f"[SlickText] Erro ao {action}: HTTP {resp.status_code} {resp.text}"
        )
        raise SlickTextSetupError(
            "slicktext_error",
            f"Erro da SlickText ao {action} (HTTP {resp.status_code}): {resp.text}",
            502,
        )

    async def _check_api_key(self, client: httpx.AsyncClient) -> None:
        # URL absoluta: com base_url, path "" viraria /brands/{id}/ (barra no fim)
        resp = await self._request(client, "GET", str(client.base_url).rstrip("/"))
        if resp.status_code in ACCOUNT_ERROR_STATUSES:
            raise SlickTextSetupError(
                "invalid_api_key",
                "Chave API inválida, sem permissão ou brand_id incorreto",
                400,
            )
        if resp.is_error:
            self._raise_api_error(resp, "validar a conta")

    async def _get_or_create_list(self, client: httpx.AsyncClient, name: str) -> str:
        resp = await self._request(client, "GET", "/lists", params={"name": name})
        if resp.is_error:
            self._raise_api_error(resp, f"buscar a lista '{name}'")
        existing = _find_by_name(resp.json().get("data"), name)
        if existing:
            logger.info(f"[SlickText] Lista '{name}' já existe, reaproveitando.")
            return str(existing["contact_list_id"])

        resp = await self._request(client, "POST", "/lists", json={"name": name})
        if resp.is_error:
            self._raise_api_error(resp, f"criar a lista '{name}'")
        return str(resp.json()["contact_list_id"])

    async def _ensure_custom_fields(self, client: httpx.AsyncClient) -> None:
        resp = await self._request(client, "GET", "/custom-fields")
        if resp.is_error:
            self._raise_api_error(resp, "buscar os custom fields")
        existing = {
            (f.get(key) or "").lower()
            for f in resp.json().get("data") or []
            for key in ("name", "internal_name")
        }

        for field in CUSTOM_FIELDS:
            if field in existing:
                continue
            resp = await self._request(
                client,
                "POST",
                "/custom-fields",
                json={"name": field, "data_type": "string", "description": field},
            )
            if resp.is_error and not _is_already_exists(resp):
                self._raise_api_error(resp, f"criar o custom field '{field}'")


def _find_by_name(items: Optional[list[dict]], name: str) -> Optional[dict]:
    # o filtro ?name= da API pode não ser exato: confere aqui
    return next((i for i in items or [] if i.get("name") == name), None)
