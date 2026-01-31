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
