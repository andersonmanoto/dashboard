"""
Funções utilitárias para manipulação de datas.
"""
from datetime import datetime

from loguru import logger
from models.enums import NetworkType


def parse_date(date_str: str, network: NetworkType) -> tuple[str | None, str | None]:
    """
    Converte string de data no formato específico da rede para date e time separados.

    Args:
        date_str: String de data no formato da rede
        network: Rede de origem (BuyGoods ou DigiStore24)

    Returns:
        Tupla (date, time) no formato (YYYY-MM-DD, HH:MM:SS) ou (None, None) se inválido

    Examples:
        >>> parse_date("2024-01-15 10:30:00", NetworkType.BUYGOODS)
        ("2024-01-15", "10:30:00")
    """
    if not date_str or date_str == "0000-00-00 00:00:00":
        return None, None

    try:
        dt = None

        if network == NetworkType.BUYGOODS:
            dt = datetime.strptime(date_str, "%Y-%m-%d %H:%M:%S")
        elif network == NetworkType.DIGISTORE24:
            dt = datetime.fromisoformat(date_str)

        if dt:
            return dt.strftime("%Y-%m-%d"), dt.strftime("%H:%M:%S")

    except Exception as e:
        logger.error(f"Erro ao converter data '{date_str}' ({network}): {e}")

    return None, None


def safe_float(value: any) -> float:
    """
    Converte valor para float de forma segura.

    Args:
        value: Valor a ser convertido

    Returns:
        Float convertido ou 0.0 se inválido

    Examples:
        >>> safe_float("123.45")
        123.45
        >>> safe_float(None)
        0.0
        >>> safe_float("")
        0.0
    """
    if value is None or value == "":
        return 0.0

    try:
        return float(value)
    except (ValueError, TypeError):
        return 0.0
