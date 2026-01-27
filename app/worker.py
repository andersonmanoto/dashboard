import sys
import os
import time
import signal
import asyncio
from loguru import logger

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from config import get_settings
from repositories.database import DatabaseRepository
from services.normalizer import PayloadNormalizer
from services.event_processor import EventProcessor
from services.slack_service import SlackService
from models.enums import NetworkType

# Controle de loop
RUNNING = True

def handle_signal(signum, frame):
    """Captura sinais de parada (Ctrl+C, Docker stop, Systemctl stop)"""
    global RUNNING
    logger.warning("Sinal de parada recebido. Terminando tarefas pendentes...")
    RUNNING = False

# Registra os sinais do Linux
signal.signal(signal.SIGINT, handle_signal)
signal.signal(signal.SIGTERM, handle_signal)

async def process_single_webhook(db_repo, normalizer, processor, item):
    """
    Função auxiliar para processar UM item de forma isolada.
    """
    inbox_id = item['id']
    try:
        # Executa operações de banco (síncronas) em uma thread separada
        await asyncio.to_thread(db_repo.update_inbox_status, inbox_id, "processing")

        network_str = item['network']
        payload = item['payload']

        # Normalização
        if network_str == NetworkType.BUYGOODS.value:
            network = NetworkType.BUYGOODS
        elif network_str == NetworkType.DIGISTORE24.value:
            network = NetworkType.DIGISTORE24
        else:
            raise ValueError(f"Rede desconhecida: {network_str}")

        normalized_event = normalizer.normalize(network, payload)
        success = await processor.process_event(normalized_event)
        
        status = "processed" if success else "processed"
        msg = None if success else "Ignored/Duplicate"

        await asyncio.to_thread(db_repo.update_inbox_status, inbox_id, status, msg)
        
    except Exception as e:
        logger.error(f"❌ Falha no webhook {inbox_id}: {e}")
        await asyncio.to_thread(db_repo.update_inbox_status, inbox_id, "failed", str(e))

async def run_worker():
    logger.info("🚀 Worker Async V2 iniciado. Aguardando webhooks...")
    
    settings = get_settings()
    db_repo = DatabaseRepository(settings)
    slack = SlackService(settings, db_repo)
    normalizer = PayloadNormalizer()
    processor = EventProcessor(db_repo, slack_service=slack)

    while RUNNING:
        try:
            # 1. Busca Pendentes (thread separada)
            pendings = await asyncio.to_thread(db_repo.fetch_pending_webhooks, limit=10)
            
            if not pendings:
                # Sleep não bloqueante
                await asyncio.sleep(5) 
                continue
            
            logger.info(f"⚡ Disparando processamento paralelo de {len(pendings)} webhooks...")

            # Lista de Tarefas (thread)
            tasks = [
                process_single_webhook(db_repo, normalizer, processor, item)
                for item in pendings
            ]
            
            # O gather espera todos terminarem. 
            await asyncio.gather(*tasks)
            
        except Exception as main_e:
            logger.exception(f"Erro no loop do worker: {main_e}")
            await asyncio.sleep(5)

    logger.info("👋 Worker finalizado.")

if __name__ == "__main__":
    asyncio.run(run_worker())