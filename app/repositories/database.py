from functools import lru_cache
from typing import Optional, Union
from uuid import UUID
from loguru import logger
from supabase import Client, create_client

from config import Settings
from models.schemas import (
    NormalizedEvent, 
    SalesStatus, 
    CheckoutInfo, 
    Affiliate,
    MissingCodename
)
from models.enums import AffiliateStatus, NetworkType


class DatabaseRepository:
    """Repositório para acesso ao Supabase."""
    
    def __init__(self, settings: Settings):
        """
        Inicializa conexão com Supabase.
        
        Args:
            settings: Configurações da aplicação
            
        Raises:
            ValueError: Se credenciais do Supabase estiverem ausentes
        """
        if not settings.supabase_url or not settings.supabase_key:
            raise ValueError("Credenciais do Supabase não configuradas")
        
        self.client: Client = create_client(
            settings.supabase_url, 
            settings.supabase_key
        )
        logger.info("Conexão com Supabase estabelecida")

    # ========== AFFILIATES ==========
    
    @lru_cache(maxsize=1000)
    def get_affiliate_by_external_id(
        self,
        network: Union[NetworkType, str],
        aff_id: str
    ) -> Optional[Affiliate]:
        """
        Busca afiliado por ID externo e rede.
        Aceita network como Enum ou str (blindado contra erros de borda).
        Usa cache em memória para otimizar importações em lote.
        """
        network_value = (
            network.value
            if isinstance(network, NetworkType)
            else network
        )

        try:
            response = (
                self.client
                .table("affiliates")
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
                f"Erro ao buscar afiliado {aff_id} "
                f"(network={network_value}): {e}"
            )
            return None
    
    def create_affiliate(self, affiliate: Affiliate) -> Optional[Affiliate]:
        """
        Cria novo afiliado.
        """
        try:
            data = affiliate.model_dump(mode='json', exclude={'id'}, exclude_none=True)
            
            response = self.client.table("affiliates")\
                .insert(data)\
                .execute()
            
            if response.data:
                return Affiliate(**response.data[0])
            
            return None
            
        except Exception as e:
            logger.error(f"Erro ao criar afiliado: {e}")
            return None
    
    # ========== CHECKOUTS ==========
    
    @lru_cache(maxsize=1000)
    def get_checkout_by_code(
        self, 
        code: str, 
        account_id: Optional[str] = None
    ) -> Optional[CheckoutInfo]:
        """
        Busca checkout por código, com desambiguação por account_id.
        Usa cache em memória para performance.
        """
        try:
            response = self.client.table("checkouts")\
                .select("id, product_id, funnel_stage, funnel_number, account_id")\
                .eq("checkout_code", code)\
                .execute()
            
            if not response.data:
                return None
            
            # Se temos account_id, tentamos match exato
            if account_id:
                matches = [
                    r for r in response.data 
                    if str(r.get("account_id")) == str(account_id)
                ]
                if matches:
                    row = matches[0]
                else:
                    row = response.data[0]  # Fallback para primeiro
            else:
                row = response.data[0]
            
            return CheckoutInfo(
                checkout_id=row["id"],
                product_id=row["product_id"],
                funnel_stage=row.get("funnel_stage"),
                funnel_number=row.get("funnel_number")
            )
            
        except Exception as e:
            logger.error(f"Erro ao buscar checkout {code}: {e}")
            return None
    
    def find_checkout_by_product_name(
        self, 
        product_name: str
    ) -> Optional[CheckoutInfo]:
        """
        Busca checkout fazendo match parcial pelo nome do produto.
        Estratégia de contingência quando o código não é encontrado.
        (Sem Cache propositalmente, pois é um fallback raro)
        """
        if not product_name:
            return None
        
        try:
            # Normaliza nome do input
            clean_input = product_name.lower().replace(" ", "")
            
            # Busca todos os produtos
            products_response = self.client.table("products")\
                .select("id, name")\
                .execute()
            
            if not products_response.data:
                return None
            
            # Ordena por tamanho (maior primeiro) para evitar falsos positivos
            products = sorted(
                products_response.data, 
                key=lambda x: len(x.get("name", "")), 
                reverse=True
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
        """Helper para buscar checkout por product_id."""
        try:
            response = self.client.table("checkouts")\
                .select("id, product_id, funnel_stage, funnel_number")\
                .eq("product_id", str(product_id))\
                .limit(1)\
                .execute()
            
            if response.data:
                row = response.data[0]
                return CheckoutInfo(
                    checkout_id=row["id"],
                    product_id=row["product_id"],
                    funnel_stage=row.get("funnel_stage"),
                    funnel_number=row.get("funnel_number")
                )
            
            return None
            
        except Exception as e:
            logger.error(f"Erro ao buscar checkout por product_id: {e}")
            return None
    
    # ========== TRANSACTIONS (RPC) ==========
    
    def save_event_transaction(
        self, 
        event: NormalizedEvent, 
        status: Optional[SalesStatus] = None
    ) -> Optional[dict]:
        """
        Salva Evento e Status numa única transação via RPC.
        Substitui upsert_event e create_sales_status.
        """
        try:
            event_json = event.model_dump(mode='json', exclude_none=True)
            
            status_json = None
            if status:
                status_json = status.model_dump(mode='json', exclude={'event_id'}, exclude_none=True)

            # Chama a função no Supabase
            response = self.client.rpc(
                'save_event_transaction', 
                {'event_data': event_json, 'status_data': status_json}
            ).execute()

            if response.data:
                saved_data = response.data
                event_id = saved_data.get('id') if isinstance(saved_data, dict) else 'N/A'
                
                logger.info(f"Transação OK | Order: {event.order_id} | Event ID: {event_id}")
                return saved_data

            return None

        except Exception as e:
            logger.error(f"Erro na transação RPC: {e}")
            raise e

    # ========== MISSING CODENAMES ==========

    def register_missing_codename(self, data: MissingCodename) -> None:
        """
        Registra um codename não encontrado (ignora se já existir).
        """
        try:
            payload = data.model_dump(mode='json', exclude_none=True)
            
            # Upsert com ignore_duplicates para respeitar a constraint unique_codename_account
            response = self.client.table("missing_codenames")\
                .upsert(
                    payload, 
                    on_conflict="codename,account_id", 
                    ignore_duplicates=True
                ).execute()
            
            # Se response.data vier preenchido, significa que houve inserção/atualização
            if response.data:
                saved_id = response.data[0].get('id')
                logger.info(
                    f"Missing Codename salvo: {data.codename} | ID: {saved_id}"
                )

        except Exception as e:
            logger.error(f"Erro ao salvar missing_codename: {e}")

    def get_missing_codenames(self, limit: int = 100) -> list[dict]:
        """
        Retorna lista de codenames não resolvidos.
        """
        try:
            response = self.client.table("missing_codenames")\
                .select("*")\
                .eq("is_resolved", False)\
                .order("created_at", desc=True)\
                .limit(limit)\
                .execute()
            return response.data
        except Exception as e:
            logger.error(f"Erro ao buscar missing_codenames: {e}")
            return []
        
    def create_inbox_entry(self, network: str, payload: dict) -> str:
        """
        Salva o webhook cru na tabela de entrada.
        Retorna o ID do registro.
        """
        try:
            data = {
                "network": network,
                "payload": payload,
                "status": "pending"
            }
            response = self.client.table("webhook_inbox").insert(data).execute()
            if response.data:
                return response.data[0]['id']
            return None
        except Exception as e:
            logger.error(f"Erro CRÍTICO ao salvar na inbox: {e}")
            raise e

    def fetch_pending_webhooks(self, limit: int = 10) -> list[dict]:
        """
        Busca webhooks pendentes (FIFO).
        """
        try:
            response = self.client.table("webhook_inbox")\
                .select("*")\
                .eq("status", "pending")\
                .order("created_at", desc=False)\
                .limit(limit)\
                .execute()
            return response.data
        except Exception as e:
            logger.error(f"Erro ao buscar pendentes: {e}")
            return []

    def update_inbox_status(self, inbox_id: str, status: str, error_msg: str = None):
        """
        Atualiza o status do processamento.
        """
        try:
            data = {"status": status}
            if error_msg:
                data["error_log"] = error_msg
            
            self.client.table("webhook_inbox")\
                .update(data)\
                .eq("id", inbox_id)\
                .execute()
        except Exception as e:
            logger.error(f"Erro ao atualizar inbox {inbox_id}: {e}")