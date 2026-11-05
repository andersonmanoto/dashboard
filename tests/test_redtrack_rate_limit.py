import asyncio
import os
import sys
from types import SimpleNamespace

import httpx
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.services import redtrack_service
from app.services.redtrack_service import RedTrackAPI, RedTrackAPIError

TOO_MANY = '{"error":"Too many requests. Please try again later."}'


def make_api():
    return RedTrackAPI(SimpleNamespace(redtrack_api_key="k", redtrack_user_id="u"))


def run_get(statuses, monkeypatch, headers=None):
    responses = iter(statuses)
    calls, sleeps = [], []

    def handler(request):
        calls.append(request)
        status = next(responses)
        return httpx.Response(
            status, text="[]" if status == 200 else TOO_MANY, headers=headers or {}
        )

    async def fake_sleep(s):
        sleeps.append(s)

    monkeypatch.setattr(redtrack_service.asyncio, "sleep", fake_sleep)

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await make_api()._get(client, "/report")

    return asyncio.run(go()), len(calls), sleeps


def test_429_then_ok(monkeypatch):
    result, calls, sleeps = run_get([429, 429, 200], monkeypatch)
    assert result == [] and calls == 3 and sleeps == [2, 4]


def test_429_respects_retry_after(monkeypatch):
    _, _, sleeps = run_get([429, 200], monkeypatch, headers={"Retry-After": "7"})
    assert sleeps == [7]


def test_429_gives_up(monkeypatch):
    with pytest.raises(RedTrackAPIError, match="429"):
        run_get([429] * redtrack_service.RATE_LIMIT_ATTEMPTS, monkeypatch)


def test_other_error_not_retried(monkeypatch):
    with pytest.raises(RedTrackAPIError, match="500"):
        run_get([500], monkeypatch)
