"""
Serviço para envio de notificações via Slack e log de erros no banco.
"""
from typing import Optional, Union
from loguru import logger
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

from config import Settings
from models.enums import NetworkType
from models.schemas import MissingCodename
from repositories.database import DatabaseRepository

class SlackService:
    """Gerencia notificações via Slack e log de erros."""
    
    def __init__(self, settings: Settings, db_repo: DatabaseRepository):
        """
        Inicializa cliente do Slack e repositório.
        """
        self.client = WebClient(token=settings.slack_bot_token)
        self.default_channel = settings.slack_default_channel
        self.monitor_channel = settings.slack_monitor_channel
        self.blacklist_prefixes = settings.codename_blacklist_prefixes
        self.db = db_repo  # Injeção do repositório
        
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
        """
        Notifica quando codename não é encontrado e salva no banco.
        """
        # Ignora codenames em blacklist
        if self._is_blacklisted(codename):
            logger.info(
                f"Codename ignorado por blacklist: {codename} "
                f"(order_id={order_id})"
            )
            return False
        
        # 1. Salva no Banco de Dados (NOVO)
        self._log_missing_codename_db(
            network, order_id, product, codename, account_id, buy_url
        )
        
        # 2. Prepara mensagem Slack
        blocks = self._build_codename_not_found_blocks(
            network=network,
            order_id=order_id,
            product=product,
            codename=codename,
            account_id=account_id,
            buy_url=buy_url
        )
        
        # 3. Envia para Slack
        target_channel = channel or self.monitor_channel
        return self._send_message(
            channel=target_channel,
            text="Codename não encontrado",
            blocks=blocks
        )
    
    def _log_missing_codename_db(
        self, 
        network, 
        order_id, 
        product, 
        codename, 
        account_id, 
        buy_url
    ) -> None:
        """Helper para registrar o erro no Supabase."""
        try:
            network_val = (
                network.value 
                if isinstance(network, NetworkType) 
                else str(network)
            )
            
            entry = MissingCodename(
                network=network_val,
                order_id=order_id,
                product_name=product,
                codename=codename or "N/A",
                account_id=account_id,
                buy_url=buy_url
            )
            
            self.db.register_missing_codename(entry)
            
        except Exception as e:
            logger.error(f"Falha ao logar missing codename no DB: {e}")

    def _is_blacklisted(self, codename: Optional[str]) -> bool:
        """Verifica se codename está na blacklist."""
        if not codename:
            return False
        
        codename_lower = codename.lower()
        return any(
            codename_lower.startswith(prefix) 
            for prefix in self.blacklist_prefixes
        )
    
    def _build_codename_not_found_blocks(
        self,
        network: Union[NetworkType, str],
        order_id: str,
        product: str,
        codename: Optional[str],
        account_id: Optional[str] = None,
        buy_url: Optional[str] = None
    ) -> list[dict]:
        """Constrói blocos formatados para mensagem Slack."""
        codename_display = codename or "N/A"
        account_display = account_id or "N/A"
        
        network_name = (
            network.value 
            if isinstance(network, NetworkType) 
            else str(network)
        )
        
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

        if account_id:
            fields.append({
                "type": "mrkdwn",
                "text": f"*Account ID:*\n`{account_display}`"
            })

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
                        "text": "Codename não encontrado. Registrado em 'missing_codenames'."
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