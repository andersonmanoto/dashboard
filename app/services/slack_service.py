"""
Serviço para envio de notificações via Slack.
"""
import json
from typing import Optional, Union
from loguru import logger
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

from config import Settings
from models.enums import NetworkType

from repositories.database import DatabaseRepository
from models.schemas import MissingCodename 


class SlackService:
    """Gerencia notificações via Slack."""
    
    def __init__(self, settings: Settings, db_repo: DatabaseRepository):
        """ Inicializa o Slack Service """
        self.client = WebClient(token=settings.slack_bot_token)
        self.default_channel = settings.slack_default_channel
        self.monitor_channel = settings.slack_monitor_channel
        self.blacklist_prefixes = settings.codename_blacklist_prefixes
        self.db = db_repo
        
        logger.info("Cliente do Slack inicializado com sucesso")

    def notify_codename_not_found(
        self,
        network: Union[NetworkType, str],
        order_id: str,
        product: str,
        codename: Optional[str],
        account_id: Optional[str] = None,
        buy_url: Optional[str] = None,
        channel: Optional[str] = None
    ) -> bool:
        """Notifica Slack e persiste no Banco de Dados."""
        
        if self._is_blacklisted(codename):
            return False
        
        # 1. Salva no Supabase
        self._log_missing_codename_db(
            network, order_id, product, codename, account_id, buy_url
        )
        
        # 2. Notifica Slack
        blocks = self._build_codename_not_found_blocks(
            network, order_id, product, codename, account_id, buy_url
        )
        target_channel = channel or self.monitor_channel
        
        return self._send_message(target_channel, "Codename não encontrado", blocks)

    def _log_missing_codename_db(self, network, order_id, product, codename, account_id, buy_url):
        """Helper para salvar no banco."""
        network_val = network.value if isinstance(network, NetworkType) else str(network)
        
        entry = MissingCodename(
            network=network_val,
            order_id=order_id,
            product_name=product,
            codename=codename or "N/A",
            account_id=account_id,
            buy_url=buy_url
        )
        self.db.register_missing_codename(entry)
    
    def notify_codename_not_found(
        self,
        network: Union[NetworkType, str],
        order_id: str,
        product: str,
        codename: Optional[str],
        account_id: Optional[str] = None,
        buy_url: Optional[str] = None,
        channel: Optional[str] = None
    ) -> bool:
        """
        Notifica quando codename não é encontrado na tabela checkouts.
        """
        # Ignora codenames em blacklist
        if self._is_blacklisted(codename):
            logger.info(
                f"Codename ignorado por blacklist: {codename} "
                f"(order_id={order_id})"
            )
            return False
        
        # Salva no log local
        self._log_codename_not_found(order_id, codename, product)
        
        # Prepara mensagem
        blocks = self._build_codename_not_found_blocks(
            network=network,
            order_id=order_id,
            product=product,
            codename=codename,
            account_id=account_id,
            buy_url=buy_url
        )
        
        # Envia para Slack
        target_channel = channel or self.monitor_channel
        return self._send_message(
            channel=target_channel,
            text="Codename não encontrado",
            blocks=blocks
        )
    
    def _is_blacklisted(self, codename: Optional[str]) -> bool:
        """Verifica se codename está na blacklist."""
        if not codename:
            return False
        
        codename_lower = codename.lower()
        return any(
            codename_lower.startswith(prefix) 
            for prefix in self.blacklist_prefixes
        )
    
    def _log_codename_not_found(
        self,
        order_id: str,
        codename: Optional[str],
        product: str
    ) -> None:
        """
        Salva codename não encontrado em arquivo JSON local.
        """
        entry = {
            order_id: {
                "codename": codename or "N/A",
                "product": product
            }
        }
        
        # Cria arquivo se não existir
        if not self.codenames_file.exists():
            self.codenames_file.write_text(
                json.dumps(entry, indent=2, ensure_ascii=False),
                encoding="utf-8"
            )
            return
        
        # Carrega dados existentes
        try:
            data = json.loads(
                self.codenames_file.read_text(encoding="utf-8")
            )
        except json.JSONDecodeError:
            logger.error(
                f"Arquivo {self.codenames_file} corrompido. Recriando."
            )
            data = {}
        
        # Atualiza e salva
        data.update(entry)
        self.codenames_file.write_text(
            json.dumps(data, indent=2, ensure_ascii=False),
            encoding="utf-8"
        )
    
    def _build_codename_not_found_blocks(
        self,
        network: Union[NetworkType, str],
        order_id: str,
        product: str,
        codename: Optional[str],
        account_id: Optional[str] = None,
        buy_url: Optional[str] = None  # <--- RECEBENDO A URL
    ) -> list[dict]:
        """Constrói blocos formatados para mensagem Slack."""
        codename_display = codename or "N/A"
        account_display = account_id or "N/A"
        
        network_name = (
            network.value 
            if isinstance(network, NetworkType) 
            else str(network)
        )
        
        # Lista básica de campos
        fields = [
            {
                "type": "mrkdwn",
                "text": f"*Product:*\n`{product}`"
            },
            {
                "type": "mrkdwn",
                "text": f"*Codename:*\n`{codename_display}`"
            }
        ]

        # Adiciona Account ID se existir
        if account_id:
            fields.append({
                "type": "mrkdwn",
                "text": f"*Account ID:*\n`{account_display}`"
            })

        # Adiciona Buy URL se existir
        if buy_url:
            fields.append({
                "type": "mrkdwn",
                "text": f"*Buy URL:*\n<{buy_url}|Link>"
            })

        return [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": f"{network_name} - {order_id}:"
                }
            },
            {
                "type": "section",
                "fields": fields
            },
            {"type": "divider"},
            {
                "type": "context",
                "elements": [
                    {
                        "type": "mrkdwn",
                        "text": "Codename não encontrado na tabela 'checkouts'."
                    }
                ]
            }
        ]
    
    def _send_message(
        self,
        channel: str,
        text: str,
        blocks: list[dict]
    ) -> bool:
        """
        Envia mensagem para o Slack.
        """
        try:
            response = self.client.chat_postMessage(
                channel=channel,
                text=text,
                blocks=blocks
            )
            return response["ok"]
            
        except SlackApiError as e:
            logger.error(
                f"Erro ao enviar mensagem para Slack: "
                f"{e.response['error']}"
            )
            return False