"""Endpoint-framework coverage for the raw public Skill upload contract."""

from __future__ import annotations

import io
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
from agentclaw.community.core.bot_collaborator.repository.protocol import (
    BotCollabLogRepositoryProtocol,
)
from agentclaw.community.core.bot_management.repository.protocol import BotRepository
from agentclaw.community.core.skill_center.factories import (
    LocalSkillPackageStorage,
    SkillServiceFactory,
)
from agentclaw.community.core.skill_center.services.local_skill_upload_service import (
    LocalSkillUploadService,
)
from agentclaw.community.core.skill_center.services.repositories import (
    SkillRepository,
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


class _Storage:
    async def prepare(self) -> None:
        return None

    async def write(self, _files: list[tuple[str, bytes]]) -> None:
        return None

    async def cleanup(self) -> bool:
        return True


class _StorageFactory:
    def local_skill_package_storage(
        self, **_kwargs
    ) -> tuple[str, LocalSkillPackageStorage]:
        # HTTP, tenant, and Core persistence are real here; filesystem faults
        # belong to the Core fault-injection matrix.
        return "test-local/raw-upload", _Storage()  # type: ignore[return-value]


class _RuntimeFactory:
    def create(self, **_kwargs):
        return self

    def sync_runtime(self):
        return True


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
        archive.writestr(
            "SKILL.md", "name: raw-upload\ndescription: raw endpoint coverage\n"
        )
        archive.writestr("scripts/main.py", "print('ok')\n")
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
            world.get(SkillSetRepository),
            world.get(BotRepository),
            world.get(CollaboratorServiceProtocol),
            storage_factory,
            _RuntimeFactory(),
            world.get(BotCollabLogRepositoryProtocol),
            world.get(SkillsPoolEditGuard),
            _Cleanup(),
            lambda: _DeviceContextResolverStub(),
        ),
        scope=None,
    )


def _assert_associated_to_owning_bot_default(response, world) -> None:
    skill_id = response.json()["data"]["skill"]["skill_id"]
    with avernet_tenant_scope(_TENANT):
        default_set = world.get(SkillSetRepository).get_default(
            user_id=_OWNER, bolt_id=_BOT_ID, engine_type="openclaw"
        )
        assert default_set is not None
        assert default_set["bolt_id"] == _BOT_ID
        assert skill_id in {
            skill["id"]
            for skill in world.get(SkillSetRepository).get_skills_in_set(
                default_set["id"]
            )
        }


@endpoint_test(
    method="POST",
    path="/openapi/v1/bots/skills/upload",
    scenario="raw_zip_created_in_verified_tenant",
    input=CaseInput(
        query_params={"bot_id": _BOT_ID, "user_id": _OWNER},
        headers=_HEADERS,
        raw_body=_package(),
    ),
    seed=_seed_uploadable_bot,
    extra_assertions=(_assert_associated_to_owning_bot_default,),
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
    path="/openapi/v1/bots/skills/upload",
    scenario="multipart_rejected_after_verified_tenant_guard",
    input=CaseInput(
        query_params={"bot_id": _BOT_ID, "user_id": _OWNER},
        headers={**_HEADERS, "content-type": "multipart/form-data; boundary=x"},
        raw_body=b"--x--",
    ),
    seed=_seed_uploadable_bot,
    expect=ExpectError(status=400),
)
def multipart_upload_is_an_explicit_error_case():
    """The public endpoint accepts only raw ``application/zip`` bodies."""
