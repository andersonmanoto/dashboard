"""
Reprocessa carrinhos abandonados já salvos em `abandoned_carts` pelo fluxo
normal do SlickText (process_slicktext_sync_task), usando o `payload`
original armazenado em cada linha.

Uso típico: um produto ficou sem `aff_id_sms` configurado por um tempo (o
sync fica silencioso nesse caso -- só loga info e sai) e depois de corrigido
o campo, as vendas que já chegaram como abandono precisam ser reenviadas.

    PYTHONPATH=.:app python3 app/scripts/backfill_slicktext_abandoned.py sohp --since-hours 36
"""

import argparse
import asyncio
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from loguru import logger


def _load_dotenv(path: str = ".env") -> None:
    """
    Injeta as chaves do .env em os.environ (get_slicktext_api_key() lê de lá
    direto, e não tem load_dotenv() nenhum no projeto -- em produção o
    processo já sobe com essas variáveis no ambiente via systemd/docker).

    Não usa `source .env` porque o arquivo tem valores com espaço sem aspas
    (ex: EMAIL_FROM=Tiger Offers Reports <...>) que quebram o parser do bash.
    """
    env_path = Path(path)
    if not env_path.exists():
        return
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv()

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
