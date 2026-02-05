from enum import Enum


class NetworkType(str, Enum):
    """
    Redes de Afiliados suportadas pelo sistema.

    Identifica a origem do webhook ou do arquivo importado.
    """

    BUYGOODS = "BuyGoods"
    DIGISTORE24 = "DigiStore24"


class ActionType(str, Enum):
    """
    Tipos de ação possíveis em um evento financeiro.

    Normaliza os diferentes status que as plataformas enviam (ex: sale, refund, rebill)
    para um formato único interno, facilitando o processamento de regras de negócio.
    """

    SALE = "sale"
    NEWORDER = "neworder"
    UPSELL = "upsell"
    REFUND = "refund"
    CHARGEBACK = "chargeback"
    REBILL = "rebill"
    CANCEL = "cancel"


class AffiliateStatus(str, Enum):
    """
    Status de operação de um afiliado no sistema.
    """
    ACTIVE = "active"
    INACTIVE = "inactive"

    @classmethod
    def _missing_(cls, value):
        """
        Se vier 'ativo' do banco, converte para 'active'.
        Isso evita o erro de validação do Pydantic.
        """
        if isinstance(value, str):
            # Normaliza para minúsculo
            normalized = value.lower().strip()
            
            # Mapeia Português -> Inglês
            if normalized == "ativo":
                return cls.ACTIVE
            if normalized == "inativo":
                return cls.INACTIVE
                
        return super()._missing_(value)

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

# Mapeamento de colunas de planilhas externas para o schema interno
SPREADSHEET_MAPPING = {
    # --- Identificadores ---
    "Order ID": "order_id",
    "External Order ID": "external_order_id",
    "Account ID": "account_id",
    # --- Datas Específicas ---
    "Date Created": "created_date",  # Data da venda original
    "rr_createdate": "created_date",  # Variação de nome
    "Order Date": "created_date",  # Variação de nome
    "Refund Date": "refund_date_raw",  # Coluna específica de refund
    "Chargeback Date": "chargeback_date_raw",  # Coluna específica de chargeback
    # --- Valores Financeiros ---
    "Total Collected (Transaction Amount)": "total_amount",
    "Amount": "total_amount",
    "Affiliate Commission Amount": "aff_commission",
    "Commission Amount": "aff_commission",
    "Taxes": "tax_amount",
    "Shipping Cost (Fulfillment)": "shipping_cost",
    "Payment Processing Fees": "merchant_commission",
    # --- Cliente ---
    "Customer Name": "customer_name",
    "Firstname": "customer_firstname",
    "Lastname": "customer_lastname",
    "Customer Email Address": "customer_email",
    "Customer Phone": "customer_phone",
    "Phone": "customer_phone",
    # --- Endereço ---
    "Address": "shipping_address",
    "City": "shipping_city",
    "State": "shipping_state",
    "Zip": "shipping_zip",
    "Country": "shipping_country",
    # --- Detalhes do Produto ---
    "Product Names": "product_name",
    "Product Name": "product_name",
    "Product Codenames": "product_codename",
    "Product Codename": "product_codename",
    "Affiliate ID": "aff_id",
    "Affiliate Name": "aff_name",
    # --- Status e Controle ---
    "Status": "status",
    "Was Canceled": "was_canceled",
    "Type": "action_source",  # "refund", "chargeback"
    "Chargeback Reason": "reason",
    "Reason": "reason",
    "Is Test": "is_test",
}
