"""
Monta as conversões que vão pra API do RedTrack (POST /conversions).

Por enquanto só vendas de front (Purchase) da BuyGoods, de pares
(produto, aff_id) cadastrados em internal_aff_ids -- upsells/downsells não
são enviados. Quem decide se o evento entra na fila é o EventProcessor; aqui
só se monta a linha da redtrack_conversion_queue.
"""

import re
from datetime import datetime, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from models.schemas import NormalizedEvent

REDTRACK_CONVERSION_TYPE = "Purchase"

# clickid do RedTrack: 24 hex (estilo ObjectId, 8 primeiros = timestamp).
# Filtra fbclid/gclid/ids de outros trackers que chegam no subid.
REDTRACK_CLICKID_RE = re.compile(r"^[0-9a-f]{24}$")

# rr_createdate da BuyGoods vem no horário de Nova York, sem fuso (confirmado
# em 28/09: venda "00:13:43" com InitiateCheckout no RedTrack às 04:12Z).
BUYGOODS_TZ = ZoneInfo("America/New_York")


def buygoods_created_at(event: NormalizedEvent) -> Optional[str]:
    """Data/hora da venda em UTC no formato que o RedTrack aceita (ISO com Z)."""
    if not event.event_date or not event.event_time:
        return None
    local = datetime.strptime(
        f"{event.event_date} {event.event_time}", "%Y-%m-%d %H:%M:%S"
    ).replace(tzinfo=BUYGOODS_TZ)
    return local.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def build_conversion_row(event: NormalizedEvent, event_id: Optional[str]) -> dict:
    """
    Monta a linha da fila pra um Purchase de afiliado interno já validado.

    A linha sai 'skipped' (com skip_reason) quando não dá pra enviar:
      - invalid_click_id: sem subid ou subid que não é clickid do RedTrack
      - funnel_stage_fallback: codename não achado em checkouts, e o
        funnel_stage "Purchase" veio do fallback por nome do produto -- pode
        ser um upsell, então não arrisca mandar como front
      - missing_created_at: sem data da venda
    """
    payload = event.payload or {}
    click_id = (event.click_id or "").strip() or None
    created_at = buygoods_created_at(event)

    skip_reason = None
    if not click_id or not REDTRACK_CLICKID_RE.match(click_id):
        skip_reason = "invalid_click_id"
    elif event.checkout_id is None:
        skip_reason = "funnel_stage_fallback"
    elif not created_at:
        skip_reason = "missing_created_at"

    # Formato validado em 28/09: body é uma lista, campaign_id vazio (o
    # RedTrack resolve a campanha pelo clickid), created_at retroativo é
    # respeitado. A API não aceita status: a conversão entra como "other".
    conversion = {
        "campaign_id": "",
        "clickid": click_id,
        "type": REDTRACK_CONVERSION_TYPE,
        "payout": round(event.aff_commission or 0.0, 2),
        "created_at": created_at,
    }

    return {
        "event_id": event_id,
        "network": getattr(event.network, "value", event.network),
        "bg_order_id": str(payload.get("order_id") or event.order_id),
        "event_order_id": event.order_id,
        "conversion_type": REDTRACK_CONVERSION_TYPE,
        "click_id": click_id,
        "payload": conversion,
        "status": "skipped" if skip_reason else "pending",
        "skip_reason": skip_reason,
    }
