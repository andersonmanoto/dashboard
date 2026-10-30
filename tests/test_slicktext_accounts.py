import asyncio
from datetime import datetime
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../app")))

import pytest
from services import slicktext_accounts
from services.slicktext_accounts import (
    SEND_ATTEMPTS,
    active_list_targets,
    credentials_by_account,
    deactivation_fields,
    send_with_account_check,
)

ACTIVE = {"id": "acc-1", "name": "SMS CONTA 09", "brand_id": "35113", "api_key": "k1"}
CREDENTIALS = credentials_by_account([ACTIVE])


def test_credentials_by_account_skips_missing_key():
    rows = [ACTIVE, {"id": "acc-2", "name": "sem key", "brand_id": "1", "api_key": None}]
    assert set(credentials_by_account(rows)) == {"acc-1"}
    assert credentials_by_account(None) == {}



def test_deactivation_fields_sets_status_and_timestamp():
    fields = deactivation_fields()
    assert fields["status"] == "inactive"
    assert fields["allow_input"] is False
    assert datetime.fromisoformat(fields["deactivated_at"]).tzinfo is not None


def test_active_list_targets():
    mappings = [
        {"abandoned_list_id": "173009", "slicktext_accounts": {"id": "acc-1"}},
        # conta inactive (fora da get_slicktext_credentials): não envia
        {"abandoned_list_id": "162335", "slicktext_accounts": {"id": "acc-inativa"}},
        # conta active mas sem lista desse tipo: não envia
        {"abandoned_list_id": None, "slicktext_accounts": {"id": "acc-1"}},
        {"abandoned_list_id": "1", "slicktext_accounts": None},
    ]
    assert active_list_targets(mappings, "abandoned_list_id", CREDENTIALS) == [("173009", ACTIVE)]
    # PostgREST devolve objeto em vez de lista quando só tem uma linha
    assert active_list_targets(mappings[0], "abandoned_list_id", CREDENTIALS) == [("173009", ACTIVE)]


@pytest.fixture
def run(monkeypatch):
    """Roda send_with_account_check sem esperar o backoff, com GET /brands fake."""

    async def no_sleep(_):
        pass

    monkeypatch.setattr(slicktext_accounts.asyncio, "sleep", no_sleep)

    def go(results, brand_down):
        calls, deactivated = [], []

        async def send():
            calls.append(1)
            return results[len(calls) - 1]

        async def fake_brand_is_down(api_url, account):
            return brand_down

        async def deactivate(account_id):
            deactivated.append(account_id)

        monkeypatch.setattr(slicktext_accounts, "brand_is_down", fake_brand_is_down)
        result = asyncio.run(
            send_with_account_check(
                send, lambda r: r == "account_error", ACTIVE, api_url="x", deactivate=deactivate
            )
        )
        return result, len(calls), deactivated

    return go


def test_success_first_try(run):
    assert run(["ok"], brand_down=True) == ("ok", 1, [])


def test_other_error_is_not_retried(run):
    # erro que não é 401/404 (ex: 500) não entra no retry de conta
    assert run(["api_error"], brand_down=True) == ("api_error", 1, [])


def test_recovers_before_last_attempt(run):
    assert run(["account_error", "ok"], brand_down=True) == ("ok", 2, [])


def test_deactivates_when_brand_confirms(run):
    results = ["account_error"] * SEND_ATTEMPTS
    assert run(results, brand_down=True) == ("account_error", SEND_ATTEMPTS, ["acc-1"])


def test_keeps_active_when_brand_is_alive(run):
    # 404 no envio mas GET /brands 200: conta viva, problema é outro (list_id)
    results = ["account_error"] * SEND_ATTEMPTS
    assert run(results, brand_down=False) == ("account_error", SEND_ATTEMPTS, [])
