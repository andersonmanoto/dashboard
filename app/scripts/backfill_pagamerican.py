"""
Backfill retroativo de vendas da PagAmerican via API REST (GET /purchases).

Contexto: o endpoint de recebimento de webhooks foi criado em 2026-09-10,
mas a URL gerada nunca foi configurada no painel da PagAmerican -- a
webhook_inbox pra essa rede ficou zerada, então nenhuma venda real chegou
pelo fluxo normal. Todo o histórico precisa ser puxado por essa API até a
URL ser corrigida no painel.

O formato da API de purchases é bem diferente do payload do webhook: é flat,
valores em dólares (não centavos) e não traz clickid/utm/endereço. Por isso
o evento é montado direto aqui em vez de reaproveitar
PayloadNormalizer._normalize_pagamerican, que espera o formato aninhado do
webhook.

Cada purchase da API (inclusive upsells) já é uma venda individual de um
produto só -- o `purchaseId` é o equivalente ao `orderId` que o webhook
manda por evento. O `orderId` da API, por outro lado, identifica o checkout
como um todo e se repete entre front e upsells de uma mesma compra, então
NÃO pode ser usado como order_id interno (duplicaria a dedupe).

Uso:
    PYTHONPATH=app python3 app/scripts/backfill_pagamerican.py <from> <to>

    <from>/<to> no formato YYYY-MM-DD. A API rejeita janelas maiores que 90
    dias, então o período é dividido automaticamente em blocos.
"""

import asyncio
import sys
from datetime import datetime, timedelta

import httpx
from config import get_settings
from loguru import logger
from models.enums import ActionType, NetworkType
from models.schemas import NormalizedEvent, OrderDetails
from repositories.database import DatabaseRepository
from services.event_processor import EventProcessor
from utils.date_utils import parse_date

BASE_URL = "https://external-api-service.pagamerican.app/api/v1"
PAGE_LIMIT = 1000
MAX_WINDOW_DAYS = 90


def _date_chunks(date_from: str, date_to: str) -> list[tuple[str, str]]:
    """Divide o período em blocos de no máximo MAX_WINDOW_DAYS (limite da API)."""
    start = datetime.strptime(date_from, "%Y-%m-%d").date()
    end = datetime.strptime(date_to, "%Y-%m-%d").date()

    chunks = []
    cursor = start
    while cursor <= end:
        chunk_end = min(cursor + timedelta(days=MAX_WINDOW_DAYS - 1), end)
        chunks.append((cursor.isoformat(), chunk_end.isoformat()))
        cursor = chunk_end + timedelta(days=1)
    return chunks


async def fetch_purchases(api_key: str, date_from: str, date_to: str) -> list[dict]:
    """Pagina o endpoint /purchases para um único bloco de datas."""
    purchases = []
    page = 1
    async with httpx.AsyncClient(
        headers={"Authorization": f"Bearer {api_key}"}, timeout=30.0
    ) as client:
        while True:
            resp = await client.get(
                f"{BASE_URL}/purchases",
                params={
                    "page": page,
                    "limit": PAGE_LIMIT,
                    "from": date_from,
                    "to": date_to,
                },
            )
            resp.raise_for_status()
            body = resp.json()
            data = body.get("data") or []
            purchases.extend(data)
            logger.info(
                f"[{date_from} -> {date_to}] página {page}: {len(data)} compra(s)."
            )

            if not body.get("meta", {}).get("truncated"):
                break
            page += 1

    return purchases


def _build_event(item: dict) -> NormalizedEvent:
    """Converte um item de /purchases num NormalizedEvent pronto pra persistir."""
    purchase_id = str(item["purchaseId"])
    created_raw = (item.get("purchaseCreatedAt") or "").split(".")[0]
    event_date, event_time = parse_date(created_raw, NetworkType.PAGAMERICAN)

    sale_total = float(item.get("amountTransaction") or 0.0)
    merchant_commission = float(item.get("amountCreatorPlatformFee") or 0.0)
    merchant_rate = (
        round(merchant_commission / sale_total, 4) if sale_total > 0 else 0.0
    )

    product_id = item.get("productId")

    return NormalizedEvent(
        network=NetworkType.PAGAMERICAN,
        order_id=purchase_id,
        action_type=ActionType.NEWORDER,
        event_date=event_date,
        event_time=event_time,
        customer_name=item.get("customerName"),
        customer_email=item.get("customerEmail"),
        customer_phone=item.get("customerPhone"),
        # A API não informa moeda -- toda a operação observada é em USD
        # (telefones/valores compatíveis com a mesma faixa dos webhooks).
        currency="USD",
        sale_total=sale_total,
        product_price=float(item.get("amountItemsGross") or 0.0),
        aff_commission=float(item.get("amountAffiliation") or 0.0),
        tax_amount=float(item.get("amountTaxes") or 0.0),
        merchant_commission=merchant_commission,
        merchant_commission_rate=merchant_rate,
        shipping_cost=float(item.get("amountShippingNet") or 0.0),
        payment_method=item.get("paymentMethod"),
        # Sem tracking (clickid/utm) e sem endereço -- a API de purchases não
        # devolve trackingParameters nem shipping, diferente do webhook.
        is_upsell=item.get("purchaseType") != "front",
        is_test=False,
        order_details=OrderDetails(
            external_product_id=str(product_id) if product_id else None,
            external_checkout_code=item.get("offerCode"),
            external_affiliate_id="0",
            external_affiliate_name="Tiger Offers",
            product_name=item.get("productName"),
        ),
        payload=item,
    )


async def main():
    if len(sys.argv) < 3:
        logger.error(
            "Uso: PYTHONPATH=app python3 app/scripts/backfill_pagamerican.py "
            "<from YYYY-MM-DD> <to YYYY-MM-DD>"
        )
        return

    date_from, date_to = sys.argv[1], sys.argv[2]

    settings = get_settings()
    if not settings.pagamerican_api_key:
        logger.error("PAGAMERICAN_API_KEY não configurada no .env.")
        return

    db_repo = DatabaseRepository(settings)
    # slack_service=None: evita flood de notificações durante importação em massa
    processor = EventProcessor(db_repo, slack_service=None)

    logger.info(f"Buscando compras da PagAmerican de {date_from} a {date_to}...")

    purchases: list[dict] = []
    for chunk_from, chunk_to in _date_chunks(date_from, date_to):
        purchases.extend(
            await fetch_purchases(settings.pagamerican_api_key, chunk_from, chunk_to)
        )

    logger.info(f"{len(purchases)} compra(s) encontrada(s) no período total.")

    success = skipped = errors = 0

    for item in purchases:
        order_id = str(item["purchaseId"])
        try:
            if db_repo.event_exists(order_id, ActionType.NEWORDER.value):
                logger.warning(f"Order {order_id} já existe em events. Ignorando.")
                skipped += 1
                continue

            event = _build_event(item)
            processed = await processor.process_event(event)

            if processed:
                success += 1
            else:
                skipped += 1

        except Exception as e:
            errors += 1
            logger.error(f"Erro ao processar purchase {order_id}: {e}")

    logger.success(
        f"Backfill PagAmerican concluído.\n"
        f"---> Processados: {success}\n"
        f"---> Ignorados: {skipped}\n"
        f"---> Falhas: {errors}"
    )


if __name__ == "__main__":
    asyncio.run(main())
