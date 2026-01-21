from loguru import logger
from datetime import datetime
from typing import Dict, Any, Optional

def safe_float(value: Any) -> float:
    try:
        if value is None or value == "":
            return 0.0
        return float(value)
    except (ValueError, TypeError):
        return 0.0

def parse_date(date_str: str, source: str) -> tuple[Optional[str], Optional[str]]:
    try:
        dt = None
        if not date_str or date_str == "0000-00-00 00:00:00":
            return None, None

        if source == 'BuyGoods':
            dt = datetime.strptime(date_str, "%Y-%m-%d %H:%M:%S")
        elif source == 'DigiStore24':
            dt = datetime.fromisoformat(date_str)
        
        if dt:
            return dt.strftime("%Y-%m-%d"), dt.strftime("%H:%M:%S")
            
    except Exception as e:
        logger.error(f"Erro ao converter data '{date_str}' ({source}): {e}")
    
    return None, None

def normalize_payload(network: str, data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Normaliza e valida o payload.
    """
    if not data:
        raise ValueError(f"Payload vazio recebido de {network}")

    order_ref = data.get("order_id_global") or data.get("order_id")
    if not order_ref:
        raise ValueError(f"Campo 'order_id' obrigatório ausente em {network}.")

    order_ref = str(order_ref)
    logger.info(f"⚙️ Normalizando: {network} | Order: {order_ref}")

    # BuyGoods envia "1" (string) ou 1 (int) para testes.
    raw_is_test = data.get("is_test", "0")
    is_test_bool = str(raw_is_test) == "1"

    # Estrutura base do evento
    normalized = {
        "network": network,
        "payload": data,
        "created_at": datetime.now().isoformat(),
        "is_upsell": False,
        "is_test": is_test_bool,
        "order_details": {},
        "shipping_details": {},
        "_temp_checkout_code": None,
        "merchant_commission_rate": 0.0
    }

    try:
        # ==================== BUYGOODS ====================
        if network == "BuyGoods":
            # 1. Determina o tipo de ação primeiro
            raw_action = data.get("action_type", "sale")
            
            # Por padrão, assume a data de criação da venda (NEWORDER)
            date_source_col = "rr_createdate"
            
            # Se for Chargeback, tenta pegar a data específica do chargeback
            if raw_action == "chargeback" and data.get("date_chargedback"):
                date_source_col = "date_chargedback"
                
            # Se for Refund, tenta pegar a data específica do reembolso
            elif raw_action == "refund" and data.get("date_refunded"):
                date_source_col = "date_refunded"
            
            # Se for Rebill (Recorrência), usa a data da transação atual
            elif raw_action == "rebill" and data.get("transaction_date"):
                date_source_col = "transaction_date"

            d, t = parse_date(data.get(date_source_col, ""), "BuyGoods")
            
            full_name = data.get("name")
            if not full_name:
                fname = data.get("customer_firstname", "")
                lname = data.get("customer_lastname", "")
                full_name = f"{fname} {lname}".strip()
            
            raw_checkout_code = data.get("product_codename")

            val_merchant_comm = safe_float(data.get("merchant_commission"))
            val_total_clean = safe_float(data.get("total_clean"))
            
            merchant_rate = 0.0
            if val_total_clean > 0:
                merchant_rate = (val_merchant_comm / val_total_clean)

            normalized.update({
                "_temp_checkout_code": raw_checkout_code,
                "event_date": d,
                "event_time": t,
                "order_id": order_ref,
                "action_type": raw_action,
                "click_id": data.get("subid"),
                "lang": data.get("lang"),
                "customer_name": full_name,
                "customer_email": data.get("customer_emailaddress"),
                "customer_phone": data.get("customer_phone"),
                "currency": data.get("currency"),
                "sale_total": val_total_clean,
                "product_price": safe_float(data.get("product_price")),
                "aff_commission": safe_float(data.get("aff_commission")),
                "tax_amount": safe_float(data.get("taxes")),
                "merchant_commission": val_merchant_comm,
                "merchant_commission_rate": round(merchant_rate, 2),
                
                # "chargeback_fee": safe_float(data.get("chargeback_fee")), 
                
                "shipping_cost": safe_float(data.get("shipping_cost")),
                "payment_method": data.get("payment_method"),
                "payment_cardtype": data.get("payment_cardtype"),
                "sub_tiger_2": data.get("subid2"),
                "sub_tiger_3": data.get("subid3"),
                "sub_tiger_4": data.get("subid4"),
                "sub_tiger_5": data.get("subid5"),
                "is_upsell": str(data.get("flag_upsell")) == "1",
                "sale_url": data.get("salespage_url"),
                "order_details": {
                    "external_product_id": data.get("product_id"),
                    "external_checkout_code": raw_checkout_code, 
                    "external_affiliate_id": data.get("aff_id"),
                    "external_affiliate_name": data.get("aff_name"),
                    "product_name": data.get("product_name"),
                    "sku": data.get("sku"),
                    "funnel_codename": data.get("funnel_codename")
                },
                "shipping_details": {
                    "address": data.get("address"),
                    "city": data.get("city"),
                    "state": data.get("state"),
                    "zip": data.get("zip"),
                    "country": data.get("country")
                }
            })

        # ==================== DIGISTORE24 ====================
        elif network == "DigiStore24":
            d, t = parse_date(data.get("datetime_full", ""), "DigiStore24")
            
            fname = data.get("first_name", "")
            lname = data.get("last_name", "")
            full_name = f"{fname} {lname}".strip()

            # DigiStore às vezes envia 'is_test_payment' (booleano ou string)
            # Vamos garantir que pegamos se vier no payload genérico ou específico deles
            ds_is_test = is_test_bool # Usa o valor genérico capturado no início
            if "is_test_payment" in data:
                 # Se for string "true" ou bool True
                 ds_is_test = str(data.get("is_test_payment")).lower() in ["true", "1", "yes"]

            normalized.update({
                "event_date": d,
                "event_time": t,
                "order_id": order_ref,
                "action_type": data.get("transaction_type"),
                "click_id": data.get("cid"),
                "customer_name": full_name,
                "customer_email": data.get("email"),
                "currency": data.get("currency"),
                "sale_total": safe_float(data.get("amount_brutto")),
                "aff_commission": safe_float(data.get("amount_affiliate")),
                "tax_amount": safe_float(data.get("taxes")),
                "sub_tiger_2": data.get("sid2"),
                "sub_tiger_3": data.get("sid3"),
                "sub_tiger_4": data.get("sid4"),
                "sub_tiger_5": data.get("sid5"),
                "is_upsell": data.get("order_type") == "upsell",
                "is_test": ds_is_test, # Atualiza caso a lógica da DigiStore seja diferente
                "_temp_checkout_code": None,
                "order_details": {
                    "external_product_id": data.get("product_id"),
                    "external_affiliate_id": data.get("affiliate_id"),
                    "external_affiliate_name": data.get("affiliate_name"),
                    "product_name": data.get("product_name"),
                    "billing_type": data.get("billing_type"),
                    "merchant_id": data.get("merchant_id")
                },
                "shipping_details": {
                    "country": data.get("country")
                }
            })
            
        else:
            raise ValueError(f"Rede desconhecida: {network}")

        return normalized

    except Exception as e:
        if isinstance(e, ValueError):
            raise e
        logger.exception(f"Erro crítico normalização ({network}): {e}")
        raise e