"""Protocol-1.0 BCN uplink response contract tests."""

from unittest.mock import MagicMock

import pytest

from secbaas.community.api.bcn import ChatEvent
from secbaas.community.core.service.bcn.uplink import BcnUplinkClient, BcnUplinkConfig


def _client() -> BcnUplinkClient:
    secret = MagicMock()
    secret.get_secret.return_value = "token-not-for-logs"
    return BcnUplinkClient(
        BcnUplinkConfig(base_url="https://bcn.invalid", provider_id="provider"),
        secret,
    )


@pytest.mark.parametrize("status", [200, 201, 204])
def test_2xx_is_success(status):
    result = _client()._parse_response(status, "")
    assert result.ok is True
    assert result.retryable is False
    assert result.category == "success"
    assert result.status_code == status


def test_explicit_dedup_409_is_success():
    result = _client()._parse_response(409, '{"deduplicated":true}')
    assert result.ok is True
    assert result.deduplicated is True
    assert result.category == "deduplicated"


@pytest.mark.parametrize("status", [408, 409, 425, 429, 500, 502, 503, 504])
def test_transient_status_is_retryable(status):
    result = _client()._parse_response(status, "")
    assert result.ok is False
    assert result.retryable is True
    assert result.category == "retryable_http"


@pytest.mark.parametrize("status", [400, 401, 403, 404, 410, 422])
def test_other_4xx_is_terminal(status):
    result = _client()._parse_response(status, "")
    assert result.ok is False
    assert result.retryable is False
    assert result.category == "terminal_http"


@pytest.mark.asyncio
async def test_send_event_uses_caller_event_id_without_logging_payload(monkeypatch):
    client = _client()
    captured = {}

    async def fake_retry(method, url, *, headers, payload, run_id):
        captured.update(
            method=method, url=url, headers=headers, payload=payload, run_id=run_id
        )
        return client._parse_response(200, "")

    monkeypatch.setattr(client, "_retry_request", fake_retry)
    event = ChatEvent(run_id="run-1", state="final")
    result = await client.send_event(event, "bot-1", event_id="run-1")

    assert result.ok is True
    assert captured["headers"]["X-BCN-Event-Id"] == "run-1"
    assert captured["payload"]["run_id"] == "run-1"
