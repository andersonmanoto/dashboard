"""
Serviço responsável por processar e enriquecer eventos antes de persistir.
Coordena a lógica de negócio entre normalização, enriquecimento e persistência.
"""
from typing import Optional
from uuid import UUID
from loguru import logger

from models.schemas import (
    NormalizedEvent,
    SalesStatus,
    CheckoutInfo,
    Affiliate
)
from models.enums import (
    ActionType,
    AffiliateStatus,
    TRACKABLE_ACTIONS,
    LOSS_ACTIONS,
    NetworkType
)
from repositories.database import DatabaseRepository
from services.slack_service import SlackService
from utils.date_utils import parse_date, safe_float


class EventProcessor:
    """Processa e enriquece eventos antes de salvar."""

    def __init__(
        self,
        db_repo: DatabaseRepository,
        slack_service: Optional[SlackService] = None
    ):
        self.db = db_repo
        self.slack = slack_service

    @staticmethod
    def _network_value(network):
        """Garante valor serializável de network."""
        return network.value if isinstance(network, NetworkType) else network

    @staticmethod
    def _normalize_uuid(value) -> Optional[str]:
        if not value:
            return None
        return str(value) if isinstance(value, UUID) else value
    

    async def process_event(self, event: NormalizedEvent) -> bool:
        try:
            order_id = event.order_id
            action_type = event.action_type

            # 1. Ignora cancelamentos
            if action_type == ActionType.CANCEL:
                logger.info(
                    f"action_type: CANCEL (Order: {order_id}). Ignorando evento."
                )
                return False

            # 2. Afiliado
            await self._enrich_affiliate(event)

            # 3. Checkout / produto
            await self._enrich_checkout(event)

            # 4. Ajustes por ação
            self._adjust_event_by_action_type(event)

            # 5. Persistência do evento
            saved_event = self.db.upsert_event(event)

            if not saved_event:
                logger.info(
                    f"Evento duplicado ou não salvo (Order: {order_id})"
                )
                return False

            event_id = self._normalize_uuid(saved_event.get("id"))

            if not event_id:
                logger.warning(
                    f"Evento salvo mas sem ID retornado (Order: {order_id})"
                )
                return False

            # 6. Eventos de teste não geram sales_status
            if event.is_test:
                logger.info(
                    f"Evento de TESTE detectado (Order: {order_id}). "
                    f"Ignorando sales_status."
                )
                return True

            # 7. Sales status
            if action_type in TRACKABLE_ACTIONS:
                self._create_sales_status(event, event_id)

            return True

        except Exception as e:
            logger.exception(f"Erro ao processar evento: {e}")
            return False


    async def _enrich_affiliate(self, event: NormalizedEvent) -> None:
        external_aff_id = event.order_details.external_affiliate_id
        if not external_aff_id:
            return

        network = self._network_value(event.network)

        affiliate = self.db.get_affiliate_by_external_id(
            network,
            external_aff_id
        )

        if not affiliate:
            aff_name = (
                event.order_details.external_affiliate_name
                or "Tiger Offers"
            )

            new_affiliate = Affiliate(
                network=network,
                aff_id=external_aff_id,
                aff_name=aff_name,
                status=AffiliateStatus.ACTIVE
            )

            # Blindagem contra race condition
            try:
                affiliate = self.db.create_affiliate(new_affiliate)
            except Exception:
                affiliate = self.db.get_affiliate_by_external_id(
                    network,
                    external_aff_id
                )

        if affiliate and affiliate.id:
            event.affiliate_id = affiliate.id


    async def _enrich_checkout(self, event: NormalizedEvent) -> None:
        checkout_code = event.order_details.external_checkout_code
        account_id = event.payload.get("account_id") if event.payload else None

        checkout_info: Optional[CheckoutInfo] = None

        if checkout_code:
            checkout_info = self.db.get_checkout_by_code(
                checkout_code,
                account_id
            )

            if checkout_info:
                logger.debug(
                    f"Checkout vinculado via código: {checkout_code} "
                    f"(Account: {account_id})"
                )
            else:
                logger.warning(
                    f"product_codename '{checkout_code}' "
                    f"(Account: {account_id}) não encontrado."
                )

                if self.slack:
                    self.slack.notify_codename_not_found(
                        network=self._network_value(event.network),
                        order_id=event.order_id,
                        product=event.order_details.product_name or "N/A",
                        codename=checkout_code,
                        account_id=event.account_id
                    )

        # Fallback por nome do produto
        if not checkout_info:
            product_name = event.order_details.product_name
            if product_name:
                checkout_info = self.db.find_checkout_by_product_name(
                    product_name
                )

        if checkout_info:
            event.checkout_id = checkout_info.checkout_id
            event.product_id = checkout_info.product_id
            event.funnel_stage = checkout_info.funnel_stage
            event.funnel_number = checkout_info.funnel_number


    def _adjust_event_by_action_type(self, event: NormalizedEvent) -> None:
        payload = event.payload or {}
        action_type = event.action_type

        if action_type == ActionType.REFUND:
            if event.network == NetworkType.BUYGOODS:
                refund_date_str = payload.get("date_refunded")
                if refund_date_str:
                    date, time = parse_date(refund_date_str, event.network)
                    if date:
                        event.event_date = date
                        event.event_time = time

                refund_amount = safe_float(payload.get("refund_amount"))
                if refund_amount > 0:
                    event.sale_total = refund_amount

        elif action_type == ActionType.REBILL:
            if event.network == NetworkType.BUYGOODS:
                trans_date_str = payload.get("transaction_date")
                if trans_date_str:
                    date, time = parse_date(trans_date_str, event.network)
                    if date:
                        event.event_date = date
                        event.event_time = time
                        logger.debug(
                            f"Rebill data ajustada: {date} {time}"
                        )


    def _create_sales_status(
        self,
        event: NormalizedEvent,
        event_id: str
    ) -> None:
        payload = event.payload or {}
        amount_affected = self._calculate_amount_affected(event)

        status = SalesStatus(
            event_id=event_id,
            order_id=event.order_id,
            affiliate_id=event.affiliate_id,
            product_id=event.product_id,
            network=self._network_value(event.network),
            status_type=event.action_type,
            status_reason=payload.get("comments"),
            status_date=event.event_date,
            status_time=event.event_time,
            amount_affected=amount_affected
        )

        try:
            self.db.create_sales_status(status)
        except Exception as e:
            error_str = str(e).lower()
            if "23503" in error_str or "foreign key" in error_str:
                logger.info(
                    f"Status ignorado - evento pai não persistido: "
                    f"{event.order_id}"
                )
            else:
                raise


    def _calculate_amount_affected(self, event: NormalizedEvent) -> float:
        payload = event.payload or {}
        action_type = event.action_type

        if action_type in LOSS_ACTIONS:
            if event.network == NetworkType.BUYGOODS:
                if action_type == ActionType.REFUND:
                    amount = safe_float(payload.get("refund_amount"))
                    return amount or safe_float(payload.get("total_clean"))

                if action_type == ActionType.CHARGEBACK:
                    amount = safe_float(payload.get("total_amount_charged"))
                    return amount or safe_float(payload.get("total_clean"))

            amount = safe_float(payload.get("amount"))
            return abs(amount) if amount < 0 else amount

        return event.sale_total
