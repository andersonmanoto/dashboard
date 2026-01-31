from datetime import datetime
from typing import Any, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .enums import ActionType, AffiliateStatus, NetworkType


class OrderDetails(BaseModel):
    """
    Estrutura para detalhes específicos do produto e oferta.

    Armazena dados brutos vindos do webhook que ajudam a identificar
    o item vendido, como SKUs, nomes externos e códigos de checkout.
    """

    external_product_id: Optional[str] = None
    external_checkout_code: Optional[str] = None
    external_affiliate_id: Optional[str] = None
    external_affiliate_name: Optional[str] = None
    product_name: Optional[str] = None
    sku: Optional[str] = None
    funnel_codename: Optional[str] = None
    billing_type: Optional[str] = None
    merchant_id: Optional[str] = None


class ShippingDetails(BaseModel):
    """
    Dados de entrega e localização do cliente.
    """

    address: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    zip: Optional[str] = None
    country: Optional[str] = None


class NormalizedEvent(BaseModel):
    """
    Evento financeiro normalizado e pronto para persistência.

    Este é o modelo principal do sistema. Ele converte os formatos heterogêneos
    das diversas redes (BuyGoods, DigiStore) em uma estrutura padrão unificada.
    Contém desde dados financeiros até informações de rastreamento (tracking).
    """

    # Identificação
    network: NetworkType
    order_id: str
    action_type: ActionType
    account_id: Optional[str] = None

    # Datas
    event_date: Optional[str] = None
    event_time: Optional[str] = None
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat())

    # Cliente
    customer_name: Optional[str] = None
    customer_email: Optional[str] = None
    customer_phone: Optional[str] = None

    # Financeiro
    currency: Optional[str] = None
    sale_total: float = 0.0
    product_price: float = 0.0
    aff_commission: float = 0.0
    tax_amount: float = 0.0
    merchant_commission: float = 0.0
    merchant_commission_rate: float = 0.0
    shipping_cost: float = 0.0

    # Pagamento
    payment_method: Optional[str] = None
    payment_cardtype: Optional[str] = None

    # Tracking
    click_id: Optional[str] = None
    sub_tiger_2: Optional[str] = None
    sub_tiger_3: Optional[str] = None
    sub_tiger_4: Optional[str] = None
    sub_tiger_5: Optional[str] = None

    # Flags
    is_upsell: bool = False
    is_test: bool = False

    # IDs vinculados
    affiliate_id: Optional[UUID] = None
    checkout_id: Optional[UUID] = None
    product_id: Optional[UUID] = None

    # Funil
    funnel_stage: Optional[str] = None
    funnel_number: Optional[int] = None

    # Outros
    lang: Optional[str] = None
    sale_url: Optional[str] = None

    # Detalhes estruturados
    order_details: OrderDetails = Field(default_factory=OrderDetails)
    shipping_details: ShippingDetails = Field(default_factory=ShippingDetails)

    # Payload original
    payload: dict[str, Any] = Field(default_factory=dict)

    @field_validator(
        "sale_total",
        "product_price",
        "aff_commission",
        "tax_amount",
        "merchant_commission",
        "shipping_cost",
        mode="before",
    )
    @classmethod
    def ensure_float(cls, v: Any) -> float:
        """
        Converte strings vazias ou nulas para 0.0 em campos monetários.

        Necessário pois algumas redes enviam strings vazias ("") em vez de 0
        para valores numéricos.
        """
        if v is None or v == "":
            return 0.0
        try:
            return float(v)
        except (ValueError, TypeError):
            return 0.0

    model_config = ConfigDict(use_enum_values=True)


class SalesStatus(BaseModel):
    """
    Registro granular de alterações no status financeiro.

    Diferente da tabela de eventos (que é um log imutável), esta estrutura
    alimenta a tabela `sales_status`, usada para calcular o "Status Atual"
    de uma venda (ex: se ela foi reembolsada, sofreu chargeback, etc.).
    """

    event_id: UUID
    order_id: str
    affiliate_id: Optional[UUID] = None
    product_id: Optional[UUID] = None
    network: NetworkType
    status_type: ActionType
    status_reason: Optional[str] = None
    status_date: Optional[str] = None
    status_time: Optional[str] = None
    amount_affected: float = 0.0

    model_config = ConfigDict(use_enum_values=True)


class CheckoutInfo(BaseModel):
    """
    DTO (Data Transfer Object) para resultado de busca de checkouts.

    Usado pelo repositório para retornar os dados encontrados na tabela
    `checkouts` (vínculo entre código do produto e etapa do funil).
    """

    checkout_id: UUID
    product_id: UUID
    funnel_stage: Optional[str] = None
    funnel_number: Optional[int] = None


class Affiliate(BaseModel):
    """
    Representação de um Afiliado no sistema.
    """

    id: Optional[UUID] = None
    network: NetworkType
    aff_id: str
    aff_name: str
    status: AffiliateStatus = AffiliateStatus.ACTIVE
    tear_id: Optional[UUID] = None

    model_config = ConfigDict(use_enum_values=True)


class MissingCodename(BaseModel):
    """
    Log de erro para produtos não mapeados (Missing Codenames).

    Usado para registrar quando um webhook chega com um código de produto
    (codename) que não existe no banco de dados, facilitando a auditoria.
    """

    network: str
    order_id: str
    product_name: Optional[str] = None
    codename: Optional[str] = None
    account_id: Optional[str] = None
    buy_url: Optional[str] = None
