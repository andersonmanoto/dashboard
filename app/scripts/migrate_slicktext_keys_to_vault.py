"""
Copia as api_keys do SlickText do .env (SLICKTEXT_API_KEY_<BRAND_ID>) pro
Vault, uma vez, depois da migration 20261027_slicktext_accounts_vault_status.sql
e ANTES do deploy (o código novo só lê do Vault).

Só mexe em conta sem secret_id; rodar de novo não sobrescreve nada.

    PYTHONPATH=. python3 app/scripts/migrate_slicktext_keys_to_vault.py
"""

import re

from dotenv import dotenv_values
from loguru import logger
from supabase import create_client

from app.config import get_settings


def env_key_for(brand_id: str, env: dict) -> str:
    """Mesma convenção da antiga get_slicktext_api_key: '35016' -> SLICKTEXT_API_KEY_35016."""
    suffix = re.sub(r"[^A-Za-z0-9]+", "_", brand_id).strip("_").upper()
    return env.get(f"SLICKTEXT_API_KEY_{suffix}") or ""


def main() -> None:
    settings = get_settings()
    db = create_client(settings.supabase_url, settings.supabase_key)
    env = dotenv_values(".env")

    accounts = (
        db.table("slicktext_accounts")
        .select("id, name, brand_id, secret_id")
        .execute()
        .data
    )
    for account in accounts:
        label = f"'{account['name']}' (brand_id {account['brand_id']})"
        if account.get("secret_id"):
            logger.info(f"{label}: já tem key no Vault, pulando.")
            continue
        api_key = env_key_for(account["brand_id"], env)
        if not api_key:
            logger.warning(f"{label}: sem key no .env -- cadastrar pelo front.")
            continue
        db.rpc(
            "update_slicktext_account_api_key",
            {"p_id": account["id"], "p_api_key": api_key},
        ).execute()
        logger.info(f"{label}: key copiada pro Vault.")

    active = db.rpc("get_slicktext_credentials").execute().data
    logger.info(f"Contas active com key no Vault: {[a['name'] for a in active]}")


if __name__ == "__main__":
    main()
