"""``BotConfigMaterialiser``: the three-stage contract folded over a kind mapping.

The materialiser never touches render-screen rows — the handler/service is the
boundary. So its tests prove the translation each stage makes: a desired
mapping becomes one intent carrying the mapping, that intent becomes one
``created`` planned step whose ``requires_write`` keeps ``is_noop`` honest, and
each declared kind's handler outcome is flattened into the report — rows onto
``entries``, ``removed`` rows onto ``removals``, a failed kind aborting the
remaining kinds as ``skipped``. A fake handler records what the materialiser
called and answers whichever outcome the test stages.
"""
from __future__ import annotations

import pytest

from agentclaw.community.core.apply_engine.context import ApplyContext
from agentclaw.community.core.apply_engine.desired_state import DesiredBotConfig
from agentclaw.community.core.apply_engine.materialisers.bot_config import (
    BotConfigMaterialiser,
)
from agentclaw.community.core.apply_engine.materialisers.bot_config_kind_registry import (
    BotConfigKindOutcome,
    BotConfigKindRegistry,
)
from agentclaw.community.core.apply_engine.outcomes import EntryOutcome


pytestmark = pytest.mark.asyncio


def _ctx() -> ApplyContext:
    return ApplyContext(
        bot_id="b_dst", owner_id="o", actor_id="a", entity_id="e",
        env="dev", tenant="", engine_type="openclaw", bot_type="arca",
        bot={}, capabilities=object(),
    )


class _FakeHandler:
    """Records the kind it was reached with and returns the staged outcome."""

    def __init__(self, kind: str, outcome: BotConfigKindOutcome) -> None:
        self.kind = kind
        self._outcome = outcome
        self.calls: list[tuple[str, tuple, bool]] = []

    def sync_kind(self, *, ctx, entries, is_empty):
        self.calls.append((self.kind, tuple(entries), is_empty))
        return self._outcome


def _materialiser(handlers) -> BotConfigMaterialiser:
    return BotConfigMaterialiser(BotConfigKindRegistry(handlers))


def _screen(*pairs: tuple[str, str]):
    return [{"name": n, "cdn_url": u} for n, u in pairs]


async def test_resolve_carries_the_kind_mapping_on_one_intent():
    mat = _materialiser({})
    entry = DesiredBotConfig({"render_screen": _screen(("lib", "u"))})
    result = await mat.resolve(_ctx(), [entry])
    assert result.failures == ()
    assert len(result.intents) == 1
    assert result.intents[0].identity == "bot_config"
    assert dict(result.intents[0].value) == {"render_screen": tuple(_screen(("lib", "u")))}


async def test_plan_is_one_created_step_that_requires_a_write():
    """``is_noop`` must not short-circuit a mapping whose rows are already
    converged — the destination read lives behind the handler, so the write
    has to run before 'nothing changed' can be known."""
    mat = _materialiser({})
    entry = DesiredBotConfig({"render_screen": _screen(("lib", "u"))})
    result = await mat.resolve(_ctx(), [entry])
    plan = await mat.plan(_ctx(), result.intents)
    assert len(plan.entries) == 1
    assert plan.entries[0].outcome == "created"
    assert plan.entries[0].requires_write is True
    assert plan.removals == ()
    assert plan.is_noop is False


async def test_write_flattens_each_kind_handler_outcome_into_rows_and_removals():
    render = _FakeHandler(
        "render_screen",
        BotConfigKindOutcome(
            kind="render_screen",
            entries=(("lib", "created", None), ("keep", "unchanged", None)),
            removals=(("stale", None),),
        ),
    )
    mat = _materialiser({"render_screen": render})
    entry = DesiredBotConfig({"render_screen": _screen(("lib", "u"), ("keep", "k"))})
    plan = await mat.plan(_ctx(), (await mat.resolve(_ctx(), [entry])).intents)
    written = await mat.write(_ctx(), plan)

    # The handler was called with the declared entries and a non-empty flag.
    assert render.calls == [("render_screen", tuple(_screen(("lib", "u"), ("keep", "k"))), False)]
    rows = {r.identity: r for r in written.entries}
    assert rows["lib"].outcome == EntryOutcome.CREATED
    assert rows["keep"].outcome == EntryOutcome.UNCHANGED
    assert written.removals == ("stale",)


async def test_write_dispatches_each_kind_independently_when_a_new_kind_is_added():
    """Two kinds, two handlers, both reached — the per-kind dispatch the v1
    design keeps off the construct layer, exercised with a stand-in second kind."""
    render = _FakeHandler("render_screen", BotConfigKindOutcome(kind="render_screen", entries=(("lib", "created", None),)))
    task = _FakeHandler("task_nav", BotConfigKindOutcome(kind="task_nav", entries=(("nav1", "created", None),)))
    mat = _materialiser({"render_screen": render, "task_nav": task})
    entry = DesiredBotConfig({
        "render_screen": _screen(("lib", "u")),
        "task_nav": [{"name": "nav1"}],
    })
    plan = await mat.plan(_ctx(), (await mat.resolve(_ctx(), [entry])).intents)
    written = await mat.write(_ctx(), plan)
    rows = {r.identity: r for r in written.entries}
    assert rows["lib"].outcome == EntryOutcome.CREATED
    assert rows["nav1"].outcome == EntryOutcome.CREATED


async def test_a_failed_kind_aborts_the_remaining_kinds_as_skipped():
    render = _FakeHandler(
        "render_screen",
        BotConfigKindOutcome(kind="render_screen", entries=(("lib", "failed", "could not create"),)),
    )
    task = _FakeHandler("task_nav", BotConfigKindOutcome(kind="task_nav", entries=(("nav1", "created", None),)))
    mat = _materialiser({"render_screen": render, "task_nav": task})
    entry = DesiredBotConfig({
        "render_screen": _screen(("lib", "u")),
        "task_nav": [{"name": "nav1"}],
    })
    plan = await mat.plan(_ctx(), (await mat.resolve(_ctx(), [entry])).intents)
    written = await mat.write(_ctx(), plan)

    rows = {r.identity: r for r in written.entries}
    assert rows["lib"].outcome == EntryOutcome.FAILED
    assert rows["lib"].reason == "could not create"
    # The remaining kind was never run — its handler recorded no calls — and
    # its declared rows are surfaced as SKIPPED.
    assert task.calls == []
    assert rows["nav1"].outcome == EntryOutcome.SKIPPED
    assert "task_nav" in rows["nav1"].reason


async def test_an_empty_bot_config_mapping_writes_nothing():
    """Declared bot_config with no kinds: a no-op, zero rows and zero removals."""
    mat = _materialiser({})
    entry = DesiredBotConfig({})
    plan = await mat.plan(_ctx(), (await mat.resolve(_ctx(), [entry])).intents)
    written = await mat.write(_ctx(), plan)
    assert written.entries == ()
    assert written.removals == ()


async def test_an_unregistered_kind_lands_on_the_noop_refusal_during_write():
    """A kind no DI-bound handler resolves reaches write as a refusal — the
    planner cannot see it, so the abort surfaces here, one failed row."""
    mat = _materialiser({})  # render_screen deliberately unregistered
    entry = DesiredBotConfig({"render_screen": _screen(("lib", "u"))})
    plan = await mat.plan(_ctx(), (await mat.resolve(_ctx(), [entry])).intents)
    written = await mat.write(_ctx(), plan)
    rows = {r.identity: r for r in written.entries}
    assert rows["lib"].outcome == EntryOutcome.FAILED
    assert "render_screen" in rows["lib"].reason
