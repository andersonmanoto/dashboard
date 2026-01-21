from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError
from loguru import logger
import json
from pathlib import Path

class SlackNotifier:
    # Prefixos de codename que devem ser ignorados
    BLACKLIST_PREFIXES = ("calls", "wc")

    def __init__(self, bot_token: str, default_channel: str | None = None):
        try:
            self.client = WebClient(token=bot_token)
            self.default_channel = default_channel
            logger.info("Cliente do Slack inicializado com sucesso")
        except Exception as e:
            logger.error(f"Falha ao inicializar o cliente do Slack: {e}")
            raise

    # ---------- PUBLIC API ----------
    def codename_not_found(
        self,
        network: str,
        order_id: str,
        product: str,
        codename: str | None,
        channel: str | None = None
    ) -> bool:
        """
        Alerta quando o codename não existe na tabela checkouts,
        respeitando blacklist de prefixos.
        """

        # ignora codenames em blacklist
        if self._is_blacklisted_codename(codename):
            logger.info(
                f"Codename ignorado por blacklist: {codename} (order_id={order_id})"
            )
            return False

        # salva log local
        self._log_codename_not_found(
            order_id=order_id,
            codename=codename,
            product=product
        )

        # monta mensagem Slack
        blocks = self._build_codename_not_found_blocks(
            network=network,
            order_id=order_id,
            product=product,
            codename=codename
        )

        # envia Slack
        return self._send(
            text="Codename não encontrado",
            blocks=blocks,
            channel=channel
        )

    # ---------- INTERNALS ----------
    def _send(self, text: str, blocks: list, channel: str | None) -> bool:
        target_channel = channel or self.default_channel
        if not target_channel:
            raise ValueError("Canal não informado e nenhum canal padrão definido.")

        try:
            response = self.client.chat_postMessage(
                channel=target_channel,
                text=text,
                blocks=blocks
            )
            return response["ok"]

        except SlackApiError as e:
            logger.error(
                f"Erro ao enviar mensagem para o Slack: {e.response['error']}"
            )
            return False

    def _is_blacklisted_codename(self, codename: str | None) -> bool:
        if not codename:
            return False
        return codename.lower().startswith(self.BLACKLIST_PREFIXES)

    def _log_codename_not_found(
        self,
        order_id: str,
        codename: str | None,
        product: str,
        file_path: str = "codenames.json"
    ) -> None:
        path = Path(file_path)

        entry = {
            order_id: {
                "codename": codename or "N/A",
                "product": product
            }
        }

        # cria o arquivo se não existir
        if not path.exists():
            with path.open("w", encoding="utf-8") as f:
                json.dump(entry, f, indent=2, ensure_ascii=False)
            return

        # carrega arquivo existente
        try:
            with path.open("r", encoding="utf-8") as f:
                data = json.load(f)

        except json.JSONDecodeError:
            logger.error("Arquivo codenames.json corrompido. Recriando.")
            data = {}

        # atualiza (sobrescreve se order_id já existir)
        data.update(entry)

        # grava novamente
        with path.open("w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    def _build_codename_not_found_blocks(
        self,
        network: str,
        order_id: str,
        product: str,
        codename: str | None
    ) -> list:
        codename_display = codename or "N/A"

        return [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": f"{network} - {order_id}:"
                }
            },
            {
                "type": "section",
                "fields": [
                    {
                        "type": "mrkdwn",
                        "text": f"*Product:*\n`{product}`"
                    },
                    {
                        "type": "mrkdwn",
                        "text": f"*Codename:*\n`{codename_display}`"
                    }
                ]
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