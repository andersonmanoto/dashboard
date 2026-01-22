"""
Serviço para envio de notificações via Slack.
"""
import json
from pathlib import Path
from typing import Optional, Union
from loguru import logger
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

from config import Settings
from models.enums import NetworkType


class SlackService:
    """Gerencia notificações via Slack."""
    
    def __init__(self, settings: Settings):
        """
        Inicializa cliente do Slack.
        """
        self.client = WebClient(token=settings.slack_bot_token)
        self.default_channel = settings.slack_default_channel
        self.monitor_channel = settings.slack_monitor_channel
        self.blacklist_prefixes = settings.codename_blacklist_prefixes
        self.codenames_file = Path(settings.codenames_file)
        
        logger.info("Cliente do Slack inicializado com sucesso")
    
    def notify_codename_not_found(
        self,
        network: Union[NetworkType, str],
        order_id: str,
        product: str,
        codename: Optional[str],
        account_id: Optional[str] = None,
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
            account_id=account_id
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
        account_id: Optional[str] = None
    ) -> list[dict]:
        """Constrói blocos formatados para mensagem Slack."""
        codename_display = codename or "N/A"
        account_display = account_id or "N/A"
        print(account_display)
        
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
                        "text": "⚠️ codename não encontrado na tabela `checkouts`"
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