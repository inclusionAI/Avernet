"""Rule 25 conformance — EngineExtClient.

Consumer under test: ``TeclawComposeProducer`` (the only ``EngineExtClient``
consumer — ``core/service_bot/services/deploy/teclaw_compose_producer.py``). At
build time it calls ``engine_ext_client.fetch(bot)`` and freezes the returned
**opaque** payload into the version artifact's ``engine_ext`` — carried verbatim,
never interpreted.

Two contract facets, matching the plugin's two flavors:

* **Noop (``LocalEngineExtClient``, the ``world``-injected local impl)** — every
  ``fetch`` returns ``{}`` (dev has no engine to ask). The consumer must surface
  that as an empty ``engine_ext`` and still succeed.
* **Mock (same impl, ``MockSeam.set_response``)** — a fixed opaque JSON the
  consumer must carry through **byte-for-byte**, including nested structure, with
  no interpretation. This is the executable spec for engine-ext opacity.

Plugin-hit assertion: ``LocalEngineExtClient`` is a :class:`MockSeam`, so each
``fetch`` is recorded in ``.calls`` — we assert the consumer actually routed
through the plugin (a bypass would leave ``.calls`` empty and ``engine_ext``
unset).

Local impl construction (not the ``world`` fixture): ``EngineExtClient`` has no
DI binding yet — its only consumer is ``TeclawComposeProducer`` wired via DI, which
is the 🔒 Task-15 (engine-handshake) path. So, exactly like the
``DeviceFileSystemPlugin`` contract suite (``test_device_filesystem.py``), this
constructs the trivially-instantiable local impl directly and hands it to the
consumer's constructor — the same instance the consumer would receive once Task 15
binds it. The local impl IS the executable spec either way.
"""
from __future__ import annotations

from typing import Any

from agentclaw.community.core.service_bot.services.deploy.teclaw_compose_producer import (
    TeclawComposeProducer,
)
from agentclaw.community.kernel.bot_config import BotConfigArtifact, McpManifest
from agentclaw.community.plugin_api.engine_ext_client import EngineExtClient
from agentclaw.community.plugins.local.engine_ext_client import LocalEngineExtClient


class _StubComposer:
    """Engine-agnostic ``ConfigComposerLike`` — returns a fixed artifact so the
    test isolates the engine_ext fetch+pin the consumer adds on top."""

    def compose(
        self, bot: dict[str, Any], *, version: int | None = None
    ) -> BotConfigArtifact:
        return BotConfigArtifact(schema_version=3, engine_type="teclaw", mcp=McpManifest())


# ── the local impl satisfies the Protocol contract directly ──────────────────


def test_local_engine_ext_client_returns_empty() -> None:
    """The local impl is an ``EngineExtClient`` whose contract is: every bot's
    engine_ext is empty (no external engine in dev)."""
    client = LocalEngineExtClient()
    assert isinstance(client, EngineExtClient)
    assert client.fetch({"bot_id": "b1", "entity_id": "u1"}) == {}


# ── consumer ↔ Protocol conformance: Noop flavor ─────────────────────────────


def test_consumer_surfaces_empty_engine_ext_from_local_noop() -> None:
    """``TeclawComposeProducer`` driven by the local impl: the Noop's ``{}`` is
    fetched via the plugin and surfaced as an empty engine *payload* — the producer
    then adds the backend identity + draft-stage keys on top — and the build still
    succeeds."""
    client = LocalEngineExtClient()
    producer = TeclawComposeProducer(_StubComposer(), client)

    result = producer.produce_artifact({"bot_id": "b1", "entity_id": "u1"}, 3)

    assert result.success is True
    # Engine payload empty (Noop); producer injects identity + draft stage (owner_id
    # defaults to "" — no owner_id on this bot row).
    assert result.ext["config_artifact"]["engine_ext"] == {
        "bot_id": "b1",
        "owner_id": "",
        "stage": "draft",
    }
    # ...and the plugin was actually consulted (MockSeam records the call) — no bypass.
    assert [c.method for c in client.calls_to("fetch")] == ["fetch"]


# ── consumer ↔ Protocol conformance: Mock flavor (opacity) ───────────────────


def test_consumer_carries_mock_engine_ext_verbatim() -> None:
    """Mock flavor (``MockSeam.set_response``): an arbitrary nested opaque payload
    is carried through the consumer **byte-for-byte**, proving engine-ext opacity —
    the backend stores/freezes it without interpreting its shape. The producer adds
    the backend identity + draft-stage keys *alongside* it (additive, never touching
    the engine-owned keys).

    A fresh local impl instance is used (not the ``world`` singleton) so the mock
    response never leaks into other contract suites sharing the DI container."""
    opaque = {
        "memory_ref": "oss://ws/MEMORY.md",
        "identity_ref": "oss://ws/IDENTITY.md",
        "nested": {"weights": [1, 2, 3], "flag": True, "none": None},
    }
    client = LocalEngineExtClient()
    client.set_response("fetch", opaque)
    producer = TeclawComposeProducer(_StubComposer(), client)

    result = producer.produce_artifact({"bot_id": "b2", "entity_id": "u2"}, 5)

    pinned = result.ext["config_artifact"]
    # Opaque payload carried byte-for-byte (verbatim, not merged/normalized), with
    # the backend identity + draft-stage keys added alongside.
    assert pinned["engine_ext"] == {
        **opaque,
        "bot_id": "b2",
        "owner_id": "",
        "stage": "draft",
    }
    # the engine-owned keys are untouched (opacity preserved)...
    for k, v in opaque.items():
        assert pinned["engine_ext"][k] == v
    # ...and the opaque payload was not aliased into the artifact.
    assert pinned["engine_ext"] is not opaque
    assert client.calls_to("fetch")  # plugin-hit


def test_local_impl_fetch_is_pure_and_repeatable() -> None:
    """The Noop contract is stable: repeated fetches with different bot dicts all
    yield ``{}`` (no hidden state, no per-bot branching)."""
    client = LocalEngineExtClient()
    assert client.fetch({"bot_id": "a"}) == {}
    assert client.fetch({"bot_id": "b", "engine_type": "teclaw"}) == {}
    assert client.fetch({}) == {}
