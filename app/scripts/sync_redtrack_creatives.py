"""
Coleta manual (backfill) do ranking de criativos do RedTrack: grava
redtrack_events + redtrack_cost_daily e recalcula snapshot_daily_redtrack pros
dias do intervalo. O cron do worker (cron_sync_redtrack_creatives) só cobre
hoje e ontem.

Idempotente: cada dia é substituído inteiro no custo e na snapshot, e as
conversões são upsert pelo id do RedTrack. Pode rodar quantas vezes quiser.

Uso:
    PYTHONPATH=.:app python3 app/scripts/sync_redtrack_creatives.py <from> <to>

    <from>/<to> no formato YYYY-MM-DD (fuso da conta do RedTrack, inclusiva).
"""

import asyncio
import sys
from datetime import date, timedelta

from config import get_settings
from loguru import logger
from repositories.database import DatabaseRepository
from services.redtrack_creative_service import RedTrackCreativeSync
from services.redtrack_service import RedTrackAPI


async def main() -> None:
    if len(sys.argv) != 3:
        logger.error(
            "Uso: PYTHONPATH=.:app python3 app/scripts/sync_redtrack_creatives.py "
            "<from YYYY-MM-DD> <to YYYY-MM-DD>"
        )
        return

    date_from = date.fromisoformat(sys.argv[1])
    date_to = date.fromisoformat(sys.argv[2])
    days = [date_from + timedelta(days=i) for i in range((date_to - date_from).days + 1)]

    settings = get_settings()
    sync = RedTrackCreativeSync(RedTrackAPI(settings), DatabaseRepository(settings))
    await sync.sync(days)


if __name__ == "__main__":
    asyncio.run(main())
