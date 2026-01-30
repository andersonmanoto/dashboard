"""
Modelos Pydantic para validação e serialização de dados.
"""
from datetime import datetime
from typing import Any, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .enums import ActionType, AffiliateStatus, NetworkType


class OrderDetails(BaseModel):
    """Detalhes do pedido."""
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
    """Detalhes de envio."""
    address: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    zip: Optional[str] = None
    country: Optional[str] = None


class NormalizedEvent(BaseModel):
    """Evento normalizado para persistência."""

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

    @field_validator('sale_total', 'product_price', 'aff_commission',
                     'tax_amount', 'merchant_commission', 'shipping_cost',
                     mode='before')
    @classmethod
    def ensure_float(cls, v: Any) -> float:
        """Garante que valores numéricos sejam float."""
        if v is None or v == "":
            return 0.0
        try:
            return float(v)
        except (ValueError, TypeError):
            return 0.0

    model_config = ConfigDict(use_enum_values=True)


class SalesStatus(BaseModel):
    """Registro de mudança de status de venda."""
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
    """Informações de checkout."""
    checkout_id: UUID
    product_id: UUID
    funnel_stage: Optional[str] = None
    funnel_number: Optional[int] = None


class Affiliate(BaseModel):
    """Modelo de afiliado."""
    id: Optional[UUID] = None
    network: NetworkType
    aff_id: str
    aff_name: str
    status: AffiliateStatus = AffiliateStatus.ACTIVE

    model_config = ConfigDict(use_enum_values=True)


class MissingCodename(BaseModel):
    """Log de codename não encontrado."""
    network: str
    order_id: str
    product_name: Optional[str] = None
    codename: Optional[str] = None
    account_id: Optional[str] = None
    buy_url: Optional[str] = None
