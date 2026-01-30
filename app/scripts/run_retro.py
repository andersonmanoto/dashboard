import asyncio
import sys

from config import get_settings
from loguru import logger
from models.enums import NetworkType
from repositories.database import DatabaseRepository
from services.event_processor import EventProcessor
from services.retro_service import SpreadsheetRetro


async def main():
    if len(sys.argv) < 2:
        logger.error(
            "Uso: python scripts/run_import.py <caminho_do_arquivo.csv>"
        )
        return

    file_path = sys.argv[1]

    try:
        settings = get_settings()

        # 1. Configura Dependências
        db_repo = DatabaseRepository(settings)

        # None para o SlackService para evitar flood de notificações
        event_processor = EventProcessor(db_repo, slack_service=None)

        # 2. Inicializa o Importer com o Processor
        importer = SpreadsheetRetro(event_processor)

        # 3. Executa Importação
        logger.info(f"Iniciando importação do arquivo: {file_path}")

        # Agora usamos await pois o importer chama métodos async do processor
        await importer.process_file(
            file_path,
            network=NetworkType.BUYGOODS
        )

    except Exception as e:
        logger.exception(f"Erro fatal na execução do script: {e}")

if __name__ == "__main__":
    asyncio.run(main())
