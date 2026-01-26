import sys
import os
import asyncio
import json
import pandas as pd
from loguru import logger

# --- SETUP DE CAMINHO ---
# Adiciona a pasta raiz ao path para encontrar o módulo 'app'
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from models.enums import NetworkType, ActionType
from services.retro_service import SpreadsheetImporter, SPREADSHEET_MAPPING
from services.event_processor import EventProcessor

# Mock do EventProcessor para não precisar de banco de dados
class MockProcessor(EventProcessor):
    def __init__(self):
        pass

    async def process_event(self, event):
        # Apenas retorna o evento para inspeção visual
        return event

async def debug_file(file_path: str):
    if not os.path.exists(file_path):
        logger.error(f"Arquivo não encontrado: {file_path}")
        return

    logger.info(f"🔍 DEBUGGING: {file_path}")
    
    # Instancia o Importer com o MockProcessor
    processor = MockProcessor()
    importer = SpreadsheetImporter(processor)
    
    # --- 1. LOAD (Cópia da lógica do service) ---
    try:
        df = pd.read_csv(file_path, skiprows=3)
    except Exception:
        df = pd.read_excel(file_path, skiprows=3)

    cols_to_keep = list(SPREADSHEET_MAPPING.keys())
    df = df[df.columns.intersection(cols_to_keep)].copy()
    df.rename(columns=SPREADSHEET_MAPPING, inplace=True)
    
    # --- 2. CLEAN ---
    importer._clean_dataframe(df)
    
    records = df.to_dict(orient='records')
    logger.info(f"Encontrados {len(records)} registros. Mostrando os primeiros 3...")
    
    # --- 3. TRANSFORM & PRINT ---
    for i, row in enumerate(records[:3]): # Mostra apenas os 3 primeiros para não poluir
        try:
            event = importer._transform_row_to_event(row, NetworkType.BUYGOODS)
            
            # Converte para dict e serializa datas para string para imprimir bonito
            event_dict = event.model_dump()
            
            print(f"\n--- EVENTO {i+1} ({event.action_type}) ---")
            print(json.dumps(event_dict, indent=2, default=str))
            
            # Validações Rápidas de Integridade
            if event.action_type == ActionType.REFUND:
                if not event_dict.get('payload', {}).get('date_refunded'):
                     logger.warning("⚠️ ALERTA: Refund sem 'date_refunded' no payload!")
                else:
                     logger.success(f"✅ Data Refund OK: {event_dict['payload']['date_refunded']}")

            elif event.action_type == ActionType.CHARGEBACK:
                if not event_dict.get('payload', {}).get('date_chargedback'):
                     logger.warning("⚠️ ALERTA: Chargeback sem 'date_chargedback' no payload!")
                else:
                     logger.success(f"✅ Data Chargeback OK: {event_dict['payload']['date_chargedback']}")
            
            if event.action_type in [ActionType.REFUND, ActionType.CHARGEBACK]:
                 logger.info(f"💰 Valor afetado (loss): {event_dict.get('payload', {}).get('total_amount_charged') or event_dict.get('payload', {}).get('refund_amount')}")

        except Exception as e:
            logger.error(f"Erro ao transformar linha {i}: {e}")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Uso: python scripts/debug_importer.py <caminho_do_arquivo.xlsx>")
    else:
        asyncio.run(debug_file(sys.argv[1]))