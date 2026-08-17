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
