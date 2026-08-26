"""Core fault-injection seam for Local Skill create-or-replace uploads."""

from __future__ import annotations

import asyncio
import io
import zipfile
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
from agentclaw.community.core.skill_center.services import (
    local_skill_upload_service as upload_module,
)
from agentclaw.community.core.skill_center.services.local_skill_upload_service import (
    LocalSkillUploadService,
)
from agentclaw.community.core.skill_center.services.skill_parser import SkillParser
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
        return row

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
    def check_collaborator_permission(self, *args):
        return {"has_permission": True}


class _Audit:
    def __init__(self):
        self.rows = []

    def insert(self, row):
        self.rows.append(row)


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

    def sync_runtime(self, *, desired_skills=None):
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
        return row


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
    def __init__(self, filesystem):
        super().__init__(filesystem)
        self.locator_calls: list[dict] = []

    def local_skill_package_storage_for_locator(self, *, locator, **kwargs):
        self.locator_calls.append({"locator": locator, **kwargs})
        return _Storage(self._filesystem, locator)


class _ReplacementRuntime:
    def __init__(self, results):
        self.results = iter(results)
        self.calls = 0

    def create(self, **_kwargs):
        return self

    def sync_runtime(self, *, desired_skills=None):
        self.calls += 1
        return next(self.results)

    async def project(self, **_kwargs):
        self.calls += 1
        if not next(self.results):
            raise RuntimeError("runtime reconcile failed")


class _DeviceResolver:
    def __init__(self, provider="local"):
        self.provider = provider

    def resolve_for_bot(self, _bot_id, _owner_id):
        return SimpleNamespace(provider=self.provider)


def _replacement_service(
    filesystem,
    repo,
    runtime,
    _unused_cleanup=None,
    guard=None,
    *,
    provider="local",
):
    return LocalSkillUploadService(
        repo,
        _Bot(),
        _Collaborators(),
        _ReplacementFactory(filesystem),
        _Audit(),
        guard or _Guard(),
        lambda: _DeviceResolver(provider),
        runtime,
        SkillParser(),
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
):
    return LocalSkillUploadService(
        repo or _Repo(),
        bot or _Bot(status),
        collaborators or _Collaborators(),
        factory or _Factory(filesystem),
        audit or _Audit(),
        guard or _Guard(),
        lambda: _DeviceResolver(provider),
        _RuntimeFactory(),
        SkillParser(),
    )


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
    assert audit.rows == [
        {
            "bot_id": "bot",
            "owner_id": "owner",
            "operator_id": "collaborator",
            "detail": '{"action": "local_skill_upload", "skill_id": "9"}',
        }
    ]


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
            "owner_id": "owner",
            "bot_id": "bot",
            "engine_type": "openclaw",
            "entity_type": "staff",
            "is_desktop": False,
            "is_teclaw": False,
            "name": "upload-skill",
        }
    ]
    assert repo.created[0]["user_id"] == "owner"


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


def test_zip_security_rejects_traversal_and_requires_skill_metadata():
    service = _service(_Filesystem())
    with pytest.raises(LocalSkillInvalidPackageError):
        service._unpack(_zip({"../SKILL.md": b"name: bad\ndescription: nope\n"}))
    with pytest.raises(LocalSkillInvalidPackageError):
        service._unpack(_zip({"SKILL.md": b"name: bad\n"}))
    with pytest.raises(LocalSkillInvalidPackageError):
        service._unpack(_zip({"SKILL.md": b"name: bad\ndescription: \xff\n"}))


def test_zip_accepts_root_skill_with_subdirectories_and_matching_wrapper():
    service = _service(_Filesystem())
    name, _, files = service._unpack(
        _zip(
            {
                "SKILL.md": _skill_md("root-skill"),
                "scripts/main.py": b"print('ok')",
            }
        )
    )
    assert name == "root-skill"
    assert [path for path, _ in files] == ["SKILL.md", "scripts/main.py"]
    name, _, files = service._unpack(
        _zip(
            {
                "wrapped/SKILL.md": _skill_md("wrapped"),
                "wrapped/a.txt": b"x",
            }
        )
    )
    assert name == "wrapped" and [path for path, _ in files] == ["SKILL.md", "a.txt"]


@pytest.mark.parametrize(
    ("metadata", "expected_description"),
    [
        (
            b"---\nname: upload-skill\ndescription: |\n  first line\n  second line\n---\n",
            "first line\nsecond line",
        ),
        (
            b"---\nname: upload-skill\ndescription: >\n  first line\n  second line\n---\n",
            "first line second line",
        ),
    ],
)
def test_zip_preserves_multiline_skill_description(metadata, expected_description):
    name, description, _ = _service(_Filesystem())._unpack(_zip({"SKILL.md": metadata}))

    assert name == "upload-skill"
    assert description == expected_description


@pytest.mark.parametrize(
    "path",
    [
        "/SKILL.md",
        "C:/SKILL.md",
        "\\\\server\\SKILL.md",
        "dir\\SKILL.md",
        "../SKILL.md",
    ],
)
def test_zip_rejects_absolute_windows_and_traversal_paths(path):
    with pytest.raises(LocalSkillInvalidPackageError):
        _service(_Filesystem())._unpack(_zip({path: b"name: bad\ndescription: no\n"}))


@pytest.mark.parametrize(
    "entries",
    [
        {},
        {
            "SKILL.md": b"name: one\ndescription: one\n",
            "a/SKILL.md": b"name: one\ndescription: two\n",
        },
        {"wrapped/SKILL.md": b"name: wrapped\ndescription: yes\n", "outside.txt": b"x"},
        {"SKILL.md": b"name: one\ndescription: yes\n", "a//b": b"x", "a/./b": b"y"},
    ],
)
def test_zip_rejects_missing_multiple_outside_wrapper_and_normalized_duplicates(
    entries,
):
    with pytest.raises(LocalSkillInvalidPackageError):
        _service(_Filesystem())._unpack(_zip(entries))


def test_zip_explains_when_multiple_skill_files_are_present():
    with pytest.raises(LocalSkillInvalidPackageError) as error:
        _service(_Filesystem())._unpack(
            _zip(
                {
                    "SKILL.md": b"name: one\ndescription: one\n",
                    "nested/SKILL.md": b"name: one\ndescription: two\n",
                }
            )
        )

    assert error.value.public_message == (
        "Skill package must contain exactly one SKILL.md file"
    )


def test_zip_explains_when_an_archive_entry_cannot_be_read(monkeypatch):
    class _UnreadableArchive:
        def infolist(self):
            return [zipfile.ZipInfo("SKILL.md")]

        def read(self, _info):
            raise OSError("injected archive read failure")

    monkeypatch.setattr(
        upload_module.zipfile, "ZipFile", lambda *_args: _UnreadableArchive()
    )

    with pytest.raises(LocalSkillInvalidPackageError) as error:
        _service(_Filesystem())._unpack(b"not-read")

    assert error.value.public_message == "Skill package could not be read"


def test_zip_explains_when_wrapper_directory_does_not_match_skill_name():
    with pytest.raises(LocalSkillInvalidPackageError) as error:
        _service(_Filesystem())._unpack(
            _zip(
                {
                    "wrong-directory/SKILL.md": (
                        b"name: actual-skill\ndescription: valid\n"
                    )
                }
            )
        )

    assert error.value.public_message == (
        "Skill directory name must match SKILL.md name"
    )


def test_zip_rejects_a_file_that_conflicts_with_its_wrapper_directory():
    with pytest.raises(LocalSkillInvalidPackageError) as error:
        _service(_Filesystem())._unpack(
            _zip(
                {
                    "wrapped/SKILL.md": b"name: wrapped\ndescription: valid\n",
                    "wrapped": b"not a directory",
                }
            )
        )

    assert error.value.public_message == (
        "Skill package files must be under one Skill directory"
    )


@pytest.mark.parametrize("name", ["skills-center", "skills-local", "skills-repo"])
def test_zip_rejects_reserved_content_store_names(name):
    with pytest.raises(LocalSkillInvalidPackageError):
        _service(_Filesystem())._unpack(_zip({"SKILL.md": _skill_md(name, "reserved")}))


@pytest.mark.parametrize("kind", [0o120000, 0o160000, 0o060000])
def test_zip_rejects_links_and_devices(kind):
    with pytest.raises(LocalSkillInvalidPackageError):
        _service(_Filesystem())._unpack(
            _zip(
                {"SKILL.md": b"name: bad\ndescription: no\n"},
                attrs={"SKILL.md": kind << 16},
            )
        )


def test_zip_enforces_documented_file_count_and_path_and_size_limits(monkeypatch):
    service = _service(_Filesystem())
    monkeypatch.setattr(upload_module, "_MAX_FILES", 1)
    with pytest.raises(upload_module.LocalSkillTooLargeError):
        service._unpack(_zip({"SKILL.md": _skill_md("many", "yes"), "x": b"x"}))
    monkeypatch.setattr(upload_module, "_MAX_FILES", 500)
    with pytest.raises(LocalSkillInvalidPackageError):
        service._unpack(_zip({"a" * 257: b"x", "SKILL.md": _skill_md("long", "yes")}))
    monkeypatch.setattr(upload_module, "_MAX_FILE", 2)
    with pytest.raises(upload_module.LocalSkillTooLargeError):
        service._unpack(_zip({"SKILL.md": _skill_md("big", "yes")}))
    monkeypatch.setattr(upload_module, "_MAX_FILE", 10 * 1024 * 1024)
    monkeypatch.setattr(upload_module, "_MAX_EXPANDED", 2)
    with pytest.raises(upload_module.LocalSkillTooLargeError):
        service._unpack(_zip({"SKILL.md": _skill_md("expanded", "yes")}))
    monkeypatch.setattr(upload_module, "_MAX_EXPANDED", 50 * 1024 * 1024)
    monkeypatch.setattr(upload_module, "_MAX_COMPRESSED", 1)
    with pytest.raises(upload_module.LocalSkillTooLargeError):
        service._unpack(_zip({"SKILL.md": _skill_md("compressed", "yes")}))


class _Denied:
    def check_collaborator_permission(self, *args):
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


class _FailAudit(_Audit):
    def insert(self, row):
        raise RuntimeError("audit failure")


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["write", "create", "audit"])
async def test_each_creation_failure_compensates_and_never_returns_success(stage):
    package = _zip({"SKILL.md": _skill_md()})
    filesystem = _Filesystem(fail=stage == "write")
    repo = _FailRepo() if stage == "create" else _Repo()
    audit = _FailAudit() if stage == "audit" else _Audit()
    service = _service(filesystem, repo=repo, audit=audit)
    with pytest.raises(LocalSkillStorageError):
        await service.upload_local_skill(
            bot_id="bot", owner_id="owner", actor_id="owner", package=package
        )
    assert filesystem.deleted == ["/private/skills-local/upload-skill"]


@pytest.mark.asyncio
async def test_failed_rollback_step_does_not_stop_package_cleanup():
    package = _zip({"SKILL.md": _skill_md()})
    repo = _Repo()
    repo.delete = lambda *args: (_ for _ in ()).throw(RuntimeError())
    service = _service(_Filesystem(), repo=repo, audit=_FailAudit())
    with pytest.raises(LocalSkillStorageError):
        await service.upload_local_skill(
            bot_id="bot", owner_id="owner", actor_id="owner", package=package
        )
    assert service._skill_service_factory._filesystem.deleted == [
        "/private/skills-local/upload-skill"
    ]


@pytest.mark.asyncio
async def test_failed_final_cleanup_leaves_no_database_authority_or_success():
    """A residual orphan is not retried into or exposed as a Local Skill."""
    package = _zip({"SKILL.md": _skill_md()})
    filesystem = _Filesystem(cleanup_results=[False, False])
    repo = _Repo()
    service = _service(filesystem, repo=repo, audit=_FailAudit())

    with pytest.raises(LocalSkillStorageError):
        await service.upload_local_skill(
            bot_id="bot", owner_id="owner", actor_id="owner", package=package
        )

    assert repo.created == []
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
    result = await _replacement_service(
        filesystem, repo, runtime
    ).upload_local_skill(
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

    with pytest.raises(LocalSkillStorageError):
        await _replacement_service(
            filesystem, repo, _ReplacementRuntime([True, True])
        ).upload_local_skill(
            bot_id="bot",
            owner_id="owner",
            actor_id="owner",
            package=_zip({"SKILL.md": _skill_md(description="new description")}),
        )

    assert old["git_path"] == f"local://{canonical}"
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
async def test_active_replacement_runtime_failure_restores_old_metadata_and_runtime_mapping():
    filesystem = _Filesystem()
    filesystem.files["/private/skills-local/upload-skill/SKILL.md"] = b"old"
    old = _existing_skill(active=True)
    repo = _ReplacementRepo([old])
    runtime = _ReplacementRuntime([False, True])
    with pytest.raises(LocalSkillRuntimeSyncError):
        await _replacement_service(filesystem, repo, runtime).upload_local_skill(
            bot_id="bot",
            owner_id="owner",
            actor_id="owner",
            package=_zip({"SKILL.md": _skill_md(description="new description")}),
        )
    assert old["git_path"] == "local:///private/skills-local/upload-skill"
    assert old["description"] == "old description"
    assert runtime.calls == 2
    assert filesystem.files["/private/skills-local/upload-skill/SKILL.md"] == b"old"


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


def test_directory_package_uses_the_same_wrapper_normalization_as_zip_upload():
    package = LocalSkillUploadService._pack_directory(
        [
            ("weather/SKILL.md", _skill_md(name="weather")),
            ("weather/scripts/fetch.py", b"print('weather')"),
        ]
    )

    name, description, files = LocalSkillUploadService._unpack(package)

    assert name == "weather"
    assert description == "useful"
    assert files == [
        ("SKILL.md", _skill_md(name="weather")),
        ("scripts/fetch.py", b"print('weather')"),
    ]


@pytest.mark.asyncio
async def test_directory_upload_reuses_the_raw_zip_upload_lifecycle():
    class _CapturingUploadService(LocalSkillUploadService):
        def __init__(self) -> None:
            self.call: dict[str, object] | None = None

        async def upload_local_skill(
            self,
            *,
            bot_id: str,
            owner_id: str,
            actor_id: str,
            package: bytes,
        ) -> dict[str, object]:
            self.call = {
                "bot_id": bot_id,
                "owner_id": owner_id,
                "actor_id": actor_id,
                "package": package,
            }
            return {"operation": "created"}

    service = _CapturingUploadService()
    result = await service.upload_local_skill_files(
        bot_id="bot",
        owner_id="owner",
        actor_id="collaborator",
        files=[
            ("weather/SKILL.md", _skill_md(name="weather")),
            ("weather/scripts/fetch.py", b"print('weather')"),
        ],
    )

    assert result == {"operation": "created"}
    assert service.call is not None
    assert service.call.keys() == {"bot_id", "owner_id", "actor_id", "package"}
    assert service.call["bot_id"] == "bot"
    assert service.call["owner_id"] == "owner"
    assert service.call["actor_id"] == "collaborator"
    name, description, files = LocalSkillUploadService._unpack(
        service.call["package"]  # type: ignore[arg-type]
    )
    assert name == "weather"
    assert description == "useful"
    assert files == [
        ("SKILL.md", _skill_md(name="weather")),
        ("scripts/fetch.py", b"print('weather')"),
    ]


def test_directory_package_rejects_path_traversal_before_it_can_be_archived():
    with pytest.raises(LocalSkillInvalidPackageError):
        LocalSkillUploadService._pack_directory(
            [("skill/../outside/SKILL.md", _skill_md())]
        )
