"""User-list E2E business flows over LOCAL SQLite."""
from __future__ import annotations

import pytest

from tests.community._flows.user_list.api_lifecycle import USER_LIST_FLOWS
from tests.community.framework.flow_runner import run_flow


@pytest.mark.e2e
@pytest.mark.parametrize("case", USER_LIST_FLOWS, ids=lambda case: case.name)
def test_user_list_flow(case, app_with_testing_modules, world):
    """Run the membership write/read lifecycle through the HTTP application."""
    ctx = run_flow(case, app_with_testing_modules, world)
    assert ctx is not None


@pytest.mark.parametrize("is_operator", [True, False])
@pytest.mark.parametrize("is_super_admin", [True, False])
@pytest.mark.parametrize("entity_id", ["rollout_owner", "*"])
@pytest.mark.parametrize("user_list_type", ["caller_identity", "service_bot_runtime_artifact"])
@pytest.mark.parametrize("enabled", [True, False])
def test_user_list_correction_super_admin_boundary(
    app_with_testing_modules, world, caplog, monkeypatch, is_operator, is_super_admin, entity_id, user_list_type, enabled,
):
    from fastapi.testclient import TestClient
    from sqlalchemy import select

    from agentclaw.community.core.user_list.models import EntityUserListModel
    from agentclaw.community.plugin_api.auth import AuthPlugin
    from agentclaw.community.plugin_api.database import DatabasePlugin
    from tests.community.factories.access import make_staff_user

    from agentclaw.community.core.access import admin_scopes

    monkeypatch.setattr(admin_scopes, "super_admin", lambda: frozenset({"correction_actor"}) if is_super_admin else frozenset())
    make_staff_user(world, user_id="correction_actor")
    world.get(AuthPlugin).set_response("is_operator_allowed", is_operator)
    database = world.get(DatabasePlugin)
    with database.orm_session() as session:
        session.add(EntityUserListModel(entity_id=entity_id, user_list_type=user_list_type, env="pre"))
        if not enabled:
            session.add(EntityUserListModel(entity_id=entity_id, user_list_type=user_list_type, env="prod"))

    client = TestClient(app_with_testing_modules)
    caplog.set_level("INFO")
    headers = {"x-user-id": "correction_actor", "Authorization": "Bearer private-test-auth-value"}
    response = client.put(
        "/api/v1/user-lists/correct", params={"env": "prod"}, headers=headers,
        json={"entity_id": entity_id, "user_list_type": user_list_type, "in_whitelist": enabled},
    )
    assert response.status_code == (200 if is_super_admin else 403)
    if is_super_admin:
        assert "user_list.correction.request" in caplog.text
        assert "user_list.correction.success" in caplog.text
        for field in ("actor_id=correction_actor", f"entity_id={entity_id}",
                      f"user_list_type={user_list_type}", "env=prod", f"in_whitelist={enabled}", "elapsed_ms="):
            assert field in caplog.text
    else:
        assert "[DomainError 403] Forbidden on PUT /api/v1/user-lists/correct" in caplog.text
        assert "user_list.correction.success" not in caplog.text
    assert "private-test-auth-value" not in caplog.text
    if is_super_admin:
        assert response.json()["data"]["in_whitelist"] is enabled
    with database.orm_session() as session:
        environments = set(session.scalars(select(EntityUserListModel.env).where(
            EntityUserListModel.entity_id == entity_id,
            EntityUserListModel.user_list_type == user_list_type,
        )).all())
    expected_enabled = enabled if is_super_admin else not enabled
    assert environments == ({"pre", "prod"} if expected_enabled else {"pre"})

    # Membership reads remain available to ordinary authenticated users.
    world.get(AuthPlugin).set_response("is_operator_allowed", False)
    check = client.get(
        "/api/v1/user-lists/check", headers=headers,
        params={"entity_id": entity_id, "user_list_type": user_list_type, "env": "prod"},
    )
    assert check.status_code == 200
    assert check.json()["data"]["in_whitelist"] is expected_enabled


def test_user_list_correction_logs_write_failure(app_with_testing_modules, world, caplog):
    from fastapi.testclient import TestClient

    from agentclaw.community.core.repository.protocols.identity import UserListRepositoryProtocol
    from tests.community.factories.access import make_staff_user
    from tests.community.framework import bind_failing_method

    make_staff_user(world, user_id="correction_operator")
    bind_failing_method(
        world, UserListRepositoryProtocol, "set_membership", RuntimeError("synthetic persistence unavailable"),
    )
    caplog.set_level("INFO")
    client = TestClient(app_with_testing_modules, raise_server_exceptions=False)
    response = client.put(
        "/api/v1/user-lists/correct", params={"env": "prod"},
        headers={"x-user-id": "correction_operator", "Authorization": "Bearer private-test-auth-value"},
        json={"entity_id": "*", "user_list_type": "service_bot_runtime_artifact", "in_whitelist": True},
    )
    assert response.status_code == 500
    assert "user_list.correction.request" in caplog.text
    assert "user_list.correction.failed" in caplog.text
    for field in ("actor_id=correction_operator", "entity_id=*", "env=prod", "error_type=RuntimeError", "elapsed_ms="):
        assert field in caplog.text
    assert "user_list.correction.success" not in caplog.text
    assert "private-test-auth-value" not in caplog.text


@pytest.fixture(autouse=True)
def _correction_super_admins(monkeypatch):
    from agentclaw.community.core.access import admin_scopes

    monkeypatch.setattr(admin_scopes, "super_admin", lambda: frozenset(['e2e_user', 'correction_operator']))
