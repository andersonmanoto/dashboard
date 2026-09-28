"""
Carga inicial (ou recarga) dos tokens OAuth do AWeber na tabela
aweber_oauth_tokens.

O OAuth do AWeber exige um login manual no navegador uma única vez (feito
com o pacote pagamerican-aweber: scripts/oauth_bootstrap.py, que gera um
aweber_tokens.json). Este script copia esse arquivo pro banco; dali pra
frente o worker renova o access token sozinho (cron_send_aweber_leads).
Só precisa rodar de novo se o refresh token for revogado.

Uso:
    PYTHONPATH=app python3 app/scripts/aweber_import_tokens.py <aweber_tokens.json>
"""

import json
import sys
from datetime import datetime, timezone

from config import get_settings
from loguru import logger
from repositories.database import DatabaseRepository


def main() -> None:
    if len(sys.argv) != 2:
        logger.error(
            "Uso: PYTHONPATH=app python3 app/scripts/aweber_import_tokens.py "
            "<aweber_tokens.json>"
        )
        return

    with open(sys.argv[1], encoding="utf-8") as fh:
        tokens = json.load(fh)

    # expires_at do pacote é epoch (float); sem ele, força refresh no 1º envio
    expires_at = datetime.fromtimestamp(
        float(tokens.get("expires_at") or 0), timezone.utc
    )

    db_repo = DatabaseRepository(get_settings())
    db_repo.save_aweber_tokens(
        tokens["access_token"], tokens["refresh_token"], expires_at
    )
    logger.info(f"Tokens AWeber gravados em aweber_oauth_tokens (expira {expires_at}).")


if __name__ == "__main__":
    main()
