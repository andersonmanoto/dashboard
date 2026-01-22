"""
Repositório para acesso ao banco de dados Supabase.
"""
from typing import Optional, Union
from uuid import UUID
from loguru import logger
from supabase import Client, create_client

from config import Settings
from models.schemas import (
    NormalizedEvent, 
    SalesStatus, 
    CheckoutInfo, 
    Affiliate
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
    
    # ========== HELPER MÉTODOS ==========

    def _sanitize_data(self, data: dict) -> dict:
        """
        Converte tipos complexos (como UUID) para strings para garantir
        serialização JSON correta.
        """
        for key, value in data.items():
            if isinstance(value, UUID):
                data[key] = str(value)
        return data

    # ========== AFFILIATES ==========
    
    def get_affiliate_by_external_id(
        self,
        network: Union[NetworkType, str],
        aff_id: str
    ) -> Optional[Affiliate]:
        """
        Busca afiliado por ID externo e rede.
        Aceita network como Enum ou str (blindado contra erros de borda).
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
        
        Args:
            affiliate: Dados do afiliado
            
        Returns:
            Affiliate criado ou None em caso de erro
        """
        try:
            data = affiliate.model_dump(exclude={'id'}, exclude_none=True)
            
            # CORREÇÃO: Sanitização de UUIDs
            data = self._sanitize_data(data)
            
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
    
    def get_checkout_by_code(
        self, 
        code: str, 
        account_id: Optional[str] = None
    ) -> Optional[CheckoutInfo]:
        """
        Busca checkout por código, com desambiguação por account_id.
        
        Args:
            code: Código do checkout
            account_id: ID da conta para desambiguação (opcional)
            
        Returns:
            CheckoutInfo ou None se não encontrado
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
        
        Args:
            product_name: Nome do produto para buscar
            
        Returns:
            CheckoutInfo ou None se não encontrado
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
    
    # ========== EVENTS ==========
    
    def upsert_event(self, event: NormalizedEvent) -> Optional[dict]:
        """
        Insere ou atualiza evento usando UPSERT.
        
        Args:
            event: Evento normalizado
            
        Returns:
            Dados do evento salvo ou None em caso de erro
        """
        try:
            data = event.model_dump(exclude_none=True)
            # Remove o account_id do upsert em 'events'
            data = event.model_dump(exclude_none=True, exclude={'account_id'})
            
            # Sanitização de UUIDs
            data = self._sanitize_data(data)
            
            response = self.client.table("events").upsert(
                data,
                on_conflict="network,order_id,action_type,event_date,event_time,sale_total"
            ).execute()
            
            if response.data:
                saved_id = response.data[0].get('id')
                logger.info(
                    f"Evento salvo: {event.action_type} | ID: {saved_id}"
                )

                return response.data[0]
            
            return None
            
        except Exception as e:
            logger.error(f"Erro ao salvar evento: {e}")
            raise
    
    # ========== SALES STATUS ==========
    
    def create_sales_status(self, status: SalesStatus) -> Optional[dict]:
        """
        Cria registro de mudança de status de venda.
        
        Args:
            status: Dados do status
            
        Returns:
            Dados do status salvo ou None em caso de erro
        """
        try:
            data = status.model_dump(exclude_none=True)
            data = self._sanitize_data(data)
            
            response = self.client.table("sales_status")\
                .insert(data)\
                .execute()
            
            if response.data:
                saved_id = response.data[0].get('id')
                logger.info(
                    f"Status salvo: {status.status_type} | ID: {saved_id}"
                )
                return response.data[0]
            
            return None
            
        except Exception as e:
            logger.error(f"Erro ao salvar sales_status: {e}")
            return None