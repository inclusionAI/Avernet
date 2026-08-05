"""Core fault-injection coverage for recoverable public Local Skill deletion."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from agentclaw.community.core.skill_center.factories import LocalSkillPackageStorage
from agentclaw.community.core.skill_center.errors import (
    LocalSkillActiveError,
    LocalSkillNotReadyError,
    LocalSkillStorageError,
)
from agentclaw.community.core.skill_center.services.local_skill_delete_service import (
    LocalSkillDeleteService,
)
from agentclaw.community.core.skill_center.services.repositories import (
    ActiveSkillSetReferenceError,
)


class _Files:
    def __init__(self) -> None:
        self.files = {"/skills/one/SKILL.md": b"name: one\ndescription: One\n"}
        self.fail_delete: set[str] = set()
        self.partial_fail_delete: set[str] = set()
        self.fail_write_prefixes: set[str] = set()

    async def exists(self, path):
        return any(file_path.startswith(f"{path}/") for file_path in self.files)

    async def list_dir(self, path, *, recursive=False):
        items = [
            {"relative_path": file_path.removeprefix(f"{path}/"), "is_dir": False}
            for file_path in self.files
            if file_path.startswith(f"{path}/")
        ]
        return items or None

    async def read_file(self, path):
        return self.files.get(path)

    async def write_file(self, path, content):
        if any(path.startswith(prefix) for prefix in self.fail_write_prefixes):
            raise OSError("injected write failure")
        self.files[path] = content

    async def delete_tree(self, path):
        if path in self.partial_fail_delete:
            source_files = sorted(
                file_path
                for file_path in self.files
                if file_path.startswith(f"{path}/")
            )
            if source_files:
                del self.files[source_files[0]]
            return False
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
        self, *, active=False, fail_delete=False, active_during_delete=False
    ) -> None:
        self.active = active
        self.fail_delete = fail_delete
        self.active_during_delete = active_during_delete
        self.deleted = False
        self.pending_work = None

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
        if self.fail_delete:
            raise RuntimeError("database write failed")
        if self.active_during_delete:
            raise ActiveSkillSetReferenceError()
        self.pending_work = _kwargs
        self.deleted = True
        return 1

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
        self.storage_kwargs = None

    def local_skill_package_storage_for_locator(
        self, *, locator, entity_type, is_desktop, is_teclaw, **_kwargs
    ):
        self.locator_kwargs = {
            "entity_type": entity_type,
            "is_desktop": is_desktop,
            "is_teclaw": is_teclaw,
        }
        return LocalSkillPackageStorage(self.files, locator)

    def local_skill_package_storage(self, *, directory_name, **_kwargs):
        self.storage_kwargs = _kwargs
        locator = f"/skills/{directory_name}"
        return locator, LocalSkillPackageStorage(self.files, locator)


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


class _Cleanup:
    def __init__(self):
        self.work = []
        self.preparing = []
        self.repair_required = []
        self._records = {}
        self._next_work_id = 1

    def record_preparing(self, **kwargs):
        self.preparing.append(kwargs)
        work_id = self._next_work_id
        self._next_work_id += 1
        self._records[work_id] = {**kwargs, "id": work_id, "status": "preparing"}
        return work_id

    def record_pending(self, **kwargs):
        self.work.append(kwargs)
        for record in self._records.values():
            if record["package_locator"] == kwargs["package_locator"]:
                record.update(kwargs, status="pending")
                return record["id"]
        work_id = self._next_work_id
        self._next_work_id += 1
        self._records[work_id] = {**kwargs, "id": work_id, "status": "pending"}
        return work_id

    def record_repair_required(self, **kwargs):
        self.repair_required.append(kwargs)
        for record in self._records.values():
            if record["package_locator"] == kwargs["package_locator"]:
                record.update(kwargs, status="repair_required")
                return record["id"]
        work_id = self._next_work_id
        self._next_work_id += 1
        self._records[work_id] = {
            **kwargs,
            "id": work_id,
            "status": "repair_required",
        }
        return work_id

    def list_repair_required(self, **kwargs):
        return [
            record.copy()
            for record in self._records.values()
            if record["status"] == "repair_required"
            and all(record[key] == value for key, value in kwargs.items())
        ]

    def mark_cleaned(self, **_kwargs):
        return True

    def cancel_pending(self, *, work_id, **_kwargs):
        record = self._records.get(work_id)
        if record is None:
            return False
        record["status"] = "cancelled"
        return True


def _service(
    *, active=False, fail_delete=False, active_during_delete=False,
    on_acquire=None, status="ACTIVE", provider="local",
):
    files = _Files()
    skills = _Skills(
        active=active, fail_delete=fail_delete,
        active_during_delete=active_during_delete,
    )
    guard = _Guard(on_acquire)
    cleanup = _Cleanup()
    service = LocalSkillDeleteService(
        skills,
        _Sets(skills),
        _Bots(status),
        _Collaborators(),
        _Factory(files),
        guard,
        cleanup,
        lambda: SimpleNamespace(
            resolve_for_bot=lambda *_args: SimpleNamespace(provider=provider)
        ),
    )
    return service, files, skills, guard, cleanup


@pytest.mark.asyncio
async def test_inactive_delete_quarantines_then_removes_database_state_and_package():
    service, files, skills, guard, cleanup = _service()

    await service.delete_local_skill(skill_id="9", actor_id="owner")

    assert skills.deleted is True
    assert files.files == {}
    assert cleanup.work == []
    assert guard.events == [("dev", "owner", "bot"), "release"]


@pytest.mark.asyncio
async def test_teclaw_delete_uses_device_context_for_both_package_storages():
    service, _files, _skills, _guard, _cleanup = _service(provider="teclaw")

    await service.delete_local_skill(skill_id="9", actor_id="owner")

    factory = service._skill_service_factory
    assert factory.locator_kwargs["is_teclaw"] is True
    assert factory.storage_kwargs["is_teclaw"] is True


@pytest.mark.asyncio
async def test_delete_fails_closed_when_device_context_cannot_be_resolved():
    service, files, skills, _guard, _cleanup = _service()

    def _unavailable_resolver():
        raise RuntimeError("device binding unavailable")

    service._device_context_resolver_provider = _unavailable_resolver

    with pytest.raises(LocalSkillStorageError):
        await service.delete_local_skill(skill_id="9", actor_id="owner")

    assert skills.deleted is False
    assert files.files == {"/skills/one/SKILL.md": b"name: one\ndescription: One\n"}


@pytest.mark.asyncio
async def test_active_delete_is_rejected_before_quarantine_or_database_mutation():
    service, files, skills, _guard, cleanup = _service(active=True)

    with pytest.raises(LocalSkillActiveError):
        await service.delete_local_skill(skill_id="9", actor_id="owner")

    assert skills.deleted is False
    assert files.files == {"/skills/one/SKILL.md": b"name: one\ndescription: One\n"}
    assert cleanup.work == []


@pytest.mark.asyncio
async def test_current_default_set_exclusion_is_the_active_authority_under_lock():
    service, files, skills, _guard, cleanup = _service(active=False)

    class _CurrentDefaultIsActive:
        def get_default(self, **_kwargs):
            return {"id": "4"}

        def get_all_excluded_skills(self, *_args):
            return []

    service._skill_set_repo = _CurrentDefaultIsActive()
    with pytest.raises(LocalSkillActiveError):
        await service.delete_local_skill(skill_id="9", actor_id="owner")

    assert skills.deleted is False
    assert files.files == {"/skills/one/SKILL.md": b"name: one\ndescription: One\n"}
    assert cleanup.work == []


@pytest.mark.asyncio
async def test_exclusion_from_a_previous_default_set_allows_delete():
    service, files, skills, _guard, cleanup = _service(active=False)

    class _PreviousDefaultExcluded(_Sets):
        def get_excluded_skills(self, *_args):
            return []

        def get_all_excluded_skills(self, *_args):
            return [9]

    service._skill_set_repo = _PreviousDefaultExcluded(skills)
    await service.delete_local_skill(skill_id="9", actor_id="owner")

    assert skills.deleted is True
    assert files.files == {}
    assert cleanup.work == []


@pytest.mark.asyncio
async def test_non_ready_delete_is_rejected_without_package_or_database_mutation():
    service, files, skills, _guard, cleanup = _service(status="PENDING")

    with pytest.raises(LocalSkillNotReadyError):
        await service.delete_local_skill(skill_id="9", actor_id="owner")

    assert skills.deleted is False
    assert files.files == {"/skills/one/SKILL.md": b"name: one\ndescription: One\n"}
    assert cleanup.work == []


@pytest.mark.asyncio
async def test_database_failure_restores_verified_package_before_fixed_storage_error():
    service, files, skills, _guard, cleanup = _service(fail_delete=True)

    with pytest.raises(LocalSkillStorageError):
        await service.delete_local_skill(skill_id="9", actor_id="owner")

    assert skills.deleted is False
    assert files.files == {"/skills/one/SKILL.md": b"name: one\ndescription: One\n"}


@pytest.mark.asyncio
async def test_transactional_active_recheck_restores_package_and_returns_active_error():
    service, files, skills, _guard, _cleanup = _service(active_during_delete=True)

    with pytest.raises(LocalSkillActiveError):
        await service.delete_local_skill(skill_id="9", actor_id="owner")

    assert skills.deleted is False
    assert files.files == {"/skills/one/SKILL.md": b"name: one\ndescription: One\n"}


@pytest.mark.asyncio
async def test_quarantine_identity_is_durable_before_source_bytes_are_removed(monkeypatch):
    service, files, _skills, _guard, cleanup = _service()
    original_quarantine = LocalSkillPackageStorage.quarantine_to

    class _ProcessCrash(BaseException):
        pass

    async def crash_after_source_removal(package, quarantine):
        await original_quarantine(package, quarantine)
        raise _ProcessCrash()

    monkeypatch.setattr(
        LocalSkillPackageStorage, "quarantine_to", crash_after_source_removal
    )
    with pytest.raises(_ProcessCrash):
        await service.delete_local_skill(skill_id="9", actor_id="owner")
    assert cleanup.preparing[0]["skill_id"] == "9"
    assert cleanup.repair_required[0]["skill_id"] == "9"
    assert "/skills/one/SKILL.md" not in files.files
    assert any(".one.delete-" in path for path in files.files)


@pytest.mark.asyncio
async def test_next_delete_recovers_a_crash_retained_quarantine_before_retrying(monkeypatch):
    service, files, skills, _guard, cleanup = _service()
    original_quarantine = LocalSkillPackageStorage.quarantine_to

    class _ProcessCrash(BaseException):
        pass

    async def crash_after_source_removal(package, quarantine):
        await original_quarantine(package, quarantine)
        raise _ProcessCrash()

    monkeypatch.setattr(
        LocalSkillPackageStorage, "quarantine_to", crash_after_source_removal
    )
    with pytest.raises(_ProcessCrash):
        await service.delete_local_skill(skill_id="9", actor_id="owner")
    retained_work_id = cleanup.list_repair_required(
        env="dev", owner_id="owner", bot_id="bot", skill_id="9"
    )[0]["id"]

    monkeypatch.setattr(
        LocalSkillPackageStorage, "quarantine_to", original_quarantine
    )
    await service.delete_local_skill(skill_id="9", actor_id="owner")

    assert skills.deleted is True
    assert files.files == {}
    assert cleanup._records[retained_work_id]["status"] == "cancelled"


@pytest.mark.asyncio
async def test_next_delete_cancels_pre_copy_repair_work_when_source_verifies(monkeypatch):
    service, files, skills, _guard, cleanup = _service()
    original_quarantine = LocalSkillPackageStorage.quarantine_to

    class _ProcessCrash(BaseException):
        pass

    async def crash_before_quarantine_copy(_package, _quarantine):
        raise _ProcessCrash()

    monkeypatch.setattr(
        LocalSkillPackageStorage, "quarantine_to", crash_before_quarantine_copy
    )
    with pytest.raises(_ProcessCrash):
        await service.delete_local_skill(skill_id="9", actor_id="owner")
    retained_work_id = cleanup.list_repair_required(
        env="dev", owner_id="owner", bot_id="bot", skill_id="9"
    )[0]["id"]
    assert files.files == {"/skills/one/SKILL.md": b"name: one\ndescription: One\n"}

    monkeypatch.setattr(
        LocalSkillPackageStorage, "quarantine_to", original_quarantine
    )
    await service.delete_local_skill(skill_id="9", actor_id="owner")

    assert skills.deleted is True
    assert files.files == {}
    assert cleanup._records[retained_work_id]["status"] == "cancelled"


@pytest.mark.asyncio
async def test_retry_repairs_an_incomplete_existing_source_before_purging_quarantine():
    service, files, skills, _guard, cleanup = _service()
    files.files["/skills/one/scripts/main.py"] = b"print('verified')\n"
    files.partial_fail_delete.add("/skills/one")
    files.fail_write_prefixes.add("/skills/one/")

    with pytest.raises(LocalSkillStorageError):
        await service.delete_local_skill(skill_id="9", actor_id="owner")

    files.fail_write_prefixes.clear()
    files.partial_fail_delete.clear()
    await service.delete_local_skill(skill_id="9", actor_id="owner")

    assert skills.deleted is True
    assert files.files == {}
    assert cleanup.repair_required


@pytest.mark.asyncio
async def test_database_failure_records_quarantine_cleanup_after_source_restores():
    service, files, skills, _guard, cleanup = _service(fail_delete=True)
    original_delete = files.delete_tree

    async def fail_restored_quarantine_purge(path):
        if ".one.delete-" in path:
            return False
        return await original_delete(path)

    files.delete_tree = fail_restored_quarantine_purge
    with pytest.raises(LocalSkillStorageError):
        await service.delete_local_skill(skill_id="9", actor_id="owner")

    assert skills.deleted is False
    assert files.files["/skills/one/SKILL.md"] == b"name: one\ndescription: One\n"
    assert cleanup.work[0]["package_locator"].startswith("/skills/.one.delete-")


@pytest.mark.asyncio
async def test_restore_failure_is_not_swallowed_after_database_rollback():
    service, files, skills, _guard, cleanup = _service(fail_delete=True)
    files.fail_write_prefixes.add("/skills/one/")

    with pytest.raises(LocalSkillStorageError):
        await service.delete_local_skill(skill_id="9", actor_id="owner")

    assert skills.deleted is False
    assert any(".one.delete-" in path for path in files.files)
    assert cleanup.repair_required[0]["skill_id"] == "9"


@pytest.mark.asyncio
async def test_failed_source_cleanup_restores_authoritative_bytes_and_removes_quarantine():
    service, files, skills, _guard, cleanup = _service()
    files.fail_delete.add("/skills/one")

    with pytest.raises(LocalSkillStorageError):
        await service.delete_local_skill(skill_id="9", actor_id="owner")

    assert skills.deleted is False
    assert files.files == {"/skills/one/SKILL.md": b"name: one\ndescription: One\n"}
    assert cleanup.work == []


@pytest.mark.asyncio
async def test_partial_source_cleanup_failure_repairs_authoritative_bytes_before_quarantine_purge():
    service, files, skills, _guard, cleanup = _service()
    files.files["/skills/one/scripts/main.py"] = b"print('restored')\n"
    files.partial_fail_delete.add("/skills/one")

    with pytest.raises(LocalSkillStorageError):
        await service.delete_local_skill(skill_id="9", actor_id="owner")

    assert skills.deleted is False
    assert files.files == {
        "/skills/one/SKILL.md": b"name: one\ndescription: One\n",
        "/skills/one/scripts/main.py": b"print('restored')\n",
    }
    assert cleanup.work == []


@pytest.mark.asyncio
async def test_unverified_partial_source_repair_retains_complete_quarantine_fail_closed():
    service, files, skills, _guard, cleanup = _service()
    files.files["/skills/one/scripts/main.py"] = b"print('retain')\n"
    files.partial_fail_delete.add("/skills/one")
    files.fail_write_prefixes.add("/skills/one/")

    with pytest.raises(LocalSkillStorageError):
        await service.delete_local_skill(skill_id="9", actor_id="owner")

    assert skills.deleted is False
    assert any(".one.delete-" in path for path in files.files)
    assert cleanup.repair_required[0]["skill_id"] == "9"


@pytest.mark.asyncio
async def test_post_commit_purge_failure_records_durable_quarantine_cleanup():
    service, files, skills, _guard, cleanup = _service()
    original_delete = files.delete_tree

    async def fail_quarantine_purge(path):
        if ".one.delete-" in path:
            return False
        return await original_delete(path)

    files.delete_tree = fail_quarantine_purge
    await service.delete_local_skill(skill_id="9", actor_id="owner")

    assert skills.deleted is True
    assert skills.pending_work["skill_id"] == "9"
    assert ".one.delete-" in skills.pending_work["quarantine_locator"]


@pytest.mark.asyncio
async def test_purge_completion_mark_failure_propagates_without_resurrecting_skill():
    service, _files, skills, _guard, cleanup = _service()
    cleanup.mark_cleaned = lambda **_kwargs: False

    with pytest.raises(LocalSkillStorageError):
        await service.delete_local_skill(skill_id="9", actor_id="owner")

    assert skills.deleted is True
    assert skills.pending_work is not None


@pytest.mark.asyncio
async def test_lock_rereads_active_state_before_any_package_mutation():
    skills = _Skills(active=False)
    service, files, _ignored, _guard, _cleanup = _service(
        on_acquire=lambda: setattr(skills, "active", True)
    )
    service._skill_repo = skills
    service._skill_set_repo = _Sets(skills)

    with pytest.raises(LocalSkillActiveError):
        await service.delete_local_skill(skill_id="9", actor_id="owner")

    assert files.files == {"/skills/one/SKILL.md": b"name: one\ndescription: One\n"}
