from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
from loguru import logger
from models.enums import ActionType, NetworkType, SPREADSHEET_MAPPING
from models.schemas import NormalizedEvent, OrderDetails, ShippingDetails
from repositories.database import DatabaseRepository
from services.event_processor import EventProcessor


class SpreadsheetRetro:
    """
    Serviço de ingestão de dados históricos (Retroativos).

    Responsável por ler planilhas (CSV/Excel), normalizar colunas heterogêneas
    para o padrão interno e converter cada linha em um evento processável.
    Suporta a importação de Vendas, Reembolsos e Chargebacks.
    """

    def __init__(self, processor: EventProcessor):
        """
        Inicializa o serviço de importação.

        Args:
            processor (EventProcessor): Instância do processador de eventos responsável
                por salvar os dados e aplicar regras de negócio.
        """
        self.processor = processor

    async def process_file(
        self, file_path: str, network: NetworkType = NetworkType.BUYGOODS
    ):
        """
        Lê e processa um arquivo de planilha completo.

        Realiza o ciclo completo de ETL (Extract, Transform, Load):
        1. Carrega o arquivo (suporta .csv e .xlsx).
        2. Renomeia colunas para o padrão interno (SPREADSHEET_MAPPING).
        3. Limpa e tipa os dados (floats, datas, strings).
        4. Itera sobre as linhas, convertendo para eventos e persistindo via Processor.

        Args:
            file_path (str): Caminho absoluto do arquivo no disco.
            network (NetworkType): Rede de origem dos dados (padrão: BuyGoods).

        Raises:
            FileNotFoundError: Se o arquivo não existir.
        """
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
        records = df.replace({np.nan: None}).to_dict(orient="records")

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
        """
        Aplica limpeza e tipagem em massa no DataFrame (Pandas).

        Operações realizadas:
        - Consolidação de nomes (First + Last Name).
        - Limpeza de símbolos monetários ('$', ',') e conversão para float.
        - Conversão de colunas de data para objetos datetime.
        - Tratamento de nulos e espaços em strings.
        - Conversão de flags booleanas (is_test, was_canceled).

        Args:
            df (pd.DataFrame): DataFrame bruto carregado do arquivo.
        """
        # Consolida nome do cliente
        if "customer_name" not in df.columns and "customer_firstname" in df.columns:
            firstname = df["customer_firstname"].fillna("")
            lastname = df.get("customer_lastname", "").fillna("")
            df["customer_name"] = (firstname + " " + lastname).str.strip()

        # Remove '$' e converte para float
        money_cols = [
            "total_amount",
            "aff_commission",
            "tax_amount",
            "shipping_cost",
            "merchant_commission",
        ]
        for col in money_cols:
            if col in df.columns:
                df[col] = (
                    df[col]
                    .astype(str)
                    .str.replace("$", "", regex=False)
                    .str.replace(",", "", regex=False)
                )
                df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0)

        # Trata todas as colunas de data possíveis
        date_cols = ["created_date", "refund_date_raw", "chargeback_date_raw"]
        for col in date_cols:
            if col in df.columns:
                # Converte para datetime
                df[f"{col}_dt"] = pd.to_datetime(df[col], errors="coerce")

        # Limpa Strings
        string_cols = [
            "order_id",
            "customer_name",
            "customer_email",
            "product_name",
            "product_codename",
            "action_source",
            "reason",
        ]

        for col in string_cols:
            if col in df.columns:
                df[col] = df[col].fillna("").astype(str).str.strip()

        # Normaliza boleanos
        bool_cols = ["is_test", "was_canceled"]
        for col in bool_cols:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0).astype(bool)

    def _get_event_datetime(self, row: dict, action: ActionType) -> tuple[str, str]:
        """
        Define a data e hora corretas do evento baseado na ação.

        Lógica de prioridade:
        1. Se for Refund/Chargeback, tenta usar a coluna específica dessa ação.
        2. Se não houver data específica, usa a data de criação do pedido.
        3. Se tudo falhar, usa a data/hora atual (now) como fallback de segurança.

        Args:
            row (dict): Linha do dataframe contendo os dados.
            action (ActionType): O tipo de ação identificado para essa linha.

        Returns:
            tuple[str, str]: Par (Data YYYY-MM-DD, Hora HH:MM:SS).
        """
        target_dt = None

        if action == ActionType.REFUND:
            target_dt = row.get("refund_date_raw_dt")
        elif action == ActionType.CHARGEBACK:
            target_dt = row.get("chargeback_date_raw_dt")

        # Fallback 1:
        # Se não achou data específica (ou é neworder), tenta data de criação
        if pd.isnull(target_dt):
            target_dt = row.get("created_date_dt")

        # Fallback 2: Data atual (segurança)
        if pd.isnull(target_dt):
            target_dt = datetime.now()

        return target_dt.strftime("%Y-%m-%d"), target_dt.strftime("%H:%M:%S")

    def _transform_row_to_event(
        self, row: dict, network: NetworkType
    ) -> NormalizedEvent:
        """
        Converte uma linha de dados limpa em um objeto NormalizedEvent.

        Mapeia os campos do dicionário para o schema Pydantic, define o
        ActionType correto (Sale, Refund, Chargeback) e preenche os
        detalhes financeiros e de produto.

        Args:
            row (dict): Dicionário representando uma linha da planilha.
            network (NetworkType): Rede de origem.

        Returns:
            NormalizedEvent: Evento pronto para ser processado.
        """
        # 1. Determina Ação
        action = ActionType.NEWORDER

        if row.get("was_canceled"):
            action = ActionType.REFUND

        action_source = row.get("action_source", "").lower()
        if "refund" in action_source:
            action = ActionType.REFUND
        elif "chargeback" in action_source:
            action = ActionType.CHARGEBACK

        # 2. Determina Data
        event_date, event_time = self._get_event_datetime(row, action)

        # 3. Payload Extra
        payload = row.copy()
        # Remove objetos datetime do payload para ser JSON serializable
        keys_to_remove = [k for k in payload.keys() if k.endswith("_dt")]
        for k in keys_to_remove:
            del payload[k]

        if row.get("reason"):
            payload["comments"] = row.get("reason")

        if action == ActionType.REFUND:
            payload["date_refunded"] = f"{event_date} {event_time}"
            # Injeta refund_amount para o processador calcular o prejuízo
            payload["refund_amount"] = row.get("total_amount")

        elif action == ActionType.CHARGEBACK:
            payload["date_chargedback"] = f"{event_date} {event_time}"
            # Injeta total_amount_charged para calcular o prejuízo
            payload["total_amount_charged"] = row.get("total_amount")

        elif action == ActionType.NEWORDER:
            payload["rr_createdate"] = f"{event_date} {event_time}"
            payload["total_clean"] = row.get("total_amount")

        # Fallback genérico para total_clean
        if "total_clean" not in payload and row.get("total_amount"):
            payload["total_clean"] = row.get("total_amount")

        # Se algum campo numérico obrigatório for None/NaN, força 0.0
        sale_total = row.get("total_amount")
        if sale_total is None or pd.isna(sale_total):
            sale_total = 0.0

        order_details = OrderDetails(
            product_name=row.get("product_name"),
            external_checkout_code=row.get("product_codename"),
            external_affiliate_id=str(row.get("aff_id")),
            external_affiliate_name=row.get("aff_name"),
        )

        shipping_details = ShippingDetails(
            address=row.get("shipping_address"),
            city=row.get("shipping_city"),
            state=row.get("shipping_state"),
            zip=str(row.get("shipping_zip")),
            country=row.get("shipping_country"),
        )

        return NormalizedEvent(
            network=network,
            order_id=str(row.get("order_id")),
            account_id=str(row.get("account_id")) if row.get("account_id") else None,
            action_type=action,
            event_date=event_date,
            event_time=event_time,
            sale_total=float(sale_total),
            aff_commission=float(row.get("aff_commission") or 0.0),
            tax_amount=float(row.get("tax_amount") or 0.0),
            shipping_cost=float(row.get("shipping_cost") or 0.0),
            merchant_commission=float(row.get("merchant_commission") or 0.0),
            customer_name=row.get("customer_name"),
            customer_email=row.get("customer_email"),
            customer_phone=str(row.get("customer_phone", "")),
            is_test=bool(row.get("is_test", False)),
            order_details=order_details,
            shipping_details=shipping_details,
            payload=payload,
        )


# Helper para o retro em background
async def run_retro_background(file_path: str, db_repo: DatabaseRepository):
    """
    Tarefa de background para orquestrar a importação e limpeza.

    Função wrapper projetada para ser chamada pelo FastAPI BackgroundTasks.
    1. Instancia as dependências necessárias (EventProcessor, SpreadsheetRetro).
    2. Executa o processamento do arquivo.
    3. Garante a remoção do arquivo temporário do disco (cleanup), mesmo em caso de erro.

    Args:
        file_path (str): Caminho do arquivo temporário.
        db_repo (DatabaseRepository): Repositório para injetar no processador.
    """
    path_obj = Path(file_path)
    try:
        from models.enums import NetworkType
        from services.event_processor import EventProcessor

        processor = EventProcessor(db_repo, slack_service=None)
        retro = SpreadsheetRetro(processor)

        logger.info(f"Background Task: Processando: {file_path}")
        await retro.process_file(file_path, network=NetworkType.BUYGOODS)
        logger.info("✅ Background Task: Processamento finalizado")

    except Exception as e:
        logger.exception(f"Background Task Falhou: {e}")

    finally:
        if path_obj.exists():
            try:
                path_obj.unlink()
                logger.debug(f"Arquivo temporário removido: {file_path}")
            except OSError as e:
                logger.warning(f"Não foi possível remover arquivo: {e}")
