from typing import Optional, Union

from loguru import logger
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

from app.config import Settings
from app.models.enums import NetworkType
from app.models.schemas import MissingCodename
from app.repositories.database import DatabaseRepository


class SlackService:
    """Gerencia notificações via Slack e log de erros."""

    def __init__(self, settings: Settings, db_repo: DatabaseRepository):
        """
        Inicializa o serviço de notificações.

        Configura o cliente do Slack (WebClient) com o token do bot e
        armazena as referências de canais e repositório de dados.

        Args:
            settings (Settings): Configurações globais (tokens, canais).
            db_repo (DatabaseRepository): Repositório para logar erros críticos.
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
        channel: Optional[str] = None,
        aff_id: Optional[str] = None,
        aff_name: Optional[str] = None,
    ) -> bool:
        """
        Orquestra o tratamento de erro para produtos não mapeados.

        1. Verifica se o codename está na blacklist (ignora se estiver).
        2. Registra o erro na tabela `missing_codenames` do banco.
        3. Envia um alerta visual formatado para o canal de monitoramento do Slack.
        """
        # Ignora codenames em blacklist
        if self._is_blacklisted(codename):
            logger.info(
                f"Codename ignorado por blacklist: {codename} (order_id={order_id})"
            )
            return False

        # 1. Salva no Banco de Dados
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
            buy_url=buy_url,
            aff_id=aff_id,
            aff_name=aff_name,
        )

        # 3. Envia para Slack usando o método público
        target_channel = channel or self.monitor_channel
        return self.send_message(
            channel=target_channel, text="Codename não encontrado", blocks=blocks
        )

    def _log_missing_codename_db(
        self, network, order_id, product, codename, account_id, buy_url
    ) -> None:
        """
        Persiste o erro de codename no banco de dados para auditoria futura.
        """
        try:
            network_val = (
                network.value if isinstance(network, NetworkType) else str(network)
            )

            entry = MissingCodename(
                network=network_val,
                order_id=order_id,
                product_name=product,
                codename=codename or "N/A",
                account_id=account_id,
                buy_url=buy_url,
            )

            self.db.register_missing_codename(entry)

        except Exception as e:
            logger.error(f"Falha ao logar missing codename no DB: {e}")

    def _is_blacklisted(self, codename: Optional[str]) -> bool:
        """
        Verifica se o codename deve ser ignorado.
        """
        if not codename:
            return False

        codename_lower = codename.lower()
        return any(
            codename_lower.startswith(prefix) for prefix in self.blacklist_prefixes
        )

    def _build_codename_not_found_blocks(
        self,
        network: Union[NetworkType, str],
        order_id: str,
        product: str,
        codename: Optional[str],
        account_id: Optional[str] = None,
        buy_url: Optional[str] = None,
        aff_id: Optional[str] = None,
        aff_name: Optional[str] = None,
    ) -> list[dict]:
        """
        Gera o layout da mensagem do Slack usando Block Kit.
        """
        codename_display = codename or "N/A"
        account_display = account_id or "N/A"

        network_name = (
            network.value if isinstance(network, NetworkType) else str(network)
        )

        fields = [
            {"type": "mrkdwn", "text": f"*Product:*\n`{product}`"},
            {"type": "mrkdwn", "text": f"*Codename:*\n`{codename_display}`"},
        ]

        if account_id:
            fields.append(
                {"type": "mrkdwn", "text": f"*Account ID:*\n`{account_display}`"}
            )

        if aff_id or aff_name:
            affiliate_display = f"{aff_name or 'N/A'} (ID: {aff_id or 'N/A'})"
            fields.append(
                {"type": "mrkdwn", "text": f"*Affiliate:*\n`{affiliate_display}`"}
            )

        if buy_url:
            fields.append({"type": "mrkdwn", "text": f"*Buy URL:*\n<{buy_url}|Link>"})

        return [
            {
                "type": "header",
                "text": {"type": "plain_text", "text": f"{network_name} - {order_id}:"},
            },
            {"type": "section", "fields": fields},
            {"type": "divider"},
            {
                "type": "context",
                "elements": [
                    {
                        "type": "mrkdwn",
                        "text": (
                            "Codename não encontrado. "
                            "Registrado em 'missing_codenames'."
                        ),
                    }
                ],
            },
        ]

    def send_message(
        self,
        channel: str,
        text: str,
        blocks: list[dict],
        username: Optional[str] = None,
        icon_emoji: Optional[str] = None,
        icon_url: Optional[str] = None,  # <-- Novo parâmetro adicionado aqui
    ) -> bool:
        """
        Dispara a mensagem final para a API do Slack.
        """
        try:
            kwargs = {
                "channel": channel,
                "text": text,
                "blocks": blocks,
            }

            if username:
                kwargs["username"] = username
            if icon_emoji:
                kwargs["icon_emoji"] = icon_emoji
            if icon_url:
                kwargs["icon_url"] = icon_url

            response = self.client.chat_postMessage(**kwargs)
            return response["ok"]

        except SlackApiError as e:
            logger.error(f"Erro ao enviar mensagem para Slack: {e.response['error']}")
            return False
