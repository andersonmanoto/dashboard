"""
Configurações centralizadas da aplicação.
Usa pydantic-settings para validação e carregamento de variáveis de ambiente.
"""
from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict
import tempfile

class Settings(BaseSettings):
    """Configurações da aplicação carregadas de variáveis de ambiente."""
    
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore"
    )
    
    # API
    app_name: str = "Webhook Normalizer"
    debug: bool = False
    
    # Segurança
    webhook_secret: str
    upload_api_key: str
    
    # Supabase
    supabase_url: str
    supabase_key: str
    
    # Web Scanner
    scanner_secret_token: str
    structure_map_file: str = "structure_map.json"
    
    # Slack
    slack_bot_token: str
    slack_default_channel: str = "#None"
    slack_monitor_channel: str = "#monitor-sites"
    
    # Blacklist de codenames
    codename_blacklist_prefixes: tuple[str, ...] = ("calls", "wc")

    # Arquivos
    temp_dir: str = tempfile.gettempdir()


@lru_cache
def get_settings() -> Settings:
    """
    Retorna instância única (cached) das configurações.
    
    Returns:
        Settings: Configurações da aplicação
    """
    return Settings()