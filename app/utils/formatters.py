from typing import Optional

import pycountry
from loguru import logger


def country_to_alpha2(value: Optional[str]) -> str:
    """
    Resolve um país (nome completo ou código) pro ISO 3166-1 alpha-2.

    Necessário pra `phonenumbers.parse()`, que exige a região nesse formato
    -- passar o nome completo ("United Kingdom") faz ele estourar
    NumberParseException pra qualquer número em formato local (sem o
    código do país), já que não sabe qual prefixo de tronco assumir.
    Retorna "" se não conseguir identificar.
    """
    if not value:
        return ""

    value = value.strip()
    if len(value) == 2:
        return value.upper()

    try:
        match = pycountry.countries.search_fuzzy(value)
        return match[0].alpha_2 if match else ""
    except LookupError:
        logger.warning(f"País não reconhecido pra ISO2: '{value}'")
        return ""


def safe_float(value: any) -> float:
    """
    Converte valores instáveis (None, strings vazias, texto) para float seguro.

    Essencial para lidar com payloads financeiros onde campos como 'taxes' ou
    'shipping' podem vir vazios ou nulos em vez de 0.

    Args:
        value (any): O valor a ser convertido.

    Returns:
        float: O valor numérico ou 0.0 em caso de erro/nulo.
    """
    if value is None or value == "":
        return 0.0

    try:
        return float(value)
    except (ValueError, TypeError):
        return 0.0
