"""Real Router -> DI -> Center query coverage for Desktop Bot Skills."""

from __future__ import annotations

import json
import time
from datetime import datetime

import jwt
from fastapi.testclient import TestClient

from agentclaw.community.adapters.http.openapi_v1.dependencies import (
    PRINCIPAL_HEADER,
    require_principal,
)
from agentclaw.community.api.bot_app_grant_service import BotAppGrantServiceProtocol
from agentclaw.community.core.models.skill import Skill
from agentclaw.community.api.space_service import (
    SpaceMemberServiceProtocol,
    SpaceServiceProtocol,
)
from agentclaw.community.core.models.space_skill import SkillSpaceBinding, SkillVersion
from agentclaw.community.core.gateway_principal import (
    AppPrincipal,
    GatewayApp,
    VerifiedCaller,
)
from agentclaw.community.core.repository.protocols.center_skill_access import (
    CenterSkillAccessRepositoryProtocol,
)
from agentclaw.community.core.repository.protocols.skill_center import (
    SkillRepository,
    SkillVersionRepositoryProtocol,
)
from agentclaw.community.core.repository.protocols.bot import BotRepository
from agentclaw.community.core.skill_center.canonical_center_store import (
    CanonicalCenterVersion,
    CanonicalCenterVersionIdentity,
    CanonicalCenterVersionStore,
)
from agentclaw.community.core.skill_center.factories import SkillParameterServiceFactory
from agentclaw.community.plugin_api.database import DatabasePlugin
from agentclaw.community.core.spaces.models import SpaceRole
from agentclaw.community.utils.env_utils import get_current_env
from agentclaw.community.utils.gateway_principal_config import (
    init_principal_verifier_config,
)
from tests.community.factories.bot_collaborator import (
    make_bot,
    make_collaborator_record,
)


_OWNER = "desktop-center-owner"
_BOT = "desktop-center-bot"
_SKILL_UUID = "11111111-1111-4111-8111-111111111111"
_SIGNING_KEY = "desktop-center-router-signing-key-at-least-32-bytes"
_APP_ID = 2063


class _Secret:
    secret_user = "test"
    secret_value = _SIGNING_KEY


class _SecretResolver:
    def get_secret(self, _secret_name: str) -> _Secret:
        return _Secret()


def _headers(user_id: str = _OWNER) -> dict[str, str]:
    now = int(time.time())
    token = jwt.encode(
        {
            "iss": "gateway",
            "aud": "backend",
            "iat": now,
            "exp": now + 3600,
            "principals": [
                {
                    "type": "user",
                    "tenant": "teamclaw",
                    "subject": {"id": user_id, "username": f"{user_id}@example.test"},
                }
            ],
        },
        _SIGNING_KEY,
        algorithm="HS256",
    )
    return {PRINCIPAL_HEADER: token}


def _app_caller() -> VerifiedCaller:
    return VerifiedCaller(
        principals=(
            AppPrincipal(
                tenant="teamclaw",
                app=GatewayApp(
                    app_id=_APP_ID,
                    app_name="desktop-center-client",
                    owners="desktop-team",
                    tenant="teamclaw",
                ),
            ),
        )
    )


def _seed_center_skill(
    world, *, is_public: bool = True, space_id: int | None = None
) -> int:
    init_principal_verifier_config(_SecretResolver(), "test-key", strict=False)
    env = get_current_env()
    make_bot(
        world,
        bot_id=_BOT,
        owner_id=_OWNER,
        bot_type="desktop",
        status="PENDING",
        active_engine="openclaw",
    )
    database = world.get(DatabasePlugin)
    with database.orm_session() as session:
        skill = Skill(
            name="center-report",
            description="Published Center report",
            git_path="center://public-center-report",
            is_public=is_public,
            is_builtin=False,
            user_id=None,
            bolt_id="default",
            env=env,
            status="PUBLISHED",
            version=1,
            skill_uuid=_SKILL_UUID,
            source_type="center",
            avernet_tenant="teamclaw",
        )
        session.add(skill)
        session.flush()
        session.add(
            SkillVersion(
                skill_id=int(skill.id),
                publication_attempt_id=None,
                version_ordinal=1,
                status="PUBLISHED",
                sc_version_number="1.0.0",
                sc_skill_id=101,
                sc_version_id=1001,
                name="center-report",
                description="Published Center report",
                metadata_json=json.dumps({"mcp_dependencies": []}),
                published_at=datetime(2026, 9, 9),
                created_by=_OWNER,
                env=env,
                avernet_tenant="teamclaw",
            )
        )
        if space_id is not None:
            session.add(
                SkillSpaceBinding(
                    skill_id=int(skill.id),
                    space_id=space_id,
                    created_by=_OWNER,
                    env=env,
                    avernet_tenant="teamclaw",
                )
            )
        skill_id = int(skill.id)
        session.commit()

    world.get(CanonicalCenterVersionStore).write_version(
        CanonicalCenterVersion.from_files(
            CanonicalCenterVersionIdentity(
                skill_uuid=_SKILL_UUID,
                sc_version_number="1.0.0",
            ),
            {
                "SKILL.md": (
                    b"---\nname: center-report\ndescription: Published Center report\n"
                    b"config:\n  - name: region\n    required: true\n"
                    b"---\n# Exact published content\n"
                )
            },
        )
    )
    return skill_id


def _add_center_version(world, *, skill_id: int, ordinal: int, number: str) -> None:
    env = get_current_env()
    with world.get(DatabasePlugin).orm_session() as session:
        session.add(
            SkillVersion(
                skill_id=skill_id,
                publication_attempt_id=None,
                version_ordinal=ordinal,
                status="PUBLISHED",
                sc_version_number=number,
                sc_skill_id=101,
                sc_version_id=1000 + ordinal,
                name="center-report",
                description=f"Published Center report v{ordinal}",
                metadata_json=json.dumps({"mcp_dependencies": []}),
                published_at=datetime(2026, 9, 9, 0, ordinal),
                created_by=_OWNER,
                env=env,
                avernet_tenant="teamclaw",
            )
        )
        session.commit()
    world.get(CanonicalCenterVersionStore).write_version(
        CanonicalCenterVersion.from_files(
            CanonicalCenterVersionIdentity(
                skill_uuid=_SKILL_UUID,
                sc_version_number=number,
            ),
            {
                "SKILL.md": (
                    b"---\nname: center-report\ndescription: Latest Center report\n"
                    b"config:\n  - name: region\n    required: true\n"
                    b"---\n# Exact published content V2\n"
                )
            },
        )
    )


def test_desktop_reads_public_center_exact_content_through_real_router_and_di(
    app_with_testing_modules,
    world,
) -> None:
    skill_id = _seed_center_skill(world)
    _add_center_version(world, skill_id=skill_id, ordinal=2, number="2.0.0")
    client = TestClient(app_with_testing_modules)
    params = {"owner_id": _OWNER, "user_id": _OWNER}

    detail = client.get(
        f"/openapi/v1/bots/{_BOT}/skills/{skill_id}",
        params=params,
        headers=_headers(),
    )
    response = client.get(
        f"/openapi/v1/bots/{_BOT}/skills/{skill_id}/content",
        params=params,
        headers=_headers(),
    )

    assert detail.status_code == 200, detail.text
    assert detail.json()["data"]["skill_id"] == str(skill_id)
    assert response.status_code == 200, response.text
    assert response.json()["data"]["content"].endswith(
        "# Exact published content V2\n"
    )
    persisted = world.get(SkillRepository).get_by_id(str(skill_id))
    assert persisted is not None
    assert persisted["bolt_id"] == "default"
    assert persisted["user_id"] is None


def test_cloud_and_desktop_bots_read_the_same_published_center_content(
    app_with_testing_modules,
    world,
) -> None:
    skill_id = _seed_center_skill(world)
    cloud_bot = "center-cloud-bot"
    make_bot(
        world,
        bot_id=cloud_bot,
        owner_id=_OWNER,
        bot_type="personal",
        status="ACTIVE",
        active_engine="openclaw",
    )
    client = TestClient(app_with_testing_modules)
    params = {"owner_id": _OWNER, "user_id": _OWNER}

    desktop = client.get(
        f"/openapi/v1/bots/{_BOT}/skills/{skill_id}/content",
        params=params,
        headers=_headers(),
    )
    cloud = client.get(
        f"/openapi/v1/bots/{cloud_bot}/skills/{skill_id}/content",
        params=params,
        headers=_headers(),
    )

    assert desktop.status_code == cloud.status_code == 200
    assert desktop.json()["data"] == cloud.json()["data"]


def test_center_access_and_version_protocols_share_one_composition_root_instance(
    world,
) -> None:
    assert world.get(CenterSkillAccessRepositoryProtocol) is world.get(
        SkillVersionRepositoryProtocol
    )


def test_desktop_center_parameters_use_existing_bot_scoped_json_routes(
    app_with_testing_modules,
    world,
) -> None:
    skill_id = _seed_center_skill(world)

    class _Parameters:
        def __init__(self) -> None:
            self.saved: tuple[str, dict] | None = None

        def get_skill_parameters(self, name: str) -> dict:
            assert name == "center-report"
            return {"region": "cn"}

        async def save_skill_parameters(self, name: str, values: dict) -> bool:
            self.saved = (name, values)
            return True

    class _Factory:
        def __init__(self) -> None:
            self.parameters = _Parameters()

        async def create(self, **kwargs):
            assert kwargs == {"bot_id": _BOT, "user_id": _OWNER}
            return self.parameters

    factory = _Factory()
    world.injector.binder.bind(SkillParameterServiceFactory, to=factory, scope=None)
    client = TestClient(app_with_testing_modules)
    path = f"/openapi/v1/bots/{_BOT}/skills/{skill_id}/parameters"
    params = {"owner_id": _OWNER, "user_id": _OWNER}

    read = client.get(path, params=params, headers=_headers())
    written = client.put(
        path,
        params=params,
        headers=_headers(),
        json={"parameters": {"region": "sg"}},
    )

    assert read.status_code == 200, read.text
    assert read.json()["data"]["parameters"] == {"region": "cn"}
    assert written.status_code == 200, written.text
    assert written.json()["data"]["parameters"] == {"region": "sg"}
    assert factory.parameters.saved == ("center-report", {"region": "sg"})


def test_desktop_can_directly_activate_visible_center_skill_before_installation(
    app_with_testing_modules,
    world,
) -> None:
    skill_id = _seed_center_skill(world)

    response = TestClient(app_with_testing_modules).post(
        f"/openapi/v1/bots/{_BOT}/skills/{skill_id}/activate",
        params={"owner_id": _OWNER, "user_id": _OWNER},
        headers=_headers(),
    )

    assert response.status_code == 200, response.text
    assert response.json()["data"]["skill"]["active"] is True
    assert response.json()["data"]["runtime_projection"]["status"] == "PENDING"

    deactivated = TestClient(app_with_testing_modules).post(
        f"/openapi/v1/bots/{_BOT}/skills/{skill_id}/deactivate",
        params={"owner_id": _OWNER, "user_id": _OWNER},
        headers=_headers(),
    )
    assert deactivated.status_code == 200, deactivated.text
    assert deactivated.json()["data"]["skill"]["active"] is False
    assert deactivated.json()["data"]["runtime_projection"]["status"] == "PENDING"


def test_shared_center_readme_uses_latest_published_content_without_bot_context(
    app_with_testing_modules,
    world,
) -> None:
    skill_id = _seed_center_skill(world)

    response = TestClient(app_with_testing_modules).get(
        f"/openapi/v1/bots/skills/{skill_id}/readme",
        headers=_headers(),
    )

    assert response.status_code == 200, response.text
    assert response.json()["data"]["content"].endswith("# Exact published content\n")


def test_private_space_center_content_requires_live_space_membership_after_bot_access(
    app_with_testing_modules,
    world,
) -> None:
    member = "desktop-center-space-member"
    outsider = "desktop-center-space-outsider"
    space = world.get(SpaceServiceProtocol).create_team(
        name="Center Consumers", creator_id=_OWNER, create_sc_team=False
    )
    skill_id = _seed_center_skill(world, is_public=False, space_id=space.id)
    bot = world.get(BotRepository).get_by_id_and_owner(_BOT, _OWNER)
    assert bot is not None
    for actor in (member, outsider):
        make_collaborator_record(
            world,
            bot_pk=int(bot["id"]),
            bot_id=_BOT,
            owner_id=_OWNER,
            user_id=actor,
            role="member",
        )
    world.get(SpaceMemberServiceProtocol).add_member(
        space_id=space.id,
        actor_id=_OWNER,
        user_id=member,
        role=SpaceRole.MEMBER,
    )
    client = TestClient(app_with_testing_modules)
    path = f"/openapi/v1/bots/{_BOT}/skills/{skill_id}/content"
    params = {"owner_id": _OWNER}

    allowed = client.get(
        path,
        params={**params, "user_id": member},
        headers=_headers(member),
    )
    denied = client.get(
        path,
        params={**params, "user_id": outsider},
        headers=_headers(outsider),
    )

    assert allowed.status_code == 200, allowed.text
    assert denied.status_code == 404


def test_app_grant_and_bot_collaboration_reach_public_center_content(
    app_with_testing_modules,
    world,
) -> None:
    collaborator = "desktop-center-app-user"
    skill_id = _seed_center_skill(world)
    bot = world.get(BotRepository).get_by_id_and_owner(_BOT, _OWNER)
    assert bot is not None
    make_collaborator_record(
        world,
        bot_pk=int(bot["id"]),
        bot_id=_BOT,
        owner_id=_OWNER,
        user_id=collaborator,
        role="member",
    )
    world.get(BotAppGrantServiceProtocol).grant(
        bot_id=_BOT,
        user_id=collaborator,
        owner_id=_OWNER,
        app_id=_APP_ID,
        app_name="desktop-center-client",
    )
    app_with_testing_modules.dependency_overrides[require_principal] = _app_caller
    try:
        response = TestClient(app_with_testing_modules).get(
            f"/openapi/v1/bots/{_BOT}/skills/{skill_id}/content",
            params={"owner_id": _OWNER, "user_id": collaborator},
        )
    finally:
        app_with_testing_modules.dependency_overrides.pop(require_principal, None)

    assert response.status_code == 200, response.text
    assert response.json()["data"]["content"].endswith("# Exact published content\n")
