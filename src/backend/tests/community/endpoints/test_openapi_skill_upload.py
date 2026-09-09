"""Endpoint-framework coverage for the raw public Skill upload contract."""

from __future__ import annotations

import io
from pathlib import Path
import shutil
import tempfile
import time
import zipfile

import jwt

from agentclaw.community.adapters.http.openapi_v1.dependencies import PRINCIPAL_HEADER
from agentclaw.community.api.local_skill_upload_service import (
    LocalSkillUploadServiceProtocol,
)
from agentclaw.community.core.bot_collaborator.protocols import (
    CollaboratorServiceProtocol,
)
from agentclaw.community.core.repository.protocols.bot import (
    BotCollabLogRepositoryProtocol,
)
from agentclaw.community.core.repository.protocols.bot import BotRepository
from agentclaw.community.core.skill_center.factories import (
    LocalSkillPackageStorage,
    SkillServiceFactory,
)
from agentclaw.community.core.skill_center.services.local_skill_upload_service import (
    LocalSkillUploadService,
)
from agentclaw.community.core.skill_center.services.skill_parser import SkillParser
from agentclaw.community.core.skill_center.skill_package import SkillPackageValidator
from agentclaw.community.core.repository.protocols.skill_center import SkillRepository
from agentclaw.community.core.repository.protocols.skill_center import (
    SkillSetRepository,
)
from agentclaw.community.core.skills_pool.edit_guard import SkillsPoolEditGuard
from agentclaw.community.utils.avernet_tenant import avernet_tenant_scope
from agentclaw.community.utils.gateway_principal_config import (
    init_principal_verifier_config,
)
from tests.community.framework import (
    CaseInput,
    ExpectError,
    ExpectSuccess,
    endpoint_test,
)


_OWNER = "upload-owner"
_BOT_ID = "raw-upload-bot"
_TENANT = "raw-upload-tenant"
_KEY = "raw-upload-framework-signing-key-at-least-32-bytes"


class _Secret:
    secret_user = "test"
    secret_value = _KEY


class _Resolver:
    def get_secret(self, _secret_name: str) -> _Secret:
        return _Secret()


_RAW_UPLOAD_FILES = {
    "SKILL.md": b"name: raw-upload\ndescription: raw endpoint coverage\n",
    "docs/notes.txt": "plain text\n".encode(),
    "assets/icon.png": b"\x89PNG\r\n\x1a\n\xff\x00",
    "assets/nonutf8.bin": b"\xff\x00\x80\xfe",
    "assets/empty.txt": b"",
}
_FOLDER_UPLOAD_FILES = {
    "SKILL.md": b"name: folder-upload\ndescription: folder coverage\n",
    "docs/notes.txt": "plain text\n".encode(),
    "assets/icon.png": b"\x89PNG\r\n\x1a\n\xff\x00",
    "assets/nonutf8.bin": b"\xff\x00\x80\xfe",
    "assets/empty.txt": b"",
}


class _Filesystem:
    """Small Local-device fixture backed by a real temporary directory."""

    def __init__(self, root: Path) -> None:
        self._root = root

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

    async def write_file(self, path: str, content: bytes) -> None:
        target = self._path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)

    async def read_file(self, path: str) -> bytes | None:
        target = self._path(path)
        return target.read_bytes() if target.is_file() else None

    async def list_dir(self, path: str, *, recursive: bool = False):
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

    async def delete_tree(self, path: str) -> bool:
        directory = self._path(path)
        if directory.exists():
            shutil.rmtree(directory)
        return True

    async def exists(self, path: str) -> bool:
        return self._path(path).exists()


class _StorageFactory:
    def __init__(self) -> None:
        self._temporary_directory = tempfile.TemporaryDirectory()
        self._root = Path(self._temporary_directory.name)
        self._filesystem = _Filesystem(self._root)

    def local_skill_package_storage(
        self, *, name: str, directory_name: str | None = None, **_kwargs
    ) -> tuple[str, LocalSkillPackageStorage]:
        directory = f"test-local/{directory_name or name}"
        return directory, LocalSkillPackageStorage(self._filesystem, directory)

    def files_for(self, name: str) -> dict[str, bytes]:
        directory = self._root / "test-local" / name
        return {
            path.relative_to(directory).as_posix(): path.read_bytes()
            for path in directory.rglob("*")
            if path.is_file()
        }


class _RuntimeFactory:
    def create(self, **_kwargs):
        return self

    def project_skills(self):
        return True

    async def project(self, **_kwargs):
        return None


class _DeviceContextResolverStub:
    def resolve_for_bot(self, _bot_id, _owner_id):
        return type("DeviceContextStub", (), {"provider": "local"})()


class _Cleanup:
    def record_pending(self, **_kwargs):
        return True

    def list_pending(self, **_kwargs):
        return []

    def mark_cleaned(self, **_kwargs):
        return True

    def mark_failed(self, **_kwargs):
        return True


def _package() -> bytes:
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w") as archive:
        for path, content in _RAW_UPLOAD_FILES.items():
            archive.writestr(path, content)
    return payload.getvalue()


def _principal() -> str:
    """A caller in ``_TENANT`` — the tenant asserted by the ``app`` principal.

    A ``user`` principal carries no tenant (nothing in a user credential proves
    one), so a user-only token would scope to the internal default and this file
    would seed one tenant while the request read another.
    """
    now = int(time.time())
    return jwt.encode(
        {
            "iss": "gateway",
            "aud": "backend",
            "iat": now,
            "exp": now + 3600,
            "principals": [
                {
                    "type": "user",
                    "subject": {"id": _OWNER, "username": "upload@example.test"},
                },
                {
                    "type": "app",
                    "tenant": _TENANT,
                    "app": {
                        "app_id": 1,
                        "app_name": "Partner App",
                        "owners": "partner-org",
                        "tenant": _TENANT,
                    },
                },
            ],
        },
        _KEY,
        algorithm="HS256",
    )


_HEADERS = {"content-type": "application/zip", PRINCIPAL_HEADER: _principal()}


def _seed_uploadable_bot(world) -> None:
    """Seed the same non-default tenant that the verified request selects."""
    init_principal_verifier_config(_Resolver(), "test-key", strict=False)
    storage_factory = _StorageFactory()
    world.injector.binder.bind(SkillServiceFactory, to=storage_factory, scope=None)
    world.injector.binder.bind(_StorageFactory, to=storage_factory, scope=None)
    with avernet_tenant_scope(_TENANT):
        world.get(BotRepository).insert(
            {
                "bot_id": _BOT_ID,
                "bot_name": "Raw upload Bot",
                "owner_id": _OWNER,
                "owner_name": _OWNER,
                "entity_id": _OWNER,
                "entity_type": "staff",
                "creator_id": _OWNER,
                "status": "ACTIVE",
                "active_engine": "openclaw",
            }
        )
        world.get(SkillSetRepository).create(
            {
                "name": "Other Bot Default",
                "description": "Must not receive this upload",
                "user_id": _OWNER,
                "bolt_id": "other-bot",
                "is_default": True,
                "is_builtin": False,
                "is_active": False,
                "engine_type": "openclaw",
            }
        )
        world.get(SkillSetRepository).create(
            {
                "name": "Default",
                "description": "Default Skill Set",
                "user_id": _OWNER,
                "bolt_id": _BOT_ID,
                "is_default": True,
                "is_builtin": False,
                "is_active": False,
                "engine_type": "openclaw",
            }
        )
    # Bind the real Core service after the test's storage port is selected.
    # This avoids creating an engine filesystem merely to prove the raw HTTP
    # contract; all persistence repositories remain the production adapters.
    world.injector.binder.bind(
        LocalSkillUploadServiceProtocol,
        to=LocalSkillUploadService(
            world.get(SkillRepository),
            world.get(BotRepository),
            world.get(CollaboratorServiceProtocol),
            storage_factory,
            world.get(BotCollabLogRepositoryProtocol),
            world.get(SkillsPoolEditGuard),
            lambda: _DeviceContextResolverStub(),
            _RuntimeFactory(),
            SkillPackageValidator(SkillParser()),
        ),
        scope=None,
    )


def _assert_created_inactive_without_default_membership(response, world) -> None:
    skill_id = response.json()["data"]["skill"]["skill_id"]
    with avernet_tenant_scope(_TENANT):
        skill = world.get(SkillRepository).get_bot_local_skill(
            skill_id=skill_id, bot_id=_BOT_ID, user_id=_OWNER
        )
        assert skill is not None and skill["active"] is False


def _assert_raw_zip_bytes_reach_the_local_filesystem(response, world) -> None:
    _assert_created_inactive_without_default_membership(response, world)
    assert world.get(_StorageFactory).files_for("raw-upload") == _RAW_UPLOAD_FILES


def _assert_folder_bytes_reach_the_local_filesystem(response, world) -> None:
    _assert_created_inactive_without_default_membership(response, world)
    assert world.get(_StorageFactory).files_for("folder-upload") == _FOLDER_UPLOAD_FILES


@endpoint_test(
    method="POST",
    path="/openapi/v1/bots/{bot_id}/skills",
    scenario="raw_zip_created_in_verified_tenant",
    input=CaseInput(
        path_params={"bot_id": _BOT_ID},
        query_params={"user_id": _OWNER},
        headers=_HEADERS,
        raw_body=_package(),
    ),
    seed=_seed_uploadable_bot,
    extra_assertions=(_assert_raw_zip_bytes_reach_the_local_filesystem,),
    expect=ExpectSuccess(
        status=201,
        json_contains={
            "code": 201000,
            "data": {
                "operation": "created",
                "skill": {"name": "raw-upload", "active": False},
            },
        },
    ),
)
def raw_zip_upload_creates_an_inactive_skill():
    """A verified raw ``application/zip`` request is a real happy path."""


@endpoint_test(
    method="POST",
    path="/openapi/v1/bots/{bot_id}/skills",
    scenario="multipart_rejected_after_verified_tenant_guard",
    input=CaseInput(
        path_params={"bot_id": _BOT_ID},
        query_params={"user_id": _OWNER},
        headers={**_HEADERS, "content-type": "multipart/form-data; boundary=x"},
        raw_body=b"--x--",
    ),
    seed=_seed_uploadable_bot,
    expect=ExpectError(status=400),
)
def multipart_upload_is_an_explicit_error_case():
    """The public endpoint accepts only raw ``application/zip`` bodies."""


@endpoint_test(
    method="POST",
    path="/openapi/v1/bots/{bot_id}/skills/upload-folder",
    scenario="directory_created_in_verified_tenant",
    input=CaseInput(
        path_params={"bot_id": _BOT_ID},
        query_params={"user_id": _OWNER},
        headers={PRINCIPAL_HEADER: _principal()},
        form_data={
            "file_paths": '["SKILL.md", "docs/notes.txt", "assets/icon.png", "assets/nonutf8.bin", "assets/empty.txt"]'
        },
        files=[
            ("files", (path, content)) for path, content in _FOLDER_UPLOAD_FILES.items()
        ],
    ),
    seed=_seed_uploadable_bot,
    extra_assertions=(_assert_folder_bytes_reach_the_local_filesystem,),
    expect=ExpectSuccess(
        status=201,
        json_contains={
            "code": 201000,
            "data": {
                "operation": "created",
                "skill": {"name": "folder-upload", "active": False},
            },
        },
    ),
)
def folder_upload_creates_an_inactive_skill():
    """A multipart directory upload follows the same inactive Local contract."""


@endpoint_test(
    method="POST",
    path="/openapi/v1/bots/{bot_id}/skills/upload-folder",
    scenario="directory_rejects_misaligned_relative_paths",
    input=CaseInput(
        path_params={"bot_id": _BOT_ID},
        query_params={"user_id": _OWNER},
        headers={PRINCIPAL_HEADER: _principal()},
        form_data={"file_paths": '["SKILL.md", "extra.py"]'},
        files=[("files", ("SKILL.md", b"name: folder-upload\n"))],
    ),
    seed=_seed_uploadable_bot,
    expect=ExpectError(status=400),
)
def folder_upload_rejects_misaligned_relative_paths():
    """The declared paths must remain one-to-one with uploaded files."""


# The retiring address. `POST /openapi/v1/bots/skills/upload?bot_id=` is what
# clients call today, and it is not a re-registration: the shim publishes
# `bot_id` in the query and `owner_entity_id` where the current address
# publishes `owner_id`, then translates. That translation is the code most
# likely to be wrong in `openapi_v1/deprecated/skills.py`, so it is driven here
# rather than only asserted structurally by `test_legacy_parity.py`.


@endpoint_test(
    method="POST",
    path="/openapi/v1/bots/skills/upload",
    scenario="legacy_address_creates_the_same_skill",
    input=CaseInput(
        query_params={"bot_id": _BOT_ID, "user_id": _OWNER},
        headers=_HEADERS,
        raw_body=_package(),
    ),
    seed=_seed_uploadable_bot,
    extra_assertions=(_assert_created_inactive_without_default_membership,),
    expect=ExpectSuccess(
        status=201,
        json_contains={
            "code": 201000,
            "data": {
                "operation": "created",
                "skill": {"name": "raw-upload", "active": False},
            },
        },
    ),
)
def legacy_raw_zip_upload_creates_an_inactive_skill():
    """A bot named in the query reaches the same handler as one named in the path."""


@endpoint_test(
    method="POST",
    path="/openapi/v1/bots/skills/upload",
    scenario="legacy_address_rejects_multipart_identically",
    input=CaseInput(
        query_params={"bot_id": _BOT_ID, "user_id": _OWNER},
        headers={**_HEADERS, "content-type": "multipart/form-data; boundary=x"},
        raw_body=b"--x--",
    ),
    seed=_seed_uploadable_bot,
    expect=ExpectError(status=400),
)
def legacy_multipart_upload_is_refused_as_it_always_was():
    """The retiring address rejects what it always rejected."""
