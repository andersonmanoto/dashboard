"""
Grava o id da offer criada no RedTrack (POST /offers-rt-ap) na linha
correspondente de `checkouts` do dashboard, na coluna `id_rt_offer`.

A linha é achada de dois jeitos, nessa ordem:
  1. `checkout_id` do item de `ofertas` do payload — o front insere as
     linhas de `checkouts` alguns segundos antes de chamar o endpoint, então
     já tem o UUID em mãos. É o caminho principal.
  2. Fallback (payload sem `checkout_id`): extrai o `checkout_code` da URL do
     checkout conforme a rede e busca em `checkouts` por
     checkout_code + network_id + funnel_stage Purchase. Só atualiza se achar
     exatamente uma linha.

Nada aqui levanta erro: a offer já existe no RedTrack (e não dá pra apagar
via API), então falha em `checkouts` só loga e o item volta com
`checkout_id: None` na resposta.
"""

from typing import Any, Optional
from urllib.parse import parse_qs, urlparse

from loguru import logger
from supabase import AsyncClient

from app.services.offer_announcement import NETWORK_BY_PLATAFORMA
from app.services.slicktext_accounts import retry_transport


def _ids(rows: Any) -> list[str]:
    """ids das linhas devolvidas pelo PostgREST (lista de dicts)."""
    return [str(row["id"]) for row in (rows or [])]


def extract_checkout_code(url: str, plataforma: str) -> Optional[str]:
    """
    checkout_code do checkout de front a partir da URL, por rede:
      - BuyGoods:    ...checkout.html?account_id=X&product_codename=<code>&...
      - DigiStore24: https://www.checkout-ds24.com/product/<code>
      - JVZoo:       https://www.jvzoo.com/b/<vendor>/<code>/<x>
      - PagAmerican: https://pay.pagamerican.app/<code>
    """
    parsed = urlparse((url or "").strip())
    parts = [p for p in parsed.path.split("/") if p]
    rede = plataforma.strip().lower()

    if rede == "buygoods":
        values = parse_qs(parsed.query).get("product_codename")
        return values[0] if values else None
    if rede in ("digistore", "digistore24"):
        if len(parts) >= 2 and parts[0] == "product":
            return parts[1]
        return None
    if rede == "jvzoo":
        if len(parts) >= 3 and parts[0] == "b":
            return parts[2]
        return None
    if rede == "pagamerican":
        return parts[-1] if parts else None
    return None


async def resolve_network_id(db: AsyncClient, plataforma: str) -> Optional[str]:
    network_name = NETWORK_BY_PLATAFORMA.get(plataforma.strip().lower())
    if not network_name:
        return None
    ids = _ids(
        (
            await retry_transport(
                lambda: db.table("networks")
                .select("id")
                .ilike("name", network_name)
                .limit(1)
                .execute(),
                "checkouts: busca de network",
            )
        ).data
    )
    return ids[0] if ids else None


async def link_offer_to_checkout(
    db: AsyncClient,
    offer_id: str,
    plataforma: str,
    url: str,
    network_id: Optional[str],
    checkout_id: Optional[str] = None,
) -> Optional[str]:
    """Grava `id_rt_offer` e devolve o checkouts.id atualizado (ou None)."""
    try:
        if checkout_id:
            updated = _ids(
                (
                    await retry_transport(
                        lambda: db.table("checkouts")
                        .update({"id_rt_offer": offer_id})
                        .eq("id", checkout_id)
                        .execute(),
                        f"checkouts: update id_rt_offer ({checkout_id})",
                    )
                ).data
            )
            if not updated:
                logger.warning(
                    f"[redtrack] checkout_id '{checkout_id}' não existe em checkouts; "
                    f"offer '{offer_id}' não vinculada."
                )
                return None
            return updated[0]

        code = extract_checkout_code(url, plataforma)
        if not code or not network_id:
            logger.warning(
                f"[redtrack] Sem checkout_id e sem como achar a linha em checkouts "
                f"(code={code!r}, network_id={network_id!r}, url={url}); "
                f"offer '{offer_id}' não vinculada."
            )
            return None

        matches = _ids(
            (
                await retry_transport(
                    lambda: db.table("checkouts")
                    .select("id")
                    .eq("checkout_code", code)
                    .eq("network_id", network_id)
                    .ilike("funnel_stage", "purchase")
                    .execute(),
                    f"checkouts: busca por checkout_code ({code})",
                )
            ).data
        )
        if len(matches) != 1:
            logger.warning(
                f"[redtrack] checkout_code '{code}' achou {len(matches)} linha(s) "
                f"Purchase em checkouts (esperado 1); offer '{offer_id}' não vinculada."
            )
            return None

        found_id = matches[0]
        await retry_transport(
            lambda: db.table("checkouts")
            .update({"id_rt_offer": offer_id})
            .eq("id", found_id)
            .execute(),
            f"checkouts: update id_rt_offer ({found_id})",
        )
        return found_id
    except Exception as exc:
        logger.error(
            f"[redtrack] Falha ao gravar id_rt_offer '{offer_id}' em checkouts: {exc}"
        )
        return None
