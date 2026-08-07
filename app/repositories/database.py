from functools import lru_cache
import time
import httpx
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
from supabase import create_client, ClientOptions


class DatabaseRepository:
    """
    Repositório central para operações no Supabase.

    Gerencia afiliados, checkouts, logs de erro, networks normalizadas
    e o ciclo de vida dos eventos na 'Inbox'.
    """

    def __init__(self, settings: Settings):
        if not settings.supabase_url or not settings.supabase_key:
            raise ValueError("Credenciais do Supabase não configuradas")

        self.client = create_client(
            settings.supabase_url,
            settings.supabase_key,
            options=ClientOptions(postgrest_client_timeout=30),
        )

        # Substitui session com pool configurado e keepalive curto
        self.client.postgrest.session = httpx.Client(
            timeout=httpx.Timeout(30.0, connect=10.0),
            limits=httpx.Limits(
                max_connections=20,
                max_keepalive_connections=10,
                keepalive_expiry=20.0,  # descarta idle antes do Supabase fechar (~30s)
            ),
            headers=self.client.postgrest.session.headers,
        )

        # Caches de instância
        self._networks_cache: Dict[str, str] = {}
        self._checkout_cache: Dict[str, CheckoutInfo] = {}

    def load_networks_cache(self) -> None:
        """
        Carrega todas as redes do banco para a memória na inicialização.
        Chamado pelo lifespan no main.py.
        """
        try:
            response = self.client.table("networks").select("id, name").execute()

            if response.data:
                self._networks_cache.clear()
                for row in response.data:
                    self._networks_cache[row["name"]] = row["id"]

                logger.info(
                    f"Cache de Networks carregado: {len(self._networks_cache)} redes."
                )
        except Exception as e:
            logger.error(f"Erro ao carregar cache de networks: {e}")

    def get_network_id(self, network_name: Union[str, NetworkType]) -> Optional[str]:
        """
        Busca o UUID da rede. Se não existir no cache/banco, CRIA automaticamente.
        """
        name = (
            network_name.value
            if isinstance(network_name, NetworkType)
            else str(network_name)
        )

        if name in self._networks_cache:
            return self._networks_cache[name]

        try:
            logger.warning(f"Rede nova detectada: '{name}'. Criando registro...")

            response = (
                self.client.table("networks")
                .upsert({"name": name}, on_conflict="name")
                .execute()
            )

            if response.data:
                new_id = response.data[0]["id"]
                self._networks_cache[name] = new_id
                return new_id

            # Fallback: busca o ID se upsert não retornou dados
            response = (
                self.client.table("networks")
                .select("id")
                .eq("name", name)
                .single()
                .execute()
            )

            if response.data:
                existing_id = response.data["id"]
                self._networks_cache[name] = existing_id
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

    def get_affiliate_by_external_id(
        self,
        network: Union[NetworkType, str],
        aff_id: str,
        account_id: Optional[str] = None,
    ) -> Optional[Affiliate]:
        """
        Busca um afiliado pelo seu ID na plataforma de origem e, se fornecido, pelo account_id.
        """
        network_value = network.value if isinstance(network, NetworkType) else network

        try:
            net_id_str = self.get_network_id(network)

            # Monta a query base
            query = (
                self.client.table("affiliates")
                .select("*")
                .eq("aff_id", aff_id)
                .eq("network_id", net_id_str)
            )

            # Adiciona o filtro de account_id dinamicamente se ele existir
            if account_id:
                query = query.eq("account_id", str(account_id))

            response = query.limit(1).execute()

            if response.data:
                row_data = response.data[0]
                row_data["network"] = network_value
                return Affiliate(**row_data)

            return None

        except Exception as e:
            logger.error(
                f"Erro ao buscar afiliado {aff_id} (network={network_value}, account={account_id}): {e}"
            )
            return None

    def create_affiliate(self, affiliate: Affiliate) -> Optional[Affiliate]:
        """
        Cadastra um novo afiliado no banco de dados.
        """
        try:
            net_id_str = self.get_network_id(affiliate.network)
            if net_id_str:
                affiliate.network_id = UUID(net_id_str)

            data = affiliate.model_dump(
                mode="json",
                exclude={"id", "network"},
                exclude_none=True,
            )

            # Atenção: a regra de on_conflict agora inclui account_id
            response = (
                self.client.table("affiliates")
                .upsert(data, on_conflict="aff_id, network_id, account_id")
                .execute()
            )

            if response.data:
                row_data = response.data[0]
                row_data["network"] = affiliate.network
                return Affiliate(**row_data)

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
        """
        try:
            net_id_str = self.get_network_id(event.network)
            if net_id_str:
                event.network_id = UUID(net_id_str)

            if status:
                status.network_id = event.network_id

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
        Retry automático em erros transientes de conexão.
        """
        data = {"network": network, "payload": payload, "status": "pending"}

        for attempt in range(3):
            try:
                response = self.client.table("webhook_inbox").insert(data).execute()
                if response.data:
                    return response.data[0]["id"]
                return None

            except (httpx.ConnectTimeout, httpx.RemoteProtocolError) as e:
                wait = 2**attempt
                logger.warning(
                    f"Supabase transient error (attempt={attempt + 1}): {e}, "
                    f"retrying in {wait}s"
                )
                time.sleep(wait)

            except Exception:
                logger.exception("Erro inesperado ao salvar inbox")
                raise

        raise Exception("Supabase indisponível após retries")

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

    def get_high_fee_transactions(self, filters: dict) -> list[dict]:
        try:
            period = filters.get("period", {})
            start_date = period.get("start_date")
            end_date = period.get("end_date")
            product_id = filters.get("product_id")
            network_id = filters.get("network_id")

            query = self.client.table("events").select(
                "*, affiliates(aff_name, aff_id), products(name), networks(tax)"
            )

            query = query.gte("event_date", start_date)
            query = query.lte("event_date", end_date)
            query = query.gt("merchant_commission_rate", 0.01)
            query = query.eq("is_test", False)
            query = query.in_("action_type", ["SALE", "neworder", "rebill"])

            if product_id:
                query = query.in_("product_id", product_id)
            if network_id:
                query = query.eq("network_id", network_id)

            query = query.order("merchant_commission_rate", desc=True)

            response = query.execute()
            return response.data if response.data else []

        except Exception as e:
            logger.error(f"Erro ao buscar transações de alta taxa: {e}")
            raise e

    def get_affiliates_without_recent_sales(self, days: int = 3) -> list[dict]:
        """Busca afiliados ativos sem vendas recentes nos últimos X dias."""
        try:
            response = self.client.rpc(
                "get_affiliates_without_recent_sales", {"days_limit": days}
            ).execute()
            return response.data if response.data else []
        except Exception as e:
            logger.error(f"Erro ao buscar afiliados sem vendas recentes: {e}")
            return []

    def get_chargebacks_last_30_days(self) -> list[dict]:
        """Busca afiliados com chargebacks nos últimos 30 dias."""
        try:
            response = self.client.rpc("get_affiliate_chargebacks_30_days").execute()
            return response.data if response.data else []
        except Exception as e:
            logger.error(f"Erro ao buscar chargebacks dos últimos 30 dias: {e}")
            return []

    def get_negative_net_revenue_last_30_days(self) -> list[dict]:
        """Busca afiliados com Net Revenue negativo nos últimos 30 dias."""
        try:
            response = self.client.rpc("get_negative_net_revenue_30_days").execute()
            return response.data if response.data else []
        except Exception as e:
            logger.error(f"Erro ao buscar Net Revenue negativo: {e}")
            return []

    def insert_abandoned_cart(self, cart_data: dict) -> Optional[dict]:
        """
        Insere um registro de carrinho abandonado na tabela abandoned_carts.
        """
        try:
            response = self.client.table("abandoned_carts").insert(cart_data).execute()
            if response.data:
                logger.info(
                    f"Carrinho abandonado salvo | Email: {cart_data.get('customer_email')}"
                )
                return response.data[0]
            return None
        except Exception as e:
            logger.error(f"Erro ao inserir carrinho abandonado: {e}")
            return None

    def _fetch_all_paginated(
        self, table_name: str, build_query, page_size: int = 1000, select: str = "*"
    ) -> list[dict]:
        """
        Busca todas as linhas de uma tabela/view do Supabase, paginando em blocos
        de `page_size` (o PostgREST limita a 1000 linhas por requisição).
        """
        rows: list[dict] = []
        start = 0

        while True:
            query = build_query(self.client.table(table_name).select(select))
            response = query.range(start, start + page_size - 1).execute()
            page = response.data or []
            rows.extend(page)

            if len(page) < page_size:
                break
            start += page_size

        return rows

    def get_affiliate_product_network_snapshot(
        self,
        start_date: str,
        end_date: str,
        network_id: Optional[str] = None,
        product_id: Optional[list[str]] = None,
    ) -> list[dict]:
        """Snapshot diário Afiliado + Produto + Plataforma (Nível 1 do relatório)."""

        def build(query):
            query = query.gte("snapshot_date", start_date).lte(
                "snapshot_date", end_date
            )
            if network_id:
                query = query.eq("network_id", network_id)
            if product_id:
                query = query.in_("product_id", product_id)
            return query

        try:
            return self._fetch_all_paginated(
                "snapshot_daily_affiliate_product_network", build
            )
        except Exception as e:
            logger.error(f"Erro ao buscar snapshot afiliado/produto/rede: {e}")
            raise e

    def get_affiliate_product_funnel_network_snapshot(
        self,
        start_date: str,
        end_date: str,
        network_id: Optional[str] = None,
        product_id: Optional[list[str]] = None,
    ) -> list[dict]:
        """Snapshot diário Afiliado + Produto + Funil + Plataforma (Nível 2 do relatório)."""

        def build(query):
            query = query.gte("snapshot_date", start_date).lte(
                "snapshot_date", end_date
            )
            if network_id:
                query = query.eq("network_id", network_id)
            if product_id:
                query = query.in_("product_id", product_id)
            return query

        try:
            return self._fetch_all_paginated(
                "snapshot_daily_affiliate_product_funnel_network", build
            )
        except Exception as e:
            logger.error(f"Erro ao buscar snapshot de funil por afiliado: {e}")
            raise e

    def get_affiliate_quantity_snapshot(
        self,
        start_date: str,
        end_date: str,
        network_id: Optional[str] = None,
        product_id: Optional[list[str]] = None,
    ) -> list[dict]:
        """Snapshot diário Afiliado + Produto + Funil + Pote/Quantidade (Nível 3 do relatório)."""

        def build(query):
            query = query.gte("snapshot_date", start_date).lte(
                "snapshot_date", end_date
            )
            if network_id:
                query = query.eq("network_id", network_id)
            if product_id:
                query = query.in_("product_id", product_id)
            return query

        try:
            return self._fetch_all_paginated("snapshot_daily_affiliate_quantity", build)
        except Exception as e:
            logger.error(f"Erro ao buscar snapshot de potes por afiliado: {e}")
            raise e

    def get_cogs_percentage(self) -> float:
        """Busca o percentual de COGS mais recente configurado no sistema."""
        try:
            response = (
                self.client.table("cogs")
                .select("percentage")
                .order("updated_at", desc=True)
                .limit(1)
                .execute()
            )
            if response.data:
                return float(response.data[0]["percentage"])
            return 0.0
        except Exception as e:
            logger.error(f"Erro ao buscar percentual de COGS: {e}")
            return 0.0

    def get_affiliates_lookup(self) -> Dict[str, dict]:
        """Mapa id -> {aff_name, aff_id} de todos os afiliados (paginado)."""
        try:
            rows = self._fetch_all_paginated(
                "affiliates", lambda q: q, select="id, aff_name, aff_id"
            )
            return {row["id"]: row for row in rows}
        except Exception as e:
            logger.error(f"Erro ao carregar afiliados: {e}")
            return {}

    def get_products_lookup(self) -> Dict[str, str]:
        """Mapa id -> nome do produto (paginado)."""
        try:
            rows = self._fetch_all_paginated(
                "products", lambda q: q, select="id, name"
            )
            return {row["id"]: row["name"] for row in rows}
        except Exception as e:
            logger.error(f"Erro ao carregar produtos: {e}")
            return {}

    def get_networks_lookup(self) -> Dict[str, str]:
        """Mapa id -> nome da plataforma/rede (paginado)."""
        try:
            rows = self._fetch_all_paginated(
                "networks", lambda q: q, select="id, name"
            )
            return {row["id"]: row["name"] for row in rows}
        except Exception as e:
            logger.error(f"Erro ao carregar plataformas: {e}")
            return {}
