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

    # Web Scanner
    scanner_secret_token: str
    ssh_host: str
    ssh_username: str
    ssh_password: str
    ssh_port: int = 65002
    remote_script_path: str = "/home/u463185610/scripts/get_codenames"

    # Slack
    slack_bot_token: str
    slack_default_channel: str = "#None"
    slack_monitor_channel: str = "#monitor-sites"

    # Blacklist de codenames
    codename_blacklist_prefixes: tuple[str, ...] = ("calls", "wc")

    # Arquivos
    temp_dir: str = tempfile.gettempdir()

    # SlickText & AbstractAPI
    abstract_api_key: str = ""
    slicktext_api_key: str = ""
    slicktext_brand_id: str = ""
    slicktext_api_url: str = "https://api.slicktext.com/v1"


# Dicionário mapeando o nome do produto (em minúsculas) para o ID da lista no SlickText
SLICKTEXT_PRODUCT_LISTS = {
    "prostafense": 129612,
    "audileaf": 129610,
    "nervolyn": 129608,
    "breatheasex": 129605,
    "visiumpro": 135593,
}


def get_list_id_for_product(product_name: str) -> int | None:
    """Busca o ID da lista ignorando case sensitive e espaços extras."""
    if not product_name:
        return None
    normalized_name = product_name.strip().lower()
    return SLICKTEXT_PRODUCT_LISTS.get(normalized_name)


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
