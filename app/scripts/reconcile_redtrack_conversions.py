"""
Reconciliação da fila de conversões do RedTrack (redtrack_conversion_queue).

Varre os IPNs de venda nova de uma rede (BuyGoods `neworder`, DigiStore24
`payment`) no webhook_inbox num intervalo de datas e enfileira o que ficou de fora da fila -- ex: o insert na fila falhou
(timeout do Supabase) depois de a venda já ter sido gravada na events. Passa
pelas mesmas regras do caminho ao vivo (EventProcessor.enqueue_redtrack_conversion):
só Purchase de front, só par (produto, aff_id) de internal_aff_ids, só venda
com event_date >= REDTRACK_CONVERSIONS_SEND_FROM, só rede de
REDTRACK_CONVERSIONS_NETWORKS, fora os produtos de
REDTRACK_CONVERSIONS_EXCLUDED_PRODUCT_IDS.

Idempotente: pedido que já está na fila (em qualquer status) não é tocado,
pela chave única (network, event_order_id, conversion_type). Pode rodar quantas
vezes quiser. Pra REENVIAR algo que já está na fila (ex: failed), é um UPDATE
direto: status='pending', attempts=0.

Não grava nada na events -- só lê o IPN cru, normaliza e enriquece (produto,
funnel_stage) pra decidir se entra na fila.

Uso:
    PYTHONPATH=app python3 app/scripts/reconcile_redtrack_conversions.py <from> <to> [rede]

    <from>/<to> no formato YYYY-MM-DD (data de chegada do IPN, UTC, inclusiva).
    [rede] = NetworkType.value (BuyGoods, DigiStore24). Padrão: BuyGoods.
"""

import asyncio
import sys
from datetime import date, timedelta

from config import get_settings
from loguru import logger
from models.enums import NetworkType
from repositories.database import DatabaseRepository
from services.event_processor import EventProcessor
from services.normalizer import PayloadNormalizer
from services.redtrack_conversion_service import INBOX_NEWORDER_FILTER


async def main() -> None:
    if len(sys.argv) not in (3, 4):
        logger.error(
            "Uso: PYTHONPATH=app python3 app/scripts/reconcile_redtrack_conversions.py "
            "<from YYYY-MM-DD> <to YYYY-MM-DD> [BuyGoods|DigiStore24]"
        )
        return

    network = NetworkType(sys.argv[3] if len(sys.argv) == 4 else NetworkType.BUYGOODS.value)
    if network.value not in INBOX_NEWORDER_FILTER:
        logger.error(f"Rede {network.value} não tem envio de conversões pro RedTrack.")
        return
    type_field, type_value = INBOX_NEWORDER_FILTER[network.value]

    date_from = date.fromisoformat(sys.argv[1])
    date_to_exclusive = date.fromisoformat(sys.argv[2]) + timedelta(days=1)

    settings = get_settings()
    if not settings.redtrack_conversions_send_from:
        logger.error(
            "REDTRACK_CONVERSIONS_SEND_FROM não configurado no .env -- envio pro "
            "RedTrack está desligado, nada a reconciliar."
        )
        return
    if network.value not in settings.redtrack_conversions_network_set:
        logger.error(
            f"{network.value} não está em REDTRACK_CONVERSIONS_NETWORKS -- envio "
            "desligado pra essa rede, nada a reconciliar."
        )
        return

    db_repo = DatabaseRepository(settings)
    db_repo.load_networks_cache()
    normalizer = PayloadNormalizer()
    # slack_service=None: codename não achado já foi notificado no caminho ao vivo
    processor = EventProcessor(
        db_repo,
        slack_service=None,
        zapier_webhook_enabled=False,
        redtrack_send_from=settings.redtrack_conversions_send_from,
        redtrack_excluded_product_ids=settings.redtrack_conversions_excluded_product_id_set,
        redtrack_networks=settings.redtrack_conversions_network_set,
    )

    rows = db_repo._fetch_all_paginated(
        "webhook_inbox",
        lambda q: q.eq("network", network.value)
        .eq(f"payload->>{type_field}", type_value)
        .gte("created_at", date_from.isoformat())
        .lt("created_at", date_to_exclusive.isoformat())
        .order("created_at"),
        select="id, payload",
    )
    logger.info(f"{len(rows)} IPN(s) de venda nova ({network.value}) no período.")

    errors = 0
    for row in rows:
        try:
            event = normalizer.normalize(network, row["payload"])
            # Só o checkout (product_id + funnel_stage): o filtro usa o aff_id
            # cru do IPN, e _enrich_affiliate poderia criar afiliado no banco.
            await processor._enrich_checkout(event)
            processor.enqueue_redtrack_conversion(event)
        except Exception:
            errors += 1
            logger.exception(f"Falha ao reconciliar inbox {row['id']}")

    logger.info(
        f"Reconciliação concluída: {len(rows)} IPN(s) verificado(s), {errors} erro(s). "
        "Novos na fila aparecem no log como 'Conversão RedTrack enfileirada'."
    )


if __name__ == "__main__":
    asyncio.run(main())
