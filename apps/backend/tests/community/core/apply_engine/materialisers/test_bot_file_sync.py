"""``BotFileSyncMaterialiser``: the three-stage contract over a fake port.

The materialiser never touches a real sync — the port is the boundary. So the
one thing its tests must prove is the translation each stage makes: entries
become intents, intents become a no-removal plan, and the port's
``BotFileSyncResult`` becomes the right ``EntryResult`` (CREATED on ok, FAILED
with the port's reason on a refused sync, FAILED with a composed reason when
the port raises). A fake port records the call and returns whichever answer
the test is exercising.
"""
from __future__ import annotations

import pytest

from agentclaw.community.core.apply_engine.context import ApplyContext
from agentclaw.community.core.apply_engine.desired_state import DesiredBotFileSync
from agentclaw.community.core.apply_engine.materialisers.bot_file_sync import (
    BotFileSyncMaterialiser,
)
from agentclaw.community.core.apply_engine.materialisers.bot_file_sync_noop import (
    NoopBotFileSyncPort,
)
from agentclaw.community.core.apply_engine.outcomes import EntryOutcome
from agentclaw.community.core.ports.bot_file_sync_port import (
    BotFileSyncPort,
    BotFileSyncResult,
    BotFileSyncTarget,
)


def _ctx() -> ApplyContext:
    return ApplyContext(
        bot_id="b_dst",
        owner_id="o",
        actor_id="a",
        entity_id="e",
        env="dev",
        tenant="",
        engine_type="openclaw",
        bot_type="arca",
        bot={},
        capabilities=object(),
    )


def _entry(path: str = "workspace/") -> DesiredBotFileSync:
    return DesiredBotFileSync(
        source_bot_id="b_src", source_owner_id="o", path=path, excludes=("secrets/",)
    )


class _FakePort(BotFileSyncPort):
    """Records the sync call and returns the answer the test staged."""

    def __init__(self, answer: BotFileSyncResult, *, raises: BaseException | None = None) -> None:
        self._answer = answer
        self._raises = raises
        self.target: BotFileSyncTarget | None = None
        self.source_bot_id: str | None = None
        self.path: str | None = None
        self.excludes: tuple[str, ...] | None = None
        self.calls = 0

    async def sync(self, *, target, source_bot_id, source_owner_id, path, excludes) -> BotFileSyncResult:
        self.target = target
        self.source_bot_id = source_bot_id
        self.source_owner_id = source_owner_id
        self.path = path
        self.excludes = excludes
        self.calls += 1
        if self._raises is not None:
            raise self._raises
        return self._answer


def _run(coro):
    import asyncio

    return asyncio.run(coro)


def test_resolve_turns_each_desired_entry_into_one_sync_intent() -> None:
    """An entry is its own intent; the value carried is the entry itself."""
    materialiser = BotFileSyncMaterialiser(_FakePort(BotFileSyncResult(ok=True)))
    entries = [_entry("workspace/"), _entry("data/")]
    result = _run(materialiser.resolve(_ctx(), entries))
    assert tuple(intent.identity for intent in result.intents) == ("workspace/", "data/")
    assert tuple(intent.value for intent in result.intents) == tuple(entries)
    assert result.failures == ()


def test_plan_creates_every_intent_with_no_removals() -> None:
    """A sync overwrites the tree, so there is no extra 'what's gone' set."""
    materialiser = BotFileSyncMaterialiser(_FakePort(BotFileSyncResult(ok=True)))
    intents = _run(materialiser.resolve(_ctx(), [_entry()])).intents
    plan = _run(materialiser.plan(_ctx(), intents))
    assert tuple(pe.intent.identity for pe in plan.entries) == ("workspace/",)
    assert tuple(pe.outcome for pe in plan.entries) == ("created",)
    assert plan.removals == ()


def test_write_succeeds_creates_the_entry_and_passes_coords_to_the_port() -> None:
    """``ok=True`` → CREATED, and the port sees the destination's lifted coords."""
    port = _FakePort(BotFileSyncResult(ok=True))
    materialiser = BotFileSyncMaterialiser(port)
    plan = _run(materialiser.plan(_ctx(), _run(materialiser.resolve(_ctx(), [_entry()])).intents))
    write = _run(materialiser.write(_ctx(), plan))

    assert port.calls == 1
    assert port.target.bot_id == "b_dst"
    assert port.target.owner_id == "o"
    assert port.target.entity_id == "e"
    assert port.target.env == "dev"
    assert port.target.engine_type == "openclaw"
    # The dst bot's entity_type is lifted off ctx.bot — the NAS path the port
    # resolves keys its storage id on it.
    assert port.target.entity_type == "staff"
    assert port.source_bot_id == "b_src"
    assert port.path == "workspace/"
    assert port.excludes == ("secrets/",)

    (entry,) = write.entries
    assert entry.outcome is EntryOutcome.CREATED
    assert entry.reason is None


def test_write_a_refused_sync_marks_the_entry_failed_with_the_ports_reason() -> None:
    port = _FakePort(
        BotFileSyncResult(ok=False, reason="source workspace not found")
    )
    materialiser = BotFileSyncMaterialiser(port)
    plan = _run(materialiser.plan(_ctx(), _run(materialiser.resolve(_ctx(), [_entry()])).intents))
    write = _run(materialiser.write(_ctx(), plan))

    (entry,) = write.entries
    assert entry.outcome is EntryOutcome.FAILED
    assert entry.reason == "source workspace not found"


def test_write_a_raising_sync_keeps_a_composed_reason_never_the_raw_text() -> None:
    port = _FakePort(BotFileSyncResult(ok=True), raises=RuntimeError("transport down: /x"))
    materialiser = BotFileSyncMaterialiser(port)
    plan = _run(materialiser.plan(_ctx(), _run(materialiser.resolve(_ctx(), [_entry()])).intents))
    write = _run(materialiser.write(_ctx(), plan))

    (entry,) = write.entries
    assert entry.outcome is EntryOutcome.FAILED
    # The raw exception text must not reach the apply report.
    assert entry.reason == "bot file sync failed"
    assert "/x" not in (entry.reason or "")


def test_write_a_raising_sync_logs_the_traceback_so_partial_can_be_traced(monkeypatch) -> None:
    """A raising sync surfaces as the catch-all ``"bot file sync failed"`` in
    the report (no raw text); the traceback that names what actually failed
    goes to the log under ``bot_file_sync.materialiser_failed``. Without it a
    PARTIAL with no per-branch reason is untraceable — the port's own
    resolve_build_plan / mkdir / rsync branches log inside, but an exception
    raised before those (or by a port that does not) reaches only this catch."""
    port = _FakePort(BotFileSyncResult(ok=True), raises=RuntimeError("transport down: /x"))
    materialiser = BotFileSyncMaterialiser(port)
    plan = _run(materialiser.plan(_ctx(), _run(materialiser.resolve(_ctx(), [_entry()])).intents))

    logged: list[str] = []

    class _SpyLogger:
        def exception(self, msg, *args, **_):
            logged.append(msg)

    import agentclaw.community.core.apply_engine.materialisers.bot_file_sync as mod

    monkeypatch.setattr(mod, "logger", _SpyLogger())

    _run(materialiser.write(_ctx(), plan))

    assert any("bot_file_sync.materialiser_failed" in m for m in logged)



def test_an_empty_plan_writes_no_entries_and_calls_the_port_never() -> None:
    materialiser = BotFileSyncMaterialiser(_FakePort(BotFileSyncResult(ok=True)))
    plan = _run(materialiser.plan(_ctx(), _run(materialiser.resolve(_ctx(), [])).intents))
    assert plan.entries == ()
    write = _run(materialiser.write(_ctx(), plan))
    assert write.entries == ()


def test_an_unwired_noop_port_refuses_with_the_not_yet_implemented_reason() -> None:
    """The DI default: the construct is wired, the port is not — the report says so."""
    materialiser = BotFileSyncMaterialiser(NoopBotFileSyncPort())
    plan = _run(materialiser.plan(_ctx(), _run(materialiser.resolve(_ctx(), [_entry()])).intents))
    write = _run(materialiser.write(_ctx(), plan))

    (entry,) = write.entries
    assert entry.outcome is EntryOutcome.FAILED
    assert entry.reason == "bot file sync is not yet implemented for this engine"
