import sys
import os
import asyncio
from loguru import logger

# 1. Este truque diz ao Python local para incluir a pasta 'app' como raiz.
# Assim, o 'from config import Settings' lá dentro do database.py vai funcionar perfeitamente!
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "app"))

# 2. Agora fazemos as importações da mesma forma que o seu projeto faz internamente
from config import get_settings
from repositories.database import DatabaseRepository
from services.report_service import ReportService


async def main():
    logger.info("A iniciar teste local do alerta da MaxWeb...")

    settings = get_settings()

    db_repo = DatabaseRepository(settings)
    report_service = ReportService(settings, db_repo)

    await report_service.generate_and_send_maxweb_refund_warning()

    logger.info("Teste local finalizado!")


if __name__ == "__main__":
    asyncio.run(main())
