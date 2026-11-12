"""
Backfill de checkouts.id_rt_offer pras offers criadas antes da coluna existir.

A fonte é o AutoPages: cada linha de `checkout_links` com `redtrack_id`
foi criada pelo POST /offers-rt-ap (ou à mão, no legado) e guarda a URL crua
do checkout em `link` e a plataforma via `checkout_id` -> `checkouts.name`
(do AutoPages). Com isso, a linha do dashboard é achada do mesmo jeito que o
fallback do endpoint: checkout_code extraído da URL + network_id + Purchase.

Só preenche linhas com id_rt_offer null; se a linha já tiver outro valor,
reporta como conflito e não mexe. Links sem match (legado de rede que nunca
entrou no dashboard, ex. CartPanda / DS24 de 2025) só aparecem no relatório.

Uso:
    PYTHONPATH=. venv/bin/python -m app.scripts.backfill_checkouts_id_rt_offer           # dry-run
    PYTHONPATH=. venv/bin/python -m app.scripts.backfill_checkouts_id_rt_offer --apply   # grava
"""

import asyncio
import sys
from collections import Counter

from supabase import AsyncClient, create_async_client

from app.config import get_settings
from app.services.checkout_offer_link import extract_checkout_code, resolve_network_id

PAGE = 1000


async def _all_rows(client: AsyncClient, table: str, select: str, not_null: str = "") -> list[dict]:
    rows: list[dict] = []
    start = 0
    while True:
        query = client.table(table).select(select)
        if not_null:
            query = query.not_.is_(not_null, "null")
        page = (await query.order("id").range(start, start + PAGE - 1).execute()).data or []
        rows += page  # type: ignore[arg-type]
        if len(page) < PAGE:
            return rows
        start += PAGE


async def main(apply: bool) -> None:
    settings = get_settings()
    autopages = await create_async_client(
        settings.autopages_supabase_url, settings.autopages_supabase_service_role
    )
    db = await create_async_client(settings.supabase_url, settings.supabase_key)

    ap_checkouts = {c["id"]: c["name"] for c in await _all_rows(autopages, "checkouts", "id, name")}
    links = await _all_rows(
        autopages, "checkout_links", "id, link, name, redtrack_id, checkout_id", not_null="redtrack_id"
    )
    dash = await _all_rows(
        db, "checkouts", "id, checkout_code, network_id, funnel_stage, id_rt_offer"
    )
    by_key: dict[tuple, list[dict]] = {}
    for row in dash:
        if (row.get("funnel_stage") or "").lower() == "purchase":
            by_key.setdefault((row["checkout_code"], row["network_id"]), []).append(row)

    network_cache: dict[str, str | None] = {}
    stats: Counter = Counter()
    pending: dict[str, str] = {}  # checkouts.id -> offer_id

    for link in links:
        plataforma = ap_checkouts.get(link["checkout_id"], "")
        if plataforma not in network_cache:
            network_cache[plataforma] = await resolve_network_id(db, plataforma)
        network_id = network_cache[plataforma]
        code = extract_checkout_code(link.get("link") or "", plataforma)
        matches = by_key.get((code, network_id), []) if code and network_id else []

        if len(matches) != 1:
            stats["sem match" if not matches else "ambíguo"] += 1
            print(f"  [{'sem match' if not matches else 'ambíguo'}] {link['name']} | code={code} | {link.get('link')}")
            continue

        row = matches[0]
        offer_id = link["redtrack_id"]
        if row.get("id_rt_offer") == offer_id:
            stats["já preenchido"] += 1
        elif row.get("id_rt_offer") or (row["id"] in pending and pending[row["id"]] != offer_id):
            stats["conflito"] += 1
            print(
                f"  [conflito] {link['name']} | checkout {row['id']} já tem "
                f"{row.get('id_rt_offer') or pending[row['id']]}, AutoPages diz {offer_id}"
            )
        else:
            pending[row["id"]] = offer_id
            stats["a preencher"] += 1

    print(f"\nlinks com redtrack_id: {len(links)} | {dict(stats)}")

    if not apply:
        print("dry-run: nada gravado (use --apply)")
        return

    for checkout_id, offer_id in pending.items():
        await db.table("checkouts").update({"id_rt_offer": offer_id}).eq("id", checkout_id).execute()
    print(f"gravado: {len(pending)} linha(s)")


if __name__ == "__main__":
    asyncio.run(main(apply="--apply" in sys.argv))
