"""Assembled Track-A guard coverage for every public Local Skill operation."""

from __future__ import annotations

import io
import time
import zipfile

import jwt
from fastapi.testclient import TestClient

from tests.community.adapters.http.openapi_v1.conftest import (
    mount_public_error_handlers,
    user_scoped_client,
)
from agentclaw.community.adapters.http.openapi_v1.dependencies import PRINCIPAL_HEADER
from agentclaw.community.core.bot_management.repository.protocol import BotRepository
from agentclaw.community.core.skill_center.services.repositories import SkillRepository
from agentclaw.community.utils.avernet_tenant import avernet_tenant_scope
from agentclaw.community.utils.gateway_principal_config import (
    init_principal_verifier_config,
    reset_principal_verifier_config_cache,
)


_OWNER = "skills-owner"
_BOT_ID = "skills-tenant-bot"
_PRIVATE_BOT_ID = "skills-tenant-a-private-bot"
_TENANT_A = "skills-tenant-a"
_TENANT_B = "skills-tenant-b"
_KEY = "skills-tenant-isolation-signing-key-at-least-32-bytes"


class _Secret:
    secret_user = "test"
    secret_value = _KEY


class _Resolver:
    def get_secret(self, _secret_name: str) -> _Secret:
        return _Secret()


def _principal(tenant: str) -> str:
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
                    "subject": {"id": _OWNER, "username": "skills@example.test"},
                },
                {
                    "type": "app",
                    "tenant": tenant,
                    "app": {
                        "app_id": 1,
                        "app_name": "Skills tenant isolation test",
                        "owners": _OWNER,
                        "tenant": tenant,
                    },
                },
            ],
        },
        _KEY,
        algorithm="HS256",
    )


def _package() -> bytes:
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w") as archive:
        archive.writestr(
            "SKILL.md", "name: tenant-a-skill\ndescription: must stay private\n"
        )
    return payload.getvalue()


def test_every_skills_operation_is_guarded_from_another_tenant(
    app_with_testing_modules, world
) -> None:
    """A real router, repositories, and Track-A guard reject all six paths."""
    init_principal_verifier_config(_Resolver(), "test-key", strict=False)
    skills = world.get(SkillRepository)
    with avernet_tenant_scope(_TENANT_A):
        world.get(BotRepository).insert(
            {
                "bot_id": _PRIVATE_BOT_ID,
                "bot_name": "Tenant A Skills Bot",
                "owner_id": _OWNER,
                "owner_name": _OWNER,
                "entity_id": _OWNER,
                "entity_type": "staff",
                "creator_id": _OWNER,
                "status": "ACTIVE",
                "active_engine": "openclaw",
            }
        )
        skill = skills.create(
            {
                "name": "tenant-a-skill",
                "description": "Owned by tenant A",
                "git_path": "local://tenant-a-skill",
                "user_id": _OWNER,
                "bolt_id": _PRIVATE_BOT_ID,
            }
        )
        world.get(BotRepository).insert(
            {
                "bot_id": _BOT_ID,
                "bot_name": "Tenant A Same-Shaped Bot",
                "owner_id": _OWNER,
                "owner_name": _OWNER,
                "entity_id": _OWNER,
                "entity_type": "staff",
                "creator_id": _OWNER,
                "status": "ACTIVE",
                "active_engine": "openclaw",
            }
        )
        skills.create(
            {
                "name": "tenant-a-skill",
                "description": "Same-shaped tenant A record",
                "git_path": "local://tenant-a-skill",
                "user_id": _OWNER,
                "bolt_id": _BOT_ID,
            }
        )
    with avernet_tenant_scope(_TENANT_B):
        world.get(BotRepository).insert(
            {
                "bot_id": _BOT_ID,
                "bot_name": "Tenant B Same-Shaped Bot",
                "owner_id": _OWNER,
                "owner_name": _OWNER,
                "entity_id": _OWNER,
                "entity_type": "staff",
                "creator_id": _OWNER,
                "status": "ACTIVE",
                "active_engine": "openclaw",
            }
        )
        tenant_b_skill = skills.create(
            {
                "name": "tenant-a-skill",
                "description": "Same-shaped tenant B record",
                "git_path": "local://tenant-a-skill",
                "user_id": _OWNER,
                "bolt_id": _BOT_ID,
            }
        )

    headers = {PRINCIPAL_HEADER: _principal(_TENANT_B)}
    client = user_scoped_client(app_with_testing_modules, _OWNER)
    own_list = client.get(f"/openapi/v1/bots/skills?bot_id={_BOT_ID}", headers=headers)
    assert own_list.status_code == 200
    assert own_list.json()["data"]["total"] == 1
    assert [item["skill_id"] for item in own_list.json()["data"]["items"]] == [
        tenant_b_skill["id"]
    ]
    requests = (
        client.get(
            f"/openapi/v1/bots/skills?bot_id={_PRIVATE_BOT_ID}", headers=headers
        ),
        client.get(f"/openapi/v1/bots/skills/{skill['id']}", headers=headers),
        client.post(
            f"/openapi/v1/bots/skills/upload?bot_id={_PRIVATE_BOT_ID}",
            content=_package(),
            headers={**headers, "content-type": "application/zip"},
        ),
        client.post(f"/openapi/v1/bots/skills/{skill['id']}/activate", headers=headers),
        client.post(
            f"/openapi/v1/bots/skills/{skill['id']}/deactivate", headers=headers
        ),
        client.delete(f"/openapi/v1/bots/skills/{skill['id']}", headers=headers),
    )
    try:
        for response in requests:
            assert response.status_code == 404
            assert response.json() == {
                "code": 404000,
                "message": "Not found",
                "data": None,
                "request_id": "",
            }
    finally:
        reset_principal_verifier_config_cache()

    with avernet_tenant_scope(_TENANT_A):
        unchanged = skills.get_bot_local_skill(
            skill_id=skill["id"], bot_id=_PRIVATE_BOT_ID, user_id=_OWNER
        )
        replacements = skills.list_bot_local_by_name(
            bot_id=_PRIVATE_BOT_ID, name="tenant-a-skill"
        )
    assert unchanged is not None
    assert unchanged["active"] is True
    assert [replacement["id"] for replacement in replacements] == [skill["id"]]
