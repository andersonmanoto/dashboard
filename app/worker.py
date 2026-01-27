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

async def run_worker():
    logger.info("Worker iniciado. Aguardando webhooks...")
    
    settings = get_settings()
    db_repo = DatabaseRepository(settings)
    slack = SlackService(settings, db_repo)
    normalizer = PayloadNormalizer()
    # Processador completo
    processor = EventProcessor(db_repo, slack_service=slack)

    while RUNNING:
        try:
            # 1. Busca Pendentes
            pendings = db_repo.fetch_pending_webhooks(limit=10)
            
            if not pendings:
                # Se não tem nada, dorme um pouco para economizar CPU/Banco
                time.sleep(5) 
                continue
            
            logger.info(f"Processando lote de {len(pendings)} webhooks...")

            for item in pendings:
                if not RUNNING:
                    break

                inbox_id = item['id']
                network_str = item['network']
                payload = item['payload']
                
                try:
                    # Marca como 'processing'
                    db_repo.update_inbox_status(inbox_id, "processing")

                    # 2. Identifica Rede
                    network = None
                    if network_str == NetworkType.BUYGOODS.value:
                        network = NetworkType.BUYGOODS
                    elif network_str == NetworkType.DIGISTORE24.value:
                        network = NetworkType.DIGISTORE24
                    else:
                        raise ValueError(f"Rede desconhecida: {network_str}")

                    # 3. Normaliza
                    normalized_event = normalizer.normalize(network, payload)
                    
                    # 4. Processa
                    success = await processor.process_event(normalized_event)
                    
                    if success:
                        db_repo.update_inbox_status(inbox_id, "processed")
                    else:
                        db_repo.update_inbox_status(inbox_id, "processed", "Ignored/Duplicate")

                except Exception as e:
                    logger.error(f"Falha no webhook {inbox_id}: {e}")
                    db_repo.update_inbox_status(inbox_id, "failed", str(e))
            
        except Exception as main_e:
            logger.exception(f"Erro no loop principal do worker: {main_e}")
            time.sleep(5) # Dorme para não flodar log de erro

    logger.info("Worker finalizado com sucesso.")

if __name__ == "__main__":
    asyncio.run(run_worker())