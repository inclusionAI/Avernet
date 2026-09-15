"""Core fault-injection coverage for runtime-owned Local Skill deletion."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from agentclaw.community.core.skill_center.errors import (
    ActiveSkillSetReferenceError,
    LocalSkillEditBusyError,
    LocalSkillEditLockUnavailableError,
    LocalSkillLayoutRollbackError,
    LocalSkillNotReadyError,
    LocalSkillStorageError,
    SkillAssetInUseError,
)
from agentclaw.community.core.skill_center.factories import LocalSkillPackageStorage
from agentclaw.community.core.skill_center.services.local_skill_delete_service import (
    LocalSkillDeleteService,
)
from agentclaw.community.core.skills_pool.edit_guard import (
    SkillsPoolEditBusyError,
    SkillsPoolEditLockUnavailableError,
    SkillsPoolEditRollbackError,
)


class _Files:
    def __init__(self) -> None:
        self.files = {"/skills/one/SKILL.md": b"name: one\ndescription: One\n"}
        self.fail_delete: set[str] = set()
        self.delete_calls: list[str] = []

    async def delete_tree(self, path):
        self.delete_calls.append(path)
        if path in self.fail_delete:
            return False
        self.files = {
            file_path: content
            for file_path, content in self.files.items()
            if not file_path.startswith(f"{path}/")
        }
        return True


class _Skills:
    def __init__(
        self,
        *,
        active=False,
        fail_delete=False,
        active_during_delete=False,
        delete_error=None,
    ) -> None:
        self.active = active
        self.fail_delete = fail_delete
        self.active_during_delete = active_during_delete
        self.delete_error = delete_error
        self.deleted = False

    def get_by_id(self, skill_id):
        if skill_id != "9" or self.deleted:
            return None
        return {
            "id": "9",
            "user_id": "owner",
            "bolt_id": "bot",
            "git_path": "local:///skills/one",
        }

    def get_bot_local_skill(self, **_kwargs):
        if self.deleted:
            return None
        return {**self.get_by_id("9"), "name": "one", "active": self.active}

    def delete_bot_local_skill(self, **_kwargs):
        if self.delete_error is not None:
            raise self.delete_error
        if self.fail_delete:
            raise RuntimeError("database write failed")
        if self.active_during_delete:
            raise ActiveSkillSetReferenceError()
        self.deleted = True
        return True

    def require_unreferenced_for_delete(self, _skill_id):
        if self.active:
            raise SkillAssetInUseError({"installation": 1})

    def list_skill_set_references(self, _skill_id):
        return []


class _Sets:
    def __init__(self, skills):
        self.skills = skills

    def get_default(self, **_kwargs):
        return {"id": "4"}

    def get_excluded_skills(self, *_args):
        return [] if self.skills.active else [9]

    def get_all_excluded_skills(self, *_args):
        return [] if self.skills.active else [9]

    def get_all_active_skill_sets_for_env(self, **_kwargs):
        # The repository includes the Bot-scoped default set for runtime sync;
        # its association must not override the explicit default exclusion.
        return [{"id": "4", "is_default": True}]


class _Bots:
    def __init__(self, status="ACTIVE"):
        self.status = status

    def get_by_id_and_owner(self, *_args):
        return {
            "status": self.status,
            "active_engine": "openclaw",
            "env": "dev",
            "entity_id": "owner",
        }


class _Collaborators:
    def check_collaborator_permission(self, *_args):
        return {"has_permission": True}


class _Factory:
    def __init__(self, files):
        self.files = files
        self.locator_kwargs = None

    def local_skill_package_storage_for_locator(
        self, *, locator, entity_type, is_desktop, is_teclaw, **_kwargs
    ):
        self.locator_kwargs = {
            "entity_type": entity_type,
            "is_desktop": is_desktop,
            "is_teclaw": is_teclaw,
        }
        return LocalSkillPackageStorage(self.files, locator)


class _Guard:
    def __init__(self, on_acquire=None):
        self.on_acquire = on_acquire
        self.events = []

    async def acquire_for_edit_wait(self, *, scope):
        self.events.append((scope.env, scope.entity_id, scope.bot_id))
        if self.on_acquire:
            self.on_acquire()
        return object()

    def release(self, _lease):
        self.events.append("release")


def _service(
    *,
    active=False,
    fail_delete=False,
    active_during_delete=False,
    on_acquire=None,
    status="ACTIVE",
    provider="local",
    guard_error=None,
    delete_error=None,
):
    files = _Files()
    skills = _Skills(
        active=active,
        fail_delete=fail_delete,
        active_during_delete=active_during_delete,
        delete_error=delete_error,
    )
    guard = _Guard(on_acquire)
    if guard_error is not None:

        async def fail_acquire(*, scope):
            raise guard_error

        guard.acquire_for_edit_wait = fail_acquire
    service = LocalSkillDeleteService(
        skills,
        _Sets(skills),
        _Bots(status),
        _Collaborators(),
        _Factory(files),
        guard,
        lambda: SimpleNamespace(
            resolve_for_bot=lambda *_args: SimpleNamespace(provider=provider)
        ),
    )
    return service, files, skills, guard


@pytest.mark.asyncio
async def test_inactive_delete_removes_package_once_then_database_state():
    service, files, skills, guard = _service()

    await service.delete_local_skill(skill_id="9", owner_id="owner", user_id="owner")

    assert skills.deleted is True
    assert files.files == {}
    assert files.delete_calls == ["/skills/one"]
    assert guard.events == [("dev", "owner", "bot"), "release"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("guard_error", "expected_error"),
    [
        (SkillsPoolEditBusyError("busy"), LocalSkillEditBusyError),
        (SkillsPoolEditRollbackError("rollback"), LocalSkillLayoutRollbackError),
        (
            SkillsPoolEditLockUnavailableError("cache"),
            LocalSkillEditLockUnavailableError,
        ),
    ],
)
async def test_delete_maps_guard_failures_to_public_domain_errors(
    guard_error, expected_error
):
    service, _files, _skills, _guard = _service(guard_error=guard_error)

    with pytest.raises(expected_error):
        await service.delete_local_skill(
            skill_id="9", owner_id="owner", user_id="owner"
        )


@pytest.mark.asyncio
async def test_teclaw_delete_uses_device_context_for_the_package_storage():
    service, files, _skills, _guard = _service(provider="teclaw")

    await service.delete_local_skill(skill_id="9", owner_id="owner", user_id="owner")

    factory = service._skill_service_factory
    assert factory.locator_kwargs["is_teclaw"] is True
    assert files.delete_calls == ["/skills/one"]


@pytest.mark.asyncio
async def test_delete_fails_closed_when_device_context_cannot_be_resolved():
    service, files, skills, _guard = _service()

    def _unavailable_resolver():
        raise RuntimeError("device binding unavailable")

    service._device_context_resolver_provider = _unavailable_resolver

    with pytest.raises(LocalSkillStorageError):
        await service.delete_local_skill(
            skill_id="9", owner_id="owner", user_id="owner"
        )

    assert skills.deleted is False
    assert files.files == {"/skills/one/SKILL.md": b"name: one\ndescription: One\n"}
    assert files.delete_calls == []


@pytest.mark.asyncio
async def test_active_delete_is_rejected_before_package_or_database_mutation():
    service, files, skills, _guard = _service(active=True)

    with pytest.raises(SkillAssetInUseError) as raised:
        await service.delete_local_skill(
            skill_id="9", owner_id="owner", user_id="owner"
        )

    assert raised.value.blocker_counts == {"installation": 1}
    assert skills.deleted is False
    assert files.files == {"/skills/one/SKILL.md": b"name: one\ndescription: One\n"}
    assert files.delete_calls == []


@pytest.mark.asyncio
async def test_exclusion_from_a_previous_default_set_allows_delete():
    service, files, skills, _guard = _service(active=False)

    class _PreviousDefaultExcluded(_Sets):
        def get_excluded_skills(self, *_args):
            return []

        def get_all_excluded_skills(self, *_args):
            return [9]

    service._skill_set_repo = _PreviousDefaultExcluded(skills)
    await service.delete_local_skill(skill_id="9", owner_id="owner", user_id="owner")

    assert skills.deleted is True
    assert files.files == {}
    assert files.delete_calls == ["/skills/one"]


@pytest.mark.asyncio
async def test_non_ready_delete_is_rejected_without_package_or_database_mutation():
    service, files, skills, _guard = _service(status="PENDING")

    with pytest.raises(LocalSkillNotReadyError):
        await service.delete_local_skill(
            skill_id="9", owner_id="owner", user_id="owner"
        )

    assert skills.deleted is False
    assert files.files == {"/skills/one/SKILL.md": b"name: one\ndescription: One\n"}
    assert files.delete_calls == []


@pytest.mark.asyncio
async def test_package_delete_failure_leaves_database_state_unchanged():
    service, files, skills, _guard = _service()
    files.fail_delete.add("/skills/one")

    with pytest.raises(LocalSkillStorageError):
        await service.delete_local_skill(
            skill_id="9", owner_id="owner", user_id="owner"
        )

    assert skills.deleted is False
    assert files.files == {"/skills/one/SKILL.md": b"name: one\ndescription: One\n"}
    assert files.delete_calls == ["/skills/one"]


@pytest.mark.asyncio
async def test_database_failure_leaves_the_already_deleted_package_absent():
    service, files, skills, _guard = _service(fail_delete=True)

    with pytest.raises(LocalSkillStorageError):
        await service.delete_local_skill(
            skill_id="9", owner_id="owner", user_id="owner"
        )

    assert skills.deleted is False
    assert files.files == {}
    assert files.delete_calls == ["/skills/one"]


@pytest.mark.asyncio
async def test_database_false_result_reports_storage_error_after_package_delete():
    service, files, skills, _guard = _service()
    skills.delete_bot_local_skill = lambda **_kwargs: False

    with pytest.raises(LocalSkillStorageError):
        await service.delete_local_skill(
            skill_id="9", owner_id="owner", user_id="owner"
        )

    assert skills.deleted is False
    assert files.files == {}
    assert files.delete_calls == ["/skills/one"]


@pytest.mark.asyncio
async def test_transactional_active_recheck_reports_storage_error_after_package_delete():
    service, files, skills, _guard = _service(active_during_delete=True)

    with pytest.raises(LocalSkillStorageError):
        await service.delete_local_skill(
            skill_id="9", owner_id="owner", user_id="owner"
        )

    assert skills.deleted is False
    assert files.files == {}
    assert files.delete_calls == ["/skills/one"]


@pytest.mark.asyncio
async def test_reference_race_reports_storage_error_after_package_delete():
    conflict = SkillAssetInUseError({"membership": 1})
    service, files, skills, _guard = _service(delete_error=conflict)

    with pytest.raises(LocalSkillStorageError) as raised:
        await service.delete_local_skill(
            skill_id="9", owner_id="owner", user_id="owner"
        )

    assert raised.value.__cause__ is conflict
    assert skills.deleted is False
    assert files.files == {}
    assert files.delete_calls == ["/skills/one"]


@pytest.mark.asyncio
async def test_lock_rereads_active_state_before_any_package_mutation():
    skills = _Skills(active=False)
    service, files, _ignored, _guard = _service(
        on_acquire=lambda: setattr(skills, "active", True)
    )
    service._skill_repo = skills
    service._skill_set_repo = _Sets(skills)

    with pytest.raises(SkillAssetInUseError):
        await service.delete_local_skill(
            skill_id="9", owner_id="owner", user_id="owner"
        )

    assert files.files == {"/skills/one/SKILL.md": b"name: one\ndescription: One\n"}
