"""Core fault-injection seam for first Local Skill ZIP uploads."""

from __future__ import annotations

import io
import zipfile

import pytest

from agentclaw.community.core.skill_center.errors import (
    LocalSkillInvalidPackageError,
    LocalSkillNotReadyError,
    LocalSkillStorageError,
)
from agentclaw.community.core.skill_center.services.local_skill_upload_service import (
    LocalSkillUploadService,
)
from agentclaw.community.core.skill_center.factories import LocalSkillPackageStorage
from agentclaw.community.core.skill_center.services import local_skill_upload_service as upload_module


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

    def create(self, row):
        row = {**row, "id": "9", "gmt_created": None, "gmt_modified": None}
        self.created.append(row)
        return row

    def delete(self, skill_id):
        self.created.clear()
        return True


class _Sets:
    def __init__(self, fail_at=None):
        self.default_args = None
        self.fail_at = fail_at
        self.associations: list[tuple] = []
        self.exclusions: list[tuple] = []
    def get_default(self, **kwargs):
        self.default_args = kwargs
        return {"id": "4"}

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
    def __init__(self, status="ACTIVE"):
        self.status = status

    def get_by_id_and_owner(self, *_):
        return {"status": self.status, "active_engine": "moltis"}


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
            self.files.clear()
        return result

    async def exists(self, path):
        return any(file_path.startswith(f"{path}/") for file_path in self.files)


class _Factory:
    def __init__(self, filesystem):
        self.local_dir = __import__("pathlib").Path("/private/skills-local")
        self._filesystem = filesystem

    def create(self, **kwargs):
        return self

    def local_skill_package_storage(self, *, owner_id, bot_id, engine_type, name):
        return str(self.local_dir / name), _Storage(self._filesystem, str(self.local_dir / name))

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
    def __init__(self): self.rows = []
    def insert(self, row): self.rows.append(row)


def _service(filesystem, *, status="ACTIVE", collaborators=None, repo=None, sets=None, audit=None):
    return LocalSkillUploadService(repo or _Repo(), sets or _Sets(), _Bot(status), collaborators or _Collaborators(), _Factory(filesystem), audit or _Audit())


@pytest.mark.asyncio
async def test_upload_keeps_bot_owner_when_collaborator_is_actor():
    filesystem = _Filesystem()
    audit = _Audit()
    sets = _Sets()
    service = _service(filesystem, audit=audit, sets=sets)
    result = await service.upload_local_skill(
        bot_id="bot", owner_id="owner", actor_id="collaborator",
        package=_zip({"SKILL.md": b"---\nname: upload-skill\ndescription: useful\n---\n"}),
    )
    assert result["operation"] == "created"
    assert result["skill"]["user_id"] == "owner"
    assert result["actor_id"] == "collaborator"
    assert filesystem.files["/private/skills-local/upload-skill/SKILL.md"]
    assert audit.rows == [{"bot_id": "bot", "owner_id": "owner", "operator_id": "collaborator", "detail": '{"action": "local_skill_upload", "skill_id": "9"}'}]
    assert sets.default_args == {"user_id": "owner", "bolt_id": "bot", "engine_type": "moltis"}


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
        await service.upload_local_skill(bot_id="bot", owner_id="owner", actor_id="owner", package=package)
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
        _zip({
            "SKILL.md": b"name: root-skill\ndescription: useful\n",
            "scripts/main.py": b"print('ok')",
        })
    )
    assert name == "root-skill"
    assert [path for path, _ in files] == ["SKILL.md", "scripts/main.py"]
    name, _, files = service._unpack(
        _zip({"wrapped/SKILL.md": b"name: wrapped\ndescription: useful\n", "wrapped/a.txt": b"x"})
    )
    assert name == "wrapped" and [path for path, _ in files] == ["SKILL.md", "a.txt"]


@pytest.mark.parametrize("path", ["/SKILL.md", "C:/SKILL.md", "\\\\server\\SKILL.md", "dir\\SKILL.md", "../SKILL.md"])
def test_zip_rejects_absolute_windows_and_traversal_paths(path):
    with pytest.raises(LocalSkillInvalidPackageError):
        _service(_Filesystem())._unpack(_zip({path: b"name: bad\ndescription: no\n"}))


@pytest.mark.parametrize("entries", [
    {},
    {"SKILL.md": b"name: one\ndescription: one\n", "a/SKILL.md": b"name: one\ndescription: two\n"},
    {"wrapped/SKILL.md": b"name: wrapped\ndescription: yes\n", "outside.txt": b"x"},
    {"SKILL.md": b"name: one\ndescription: yes\n", "a//b": b"x", "a/./b": b"y"},
])
def test_zip_rejects_missing_multiple_outside_wrapper_and_normalized_duplicates(entries):
    with pytest.raises(LocalSkillInvalidPackageError):
        _service(_Filesystem())._unpack(_zip(entries))


@pytest.mark.parametrize("kind", [0o120000, 0o160000, 0o060000])
def test_zip_rejects_links_and_devices(kind):
    with pytest.raises(LocalSkillInvalidPackageError):
        _service(_Filesystem())._unpack(_zip(
            {"SKILL.md": b"name: bad\ndescription: no\n"},
            attrs={"SKILL.md": kind << 16},
        ))


def test_zip_enforces_documented_file_count_and_path_and_size_limits(monkeypatch):
    service = _service(_Filesystem())
    monkeypatch.setattr(upload_module, "_MAX_FILES", 1)
    with pytest.raises(upload_module.LocalSkillTooLargeError):
        service._unpack(_zip({"SKILL.md": b"name: many\ndescription: yes\n", "x": b"x"}))
    monkeypatch.setattr(upload_module, "_MAX_FILES", 500)
    with pytest.raises(LocalSkillInvalidPackageError):
        service._unpack(_zip({"a" * 257: b"x", "SKILL.md": b"name: long\ndescription: yes\n"}))
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
    def check_collaborator_permission(self, *args): return {"has_permission": False}


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
@pytest.mark.parametrize("stage", ["write", "create", "association", "exclusion", "audit"])
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
    sets.remove_default_skill_exclusion = lambda *args: (_ for _ in ()).throw(RuntimeError())
    service = _service(_Filesystem(), repo=repo, sets=sets, audit=_FailAudit())
    with pytest.raises(LocalSkillStorageError):
        await service.upload_local_skill(
            bot_id="bot", owner_id="owner", actor_id="owner", package=package
        )
    assert service._skill_service_factory._filesystem.deleted == ["/private/skills-local/upload-skill"]


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
