import os
from loguru import logger
from fastapi import FastAPI, Request, HTTPException, BackgroundTasks, Depends, status
from services import normalize_payload, parse_date, safe_float
from database import get_db, DatabaseService
from dotenv import load_dotenv
from slack_notifier import SlackNotifier
import json

load_dotenv()

WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET")
SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN")

slack = SlackNotifier(
    bot_token=SLACK_BOT_TOKEN,
    default_channel="#None"
)

app = FastAPI(title="Webhook Normalizer")

# === FUNÇÃO AUXILIAR PARA RESOLVER CHECKOUT ===
async def resolve_checkout_data(db: DatabaseService, data: dict) -> dict:
    """
    Tenta encontrar os dados de checkout/produto usando duas estratégias:
    1. Busca exata pelo 'checkout_code' (product_codename).
       [NOVO]: Verifica 'account_id' para desambiguar códigos duplicados.
    2. Contingência: Busca parcial pelo 'product_name'.
    
    Retorna um dicionário com os campos encontrados ou vazio.
    """
    checkout_info = None
    
    # 1. Principal: Checkout Code
    checkout_code = data.get("_temp_checkout_code")
    
    # [NOVO] Captura o account_id do payload da BuyGoods para validação
    payload_account_id = data.get("payload", {}).get("account_id")

    if checkout_code:
        # Passamos o account_id para o DB tentar encontrar o match exato da conta
        checkout_info = await db.get_checkout_by_code(checkout_code, payload_account_id)
        
        if checkout_info:
            logger.debug(f"✅ Checkout vinculado via Código: {checkout_code} (Account: {payload_account_id})")
            return checkout_info
        else:
            logger.warning(f"⚠️ product_codename '{checkout_code}' (Account: {payload_account_id}) não encontrado. Tentando contingência por nome...")
            
            # Codename não encontrado na checkouts, chama o Slack
            slack.codename_not_found(
                network=data["network"],
                order_id=data.get("payload", {}).get("order_id_global", "N/A"),
                product=data.get("payload", {}).get("product_name", "N/A"),
                codename=data.get("payload", {}).get("product_codename", checkout_code),
                channel="#monitor-sites"
            )

    # 2. Contingência
    raw_product_name = data.get("order_details", {}).get("product_name")
    
    if raw_product_name:
        checkout_info = await db.get_checkout_via_product_match(raw_product_name)
        if checkout_info:
            return checkout_info

    return {}

# === TAREFA EM BACKGROUND ===
async def task_save_event(db: DatabaseService, data: dict):
    """
    Processa e enriquece os dados antes de salvar.
    Executa lógica específica para reembolsos, chargebacks E novas vendas.
    """   
    try:
        action_type = data.get("action_type")
        order_id = data.get("order_id")

        # [CORREÇÃO] Se o action_type for 'cancel', para a execução.
        if action_type == 'cancel':
            logger.info(f"🚫 action_type: CANCEL (Order: {order_id}). Ignorando este evento solenemente.")
            return

        # --- VINCULAR AFILIADO ---
        details = data.get("order_details", {})
        ext_aff_id = details.get("external_affiliate_id")
        if ext_aff_id:
            network = data.get("network")
            raw_aff_name = details.get("external_affiliate_name")
            aff_name = raw_aff_name if raw_aff_name else "Tiger Offers"
            aff_uuid = await db.get_or_create_affiliate(network, ext_aff_id, aff_name)
            if aff_uuid:
                data["affiliate_id"] = aff_uuid

        # --- VINCULAR CHECKOUT/PRODUTO ---
        found_info = await resolve_checkout_data(db, data)
        if found_info:
            data.update(found_info)
        data.pop("_temp_checkout_code", None)

        raw_payload = data.get("payload", {})

        # 1. Lógica para REFUND
        if action_type == "refund":
            # Ajustar Data
            refund_date_str = raw_payload.get("date_refunded")
            if refund_date_str:
                r_date, r_time = parse_date(refund_date_str, data.get("network"))
                if r_date:
                    data["event_date"] = r_date
                    data["event_time"] = r_time
            
            # Ajustar Valor
            raw_refund = raw_payload.get("refund_amount")
            if raw_refund:
                data["sale_total"] = safe_float(raw_refund)

        # 2. Lógica para REBILL (Recorrência)
        elif action_type == "rebill":
            trans_date_str = raw_payload.get("transaction_date")
            if trans_date_str:
                rb_date, rb_time = parse_date(trans_date_str, data.get("network"))
                if rb_date:
                    data["event_date"] = rb_date
                    data["event_time"] = rb_time
                    logger.debug(f"🔄 Rebill Data Ajustada: {rb_date} {rb_time}")

        # --- SALVAR NA TABELA EVENTS ---
        saved_records = await db.save_event(data)
        
        # Se duplicado, aborta.
        if not saved_records:
            logger.info(f"🔄 Evento duplicado ou não salvo (Order: {order_id}). Vida que segue...")
            return

        saved_event = saved_records[0]

        if not saved_event.get("id"):
            logger.warning(f"⚠️ Evento salvo mas sem ID retornado (Order: {order_id}). Pulando status.")
            return

        # --- IS_TEST ---
        if data.get("is_test") is True:
            logger.info(f"🛑 Evento de TESTE detectado (Order: {order_id}). Ignorando sales_status.")
            return

        # --- GRAVAR NA TABELA SALES_STATUS ---
        target_statuses = ["refund", "chargeback", "neworder", "sale", "upsell", "rebill"] 

        if action_type in target_statuses:
            
            network_name = data.get("network")
            
            # Pega as datas já ajustadas acima
            s_date = data.get("event_date")
            s_time = data.get("event_time")
            amount_affected = 0.0

            # === CENÁRIO A: PERDAS ===
            if action_type in ["refund", "chargeback"]:
                if network_name == "BuyGoods":
                    if action_type == "refund":
                        amount_affected = safe_float(raw_payload.get("refund_amount"))
                        if amount_affected == 0:
                             amount_affected = safe_float(raw_payload.get("total_clean"))
                        # Se por acaso a data ainda não estiver setada (fallback)
                        if not s_date:
                            date_str = raw_payload.get("date_refunded", "")
                            s_date, s_time = parse_date(date_str, network_name)
                            
                    elif action_type == "chargeback":
                        amount_affected = safe_float(raw_payload.get("total_amount_charged"))
                        if amount_affected == 0:
                            amount_affected = safe_float(raw_payload.get("total_clean"))
                else:
                    amount_affected = safe_float(raw_payload.get("amount"))
                    if amount_affected < 0: 
                        amount_affected = abs(amount_affected)

            # === CENÁRIO B: GANHOS (Sale, Upsell, Rebill) ===
            else:
                amount_affected = saved_event.get("sale_total")

            sale_status_data = {
                "event_id": saved_event.get("id"),
                "order_id": saved_event.get("order_id"),
                "affiliate_id": saved_event.get("affiliate_id"),
                "product_id": saved_event.get("product_id"),
                "network": saved_event.get("network"),
                "status_type": action_type,
                "status_reason": raw_payload.get("comments"),
                "status_date": s_date,
                "status_time": s_time,
                "amount_affected": amount_affected
            }
            
            try:
                await db.save_sales_status(sale_status_data)
            except Exception as e:
                if "23503" in str(e) or "foreign key constraint" in str(e).lower():
                    logger.info(f"🔄 Status ignorado pois o Evento Pai não foi persistido: {order_id}")
                else:
                    raise e
        
    except Exception as e:
        logger.exception(f"❌ Erro na Task Background: {e}")

# === ENDPOINTS ===

@app.post("/buygoods/{secret_token}")
async def webhook_buygoods(
    secret_token: str,
    request: Request,
    background_tasks: BackgroundTasks,
    db: DatabaseService = Depends(get_db)
):
    # 1. VALIDAÇÃO DE SEGURANÇA
    if not WEBHOOK_SECRET:
        logger.critical("⛔ WEBHOOK_SECRET não configurado no servidor!")
        raise HTTPException(status_code=500, detail="Configuration Error")
    
    if secret_token != WEBHOOK_SECRET:
        logger.warning(f"⛔ Acesso negado. Token inválido: {secret_token}")
        raise HTTPException(status_code=403, detail="Acesso Proibido")

    # 2. PROCESSAMENTO
    try:
        try:
            payload = await request.json()
        except:
            form_data = await request.form()
            payload = dict(form_data)
            
        normalized_event = normalize_payload("BuyGoods", payload)
        
        order_id = normalized_event.get("order_id")
        action = normalized_event.get("action_type")
        logger.info(f"🔔 BuyGoods: Recebido Order {order_id} ({action})")

        background_tasks.add_task(task_save_event, db, normalized_event)
        
        return {"status": "received", "id": order_id}
        
    except ValueError as ve:
        logger.warning(f"⚠️ Payload Inválido (BuyGoods): {ve}")
        return {"status": "ignored", "reason": str(ve)}
    except Exception as e:
        logger.exception("❌ Erro 500 no endpoint BuyGoods")
        raise HTTPException(status_code=500, detail="Internal Server Error")

@app.api_route("/digistore24/{secret_token}", methods=["GET", "POST"])
async def webhook_digistore(
    secret_token: str,
    request: Request,
    background_tasks: BackgroundTasks,
    db: DatabaseService = Depends(get_db)
):
    # Endpoint DigiStore (sem alterações por enquanto)
    if not WEBHOOK_SECRET:
        logger.critical("⛔ WEBHOOK_SECRET não configurado no servidor!")
        raise HTTPException(status_code=500, detail="Configuration Error")

    if secret_token != WEBHOOK_SECRET:
        logger.warning(f"⛔ Acesso negado (DigiStore). Token inválido: {secret_token}")
        raise HTTPException(status_code=403, detail="Acesso Proibido")

    try:
        if request.method == "GET":
            payload = dict(request.query_params)
        else:
            try:
                payload = await request.json()
            except:
                form_data = await request.form()
                payload = dict(form_data)

        try:
            normalized_event = normalize_payload("DigiStore24", payload)
        except ValueError as ve:
            logger.warning(f"⚠️ Payload Inválido (DigiStore24): {ve}")
            return {"status": "ignored", "reason": str(ve)}

        order_id = normalized_event.get("order_id")
        logger.info(f"🔔 DigiStore24: Recebido Order {order_id} via {request.method}")
        
        background_tasks.add_task(task_save_event, db, normalized_event)
        
        return {"status": "received", "id": order_id}
        
    except Exception as e:
        logger.exception("❌ Erro 500 no endpoint DigiStore24")
        raise HTTPException(status_code=500, detail="Internal Server Error")

@app.get("/codenames")
async def get_codenames():
    try:
        with open('codenames.json', 'r') as f:
            codenames = json.load(f)
    except FileNotFoundError:
        logger.error("Arquivo não encontrado.")

    return codenames