from datetime import datetime

from loguru import logger
from models.enums import NetworkType


def parse_date(date_str: str, network: NetworkType) -> tuple[str | None, str | None]:
    """
    Converte strings de data de diferentes redes para o formato padrão do banco.

    Analisa o formato específico de cada rede (BuyGoods usa 'YYYY-MM-DD HH:MM:SS',
    DigiStore24 usa ISO format) e separa em data e hora.

    Args:
        date_str (str): A string de data bruta recebida no webhook.
        network (NetworkType): A rede de origem para aplicar a máscara correta.

    Returns:
        tuple[str | None, str | None]: Par (YYYY-MM-DD, HH:MM:SS) ou (None, None).
    """
    # PagAmerican usa "1970-01-01 00:00:00" como placeholder de campo vazio
    # (ex: createdAt/approvedDate em payloads de refund), igual ao
    # "0000-00-00 00:00:00" da BuyGoods.
    if not date_str or date_str in ("0000-00-00 00:00:00", "1970-01-01 00:00:00"):
        return None, None

    try:
        dt = None

        if network in (NetworkType.BUYGOODS, NetworkType.PAGAMERICAN):
            dt = datetime.strptime(date_str, "%Y-%m-%d %H:%M:%S")
        elif network == NetworkType.DIGISTORE24:
            dt = datetime.fromisoformat(date_str)

        if dt:
            return dt.strftime("%Y-%m-%d"), dt.strftime("%H:%M:%S")

    except Exception as e:
        logger.error(f"Erro ao converter data '{date_str}' ({network}): {e}")

    return None, None
