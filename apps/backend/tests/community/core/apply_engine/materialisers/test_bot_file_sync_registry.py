"""``BotFileSyncRegistry`` and ``DelegatingBotFileSyncPort``: resolve by dst engine.

The delegating port is the one the materialiser calls; it asks the registry
which strategy the **destination** engine runs and delegates. An engine the
composition root did not register lands on the noop refusal, the same answer
the materialiser already translates to a FAILED entry — never a silent success.
"""
from __future__ import annotations

import asyncio

from agentclaw.community.core.apply_engine.materialisers.bot_file_sync_noop import (
    NoopBotFileSyncPort,
)
from agentclaw.community.core.apply_engine.materialisers.bot_file_sync_registry import (
    BotFileSyncRegistry,
    DelegatingBotFileSyncPort,
)
from agentclaw.community.core.ports.bot_file_sync_port import (
    BotFileSyncPort,
    BotFileSyncResult,
    BotFileSyncTarget,
)


def _target(engine: str = "openclaw") -> BotFileSyncTarget:
    return BotFileSyncTarget(
        bot_id="b_dst",
        owner_id="o",
        entity_id="e",
        env="dev",
        engine_type=engine,
        entity_type="staff",
    )


class _RecordingPort(BotFileSyncPort):
    """Returns a fixed answer and records that it was the one called."""

    def __init__(self, answer: BotFileSyncResult) -> None:
        self._answer = answer
        self.calls = 0

    async def sync(self, *, target, source_bot_id, source_owner_id, path, excludes) -> BotFileSyncResult:
        self.calls += 1
        self.last_target = target
        self.last_source_bot_id = source_bot_id
        self.last_source_owner_id = source_owner_id
        self.last_path = path
        self.last_excludes = excludes
        return self._answer


def _run(coro):
    return asyncio.run(coro)


def test_the_registry_resolves_a_registered_engine_to_its_strategy() -> None:
    openclaw = _RecordingPort(BotFileSyncResult(ok=True))
    registry = BotFileSyncRegistry({"openclaw": openclaw, "claude_code": _RecordingPort(BotFileSyncResult(ok=True))})
    assert registry.resolve("openclaw") is openclaw


def test_an_engine_the_root_did_not_register_resolves_to_the_noop_refusal() -> None:
    registry = BotFileSyncRegistry({"openclaw": _RecordingPort(BotFileSyncResult(ok=True))})
    strategy = registry.resolve("aicoding")
    assert isinstance(strategy, NoopBotFileSyncPort)


def test_an_empty_engine_type_resolves_to_the_noop_refusal() -> None:
    """A bot whose engine_type is unset or empty is unrouted — refuses, never
    guesses (the alternative — silently defaulting to openclaw — would write a
    NAS rsync into a bot that may not live on the NAS merge area)."""
    registry = BotFileSyncRegistry({"openclaw": _RecordingPort(BotFileSyncResult(ok=True))})
    assert isinstance(registry.resolve(""), NoopBotFileSyncPort)
    assert isinstance(registry.resolve(None), NoopBotFileSyncPort)


def test_the_delegating_port_calls_the_strategy_named_by_the_destination_engine() -> None:
    """The dst engine is the one whose storage model the sync writes into, so
    the delegate resolves on it and hands the call straight through."""
    openclaw = _RecordingPort(BotFileSyncResult(ok=True, reason=None))
    delegate = DelegatingBotFileSyncPort(
        BotFileSyncRegistry({"openclaw": openclaw})
    )

    result = _run(
        delegate.sync(
            target=_target("openclaw"),
            source_bot_id="b_src",
            source_owner_id="o",
            path="workspace/",
            excludes=("secrets/",),
        )
    )

    assert result.ok is True
    assert openclaw.calls == 1
    assert openclaw.last_source_bot_id == "b_src"
    assert openclaw.last_source_owner_id == "o"
    assert openclaw.last_path == "workspace/"
    assert openclaw.last_excludes == ("secrets/",)
    assert openclaw.last_target.engine_type == "openclaw"


def test_the_delegating_port_refuses_an_unregistered_dst_engine_via_noop() -> None:
    """A teclaw dst (or any unregistered engine) reaches the noop strategy, and
    the apply report reads "not yet implemented" rather than an empty success."""
    delegate = DelegatingBotFileSyncPort(
        BotFileSyncRegistry({"openclaw": _RecordingPort(BotFileSyncResult(ok=True))})
    )

    result = _run(
        delegate.sync(
            target=_target("teclaw"),
            source_bot_id="b_src",
            source_owner_id="o",
            path="workspace/",
            excludes=(),
        )
    )

    assert result.ok is False
    assert result.reason == "bot file sync is not yet implemented for this engine"
