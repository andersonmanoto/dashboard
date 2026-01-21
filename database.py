import os
from loguru import logger
from typing import Dict, Any, Optional, List
from supabase import create_client, Client
from dotenv import load_dotenv

load_dotenv()

class DatabaseService:
    _instance = None

    def __init__(self):
        self.supabase_url = os.getenv("SUPABASE_URL")
        self.supabase_key = os.getenv("SUPABASE_KEY")
        
        if not self.supabase_url or not self.supabase_key:
            logger.critical("❌ Variáveis de ambiente do Supabase não configuradas!")
            raise ValueError("Supabase URL/KEY missing")

        self.client: Client = create_client(self.supabase_url, self.supabase_key)
        logger.info("Conexão Supabase (Singleton) inicializada.")

    async def get_or_create_affiliate(self, network: str, aff_id: str, aff_name: str) -> Optional[str]:
        """
        Busca ou cria afiliado e retorna o UUID.
        """
        if not aff_id:
            return None

        try:
            # 1. Busca existente
            response = self.client.table("affiliates")\
                .select("id")\
                .eq("aff_id", aff_id)\
                .eq("network", network)\
                .limit(1)\
                .execute()
            
            if response.data and len(response.data) > 0:
                return response.data[0]['id']

            # 2. Cria novo
            new_affiliate = {
                "network": network,
                "aff_id": aff_id,
                "aff_name": aff_name,
                "status": "active"
            }
            
            insert_res = self.client.table("affiliates").insert(new_affiliate).execute()
            if insert_res.data and len(insert_res.data) > 0:
                return insert_res.data[0]['id']
            
        except Exception as e:
            logger.error(f"⚠️ Erro ao processar afiliado {aff_id}: {e}")
            return None
        return None

    async def get_checkout_by_code(self, code: str, account_id: str = None) -> Optional[Dict[str, Any]]:
        """
        Busca informações na tabela 'checkouts' usando o 'checkout_code'.
        Se houver duplicidade de código, usa o 'account_id' como critério de desempate.
        """
        if not code:
            return None

        try:
            response = self.client.table("checkouts")\
                .select("id, product_id, funnel_stage, funnel_number, account_id")\
                .eq("checkout_code", code)\
                .execute()

            rows = response.data
            if not rows:
                return None

            selected_row = None

            # Match por account_id
            if account_id:
                matches = [r for r in rows if str(r.get("account_id")) == str(account_id)]
                
                if matches:
                    selected_row = matches[0]
                elif len(rows) > 0:
                    selected_row = rows[0]
            else:
                selected_row = rows[0]

            return {
                "checkout_id": selected_row.get("id"),
                "product_id": selected_row.get("product_id"),
                "funnel_stage": selected_row.get("funnel_stage"),
                "funnel_number": selected_row.get("funnel_number")
            }

        except Exception as e:
            logger.error(f"⚠️ Erro ao buscar checkout code {code} (Account: {account_id}): {e}")
            return None

    async def get_checkout_via_product_match(self, raw_product_name: str) -> Optional[Dict[str, Any]]:
        """
        CONTINGÊNCIA INTELIGENTE (V2):
        Busca o checkout comparando o nome do produto no banco com o nome que veio da BuyGoods.
        
        Melhorias:
        1. Remove espaços de ambos os lados para casar "Visium Pro" com "VisiumPro".
        2. Ordena produtos por tamanho (decrescente) para evitar falsos positivos em nomes curtos.
        """
        if not raw_product_name:
            return None

        # "Visium Pro 6 bottles" -> "visiumpro6bottles"
        clean_input_name = raw_product_name.lower().replace(" ", "")
        
        found_product_id = None
        found_product_name = None

        try:
            # Busca lista de todos os produtos do banco
            response = self.client.table("products").select("id, name").execute()
            
            if not response.data:
                logger.warning("⚠️ Tabela de produtos vazia ou erro na leitura.")
                return None

            products_list = sorted(response.data, key=lambda x: len(x.get("name", "")), reverse=True)

            for product in products_list:
                # Ex: "VisiumPro" -> "visiumpro"
                db_name_original = product.get("name", "")
                db_name_clean = db_name_original.lower().replace(" ", "")
                
                if not db_name_clean:
                    continue

                # "visiumpro" está dentro de "visiumpro6bottles"? SIM.
                if db_name_clean in clean_input_name:
                    found_product_id = product.get("id")
                    found_product_name = db_name_original
                    logger.info(f"🔄 Contingência Sucesso: Input '{raw_product_name}' -> Match DB '{found_product_name}'")
                    break 

            # 5. Se achamos o produto, pegamos o checkout vinculado
            if found_product_id:
                checkout_res = self.client.table("checkouts")\
                    .select("id, product_id, funnel_stage, funnel_number")\
                    .eq("product_id", found_product_id)\
                    .limit(1)\
                    .execute()
                
                if checkout_res.data:
                    row = checkout_res.data[0]
                    return {
                        "checkout_id": row.get("id"),
                        "product_id": row.get("product_id"),
                        "funnel_stage": row.get("funnel_stage"),
                        "funnel_number": row.get("funnel_number")
                    }
                else:
                    logger.warning(f"⚠️ Produto '{found_product_name}' encontrado, mas não há checkouts cadastrados para ele.")
            
            else:
                logger.debug(f"❌ Contingência falhou: Nenhum produto corresponde a '{raw_product_name}'")

        except Exception as e:
            logger.error(f"⚠️ Erro na busca de contingência por nome: {e}")
            
        return None

    async def save_event(self, event_data: dict) -> List[dict]:
        """
        Salva o evento na tabela 'events'.
        Usa UPSERT baseado na constraint granular (network, order, action, date, time, total).
        """
        try:
            clean_data = {k: v for k, v in event_data.items() if v is not None}

            response = self.client.table("events").upsert(
                clean_data, 
                on_conflict="network, order_id, action_type, event_date, event_time, sale_total"
            ).execute()
            
            return response.data if response.data else []

        except Exception as e:
            logger.error(f"❌ Erro ao salvar evento no DB: {e}")
            raise e
        
    async def save_sales_status(self, status_data: Dict[str, Any]):
        """
        Insere um registro na tabela 'sales_status'.
        """
        try:
            response = self.client.table("sales_status").insert(status_data).execute()
            
            if response.data:
                logger.info(f"💾 Status '{status_data.get('status_type')}' salvo em sales_status. ID: {response.data[0].get('id')}")
                return response.data
            
        except Exception as e:
            logger.error(f"❌ Erro ao salvar sales_status: {e}")
            return None

# Instância Singleton
db_instance = DatabaseService()

def get_db():
    return db_instance