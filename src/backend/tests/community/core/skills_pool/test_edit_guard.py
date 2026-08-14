from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest

from agentclaw.community.core.skills_pool.edit_guard import (
    SkillsPoolEditGuard,
    SkillsPoolEditPausedError,
)
from agentclaw.community.core.skills_pool.types import (
    BotSkillLayoutScope,
    BotSkillLayoutState,
    SkillLayout,
    SkillLayoutPhase,
)


SCOPE = BotSkillLayoutScope(env="pre", entity_id="entity-1", bot_id="bot-1")


class _Cache:
    def __init__(self) -> None:
        self.held: dict[str, str] = {}

    def acquire_lock(self, key: str, ttl: int = 30) -> str | None:
        if key in self.held:
            return None
        self.held[key] = "token"
        return "token"

    def release_lock(self, key: str, token: str) -> bool:
        if self.held.get(key) != token:
            return False
        del self.held[key]
        return True


class _Layouts:
    def __init__(self) -> None:
        self.state = BotSkillLayoutState(
            scope=SCOPE,
            active_layout=SkillLayout.POOL,
            target_layout=None,
            phase=SkillLayoutPhase.POOL_ACTIVE,
            migration_generation="generation-1",
            persisted=True,
        )

    def get(self, scope: BotSkillLayoutScope) -> BotSkillLayoutState:
        assert scope == SCOPE
        return self.state


def test_rollback_lease_excludes_new_local_edits() -> None:
    layouts = _Layouts()
    guard = SkillsPoolEditGuard(
        cache=_Cache(),
        layout_repository=layouts,
    )
    rollback_lease = guard.acquire_for_rollback(scope=SCOPE)
    assert rollback_lease is not None

    with pytest.raises(SkillsPoolEditPausedError, match="read-only"):
        guard.acquire_for_edit(scope=SCOPE)

    assert guard.release(rollback_lease)


def test_lock_identity_includes_entity_for_reused_bot_ids() -> None:
    guard = SkillsPoolEditGuard(
        cache=_Cache(),
        layout_repository=_Layouts(),
    )
    other_scope = replace(SCOPE, entity_id="entity-2")

    first = guard.acquire_for_rollback(scope=SCOPE)
    second = guard.acquire_for_rollback(scope=other_scope)

    assert first is not None
    assert second is not None
    assert first.key != second.key


@pytest.mark.asyncio
async def test_waiting_edit_acquires_lock_after_ordinary_edit_releases() -> None:
    guard = SkillsPoolEditGuard(
        cache=_Cache(),
        layout_repository=_Layouts(),
    )
    first = guard.acquire_for_edit(scope=SCOPE)

    waiting = asyncio.create_task(
        guard.acquire_for_edit_wait(scope=SCOPE, timeout_seconds=0.2)
    )
    await asyncio.sleep(0.02)
    assert not waiting.done()

    assert guard.release(first)
    second = await waiting
    assert guard.release(second)


def test_rollback_phase_rejects_edit_even_after_lock_becomes_available() -> None:
    layouts = _Layouts()
    layouts.state = replace(
        layouts.state,
        target_layout=SkillLayout.LEGACY,
        phase=SkillLayoutPhase.LEGACY_ROLLBACK_PREPARING,
    )
    guard = SkillsPoolEditGuard(
        cache=_Cache(),
        layout_repository=layouts,
    )

    with pytest.raises(SkillsPoolEditPausedError, match="rollback"):
        guard.acquire_for_edit(scope=SCOPE)
