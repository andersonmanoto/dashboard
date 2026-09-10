from typing import Any

from loguru import logger
from models.enums import DATE_FIELD_MAPPING, ActionType, NetworkType
from models.schemas import NormalizedEvent, OrderDetails, ShippingDetails
from utils.date_utils import parse_date
from utils.formatters import safe_float


class PayloadNormalizer:
    """
    Padroniza dados brutos de webhooks para o formato interno do sistema.

    Atua como uma fábrica que decide qual estratégia de normalização usar
    com base na rede de origem (NetworkType).
    """

    def normalize(
        self, network: NetworkType, payload: dict[str, Any]
    ) -> NormalizedEvent:
        """
        Converte o payload bruto em um evento normalizado.

        Args:
            network (NetworkType): A rede de onde veio o webhook.
            payload (dict[str, Any]): O dicionário de dados recebido.

        Returns:
            NormalizedEvent: Objeto padronizado pronto para processamento.

        Raises:
            ValueError: Se o payload estiver vazio, sem order_id ou de rede desconhecida.
        """
        if not payload:
            raise ValueError(f"Payload vazio recebido de {network}")

        order_id = self._extract_order_id(network, payload)
        if not order_id:
            raise ValueError(f"Campo 'order_id' obrigatório: {network}")

        logger.info(f"Normalizando: {network} | Order: {order_id}")

        if network == NetworkType.BUYGOODS:
            return self._normalize_buygoods(payload, order_id)
        elif network == NetworkType.DIGISTORE24:
            return self._normalize_digistore24(payload, order_id)
        elif network == NetworkType.PAGAMERICAN:
            return self._normalize_pagamerican(payload, order_id)
        else:
            raise ValueError(f"Rede desconhecida: {network}")

    def _extract_order_id(self, network: NetworkType, payload: dict) -> str | None:
        """
        Extrai o ID do pedido de forma agnóstica à rede.

        Tenta buscar 'order_id_global' (comum em agregadores), 'order_id' ou
        'orderId' (camelCase, usado pela PagAmerican).
        """
        order_id = (
            payload.get("order_id_global")
            or payload.get("order_id")
            or payload.get("orderId")
        )
        return str(order_id) if order_id else None

    def _parse_is_test(self, payload: dict, field: str = "is_test") -> bool:
        """
        Converte as várias representações de booleano (1, 'true', 'yes') para bool.
        """
        raw_value = payload.get(field, "0")
        return str(raw_value) in ("1", "true", "True", "yes")

    # ========== BUYGOODS ==========

    def _normalize_buygoods(self, payload: dict, order_id: str) -> NormalizedEvent:
        """
        Aplica regras de mapeamento específicas da BuyGoods.

        Mapeia campos financeiros, calcula taxas de comissão do merchant
        e constrói os objetos de detalhes do pedido.
        """
        action_type = ActionType(payload.get("action_type", "sale"))
        aff_name = self._sanitize_affiliate_name(payload.get("aff_name"))

        # Determina campo de data baseado no action_type
        date_field = self._get_buygoods_date_field(action_type, payload)
        event_date, event_time = parse_date(
            payload.get(date_field, ""), NetworkType.BUYGOODS
        )

        # Nome completo do cliente
        customer_name = self._build_full_name(
            payload.get("name"),
            payload.get("customer_firstname"),
            payload.get("customer_lastname"),
        )

        # Cálculo de merchant commission rate
        merchant_commission = safe_float(payload.get("merchant_commission"))
        total_clean = safe_float(payload.get("total_clean"))
        merchant_rate = (
            round(merchant_commission / total_clean, 4) if total_clean > 0 else 0.0
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
                external_affiliate_name=aff_name,
                product_name=payload.get("product_name"),
                sku=payload.get("sku"),
                funnel_codename=payload.get("funnel_codename"),
            ),
            shipping_details=ShippingDetails(
                address=payload.get("address"),
                city=payload.get("city"),
                state=payload.get("state"),
                zip=payload.get("zip"),
                country=payload.get("country"),
            ),
            payload=payload,
        )

    def _get_buygoods_date_field(self, action_type: ActionType, payload: dict) -> str:
        """
        Seleciona o campo de data correto baseado no tipo de ação.

        Ex: 'date_refunded' para reembolsos, 'transaction_date' para rebills,
        ou 'rr_createdate' como padrão.
        """
        mapping = DATE_FIELD_MAPPING.get(NetworkType.BUYGOODS, {})

        # Verifica se há campo específico para o action_type
        field = mapping.get(action_type)
        if field and payload.get(field):
            return field

        # Fallback para campo padrão
        return mapping.get("default", "rr_createdate")

    def _build_full_name(
        self, full_name: str | None, first_name: str | None, last_name: str | None
    ) -> str:
        """
        Constrói nome completo concatenando partes se necessário.
        """
        if full_name:
            return full_name

        parts = [first_name or "", last_name or ""]
        return " ".join(filter(None, parts)).strip()

    # ========== DIGISTORE24 ==========

    def _normalize_digistore24(self, payload: dict, order_id: str) -> NormalizedEvent:
        """
        Aplica regras de mapeamento específicas da DigiStore24.

        Realiza a tradução de status (ex: 'payment' -> 'neworder') e prepara
        os identificadores de produto para o processador.
        """

        aff_name = self._sanitize_affiliate_name(payload.get("affiliate_name"))

        # 1. Parse de Data
        event_date, event_time = parse_date(
            payload.get("datetime_full", ""), NetworkType.DIGISTORE24
        )

        # 2. Nome do Cliente
        customer_name = self._build_full_name(
            None, payload.get("first_name"), payload.get("last_name")
        )

        # 3. Flags (Teste e Upsell)
        # Verifica campo específico ou flag genérica
        is_test = self._parse_is_test(
            payload, "is_test_payment"
        ) or self._parse_is_test(payload)

        # A DigiStore marca upsell se 'order_type' for 'upsell' OU se 'upsell_no' for diferente de 0
        is_upsell = (
            payload.get("order_type") == "upsell"
            or str(payload.get("upsell_no", "0")) != "0"
        )

        # 4. Tradução de ActionType (Lógica Unificada V2)
        raw_type = payload.get("transaction_type", "sale").lower()

        if raw_type == "payment":
            # REGRA DEFINIDA: Payment sempre entra como NEWORDER.
            # A distinção de Upsell é feita exclusivamente pelo campo booleano is_upsell.
            action_type = ActionType.NEWORDER

        elif raw_type == "refund":
            action_type = ActionType.REFUND

        elif raw_type == "chargeback":
            action_type = ActionType.CHARGEBACK

        elif raw_type == "rebill":
            action_type = ActionType.REBILL

        else:
            # Fallback de segurança
            logger.warning(
                f"DigiStore: Tipo desconhecido '{raw_type}'. Assumindo NEWORDER."
            )
            action_type = ActionType.NEWORDER

        # 5. Identificadores de Produto
        product_id = payload.get("product_id")

        return NormalizedEvent(
            network=NetworkType.DIGISTORE24,
            order_id=order_id,
            action_type=action_type,
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
            is_upsell=is_upsell,
            is_test=is_test,
            # Detalhes
            order_details=OrderDetails(
                external_product_id=product_id,
                external_checkout_code=product_id,
                external_affiliate_id=payload.get("affiliate_id"),
                external_affiliate_name=aff_name,
                product_name=payload.get("product_name"),
                billing_type=payload.get("billing_type"),
                merchant_id=payload.get("merchant_id"),
            ),
            shipping_details=ShippingDetails(country=payload.get("country")),
            payload=payload,
        )

    # ========== PAGAMERICAN ==========

    # A PagAmerican não manda um campo tipo "action_type": o tipo do evento
    # vem no envelope ("event"), que o router injeta em payload["_pa_event"]
    # antes de enfileirar. Só os dois eventos documentados até agora estão
    # mapeados; qualquer outro estoura ValueError (job cai como "failed" na
    # inbox pra triagem, sem derrubar o worker).
    _PAGAMERICAN_EVENT_ACTION_MAP = {
        "order.purchase.created.v1": ActionType.NEWORDER,
        "refund.transaction.confirmed.v1": ActionType.REFUND,
    }

    def _normalize_pagamerican(self, payload: dict, order_id: str) -> NormalizedEvent:
        """
        Aplica regras de mapeamento específicas da PagAmerican.

        Diferenças-chave em relação à BuyGoods/DigiStore:
        - `amounts`/`commission` vêm em centavos; `refund.*` já vem em dólares.
        - Não existe aff_id/aff_name no payload (tracking é só via `clickid`) —
          por ora todo evento é atribuído ao afiliado fixo "Tiger Offers"
          (aff_id "0"), sem account_id (conceito exclusivo da BuyGoods, onde
          cada produto é uma conta separada).
        - Não há sinalização explícita de upsell -> assume front (is_upsell=False).
        - Checkout leva direto pro checkout de 1 produto só, então `products`
          sempre tem um único item.
        """
        pa_event = payload.get("_pa_event", "")
        action_type = self._PAGAMERICAN_EVENT_ACTION_MAP.get(pa_event)
        if action_type is None:
            raise ValueError(f"Evento PagAmerican desconhecido: '{pa_event}'")

        customer = payload.get("customer") or {}
        shipping = payload.get("shipping") or {}
        amounts = payload.get("amounts") or {}
        commission = payload.get("commission") or {}
        tracking = payload.get("trackingParameters") or {}
        refund = payload.get("refund") or {}

        products = payload.get("products") or []
        product = products[0] if products else {}

        # Data do evento: refund usa refundedAt; purchase usa approvedDate
        # (createdAt como fallback). Em refunds, createdAt/approvedDate vêm
        # zerados ("1970-01-01 00:00:00").
        if action_type == ActionType.REFUND:
            date_raw = payload.get("refundedAt") or payload.get("createdAt")
        else:
            date_raw = payload.get("approvedDate") or payload.get("createdAt")
        event_date, event_time = parse_date(date_raw or "", NetworkType.PAGAMERICAN)

        sale_total = safe_float(amounts.get("totalInCents")) / 100
        tax_amount = safe_float(amounts.get("taxesInCents")) / 100
        shipping_cost = safe_float(amounts.get("shippingGrossInCents")) / 100
        product_price = safe_float(product.get("priceInCents")) / 100
        aff_commission = safe_float(commission.get("userCommissionInCents")) / 100
        merchant_commission = safe_float(commission.get("gatewayFeeInCents")) / 100
        merchant_rate = (
            round(merchant_commission / sale_total, 4) if sale_total > 0 else 0.0
        )

        if action_type == ActionType.REFUND and refund.get("amountRefunded") is not None:
            # `refund.amountRefunded` já vem em dólares (não em centavos como
            # `amounts`/`commission`) e é o valor efetivamente devolvido —
            # mais preciso que `amounts.totalInCents` pra refunds parciais.
            sale_total = safe_float(refund.get("amountRefunded"))

        return NormalizedEvent(
            network=NetworkType.PAGAMERICAN,
            order_id=order_id,
            action_type=action_type,
            event_date=event_date,
            event_time=event_time,
            # Cliente
            customer_name=customer.get("name"),
            customer_email=customer.get("email"),
            customer_phone=customer.get("phone"),
            # Financeiro
            currency=amounts.get("currency") or commission.get("currency"),
            sale_total=sale_total,
            product_price=product_price,
            aff_commission=aff_commission,
            tax_amount=tax_amount,
            merchant_commission=merchant_commission,
            merchant_commission_rate=merchant_rate,
            shipping_cost=shipping_cost,
            # Pagamento
            payment_method=payload.get("paymentMethod"),
            # Tracking (PagAmerican só recebe ?clickid={clickid} do RedTrack;
            # os demais utm_* são captados nativamente pela PagAmerican)
            click_id=tracking.get("src"),
            sub_tiger_2=tracking.get("utm_campaign"),
            sub_tiger_3=tracking.get("utm_content"),
            sub_tiger_4=tracking.get("utm_source"),
            sub_tiger_5=tracking.get("utm_term"),
            # Flags
            is_upsell=False,
            is_test=bool(payload.get("isTest")),
            # Detalhes
            order_details=OrderDetails(
                external_product_id=product.get("id"),
                external_checkout_code=product.get("offerCode"),
                external_affiliate_id="0",
                external_affiliate_name="Tiger Offers",
                product_name=product.get("name"),
                sku=product.get("sku"),
            ),
            shipping_details=ShippingDetails(
                address=shipping.get("address1"),
                city=shipping.get("city"),
                state=shipping.get("state"),
                zip=shipping.get("zipCode"),
                country=shipping.get("country"),
            ),
            payload=payload,
        )

    def _sanitize_affiliate_name(self, raw_name: str | None) -> str | None:
        """
        Padroniza nomes de afiliados baseados em regras de negócio internas.
        """
        if not raw_name:
            return raw_name

        clean_name = raw_name.strip()

        # Dicionário de conversão (De -> Para)
        name_mapping = {
            "Gestor One": "Aff TigerOffers",
            # "Outro Nome Errado": "Nome Certo",
        }

        return name_mapping.get(clean_name, clean_name)
