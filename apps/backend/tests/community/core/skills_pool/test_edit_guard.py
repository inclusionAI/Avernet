from __future__ import annotations

import asyncio
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock
import logging

import pytest

from agentclaw.community.core.skills_pool.edit_guard import (
    SkillsPoolEditBusyError,
    SkillsPoolEditGuard,
    SkillsPoolEditLockUnavailableError,
    SkillsPoolEditRollbackError,
)
from agentclaw.community.core.skills_pool.participation import SkillLayoutParticipation
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


class _Participation:
    def __init__(self, *, participates: bool = True, label: str = "pool") -> None:
        self._value = SkillLayoutParticipation(
            participates_in_pool_layout=participates,
            label=label,
        )

    def resolve(self, *, scope: BotSkillLayoutScope) -> SkillLayoutParticipation:
        assert scope == SCOPE
        return self._value


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


def _guard(**kwargs) -> SkillsPoolEditGuard:
    bindings = kwargs.pop("binding_repository", None) or Mock()
    skills = kwargs.pop("skill_repository", None)
    if skills is None:
        skills = Mock()
        skills.has_pending_local_skill_dispatch.return_value = False
    return SkillsPoolEditGuard(
        **kwargs, binding_repository=bindings, skill_repository=skills,
    )


def test_rollback_lease_excludes_new_local_edits() -> None:
    layouts = _Layouts()
    guard = _guard(
        cache=_Cache(),
        layout_repository=layouts,
        participation_resolver=_Participation(),
    )
    rollback_lease = guard.acquire_for_rollback(scope=SCOPE)
    assert rollback_lease is not None

    with pytest.raises(SkillsPoolEditBusyError, match="Another skill update"):
        guard.acquire_for_edit(scope=SCOPE)

    assert guard.release(rollback_lease)


def test_non_pool_edit_excludes_mutations_from_another_worker() -> None:
    cache = _Cache()
    layouts = _Layouts()
    participation = _Participation(participates=False, label="legacy_layout_state")
    first_worker = _guard(
        cache=cache, layout_repository=layouts, participation_resolver=participation
    )
    second_worker = _guard(
        cache=cache, layout_repository=layouts, participation_resolver=participation
    )

    lease = first_worker.acquire_for_edit(scope=SCOPE)
    try:
        with pytest.raises(SkillsPoolEditBusyError):
            second_worker.acquire_for_edit(scope=SCOPE)
        assert second_worker.acquire_for_rollback(scope=SCOPE) is None
    finally:
        assert first_worker.release(lease)


def test_layout_edit_waits_for_unconfirmed_upload_on_current_device() -> None:
    package_dispatches = Mock()
    package_dispatches.has_pending_local_skill_dispatch.side_effect = (
        lambda *, device_id=None, **_scope: device_id in (None, "device-a")
    )
    bindings = Mock()
    bindings.get_active_by_bot_and_owner.return_value = SimpleNamespace(id=5, device_id="device-a")
    guard = _guard(
        cache=_Cache(), layout_repository=_Layouts(), participation_resolver=_Participation(),
        binding_repository=bindings, skill_repository=package_dispatches,
    )

    with pytest.raises(SkillsPoolEditBusyError):
        guard.acquire_for_migration(scope=SCOPE)
    assert guard.acquire_for_rollback(scope=SCOPE) is None

    bindings.get_active_by_bot_and_owner.return_value = None
    with pytest.raises(SkillsPoolEditBusyError):
        guard.acquire_for_migration(scope=SCOPE)
    assert guard.acquire_for_rollback(scope=SCOPE) is None

    bindings.get_active_by_bot_and_owner.return_value = SimpleNamespace(id=6, device_id="device-b")
    assert guard.release(guard.acquire_for_migration(scope=SCOPE))
    rollback = guard.acquire_for_rollback(scope=SCOPE)
    assert rollback is not None
    assert guard.release(rollback)


def test_lock_identity_includes_entity_for_reused_bot_ids() -> None:
    guard = _guard(
        cache=_Cache(),
        layout_repository=_Layouts(),
        participation_resolver=_Participation(),
    )
    other_scope = replace(SCOPE, entity_id="entity-2")

    first = guard.acquire_for_rollback(scope=SCOPE)
    second = guard.acquire_for_rollback(scope=other_scope)

    assert first is not None
    assert second is not None
    assert first.key != second.key


@pytest.mark.asyncio
async def test_waiting_edit_acquires_lock_after_ordinary_edit_releases() -> None:
    guard = _guard(
        cache=_Cache(),
        layout_repository=_Layouts(),
        participation_resolver=_Participation(),
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
    guard = _guard(
        cache=_Cache(),
        layout_repository=layouts,
        participation_resolver=_Participation(),
    )

    with pytest.raises(SkillsPoolEditRollbackError, match="rollback"):
        guard.acquire_for_edit(scope=SCOPE)


def test_lock_contention_rechecks_for_a_rollback_before_reporting_busy() -> None:
    layouts = _Layouts()
    cache = _Cache()
    guard = _guard(
        cache=cache,
        layout_repository=layouts,
        participation_resolver=_Participation(),
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
    guard = _guard(
        cache=cache,
        layout_repository=layouts,
        participation_resolver=_Participation(),
    )

    with pytest.raises(SkillsPoolEditRollbackError, match="rollback"):
        guard.acquire_for_edit(scope=SCOPE)

    assert cache.held == {}


@pytest.mark.asyncio
async def test_legacy_bot_still_serializes_ordinary_edits() -> None:
    layouts = _Layouts()
    layouts.state = replace(
        layouts.state,
        active_layout=SkillLayout.LEGACY,
        target_layout=None,
        phase=SkillLayoutPhase.LEGACY_ACTIVE,
        persisted=False,
    )
    guard = _guard(
        cache=_Cache(),
        layout_repository=layouts,
        participation_resolver=_Participation(
            participates=False, label="legacy_layout_state"
        ),
    )

    first = guard.acquire_for_edit(scope=SCOPE)
    waiting = asyncio.create_task(
        guard.acquire_for_edit_wait(scope=SCOPE, timeout_seconds=0.2)
    )
    await asyncio.sleep(0.02)
    assert not waiting.done()

    assert guard.release(first)
    second = await waiting
    assert second.token is not None
    assert guard.release(second)


def test_cache_outage_is_not_reported_as_lock_contention() -> None:
    guard = _guard(
        cache=_UnavailableCache(),
        layout_repository=_Layouts(),
        participation_resolver=_Participation(),
    )

    with pytest.raises(SkillsPoolEditLockUnavailableError, match="lock service"):
        guard.acquire_for_edit(scope=SCOPE)


def test_lock_lifecycle_logs_key_ttl_token_fingerprint_and_duration(caplog) -> None:
    caplog.set_level(logging.INFO)
    guard = _guard(
        cache=_Cache(),
        layout_repository=_Layouts(),
        participation_resolver=_Participation(),
    )

    lease = guard.acquire_for_edit(scope=SCOPE)
    assert guard.release(lease)

    messages = [record.getMessage() for record in caplog.records]
    assert any(
        "lock_acquire" in message
        and lease.key in message
        and "ttl_seconds=600" in message
        and "token_fingerprint=" in message
        and "duration_ms=" in message
        and "outcome=acquired" in message
        for message in messages
    )
    assert any("lock_release" in message and "outcome=released" in message for message in messages)


@pytest.mark.asyncio
async def test_wait_does_not_retry_a_cache_outage_as_ordinary_contention() -> None:
    guard = _guard(
        cache=_UnavailableCache(),
        layout_repository=_Layouts(),
        participation_resolver=_Participation(),
    )

    with pytest.raises(SkillsPoolEditLockUnavailableError, match="lock service"):
        await guard.acquire_for_edit_wait(scope=SCOPE, timeout_seconds=0.1)
