"""Focused tests for local mixed-singlebox BCN uplink overrides."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from secbaas.community.api.bcn import ChatEvent
from secbaas.community.core.service.bcn.uplink._uplink_client import (
    BcnUplinkClient,
    BcnUplinkConfig,
)


def test_local_uplink_token_precedes_the_secret_store(monkeypatch):
    secret_store = MagicMock()
    secret_store.get_secret.side_effect = RuntimeError("must not read online secret")
    client = BcnUplinkClient(
        BcnUplinkConfig(base_url="https://online.example", provider_id="provider"),
        secret_store,
    )
    monkeypatch.setenv("BCS_BAAS_UPLINK_TOKEN", "runtime-loopback-token")

    headers = client._build_headers(event_id="event-1", bot_id="bot-1")

    assert headers["Authorization"] == "Bearer runtime-loopback-token"
    secret_store.get_secret.assert_not_called()


@pytest.mark.asyncio
async def test_local_uplink_url_precedes_config(monkeypatch):
    client = BcnUplinkClient(
        BcnUplinkConfig(base_url="https://online.example", provider_id="provider"),
        MagicMock(),
    )
    monkeypatch.setenv("BCS_BAAS_UPLINK_URL", "http://127.0.0.1:28083")
    monkeypatch.setenv("BCS_BAAS_UPLINK_TOKEN", "runtime-loopback-token")
    client._retry_request = AsyncMock(return_value=MagicMock())

    await client.send_event(ChatEvent(run_id="run-1", state="final"), bot_id="bot-1")

    assert client._retry_request.await_args.args[:2] == (
        "POST",
        "http://127.0.0.1:28083/bot/events",
    )
