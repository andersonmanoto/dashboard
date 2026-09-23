"""
Reprocessa carrinhos abandonados já salvos em `abandoned_carts` pelo fluxo
normal do SlickText (process_slicktext_sync_task), usando o `payload`
original armazenado em cada linha.

Uso típico: um produto ficou sem `aff_id_sms` configurado por um tempo (o
sync fica silencioso nesse caso -- só loga info e sai) e depois de corrigido
o campo, as vendas que já chegaram como abandono precisam ser reenviadas.

    PYTHONPATH=app python3 app/scripts/backfill_slicktext_abandoned.py sohp --since-hours 36
"""

import argparse
import asyncio
from datetime import datetime, timedelta, timezone

from loguru import logger

from config import get_settings
from repositories.database import DatabaseRepository
from services.slicktext_service import process_slicktext_sync_task


async def backfill(product_codename_prefix: str, since_hours: int):
    settings = get_settings()
    db_repo = DatabaseRepository(settings)

    since_dt = datetime.now(timezone.utc) - timedelta(hours=since_hours)
    response = (
        db_repo.client.table("abandoned_carts")
        .select("id, payload, created_at")
        .ilike("product_codename", f"{product_codename_prefix}%")
        .gte("created_at", since_dt.isoformat())
        .order("created_at", desc=False)
        .execute()
    )

    rows = response.data or []
    logger.info(f"{len(rows)} linha(s) encontrada(s) pra reprocessar.")

    for row in rows:
        payload = row.get("payload") or {}
        try:
            await process_slicktext_sync_task(payload, settings, db_repo)
        except Exception:
            logger.exception(f"Falha ao reprocessar abandoned_cart {row['id']}")

    logger.info("Backfill concluído.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("product_codename_prefix", help="ex: sohp")
    parser.add_argument("--since-hours", type=int, default=36)
    args = parser.parse_args()

    asyncio.run(backfill(args.product_codename_prefix, args.since_hours))
