import pandas as pd
import numpy as np
from typing import Optional
from pathlib import Path
from loguru import logger
from datetime import datetime

from models.schemas import NormalizedEvent, OrderDetails, ShippingDetails
from models.enums import NetworkType, ActionType
from services.event_processor import EventProcessor
from repositories.database import DatabaseRepository

# Mapeamento Unificado
SPREADSHEET_MAPPING = {
    # --- Identificadores ---
    "Order ID": "order_id",
    "External Order ID": "external_order_id",
    "Account ID": "account_id",
    
    # --- Datas Específicas ---
    "Date Created": "created_date",     # Data da venda original
    "rr_createdate": "created_date",    # Variação de nome
    "Order Date": "created_date",       # Variação de nome
    
    "Refund Date": "refund_date_raw",       # Coluna específica de refund
    "Chargeback Date": "chargeback_date_raw", # Coluna específica de chargeback
    
    # --- Valores Financeiros ---
    "Total Collected (Transaction Amount)": "total_amount",
    "Amount": "total_amount",
    
    "Affiliate Commission Amount": "aff_commission",
    "Commission Amount": "aff_commission",
    
    "Taxes": "tax_amount",
    "Shipping Cost (Fulfillment)": "shipping_cost",
    "Payment Processing Fees": "merchant_commission",
    
    # --- Cliente ---
    "Customer Name": "customer_name",
    "Firstname": "customer_firstname",
    "Lastname": "customer_lastname",
    
    "Customer Email Address": "customer_email",
    "Customer Phone": "customer_phone",
    "Phone": "customer_phone",
    
    # --- Endereço ---
    "Address": "shipping_address",
    "City": "shipping_city",
    "State": "shipping_state",
    "Zip": "shipping_zip",
    "Country": "shipping_country",
    
    # --- Detalhes do Produto ---
    "Product Names": "product_name",
    "Product Name": "product_name",
    
    "Product Codenames": "product_codename",
    "Product Codename": "product_codename",
    
    "Affiliate ID": "aff_id",
    "Affiliate Name": "aff_name",
    
    # --- Status e Controle ---
    "Status": "status",
    "Was Canceled": "was_canceled",
    "Type": "action_source",           # "refund", "chargeback"
    "Chargeback Reason": "reason",
    "Reason": "reason",
    "Is Test": "is_test"
}

class SpreadsheetRetro:
    """
    Serviço responsável por importar planilhas de vendas, refunds e chargebacks.
    Seleciona a data correta baseada no tipo de evento.
    """

    def __init__(self, processor: EventProcessor):
        self.processor = processor

    async def process_file(self, file_path: str, network: NetworkType = NetworkType.BUYGOODS):
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"Arquivo não encontrado: {file_path}")

        logger.info(f"Iniciando importação de: {file_path}")
        
        # Início da contagem
        start_time = datetime.now()

        # 1. LOAD
        try:
            df = pd.read_csv(file_path, skiprows=3)
        except Exception:
            df = pd.read_excel(file_path, skiprows=3)

        # 2. RENAME
        cols_to_keep = list(SPREADSHEET_MAPPING.keys())
        df = df[df.columns.intersection(cols_to_keep)].copy()
        df.rename(columns=SPREADSHEET_MAPPING, inplace=True)

        # 3. CLEAN
        self._clean_dataframe(df)

        # 4. TRANSFORM & PROCESS
        success_count = 0
        error_count = 0
        skipped_count = 0

        # Converte para dict (NaN dá ruim pro json)
        records = df.replace({np.nan: None}).to_dict(orient='records')

        for row in records:
            try:
                event = self._transform_row_to_event(row, network)
                
                processed = await self.processor.process_event(event)
                
                if processed:
                    success_count += 1
                else:
                    skipped_count += 1
                
            except Exception as e:
                error_count += 1
                logger.error(f"Erro ao processar linha {row.get('order_id')}: {e}")

        # Fim da contagem e cálculo da duração
        end_time = datetime.now()
        duration = end_time - start_time

        logger.success(
            f"Importação concluída em {duration}\n"
            f"---> Processados: {success_count}\n"
            f"---> Ignorados: {skipped_count}\n"
            f"---> Falhas: {error_count}"
        )

    def _clean_dataframe(self, df: pd.DataFrame):
        """Realiza limpeza em massa no DataFrame."""
        
        # Consolida nome do cliente
        if 'customer_name' not in df.columns and 'customer_firstname' in df.columns:
            df['customer_name'] = df['customer_firstname'].fillna('') + ' ' + df.get('customer_lastname', '').fillna('')
            df['customer_name'] = df['customer_name'].str.strip()
        
        # Remove '$' e converte para float
        money_cols = ['total_amount', 'aff_commission', 'tax_amount', 'shipping_cost', 'merchant_commission']
        for col in money_cols:
            if col in df.columns:
                df[col] = df[col].astype(str).str.replace('$', '', regex=False).str.replace(',', '', regex=False)
                df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0.0)

        # Trata todas as colunas de data possíveis
        date_cols = ['created_date', 'refund_date_raw', 'chargeback_date_raw']
        for col in date_cols:
            if col in df.columns:
                # Converte para datetime
                df[f'{col}_dt'] = pd.to_datetime(df[col], errors='coerce')
        
        # Limpa Strings
        string_cols = ['order_id', 'customer_name', 'customer_email', 'product_name', 'product_codename', 'action_source', 'reason']
        for col in string_cols:
            if col in df.columns:
                df[col] = df[col].fillna("").astype(str).str.strip()

        # Normaliza boleanos
        bool_cols = ['is_test', 'was_canceled']
        for col in bool_cols:
            if col in df.columns:
                 df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0).astype(bool)

    def _get_event_datetime(self, row: dict, action: ActionType) -> tuple[str, str]:
        """
        Retorna a data e hora corretas com base na ação.
        Prioridade: Data Específica da Ação -> Data de Criação -> Agora
        """
        target_dt = None

        if action == ActionType.REFUND:
            target_dt = row.get('refund_date_raw_dt')
        elif action == ActionType.CHARGEBACK:
            target_dt = row.get('chargeback_date_raw_dt')
        
        # Fallback 1: Se não achou data específica (ou é neworder), tenta data de criação
        if pd.isnull(target_dt):
            target_dt = row.get('created_date_dt')
            
        # Fallback 2: Data atual (segurança)
        if pd.isnull(target_dt):
            target_dt = datetime.now()

        return target_dt.strftime('%Y-%m-%d'), target_dt.strftime('%H:%M:%S')

    def _transform_row_to_event(self, row: dict, network: NetworkType) -> NormalizedEvent:
        """Converte uma linha limpa do DF para o schema NormalizedEvent."""
        
        # 1. Determina Ação
        action = ActionType.NEWORDER
        
        if row.get('was_canceled'):
            action = ActionType.REFUND
            
        action_source = row.get('action_source', '').lower() if row.get('action_source') else ''
        if 'refund' in action_source:
            action = ActionType.REFUND
        elif 'chargeback' in action_source:
            action = ActionType.CHARGEBACK
        
        # 2. Determina Data
        event_date, event_time = self._get_event_datetime(row, action)

        # 3. Payload Extra
        payload = row.copy()
        # Remove objetos datetime do payload para ser JSON serializable
        keys_to_remove = [k for k in payload.keys() if k.endswith('_dt')]
        for k in keys_to_remove:
            del payload[k]
            
        if row.get('reason'):
            payload['comments'] = row.get('reason')
        
        # Mapeia datas específicas para o payload original caso o processador precise
        # E popula campos financeiros para o cálculo de LOSS
        if action == ActionType.REFUND:
             payload['date_refunded'] = f"{event_date} {event_time}"
             # Injeta refund_amount para o processador calcular o prejuízo
             payload['refund_amount'] = row.get('total_amount')
             
        elif action == ActionType.CHARGEBACK:
             payload['date_chargedback'] = f"{event_date} {event_time}"
             # Injeta total_amount_charged para o processador calcular o prejuízo
             payload['total_amount_charged'] = row.get('total_amount')
             
        elif action == ActionType.NEWORDER:
             payload['rr_createdate'] = f"{event_date} {event_time}"
             payload['total_clean'] = row.get('total_amount')
        
        # Fallback genérico para total_clean
        if 'total_clean' not in payload and row.get('total_amount'):
            payload['total_clean'] = row.get('total_amount')
        
        # Se algum campo numérico obrigatório for None/NaN, força 0.0
        sale_total = row.get('total_amount')
        if sale_total is None or pd.isna(sale_total):
            sale_total = 0.0
            
        order_details = OrderDetails(
            product_name=row.get('product_name'),
            external_checkout_code=row.get('product_codename'),
            external_affiliate_id=str(row.get('aff_id')),
            external_affiliate_name=row.get('aff_name')
        )
        
        shipping_details = ShippingDetails(
            address=row.get('shipping_address'),
            city=row.get('shipping_city'),
            state=row.get('shipping_state'),
            zip=str(row.get('shipping_zip')),
            country=row.get('shipping_country')
        )

        return NormalizedEvent(
            network=network,
            order_id=str(row.get('order_id')),
            action_type=action,
            event_date=event_date,
            event_time=event_time,
            sale_total=float(sale_total),
            aff_commission=float(row.get('aff_commission') or 0.0),
            tax_amount=float(row.get('tax_amount') or 0.0),
            shipping_cost=float(row.get('shipping_cost') or 0.0),
            merchant_commission=float(row.get('merchant_commission') or 0.0),
            customer_name=row.get('customer_name'),
            customer_email=row.get('customer_email'),
            customer_phone=str(row.get('customer_phone', '')),
            is_test=bool(row.get('is_test', False)),
            order_details=order_details,
            shipping_details=shipping_details,
            payload=payload
        )
    
# Helper para o retro em background
async def run_retro_background(file_path: str, db_repo: DatabaseRepository):
    """
    Função wrapper para rodar o retro em background e limpar o arquivo depois.
    """
    path_obj = Path(file_path)
    try:
        from services.event_processor import EventProcessor 
        from models.enums import NetworkType

        processor = EventProcessor(db_repo, slack_service=None) 
        retro = SpreadsheetRetro(processor)
        
        logger.info(f"⏳ Background Task: Iniciando processamento de {file_path}")
        await retro.process_file(file_path, network=NetworkType.BUYGOODS)
        logger.info("✅ Background Task: Processamento finalizado")
        
    except Exception as e:
        logger.exception(f"❌ Background Task Falhou: {e}")
        
    finally:
        if path_obj.exists():
            try:
                path_obj.unlink()
                logger.debug(f"Arquivo temporário removido: {file_path}")
            except OSError as e:
                logger.warning(f"Não foi possível remover arquivo: {e}")