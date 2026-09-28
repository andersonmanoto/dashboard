"""
Leads da PagAmerican pra lista do AWeber (POST /subscribers), com OAuth.

As funções build_* montam o corpo do subscriber que vai pra
aweber_lead_queue (chamadas pelo EventProcessor). AWeberAPI é usada só pelo
cron do worker: envia os pendentes e renova o access token (~2h), que fica
na tabela aweber_oauth_tokens.
"""

import ipaddress
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx
from loguru import logger

AUTH_TOKEN_URL = "https://auth.aweber.com/oauth2/token"
API_BASE = "https://api.aweber.com/1.0"
# Renova o access token 5 min antes de expirar
TOKEN_SKEW = timedelta(minutes=5)


def _public_ip(raw: Optional[str]) -> Optional[str]:
    """IP do cliente, só se for público -- o AWeber rejeita IP privado/reservado."""
    try:
        ip = ipaddress.ip_address((raw or "").strip())
    except ValueError:
        return None
    return str(ip) if ip.is_global else None


def _subscriber(
    email: Optional[str],
    name: Optional[str],
    offer_code: Optional[str] = None,
    ip: Optional[str] = None,
    utm_source: Optional[str] = None,
) -> Optional[dict]:
    email = (email or "").strip().lower()
    if not email or "@" not in email:
        return None

    subscriber: dict = {"email": email, "update_existing": "true"}
    if name and name.strip():
        subscriber["name"] = name.strip()[:60]
    if offer_code:
        subscriber["tags"] = [f"offer-{offer_code.strip()}"[:60]]
    if ip := _public_ip(ip):
        subscriber["ip_address"] = ip
    # Só utm_source: o `src` da PagAmerican é o clickid do RedTrack (24
    # chars), que o AWeber cortaria em 20.
    if utm_source and utm_source.strip():
        subscriber["ad_tracking"] = utm_source.strip()[:20]
    return subscriber


def build_order_subscriber(event) -> Optional[dict]:
    """Subscriber de uma venda PagAmerican (order.purchase.created.v1). None se sem email."""
    payload = event.payload or {}
    customer = payload.get("customer") or {}
    products = payload.get("products") or [{}]
    tracking = payload.get("trackingParameters") or {}
    return _subscriber(
        email=event.customer_email,
        name=event.customer_name,
        offer_code=(products[0] or {}).get("offerCode"),
        ip=customer.get("ip"),
        utm_source=tracking.get("utm_source"),
    )


def build_abandon_subscriber(cart_data: dict) -> Optional[dict]:
    """
    Subscriber de um checkout abandonado da PagAmerican, a partir do
    cart_data já normalizado (os dados vêm em checkoutForm.*, não em
    customer.*). None se sem email.
    """
    return _subscriber(
        email=cart_data.get("customer_email"),
        name=cart_data.get("customer_name"),
        offer_code=cart_data.get("product_codename"),
    )


class AWeberAPIError(Exception):
    """Erro de config/OAuth do AWeber (sem credencial, sem token, refresh recusado)."""


class AWeberAPI:
    def __init__(self, settings, db_repo):
        # Sem type hints de Settings/DatabaseRepository de propósito: este
        # módulo é importado pelo EventProcessor (imports sem prefixo `app.`)
        # e pelo worker (com prefixo) -- importar um dos dois quebraria o outro.
        if not settings.aweber_enabled:
            raise AWeberAPIError(
                "AWEBER_CLIENT_ID / AWEBER_CLIENT_SECRET / AWEBER_ACCOUNT_ID "
                "não definidos no .env"
            )
        self.settings = settings
        self.db = db_repo
        self.default_tags = [
            t.strip() for t in settings.aweber_default_tags.split(",") if t.strip()
        ]
        self.account_url = f"{API_BASE}/accounts/{settings.aweber_account_id}"

    async def _refresh(self, client: httpx.AsyncClient, refresh_token: str) -> str:
        response = await client.post(
            AUTH_TOKEN_URL,
            data={"grant_type": "refresh_token", "refresh_token": refresh_token},
            auth=(self.settings.aweber_client_id, self.settings.aweber_client_secret),
        )
        if response.status_code >= 400:
            raise AWeberAPIError(
                f"AWeber recusou o refresh do token ({response.status_code}): "
                f"{response.text[:300]}"
            )
        token = response.json()
        expires_at = datetime.now(timezone.utc) + timedelta(
            seconds=int(token.get("expires_in") or 7200)
        )
        self.db.save_aweber_tokens(
            token["access_token"],
            # AWeber pode ou não rotacionar o refresh token; guarda o que vier
            token.get("refresh_token") or refresh_token,
            expires_at,
        )
        logger.info("Access token AWeber renovado.")
        return token["access_token"]

    async def get_access_token(
        self, client: httpx.AsyncClient, force_refresh: bool = False
    ) -> str:
        tokens = self.db.get_aweber_tokens()
        if not tokens:
            raise AWeberAPIError(
                "Tabela aweber_oauth_tokens vazia -- rode "
                "app/scripts/aweber_import_tokens.py"
            )
        expires_at = datetime.fromisoformat(tokens["expires_at"])
        if force_refresh or datetime.now(timezone.utc) >= expires_at - TOKEN_SKEW:
            return await self._refresh(client, tokens["refresh_token"])
        return tokens["access_token"]

    async def add_subscriber(
        self,
        client: httpx.AsyncClient,
        list_id: str,
        subscriber: dict,
        access_token: str,
    ) -> tuple[httpx.Response, str]:
        """
        POST /lists/{list_id}/subscribers. Sucesso = 201 (criado) ou 200 (já existia e foi
        atualizado, por causa do update_existing). Em 401 renova o token e
        tenta uma vez de novo. Devolve a resposta e o token em uso.
        """
        body = dict(subscriber)
        tags = list(dict.fromkeys([*self.default_tags, *body.get("tags", [])]))
        if tags:
            body["tags"] = tags

        response = await client.post(
            f"{self.account_url}/lists/{list_id}/subscribers",
            json=body,
            headers={"Authorization": f"Bearer {access_token}"},
        )
        if response.status_code == 401:
            access_token = await self.get_access_token(client, force_refresh=True)
            response = await client.post(
                f"{self.account_url}/lists/{list_id}/subscribers",
                json=body,
                headers={"Authorization": f"Bearer {access_token}"},
            )
        return response, access_token
