"""The ``bot_config`` kind registry and the ``render_screen`` handler.

Two things to prove: the registry hands back a registered handler and falls
back to the noop refusal for an unregistered kind (a registry that drifted
from the validator's kind set must converge nothing and say why, not silently
succeed); and the ``RenderScreenSyncHandler`` is the thin adapter its docstring
claims — it builds ``RenderScreenRow`` objects from the declared entries,
hands them to the service, and translates the service's per-row outcomes into
the ``BotConfigKindOutcome`` the materialiser flattens, splitting ``removed``
into ``removals`` and the rest onto ``entries`` in declaration order.
"""
from __future__ import annotations

from unittest.mock import MagicMock

from agentclaw.community.core.apply_engine.context import ApplyContext
from agentclaw.community.core.apply_engine.materialisers.bot_config_kind_registry import (
    BotConfigKindRegistry,
    NoopBotConfigSyncHandler,
)
from agentclaw.community.core.apply_engine.materialisers.bot_config_render_screen_handler import (
    RenderScreenSyncHandler,
)
from agentclaw.community.core.bot_management.render_screen_service_protocol import (
    RenderScreenCopyResult,
    RenderScreenCopyRow,
    RenderScreenRow,
)


def _ctx() -> ApplyContext:
    return ApplyContext(
        bot_id="b_dst", owner_id="o", actor_id="a", entity_id="e",
        env="dev", tenant="", engine_type="openclaw", bot_type="arca",
        bot={}, capabilities=object(),
    )


class _FakeService:
    def __init__(self, rows: tuple[RenderScreenCopyRow, ...]):
        self._rows = rows
        self.seen_rows: tuple[RenderScreenRow, ...] | None = None
        self.seen_bot_id: str | None = None
        self.seen_actor_id: str | None = None

    def copy_render_screens(self, *, bot_id, owner_id, actor_id, rows):
        self.seen_rows = tuple(rows)
        self.seen_bot_id = bot_id
        self.seen_actor_id = actor_id
        return RenderScreenCopyResult(rows=self._rows)


def test_registry_returns_a_registered_handler():
    handler = MagicMock()
    handler.kind = "render_screen"
    registry = BotConfigKindRegistry({"render_screen": handler})
    assert registry.resolve("render_screen") is handler


def test_registry_falls_back_to_the_noop_refusal_for_an_unregistered_kind():
    registry = BotConfigKindRegistry({})
    handler = registry.resolve("task_nav")
    assert isinstance(handler, NoopBotConfigSyncHandler)
    assert handler.kind == "task_nav"
    outcome = handler.sync_kind(
        ctx=_ctx(), entries=[{"name": "x"}], is_empty=False,
    )
    assert outcome.ok is False
    assert outcome.entries[0][1] == "failed"
    assert "task_nav" in outcome.entries[0][2]


def test_render_screen_handler_builds_rows_and_translates_the_service_result():
    service = _FakeService(rows=(
        RenderScreenCopyRow("lib", "created"),
        RenderScreenCopyRow("stale", "removed"),
        RenderScreenCopyRow("keep", "unchanged"),
    ))
    handler = RenderScreenSyncHandler(service)
    assert handler.kind == "render_screen"

    outcome = handler.sync_kind(
        ctx=_ctx(),
        entries=[{"name": "lib", "cdn_url": "https://cdn/x.js"}, {"name": "keep", "cdn_url": "https://cdn/k.js"}],
        is_empty=False,
    )

    # The rows handed to the service are the declared entries, in order, with
    # the manifest's shape turned into RenderScreenRow — nothing else.
    assert service.seen_bot_id == "b_dst"
    assert service.seen_actor_id == "a"
    assert service.seen_rows == (
        RenderScreenRow("lib", "https://cdn/x.js"),
        RenderScreenRow("keep", "https://cdn/k.js"),
    )
    assert outcome.kind == "render_screen"
    # ``removed`` is split out into removals; entries keep declaration order.
    assert outcome.entries == (("lib", "created", None), ("keep", "unchanged", None))
    assert outcome.removals == (("stale", None),)
    assert outcome.ok is True


def test_render_screen_handler_marks_the_category_not_ok_on_a_failed_row():
    service = _FakeService(rows=(
        RenderScreenCopyRow("lib", "failed", "could not be created"),
    ))
    handler = RenderScreenSyncHandler(service)
    outcome = handler.sync_kind(
        ctx=_ctx(), entries=[{"name": "lib", "cdn_url": "https://cdn/x.js"}], is_empty=False,
    )
    assert outcome.ok is False
    assert outcome.entries == (("lib", "failed", "could not be created"),)
