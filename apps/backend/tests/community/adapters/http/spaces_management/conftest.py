"""Real HTTP → service → repository → SQLite test composition."""


from agentclaw.community.core.repository.implementations.spaces.persistence_effects import NoKnowledgeSpacePersistenceEffects
import asyncio
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from fastapi_injector import attach_injector
from injector import Injector

from agentclaw.community.adapters.http.auth.dependencies import get_current_user
from agentclaw.community.adapters.http.auth.models import AuthenticatedUser
from agentclaw.community.core.config.sofa import sofa_config
from agentclaw.community.adapters.http.spaces_management.router import router
from agentclaw.community.api.space_management_service import (
    SpaceManagementServiceProtocol,
)
from agentclaw.community.core.repository.implementations.spaces.management import (
    SpaceManagementRepository,
)
from agentclaw.community.core.repository.implementations.spaces.space import (
    SpaceRepository,
)
from agentclaw.community.core.spaces.services.space_management_service import (
    SpaceManagementService,
)
from agentclaw.community.plugin_api.skill_center_client import (
    SkillCenterTeamCreateResult,
)
from agentclaw.community.plugins.local.database import SqliteDB, reset_for_tests
from agentclaw.community.utils.env_utils import get_current_env


from itertools import count


@pytest.fixture
def harness(monkeypatch):
    monkeypatch.setenv("SERVER_ENV", "pre")
    reset_for_tests()
    db = SqliteDB()
    asyncio.run(db.bootstrap())
    sc = MagicMock()
    team_ids = count(1)
    sc.create_team.side_effect = lambda request: SkillCenterTeamCreateResult(
        team_id=f"sc-team-{next(team_ids)}",
    )
    staff = MagicMock()
    staff.get_profile_by_work_no.return_value.nick_name = "Employee"
    service = SpaceManagementService(
        SpaceManagementRepository(db),
        SpaceRepository(db, NoKnowledgeSpacePersistenceEffects()),
        sc,
        staff,
        get_current_env,
    )
    app = FastAPI()
    app.include_router(router)
    # Keep the real whitelist dependency; replace only the authenticated identity.
    caller = AuthenticatedUser(
        id="operator", staffId="operator", operatorName="Operator"
    )
    app.dependency_overrides[get_current_user] = lambda: caller
    # Patch only the proxy instance dictionary: teardown must remove the key,
    # not restore a resolved value that would shadow future config providers.
    monkeypatch.setitem(
        vars(sofa_config), "user_config", {"admin": {"super_admin": ["operator"]}}
    )
    injector = Injector()
    injector.binder.bind(SpaceManagementServiceProtocol, to=service)
    attach_injector(app, injector)
    with TestClient(app) as client:
        yield client, db, sc, staff, service
    reset_for_tests()


@pytest.fixture
def create(harness):
    client, *_ = harness

    def create_team(name="Example", admin="100001", members=()):
        response = client.post(
            "/api/spaces/initialize",
            json={
                "space_name": name,
                "admin_user_id": admin,
                "member_user_ids": list(members),
            },
        )
        assert response.status_code == 201, response.text
        return response.json()["data"]["space"]["id"]

    return create_team
