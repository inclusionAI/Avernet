"""Production route mounting, DI composition and ordinary HTTP verification."""

from unittest.mock import AsyncMock
import pytest
from fastapi.testclient import TestClient

from agentclaw.community.adapters.http.app import app
from agentclaw.community.core.config.sofa import sofa_config
from agentclaw.community.core.errors import Unauthorized
from agentclaw.community.plugin_api.auth import AuthPlugin, AuthenticatedIdentity
from agentclaw.community.api.space_management_service import (
    SpaceManagementServiceProtocol,
)
from agentclaw.community.core.spaces.repository.models import (
    SpaceMemberModel,
    SpaceModel,
)
from agentclaw.community.core.spaces.services.space_management_service import (
    SpaceManagementService,
)
from agentclaw.community.plugin_api.database import DatabasePlugin
from agentclaw.community.plugin_api.skill_center_client import SkillCenterClient
from agentclaw.community.plugin_api.staff_dept import StaffDeptPlugin


@pytest.fixture
def application(harness, test_injector, monkeypatch):
    _, db, sc, staff, _ = harness
    test_injector.binder.bind(DatabasePlugin, to=db)
    test_injector.binder.bind(SkillCenterClient, to=sc)
    test_injector.binder.bind(StaffDeptPlugin, to=staff)
    service = test_injector.get(SpaceManagementServiceProtocol)
    assert isinstance(service, SpaceManagementService)
    monkeypatch.setattr(app.state, "injector", test_injector)
    auth_plugin = AsyncMock(spec=AuthPlugin)

    async def resolve(ctx):
        # Test boundary only: production authentication is owned by its AuthPlugin.
        identity = ctx.headers.get("x-test-identity")
        if identity is None:
            raise Unauthorized("missing test login")
        return AuthenticatedIdentity(
            id="session-user", staffId=identity, operatorName="Test"
        )

    auth_plugin.resolve_user_from_request.side_effect = resolve
    test_injector.binder.bind(AuthPlugin, to=auth_plugin)
    # Do not run the product lifespan: the test injector supplies initialized SQLite.
    client = TestClient(app, raise_server_exceptions=True)
    try:
        yield client, {"x-test-identity": "operator"}
    finally:
        client.close()


def test_real_app_authenticated_request_uses_production_di_and_sqlite(
    application, harness
):
    client, auth = application
    _, db, sc, *_ = harness
    response = client.post(
        "/api/spaces/initialize",
        headers=auth,
        json={
            "space_name": "Authenticated request",
            "admin_user_id": "100001",
            "member_user_ids": ["100002"],
        },
    )
    assert response.status_code == 201, response.text
    sid = response.json()["data"]["space"]["id"]
    response = client.get(f"/api/spaces/{sid}", headers=auth)
    assert response.status_code == 200, response.text
    assert response.json()["data"]["member_count"] == 2
    with db.orm_session() as session:
        assert session.query(SpaceModel).one().env == "pre"
        assert session.query(SpaceMemberModel).count() == 2
    sc.create_team.assert_called_once()


@pytest.mark.parametrize(
    "method,path",
    [
        ("POST", "/api/spaces"),
        ("POST", "/api/spaces/initialize"),
        ("GET", "/api/spaces"),
        ("GET", "/api/spaces/1"),
        ("POST", "/api/spaces/1/update"),
        ("DELETE", "/api/spaces/1"),
        ("POST", "/api/spaces/1/delete-with-members"),
        ("GET", "/api/spaces/1/members"),
        ("POST", "/api/spaces/1/members/batch-add"),
        ("POST", "/api/spaces/1/members/batch-remove"),
        ("PUT", "/api/spaces/1/members/roles"),
    ],
)
@pytest.mark.parametrize(
    "identity,status", [(None, 401), ("", 401), ("anonymous", 401), ("ordinary", 403)]
)
def test_all_mounted_endpoints_refuse_missing_or_invalid_caller(
    application, harness, method, path, identity, status
):
    client, _ = application
    _, db, sc, staff, _ = harness
    response = client.request(
        method,
        path,
        headers={} if identity is None else {"x-test-identity": identity},
        json={"admin_user_id": "operator"},
    )
    assert response.status_code == status, response.text
    sc.create_team.assert_not_called()
    staff.get_profile_by_work_no.assert_not_called()
    with db.orm_session() as session:
        assert session.query(SpaceModel).count() == 0
        assert session.query(SpaceMemberModel).count() == 0


@pytest.mark.parametrize(
    "config",
    [
        {},
        {"admin": {}},
        {"admin": {"super_admin": []}},
        {"admin": {"super_admin": "operator"}},
        {"admin": "invalid"},
    ],
)
def test_unconfigured_or_malformed_whitelist_denies(
    application, harness, monkeypatch, config
):
    client, auth = application
    monkeypatch.setattr(sofa_config, "user_config", config)
    response = client.post(
        "/api/spaces",
        headers=auth,
        json={"space_name": "Denied", "admin_user_id": "operator"},
    )
    assert response.status_code == 403
    assert response.json()["code"] == "SPACE_ADMIN_REQUIRED"
    _, db, sc, *_ = harness
    sc.create_team.assert_not_called()
    with db.orm_session() as session:
        assert session.query(SpaceModel).count() == 0


def test_whitelist_uses_staff_id_not_identity_id(application, monkeypatch):
    client, auth = application
    monkeypatch.setattr(
        sofa_config, "user_config", {"admin": {"super_admin": ["session-user"]}}
    )
    assert client.get("/api/spaces", headers=auth).status_code == 403
