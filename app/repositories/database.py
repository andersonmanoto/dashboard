from functools import lru_cache
from typing import Optional, Union
from uuid import UUID

from config import Settings
from loguru import logger
from models.enums import NetworkType
from models.schemas import (
    Affiliate,
    CheckoutInfo,
    MissingCodename,
    NormalizedEvent,
    SalesStatus,
)
from supabase import Client, create_client


class DatabaseRepository:
    """
    Repositório central para operações no Supabase.

    Gerencia afiliados, checkouts, logs de erro e o ciclo de vida
    dos eventos na 'Inbox'.
    """

    def __init__(self, settings: Settings):
        """
        Inicializa a conexão com o Supabase.

        Args:
            settings (Settings): Configurações contendo URL e Key do projeto.

        Raises:
            ValueError: Se as credenciais não estiverem presentes no .env.
        """
        if not settings.supabase_url or not settings.supabase_key:
            raise ValueError("Credenciais do Supabase não configuradas")

        self.client: Client = create_client(
            settings.supabase_url, settings.supabase_key
        )
        logger.info("Conexão com Supabase estabelecida")

    @lru_cache(maxsize=1)
    def get_default_tear_id(self) -> Optional[UUID]:
        """
        Busca o ID do Tear padrão (Number 0).
        """
        try:
            response = (
                self.client.table("tears")
                .select("id")
                .eq("tear_number", 0)
                .limit(1)
                .execute()
            )

            if response.data:
                return UUID(response.data[0]["id"])
            
            logger.warning("ALERTA: Tear Number 0 não encontrado no banco!")
            return None

        except Exception as e:
            logger.error(f"Erro ao buscar default tear id: {e}")
            return None

    # ========== AFFILIATES ==========

    @lru_cache(maxsize=1000)
    def get_affiliate_by_external_id(
        self, network: Union[NetworkType, str], aff_id: str
    ) -> Optional[Affiliate]:
        """
        Busca um afiliado pelo seu ID na plataforma de origem.

        Utiliza cache em memória (`lru_cache`) para evitar chamadas repetidas
        ao banco durante processamentos em lote (bulk), já que os dados
        de afiliados mudam pouco.

        Args:
            network (Union[NetworkType, str]): Rede (ex: BuyGoods).
            aff_id (str): ID do afiliado na rede (ex: '12345').

        Returns:
            Optional[Affiliate]: Objeto Affiliate se encontrado, None caso contrário.
        """
        network_value = network.value if isinstance(network, NetworkType) else network

        try:
            response = (
                self.client.table("affiliates")
                .select("*")
                .eq("aff_id", aff_id)
                .eq("network", network_value)
                .limit(1)
                .execute()
            )

            if response.data:
                return Affiliate(**response.data[0])

            return None

        except Exception as e:
            logger.error(
                f"Erro ao buscar afiliado {aff_id} (network={network_value}): {e}"
            )
            return None

    def create_affiliate(self, affiliate: Affiliate) -> Optional[Affiliate]:
        """
        Cadastra um novo afiliado no banco de dados.

        Args:
            affiliate (Affiliate): Objeto com os dados do afiliado.

        Returns:
            Optional[Affiliate]: O afiliado criado (com ID gerado) ou None se falhar.
        """
        try:
            data = affiliate.model_dump(
                mode="json",
                exclude={"id"},
                exclude_none=True,
            )

            response = self.client.table("affiliates").insert(data).execute()

            if response.data:
                return Affiliate(**response.data[0])

            return None

        except Exception as e:
            logger.error(f"Erro ao criar afiliado: {e}")
            return None

    # ========== CHECKOUTS ==========

    @lru_cache(maxsize=1000)
    def get_checkout_by_code(
        self, code: str, account_id: Optional[str] = None
    ) -> Optional[CheckoutInfo]:
        """
        Busca informações de checkout baseadas no código do produto (codename).

        A busca utiliza uma chave composta (checkout_code + account_id) para
        resolver colisões onde o mesmo código (ex: 'nat3u') é usado em
        contas diferentes para produtos diferentes.

        Args:
            code (str): Código do produto vindo no webhook.
            account_id (Optional[str]): ID da conta na plataforma (para desambiguação).

        Returns:
            Optional[CheckoutInfo]: Dados do funil e produto vinculado.
        """
        try:
            # Inicia a query base
            query = (
                self.client.table("checkouts")
                .select("id, product_id, funnel_stage, funnel_number, account_id")
                .eq("checkout_code", code)
            )

            # Busca pelo account_id
            if account_id:
                query = query.eq("account_id", str(account_id))

            # Executa e pega o que deu match (checkout_code + account_id)
            response = query.limit(1).execute()

            if not response.data:
                # Se não achou com a conta específica,
                # retorna None para evitar atribuição errada.
                return None

            row = response.data[0]

            return CheckoutInfo(
                checkout_id=row["id"],
                product_id=row["product_id"],
                funnel_stage=row.get("funnel_stage"),
                funnel_number=row.get("funnel_number"),
            )

        except Exception:
            logger.exception(
                "Erro ao buscar checkout %s (Account %s)",
                code,
                account_id,
            )

            return None

    def find_checkout_by_product_name(
        self, product_name: str
    ) -> Optional[CheckoutInfo]:
        """
        Busca fallback por nome do produto (Partial Match).

        Acionado quando o `checkout_code` não é encontrado. Tenta localizar
        o produto pelo nome (normalizado) para recuperar as informações do funil.
        NÃO utiliza cache pois é uma operação de exceção/contingência.

        Args:
            product_name (str): Nome do produto vindo no webhook.

        Returns:
            Optional[CheckoutInfo]: Dados do checkout se houver match de nome.
        """
        if not product_name:
            return None

        try:
            # Normaliza nome do input
            clean_input = product_name.lower().replace(" ", "")

            # Busca todos os produtos
            products_response = (
                self.client.table("products").select("id, name").execute()
            )

            if not products_response.data:
                return None

            # Ordena por tamanho (maior primeiro) para evitar falsos positivos
            products = sorted(
                products_response.data,
                key=lambda x: len(x.get("name", "")),
                reverse=True,
            )

            # Busca match
            for product in products:
                db_name = product.get("name", "").lower().replace(" ", "")
                if db_name and db_name in clean_input:
                    logger.info(
                        f"Match por nome: '{product_name}' -> '{product['name']}'"
                    )

                    # Busca checkout vinculado
                    return self._get_checkout_by_product_id(product["id"])

            return None

        except Exception as e:
            logger.error(f"Erro na busca por nome de produto: {e}")
            return None

    def _get_checkout_by_product_id(self, product_id: UUID) -> Optional[CheckoutInfo]:
        """
        Método auxiliar para recuperar dados de checkout dado um Product ID.

        Args:
            product_id (UUID): ID do produto no banco.

        Returns:
            Optional[CheckoutInfo]: Dados do checkout.
        """
        try:
            response = (
                self.client.table("checkouts")
                .select("id, product_id, funnel_stage, funnel_number")
                .eq("product_id", str(product_id))
                .limit(1)
                .execute()
            )

            if response.data:
                row = response.data[0]
                return CheckoutInfo(
                    checkout_id=row["id"],
                    product_id=row["product_id"],
                    funnel_stage=row.get("funnel_stage"),
                    funnel_number=row.get("funnel_number"),
                )

            return None

        except Exception as e:
            logger.error(f"Erro ao buscar checkout por product_id: {e}")
            return None

    # ========== TRANSACTIONS (RPC) ==========

    def save_event_transaction(
        self, event: NormalizedEvent, status: Optional[SalesStatus] = None
    ) -> Optional[dict]:
        """
        Persiste o Evento e o Status Financeiro atomicamente.

        Utiliza uma Stored Procedure (RPC) no Supabase (`save_event_transaction`)
        para garantir que ou tudo é salvo ou nada é salvo, mantendo a integridade
        entre a tabela de eventos (events) e a de status (sales_status).

        Args:
            event (NormalizedEvent): O evento principal normalizado.
            status (Optional[SalesStatus]): O status financeiro (se aplicável).

        Returns:
            Optional[dict]: O registro salvo retornado pelo banco.
        """
        try:
            event_json = event.model_dump(mode="json", exclude_none=True)

            status_json = None
            if status:
                status_json = status.model_dump(
                    mode="json", exclude={"event_id"}, exclude_none=True
                )

            # Chama a função no Supabase
            response = self.client.rpc(
                "save_event_transaction",
                {"event_data": event_json, "status_data": status_json},
            ).execute()

            if response.data:
                saved_data = response.data
                event_id = (
                    saved_data.get("id") if isinstance(saved_data, dict) else "N/A"
                )

                logger.info(
                    f"Transação OK | Order: {event.order_id} | Event ID: {event_id}"
                )

                return saved_data

            return None

        except Exception as e:
            logger.error(f"Erro na transação RPC: {e}")
            raise e

    # ========== MISSING CODENAMES ==========

    def register_missing_codename(self, data: MissingCodename) -> None:
        """
        Registra falha na identificação de produto (Codename não encontrado).

        Útil para auditoria e correção de funis. Permite duplicatas de codename
        apenas se o `order_id` for diferente, garantindo contagem real de
        ordens sem informação de checkout por erro de configuração.

        Args:
            data (MissingCodename): Dados do erro para log.
        """
        try:
            payload = data.model_dump(mode="json")

            # Upsert com ignore_duplicates para respeitar
            # a constraint unique_codename_account
            response = (
                self.client.table("missing_codenames")
                .upsert(
                    payload,
                    on_conflict="order_id,codename,account_id",
                    ignore_duplicates=True,
                )
                .execute()
            )

            # Se response.data vier preenchido,
            # significa que houve inserção/atualização
            if response.data:
                saved_id = response.data[0].get("id")
                logger.info(f"Missing Codename salvo: {data.codename} | ID: {saved_id}")

        except Exception as e:
            logger.error(f"Erro ao salvar missing_codename: {e}")

    def get_missing_codenames(self, limit: int = 100) -> list[dict]:
        """
        Recupera lista de erros de codename não resolvidos.

        Args:
            limit (int): Número máximo de registros a retornar.

        Returns:
            list[dict]: Lista de registros de erro.
        """
        try:
            response = (
                self.client.table("missing_codenames")
                .select("*")
                .eq("is_resolved", False)
                .order("created_at", desc=True)
                .limit(limit)
                .execute()
            )
            return response.data
        except Exception as e:
            logger.error(f"Erro ao buscar missing_codenames: {e}")
            return []

    def create_inbox_entry(self, network: str, payload: dict) -> str:
        """
        Registra um novo webhook cru na tabela Inbox.

        Esta é a primeira etapa do processamento. O dado é salvo "as-is"
        para garantir durabilidade antes de qualquer lógica.

        Args:
            network (str): Rede de origem (BuyGoods/DigiStore).
            payload (dict): JSON completo recebido.

        Returns:
            str: O ID (UUID) do registro criado na inbox.

        Raises:
            Exception: Se falhar ao salvar, propaga erro para retornar 500 na API.
        """
        try:
            data = {"network": network, "payload": payload, "status": "pending"}
            response = self.client.table("webhook_inbox").insert(data).execute()

            if response.data:
                return response.data[0]["id"]
            return None
        except Exception as e:
            logger.error(f"Erro CRÍTICO ao salvar na inbox: {e}")
            raise e

    def fetch_pending_webhooks(self, limit: int = 10) -> list[dict]:
        """
        Busca os próximos webhooks pendentes para processamento (FIFO).

        Args:
            limit (int): Tamanho do lote.

        Returns:
            list[dict]: Lista de registros da inbox com status 'pending'.
        """
        try:
            response = (
                self.client.table("webhook_inbox")
                .select("*")
                .eq("status", "pending")
                .order("created_at", desc=False)
                .limit(limit)
                .execute()
            )
            return response.data
        except Exception as e:
            logger.error(f"Erro ao buscar pendentes: {e}")
            return []

    def update_inbox_status(self, inbox_id: str, status: str, error_msg: str = None):
        """
        Atualiza o estado de processamento de um item da Inbox.

        Args:
            inbox_id (str): ID do registro.
            status (str): Novo status (processing, processed, failed).
            error_msg (str, optional): Mensagem de erro em caso de falha.
        """
        try:
            data = {"status": status}
            if error_msg:
                data["error_log"] = error_msg

            self.client.table("webhook_inbox").update(data).eq("id", inbox_id).execute()
        except Exception as e:
            logger.error(f"Erro ao atualizar inbox {inbox_id}: {e}")
