"""Core fault-injection seam for first Local Skill ZIP uploads."""

from __future__ import annotations

import asyncio
import io
import zipfile

import pytest

from agentclaw.community.core.skill_center.errors import (
    LocalSkillInvalidPackageError,
    LocalSkillNotReadyError,
    LocalSkillRuntimeSyncError,
    LocalSkillStorageError,
)
from agentclaw.community.core.skill_center.services.local_skill_upload_service import (
    LocalSkillUploadService,
)
from agentclaw.community.core.skill_center.factories import LocalSkillPackageStorage
from agentclaw.community.core.skill_center.services import (
    local_skill_upload_service as upload_module,
)


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


class _Sets:
    def __init__(self, fail_at=None, default_exists=True):
        self.default_args = None
        self.fail_at = fail_at
        self.default_exists = default_exists
        self.created_sets: list[dict] = []
        self.associations: list[tuple] = []
        self.exclusions: list[tuple] = []

    def get_default(self, **kwargs):
        self.default_args = kwargs
        if not self.default_exists and not self.created_sets:
            return None
        return {"id": "4", **(self.created_sets[-1] if self.created_sets else {})}

    def create(self, row):
        created = {**row, "id": "4"}
        self.created_sets.append(created)
        return created

    def add_skill_to_set(self, *args, **kwargs):
        if self.fail_at == "association":
            raise RuntimeError("association")
        self.associations.append(args)
        return True

    def remove_skill_from_set(self, *args):
        self.associations.remove(args)
        return True

    def add_default_skill_exclusion(self, *args):
        if self.fail_at == "exclusion":
            return False
        self.exclusions.append(args)
        return True

    def remove_default_skill_exclusion(self, *args):
        self.exclusions.remove(args)
        return True


class _Bot:
    def __init__(self, status="ACTIVE", entity_id="owner"):
        self.status = status
        self.entity_id = entity_id

    def get_by_id_and_owner(self, *_):
        return {
            "status": self.status,
            "active_engine": "moltis",
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


@pytest.mark.asyncio
async def test_package_storage_prepare_accepts_an_absent_first_upload_directory():
    filesystem = _Filesystem()
    await LocalSkillPackageStorage(
        filesystem, "/private/skills-local/upload-skill"
    ).prepare()
    assert filesystem.deleted == []


class _Collaborators:
    def check_collaborator_permission(self, *args):
        return {"has_permission": True}


class _Audit:
    def __init__(self):
        self.rows = []

    def insert(self, row):
        self.rows.append(row)


class _Guard:
    def __init__(self):
        self._lock = asyncio.Lock()

    async def acquire_for_edit_wait(self, **_kwargs):
        await self._lock.acquire()
        return object()

    def release(self, _lease):
        self._lock.release()
        return True


class _Cleanup:
    def __init__(self):
        self.rows = []

    def record_pending(self, **kwargs):
        self.rows.append(kwargs)
        return True


class _RuntimeFactory:
    def create(self, **kwargs):
        return self

    def sync_runtime(self):
        return True


class _ReplacementRepo(_Repo):
    def __init__(self, rows):
        super().__init__()
        self.rows = rows
        self.updates = []

    def list_bot_local_by_name(self, **_kwargs):
        return self.rows

    def update(self, skill_id, values):
        self.updates.append((skill_id, values))
        row = next(row for row in self.rows if row["id"] == skill_id)
        row.update(values)
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
    def local_skill_package_storage(self, *, name, directory_name=None, **_kwargs):
        directory = str(self.local_dir / (directory_name or name))
        return directory, _Storage(self._filesystem, directory)

    def local_skill_package_storage_for_locator(self, *, locator, **_kwargs):
        return _Storage(self._filesystem, locator)


class _ReplacementRuntime:
    def __init__(self, results):
        self.results = iter(results)
        self.calls = 0

    def create(self, **_kwargs):
        return self

    def sync_runtime(self):
        self.calls += 1
        return next(self.results)


def _replacement_service(filesystem, repo, runtime, cleanup=None, guard=None):
    return LocalSkillUploadService(
        repo,
        _Sets(),
        _Bot(),
        _Collaborators(),
        _ReplacementFactory(filesystem),
        runtime,
        _Audit(),
        guard or _Guard(),
        cleanup or _Cleanup(),
    )


def _service(
    filesystem,
    *,
    status="ACTIVE",
    collaborators=None,
    repo=None,
    sets=None,
    audit=None,
    bot=None,
    factory=None,
):
    return LocalSkillUploadService(
        repo or _Repo(),
        sets or _Sets(),
        bot or _Bot(status),
        collaborators or _Collaborators(),
        factory or _Factory(filesystem),
        _RuntimeFactory(),
        audit or _Audit(),
        _Guard(),
        _Cleanup(),
    )


@pytest.mark.asyncio
async def test_upload_keeps_bot_owner_when_collaborator_is_actor():
    filesystem = _Filesystem()
    audit = _Audit()
    sets = _Sets()
    service = _service(filesystem, audit=audit, sets=sets)
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
    assert sets.default_args == {
        "user_id": "owner",
        "bolt_id": "bot",
        "engine_type": "moltis",
    }


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
            "engine_type": "moltis",
            "entity_type": "staff",
            "is_desktop": False,
            "is_teclaw": False,
            "name": "upload-skill",
        }
    ]
    assert repo.created[0]["user_id"] == "owner"


@pytest.mark.asyncio
async def test_upload_creates_missing_bot_default_set_before_association():
    filesystem = _Filesystem()
    sets = _Sets(default_exists=False)
    service = _service(filesystem, sets=sets)

    result = await service.upload_local_skill(
        bot_id="bot",
        owner_id="owner",
        actor_id="owner",
        package=_zip({"SKILL.md": b"name: upload-skill\ndescription: useful\n"}),
    )

    assert result["operation"] == "created"
    assert sets.default_args == {
        "user_id": "owner",
        "bolt_id": "bot",
        "engine_type": "moltis",
    }
    assert sets.created_sets == [
        {
            "id": "4",
            "name": "默认技能集",
            "description": "系统默认技能集，用户可以根据需要添加或移除技能",
            "user_id": "owner",
            "bolt_id": "bot",
            "is_default": True,
            "is_builtin": False,
            "is_active": False,
            "engine_type": "moltis",
        }
    ]
    assert sets.associations == [("4", "9")]


@pytest.mark.asyncio
async def test_not_ready_and_storage_failure_leave_no_public_skill():
    package = _zip({"SKILL.md": b"name: upload-skill\ndescription: useful\n"})
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
                "SKILL.md": b"name: root-skill\ndescription: useful\n",
                "scripts/main.py": b"print('ok')",
            }
        )
    )
    assert name == "root-skill"
    assert [path for path, _ in files] == ["SKILL.md", "scripts/main.py"]
    name, _, files = service._unpack(
        _zip(
            {
                "wrapped/SKILL.md": b"name: wrapped\ndescription: useful\n",
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


@pytest.mark.parametrize("name", ["skills-center", "skills-local", "skills-repo"])
def test_zip_rejects_reserved_content_store_names(name):
    with pytest.raises(LocalSkillInvalidPackageError):
        _service(_Filesystem())._unpack(
            _zip({"SKILL.md": f"name: {name}\ndescription: reserved\n".encode()})
        )


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
        service._unpack(
            _zip({"SKILL.md": b"name: many\ndescription: yes\n", "x": b"x"})
        )
    monkeypatch.setattr(upload_module, "_MAX_FILES", 500)
    with pytest.raises(LocalSkillInvalidPackageError):
        service._unpack(
            _zip({"a" * 257: b"x", "SKILL.md": b"name: long\ndescription: yes\n"})
        )
    monkeypatch.setattr(upload_module, "_MAX_FILE", 2)
    with pytest.raises(upload_module.LocalSkillTooLargeError):
        service._unpack(_zip({"SKILL.md": b"name: big\ndescription: yes\n"}))
    monkeypatch.setattr(upload_module, "_MAX_FILE", 10 * 1024 * 1024)
    monkeypatch.setattr(upload_module, "_MAX_EXPANDED", 2)
    with pytest.raises(upload_module.LocalSkillTooLargeError):
        service._unpack(_zip({"SKILL.md": b"name: expanded\ndescription: yes\n"}))
    monkeypatch.setattr(upload_module, "_MAX_EXPANDED", 50 * 1024 * 1024)
    monkeypatch.setattr(upload_module, "_MAX_COMPRESSED", 1)
    with pytest.raises(upload_module.LocalSkillTooLargeError):
        service._unpack(_zip({"SKILL.md": b"name: compressed\ndescription: yes\n"}))


class _Denied:
    def check_collaborator_permission(self, *args):
        return {"has_permission": False}


@pytest.mark.asyncio
async def test_owner_locator_and_denied_collaborator_cannot_forge_access():
    from agentclaw.community.core.skill_center.errors import LocalSkillNotFoundError

    package = _zip({"SKILL.md": b"name: upload-skill\ndescription: useful\n"})
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
@pytest.mark.parametrize(
    "stage", ["write", "create", "association", "exclusion", "audit"]
)
async def test_each_creation_failure_compensates_and_never_returns_success(stage):
    package = _zip({"SKILL.md": b"name: upload-skill\ndescription: useful\n"})
    filesystem = _Filesystem(fail=stage == "write")
    repo = _FailRepo() if stage == "create" else _Repo()
    sets = _Sets(fail_at=stage)
    audit = _FailAudit() if stage == "audit" else _Audit()
    service = _service(filesystem, repo=repo, sets=sets, audit=audit)
    with pytest.raises(LocalSkillStorageError):
        await service.upload_local_skill(
            bot_id="bot", owner_id="owner", actor_id="owner", package=package
        )
    assert filesystem.deleted == ["/private/skills-local/upload-skill"]


@pytest.mark.asyncio
async def test_failed_rollback_step_does_not_stop_package_cleanup():
    package = _zip({"SKILL.md": b"name: upload-skill\ndescription: useful\n"})
    repo = _Repo()
    sets = _Sets()
    sets.remove_default_skill_exclusion = lambda *args: (_ for _ in ()).throw(
        RuntimeError()
    )
    service = _service(_Filesystem(), repo=repo, sets=sets, audit=_FailAudit())
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
    package = _zip({"SKILL.md": b"name: upload-skill\ndescription: useful\n"})
    filesystem = _Filesystem(cleanup_results=[False, False])
    repo = _Repo()
    sets = _Sets()
    service = _service(filesystem, repo=repo, sets=sets, audit=_FailAudit())

    with pytest.raises(LocalSkillStorageError):
        await service.upload_local_skill(
            bot_id="bot", owner_id="owner", actor_id="owner", package=package
        )

    assert repo.created == []
    assert sets.associations == []
    assert sets.exclusions == []
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
            package=_zip({"SKILL.md": b"name: upload-skill\ndescription: useful\n"}),
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
    result = await _replacement_service(
        filesystem, repo, _ReplacementRuntime([True])
    ).upload_local_skill(
        bot_id="bot",
        owner_id="owner",
        actor_id="collaborator",
        package=_zip(
            {"SKILL.md": b"name: upload-skill\ndescription: new description\n"}
        ),
    )
    assert result["operation"] == "updated"
    assert result["skill"]["id"] == "9"
    assert result["skill"]["user_id"] == "owner"
    assert result["skill"]["active"] is False
    assert result["skill"]["git_path"] != "local:///private/skills-local/upload-skill"
    assert "/private/skills-local/upload-skill" in filesystem.deleted
    assert any("replacement-" in path for path in filesystem.files)


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
            package=_zip(
                {"SKILL.md": b"name: upload-skill\ndescription: new description\n"}
            ),
        )
    assert old["git_path"] == "local:///private/skills-local/upload-skill"
    assert old["description"] == "old description"
    assert runtime.calls == 2
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
            package=_zip(
                {"SKILL.md": b"name: upload-skill\ndescription: new description\n"}
            ),
        )
    assert filesystem.files == {}
    assert repo.updates == []


@pytest.mark.asyncio
async def test_post_switch_obsolete_cleanup_failure_is_recorded_without_undoing_update():
    filesystem = _Filesystem(cleanup_results=[False])
    repo = _ReplacementRepo([_existing_skill(active=False)])
    cleanup = _Cleanup()
    result = await _replacement_service(
        filesystem, repo, _ReplacementRuntime([True]), cleanup
    ).upload_local_skill(
        bot_id="bot",
        owner_id="owner",
        actor_id="owner",
        package=_zip(
            {"SKILL.md": b"name: upload-skill\ndescription: new description\n"}
        ),
    )
    assert result["operation"] == "updated"
    assert cleanup.rows == [
        {
            "env": "test",
            "owner_id": "owner",
            "bot_id": "bot",
            "skill_id": "9",
            "package_locator": "/private/skills-local/upload-skill",
        }
    ]


class _ConcurrentGuard:
    def __init__(self):
        self.lock = asyncio.Lock()

    async def acquire_for_edit_wait(self, **_kwargs):
        await self.lock.acquire()
        return object()

    def release(self, _lease):
        self.lock.release()
        return True


class _YieldingFilesystem(_Filesystem):
    async def write_file(self, path, content):
        await asyncio.sleep(0)
        await super().write_file(path, content)


@pytest.mark.asyncio
async def test_concurrent_same_name_uploads_serialize_then_converge_on_one_skill():
    repo = _ConcurrentRepo()
    filesystem = _YieldingFilesystem()
    guard = _ConcurrentGuard()
    package = _zip({"SKILL.md": b"name: upload-skill\ndescription: concurrent\n"})
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
