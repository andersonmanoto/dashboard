import sys
import os
import asyncio
from loguru import logger
from dotenv import load_dotenv

# --- SETUP DE CAMINHO ---
# Adiciona a pasta raiz ao path para encontrar o módulo 'app' e o .env
ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, ROOT_DIR)

load_dotenv(os.path.join(ROOT_DIR, '.env'))

from config import get_settings
from repositories.database import DatabaseRepository
from services.retro_service import SpreadsheetImporter
from services.event_processor import EventProcessor
from models.enums import NetworkType

async def main():
    if len(sys.argv) < 2:
        logger.error("Uso: python scripts/run_import.py <caminho_do_arquivo.csv>")
        return

    file_path = sys.argv[1]
    
    try:
        # Agora o get_settings() vai encontrar as variáveis carregadas pelo load_dotenv
        settings = get_settings()
        
        # 1. Configura Dependências
        db_repo = DatabaseRepository(settings)
        
        # Criamos o EventProcessor (passamos None para o SlackService para evitar flood de notificações)
        event_processor = EventProcessor(db_repo, slack_service=None)
        
        # 2. Inicializa o Importer com o Processor
        importer = SpreadsheetImporter(event_processor)
        
        # 3. Executa Importação
        logger.info(f"Conectado ao banco. Iniciando importação do arquivo: {file_path}")
        
        # Agora usamos await pois o importer chama métodos async do processor
        await importer.process_file(file_path, network=NetworkType.BUYGOODS)
        
    except Exception as e:
        logger.exception(f"Erro fatal na execução do script: {e}")

if __name__ == "__main__":
    asyncio.run(main())