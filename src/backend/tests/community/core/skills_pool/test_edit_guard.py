from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest

from agentclaw.community.core.skills_pool.edit_guard import (
    SkillsPoolEditBusyError,
    SkillsPoolEditGuard,
    SkillsPoolEditLockUnavailableError,
    SkillsPoolEditRollbackError,
)
from agentclaw.community.plugin_api.cache import CacheLockInfrastructureError
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

    def acquire_lock_strict(self, key: str, ttl: int = 30) -> str | None:
        return self.acquire_lock(key, ttl)

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


class _Bots:
    def __init__(self, *, engine: str = "openclaw") -> None:
        self.engine = engine

    def get_by_id_and_entity(self, bot_id: str, entity_id: str):
        assert bot_id == SCOPE.bot_id
        assert entity_id == SCOPE.entity_id
        return {"env": SCOPE.env, "active_engine": self.engine}


class _UnavailableCache(_Cache):
    def acquire_lock_strict(self, key: str, ttl: int = 30) -> str | None:
        raise CacheLockInfrastructureError("injected cache outage")


class _RollbackAfterAcquireCache(_Cache):
    def __init__(self, layouts: _Layouts) -> None:
        super().__init__()
        self._layouts = layouts

    def acquire_lock_strict(self, key: str, ttl: int = 30) -> str | None:
        token = super().acquire_lock_strict(key, ttl)
        self._layouts.state = replace(
            self._layouts.state,
            target_layout=SkillLayout.LEGACY,
            phase=SkillLayoutPhase.LEGACY_ROLLBACK_PREPARING,
        )
        return token


def test_rollback_lease_excludes_new_local_edits() -> None:
    layouts = _Layouts()
    guard = SkillsPoolEditGuard(
        cache=_Cache(),
        layout_repository=layouts,
        bot_repository=_Bots(),
    )
    rollback_lease = guard.acquire_for_rollback(scope=SCOPE)
    assert rollback_lease is not None

    with pytest.raises(SkillsPoolEditBusyError, match="Another skill update"):
        guard.acquire_for_edit(scope=SCOPE)

    assert guard.release(rollback_lease)


def test_lock_identity_includes_entity_for_reused_bot_ids() -> None:
    guard = SkillsPoolEditGuard(
        cache=_Cache(),
        layout_repository=_Layouts(),
        bot_repository=_Bots(),
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
        bot_repository=_Bots(),
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
        bot_repository=_Bots(),
    )

    with pytest.raises(SkillsPoolEditRollbackError, match="rollback"):
        guard.acquire_for_edit(scope=SCOPE)


def test_lock_contention_rechecks_for_a_rollback_before_reporting_busy() -> None:
    layouts = _Layouts()
    cache = _Cache()
    guard = SkillsPoolEditGuard(
        cache=cache,
        layout_repository=layouts,
        bot_repository=_Bots(),
    )
    assert cache.acquire_lock(guard._key(scope=SCOPE)) is not None
    layouts.state = replace(
        layouts.state,
        target_layout=SkillLayout.LEGACY,
        phase=SkillLayoutPhase.LEGACY_ROLLBACK_PREPARING,
    )

    with pytest.raises(SkillsPoolEditRollbackError, match="rollback"):
        guard.acquire_for_edit(scope=SCOPE)


def test_rollback_starting_after_lock_acquisition_releases_edit_lease() -> None:
    layouts = _Layouts()
    cache = _RollbackAfterAcquireCache(layouts)
    guard = SkillsPoolEditGuard(
        cache=cache,
        layout_repository=layouts,
        bot_repository=_Bots(),
    )

    with pytest.raises(SkillsPoolEditRollbackError, match="rollback"):
        guard.acquire_for_edit(scope=SCOPE)

    assert cache.held == {}


def test_teclaw_bypasses_an_abandoned_pool_edit_lock() -> None:
    cache = _Cache()
    guard = SkillsPoolEditGuard(
        cache=cache,
        layout_repository=_Layouts(),
        bot_repository=_Bots(engine="teclaw"),
    )
    # Simulates a previous worker acquiring the legacy generic lock and then
    # exiting before its finally block.  Teclaw never owns Pool rollback, so a
    # current Teclaw write must not wait for that lock's TTL.
    abandoned = cache.acquire_lock(guard._key(scope=SCOPE))
    assert abandoned is not None

    lease = guard.acquire_for_edit(scope=SCOPE)

    assert lease.token is None
    assert guard.release(lease) is True
    assert cache.held


def test_cache_outage_is_not_reported_as_lock_contention() -> None:
    guard = SkillsPoolEditGuard(
        cache=_UnavailableCache(),
        layout_repository=_Layouts(),
        bot_repository=_Bots(),
    )

    with pytest.raises(SkillsPoolEditLockUnavailableError, match="lock service"):
        guard.acquire_for_edit(scope=SCOPE)


@pytest.mark.asyncio
async def test_wait_does_not_retry_a_cache_outage_as_ordinary_contention() -> None:
    guard = SkillsPoolEditGuard(
        cache=_UnavailableCache(),
        layout_repository=_Layouts(),
        bot_repository=_Bots(),
    )

    with pytest.raises(SkillsPoolEditLockUnavailableError, match="lock service"):
        await guard.acquire_for_edit_wait(scope=SCOPE, timeout_seconds=0.1)
