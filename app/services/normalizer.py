"""
Serviço responsável por normalizar payloads de diferentes redes
para um formato unificado.
"""
from typing import Any
from loguru import logger

from models.schemas import (
    NormalizedEvent, 
    OrderDetails, 
    ShippingDetails
)
from models.enums import NetworkType, ActionType, DATE_FIELD_MAPPING
from utils.date_utils import parse_date, safe_float


class PayloadNormalizer:
    """Normaliza payloads de diferentes redes para formato unificado."""
    
    def normalize(
        self, 
        network: NetworkType, 
        payload: dict[str, Any]
    ) -> NormalizedEvent:
        """
        Normaliza payload de acordo com a rede.
        
        Args:
            network: Rede de origem
            payload: Dados brutos do webhook
            
        Returns:
            NormalizedEvent: Evento normalizado
            
        Raises:
            ValueError: Se payload estiver inválido
        """
        if not payload:
            raise ValueError(f"Payload vazio recebido de {network}")
        
        order_id = self._extract_order_id(network, payload)
        if not order_id:
            raise ValueError(f"Campo 'order_id' obrigatório ausente em {network}")
        
        logger.info(f"⚙️ Normalizando: {network} | Order: {order_id}")
        
        if network == NetworkType.BUYGOODS:
            return self._normalize_buygoods(payload, order_id)
        elif network == NetworkType.DIGISTORE24:
            return self._normalize_digistore24(payload, order_id)
        else:
            raise ValueError(f"Rede desconhecida: {network}")
    
    def _extract_order_id(self, network: NetworkType, payload: dict) -> str | None:
        """Extrai order_id do payload."""
        order_id = payload.get("order_id_global") or payload.get("order_id")
        return str(order_id) if order_id else None
    
    def _parse_is_test(self, payload: dict, field: str = "is_test") -> bool:
        """Converte campo is_test para booleano."""
        raw_value = payload.get(field, "0")
        return str(raw_value) in ("1", "true", "True", "yes")
    
    # ========== BUYGOODS ==========
    
    def _normalize_buygoods(
        self, 
        payload: dict, 
        order_id: str
    ) -> NormalizedEvent:
        """Normaliza payload da BuyGoods."""
        
        action_type = ActionType(payload.get("action_type", "sale"))
        
        # Determina campo de data baseado no action_type
        date_field = self._get_buygoods_date_field(action_type, payload)
        event_date, event_time = parse_date(
            payload.get(date_field, ""), 
            NetworkType.BUYGOODS
        )
        
        # Nome completo do cliente
        customer_name = self._build_full_name(
            payload.get("name"),
            payload.get("customer_firstname"),
            payload.get("customer_lastname")
        )
        
        # Cálculo de merchant commission rate
        merchant_commission = safe_float(payload.get("merchant_commission"))
        total_clean = safe_float(payload.get("total_clean"))
        merchant_rate = (
            round(merchant_commission / total_clean, 2) 
            if total_clean > 0 else 0.0
        )
        
        return NormalizedEvent(
            network=NetworkType.BUYGOODS,
            order_id=order_id,
            action_type=action_type,
            account_id=payload.get("account_id"),
            event_date=event_date,
            event_time=event_time,
            
            # Cliente
            customer_name=customer_name,
            customer_email=payload.get("customer_emailaddress"),
            customer_phone=payload.get("customer_phone"),
            
            # Financeiro
            currency=payload.get("currency"),
            sale_total=safe_float(payload.get("total_clean")),
            product_price=safe_float(payload.get("product_price")),
            aff_commission=safe_float(payload.get("aff_commission")),
            tax_amount=safe_float(payload.get("taxes")),
            merchant_commission=merchant_commission,
            merchant_commission_rate=merchant_rate,
            shipping_cost=safe_float(payload.get("shipping_cost")),
            
            # Pagamento
            payment_method=payload.get("payment_method"),
            payment_cardtype=payload.get("payment_cardtype"),
            
            # Tracking
            click_id=payload.get("subid"),
            sub_tiger_2=payload.get("subid2"),
            sub_tiger_3=payload.get("subid3"),
            sub_tiger_4=payload.get("subid4"),
            sub_tiger_5=payload.get("subid5"),
            
            # Flags
            is_upsell=str(payload.get("flag_upsell")) == "1",
            is_test=self._parse_is_test(payload),
            
            # Outros
            lang=payload.get("lang"),
            sale_url=payload.get("salespage_url"),
            
            # Detalhes
            order_details=OrderDetails(
                external_product_id=payload.get("product_id"),
                external_checkout_code=payload.get("product_codename"),
                external_affiliate_id=payload.get("aff_id"),
                external_affiliate_name=payload.get("aff_name"),
                product_name=payload.get("product_name"),
                sku=payload.get("sku"),
                funnel_codename=payload.get("funnel_codename")
            ),
            
            shipping_details=ShippingDetails(
                address=payload.get("address"),
                city=payload.get("city"),
                state=payload.get("state"),
                zip=payload.get("zip"),
                country=payload.get("country")
            ),
            
            payload=payload
        )
    
    def _get_buygoods_date_field(
        self, 
        action_type: ActionType, 
        payload: dict
    ) -> str:
        """Determina qual campo de data usar para BuyGoods."""
        mapping = DATE_FIELD_MAPPING.get(NetworkType.BUYGOODS, {})
        
        # Verifica se há campo específico para o action_type
        field = mapping.get(action_type)
        if field and payload.get(field):
            return field
        
        # Fallback para campo padrão
        return mapping.get("default", "rr_createdate")
    
    def _build_full_name(
        self, 
        full_name: str | None, 
        first_name: str | None, 
        last_name: str | None
    ) -> str:
        """Constrói nome completo do cliente."""
        if full_name:
            return full_name
        
        parts = [first_name or "", last_name or ""]
        return " ".join(filter(None, parts)).strip()
    
    # ========== DIGISTORE24 ==========
    
    def _normalize_digistore24(
        self, 
        payload: dict, 
        order_id: str
    ) -> NormalizedEvent:
        """Normaliza payload da DigiStore24."""
        
        event_date, event_time = parse_date(
            payload.get("datetime_full", ""), 
            NetworkType.DIGISTORE24
        )
        
        customer_name = self._build_full_name(
            None,
            payload.get("first_name"),
            payload.get("last_name")
        )
        
        # DigiStore pode ter campo específico is_test_payment
        is_test = (
            self._parse_is_test(payload, "is_test_payment") 
            or self._parse_is_test(payload)
        )
        
        return NormalizedEvent(
            network=NetworkType.DIGISTORE24,
            order_id=order_id,
            action_type=ActionType(payload.get("transaction_type", "sale")),
            event_date=event_date,
            event_time=event_time,
            
            # Cliente
            customer_name=customer_name,
            customer_email=payload.get("email"),
            
            # Financeiro
            currency=payload.get("currency"),
            sale_total=safe_float(payload.get("amount_brutto")),
            aff_commission=safe_float(payload.get("amount_affiliate")),
            tax_amount=safe_float(payload.get("taxes")),
            
            # Tracking
            click_id=payload.get("cid"),
            sub_tiger_2=payload.get("sid2"),
            sub_tiger_3=payload.get("sid3"),
            sub_tiger_4=payload.get("sid4"),
            sub_tiger_5=payload.get("sid5"),
            
            # Flags
            is_upsell=payload.get("order_type") == "upsell",
            is_test=is_test,
            
            # Detalhes
            order_details=OrderDetails(
                external_product_id=payload.get("product_id"),
                external_affiliate_id=payload.get("affiliate_id"),
                external_affiliate_name=payload.get("affiliate_name"),
                product_name=payload.get("product_name"),
                billing_type=payload.get("billing_type"),
                merchant_id=payload.get("merchant_id")
            ),
            
            shipping_details=ShippingDetails(
                country=payload.get("country")
            ),
            
            payload=payload
        )