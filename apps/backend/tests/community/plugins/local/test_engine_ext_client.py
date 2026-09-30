"""Unit tests for the EngineExtClient local Noop + a Mock test double."""
from __future__ import annotations

from typing import Any

import pytest

from agentclaw.community.plugin_api.engine_ext_client import EngineExtClient
from agentclaw.community.plugins.local.engine_ext_client import LocalEngineExtClient


class MockEngineExtClient(EngineExtClient):
    """Test double — returns a fixed, opaque payload (engine_ext) per bot."""

    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def fetch(self, bot: dict[str, Any]) -> dict[str, Any]:
        return dict(self._payload)


@pytest.mark.unit
def test_local_engine_ext_client_returns_empty() -> None:
    assert LocalEngineExtClient().fetch({"bot_id": "b", "engine_type": "openclaw"}) == {}


@pytest.mark.unit
def test_local_satisfies_protocol() -> None:
    assert isinstance(LocalEngineExtClient(), EngineExtClient)


@pytest.mark.unit
def test_mock_returns_fixed_opaque_payload() -> None:
    opaque = {"memory_ref": "nas://ws/MEMORY.md", "nested": {"a": [1, 2]}}
    client = MockEngineExtClient(opaque)
    got = client.fetch({"bot_id": "b"})
    assert got == opaque
    # Defensive copy — callers can't mutate the double's payload.
    got["x"] = 1
    assert "x" not in client.fetch({"bot_id": "b"})
