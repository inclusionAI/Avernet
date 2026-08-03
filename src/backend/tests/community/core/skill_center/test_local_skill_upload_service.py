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


def _zip(entries: dict[str, bytes]) -> bytes:
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w") as archive:
        for path, content in entries.items():
            archive.writestr(path, content)
    return payload.getvalue()


class _Repo:
    def __init__(self) -> None:
        self.created: list[dict] = []
        self.exclusions: list[tuple] = []

    def get_bot_local_by_name(self, **kwargs):
        return None

    def create(self, row):
        row = {**row, "id": "9", "gmt_created": None, "gmt_modified": None}
        self.created.append(row)
        return row

    def add_default_skill_exclusion(self, *args):
        self.exclusions.append(args)
        return True

    def remove_default_skill_exclusion(self, *args):
        self.exclusions.remove(args)
        return True

    def delete(self, skill_id):
        self.created.clear()
        return True


class _Sets:
    def get_default(self, **kwargs):
        return {"id": "4"}

    def add_skill_to_set(self, *args, **kwargs):
        return True

    def remove_skill_from_set(self, *args):
        return True


class _Bot:
    def __init__(self, status="ACTIVE"):
        self.status = status

    def get_by_id_and_owner(self, *_):
        return {"status": self.status, "active_engine": "moltis"}


class _Filesystem:
    def __init__(self, fail=False):
        self.files: dict[str, bytes] = {}
        self.deleted: list[str] = []
        self.fail = fail

    async def write_file(self, path, content):
        if self.fail:
            raise OSError("private path")
        self.files[path] = content

    async def delete_tree(self, path):
        self.deleted.append(path)
        return True


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

    async def cleanup(self):
        return await self.filesystem.delete_tree(self.directory)


class _Collaborators:
    def check_collaborator_permission(self, *args):
        return {"has_permission": True}


def _service(filesystem, *, status="ACTIVE"):
    return LocalSkillUploadService(_Repo(), _Sets(), _Bot(status), _Collaborators(), _Factory(filesystem))


@pytest.mark.asyncio
async def test_upload_keeps_bot_owner_when_collaborator_is_actor():
    filesystem = _Filesystem()
    service = _service(filesystem)
    result = await service.upload_local_skill(
        bot_id="bot", owner_id="owner", actor_id="collaborator",
        package=_zip({"SKILL.md": b"---\nname: upload-skill\ndescription: useful\n---\n"}),
    )
    assert result["operation"] == "created"
    assert result["skill"]["user_id"] == "owner"
    assert result["actor_id"] == "collaborator"
    assert filesystem.files["/private/skills-local/upload-skill/SKILL.md"]


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
