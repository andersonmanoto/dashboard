import os
import re
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

    # Supabase
    supabase_url: str
    supabase_key: str

    # Redis
    redis_host: str = "redis"
    redis_port: int = 6379

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

    # AutoPages (projeto Supabase separado)
    autopages_supabase_url: str = ""
    autopages_supabase_service_role: str = ""


def get_slicktext_api_key(brand_id: str | None) -> str:
    """
    Resolve a api_key do SlickText para uma conta específica, a partir do
    brand_id (identificador estável da conta no SlickText). De propósito NÃO
    usa slicktext_accounts.name pra isso -- name é só um rótulo humano,
    editável livremente (ex: "SMS TIGER CONTIGENCIA"), e usá-lo pra derivar a
    env var quebraria a integração toda vez que alguém renomear a conta.

    Toda conta usa a mesma convenção: env var SLICKTEXT_API_KEY_<BRAND_ID em
    maiúsculo>, ex: brand_id '35016' -> SLICKTEXT_API_KEY_35016. O brand_id
    não é segredo e fica no banco (slicktext_accounts.brand_id), só a
    api_key vive no .env.
    """
    if not brand_id:
        return ""
    env_suffix = re.sub(r"[^A-Za-z0-9]+", "_", brand_id).strip("_").upper()
    return os.environ.get(f"SLICKTEXT_API_KEY_{env_suffix}", "")


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
