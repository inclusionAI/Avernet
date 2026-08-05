"""Core fault-injection seam for public Local Skill activation commands."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from agentclaw.community.core.skill_center.errors import (
    LocalSkillNotFoundError,
    LocalSkillNotReadyError,
    LocalSkillRuntimeSyncError,
    LocalSkillStorageError,
)
from agentclaw.community.core.skill_center.services.local_skill_state_service import (
    LocalSkillStateService,
)


class _Skills:
    def __init__(self, *, active: bool = False, git_path: str = "local://one") -> None:
        self.active = active
        self.git_path = git_path

    def get_by_id(self, skill_id: str):
        if skill_id != "9":
            return None
        return {
            "id": "9",
            "user_id": "owner",
            "bolt_id": "bot",
            "git_path": self.git_path,
        }

    def get_bot_local_skill(self, **kwargs):
        if self.git_path != "local://one":
            return None
        return {
            "id": "9",
            "user_id": "owner",
            "bolt_id": "bot",
            "git_path": self.git_path,
            "name": "one",
            "active": self.active,
        }


class _Sets:
    def __init__(self, skills: _Skills) -> None:
        self.skills = skills
        self.events: list[str] = []

    def get_default(self, **kwargs):
        return {"id": "4"}

    def remove_default_skill_exclusion(self, *args):
        self.events.append("remove")
        self.skills.active = True
        return True

    def add_default_skill_exclusion(self, *args):
        self.events.append("add")
        self.skills.active = False
        return True


class _Bots:
    def __init__(self, status: str, entity_id: str = "owner") -> None:
        self.status = status
        self.entity_id = entity_id

    def get_by_id_and_owner(self, *_args):
        return {
            "status": self.status,
            "active_engine": "openclaw",
            "env": "pre",
            "entity_id": self.entity_id,
            "entity_type": "staff",
        }


class _Collaborators:
    def check_collaborator_permission(self, *_args):
        return {"has_permission": True}


class _Guard:
    def __init__(self, on_acquire=None) -> None:
        self.events: list[str] = []
        self._on_acquire = on_acquire

    def acquire_for_edit(self, *, scope):
        self.events.append(f"acquire:{scope.env}:{scope.entity_id}:{scope.bot_id}")
        if self._on_acquire is not None:
            self._on_acquire()
            self._on_acquire = None
        return object()

    def release(self, _lease):
        self.events.append("release")
        return True


class _Runtime:
    def __init__(self, success: bool) -> None:
        self.success = success
        self.calls = 0
        self.skill_service = MagicMock()
        self.skill_service.deactivate_skill = AsyncMock(return_value=True)

    def sync_runtime(self):
        self.calls += 1
        return self.success


class _Factory:
    def __init__(self, runtime: _Runtime) -> None:
        self.runtime = runtime
        self.kwargs = None

    def create(self, **kwargs):
        self.kwargs = kwargs
        return self.runtime


def _service(
    *,
    active: bool = False,
    sync_success: bool = True,
    git_path: str = "local://one",
    status: str = "ACTIVE",
    entity_id: str = "owner",
    collaborators=None,
    on_acquire=None,
):
    skills = _Skills(active=active, git_path=git_path)
    sets = _Sets(skills)
    guard = _Guard(on_acquire)
    runtime = _Runtime(sync_success)
    factory = _Factory(runtime)
    service = LocalSkillStateService(
        skills, sets, _Bots(status, entity_id), collaborators or _Collaborators(), factory, guard
    )
    return service, skills, sets, guard, runtime, factory


@pytest.mark.asyncio
async def test_activate_changes_desired_state_then_reconciles_under_bot_layout_lock():
    service, _skills, sets, guard, runtime, factory = _service()

    result = await service.set_local_skill_active(
        skill_id="9", actor_id="owner", active=True
    )

    assert result["active"] is True
    assert result["changed"] is True
    assert sets.events == ["remove"]
    assert runtime.calls == 1
    assert guard.events == ["acquire:pre:owner:bot", "release"]
    assert factory.kwargs == {
        "user_id": "owner",
        "entity_id": "owner",
        "bot_id": "bot",
        "engine_type": "openclaw",
        "entity_type": "staff",
    }


@pytest.mark.asyncio
async def test_runtime_sync_uses_the_bot_entity_for_skill_paths():
    service, _skills, _sets, _guard, _runtime, factory = _service(
        entity_id="project-entity"
    )

    await service.set_local_skill_active(skill_id="9", actor_id="owner", active=True)

    assert factory.kwargs["entity_id"] == "project-entity"


@pytest.mark.asyncio
async def test_idempotent_deactivate_still_reconciles_without_mutating_database():
    service, _skills, sets, _guard, runtime, _factory = _service(active=False)

    result = await service.set_local_skill_active(
        skill_id="9", actor_id="owner", active=False
    )

    assert result["active"] is False
    assert result["changed"] is False
    assert sets.events == []
    assert runtime.calls == 1


@pytest.mark.asyncio
async def test_deactivate_removes_stale_runtime_link_before_sync():
    service, _skills, _sets, _guard, runtime, _factory = _service(active=True)

    await service.set_local_skill_active(skill_id="9", actor_id="owner", active=False)

    runtime.skill_service.deactivate_skill.assert_awaited_once_with(
        "one", bolt_id="bot", user_id="owner"
    )
    assert runtime.calls == 1


@pytest.mark.asyncio
async def test_lock_rereads_desired_state_changed_while_waiting_before_calculating_changed():
    service, skills, sets, _guard, runtime, _factory = _service(
        active=False,
        on_acquire=lambda: setattr(skills, "active", True),
    )

    result = await service.set_local_skill_active(
        skill_id="9", actor_id="owner", active=True
    )

    assert result["changed"] is False
    assert skills.active is True
    assert sets.events == []
    assert runtime.calls == 1


@pytest.mark.asyncio
async def test_lock_rereads_prior_state_for_runtime_failure_compensation_after_waiting():
    service, skills, sets, _guard, runtime, _factory = _service(
        active=False,
        sync_success=False,
        on_acquire=lambda: setattr(skills, "active", True),
    )

    with pytest.raises(LocalSkillRuntimeSyncError):
        await service.set_local_skill_active(
            skill_id="9", actor_id="owner", active=True
        )

    assert skills.active is True
    assert sets.events == []
    assert runtime.calls == 1


@pytest.mark.asyncio
async def test_runtime_failure_restores_previous_desired_state_before_fixed_failure():
    service, skills, sets, guard, runtime, _factory = _service(
        active=False, sync_success=False
    )

    with pytest.raises(LocalSkillRuntimeSyncError):
        await service.set_local_skill_active(
            skill_id="9", actor_id="owner", active=True
        )

    assert skills.active is False
    assert sets.events == ["remove", "add"]
    assert runtime.calls == 1
    assert guard.events[-1] == "release"


@pytest.mark.asyncio
async def test_runtime_factory_failure_also_compensates_before_fixed_failure():
    service, skills, sets, _guard, _runtime, factory = _service(
        active=False, sync_success=True
    )

    def fail_create(**_kwargs):
        raise RuntimeError("private runtime resolution")

    factory.create = fail_create
    with pytest.raises(LocalSkillRuntimeSyncError):
        await service.set_local_skill_active(
            skill_id="9", actor_id="owner", active=True
        )

    assert skills.active is False
    assert sets.events == ["remove", "add"]


@pytest.mark.asyncio
async def test_failed_runtime_compensation_never_claims_runtime_sync_error():
    service, skills, sets, _guard, _runtime, _factory = _service(
        active=False, sync_success=False
    )

    def fail_restore(*_args):
        sets.events.append("add")
        raise RuntimeError("private database failure")

    sets.add_default_skill_exclusion = fail_restore
    with pytest.raises(LocalSkillStorageError):
        await service.set_local_skill_active(
            skill_id="9", actor_id="owner", active=True
        )

    assert skills.active is True
    assert sets.events == ["remove", "add"]


@pytest.mark.asyncio
async def test_non_local_skill_is_masked_before_lock_or_runtime():
    for source in ("git://market/one", "center://published-skill"):
        service, _skills, _sets, guard, runtime, _factory = _service(git_path=source)

        with pytest.raises(LocalSkillNotFoundError):
            await service.set_local_skill_active(
                skill_id="9", actor_id="owner", active=True
            )

        assert guard.events == []
        assert runtime.calls == 0


@pytest.mark.asyncio
async def test_non_ready_or_unauthorized_request_never_mutates_or_syncs_runtime():
    service, _skills, _sets, guard, runtime, _factory = _service(status="PENDING")
    with pytest.raises(LocalSkillNotReadyError):
        await service.set_local_skill_active(
            skill_id="9", actor_id="owner", active=True
        )
    assert guard.events == ["acquire:pre:owner:bot", "release"]
    assert runtime.calls == 0

    class _Denied:
        def check_collaborator_permission(self, *_args):
            return {"has_permission": False}

    service, _skills, _sets, guard, runtime, _factory = _service(
        collaborators=_Denied()
    )
    with pytest.raises(LocalSkillNotFoundError):
        await service.set_local_skill_active(
            skill_id="9", actor_id="attacker", active=True
        )
    assert guard.events == ["acquire:pre:owner:bot", "release"]
    assert runtime.calls == 0
