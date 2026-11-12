"""
Troca o buylink de uma offer já criada no RedTrack (PUT /offers-rt-ap/url).

O front manda só `checkout_id` (checkouts.id do dashboard) + o buylink cru.
Daqui sai:
  1. Linha de `checkouts` -> `id_rt_offer` (offer no RedTrack) e network_id
     -> networks.name (= plataforma usada pelas regras de URL).
  2. GET da offer no RedTrack -> aff_id atual, lido da URL que ela já tem
     (parâmetro de afiliado da rede, AFF_PARAM_BY_PLATAFORMA). PagAmerican
     não leva aff.
  3. URL de tracking nova = mesma regra da criação (build_offer_url) sobre o
     buylink sem parâmetros de tracking (strip_tracking_params).
  4. PUT da offer com o objeto completo e a url trocada (pula se já igual).
  5. `checkouts.url` / `checkout_code` (buylink cru e código extraído dele) e
     `checkout_links.link` do AutoPages (por redtrack_id) com o buylink cru.

Ordem pensada pra retry: o RedTrack é atualizado primeiro; se dashboard ou
AutoPages falharem depois disso, o erro avisa e repetir a mesma chamada é
seguro (o aff_id sai da offer já atualizada, que mantém o mesmo valor, e o
PUT é pulado se a url não mudou).
"""

from typing import Any, Optional
from urllib.parse import parse_qs, urlparse

import httpx
from loguru import logger
from supabase import AsyncClient, create_async_client

from app.config import Settings
from app.services.autopages_service import AutoPagesService
from app.services.checkout_offer_link import extract_checkout_code
from app.services.redtrack_offer_service import (
    AFF_PARAM_BY_PLATAFORMA,
    SUPPORTED_PLATAFORMAS,
    build_offer_url,
    strip_tracking_params,
)
from app.services.redtrack_service import RedTrackAPI, RedTrackAPIError
from app.services.slicktext_accounts import retry_transport


class OfferUrlUpdateError(Exception):
    """Erro esperado do fluxo, já com o status HTTP que o router devolve."""

    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status_code = status_code


def extract_aff_id(offer_url: str, plataforma: str) -> Optional[str]:
    """aff_id atual da offer, lido do parâmetro de afiliado da rede."""
    param = AFF_PARAM_BY_PLATAFORMA.get(plataforma.strip().lower())
    if not param:
        return None
    values = parse_qs(urlparse(offer_url or "").query).get(param)
    return values[0] if values else None


def _first(rows: Any) -> Optional[dict]:
    rows = rows or []
    return dict(rows[0]) if rows else None


class RedTrackOfferUrlService:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.redtrack = RedTrackAPI(settings)
        self.autopages = AutoPagesService(settings)

    async def _db(self) -> AsyncClient:
        return await create_async_client(self.settings.supabase_url, self.settings.supabase_key)

    async def update_offer_url(self, checkout_id: str, raw_url: str) -> dict:
        raw_url = (raw_url or "").strip()
        if not raw_url.lower().startswith(("http://", "https://")):
            raise OfferUrlUpdateError(422, f"URL inválida: '{raw_url}'.")

        db = await self._db()

        checkout = _first(
            (
                await retry_transport(
                    lambda: db.table("checkouts")
                    .select("id, id_rt_offer, network_id, checkout_code, url")
                    .eq("id", checkout_id)
                    .limit(1)
                    .execute(),
                    f"checkouts: busca {checkout_id}",
                )
            ).data
        )
        if not checkout:
            raise OfferUrlUpdateError(404, f"checkout_id '{checkout_id}' não existe em checkouts.")
        offer_id = checkout.get("id_rt_offer")
        if not offer_id:
            raise OfferUrlUpdateError(
                409, f"checkout '{checkout_id}' não tem offer no RedTrack (id_rt_offer vazio)."
            )

        network = _first(
            (
                await retry_transport(
                    lambda: db.table("networks")
                    .select("name")
                    .eq("id", checkout["network_id"])
                    .limit(1)
                    .execute(),
                    "networks: busca por id",
                )
            ).data
        )
        plataforma = ((network or {}).get("name") or "").strip()
        if plataforma.lower() not in SUPPORTED_PLATAFORMAS:
            raise OfferUrlUpdateError(
                422, f"Rede '{plataforma or checkout['network_id']}' sem regra de URL de offer."
            )

        buylink = strip_tracking_params(raw_url, plataforma)
        checkout_code = extract_checkout_code(buylink, plataforma)
        if not checkout_code:
            raise OfferUrlUpdateError(
                422, f"Não achei o código do checkout na URL pra rede {plataforma}: '{buylink}'."
            )

        if checkout_code != checkout.get("checkout_code"):
            clash = (
                await retry_transport(
                    lambda: db.table("checkouts")
                    .select("id")
                    .eq("checkout_code", checkout_code)
                    .eq("network_id", checkout["network_id"])
                    .neq("id", checkout_id)
                    .limit(1)
                    .execute(),
                    f"checkouts: conflito de checkout_code {checkout_code}",
                )
            ).data
            if clash:
                raise OfferUrlUpdateError(
                    409,
                    f"checkout_code '{checkout_code}' já pertence a outro checkout "
                    f"({_first(clash)['id']}) nessa rede.",  # type: ignore[index]
                )

        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                offer = await self.redtrack.get_offer(client, offer_id)
            except RedTrackAPIError as e:
                raise OfferUrlUpdateError(502, str(e)) from e

            previous_url = offer.get("url") or ""
            aff_id = extract_aff_id(previous_url, plataforma)
            if plataforma.lower() in AFF_PARAM_BY_PLATAFORMA and not aff_id:
                raise OfferUrlUpdateError(
                    422,
                    f"Não achei o aff_id ('{AFF_PARAM_BY_PLATAFORMA[plataforma.lower()]}') "
                    f"na URL atual da offer '{offer_id}': {previous_url}",
                )

            tracking_url = build_offer_url(buylink, aff_id or "", plataforma)
            changed = tracking_url != previous_url
            if changed:
                try:
                    await self.redtrack.update_offer(client, {**offer, "url": tracking_url})
                except RedTrackAPIError as e:
                    raise OfferUrlUpdateError(502, str(e)) from e
                logger.info(f"[redtrack] Offer '{offer_id}' com URL nova: {tracking_url}")

        after_rt = (
            f"A offer '{offer_id}' já está com a URL nova no RedTrack; "
            "repita a chamada pra terminar a sincronização."
        )
        try:
            await retry_transport(
                lambda: db.table("checkouts")
                .update({"url": buylink, "checkout_code": checkout_code})
                .eq("id", checkout_id)
                .execute(),
                f"checkouts: update url {checkout_id}",
            )
        except Exception as e:
            logger.error(f"[redtrack] Falha ao atualizar checkouts '{checkout_id}': {e}")
            raise OfferUrlUpdateError(502, f"Falha ao atualizar checkouts: {e}. {after_rt}") from e

        try:
            autopages_rows = await self.autopages.update_checkout_link_url(offer_id, buylink)
        except Exception as e:
            logger.error(f"[redtrack] Falha ao atualizar checkout_links da offer '{offer_id}': {e}")
            raise OfferUrlUpdateError(
                502, f"Falha ao atualizar checkout_links no AutoPages: {e}. {after_rt}"
            ) from e
        if not autopages_rows:
            logger.warning(f"[redtrack] Offer '{offer_id}' sem linha em checkout_links do AutoPages.")

        return {
            "checkout_id": checkout_id,
            "offer_id": offer_id,
            "plataforma": plataforma,
            "buylink": buylink,
            "url": tracking_url,
            "url_anterior": previous_url,
            "redtrack_atualizado": changed,
            "checkout_code": checkout_code,
            "checkout_code_anterior": checkout.get("checkout_code"),
            "autopages_checkout_links": autopages_rows,
        }
