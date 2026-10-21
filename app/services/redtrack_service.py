"""Cliente para criar recursos no RedTrack via API (offers, por enquanto)."""

import json

import httpx
from loguru import logger

from app.config import Settings


class RedTrackAPIError(Exception):
    """Erro ao chamar a API do RedTrack (config faltando ou resposta de erro)."""


class RedTrackAPI:
    """
    Cliente para criar e gerir recursos no RedTrack via API.
    """

    def __init__(self, settings: Settings):
        self.api_key = settings.redtrack_api_key
        self.user_id = settings.redtrack_user_id
        self.base_url = "https://api.redtrack.io"

        if not self.api_key or not self.user_id:
            raise RedTrackAPIError(
                "REDTRACK_API_KEY / REDTRACK_USER_ID não definidos no .env"
            )

        self.headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "X-Auth-Token": self.api_key,
        }
        logger.info("RedTrack API inicializado.")

    async def create_offer(self, title: str, url: str, program_id: str) -> dict:
        """Cria uma offer no RedTrack (POST /offers) e retorna o JSON de resposta.

        `program_id` é o campo real da API pro "Offer source" mostrado na UI
        (confirmado via erro "Program ID is invalid" ao tentar com
        `network_id`, que não existe de fato no endpoint).
        """
        endpoint = f"{self.base_url}/offers"
        payload = {
            "title": title,
            "url": url,
            "program_id": program_id,
            "user_id": self.user_id,
        }

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    endpoint, headers=self.headers, json=payload
                )
        except httpx.TransportError as e:
            raise RedTrackAPIError(
                f"Falha de conexão ao criar offer '{title}' no RedTrack: {e}"
            ) from e

        if response.status_code not in (200, 201):
            raise RedTrackAPIError(
                f"RedTrack retornou {response.status_code} ao criar offer "
                f"'{title}': {response.text}"
            )

        try:
            return response.json()
        except UnicodeDecodeError:
            # RedTrack já criou a offer (status 200/201) — o corpo só tem
            # algum byte não-UTF-8 (ex: caractere acentuado mal codificado
            # em algum campo). Decodifica tolerando isso em vez de perder a
            # resposta (e o id da offer criada) por causa de um campo cosmético.
            logger.warning(
                f"[redtrack] resposta ao criar offer '{title}' com bytes "
                "não-UTF-8; decodificando com substituição de caracteres inválidos"
            )
            text = response.content.decode("utf-8", errors="replace")
            return json.loads(text)

    async def upload_conversion(
        self, client: httpx.AsyncClient, conversion: dict
    ) -> httpx.Response:
        """Envia UMA conversão pro RedTrack (POST /conversions) e devolve a resposta.

        O body é uma lista; manda um item por chamada de propósito, pra um
        item inválido não derrubar os outros. A api_key vai na query string
        (formato validado em 28/09, resposta 201). O processamento no RedTrack
        é assíncrono: 201 = recebido, a conversão aparece no GET /conversions
        alguns segundos depois.
        """
        return await client.post(
            f"{self.base_url}/conversions",
            params={"api_key": self.api_key},
            headers=self.headers,
            json=[conversion],
        )

    async def _get(self, client: httpx.AsyncClient, path: str, **params):
        """GET com a api_key na query (mesmo formato do POST /conversions)."""
        try:
            response = await client.get(
                f"{self.base_url}{path}",
                params={"api_key": self.api_key, **params},
                headers=self.headers,
            )
        except httpx.TransportError as e:
            raise RedTrackAPIError(f"Falha de conexão em GET {path}: {e}") from e

        if response.status_code != 200:
            raise RedTrackAPIError(
                f"RedTrack retornou {response.status_code} em GET {path}: "
                f"{response.text[:300]}"
            )
        return json.loads(response.content.decode("utf-8", errors="replace"))

    async def get_conversions(
        self, client: httpx.AsyncClient, date_from: str, date_to: str, per: int = 1000
    ) -> list[dict]:
        """Todas as conversões do período (GET /conversions, paginado).

        Resposta: {"items": [...], "total": N}. Datas no fuso da conta.
        """
        items: list[dict] = []
        page = 1
        while True:
            data = await self._get(
                client, "/conversions",
                date_from=date_from, date_to=date_to, per=per, page=page,
            )
            batch = data.get("items") or []
            items.extend(batch)
            if not batch or len(items) >= data.get("total", 0):
                return items
            page += 1

    async def get_report(
        self,
        client: httpx.AsyncClient,
        group: str,
        date_from: str,
        date_to: str,
        per: int = 1000,
    ) -> list[dict]:
        """Linhas do GET /report agrupado por `group` (ex: "rt_ad,landing,campaign").

        Resposta é uma lista crua, sem total: pagina até vir uma página incompleta.
        """
        rows: list[dict] = []
        page = 1
        while True:
            batch = await self._get(
                client, "/report",
                group=group, date_from=date_from, date_to=date_to, per=per, page=page,
            )
            rows.extend(batch)
            if len(batch) < per:
                return rows
            page += 1

    async def get_campaign(self, client: httpx.AsyncClient, campaign_id: str) -> dict:
        return await self._get(client, f"/campaigns/{campaign_id}")

    async def get_offer(self, client: httpx.AsyncClient, offer_id: str) -> dict:
        return await self._get(client, f"/offers/{offer_id}")
