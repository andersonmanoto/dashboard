import tempfile
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    Define as configurações globais e segredos da aplicação.

    As variáveis são carregadas automaticamente do arquivo .env ou das variáveis
    de ambiente do sistema operacional. O Pydantic valida os tipos (str, bool, etc)
    na inicialização.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        frozen=True,
    )

    # API
    app_name: str = "Webhook Normalizer"
    debug: bool = False

    # Segurança
    webhook_secret: str
    upload_api_key: str
    reports_api_key: str
    funnel_sync_api_key: str = ""
    redtrack_offers_api_key: str = ""
    slicktext_setup_endpoint_key: str = ""
    # X-API-KEY do POST /slicktext/inputs (input de leads em lote, chamado pela edge)
    slicktext_input_endpoint_key: str = ""
    # Secret Key da JVZoo (My Account → Seller Settings) -- valida o cverify do JVZIPN v2
    jvzoo_secret_key: str = ""

    # Supabase
    supabase_url: str
    supabase_key: str

    # Redis
    redis_host: str = "redis"
    redis_port: int = 6379
    redis_password: str | None = None

    # Resend (Email)
    resend_api_key: str = ""
    email_from: str = "reports@dash.tigeroffers.com"
    report_target_emails: str = "andersonmano@tigeroffers.com"
    chargeback_target_emails: str = "andersonmano@tigeroffers.com"
    netrevenue_target_emails: str = "andersonmano@tigeroffers.com"
    affiliate_report_bcc_emails: str = (
        "andersonmano@tigeroffers.com,douglasferreira@tigeroffers.com"
    )

    # Web Scanner
    scanner_secret_token: str
    ssh_host: str
    ssh_username: str
    ssh_password: str
    ssh_port: int = 65002
    remote_script_path: str = "/home/u463185610/scripts/get_codenames"
    hosting_base: str = "/home/u463185610/domains"

    # Slack
    slack_bot_token: str
    slack_default_channel: str = "#None"
    slack_monitor_channel: str = "#monitor-sites"
    slack_maxweb_targets: str = "#monitor-sites"
    slack_slicktext_channel: str = "#tech-sms"  # alerta de conta SlickText desativada
    slack_offers_channel: str = "#backend-geral"  # aviso de oferta criada (/offers-rt-ap)

    # Blacklist de codenames
    codename_blacklist_prefixes: tuple[str, ...] = ("calls", "wc")

    # Arquivos
    temp_dir: str = tempfile.gettempdir()

    # SlickText & AbstractAPI
    abstract_api_key: str = ""
    slicktext_api_url: str = "https://api.slicktext.com/v1"

    # RedTrack
    redtrack_api_key: str = ""
    redtrack_user_id: str = ""
    # Data de corte (YYYY-MM-DD) do envio de conversões pro RedTrack: só
    # vendas com event_date >= essa data entram na fila. Vazio = desligado.
    # Deve ser o dia em que a ponte do terceiro for desligada -- antes disso
    # os dois enviariam o mesmo Purchase (duplicado no RedTrack).
    redtrack_conversions_send_from: str = ""
    # Lista de products.id (separados por vírgula) que NÃO enviam conversões
    # -- exceção, ex: produto que ainda não migrou. Vazio = todo produto de
    # internal_aff_ids envia.
    redtrack_conversions_excluded_product_ids: str = ""
    # Redes (NetworkType.value, separadas por vírgula) que enviam conversões.
    # DigiStore24 fica de fora até a conta de afiliado da Tiger estar no ar e
    # o IPN de afiliado validado com uma venda real.
    redtrack_conversions_networks: str = "BuyGoods"

    @property
    def redtrack_conversions_network_set(self) -> frozenset[str]:
        return frozenset(
            n.strip() for n in self.redtrack_conversions_networks.split(",") if n.strip()
        )

    @property
    def redtrack_conversions_excluded_product_id_set(self) -> frozenset[str]:
        return frozenset(
            p.strip()
            for p in self.redtrack_conversions_excluded_product_ids.split(",")
            if p.strip()
        )

    # PagAmerican
    pagamerican_api_key: str = ""

    # AutoPages (projeto Supabase separado)
    autopages_supabase_url: str = ""
    autopages_supabase_service_role: str = ""

    # Zapier (leads: novos pedidos e carrinhos abandonados)
    zapier_webhook_url: str = ""

    # AWeber (leads da PagAmerican, da BuyGoods e da DigiStore24: vendas e
    # carrinhos abandonados). Os tokens OAuth ficam na tabela aweber_oauth_tokens, não
    # aqui.
    aweber_client_id: str = ""
    aweber_client_secret: str = ""
    aweber_account_id: str = ""
    # As listas de destino são por produto (qualquer rede): tabela
    # aweber_product_lists.
    # Tags extras aplicadas a TODO lead, de qualquer rede, separadas por
    # vírgula. A tag da rede ("pagamerican"/"buygoods"/"digistore24") já vai
    # em cada lead, então isto normalmente fica vazio -- "pagamerican" aqui
    # marcaria os leads das outras redes como PagAmerican.
    aweber_default_tags: str = ""

    @property
    def aweber_enabled(self) -> bool:
        return all(
            (
                self.aweber_client_id,
                self.aweber_client_secret,
                self.aweber_account_id,
            )
        )


@lru_cache
def get_settings() -> Settings:
    """
    Retorna uma instância única (cached) das configurações.

    Utiliza `lru_cache` para evitar ler o arquivo .env a cada requisição,
    mantendo as configurações em memória após o primeiro acesso.

    Returns:
        Settings: Objeto contendo todas as configurações validadas.
    """
    return Settings()
