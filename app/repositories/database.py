from functools import lru_cache
from typing import Dict, Optional, Union
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

    Gerencia afiliados, checkouts, logs de erro, networks normalizadas
    e o ciclo de vida dos eventos na 'Inbox'.
    """

    # Cache: "BuyGoods" -> "uuid-do-banco"
    _networks_cache: Dict[str, str] = {}

    def __init__(self, settings: Settings):
        """
        Inicializa a conexão com o Supabase.
        """
        if not settings.supabase_url or not settings.supabase_key:
            raise ValueError("Credenciais do Supabase não configuradas")

        self.client: Client = create_client(
            settings.supabase_url, settings.supabase_key
        )
        logger.info("Conexão com Supabase estabelecida")

    def load_networks_cache(self) -> None:
        """
        Carrega todas as redes do banco para a memória na inicialização.
        Chamado pelo lifespan no main.py.
        """
        try:
            # Busca ID e Nome de todas as redes
            response = self.client.table("networks").select("id, name").execute()

            if response.data:
                # Limpa e popula o cache
                DatabaseRepository._networks_cache.clear()
                for row in response.data:
                    name = row["name"]
                    uid = row["id"]
                    DatabaseRepository._networks_cache[name] = uid

                logger.info(
                    f"Cache de Networks carregado: {len(DatabaseRepository._networks_cache)} redes."
                )
        except Exception as e:
            logger.error(f"Erro ao carregar cache de networks: {e}")

    def get_network_id(self, network_name: Union[str, NetworkType]) -> Optional[str]:
        """
        Busca o UUID da rede. Se não existir no cache/banco, CRIA automaticamente.
        """
        # 1. Normaliza input (Enum -> str)
        name = (
            network_name.value
            if isinstance(network_name, NetworkType)
            else str(network_name)
        )

        # 2. Tenta Cache (Rápido - O(1))
        if name in DatabaseRepository._networks_cache:
            return DatabaseRepository._networks_cache[name]

        # 3. Se não tá no cache, tenta criar (Auto-provisioning)
        try:
            logger.warning(f"Rede nova detectada: '{name}'. Criando registro...")

            # Tenta inserir (Upsert para garantir unicidade)
            response = (
                self.client.table("networks")
                .upsert({"name": name}, on_conflict="name")
                .execute()
            )

            if response.data:
                new_id = response.data[0]["id"]
                DatabaseRepository._networks_cache[name] = new_id
                return new_id

            # Fallback: Se upsert não retornou dados, busca o ID
            response = (
                self.client.table("networks")
                .select("id")
                .eq("name", name)
                .single()
                .execute()
            )

            if response.data:
                existing_id = response.data["id"]
                DatabaseRepository._networks_cache[name] = existing_id
                return existing_id

            return None

        except Exception as e:
            logger.error(f"Erro crítico ao resolver network_id para '{name}': {e}")
            return None

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

    @lru_cache(maxsize=1000)
    def get_affiliate_by_external_id(
        self, network: Union[NetworkType, str], aff_id: str
    ) -> Optional[Affiliate]:
        """
        Busca um afiliado pelo seu ID na plataforma de origem.
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
        """
        try:
            data = affiliate.model_dump(
                mode="json",
                exclude={"id"},
                exclude_none=True,
            )

            response = (
                self.client.table("affiliates")
                .upsert(data, on_conflict="aff_id, network")
                .execute()
            )

            if response.data:
                return Affiliate(**response.data[0])

            return None

        except Exception as e:
            logger.error(f"Erro ao criar/atualizar afiliado: {e}")
            return None

    def get_checkout_by_code(
        self, code: str, account_id: Optional[str] = None
    ) -> Optional[CheckoutInfo]:
        """
        Busca informações de checkout baseadas no código do produto (codename).
        """
        if not hasattr(self, "_checkout_cache"):
            self._checkout_cache = {}

        cache_key = f"{code}:{account_id}"

        if cache_key in self._checkout_cache:
            return self._checkout_cache[cache_key]

        try:
            query = (
                self.client.table("checkouts")
                .select("id, product_id, funnel_stage, funnel_number, account_id")
                .eq("checkout_code", code)
            )

            if account_id:
                query = query.eq("account_id", str(account_id))

            response = query.limit(1).execute()

            if not response.data:
                return None

            row = response.data[0]

            result = CheckoutInfo(
                checkout_id=row["id"],
                product_id=row["product_id"],
                funnel_stage=row.get("funnel_stage"),
                funnel_number=row.get("funnel_number"),
            )

            self._checkout_cache[cache_key] = result
            return result

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
        """
        if not product_name:
            return None

        try:
            clean_input = product_name.lower().replace(" ", "")

            products_response = (
                self.client.table("products").select("id, name").execute()
            )

            if not products_response.data:
                return None

            products = sorted(
                products_response.data,
                key=lambda x: len(x.get("name", "")),
                reverse=True,
            )

            for product in products:
                db_name = product.get("name", "").lower().replace(" ", "")
                if db_name and db_name in clean_input:
                    logger.info(
                        f"Match por nome: '{product_name}' -> '{product['name']}'"
                    )
                    return self._get_checkout_by_product_id(product["id"])

            return None

        except Exception as e:
            logger.error(f"Erro na busca por nome de produto: {e}")
            return None

    def _get_checkout_by_product_id(self, product_id: UUID) -> Optional[CheckoutInfo]:
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

    def save_event_transaction(
        self, event: NormalizedEvent, status: Optional[SalesStatus] = None
    ) -> Optional[dict]:
        """
        Persiste o Evento e o Status Financeiro atomicamente.
        AGORA: Resolve o nome da rede para UUID antes de enviar ao banco.
        """
        try:
            # 1. Resolve Network ID para o Evento
            # Busca no cache ou cria se não existir
            net_id_str = self.get_network_id(event.network)
            if net_id_str:
                event.network_id = UUID(net_id_str)

            # 2. Resolve Network ID para o Status (se houver)
            if status:
                status.network_id = event.network_id

            # Serializa para JSON
            event_json = event.model_dump(mode="json", exclude_none=True)

            status_json = None
            if status:
                status_json = status.model_dump(
                    mode="json", exclude={"event_id"}, exclude_none=True
                )

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
                    f"Transação OK | Order: {event.order_id} |"
                    f"NetID: {event.network_id} | DB_ID: {event_id}"
                )

                return saved_data

            return None

        except Exception as e:
            logger.error(f"Erro na transação RPC: {e}")
            raise e

    def register_missing_codename(self, data: MissingCodename) -> None:
        """
        Registra falha na identificação de produto.
        """
        try:
            payload = data.model_dump(mode="json")

            response = (
                self.client.table("missing_codenames")
                .upsert(
                    payload,
                    on_conflict="order_id,codename,account_id",
                    ignore_duplicates=True,
                )
                .execute()
            )

            if response.data:
                saved_id = response.data[0].get("id")
                logger.info(f"Missing Codename salvo: {data.codename} | ID: {saved_id}")

        except Exception as e:
            logger.error(f"Erro ao salvar missing_codename: {e}")

    def get_missing_codenames(self, limit: int = 100) -> list[dict]:
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
        try:
            data = {"status": status}
            if error_msg:
                data["error_log"] = error_msg

            self.client.table("webhook_inbox").update(data).eq("id", inbox_id).execute()
        except Exception as e:
            logger.error(f"Erro ao atualizar inbox {inbox_id}: {e}")

    def check_connection(self) -> bool:
        """
        Verifica se a conexão com o Supabase está ativa.
        """
        try:
            self.client.table("affiliates").select("id").limit(1).execute()
            return True
        except Exception as e:
            logger.error(f"Health Check DB falhou: {e}")
            return False
