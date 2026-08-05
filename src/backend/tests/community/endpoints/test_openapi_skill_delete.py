"""Endpoint-framework coverage for recoverable public Local Skill deletion."""

from __future__ import annotations

import time

import jwt

from agentclaw.community.adapters.http.openapi_v1.dependencies import PRINCIPAL_HEADER
from agentclaw.community.api.local_skill_delete_service import (
    LocalSkillDeleteServiceProtocol,
)
from agentclaw.community.core.bot_collaborator.protocols import (
    CollaboratorServiceProtocol,
)
from agentclaw.community.core.bot_management.repository.protocol import BotRepository
from agentclaw.community.core.skill_center.factories import LocalSkillPackageStorage
from agentclaw.community.core.skill_center.services.local_skill_delete_service import (
    LocalSkillDeleteService,
)
from agentclaw.community.core.skill_center.services.repositories import (
    SkillRepository,
    SkillSetRepository,
)
from agentclaw.community.plugin_api.local_skill_cleanup import LocalSkillCleanupRepository
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


_OWNER = "delete-owner"
_BOT_ID = "delete-bot"
_TENANT = "delete-tenant"
_KEY = "delete-framework-signing-key-at-least-32-bytes"


class _Secret:
    secret_user = "test"
    secret_value = _KEY


class _Resolver:
    def get_secret(self, _secret_name: str) -> _Secret:
        return _Secret()


class _Files:
    def __init__(self) -> None:
        self.files = {"/skills/delete/SKILL.md": b"name: delete\ndescription: Delete\n"}

    async def exists(self, path: str) -> bool:
        return any(file_path.startswith(f"{path}/") for file_path in self.files)

    async def list_dir(self, path: str, *, recursive: bool = False):
        rows = [
            {"relative_path": file_path.removeprefix(f"{path}/"), "is_dir": False}
            for file_path in self.files
            if file_path.startswith(f"{path}/")
        ]
        return rows or None

    async def read_file(self, path: str):
        return self.files.get(path)

    async def write_file(self, path: str, content: bytes) -> None:
        self.files[path] = content

    async def delete_tree(self, path: str) -> bool:
        self.files = {
            file_path: content
            for file_path, content in self.files.items()
            if not file_path.startswith(f"{path}/")
        }
        return True


class _StorageFactory:
    def __init__(self) -> None:
        self.files = _Files()

    def local_skill_package_storage_for_locator(self, *, locator: str, **_kwargs):
        return LocalSkillPackageStorage(self.files, locator)

    def local_skill_package_storage(self, *, directory_name: str, **_kwargs):
        locator = f"/skills/{directory_name}"
        return locator, LocalSkillPackageStorage(self.files, locator)


class _Guard:
    async def acquire_for_edit_wait(self, **_kwargs):
        return object()

    def release(self, _lease) -> bool:
        return True


def _principal() -> str:
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
                    "tenant": _TENANT,
                    "subject": {"id": _OWNER, "username": "delete@example.test"},
                }
            ],
        },
        _KEY,
        algorithm="HS256",
    )


_HEADERS = {PRINCIPAL_HEADER: _principal()}


def _seed_delete(world, *, active: bool) -> None:
    init_principal_verifier_config(_Resolver(), "test-key", strict=False)
    storage_factory = _StorageFactory()
    with avernet_tenant_scope(_TENANT):
        world.get(BotRepository).insert(
            {
                "bot_id": _BOT_ID,
                "bot_name": "Delete Bot",
                "owner_id": _OWNER,
                "owner_name": _OWNER,
                "entity_id": _OWNER,
                "entity_type": "staff",
                "creator_id": _OWNER,
                "status": "ACTIVE",
                "active_engine": "openclaw",
            }
        )
        default_set = world.get(SkillSetRepository).create(
            {
                "name": "Default",
                "user_id": _OWNER,
                "bolt_id": _BOT_ID,
                "is_default": True,
                "is_builtin": False,
                "is_active": False,
                "engine_type": "openclaw",
            }
        )
        skill = world.get(SkillRepository).create(
            {
                "name": "delete",
                "description": "Endpoint deletion",
                "git_path": "local:///skills/delete",
                "user_id": _OWNER,
                "bolt_id": _BOT_ID,
                "source_type": "upload",
            }
        )
        world.get(SkillSetRepository).add_skill_to_set(
            default_set["id"], skill["id"], user_id=_OWNER
        )
        if not active:
            world.get(SkillSetRepository).add_default_skill_exclusion(
                _OWNER, _BOT_ID, int(default_set["id"]), int(skill["id"])
            )
    world.injector.binder.bind(
        LocalSkillDeleteServiceProtocol,
        to=LocalSkillDeleteService(
            world.get(SkillRepository),
            world.get(SkillSetRepository),
            world.get(BotRepository),
            world.get(CollaboratorServiceProtocol),
            storage_factory,
            _Guard(),
            world.get(LocalSkillCleanupRepository),
        ),
        scope=None,
    )


def _seed_inactive_delete(world) -> None:
    _seed_delete(world, active=False)


def _seed_active_delete(world) -> None:
    _seed_delete(world, active=True)


def _seed_inactive_skill_in_active_custom_set(world) -> None:
    _seed_delete(world, active=False)
    with avernet_tenant_scope(_TENANT):
        skill = world.get(SkillRepository).get_bot_local_skill(
            skill_id="1", bot_id=_BOT_ID, user_id=_OWNER
        )
        assert skill is not None
        active_set = world.get(SkillSetRepository).create(
            {
                "name": "Active custom",
                "user_id": _OWNER,
                "bolt_id": _BOT_ID,
                "is_default": False,
                "is_builtin": False,
                "is_active": True,
                "engine_type": "openclaw",
            }
        )
        world.get(SkillSetRepository).add_skill_to_set(
            active_set["id"], skill["id"], user_id=_OWNER
        )


@endpoint_test(
    method="DELETE",
    path="/openapi/v1/bots/skills/{skill_id}",
    scenario="deletes_exact_inactive_local_skill",
    input=CaseInput(path_params={"skill_id": "1"}, headers=_HEADERS),
    seed=_seed_inactive_delete,
    expect=ExpectSuccess(status=200, json_contains={"code": 200000, "data": {"deleted": True}}),
)
def delete_inactive_local_skill():
    """The real router, Core service, transaction, and package port delete one Skill."""


@endpoint_test(
    method="DELETE",
    path="/openapi/v1/bots/skills/{skill_id}",
    scenario="rejects_active_local_skill",
    input=CaseInput(path_params={"skill_id": "1"}, headers=_HEADERS),
    seed=_seed_active_delete,
    expect=ExpectError(
        status=409,
        json_contains={"code": 409102, "message": "Skill is active", "data": None},
    ),
)
def delete_active_local_skill_is_rejected():
    """The active conflict is visible through the assembled public endpoint."""


@endpoint_test(
    method="DELETE",
    path="/openapi/v1/bots/skills/{skill_id}",
    scenario="rejects_inactive_default_skill_referenced_by_active_custom_set",
    input=CaseInput(path_params={"skill_id": "1"}, headers=_HEADERS),
    seed=_seed_inactive_skill_in_active_custom_set,
    expect=ExpectError(
        status=409,
        json_contains={"code": 409102, "message": "Skill is active", "data": None},
    ),
)
def delete_skill_referenced_by_active_custom_set_is_rejected():
    """Default exclusion cannot override an active custom SkillSet reference."""
