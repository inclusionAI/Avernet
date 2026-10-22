"""Core fault-injection seam for Local Skill create-or-replace uploads."""

from __future__ import annotations

import asyncio
import hashlib
import io
import shutil
import threading
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from agentclaw.community.core.skill_center.errors import (
    LocalSkillEditBusyError,
    LocalSkillEditLockUnavailableError,
    LocalSkillInvalidPackageError,
    LocalSkillLayoutRollbackError,
    LocalSkillNotReadyError,
    LocalSkillRuntimeSyncError,
    LocalSkillStorageError,
)
from agentclaw.community.core.skill_center.factories import LocalSkillPackageStorage
from agentclaw.community.core.skill_center.runtime_projection_contract import (
    RuntimeProjectionResult,
)
from agentclaw.community.core.skill_center.services import (
    local_skill_upload_service as upload_module,
)
from agentclaw.community.core.skill_center.services.local_skill_upload_service import (
    LocalSkillUploadService,
)
from agentclaw.community.core.skill_center.services.local_skill_security_upload_observer import (
    LocalSkillSecurityUploadObserver,
)
from agentclaw.community.core.skill_center.services.desktop_skill_recovery import (
    DesktopSkillRecoveryService,
)
from agentclaw.community.core.skill_center.services.recovering_bot_runtime_projector import (
    RecoveringBotRuntimeProjector,
)
from agentclaw.community.core.skill_center.services.skill_parser import SkillParser
from agentclaw.community.core.skill_center.skill_package import SkillPackageValidator
from agentclaw.community.core.skills_pool.edit_guard import (
    SkillsPoolEditBusyError,
    SkillsPoolEditGuard,
    SkillsPoolEditLockUnavailableError,
    SkillsPoolEditRollbackError,
)
from agentclaw.community.core.skills_pool.participation import SkillLayoutParticipation
from agentclaw.community.core.skills_pool.types import (
    BotSkillLayoutState,
    SkillLayout,
    SkillLayoutPhase,
)
from agentclaw.community.plugin_api.object_storage import (
    ObjectCreateResult,
    ObjectReadResult,
    ObjectReadStatus,
)


def _skill_md(name: str = "upload-skill", description: str = "useful") -> bytes:
    return f"---\nname: {name}\ndescription: {description}\n---\n".encode()


def _zip(entries: dict[str, bytes], *, attrs: dict[str, int] | None = None) -> bytes:
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w") as archive:
        for path, content in entries.items():
            info = zipfile.ZipInfo(path)
            if attrs and path in attrs:
                info.external_attr = attrs[path]
            archive.writestr(info, content)
    return payload.getvalue()


class _Repo:
    def __init__(self) -> None:
        self.created: list[dict] = []

    def get_bot_local_by_name(self, **kwargs):
        return None

    def list_bot_local_by_name(self, **kwargs):
        return []

    def get_bot_local_by_locator(self, **_kwargs):
        return None

    def get_by_id(self, _skill_id):
        return None

    def create(self, row):
        row = {
            **row,
            "id": "9",
            "active": False,
            "gmt_created": None,
            "gmt_modified": None,
        }
        self.created.append(row)
        return {key: value for key, value in row.items() if key != "local_security_incarnation_id"}

    def delete(self, skill_id):
        self.created.clear()
        return True


class _Bot:
    def __init__(
        self,
        status="ACTIVE",
        entity_id="owner",
        *,
        bot_type="personal",
        engine="openclaw",
    ):
        self.status = status
        self.entity_id = entity_id
        self.bot_type = bot_type
        self.engine = engine

    def get_by_id_and_owner(self, *_):
        return {
            "status": self.status,
            "active_engine": self.engine,
            "bot_type": self.bot_type,
            "env": "test",
            "entity_id": self.entity_id,
        }

    def list_matches_by_legacy_locator(self, bot_id: str, locator: str) -> list[dict]:
        bot = self.get_by_id_and_owner(bot_id, locator)
        if bot is None:
            return []
        return [{"id": 1, "bot_id": bot_id, "owner_id": locator,
                 "entity_id": locator, **bot}]


class _Filesystem:
    def __init__(self, fail=False, cleanup_results=None):
        self.files: dict[str, bytes] = {}
        self.deleted: list[str] = []
        self.fail = fail
        self.cleanup_results = iter(cleanup_results or ())

    async def write_file(self, path, content):
        if self.fail:
            raise OSError("private path")
        self.files[path] = content

    async def read_file(self, path):
        return self.files.get(path)

    async def list_dir(self, path, *, recursive=False):
        prefix = f"{path}/"
        entries = [
            {"relative_path": file_path[len(prefix) :], "is_dir": False}
            for file_path in self.files
            if file_path.startswith(prefix)
        ]
        return entries or None

    async def delete_tree(self, path):
        self.deleted.append(path)
        result = next(self.cleanup_results, True)
        if result:
            self.files = {
                file_path: content
                for file_path, content in self.files.items()
                if not file_path.startswith(f"{path}/")
            }
        return result

    async def exists(self, path):
        return any(file_path.startswith(f"{path}/") for file_path in self.files)


class _DiskFilesystem:
    """Minimal Local-device fixture that persists package bytes under ``tmp_path``."""

    def __init__(self, root: Path, *, fail_writes=(), fail_reads=()):
        self._root = root
        self._fail_writes = set(fail_writes)
        self._fail_reads = set(fail_reads)

    def _path(self, device_path: str) -> Path:
        relative = Path(device_path.lstrip("/"))
        target = self._root / relative
        if (
            relative.is_absolute()
            or ".." in relative.parts
            or not target.resolve().is_relative_to(self._root.resolve())
        ):
            raise OSError("invalid Local Skill test path")
        return target

    async def write_file(self, path, content):
        if path in self._fail_writes:
            raise OSError(f"device write failed: {path}")
        target = self._path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)

    async def read_file(self, path):
        if path in self._fail_reads:
            raise OSError(f"device read failed: {path}")
        target = self._path(path)
        return target.read_bytes() if target.is_file() else None

    async def list_dir(self, path, *, recursive=False):
        directory = self._path(path)
        if not directory.is_dir():
            return None
        entries = directory.rglob("*") if recursive else directory.iterdir()
        return [
            {
                "relative_path": entry.relative_to(directory).as_posix(),
                "is_dir": entry.is_dir(),
            }
            for entry in entries
        ]

    async def delete_tree(self, path):
        directory = self._path(path)
        if directory.exists():
            shutil.rmtree(directory)
        return True

    async def exists(self, path):
        return self._path(path).exists()


class _Factory:
    def __init__(self, filesystem):
        self.local_dir = __import__("pathlib").Path("/private/skills-local")
        self._filesystem = filesystem
        self.storage_calls: list[dict] = []

    def create(self, **kwargs):
        return self

    def local_skill_package_storage(
        self,
        *,
        entity_id,
        owner_id,
        bot_id,
        engine_type,
        entity_type,
        is_desktop,
        is_teclaw,
        name,
        directory_name=None,
    ):
        directory = str(self.local_dir / (directory_name or name))
        self.storage_calls.append(
            {
                "entity_id": entity_id,
                "owner_id": owner_id,
                "bot_id": bot_id,
                "engine_type": engine_type,
                "entity_type": entity_type,
                "is_desktop": is_desktop,
                "is_teclaw": is_teclaw,
                "name": name,
            }
        )
        return directory, _Storage(self._filesystem, directory)

    def local_skill_package_location(self, *, name, directory_name=None, **_kwargs):
        return SimpleNamespace(
            directory=str(self.local_dir / (directory_name or name)),
            layout="LEGACY",
        )

    def local_skill_package_storage_for_locator(self, *, locator, **kwargs):
        return _Storage(self._filesystem, locator)

    def _local_skill_path_adapter(self, path):
        return path

    def _device_fs_factory(self, bot_id, owner_id):
        assert (bot_id, owner_id) == ("bot", "owner")
        return self._filesystem


class _Storage:
    def __init__(self, filesystem, directory):
        self.filesystem = filesystem
        self.directory = directory

    async def write(self, files):
        for path, content in files:
            await self.filesystem.write_file(f"{self.directory}/{path}", content)

    async def prepare(self):
        if not await self.filesystem.exists(self.directory):
            return
        if not await self.filesystem.delete_tree(self.directory):
            raise OSError("cleanup failed")

    async def cleanup(self):
        return await self.filesystem.delete_tree(self.directory)

    async def exists(self):
        return await self.filesystem.exists(self.directory)

    async def verify(self):
        entries = await self.filesystem.list_dir(self.directory, recursive=True)
        if not entries:
            raise OSError("missing package")
        return True

    async def copy_to(self, target, *, replace=False):
        prefix = f"{self.directory}/"
        files = [
            (path[len(prefix) :], content)
            for path, content in self.filesystem.files.items()
            if path.startswith(prefix)
        ]
        if not files:
            raise OSError("missing package")
        if await self.filesystem.exists(target.directory):
            if not replace:
                raise OSError("target exists")
            if not await target.cleanup():
                raise OSError("cleanup failed")
        await target.write(files)
        await target.verify()


@pytest.mark.asyncio
async def test_package_storage_prepare_accepts_an_absent_first_upload_directory():
    filesystem = _Filesystem()
    await LocalSkillPackageStorage(
        filesystem, "/private/skills-local/upload-skill"
    ).prepare()
    assert filesystem.deleted == []


@pytest.mark.asyncio
async def test_package_storage_copy_to_preserves_source_and_verifies_target():
    filesystem = _Filesystem()
    filesystem.files["/private/source/SKILL.md"] = b"skill"
    source = LocalSkillPackageStorage(filesystem, "/private/source")
    target = LocalSkillPackageStorage(filesystem, "/private/target")

    await source.copy_to(target)

    assert filesystem.files["/private/source/SKILL.md"] == b"skill"
    assert filesystem.files["/private/target/SKILL.md"] == b"skill"


@pytest.mark.asyncio
async def test_package_storage_copy_to_rejects_an_existing_target_without_replace():
    filesystem = _Filesystem()
    filesystem.files["/private/source/SKILL.md"] = b"new"
    filesystem.files["/private/target/SKILL.md"] = b"old"

    with pytest.raises(OSError, match="copy target already exists"):
        await LocalSkillPackageStorage(filesystem, "/private/source").copy_to(
            LocalSkillPackageStorage(filesystem, "/private/target")
        )

    assert filesystem.files["/private/target/SKILL.md"] == b"old"


@pytest.mark.asyncio
async def test_package_storage_copy_to_requires_existing_target_cleanup():
    filesystem = _Filesystem(cleanup_results=[False])
    filesystem.files["/private/source/SKILL.md"] = b"new"
    filesystem.files["/private/target/SKILL.md"] = b"old"

    with pytest.raises(OSError, match="unable to clear Local Skill copy target"):
        await LocalSkillPackageStorage(filesystem, "/private/source").copy_to(
            LocalSkillPackageStorage(filesystem, "/private/target"), replace=True
        )

    assert filesystem.files["/private/target/SKILL.md"] == b"old"


@pytest.mark.asyncio
async def test_package_storage_copy_to_rejects_failed_target_verification():
    filesystem = _Filesystem()
    filesystem.files["/private/source/SKILL.md"] = b"new"
    target = LocalSkillPackageStorage(filesystem, "/private/target")

    async def fail_restore(_files):
        return False

    target._restore_contents = fail_restore
    with pytest.raises(OSError, match="Local Skill copy verification failed"):
        await LocalSkillPackageStorage(filesystem, "/private/source").copy_to(target)


class _Collaborators:
    # ``**kwargs`` absorbs the seam's newer levers (``explicit``) without
    # this double caring which ladder adjudicated.
    def check_collaborator_permission(self, *args, **kwargs):
        return {"has_permission": True}


class _Audit:
    def __init__(self):
        self.rows = []

    def insert(self, row):
        self.rows.append(row)
        return SimpleNamespace(id=len(self.rows))


class _Guard:
    def __init__(self, error=None):
        self._lock = asyncio.Lock()
        self._error = error

    async def acquire_for_edit_wait(self, **_kwargs):
        if self._error is not None:
            raise self._error
        await self._lock.acquire()
        return object()

    def release(self, _lease):
        self._lock.release()
        return True


class _CapturedSecurityObserver:
    def __init__(self, guard=None, error=None):
        self.guard = guard
        self.error = error
        self.calls = []
        self.incarnation_id = "test-local-incarnation"

    def current_incarnation_id(self, **_kwargs):
        if self.guard is not None:
            assert self.guard._lock.locked()
        return self.calls[-1]["incarnation_id"] if self.calls else self.incarnation_id

    def observe(self, **kwargs):
        if self.guard is not None:
            assert self.guard._lock.locked()
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error


class _ObservedIdentityReader:
    def get_observed_incarnation_id(self, **_kwargs):
        return getattr(self, "_observed_incarnation_id", None)


class _Cleanup:
    def __init__(self):
        self.rows = []
        self.preparing = []
        self.cancelled: list[int] = []

    def record_preparing(self, **kwargs):
        self.preparing.append(kwargs)
        return len(self.preparing)

    def commit_preparing(self, work_id, *, requires_runtime_restore):
        row = self.preparing[work_id - 1]
        self.rows.append({**row, "requires_runtime_restore": requires_runtime_restore})

    def record_pending(self, **kwargs):
        self.rows.append(kwargs)
        return len(self.rows)

    def list_pending(self, **_kwargs):
        return []

    def list_repair_required(self, **_kwargs):
        return []

    def mark_cleaned(self, **_kwargs):
        return True

    def mark_failed(self, **_kwargs):
        return True

    def cancel_pending(self, *, work_id, **_kwargs):
        self.cancelled.append(work_id)
        return True


class _CleanupRecordFailure(_Cleanup):
    def record_preparing(self, **_kwargs):
        return None


class _PendingCleanup(_Cleanup):
    def __init__(self):
        super().__init__()
        self.completed: list[int] = []
        self.failed: list[tuple[int, str]] = []

    def list_pending(self, **_kwargs):
        return [{"id": 12, "package_locator": "/private/skills-local/obsolete"}]

    def mark_cleaned(self, *, work_id, **_kwargs):
        self.completed.append(work_id)
        return True

    def mark_failed(self, *, work_id, error, **_kwargs):
        self.failed.append((work_id, error))
        return True


class _RuntimeRestoreCleanup(_PendingCleanup):
    def list_pending(self, **_kwargs):
        return [
            {
                "id": 12,
                "package_locator": "/private/skills-local/staged",
                "requires_runtime_restore": True,
            }
        ]


class _UnwritableCleanupProgress(_PendingCleanup):
    def mark_cleaned(self, **_kwargs):
        return False

    def mark_failed(self, **_kwargs):
        return False


class _RuntimeFactory:
    def create(self, **kwargs):
        return self

    async def project_skills(self, *, desired_skills=None):
        return True

    async def project(self, **_kwargs):
        return None


class _ReplacementRepo(_Repo):
    def __init__(self, rows):
        super().__init__()
        self.rows = rows
        self.updates = []
        self.atomic_replacements = []

    def list_bot_local_by_name(self, **_kwargs):
        return self.rows

    def get_bot_local_by_locator(self, *, bot_id, user_id, locator):
        return next(
            (
                row
                for row in self.rows
                if row["bolt_id"] == bot_id
                and row["user_id"] == user_id
                and row["git_path"] == f"local://{locator}"
            ),
            None,
        )

    def get_bot_local_skill(self, *, skill_id, **_kwargs):
        return next((row for row in self.rows if str(row["id"]) == str(skill_id)), None)

    def update(self, skill_id, values):
        self.updates.append((skill_id, values))
        row = next(row for row in self.rows if row["id"] == skill_id)
        row.update(values)
        return row

    def replace_bot_local_skill(self, **kwargs):
        self.atomic_replacements.append(kwargs)
        row = next(
            (
                row
                for row in self.rows
                if str(row["id"]) == str(kwargs["skill_id"])
                and row["user_id"] == kwargs["owner_id"]
                and row["bolt_id"] == kwargs["bot_id"]
                and row["git_path"] == f"local://{kwargs['old_locator']}"
            ),
            None,
        )
        if row is None:
            return None
        row.update(
            {
                "description": kwargs["description"],
                "git_path": f"local://{kwargs['new_locator']}",
                "user_id": kwargs["owner_id"],
            }
        )
        return {**row, "_local_security_incarnation_id": "incarnation-test"}


class _ConcurrentRepo(_ReplacementRepo):
    def __init__(self):
        super().__init__([])

    def create(self, row):
        row = {
            **row,
            "id": "9",
            "active": False,
            "gmt_created": None,
            "gmt_modified": None,
        }
        self.rows.append(row)
        return row


class _ReplacementFactory(_Factory):
    def __init__(self, filesystem, *, aliases=None):
        super().__init__(filesystem)
        self.locator_calls: list[dict] = []
        self.aliases = aliases or {}

    def local_skill_package_storage_for_locator(self, *, locator, skill_name, **kwargs):
        self.locator_calls.append({"locator": locator, **kwargs})
        canonical = str(self.local_dir / skill_name)
        if locator != canonical and self.aliases.get(locator) != canonical:
            raise ValueError("Local Skill cleanup locator escapes skills-local")
        return _Storage(self._filesystem, canonical)


class _DiskStorageFactory(_ReplacementFactory):
    """Use the production package-storage port over the temporary Local disk."""

    def local_skill_package_storage(self, **kwargs):
        directory, _ = super().local_skill_package_storage(**kwargs)
        return directory, LocalSkillPackageStorage(self._filesystem, directory)

    def local_skill_package_storage_for_locator(self, *, locator, skill_name, **kwargs):
        self.locator_calls.append({"locator": locator, **kwargs})
        canonical = str(self.local_dir / skill_name)
        if locator != canonical and self.aliases.get(locator) != canonical:
            raise ValueError("Local Skill cleanup locator escapes skills-local")
        return LocalSkillPackageStorage(self._filesystem, canonical)


class _ReplacementRuntime:
    def __init__(self, results):
        self.results = iter(results)
        self.calls = 0
        self.scopes: list = []

    def create(self, **_kwargs):
        return self

    async def project_skills(self, *, desired_skills=None):
        self.calls += 1
        return next(self.results)

    async def project(self, *, scope=None, **_kwargs):
        self.calls += 1
        self.scopes.append(scope)
        if not next(self.results):
            raise RuntimeError("runtime reconcile failed")


class _DeviceResolver:
    def __init__(self, provider="local"):
        self.provider = provider

    def resolve_for_bot(self, _bot_id, _owner_id):
        return SimpleNamespace(provider=self.provider)


class _LegacyPackageRuntime:
    async def apply(self, **_kwargs):
        return None


class _PackageRuntime:
    def __init__(self, result=None, error=None):
        self.result = result or SimpleNamespace(
            action="created",
            content_digest="sha256:" + "a" * 64,
            target_path="/runtime/skills-local/upload-skill",
        )
        self.error = error
        self.calls: list[dict] = []

    async def apply(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.result


def _replacement_service(
    filesystem,
    repo,
    runtime,
    _unused_cleanup=None,
    guard=None,
    factory=None,
    *,
    provider="local",
    package_runtime=None,
    security_observer=None,
    security_observation_enabled=True,
):
    return LocalSkillUploadService(
        repo,
        _Bot(),
        _Collaborators(),
        factory or _ReplacementFactory(filesystem),
        guard or _Guard(),
        lambda: _DeviceResolver(provider),
        runtime,
        SkillPackageValidator(SkillParser()),
        package_runtime or _LegacyPackageRuntime(),
        lambda: security_observer or _CapturedSecurityObserver(),
        security_observation_enabled,
    )


def _service(
    filesystem,
    *,
    status="ACTIVE",
    collaborators=None,
    repo=None,
    audit=None,
    bot=None,
    factory=None,
    provider="local",
    guard=None,
    package_runtime=None,
    security_observer=None,
    security_observation_enabled=True,
):
    return LocalSkillUploadService(
        repo or _Repo(),
        bot or _Bot(status),
        collaborators or _Collaborators(),
        factory or _Factory(filesystem),
        guard or _Guard(),
        lambda: _DeviceResolver(provider),
        _RuntimeFactory(),
        SkillPackageValidator(SkillParser()),
        package_runtime or _LegacyPackageRuntime(),
        lambda: security_observer or _CapturedSecurityObserver(),
        security_observation_enabled,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("engine_package", [False, True])
@pytest.mark.parametrize("replacing", [False, True])
async def test_successful_upload_hands_exact_package_to_security_under_edit_guard(
    engine_package, replacing
):
    guard = _Guard()
    observer = _CapturedSecurityObserver(guard)
    filesystem = _Filesystem()
    if replacing:
        filesystem.files["/private/skills-local/upload-skill/SKILL.md"] = b"old"
        repo = _ReplacementRepo([_existing_skill(active=False)])
        service = _replacement_service(
            filesystem,
            repo,
            _ReplacementRuntime(
                [RuntimeProjectionResult.converged()] if engine_package else [True]
            ),
            guard=guard,
            package_runtime=_PackageRuntime() if engine_package else None,
            security_observer=observer,
        )
    else:
        service = _service(
            filesystem,
            guard=guard,
            package_runtime=_PackageRuntime() if engine_package else None,
            security_observer=observer,
        )
    result = await service.upload_local_skill(
        bot_id="bot",
        owner_id="owner",
        actor_id="collaborator",
        package=_zip({"SKILL.md": _skill_md(description="frozen description")}),
    )

    assert result["operation"] == ("updated" if replacing else "created")
    assert len(observer.calls) == 1
    call = observer.calls[0]
    assert call["skill_id"] == int(result["skill"]["id"])
    assert call["operation"] == result["operation"]
    assert call["actor_id"] == "collaborator"
    assert call["owner_id"] == "owner"
    assert call["name"] == "upload-skill"
    assert call["description"] == "frozen description"
    assert "_security_upload_order" not in result
    assert "_local_security_incarnation_id" not in result
    assert "local_security_incarnation_id" not in result["skill"]
    with zipfile.ZipFile(io.BytesIO(call["canonical_zip"])) as archive:
        assert archive.read("SKILL.md") == _skill_md(description="frozen description")
    assert not guard._lock.locked()


@pytest.mark.asyncio
async def test_formal_security_identity_failure_does_not_undo_successful_upload():
    class _FailedIdentity(_CapturedSecurityObserver):
        def current_incarnation_id(self, **_kwargs):
            raise RuntimeError("security identity store unavailable")

    guard = _Guard()
    observer = _FailedIdentity()
    service = _service(
        _Filesystem(), guard=guard, security_observer=observer,
    )
    result = await service.upload_local_skill(
        bot_id="bot", owner_id="owner", actor_id="collaborator",
        package=_zip({"SKILL.md": _skill_md()}),
    )
    assert result["operation"] == "created"
    assert observer.calls == []
    assert not guard._lock.locked()


@pytest.mark.asyncio
async def test_upload_saves_exact_snapshot_and_schedules_independent_security_work():
    class _Objects:
        def __init__(self):
            self.values = {}

        def create_object_if_absent(self, key, content):
            assert key not in self.values
            self.values[key] = content
            return ObjectCreateResult.CREATED

        def read_object(self, key):
            return ObjectReadResult(ObjectReadStatus.FOUND, self.values[key])

    class _SecurityRepository(_ObservedIdentityReader):
        def __init__(self):
            self.identity_calls = []
            self.observations = []
            self.reserved = None

        def reserve_handoff(self, **kwargs):
            self.reserved = kwargs
            return 1

        def ensure_for_asset(self, **kwargs):
            self.identity_calls.append(kwargs)
            return SimpleNamespace(provider_skill_id="tc:stable")

        def record_observation(self, **kwargs):
            self.observations.append(kwargs)
            return SimpleNamespace(observation_id=kwargs["observation_id"])

    class _Queue:
        def __init__(self):
            self.tasks = []

        def enqueue(self, task_type, payload, deadline_seconds, **kwargs):
            self.tasks.append((task_type, payload, kwargs["idempotency_key"]))

    objects = _Objects()
    repository = _SecurityRepository()
    queue = _Queue()
    observer = LocalSkillSecurityUploadObserver(
        repository=repository,
        objects=objects,
        queue=queue,
        env_provider=lambda: "pre",
    )
    result = await _service(
        _Filesystem(), security_observer=observer
    ).upload_local_skill(
        bot_id="bot",
        owner_id="owner",
        actor_id="collaborator",
        package=_zip({"SKILL.md": _skill_md(description="frozen")}),
    )

    assert result["operation"] == "created"
    assert repository.reserved is not None
    assert (
        repository.reserved["name"],
        repository.reserved["description"],
        repository.reserved["creator_id"],
    ) == ("upload-skill", "frozen", "collaborator")
    assert repository.identity_calls == [
        {
            "env": "pre",
            "bot_id": "bot",
            "owner_id": "owner",
            "skill_id": 9,
            "creator_id": "collaborator",
            "incarnation_id": repository.reserved["incarnation_id"],
        }
    ]
    observation = repository.observations[0]
    assert observation["name"] == "upload-skill"
    assert observation["description"] == "frozen"
    snapshot = objects.values[observation["snapshot_ref"]]
    assert observation["sha256"] == "sha256:" + hashlib.sha256(snapshot).hexdigest()
    with zipfile.ZipFile(io.BytesIO(snapshot)) as archive:
        assert archive.read("SKILL.md") == _skill_md(description="frozen")
    assert {task[0] for task in queue.tasks} == {
        "local_skill_security_scan",
        "local_skill_security_sync",
    }
    assert all(
        task[1]["observation_id"] == observation["observation_id"]
        for task in queue.tasks
    )


@pytest.mark.asyncio
async def test_security_handoff_failure_keeps_successful_skill_and_releases_guard(
    caplog,
):
    guard = _Guard()
    observer = _CapturedSecurityObserver(guard, RuntimeError("security unavailable"))
    result = await _service(
        _Filesystem(), guard=guard, security_observer=observer
    ).upload_local_skill(
        bot_id="bot",
        owner_id="owner",
        actor_id="owner",
        package=_zip({"SKILL.md": _skill_md()}),
    )

    assert result["operation"] == "created"
    assert result["skill"]["id"] == "9"
    assert not guard._lock.locked()
    assert "security observation handoff failed" in caplog.text


@pytest.mark.asyncio
async def test_scan_enqueue_failure_is_recorded_and_does_not_cancel_sync():
    class _Objects:
        def __init__(self):
            self.saved = {}

        def create_object_if_absent(self, key, content):
            self.saved[key] = content
            return ObjectCreateResult.CREATED

        def read_object(self, key):
            return ObjectReadResult(ObjectReadStatus.FOUND, self.saved[key])

    class _Repository(_ObservedIdentityReader):
        def __init__(self):
            self.errors = []

        def reserve_handoff(self, **_kwargs):
            return 1

        def ensure_for_asset(self, **_kwargs):
            return SimpleNamespace(provider_skill_id="tc:stable")

        def record_observation(self, **kwargs):
            return SimpleNamespace(observation_id=kwargs["observation_id"])

        def mark_scan_error(self, **kwargs):
            self.errors.append(kwargs)

    class _Queue:
        def __init__(self):
            self.tasks = []

        def enqueue(self, task_type, payload, deadline_seconds, **kwargs):
            if task_type == "local_skill_security_scan":
                raise RuntimeError("queue unavailable")
            self.tasks.append(task_type)

    repository = _Repository()
    queue = _Queue()
    observer = LocalSkillSecurityUploadObserver(
        repository=repository,
        objects=_Objects(),
        queue=queue,
        env_provider=lambda: "pre",
    )
    result = await _service(
        _Filesystem(), security_observer=observer
    ).upload_local_skill(
        bot_id="bot",
        owner_id="owner",
        actor_id="owner",
        package=_zip({"SKILL.md": _skill_md()}),
    )

    assert result["operation"] == "created"
    assert repository.errors[0]["error_code"] == "TASK_ENQUEUE_FAILED"
    assert queue.tasks == ["local_skill_security_sync"]


@pytest.mark.parametrize("known_creator", ["first-uploader", None])
def test_first_observed_replacement_uses_only_recorded_security_identity(
    known_creator
):
    class _Objects:
        def __init__(self):
            self.values = {}

        def create_object_if_absent(self, key, content):
            self.values[key] = content
            return ObjectCreateResult.CREATED

        def read_object(self, key):
            return ObjectReadResult(ObjectReadStatus.FOUND, self.values[key])

    class _Repository(_ObservedIdentityReader):
        def __init__(self):
            self.creator_id = None

        def reserve_handoff(self, **_kwargs):
            return 1

        def get_identity_for_asset(self, **_kwargs):
            return SimpleNamespace(creator_id=known_creator) if known_creator else None

        def ensure_for_asset(self, **kwargs):
            self.creator_id = kwargs["creator_id"]
            return SimpleNamespace(provider_skill_id="tc:stable")

        def record_observation(self, **kwargs):
            return SimpleNamespace(observation_id=kwargs["observation_id"])

    class _Queue:
        def enqueue(self, task_type, payload, deadline_seconds, **kwargs):
            return None

    repository = _Repository()
    observer = LocalSkillSecurityUploadObserver(
        repository=repository,
        objects=_Objects(),
        queue=_Queue(),
        env_provider=lambda: "pre",
    )
    observer.observe(
        bot_id="bot",
        owner_id="owner",
        actor_id="replacer",
        skill_id=9,
        operation="updated",
        name="upload-skill",
        description="new",
        canonical_zip=_zip({"SKILL.md": _skill_md(description="new")}),
        observation_id="replacement-observation",
        cancelled=threading.Event(),
        incarnation_id="incarnation-test",
    )

    assert repository.creator_id == known_creator


@pytest.mark.asyncio
async def test_cancelled_upload_waits_for_observation_before_replacement():
    class _BlockingObserver:
        def __init__(self):
            self.entered = threading.Event()
            self.release = threading.Event()
            self.calls = []

        def current_incarnation_id(self, **_kwargs):
            return "existing-incarnation"

        def observe(self, **kwargs):
            self.calls.append(kwargs["description"])
            if len(self.calls) == 1:
                self.entered.set()
                assert self.release.wait(timeout=3)

    observer = _BlockingObserver()
    guard = _Guard()
    repo = _ConcurrentRepo()
    service = _replacement_service(
        _Filesystem(),
        repo,
        _ReplacementRuntime([True]),
        guard=guard,
        security_observer=observer,
    )
    first = asyncio.create_task(
        service.upload_local_skill(
            bot_id="bot",
            owner_id="owner",
            actor_id="owner",
            package=_zip({"SKILL.md": _skill_md(description="A")}),
        )
    )
    assert await asyncio.to_thread(observer.entered.wait, 3)
    first.cancel()
    second = asyncio.create_task(
        service.upload_local_skill(
            bot_id="bot",
            owner_id="owner",
            actor_id="owner",
            package=_zip({"SKILL.md": _skill_md(description="B")}),
        )
    )
    await asyncio.sleep(0.02)
    assert observer.calls == ["A"]
    observer.release.set()
    with pytest.raises(asyncio.CancelledError):
        await first
    result = await second

    assert result["operation"] == "updated"
    assert observer.calls == ["A", "B"]
    assert not guard._lock.locked()


@pytest.mark.asyncio
async def test_timed_out_snapshot_cannot_make_older_replacement_current(
    monkeypatch, caplog
):
    monkeypatch.setattr(upload_module, "SECURITY_HANDOFF_TIMEOUT_SECONDS", 0.05)

    class _SlowObjects:
        def __init__(self):
            self.entered = threading.Event()
            self.release = threading.Event()
            self.values = {}

        def create_object_if_absent(self, key, content):
            with zipfile.ZipFile(io.BytesIO(content)) as archive:
                is_a = b"description: A" in archive.read("SKILL.md")
            if is_a:
                self.entered.set()
                assert self.release.wait(timeout=3)
            self.values[key] = content
            return ObjectCreateResult.CREATED

        def read_object(self, key):
            return ObjectReadResult(ObjectReadStatus.FOUND, self.values[key])

    class _SecurityRepository(_ObservedIdentityReader):
        def __init__(self):
            self.current = None
            self.failures = []

        def reserve_handoff(self, **_kwargs):
            return 1

        def get_identity_for_asset(self, **_kwargs):
            return None

        def ensure_for_asset(self, **_kwargs):
            return SimpleNamespace(provider_skill_id="tc:stable")

        def record_observation(self, **kwargs):
            self.current = kwargs["observation_id"]

        def record_handoff_failure(self, **kwargs):
            self.failures.append(kwargs)

    class _Queue:
        def enqueue(self, *_args, **_kwargs):
            return None

    objects = _SlowObjects()
    repository = _SecurityRepository()
    observer = LocalSkillSecurityUploadObserver(
        repository=repository,
        objects=objects,
        queue=_Queue(),
        env_provider=lambda: "pre",
    )
    guard = _Guard()
    service = _replacement_service(
        _Filesystem(),
        _ConcurrentRepo(),
        _ReplacementRuntime([True, True]),
        guard=guard,
        security_observer=observer,
    )
    first = asyncio.create_task(
        service.upload_local_skill(
            bot_id="bot",
            owner_id="owner",
            actor_id="owner",
            package=_zip({"SKILL.md": _skill_md(description="A")}),
        )
    )
    assert await asyncio.to_thread(objects.entered.wait, 3)
    assert (await asyncio.wait_for(first, 1))["operation"] == "created"
    second = await asyncio.wait_for(
        service.upload_local_skill(
            bot_id="bot",
            owner_id="owner",
            actor_id="owner",
            package=_zip({"SKILL.md": _skill_md(description="B")}),
        ),
        1,
    )
    assert second["operation"] == "updated"
    current_after_b = repository.current
    objects.release.set()
    await asyncio.sleep(0.05)

    assert repository.current == current_after_b
    assert len(repository.failures) == 1
    assert repository.failures[0]["stage"] == "HANDOFF_TIMEOUT"
    assert repository.failures[0]["sha256"].startswith("sha256:")
    assert repository.failures[0]["snapshot_verified"] is False
    assert "observation_id=" in caplog.text
    assert not guard._lock.locked()


@pytest.mark.asyncio
async def test_ambiguous_snapshot_write_retains_reconcilable_failure_fact():
    class _BrokenObjects:
        def create_object_if_absent(self, *_args):
            raise RuntimeError("store unavailable")

        def read_object(self, *_args):
            raise AssertionError("must not read")

    class _FailureRepository(_ObservedIdentityReader):
        def __init__(self):
            self.failures = []

        def reserve_handoff(self, **_kwargs):
            return 1

        def record_handoff_failure(self, **kwargs):
            self.failures.append(kwargs)

    repository = _FailureRepository()
    observer = LocalSkillSecurityUploadObserver(
        repository=repository,
        objects=_BrokenObjects(),
        queue=None,
        env_provider=lambda: "pre",
    )
    result = await _service(
        _Filesystem(), security_observer=observer
    ).upload_local_skill(
        bot_id="bot",
        owner_id="owner",
        actor_id="owner",
        package=_zip({"SKILL.md": _skill_md()}),
    )
    assert result["operation"] == "created"
    assert repository.failures[0]["stage"] == "SNAPSHOT_UNCERTAIN"
    assert repository.failures[0]["skill_id"] == 9
    assert repository.failures[0]["snapshot_ref"].endswith(
        repository.failures[0]["observation_id"] + ".zip"
    )


@pytest.mark.asyncio
async def test_identity_failure_keeps_verified_exact_snapshot_for_repair():
    class _Objects:
        def __init__(self):
            self.values = {}

        def create_object_if_absent(self, key, content):
            self.values[key] = content
            return ObjectCreateResult.CREATED

        def read_object(self, key):
            return ObjectReadResult(ObjectReadStatus.FOUND, self.values[key])

    class _Repository(_ObservedIdentityReader):
        def __init__(self):
            self.failures = []

        def reserve_handoff(self, **_kwargs):
            return 1

        def ensure_for_asset(self, **_kwargs):
            raise RuntimeError("identity store unavailable")

        def record_handoff_failure(self, **kwargs):
            self.failures.append(kwargs)

    objects = _Objects()
    repository = _Repository()
    observer = LocalSkillSecurityUploadObserver(
        repository=repository,
        objects=objects,
        queue=None,
        env_provider=lambda: "pre",
    )
    result = await _service(
        _Filesystem(), security_observer=observer
    ).upload_local_skill(
        bot_id="bot",
        owner_id="owner",
        actor_id="owner",
        package=_zip({"SKILL.md": _skill_md()}),
    )
    assert result["operation"] == "created"
    failure = repository.failures[0]
    assert failure["stage"] == "IDENTITY_FAILED"
    assert failure["snapshot_verified"] is True
    assert (
        "sha256:" + hashlib.sha256(objects.values[failure["snapshot_ref"]]).hexdigest()
        == failure["sha256"]
    )


@pytest.mark.asyncio
async def test_reservation_finishing_after_timeout_cannot_observe_older_upload(
    monkeypatch,
):
    monkeypatch.setattr(upload_module, "SECURITY_HANDOFF_TIMEOUT_SECONDS", 0.05)

    class _OrderingRepository(_ObservedIdentityReader):
        def __init__(self):
            self.entered = threading.Event()
            self.release = threading.Event()
            self.order = 0
            self.current = None
            self.failures = []

        def reserve_handoff(self, **kwargs):
            if kwargs["observation_id"] == self.first_id:
                self.entered.set()
                assert self.release.wait(timeout=3)
            self.order += 1
            return self.order

        def get_identity_for_asset(self, **_kwargs):
            return None

        def ensure_for_asset(self, **_kwargs):
            return SimpleNamespace(provider_skill_id="tc:stable")

        def record_observation(self, **kwargs):
            self.current = kwargs["observation_id"]

        def record_handoff_failure(self, **kwargs):
            self.failures.append(kwargs)

    class _Objects:
        def __init__(self):
            self.values = {}

        def create_object_if_absent(self, key, content):
            self.values[key] = content
            return ObjectCreateResult.CREATED

        def read_object(self, key):
            return ObjectReadResult(ObjectReadStatus.FOUND, self.values[key])

    class _Queue:
        def enqueue(self, *_args, **_kwargs):
            return None

    repository = _OrderingRepository()
    observer = LocalSkillSecurityUploadObserver(
        repository=repository,
        objects=_Objects(),
        queue=_Queue(),
        env_provider=lambda: "pre",
    )
    original_reserve = repository.reserve_handoff

    def _reserve_first(**kwargs):
        if not hasattr(repository, "first_id"):
            repository.first_id = kwargs["observation_id"]
        return original_reserve(**kwargs)

    repository.reserve_handoff = _reserve_first
    service = _replacement_service(
        _Filesystem(),
        _ConcurrentRepo(),
        _ReplacementRuntime([True, True]),
        security_observer=observer,
    )
    first = asyncio.create_task(
        service.upload_local_skill(
            bot_id="bot",
            owner_id="owner",
            actor_id="owner",
            package=_zip({"SKILL.md": _skill_md(description="A")}),
        )
    )
    assert await asyncio.to_thread(repository.entered.wait, 3)
    assert (await asyncio.wait_for(first, 1))["operation"] == "created"
    assert (
        await asyncio.wait_for(
            service.upload_local_skill(
                bot_id="bot",
                owner_id="owner",
                actor_id="owner",
                package=_zip({"SKILL.md": _skill_md(description="B")}),
            ),
            1,
        )
    )["operation"] == "updated"
    current_after_b = repository.current
    repository.release.set()
    await asyncio.sleep(0.05)
    assert repository.current == current_after_b
    assert len(repository.failures) == 1
    assert repository.failures[0]["stage"] == "HANDOFF_TIMEOUT"


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
async def test_upload_maps_guard_failures_to_public_domain_errors(
    guard_error, expected_error
):
    service = _service(
        _Filesystem(),
        guard=_Guard(guard_error),
    )

    with pytest.raises(expected_error):
        await service.upload_local_skill(
            bot_id="bot",
            owner_id="owner",
            actor_id="owner",
            package=_zip({"SKILL.md": _skill_md()}),
        )


@pytest.mark.asyncio
async def test_upload_uses_existing_bot_runtime_without_product_matrix():
    service = _service(
        _Filesystem(),
        bot=_Bot(bot_type="desktop", engine="claude_code"),
    )

    result = await service.upload_local_skill(
        bot_id="bot",
        owner_id="owner",
        actor_id="owner",
        package=_zip({"SKILL.md": _skill_md()}),
    )

    assert result["skill"]["active"] is False


@pytest.mark.asyncio
async def test_new_package_api_creates_metadata_without_per_file_writes():
    filesystem = _Filesystem()
    package_runtime = _PackageRuntime()
    service = _service(filesystem, package_runtime=package_runtime)

    result = await service.upload_local_skill(
        bot_id="bot",
        owner_id="owner",
        actor_id="owner",
        package=_zip({"SKILL.md": _skill_md()}),
    )

    assert result["operation"] == "created"
    assert result["skill"]["git_path"] == "local:///private/skills-local/upload-skill"
    assert filesystem.files == {}
    assert package_runtime.calls[0]["layout"] == "LEGACY"
    assert package_runtime.calls[0]["package"].startswith(b"PK")


@pytest.mark.asyncio
async def test_new_package_api_db_failure_does_not_issue_reverse_file_operation():
    class _FailingRepo(_Repo):
        def create(self, row):
            raise RuntimeError("db unavailable")

    package_runtime = _PackageRuntime()
    observer = _CapturedSecurityObserver()
    service = _service(
        _Filesystem(),
        repo=_FailingRepo(),
        package_runtime=package_runtime,
        security_observer=observer,
    )

    with pytest.raises(LocalSkillStorageError):
        await service.upload_local_skill(
            bot_id="bot",
            owner_id="owner",
            actor_id="owner",
            package=_zip({"SKILL.md": _skill_md()}),
        )

    assert len(package_runtime.calls) == 1
    assert observer.calls == []


@pytest.mark.asyncio
async def test_new_package_api_replace_preserves_existing_locator():
    locator = "/historical/skills-local/upload-skill"
    row = {
        "id": "10",
        "name": "upload-skill",
        "description": "old",
        "git_path": f"local://{locator}",
        "user_id": "owner",
        "bolt_id": "bot",
        "active": False,
    }
    repo = _ReplacementRepo([row])
    package_runtime = _PackageRuntime(
        result=SimpleNamespace(
            action="created",
            content_digest="sha256:" + "b" * 64,
            target_path="/runtime/skills-local/upload-skill",
        )
    )
    service = _replacement_service(
        _Filesystem(),
        repo,
        _ReplacementRuntime([RuntimeProjectionResult.converged()]),
        package_runtime=package_runtime,
    )

    result = await service.upload_local_skill(
        bot_id="bot",
        owner_id="owner",
        actor_id="owner",
        package=_zip({"SKILL.md": _skill_md(description="new")}),
    )

    assert result["operation"] == "updated"
    assert result["skill"]["git_path"] == f"local://{locator}"
    assert repo.atomic_replacements[0]["old_locator"] == locator
    assert repo.atomic_replacements[0]["new_locator"] == locator


@pytest.mark.asyncio
async def test_upload_keeps_bot_owner_when_collaborator_is_actor():
    filesystem = _Filesystem()
    audit = _Audit()
    service = _service(filesystem, audit=audit)
    result = await service.upload_local_skill(
        bot_id="bot",
        owner_id="owner",
        actor_id="collaborator",
        package=_zip(
            {"SKILL.md": b"---\nname: upload-skill\ndescription: useful\n---\n"}
        ),
    )
    assert result["operation"] == "created"
    assert result["skill"]["user_id"] == "owner"
    assert result["actor_id"] == "collaborator"
    assert filesystem.files["/private/skills-local/upload-skill/SKILL.md"]


@pytest.mark.asyncio
async def test_disabled_observation_creates_no_scan_handoff():
    filesystem = _Filesystem()
    audit = _Audit()
    observer = _CapturedSecurityObserver()
    service = _service(
        filesystem, audit=audit, security_observer=observer,
        security_observation_enabled=False,
    )
    result = await service.upload_local_skill(
        bot_id="bot", owner_id="owner", actor_id="owner",
        package=_zip({"SKILL.md": _skill_md()}),
    )
    assert result["operation"] == "created"
    assert observer.calls == []


@pytest.mark.asyncio
async def test_disabled_replacement_never_reads_security_table():
    class _ForbiddenObserver:
        def current_incarnation_id(self, **_kwargs):
            raise AssertionError("disabled observation queried security identity")

        def observe(self, **_kwargs):
            raise AssertionError("disabled observation started a scan")

    filesystem = _Filesystem()
    filesystem.files["/private/skills-local/upload-skill/SKILL.md"] = b"old"
    service = _replacement_service(
        filesystem, _ReplacementRepo([_existing_skill(active=False)]),
        _ReplacementRuntime([True]), security_observer=_ForbiddenObserver(),
        security_observation_enabled=False,
    )
    result = await service.upload_local_skill(
        bot_id="bot", owner_id="owner", actor_id="owner",
        package=_zip({"SKILL.md": _skill_md(description="new")}),
    )
    assert result["operation"] == "updated"


@pytest.mark.asyncio
async def test_local_upload_preserves_legacy_frontmatter_config_compatibility():
    result = await _service(_Filesystem()).upload_local_skill(
        bot_id="bot",
        owner_id="owner",
        actor_id="owner",
        package=_zip(
            {
                "SKILL.md": (
                    b"---\nname: upload-skill\ndescription: useful\n"
                    b"config: not-a-list\n---\n"
                )
            }
        ),
    )

    assert result["operation"] == "created"


@pytest.mark.asyncio
async def test_invalid_upload_is_rejected_before_device_or_cleanup_side_effects():
    class _UnavailableDeviceResolver:
        def resolve_for_bot(self, *_args):
            raise RuntimeError("device context unavailable")

    class _CleanupMustNotBeRead(_Cleanup):
        def list_pending(self, **_kwargs):
            raise AssertionError("invalid package must not retry cleanup")

    service = _service(_Filesystem())
    service._device_context_resolver_provider = lambda: _UnavailableDeviceResolver()
    service._cleanup_repo = _CleanupMustNotBeRead()

    with pytest.raises(LocalSkillInvalidPackageError):
        await service.upload_local_skill(
            bot_id="bot",
            owner_id="owner",
            actor_id="owner",
            package=b"not a zip archive",
        )


@pytest.mark.asyncio
async def test_upload_resolves_package_storage_with_bot_entity():
    filesystem = _Filesystem()
    factory = _Factory(filesystem)
    repo = _Repo()
    service = _service(
        filesystem,
        bot=_Bot(entity_id="project-entity"),
        factory=factory,
        repo=repo,
    )

    await service.upload_local_skill(
        bot_id="bot",
        owner_id="owner",
        actor_id="owner",
        package=_zip(
            {"SKILL.md": b"---\nname: upload-skill\ndescription: useful\n---\n"}
        ),
    )

    assert factory.storage_calls == [
        {
            "entity_id": "project-entity",
            "owner_id": "project-entity",
            "bot_id": "bot",
            "engine_type": "openclaw",
            "entity_type": "staff",
            "is_desktop": False,
            "is_teclaw": False,
            "name": "upload-skill",
        }
    ]
    assert repo.created[0]["user_id"] == "project-entity"


@pytest.mark.asyncio
async def test_upload_stays_inactive_without_creating_a_default_set_membership():
    filesystem = _Filesystem()
    service = _service(filesystem)

    result = await service.upload_local_skill(
        bot_id="bot",
        owner_id="owner",
        actor_id="owner",
        package=_zip({"SKILL.md": _skill_md()}),
    )

    assert result["operation"] == "created"


@pytest.mark.asyncio
async def test_directory_upload_uses_the_same_create_flow_as_raw_zip():
    filesystem = _Filesystem()
    result = await _service(filesystem).upload_local_skill_files(
        bot_id="bot",
        owner_id="owner",
        actor_id="owner",
        files=[
            ("folder-skill/SKILL.md", _skill_md("folder-skill")),
            ("folder-skill/scripts/main.py", b"print('ok')"),
        ],
    )

    assert result["operation"] == "created"
    assert filesystem.files == {
        "/private/skills-local/folder-skill/SKILL.md": _skill_md("folder-skill"),
        "/private/skills-local/folder-skill/scripts/main.py": b"print('ok')",
    }


@pytest.mark.asyncio
async def test_local_zip_folder_replace_and_io_failures_use_actual_filesystem(tmp_path):
    """Exercise the Local ZIP/folder entry points over a real device directory.

    This is intentionally a small product slice: the production validator,
    create/replace service, and ``LocalSkillPackageStorage`` run unchanged;
    only the device boundary is a temporary Local filesystem. The Desktop
    invoke-http byte path has its matching Engine-facing contract test.
    """
    filesystem = _DiskFilesystem(tmp_path / "device")
    factory = _DiskStorageFactory(filesystem)
    raw_files = {
        "SKILL.md": _skill_md("upload-skill", "raw archive"),
        "docs/readme.txt": b"plain text\n",
        "assets/icon.png": b"\x89PNG\r\n\x1a\n\xff\x00",
        "assets/nonutf8.bin": b"\xff\x00\x80\xfe",
        "assets/empty.txt": b"",
    }
    await _service(filesystem, factory=factory).upload_local_skill(
        bot_id="bot",
        owner_id="owner",
        actor_id="owner",
        package=_zip(raw_files),
    )

    folder_files = {
        "SKILL.md": _skill_md("folder-skill", "folder upload"),
        "scripts/run.py": b"print('ok')\n",
        "assets/nonutf8.bin": b"\x80\xff\x00",
        "assets/empty.txt": b"",
    }
    await _service(filesystem, factory=factory).upload_local_skill_files(
        bot_id="bot",
        owner_id="owner",
        actor_id="owner",
        files=[
            (f"folder-skill/{path}", content) for path, content in folder_files.items()
        ],
    )

    raw_storage = LocalSkillPackageStorage(
        filesystem, "/private/skills-local/upload-skill"
    )
    folder_storage = LocalSkillPackageStorage(
        filesystem, "/private/skills-local/folder-skill"
    )
    assert dict(await raw_storage.read_package_files()) == raw_files
    assert dict(await folder_storage.read_package_files()) == folder_files

    replacement_files = {
        "SKILL.md": _skill_md("upload-skill", "replacement archive"),
        "docs/readme.txt": b"replacement text\n",
        "assets/icon.png": b"\x89PNG\r\n\x1a\n\x00\xff",
        "assets/nonutf8.bin": b"\x50\x4b\x03\x04\xff\x00\x01",
        "assets/empty.txt": b"",
    }
    replacement = await _replacement_service(
        filesystem,
        _ReplacementRepo([_existing_skill(active=False)]),
        _ReplacementRuntime([True]),
        factory=factory,
    ).upload_local_skill(
        bot_id="bot",
        owner_id="owner",
        actor_id="owner",
        package=_zip(replacement_files),
    )
    assert replacement["operation"] == "updated"
    actual_replacement = dict(await raw_storage.read_package_files())
    assert {
        path: hashlib.sha256(content).hexdigest()
        for path, content in actual_replacement.items()
    } == {
        path: hashlib.sha256(content).hexdigest()
        for path, content in replacement_files.items()
    }

    failed_write_path = "/private/failed/write.bin"
    failed_write = LocalSkillPackageStorage(
        _DiskFilesystem(tmp_path / "write-failure", fail_writes={failed_write_path}),
        "/private/failed",
    )
    with pytest.raises(OSError, match="device write failed"):
        await failed_write.write([("write.bin", b"no write")])

    failed_read_path = "/private/read-failure/asset.bin"
    failed_read_filesystem = _DiskFilesystem(tmp_path / "read-failure")
    failed_read = LocalSkillPackageStorage(
        failed_read_filesystem, "/private/read-failure"
    )
    await failed_read.write([("asset.bin", b"written first")])
    failed_read_filesystem._fail_reads.add(failed_read_path)
    with pytest.raises(OSError, match="device read failed"):
        await failed_read.read_file("asset.bin")


@pytest.mark.asyncio
async def test_multipart_single_zip_keeps_legacy_auto_extract_behavior():
    filesystem = _Filesystem()
    archive = _zip({"SKILL.md": _skill_md("archive-skill")})

    result = await _service(filesystem).upload_local_skill_files(
        bot_id="bot",
        owner_id="owner",
        actor_id="owner",
        files=[("archive.zip", archive)],
    )

    assert result["skill"]["name"] == "archive-skill"
    assert filesystem.files[
        "/private/skills-local/archive-skill/SKILL.md"
    ] == _skill_md("archive-skill")


@pytest.mark.asyncio
async def test_not_ready_and_storage_failure_leave_no_public_skill():
    package = _zip({"SKILL.md": _skill_md()})
    with pytest.raises(LocalSkillNotReadyError):
        await _service(_Filesystem(), status="PENDING").upload_local_skill(
            bot_id="bot", owner_id="owner", actor_id="owner", package=package
        )
    filesystem = _Filesystem(fail=True)
    service = _service(filesystem)
    with pytest.raises(LocalSkillStorageError):
        await service.upload_local_skill(
            bot_id="bot", owner_id="owner", actor_id="owner", package=package
        )
    assert filesystem.deleted == ["/private/skills-local/upload-skill"]


class _Denied:
    def check_collaborator_permission(self, *args, **kwargs):
        return {"has_permission": False}


@pytest.mark.asyncio
async def test_owner_locator_and_denied_collaborator_cannot_forge_access():
    from agentclaw.community.core.skill_center.errors import LocalSkillNotFoundError

    package = _zip({"SKILL.md": _skill_md()})
    with pytest.raises(LocalSkillNotFoundError):
        await _service(_Filesystem(), collaborators=_Denied()).upload_local_skill(
            bot_id="bot", owner_id="owner", actor_id="attacker", package=package
        )


class _FailRepo(_Repo):
    def create(self, row):
        raise RuntimeError("db failure")


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["write", "create"])
async def test_each_creation_failure_compensates_and_never_returns_success(stage):
    package = _zip({"SKILL.md": _skill_md()})
    filesystem = _Filesystem(fail=stage == "write")
    repo = _FailRepo() if stage == "create" else _Repo()
    service = _service(filesystem, repo=repo)
    with pytest.raises(LocalSkillStorageError):
        await service.upload_local_skill(
            bot_id="bot", owner_id="owner", actor_id="owner", package=package
        )
    assert filesystem.deleted == ["/private/skills-local/upload-skill"]


@pytest.mark.asyncio
async def test_existing_orphan_must_clear_before_a_retry_writes_new_files():
    filesystem = _Filesystem(cleanup_results=[False, False])
    filesystem.files["/private/skills-local/upload-skill/stale.txt"] = b"orphan"
    repo = _Repo()
    with pytest.raises(LocalSkillStorageError):
        await _service(filesystem, repo=repo).upload_local_skill(
            bot_id="bot",
            owner_id="owner",
            actor_id="owner",
            package=_zip({"SKILL.md": _skill_md()}),
        )
    assert repo.created == []
    assert filesystem.files
    assert filesystem.deleted == ["/private/skills-local/upload-skill"] * 2


def _existing_skill(*, active=True):
    return {
        "id": "9",
        "name": "upload-skill",
        "description": "old description",
        "git_path": "local:///private/skills-local/upload-skill",
        "user_id": "owner",
        "bolt_id": "bot",
        "active": active,
        "gmt_created": None,
        "gmt_modified": None,
    }


@pytest.mark.asyncio
async def test_same_name_replacement_preserves_id_owner_and_desired_state_after_staging():
    filesystem = _Filesystem()
    filesystem.files["/private/skills-local/upload-skill/SKILL.md"] = b"old"
    repo = _ReplacementRepo([_existing_skill(active=False)])
    runtime = _ReplacementRuntime([True])
    result = await _replacement_service(filesystem, repo, runtime).upload_local_skill(
        bot_id="bot",
        owner_id="owner",
        actor_id="collaborator",
        package=_zip({"SKILL.md": _skill_md(description="new description")}),
    )
    assert result["operation"] == "updated"
    assert result["skill"]["id"] == "9"
    assert result["skill"]["user_id"] == "owner"
    assert result["skill"]["active"] is False
    assert result["skill"]["git_path"] == "local:///private/skills-local/upload-skill"
    assert runtime.calls == 1
    assert "/private/skills-local/upload-skill" in filesystem.deleted
    assert not any("replacement-" in path for path in filesystem.files)
    assert filesystem.files["/private/skills-local/upload-skill/SKILL.md"] == (
        _skill_md(description="new description")
    )


@pytest.mark.asyncio
async def test_legacy_fallback_replace_preserves_an_aliased_recorded_locator():
    canonical = "/private/skills-local/upload-skill"
    historical = (
        "/aidesktop/owner/bot/claude_code/workspace/skills/skills-local/upload-skill"
    )
    filesystem = _Filesystem()
    filesystem.files[f"{canonical}/SKILL.md"] = b"old"
    old = {**_existing_skill(active=False), "git_path": f"local://{historical}"}
    repo = _ReplacementRepo([old])
    factory = _ReplacementFactory(
        filesystem,
        aliases={historical: canonical},
    )

    result = await _replacement_service(
        filesystem,
        repo,
        _ReplacementRuntime([True]),
        factory=factory,
    ).upload_local_skill(
        bot_id="bot",
        owner_id="owner",
        actor_id="owner",
        package=_zip({"SKILL.md": _skill_md(description="new description")}),
    )

    assert result["skill"]["git_path"] == f"local://{historical}"
    assert repo.atomic_replacements[0]["old_locator"] == historical
    assert repo.atomic_replacements[0]["new_locator"] == historical
    assert filesystem.files[f"{canonical}/SKILL.md"] == _skill_md(
        description="new description"
    )


@pytest.mark.asyncio
async def test_replacement_rejects_a_stale_row_with_no_authoritative_package():
    filesystem = _Filesystem()
    skill = _existing_skill(active=False)
    repo = _ReplacementRepo([skill])

    with pytest.raises(LocalSkillStorageError):
        await _replacement_service(
            filesystem, repo, _ReplacementRuntime([True])
        ).upload_local_skill(
            bot_id="bot",
            owner_id="owner",
            actor_id="owner",
            package=_zip({"SKILL.md": b"name: upload-skill\ndescription: restored\n"}),
        )

    assert skill["git_path"] == "local:///private/skills-local/upload-skill"
    assert filesystem.files == {}
    assert not any(".replacement-" in path for path in filesystem.files)


@pytest.mark.asyncio
async def test_replacement_rejects_a_noncanonical_existing_locator_without_writing():
    old_locator = "/private/skills-local/.upload-skill.replacement-old"
    filesystem = _Filesystem()
    filesystem.files[f"{old_locator}/SKILL.md"] = b"old"
    old = {**_existing_skill(active=False), "git_path": f"local://{old_locator}"}
    repo = _ReplacementRepo([old])

    with pytest.raises(LocalSkillStorageError):
        await _replacement_service(
            filesystem, repo, _ReplacementRuntime([True])
        ).upload_local_skill(
            bot_id="bot",
            owner_id="owner",
            actor_id="owner",
            package=_zip({"SKILL.md": b"name: upload-skill\ndescription: canonical\n"}),
        )

    canonical = "/private/skills-local/upload-skill"
    assert old["git_path"] == f"local://{old_locator}"
    assert f"{canonical}/SKILL.md" not in filesystem.files
    assert filesystem.files[f"{old_locator}/SKILL.md"] == b"old"
    assert set(filesystem.files) == {f"{old_locator}/SKILL.md"}


@pytest.mark.asyncio
async def test_post_commit_temp_cleanup_failure_restores_old_package_and_metadata():
    # canonical publish succeeds, then the first final temp cleanup fails once.
    # The upload must restore the previous package before reporting failure.
    filesystem = _Filesystem(cleanup_results=[True, False, True, True, True])
    canonical = "/private/skills-local/upload-skill"
    filesystem.files[f"{canonical}/SKILL.md"] = b"old"
    old = _existing_skill(active=False)
    repo = _ReplacementRepo([old])
    observer = _CapturedSecurityObserver()

    with pytest.raises(LocalSkillStorageError):
        await _replacement_service(
            filesystem,
            repo,
            _ReplacementRuntime([True, True]),
            security_observer=observer,
        ).upload_local_skill(
            bot_id="bot",
            owner_id="owner",
            actor_id="owner",
            package=_zip({"SKILL.md": _skill_md(description="new description")}),
        )

    assert old["git_path"] == f"local://{canonical}"
    assert observer.calls == []
    assert old["description"] == "old description"
    assert filesystem.files[f"{canonical}/SKILL.md"] == b"old"
    assert not any(
        marker in path
        for path in filesystem.files
        for marker in (".replacement-", ".rollback-")
    )


@pytest.mark.asyncio
async def test_replacement_discards_staging_and_backup_when_backup_copy_fails(
    monkeypatch,
):
    filesystem = _Filesystem()
    canonical = "/private/skills-local/upload-skill"
    rollback = "/private/skills-local/.upload-skill.rollback-backup"
    filesystem.files[f"{canonical}/SKILL.md"] = b"old"
    filesystem.files[f"{rollback}/stale.txt"] = b"stale"
    repo = _ReplacementRepo([_existing_skill(active=False)])
    ids = iter([SimpleNamespace(hex="staged"), SimpleNamespace(hex="backup")])
    monkeypatch.setattr(upload_module, "uuid4", lambda: next(ids))

    with pytest.raises(LocalSkillStorageError):
        await _replacement_service(
            filesystem, repo, _ReplacementRuntime([True])
        ).upload_local_skill(
            bot_id="bot",
            owner_id="owner",
            actor_id="owner",
            package=_zip(
                {"SKILL.md": b"name: upload-skill\ndescription: replacement\n"}
            ),
        )

    assert rollback in filesystem.deleted
    assert "/private/skills-local/.upload-skill.replacement-staged" in (
        filesystem.deleted
    )
    assert repo.atomic_replacements == []


@pytest.mark.asyncio
async def test_restore_replacement_requires_backup_after_canonical_publish():
    filesystem = _Filesystem()
    skill = _existing_skill(active=False)
    service = _replacement_service(
        filesystem, _ReplacementRepo([skill]), _ReplacementRuntime([True])
    )

    with pytest.raises(LocalSkillStorageError):
        await service._restore_replacement(
            skill=skill,
            old_metadata={},
            owner_id="owner",
            bot_id="bot",
            staged=_Storage(filesystem, "/private/staged"),
            canonical=_Storage(filesystem, "/private/canonical"),
            backup=None,
            canonical_published=True,
            switched=False,
            runtime_sync_attempted=False,
        )


@pytest.mark.asyncio
@pytest.mark.skip(reason="durable cleanup work was removed")
async def test_replacement_is_blocked_while_the_same_skill_has_delete_repair_work():
    filesystem = _Filesystem()
    filesystem.files["/private/skills-local/upload-skill/SKILL.md"] = b"old"
    repo = _ReplacementRepo([_existing_skill(active=False)])

    class _RepairRequiredCleanup(_Cleanup):
        def list_repair_required(self, **kwargs):
            assert kwargs == {
                "env": "test",
                "owner_id": "owner",
                "bot_id": "bot",
                "skill_id": "9",
            }
            return [{"id": 12, "status": "repair_required"}]

    with pytest.raises(LocalSkillStorageError):
        await _replacement_service(
            filesystem,
            repo,
            _ReplacementRuntime([True]),
            _RepairRequiredCleanup(),
        ).upload_local_skill(
            bot_id="bot",
            owner_id="owner",
            actor_id="owner",
            package=_zip({"SKILL.md": _skill_md(description="new description")}),
        )

    assert repo.atomic_replacements == []
    assert filesystem.files == {"/private/skills-local/upload-skill/SKILL.md": b"old"}


@pytest.mark.asyncio
async def test_replacement_reads_desired_state_from_exact_local_skill_query():
    """The duplicate scan is metadata-only in production and has no ``active``."""
    filesystem = _Filesystem()
    filesystem.files["/private/skills-local/upload-skill/SKILL.md"] = b"old"
    repo = _ReplacementRepo([_existing_skill(active=True)])
    repo.list_bot_local_by_name = lambda **_kwargs: [
        {key: value for key, value in repo.rows[0].items() if key != "active"}
    ]
    runtime = _ReplacementRuntime([True])

    result = await _replacement_service(filesystem, repo, runtime).upload_local_skill(
        bot_id="bot",
        owner_id="owner",
        actor_id="owner",
        package=_zip({"SKILL.md": _skill_md(description="new description")}),
    )

    assert result["operation"] == "updated"
    assert runtime.calls == 1


@pytest.mark.asyncio
async def test_active_replacement_keeps_installation_owned_state_before_sync():
    filesystem = _Filesystem()
    filesystem.files["/private/skills-local/upload-skill/SKILL.md"] = b"old"
    repo = _ReplacementRepo([_existing_skill(active=True)])
    runtime = _ReplacementRuntime([True])

    await _replacement_service(filesystem, repo, runtime).upload_local_skill(
        bot_id="bot",
        owner_id="owner",
        actor_id="owner",
        package=_zip({"SKILL.md": _skill_md(description="new description")}),
    )

    assert runtime.calls == 1


@pytest.mark.asyncio
async def test_teclaw_replacement_resolves_provider_before_staging():
    filesystem = _Filesystem()
    filesystem.files["/private/skills-local/upload-skill/SKILL.md"] = b"old"
    repo = _ReplacementRepo([_existing_skill(active=False)])
    service = _replacement_service(
        filesystem,
        repo,
        _ReplacementRuntime([True]),
        provider="teclaw",
    )

    result = await service.upload_local_skill(
        bot_id="bot",
        owner_id="owner",
        actor_id="owner",
        package=_zip({"SKILL.md": _skill_md(description="new description")}),
    )

    factory = service._skill_service_factory
    assert result["operation"] == "updated"
    assert factory.storage_calls[0]["is_teclaw"] is True
    assert factory.locator_calls[0]["is_teclaw"] is True


@pytest.mark.asyncio
async def test_active_replacement_keeps_new_content_when_runtime_is_pending():
    filesystem = _Filesystem()
    filesystem.files["/private/skills-local/upload-skill/SKILL.md"] = b"old"
    old = _existing_skill(active=True)
    repo = _ReplacementRepo([old])
    runtime = _ReplacementRuntime([False, True])
    result = await _replacement_service(filesystem, repo, runtime).upload_local_skill(
        bot_id="bot",
        owner_id="owner",
        actor_id="owner",
        package=_zip({"SKILL.md": _skill_md(description="new description")}),
    )

    # The package/database write is the Local Skill Desired State. A runtime
    # outage is reported to the caller but must not silently restore the old
    # content after that durable state has committed.
    assert result["runtime_projection"]["status"] == "PENDING"
    assert old["git_path"] == "local:///private/skills-local/upload-skill"
    assert old["description"] == "new description"
    assert runtime.calls == 1
    assert filesystem.files["/private/skills-local/upload-skill/SKILL.md"] == (
        _skill_md(description="new description")
    )


@pytest.mark.asyncio
async def test_active_replacement_pending_ensures_common_desktop_recovery():
    class _PendingProjector:
        calls = 0

        async def project(self, **_kwargs):
            self.calls += 1
            return RuntimeProjectionResult.pending(
                code="CENTER_CONTENT_DOWNLOAD_PENDING",
                reason="Center package download is active",
            )

    class _Recovery:
        def __init__(self) -> None:
            self.calls = []

        def ensure(self, **kwargs):
            self.calls.append(kwargs)

    filesystem = _Filesystem()
    filesystem.files["/private/skills-local/upload-skill/SKILL.md"] = b"old"
    recovery = _Recovery()
    runtime = RecoveringBotRuntimeProjector(
        delegate=_PendingProjector(),  # type: ignore[arg-type]
        recovery=recovery,  # type: ignore[arg-type]
    )

    result = await _replacement_service(
        filesystem,
        _ReplacementRepo([_existing_skill(active=True)]),
        runtime,
    ).upload_local_skill(
        bot_id="bot",
        owner_id="owner",
        actor_id="owner",
        package=_zip({"SKILL.md": _skill_md(description="new description")}),
    )

    assert result["runtime_projection"]["status"] == "PENDING"
    assert recovery.calls == [{"owner_id": "owner", "bot_id": "bot"}]


@pytest.mark.asyncio
async def test_active_replacement_preserves_enqueue_failure_and_new_content(caplog):
    class _PendingProjector:
        async def project(self, **_kwargs):
            return RuntimeProjectionResult.pending(
                code="CENTER_CONTENT_DOWNLOAD_PENDING",
                reason="Center package download is active",
            )

    class _DesktopBots:
        def get_by_id_and_owner(self, bot_id, owner_id):
            return {
                "bot_id": bot_id,
                "owner_id": owner_id,
                "bot_type": "desktop",
                "status": "ACTIVE",
            }

    class _FailingTasks:
        def enqueue(self, *_args, **_kwargs):
            raise RuntimeError("task database unavailable")

    filesystem = _Filesystem()
    filesystem.files["/private/skills-local/upload-skill/SKILL.md"] = b"old"
    runtime = RecoveringBotRuntimeProjector(
        delegate=_PendingProjector(),  # type: ignore[arg-type]
        recovery=DesktopSkillRecoveryService(
            bots=_DesktopBots(),  # type: ignore[arg-type]
            tasks=_FailingTasks(),  # type: ignore[arg-type]
            task_deadline_seconds=600,
        ),
    )

    result = await _replacement_service(
        filesystem,
        _ReplacementRepo([_existing_skill(active=True)]),
        runtime,
    ).upload_local_skill(
        bot_id="bot",
        owner_id="owner",
        actor_id="owner",
        package=_zip({"SKILL.md": _skill_md(description="new description")}),
    )

    assert result["operation"] == "updated"
    assert {issue["code"] for issue in result["runtime_projection"]["issues"]} == {
        "CENTER_CONTENT_DOWNLOAD_PENDING",
        "DESKTOP_SKILL_RECOVERY_ENQUEUE_FAILED",
    }
    assert filesystem.files["/private/skills-local/upload-skill/SKILL.md"] == (
        _skill_md(description="new description")
    )
    assert "task database unavailable" in caplog.text


@pytest.mark.asyncio
@pytest.mark.skip(reason="durable cleanup work was removed")
async def test_active_replacement_restore_sync_failure_keeps_original_authority_and_records_staged_cleanup():
    filesystem = _Filesystem(cleanup_results=[True, True])
    filesystem.files["/private/skills-local/upload-skill/SKILL.md"] = b"old"
    old = _existing_skill(active=True)
    cleanup = _Cleanup()
    with pytest.raises(LocalSkillRuntimeSyncError):
        await _replacement_service(
            filesystem,
            _ReplacementRepo([old]),
            _ReplacementRuntime([False, False]),
            cleanup,
        ).upload_local_skill(
            bot_id="bot",
            owner_id="owner",
            actor_id="owner",
            package=_zip({"SKILL.md": _skill_md(description="new description")}),
        )
    assert old["git_path"] == "local:///private/skills-local/upload-skill"
    staged_work = next(
        row for row in cleanup.rows if "replacement-" in row["package_locator"]
    )
    assert staged_work["requires_runtime_restore"] is True
    assert cleanup.cancelled == [1]
    assert filesystem.files["/private/skills-local/upload-skill/SKILL.md"] == b"old"


@pytest.mark.asyncio
async def test_duplicate_legacy_matches_fail_without_writing_or_selecting_a_candidate():
    filesystem = _Filesystem()
    repo = _ReplacementRepo([_existing_skill(), {**_existing_skill(), "id": "10"}])
    with pytest.raises(upload_module.LocalSkillDuplicateError):
        await _replacement_service(
            filesystem, repo, _ReplacementRuntime([True])
        ).upload_local_skill(
            bot_id="bot",
            owner_id="owner",
            actor_id="owner",
            package=_zip({"SKILL.md": _skill_md(description="new description")}),
        )
    assert filesystem.files == {}
    assert repo.updates == []


@pytest.mark.asyncio
async def test_foreign_owner_same_name_is_excluded_from_this_owner_scope():
    filesystem = _Filesystem()
    foreign = {**_existing_skill(), "user_id": "other-owner"}
    repo = _ReplacementRepo([foreign])
    result = await _replacement_service(
        filesystem,
        repo,
        _ReplacementRuntime([True]),
    ).upload_local_skill(
        bot_id="bot",
        owner_id="owner",
        actor_id="owner",
        package=_zip({"SKILL.md": _skill_md(description="new description")}),
    )
    assert result["operation"] == "created"
    assert repo.updates == []
    assert foreign["git_path"] == "local:///private/skills-local/upload-skill"


@pytest.mark.asyncio
@pytest.mark.skip(reason="durable cleanup work was removed")
async def test_post_switch_obsolete_cleanup_failure_is_recorded_without_undoing_update():
    filesystem = _Filesystem(cleanup_results=[True, False])
    filesystem.files["/private/skills-local/upload-skill/SKILL.md"] = b"old"
    repo = _ReplacementRepo([_existing_skill(active=False)])
    cleanup = _Cleanup()
    result = await _replacement_service(
        filesystem, repo, _ReplacementRuntime([True]), cleanup
    ).upload_local_skill(
        bot_id="bot",
        owner_id="owner",
        actor_id="owner",
        package=_zip({"SKILL.md": _skill_md(description="new description")}),
    )
    assert result["operation"] == "updated"
    assert len(cleanup.rows) == 1
    assert cleanup.rows[0]["requires_runtime_restore"] is True
    assert ".upload-skill.rollback-" in cleanup.rows[0]["package_locator"]


@pytest.mark.asyncio
@pytest.mark.skip(reason="durable cleanup work was removed")
async def test_cleanup_registration_failure_restores_old_authority_before_runtime_or_purge():
    filesystem = _Filesystem()
    filesystem.files["/private/skills-local/upload-skill/SKILL.md"] = b"old"
    old = _existing_skill(active=True)
    runtime = _ReplacementRuntime([True])
    with pytest.raises(LocalSkillStorageError):
        await _replacement_service(
            filesystem,
            _ReplacementRepo([old]),
            runtime,
            _CleanupRecordFailure(),
        ).upload_local_skill(
            bot_id="bot",
            owner_id="owner",
            actor_id="owner",
            package=_zip({"SKILL.md": _skill_md(description="new description")}),
        )
    assert old["git_path"] == "local:///private/skills-local/upload-skill"
    assert filesystem.files["/private/skills-local/upload-skill/SKILL.md"] == b"old"
    assert runtime.calls == 0
    assert not any("replacement-" in path for path in filesystem.files)


@pytest.mark.asyncio
@pytest.mark.skip(reason="durable cleanup work was removed")
async def test_later_serialized_upload_retries_durable_cleanup_work():
    filesystem = _Filesystem()
    filesystem.files["/private/skills-local/obsolete/SKILL.md"] = b"obsolete"
    filesystem.files["/private/skills-local/upload-skill/SKILL.md"] = b"old"
    cleanup = _PendingCleanup()
    result = await _replacement_service(
        filesystem,
        _ReplacementRepo([_existing_skill(active=False)]),
        _ReplacementRuntime([True]),
        cleanup,
    ).upload_local_skill(
        bot_id="bot",
        owner_id="owner",
        actor_id="owner",
        package=_zip({"SKILL.md": _skill_md(description="new description")}),
    )
    assert result["operation"] == "updated"
    assert 12 in cleanup.completed
    assert cleanup.failed == []


@pytest.mark.asyncio
@pytest.mark.skip(reason="durable cleanup work was removed")
async def test_cleanup_skips_a_locator_reused_by_a_current_local_skill():
    filesystem = _Filesystem()
    locator = "/private/skills-local/upload-skill"
    filesystem.files[f"{locator}/SKILL.md"] = b"authoritative"

    class _ReusedLocatorCleanup(_PendingCleanup):
        def list_pending(self, **_kwargs):
            return [{"id": 12, "skill_id": "stale", "package_locator": locator}]

    cleanup = _ReusedLocatorCleanup()
    service = _replacement_service(
        filesystem,
        _ReplacementRepo([_existing_skill(active=False)]),
        _ReplacementRuntime([True]),
        cleanup,
    )

    await service._retry_pending_cleanup(
        bot={"env": "dev", "entity_id": "owner", "active_engine": "moltis"},
        owner_id="owner",
        bot_id="bot",
        is_teclaw=False,
    )

    assert cleanup.cancelled == [12]
    assert cleanup.completed == []
    assert filesystem.files[f"{locator}/SKILL.md"] == b"authoritative"


@pytest.mark.asyncio
@pytest.mark.parametrize("cleanup_results", [None, [False]])
@pytest.mark.skip(reason="durable cleanup work was removed")
async def test_cleanup_progress_write_failure_blocks_the_next_replacement(
    cleanup_results,
):
    filesystem = _Filesystem(cleanup_results=cleanup_results)
    filesystem.files["/private/skills-local/obsolete/SKILL.md"] = b"obsolete"
    repo = _ReplacementRepo([_existing_skill(active=False)])
    with pytest.raises(LocalSkillStorageError):
        await _replacement_service(
            filesystem,
            repo,
            _ReplacementRuntime([True]),
            _UnwritableCleanupProgress(),
        ).upload_local_skill(
            bot_id="bot",
            owner_id="owner",
            actor_id="owner",
            package=_zip({"SKILL.md": _skill_md(description="new description")}),
        )
    assert repo.updates == []


@pytest.mark.asyncio
@pytest.mark.skip(reason="durable cleanup work was removed")
async def test_runtime_restore_work_keeps_staged_bytes_until_old_mapping_is_restored():
    filesystem = _Filesystem()
    filesystem.files["/private/skills-local/staged/SKILL.md"] = b"staged"
    filesystem.files["/private/skills-local/upload-skill/SKILL.md"] = b"old"
    cleanup = _RuntimeRestoreCleanup()
    runtime = _ReplacementRuntime([True, True])
    await _replacement_service(
        filesystem,
        _ReplacementRepo([_existing_skill(active=False)]),
        runtime,
        cleanup,
    ).upload_local_skill(
        bot_id="bot",
        owner_id="owner",
        actor_id="owner",
        package=_zip({"SKILL.md": _skill_md(description="new description")}),
    )
    assert runtime.calls == 2
    assert 12 in cleanup.completed
    assert "/private/skills-local/staged/SKILL.md" not in filesystem.files


@pytest.mark.asyncio
@pytest.mark.skip(reason="durable cleanup work was removed")
async def test_runtime_restore_failure_blocks_the_next_local_skill_mutation():
    filesystem = _Filesystem()
    filesystem.files["/private/skills-local/staged/SKILL.md"] = b"staged"
    cleanup = _RuntimeRestoreCleanup()
    runtime = _ReplacementRuntime([False])
    repo = _ReplacementRepo([_existing_skill(active=False)])

    with pytest.raises(LocalSkillStorageError):
        await _replacement_service(
            filesystem, repo, runtime, cleanup
        ).upload_local_skill(
            bot_id="bot",
            owner_id="owner",
            actor_id="owner",
            package=_zip({"SKILL.md": _skill_md(description="new description")}),
        )

    assert runtime.calls == 1
    assert cleanup.failed == [(12, "runtime restore before cleanup failed")]
    assert repo.updates == []
    assert "/private/skills-local/staged/SKILL.md" in filesystem.files


class _LockingCache:
    def __init__(self):
        self.held: dict[str, str] = {}

    def acquire_lock(self, key, ttl=30):
        if key in self.held:
            return None
        self.held[key] = "token"
        return "token"

    def acquire_lock_strict(self, key, ttl=30):
        return self.acquire_lock(key, ttl)

    def release_lock(self, key, token):
        if self.held.get(key) != token:
            return False
        del self.held[key]
        return True


class _PoolLayouts:
    def get(self, scope):
        return BotSkillLayoutState(
            scope=scope,
            active_layout=SkillLayout.POOL,
            target_layout=None,
            phase=SkillLayoutPhase.POOL_ACTIVE,
            migration_generation="generation-1",
            persisted=True,
        )


class _YieldingFilesystem(_Filesystem):
    async def write_file(self, path, content):
        await asyncio.sleep(0)
        await super().write_file(path, content)


@pytest.mark.asyncio
async def test_concurrent_same_name_uploads_serialize_then_converge_on_one_skill():
    repo = _ConcurrentRepo()
    filesystem = _YieldingFilesystem()

    class _Participation:
        def resolve(self, *, scope):
            return SkillLayoutParticipation(
                participates_in_pool_layout=True,
                label="test_pool_layout",
            )

    guard = SkillsPoolEditGuard(
        cache=_LockingCache(),
        layout_repository=_PoolLayouts(),
        participation_resolver=_Participation(),
    )
    package = _zip({"SKILL.md": _skill_md(description="concurrent")})
    first, second = await asyncio.gather(
        _replacement_service(
            filesystem, repo, _ReplacementRuntime([True]), guard=guard
        ).upload_local_skill(
            bot_id="bot",
            owner_id="owner",
            actor_id="owner",
            package=package,
        ),
        _replacement_service(
            filesystem, repo, _ReplacementRuntime([True]), guard=guard
        ).upload_local_skill(
            bot_id="bot",
            owner_id="owner",
            actor_id="collaborator",
            package=package,
        ),
    )
    assert {first["operation"], second["operation"]} == {"created", "updated"}
    assert len(repo.rows) == 1
    assert first["skill"]["id"] == second["skill"]["id"] == "9"


@pytest.mark.asyncio
async def test_a_local_skill_replacement_never_touches_the_mcp_half():
    """A replace changes package bytes and description — never the MCP set.

    ``replace_bot_local_skill`` writes only description/user_id/gmt_modified
    and refuses to move ``git_path``; nothing in this flow rescans
    ``mcp_dependencies``. So the projected MCP codes are identical before and
    after, and claiming or releasing any of them would be a device write to
    restate what is already true.
    """
    filesystem = _Filesystem()
    filesystem.files["/private/skills-local/upload-skill/SKILL.md"] = b"old"
    repo = _ReplacementRepo([_existing_skill(active=False)])
    runtime = _ReplacementRuntime([True])

    await _replacement_service(filesystem, repo, runtime).upload_local_skill(
        bot_id="bot",
        owner_id="owner",
        actor_id="collaborator",
        package=_zip({"SKILL.md": _skill_md(description="new description")}),
    )

    (scope,) = runtime.scopes
    assert scope.skills is True
    assert scope.mcp is False
    assert scope.claim_all_mcp is False, (
        "a replace has prior state to converge on; it is not a bare device"
    )
    assert scope.claimed_mcp == frozenset()
    assert scope.released_mcp == frozenset()
