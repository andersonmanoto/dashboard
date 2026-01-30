import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../app")))

import pytest
from models.enums import ActionType, NetworkType
from models.schemas import NormalizedEvent
from services.normalizer import PayloadNormalizer


@pytest.fixture
def normalizer():
    return PayloadNormalizer()


# ========== DADOS DE EXEMPLO (MOCKS REAIS) ==========

PAYLOAD_BUYGOODS_REAL = {
    "sid": "",
    "sku": "6296-WGHTLBLND-231:1",
    "zip": "86327",
    "city": "Dewey",
    "cogs": "0.00",
    "lang": "",
    "name": "Tamara Giblin",
    "vid1": "",
    "vid2": "",
    "vid3": "",
    "state": "Arizona",
    "subid": "",
    "taxes": "7.9",
    "token": "626eb6f8318753879c780159585eeaed",
    "total": "$96.80",
    "aff_id": "42",
    "subid2": "",
    "subid3": "",
    "subid4": "",
    "subid5": "",
    "address": "10263  E Buckskin Drive",
    "buy_url": "",
    "country": "United States",
    "is_free": "0",
    "is_test": "0",
    "product": "Physical Product: Reduburn 1 Bottle New",
    "sessid2": "vohmO3icR9f4lNT",
    "user_id": "14774",
    "aff_name": "HelpGrid Calls",
    "comments": "",
    "currency": "USD",
    "order_id": "142891",
    "ipaddress": "66.249.84.142",
    "token_ipn": "e87e4f7ac60729aa51cad1288f4ce730",
    "account_id": "11265",
    "help_token": "3b4c5be1f996dccbd8f330f1c9088564",
    "order_date": "January 22, 2026",
    "product_id": "63",
    "action_type": "neworder",
    "billing_zip": "86327",
    "flag_upsell": "0",
    "funnel_step": "",
    "register_id": "20003",
    "total_clean": "96.80",
    "total_comma": "$96.80",
    "billing_city": "Dewey",
    "customer_zip": "86327",
    "product_name": "ABC 6 Bottles",
    "referrer_sid": "",
    "referrer_url": "",
    "shipping_zip": "86327",
    "was_canceled": "0",
    "accrual_total": "0.00",
    "billing_state": "Arizona",
    "charges_count": "",
    "customer_city": "Dewey",
    "customer_name": "Tamara Giblin",
    "date_canceled": "0000-00-00 00:00:00",
    "flag_frontend": "1",
    "flag_sms_sent": "0",
    "order_date_eu": "22/01/2026",
    "order_details": "Reduburn 1 Bottle New",
    "payment_terms": (
        "&#36;79.00<br>+ &#36;9.90 Shipping & Handling<br>+ &#36;7.9 Taxes"
    ),
    "product_price": "79.00",
    "referrer_self": "",
    "rr_createdate": "2026-01-01 16:12:59",
    "salespage_url": "",
    "shipping_city": "Dewey",
    "shipping_cost": "9.90",
    "shipping_name": "Tamara Giblin",
    "was_fulfilled": "0",
    "aff_commission": "46.46",
    "customer_phone": "4803750785",
    "customer_state": "Arizona",
    "lead_ticket_id": "5182.41843502",
    "payment_method": "Visa ending with 3663",
    "payment_status": "Completed",
    "phone_helpgrid": "+13025641687",
    "shipping_state": "Arizona",
    "traffic_source": "",
    "RUNNING_OFFLINE": "1",
    "billing_address": "10263  E Buckskin Drive",
    "billing_country": "United States",
    "country_2letter": "US",
    "coupon_discount": "0.00",
    "creditcards_zip": "86327",
    "funnel_codename": "",
    "order_date_time": "January 22, 2026,  4:12 PM",
    "order_id_global": "96ZZZZZZ",
    "sale_saved_date": "0000-00-00 00:00:00",
    "shipping_method": "0",
    "shipping_status": "Shipping request not sent",
    "total_collected": "0.00",
    "billing_lastname": "Giblin",
    "creditcards_city": "Dewey",
    "customer_country": "United States",
    "date_fulfillment": "0000-00-00 00:00:00",
    "flag_autofulfill": "0",
    "klarna_agreement": "",
    "payment_cardtype": "Visa",
    "product_codename": "abc9d",
    "product_quantity": "1",
    "product_subtotal": "79.00",
    "sale_saved_agent": "0",
    "shipping_address": "10263  E Buckskin Drive",
    "shipping_country": "United States",
    "billing_firstname": "Tamara",
    "creditcards_state": "Arizona",
    "customer_lastname": "Giblin",
    "external_order_id": "",
    "hidden_cardnumber": "4081-xxxx-xxxx-3663",
    "payment_cardlast4": "3663",
    "picture_thumbnail": "https://cdn.softwareprojects.com/...",
    "total_outstanding": "0.00",
    "amount_in_currency": "$96.80",
    "browser_user_agent": "",
    "cart_product_image": "https://cdn.softwareprojects.com/...",
    "customer_firstname": "Tamara",
    "payment_cardfirst4": "4081",
    "creditcards_address": "10263  E Buckskin Drive",
    "creditcards_country": "United States",
    "merchant_commission": "7.78",
    "product_url_encoded": "",
    "shipping_cost_total": "9.90",
    "shipping_tracking_id": "",
    "total_amount_charged": "88.9",
    "customer_emailaddress": "louigi272@gmail.com",
    "paypal_native_agreement": "",
    "storecheckedoutcarts_id": "11619",
    "total_amount_charged_in_currency": "0.00",
}

PAYLOAD_DIGISTORE_SALE = {
    "order_id": "DS-ABCDE",
    "transaction_type": "sale",
    "datetime_full": "2024-03-20T15:45:00+00:00",
    "amount_brutto": "49.90",
    "amount_affiliate": "20.00",
    "email": "maria@email.com",
    "first_name": "Maria",
    "last_name": "Pereira",
    "product_id": "12345",
    "is_test_payment": "true",
}


# ========== TESTES BUYGOODS (ATUALIZADO) ==========
def test_normalize_buygoods_real_payload(normalizer):
    """Testa uma venda REAL da BuyGoods com todas as complexidades."""
    event = normalizer.normalize(NetworkType.BUYGOODS, PAYLOAD_BUYGOODS_REAL)

    assert isinstance(event, NormalizedEvent)
    assert event.network == NetworkType.BUYGOODS

    # Valida IDs críticos
    assert event.order_id == "96ZZZZZZ"  # order_id_global
    assert event.action_type == ActionType.NEWORDER  # payload tem "neworder"

    # Valida conversão de data (usa rr_createdate como fallback padrão)
    # Payload: "2026-01-01 16:12:59"
    assert event.event_date == "2026-01-01"
    assert event.event_time == "16:12:59"

    # Valida valores financeiros
    assert event.sale_total == 96.80  # total_clean
    assert event.shipping_cost == 9.90
    assert event.tax_amount == 7.9
    assert event.merchant_commission == 7.78

    # Valida cálculo de taxa (7.78 / 96.80 = 0.0803...) -> Arredonda para 0.08
    assert event.merchant_commission_rate == 0.08

    # Valida concatenação do nome (Tamara + Giblin)
    assert event.customer_name == "Tamara Giblin"
    assert event.customer_email == "louigi272@gmail.com"

    # Valida detalhes do pedido
    assert event.order_details.external_checkout_code == "abc9d"  # product_codename
    assert event.order_details.product_name == "ABC 6 Bottles"
    assert event.order_details.external_affiliate_id == "42"

    # Flags
    assert event.is_test is False


# ========== TESTES DIGISTORE24 ==========
def test_normalize_digistore_sale(normalizer):
    event = normalizer.normalize(NetworkType.DIGISTORE24, PAYLOAD_DIGISTORE_SALE)
    assert event.network == NetworkType.DIGISTORE24
    assert event.event_date == "2024-03-20"
    assert event.sale_total == 49.90
    assert event.is_test is True
