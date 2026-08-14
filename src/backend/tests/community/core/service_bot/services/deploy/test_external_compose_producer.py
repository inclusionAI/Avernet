"""Unit tests for the ExternalComposeProducer base skeleton.

Verifies the producer owns ``engine_ext`` (injected via the hook, carried
verbatim into the artifact, never interpreted) and pins the composed artifact —
refs-only, no separate materialization store — straight onto ``ext``.
"""
from __future__ import annotations

from typing import Any

import pytest

from agentclaw.community.core.config_compose.models import ComposeRequest
from agentclaw.community.core.service_bot.services.deploy.external_compose_producer import (
    ExternalComposeProducer,
)
from agentclaw.community.kernel.bot_config import BotConfigArtifact, McpManifest


class _StubComposer:
    """Records the ``ComposeRequest`` the producer builds (the real composer's API)."""

    def __init__(self, artifact: BotConfigArtifact) -> None:
        self._artifact = artifact
        self.calls: list[ComposeRequest] = []

    def compose(self, req: ComposeRequest) -> BotConfigArtifact:
        self.calls.append(req)
        return self._artifact


def _artifact(engine_ext: dict[str, Any] | None = None) -> BotConfigArtifact:
    return BotConfigArtifact(
        schema_version=1,
        engine_type="teclaw",
        mcp=McpManifest(),
        engine_ext=engine_ext or {},
    )


@pytest.mark.unit
def test_base_fetch_engine_ext_is_empty() -> None:
    producer = ExternalComposeProducer(composer=_StubComposer(_artifact()))
    assert producer._fetch_engine_ext({"bot_id": "b"}) == {}


@pytest.mark.unit
def test_engine_ext_from_subclass_is_injected_verbatim() -> None:
    opaque = {"memory_ref": "nas://ws/MEMORY.md", "nested": {"a": [1, 2]}}

    class _Teclaw(ExternalComposeProducer):
        def _fetch_engine_ext(self, bot: dict[str, Any]) -> dict[str, Any]:
            return opaque

    composer = _StubComposer(_artifact(engine_ext={}))
    producer = _Teclaw(composer=composer)

    built = producer._compose_with_engine_ext(
        {"bot_id": "b", "owner_id": "u1", "bot_name": "Support Bot"}, 7
    )

    # engine_ext carried verbatim, plus the backend-owned identity/stage keys.
    assert built.engine_ext == {
        **opaque,
        "bot_id": "b",
        "owner_id": "u1",
        "bot_name": "Support Bot",
        "stage": "draft",
    }
    assert built.engine_type == "teclaw"
    # the composer was driven with a ComposeRequest (not a bot dict) carrying version.
    assert len(composer.calls) == 1
    assert composer.calls[0].bot_id == "b"
    assert composer.calls[0].version == 7


@pytest.mark.unit
def test_compose_request_maps_bot_row_fields() -> None:
    """Regression: the producer must adapt the publish-flow ``bot`` row
    (BotService.get_bot → ac_bots) into a correct ``ComposeRequest`` —
    owner_id→user_id, active_engine→engine_type — not pass the dict straight to
    the composer (which takes a ComposeRequest, so the dict path raised)."""
    composer = _StubComposer(_artifact())
    producer = ExternalComposeProducer(composer=composer)

    producer.produce_artifact(
        {
            "bot_id": "bot7",
            "entity_id": "staff_u1",
            "entity_type": "staff",
            "owner_id": "u1",
            "active_engine": "teclaw",
        },
        9,
    )

    req = composer.calls[0]
    assert isinstance(req, ComposeRequest)
    assert (req.entity_id, req.bot_id, req.user_id, req.engine_type, req.entity_type) == (
        "staff_u1", "bot7", "u1", "teclaw", "staff",
    )
    assert req.version == 9


class _ExtProducer(ExternalComposeProducer):
    """Subclass supplying engine_ext via the hook (the producer owns it)."""

    def __init__(self, composer, ext) -> None:
        super().__init__(composer=composer)
        self._ext = ext

    def _fetch_engine_ext(self, bot):
        return self._ext


@pytest.mark.unit
def test_produce_artifact_pins_refs_only_artifact() -> None:
    producer = _ExtProducer(
        composer=_StubComposer(_artifact()),
        ext={"memory_ref": "nas://ws/MEMORY.md"},
    )

    result = producer.produce_artifact(
        {"bot_id": "b", "entity_id": "u", "owner_id": "u1"}, 4
    )

    assert result.success is True
    # Model 1: no ARCA mount chain, no separate materialization store, no freeze
    # ceremony — just the refs-only artifact pinned onto ext for non-mount delivery
    # at verify/online. engine_ext rides inside the artifact (not a separate key),
    # carrying the engine-owned payload + the backend identity/stage keys.
    assert set(result.ext) == {"config_artifact"}
    assert result.ext["config_artifact"]["engine_ext"] == {
        "memory_ref": "nas://ws/MEMORY.md",
        "bot_id": "b",
        "owner_id": "u1",
        "bot_name": "",
        "stage": "draft",
    }


@pytest.mark.unit
def test_backend_keys_injected_with_defaults_and_win_collision() -> None:
    # Engine payload tries to set bot_id/bot_name/stage; backend keys are written
    # last and win.
    producer = _ExtProducer(
        composer=_StubComposer(_artifact()),
        ext={
            "bot_id": "ENGINE_WINS_NOT",
            "bot_name": "ENGINE_WINS_NOT",
            "stage": "engine-set",
            "keep": "me",
        },
    )

    # owner_id / bot_name absent on the bot row → default to "".
    built = producer._compose_with_engine_ext({"bot_id": "b7"}, 1)

    assert built.engine_ext == {
        "keep": "me",
        "bot_id": "b7",
        "owner_id": "",
        "bot_name": "",
        "stage": "draft",
    }
