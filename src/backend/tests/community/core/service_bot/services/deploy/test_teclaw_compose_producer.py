"""Unit tests for ``TeclawComposeProducer`` — engine_ext via plugin, then pin."""
from __future__ import annotations

from typing import Any

import pytest

from agentclaw.community.core.service_bot.services.deploy.teclaw_compose_producer import (
    TeclawComposeProducer,
)
from agentclaw.community.kernel.bot_config import BotConfigArtifact, McpManifest


class _StubComposer:
    def __init__(self, artifact: BotConfigArtifact) -> None:
        self._artifact = artifact

    def compose(self, req: Any) -> BotConfigArtifact:
        return self._artifact


class _MockEngineExtClient:
    """Test double for the EngineExtClient plugin — returns fixed opaque JSON."""

    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        self.calls: list[dict[str, Any]] = []

    def fetch(self, bot: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(bot)
        return self.payload


def _artifact() -> BotConfigArtifact:
    return BotConfigArtifact(schema_version=2, engine_type="teclaw", mcp=McpManifest())


@pytest.mark.unit
def test_engine_ext_fetched_via_plugin_and_frozen_verbatim() -> None:
    opaque = {"memory_ref": "nas://ws/MEMORY.md", "nested": {"x": [1, 2]}}
    client = _MockEngineExtClient(opaque)
    producer = TeclawComposeProducer(_StubComposer(_artifact()), client)

    result = producer.produce_artifact(
        {"bot_id": "b", "entity_id": "u", "owner_id": "u1"}, 3
    )

    # plugin consulted with the bot dict
    assert client.calls == [{"bot_id": "b", "entity_id": "u", "owner_id": "u1"}]
    # engine_ext payload carried verbatim, alongside the backend identity/stage keys.
    frozen = result.ext["config_artifact"]
    assert frozen["engine_ext"] == {
        **opaque,
        "bot_id": "b",
        "owner_id": "u1",
        "stage": "draft",
    }
    assert frozen["engine_type"] == "teclaw"


@pytest.mark.unit
def test_empty_engine_ext_from_noop_client() -> None:
    producer = TeclawComposeProducer(
        _StubComposer(_artifact()), _MockEngineExtClient({})
    )
    result = producer.produce_artifact({"bot_id": "b"}, 1)
    # Empty engine payload → only the backend identity/stage keys remain.
    assert result.ext["config_artifact"]["engine_ext"] == {
        "bot_id": "b",
        "owner_id": "",
        "stage": "draft",
    }
    assert result.success is True
