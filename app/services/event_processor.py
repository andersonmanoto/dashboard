from typing import Optional
from uuid import UUID

from loguru import logger
from models.enums import (
    LOSS_ACTIONS,
    TRACKABLE_ACTIONS,
    ActionType,
    AffiliateStatus,
    NetworkType,
)
from models.schemas import Affiliate, CheckoutInfo, NormalizedEvent, SalesStatus
from repositories.database import DatabaseRepository
from services.slack_service import SlackService
from utils.date_utils import parse_date
from utils.formatters import safe_float


class EventProcessor:
    """
    Enriquece eventos normalizados com dados do sistema (Checkouts, Afiliados).

    Responsável por:
    1. Vincular o evento a um afiliado existente (ou criar um novo).
    2. Identificar a etapa do funil (Checkout) baseada no código do produto.
    3. Calcular impactos financeiros (Loss, Net Revenue).
    4. Persistir a transação final no banco de dados.
    """

    def __init__(
        self, db_repo: DatabaseRepository, slack_service: Optional[SlackService] = None
    ):
        """
        Inicializa o processador.

        Args:
            db_repo (DatabaseRepository): Repositório para persistência.
            slack_service (Optional[SlackService]): Serviço de notificações (pode ser
                None em scripts de importação retroativa).
        """
        self.db = db_repo
        self.slack = slack_service

    @staticmethod
    def _network_value(network):
        """Converte Enum de rede para string serializável."""
        return network.value if isinstance(network, NetworkType) else network

    @staticmethod
    def _normalize_uuid(value) -> Optional[str]:
        """Converte UUID para string, tratando Nones."""
        if not value:
            return None
        return str(value) if isinstance(value, UUID) else value

    async def process_event(self, event: NormalizedEvent) -> bool:
        """
        Executa o fluxo principal de processamento de um evento.

        Args:
            event (NormalizedEvent): O evento já normalizado.

        Returns:
            bool: True se processado e salvo com sucesso, False caso contrário.
        """
        try:
            order_id = event.order_id

            # 1. Ignora cancelamentos
            if event.action_type == ActionType.CANCEL:
                logger.info(f"action_type: CANCEL (Order: {order_id}).")
                return False

            # 2. Enriquecimento de Dados
            await self._enrich_affiliate(event)
            await self._enrich_checkout(event)

            # Ajustes de datas para Refund/Rebill
            self._adjust_event_by_action_type(event)

            # 3. Preparação do Sales Status
            sales_status_obj = None

            # Eventos de TESTE não geram impacto financeiro (sales_status)
            if event.is_test:
                logger.info(
                    f"Evento de TESTE detectado (Order: {order_id}). "
                    f"Status financeiro será ignorado."
                )

            # Apenas ações rastreáveis geram sales_status
            elif event.action_type in TRACKABLE_ACTIONS:
                payload = event.payload or {}
                amount_affected = self._calculate_amount_affected(event)

                sales_status_obj = SalesStatus(
                    event_id=UUID("00000000-0000-0000-0000-000000000000"),
                    order_id=order_id,
                    affiliate_id=event.affiliate_id,
                    product_id=event.product_id,
                    network=self._network_value(event.network),
                    status_type=event.action_type,
                    status_reason=payload.get("comments"),
                    status_date=event.event_date,
                    status_time=event.event_time,
                    amount_affected=amount_affected,
                )

            # 4. Envia o Evento e o Status (se houver) para o banco via RPC
            result = self.db.save_event_transaction(event, sales_status_obj)

            if not result:
                return False

            return True

        except Exception as e:
            logger.exception(f"Erro fatal ao processar evento {event.order_id}: {e}")
            return False

    async def _enrich_affiliate(self, event: NormalizedEvent) -> None:
        """
        Localiza ou cria o afiliado vinculado ao evento.

        Se o afiliado não existir no banco, ele é criado automaticamente
        (Auto-provisioning) para garantir integridade referencial.
        """
        external_aff_id = event.order_details.external_affiliate_id
        if not external_aff_id:
            return

        network = self._network_value(event.network)

        affiliate = self.db.get_affiliate_by_external_id(network, external_aff_id)

        if not affiliate:
            aff_name = event.order_details.external_affiliate_name or "Tiger Offers"

            # 1. Busca o ID do Tear 0
            default_tear_id = self.db.get_default_tear_id()

            # 2. Cria o objeto com o tear_id preenchido
            new_affiliate = Affiliate(
                network=network,
                aff_id=external_aff_id,
                aff_name=aff_name,
                status=AffiliateStatus.ACTIVE,
                tear_id=default_tear_id
            )

            try:
                affiliate = self.db.create_affiliate(new_affiliate)
            except Exception:
                # Fallback em caso de race condition
                affiliate = self.db.get_affiliate_by_external_id(network, external_aff_id)

        if affiliate and affiliate.id:
            event.affiliate_id = affiliate.id

    async def _enrich_checkout(self, event: NormalizedEvent) -> None:
        """
        Enriquece o evento com dados de Checkout e Produto.

        Tenta vincular o código do checkout (codename) a um produto e etapa
        do funil. Se falhar, tenta buscar pelo nome do produto.
        Em último caso, loga o erro no Slack e assume valores padrão (Purchase/1).
        """
        checkout_code = event.order_details.external_checkout_code
        account_id = event.payload.get("account_id") if event.payload else None

        checkout_info: Optional[CheckoutInfo] = None
        is_exact_match = False

        # 1. Tentativa de busca pelo Código (Match Exato)
        if checkout_code:
            checkout_info = self.db.get_checkout_by_code(checkout_code, account_id)

            if checkout_info:
                is_exact_match = True
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
                    # URL de Compra
                    buy_url = event.payload.get("buy_url") if event.payload else None

                    self.slack.notify_codename_not_found(
                        network=self._network_value(event.network),
                        order_id=event.order_id,
                        product=event.order_details.product_name or "N/A",
                        codename=checkout_code,
                        account_id=event.account_id,
                        buy_url=buy_url,
                    )

        # 2. Fallback por nome do produto
        if not checkout_info:
            product_name = event.order_details.product_name
            if product_name:
                checkout_info = self.db.find_checkout_by_product_name(product_name)

        # 3. Condições do checkout_code
        if checkout_info:
            event.product_id = checkout_info.product_id
            event.funnel_number = checkout_info.funnel_number

            if is_exact_match:
                event.checkout_id = checkout_info.checkout_id
                event.funnel_stage = checkout_info.funnel_stage
            else:
                # Fallback: Se não existir o checkout_code na checkouts:
                # funnel_stage: Purchase e funnel_number: 1
                event.checkout_id = None
                event.funnel_stage = "Purchase"
                event.funnel_number = 1

    def _adjust_event_by_action_type(self, event: NormalizedEvent) -> None:
        """
        Corrige datas e valores do evento baseado no tipo de ação.

        Refunds e Rebills muitas vezes trazem a data da transação original
        no campo principal. Este método move a data real do reembolso/recorrência
        para o campo `event_date`.
        """
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
                        logger.debug(f"Rebill data ajustada: {date} {time}")

    def _create_sales_status(self, event: NormalizedEvent, event_id: str) -> None:
        """
        (Legado/Auxiliar) Cria registro de status manualmente.

        Nota: Atualmente o método principal usa `save_event_transaction` (RPC)
        que faz isso atomicamente. Este método pode ser usado em contextos
        onde a RPC não é aplicável.
        """
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
            amount_affected=amount_affected,
        )

        try:
            self.db.create_sales_status(status)
        except Exception as e:
            error_str = str(e).lower()
            if "23503" in error_str or "foreign key" in error_str:
                logger.info(
                    f"Status ignorado - evento pai não persistido: {event.order_id}"
                )
            else:
                raise

    def _calculate_amount_affected(self, event: NormalizedEvent) -> float:
        """
        Calcula o valor financeiro afetado pela ação.

        Para Vendas: É o valor total da venda.
        Para Refunds/Chargebacks: É o valor devolvido/disputado (negativo ou loss).
        """
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
