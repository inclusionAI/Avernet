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
@pytest.mark.parametrize("entity_id", ["rollout_owner", "*"])
@pytest.mark.parametrize("user_list_type", ["caller_identity", "service_bot_runtime_artifact"])
@pytest.mark.parametrize("enabled", [True, False])
def test_user_list_correction_operator_boundary(
    app_with_testing_modules, world, caplog, is_operator, entity_id, user_list_type, enabled,
):
    from fastapi.testclient import TestClient
    from sqlalchemy import select

    from agentclaw.community.core.user_list.models import EntityUserListModel
    from agentclaw.community.plugin_api.auth import AuthPlugin
    from agentclaw.community.plugin_api.database import DatabasePlugin
    from tests.community.factories.access import make_staff_user

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
    assert response.status_code == (200 if is_operator else 403)
    if is_operator:
        assert "user_list.correction.request" in caplog.text
        assert "user_list.correction.success" in caplog.text
        for field in ("actor_id=correction_actor", f"entity_id={entity_id}",
                      f"user_list_type={user_list_type}", "env=prod", f"in_whitelist={enabled}", "elapsed_ms="):
            assert field in caplog.text
    else:
        assert "[DomainError 403] Forbidden on PUT /api/v1/user-lists/correct" in caplog.text
        assert "user_list.correction.success" not in caplog.text
    assert "private-test-auth-value" not in caplog.text
    if is_operator:
        assert response.json()["data"]["in_whitelist"] is enabled
    with database.orm_session() as session:
        environments = set(session.scalars(select(EntityUserListModel.env).where(
            EntityUserListModel.entity_id == entity_id,
            EntityUserListModel.user_list_type == user_list_type,
        )).all())
    expected_enabled = enabled if is_operator else not enabled
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


@pytest.mark.parametrize("subject_type", ["user", "department"])
@pytest.mark.parametrize("bot_type", ["all", "service", "personal"])
@pytest.mark.parametrize("is_operator", [True, False])
def test_scoped_membership_management(
    app_with_testing_modules, world, caplog, subject_type, bot_type, is_operator,
):
    from fastapi.testclient import TestClient
    from agentclaw.community.core.user_list.models import EntityUserListModel
    from agentclaw.community.plugin_api.auth import AuthPlugin
    from agentclaw.community.plugin_api.database import DatabasePlugin
    from tests.community.factories.access import make_staff_user

    make_staff_user(world, user_id="scoped_operator")
    world.get(AuthPlugin).set_response("is_operator_allowed", is_operator)
    entity_id = "Group/Platform" if subject_type == "department" else "staff"
    payload = dict(entity_id=entity_id, subject_type=subject_type, bot_type=bot_type,
                   user_list_type="feature", in_whitelist=True)
    client = TestClient(app_with_testing_modules)
    caplog.set_level("INFO")
    headers = {"x-user-id": "scoped_operator", "Authorization": "Bearer AUTH_SENTINEL"}
    response = client.put("/api/v1/user-lists/correct?env=pre", headers=headers, json=payload)
    assert response.status_code == (200 if is_operator else 403), response.text
    with world.get(DatabasePlugin).orm_session() as session:
        rows = session.query(EntityUserListModel).all()
        assert len(rows) == int(is_operator)
        if rows:
            assert (rows[0].subject_type, rows[0].bot_type, rows[0].entity_id) == (subject_type, bot_type, entity_id)
    if is_operator:
        assert "user_list.correction.request" in caplog.text
        assert "user_list.correction.success" in caplog.text
        assert f"subject_type={subject_type}" in caplog.text
        assert f"bot_type={bot_type}" in caplog.text
        payload["in_whitelist"] = False
        assert client.put("/api/v1/user-lists/correct?env=pre", headers=headers, json=payload).status_code == 200
        with world.get(DatabasePlugin).orm_session() as session:
            assert session.query(EntityUserListModel).count() == 0
    assert "AUTH_SENTINEL" not in caplog.text


@pytest.mark.parametrize("field,value", [
    ("subject_type", "unknown"), ("bot_type", "desktop"),
    ("entity_id", "/Group"), ("entity_id", "Group//Platform"),
])
def test_scoped_correction_validation_has_no_side_effects(app_with_testing_modules, world, field, value):
    from fastapi.testclient import TestClient
    from agentclaw.community.core.user_list.models import EntityUserListModel
    from agentclaw.community.plugin_api.database import DatabasePlugin
    from tests.community.factories.access import make_staff_user
    make_staff_user(world, user_id="operator")
    payload = dict(entity_id="Group/Platform", subject_type="department", bot_type="all", user_list_type="feature", in_whitelist=True)
    payload[field] = value
    response = TestClient(app_with_testing_modules).put("/api/v1/user-lists/correct?env=pre", headers={"x-user-id": "operator"}, json=payload)
    assert response.status_code == 422
    with world.get(DatabasePlugin).orm_session() as session:
        assert session.query(EntityUserListModel).count() == 0


def test_scoped_correction_failure_and_conflict_logs(app_with_testing_modules, world, monkeypatch, caplog):
    from fastapi.testclient import TestClient
    from agentclaw.community.core.user_list.service import UserListService
    from agentclaw.community.core.user_list.rules import MembershipConflictError
    from tests.community.factories.access import make_staff_user
    make_staff_user(world, user_id="operator")
    service = world.get(UserListService)
    client = TestClient(app_with_testing_modules, raise_server_exceptions=False)
    caplog.set_level("INFO")
    payload = dict(entity_id="Group/Platform", subject_type="department", bot_type="service", user_list_type="feature", in_whitelist=True)
    for error, expected in [(MembershipConflictError("CONFLICT_CREDENTIAL"), 409), (RuntimeError("DB_CREDENTIAL"), 500)]:
        def fail(**kwargs):
            raise error
        monkeypatch.setattr(service._repository, "set_membership", fail)
        response = client.put("/api/v1/user-lists/correct?env=pre", headers={"x-user-id": "operator"}, json=payload)
        assert response.status_code == expected
    assert "user_list.correction.request" in caplog.text
    assert "user_list.correction.failed" in caplog.text
    assert "CONFLICT_CREDENTIAL" not in caplog.text
    # The adapter failure event must not serialize the DB exception credential.
    assert all("DB_CREDENTIAL" not in record.getMessage() for record in caplog.records if "user_list.correction" in record.getMessage())
