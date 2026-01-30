"""
Enums e constantes usados na aplicação.
"""

from enum import Enum


class NetworkType(str, Enum):
    """Redes suportadas pelo sistema."""

    BUYGOODS = "BuyGoods"
    DIGISTORE24 = "DigiStore24"


class ActionType(str, Enum):
    """Tipos de ação em eventos."""

    SALE = "sale"
    NEWORDER = "neworder"
    UPSELL = "upsell"
    REFUND = "refund"
    CHARGEBACK = "chargeback"
    REBILL = "rebill"
    CANCEL = "cancel"


class AffiliateStatus(str, Enum):
    """Status de afiliados."""

    ACTIVE = "active"
    INACTIVE = "inactive"


# Actions que geram entrada na tabela sales_status
TRACKABLE_ACTIONS = {
    ActionType.REFUND,
    ActionType.CHARGEBACK,
    ActionType.NEWORDER,
    ActionType.SALE,
    ActionType.UPSELL,
    ActionType.REBILL,
}

# Actions que representam perdas financeiras
LOSS_ACTIONS = {ActionType.REFUND, ActionType.CHARGEBACK}

# Mapeamento de campos de data por network e action
DATE_FIELD_MAPPING = {
    NetworkType.BUYGOODS: {
        ActionType.CHARGEBACK: "date_chargedback",
        ActionType.REFUND: "date_refunded",
        ActionType.REBILL: "transaction_date",
        "default": "rr_createdate",
    }
}
