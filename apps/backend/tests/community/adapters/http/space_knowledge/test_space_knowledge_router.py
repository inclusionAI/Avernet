"""Browser actor/revision claims never replace authenticated context."""

from unittest.mock import Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from fastapi_injector import attach_injector
from injector import Injector

from agentclaw.community.adapters.http.auth.dependencies import get_current_user
from agentclaw.community.adapters.http.auth.models import AuthenticatedUser
from agentclaw.community.adapters.http.space_knowledge.router import router
from agentclaw.community.api.space_knowledge_service import (
    SpaceKnowledgeServiceProtocol,
)
from agentclaw.community.plugin_api.space_knowledge import (
    KnowledgeBasePage,
    KnowledgeBaseResult,
    KnowledgeBaseState,
    SpaceKnowledgeError,
)


@pytest.fixture
def client():
    service = Mock()
    state = KnowledgeBaseState(
        "kb-one", "tenant-one", "Knowledge", "", "creating", "001234", "001234"
    )
    service.create.return_value = KnowledgeBaseResult(state, True)
    service.get.return_value = KnowledgeBaseResult(state)
    service.list_knowledge_bases.return_value = KnowledgeBasePage([state])
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(
        id="sso-subject", staffId="001234", operatorName="caller"
    )
    injector = Injector()
    injector.binder.bind(SpaceKnowledgeServiceProtocol, to=service)
    attach_injector(app, injector)
    with TestClient(app) as client:
        yield client, service


def test_actual_staff_id_and_creating_response(client):
    http, service = client
    response = http.post(
        "/api/spaces/7/knowledge-bases?actor_staff_no=attacker",
        json={"request_id": "stable", "name": "Knowledge"},
        headers={"X-Staff-Id": "attacker"},
    )
    assert response.status_code == 202
    assert response.json()["status"] == "creating"
    service.create.assert_called_once_with(
        space_id=7,
        actor_id="001234",
        request_id="stable",
        name="Knowledge",
        description="",
    )


@pytest.mark.parametrize(
    "field",
    [
        "actor_id",
        "actor_staff_no",
        "tenant_id",
        "expected_space_revision",
        "members",
        "owner_id",
        "tc_env",
    ],
)
def test_create_rejects_privileged_fields(client, field):
    http, service = client
    result = http.post(
        "/api/spaces/7/knowledge-bases",
        json={"request_id": "stable", "name": "Knowledge", field: "untrusted"},
    )
    assert result.status_code == 422
    service.create.assert_not_called()


def test_detail_uses_current_actor(client):
    http, service = client
    result = http.get("/api/spaces/7/knowledge-bases/kb-one?actor_staff_no=attacker")
    assert result.status_code == 200
    service.get.assert_called_once_with(space_id=7, actor_id="001234", kb_id="kb-one")


def test_list_uses_current_actor_and_has_stable_envelope(client):
    http, service = client
    response = http.get("/api/spaces/7/knowledge-bases?actor_staff_no=attacker")
    assert response.status_code == 200
    assert response.json()["knowledge_bases"][0]["kb_id"] == "kb-one"
    service.list_knowledge_bases.assert_called_once_with(
        space_id=7, actor_id="001234", page_size=50, cursor=""
    )
    service.list_knowledge_bases.side_effect = SpaceKnowledgeError(
        "DEPENDENCY_UNAVAILABLE"
    )
    assert http.get("/api/spaces/7/knowledge-bases").status_code == 503


def test_browser_retry_route_does_not_exist(client):
    http, service = client
    assert (
        http.post("/api/spaces/7/knowledge-bases/kb-one/retry-provision").status_code
        == 404
    )
    service.retry.assert_not_called()


@pytest.mark.parametrize(
    "code,status",
    [
        ("SPACE_SYNC_UNAVAILABLE", 503),
        ("CREATE_REQUEST_CONFLICT", 409),
        ("SPACE_MEMBERSHIP_LIMIT_EXCEEDED", 409),
        ("KB_DELETED", 410),
        ("private error", 503),
    ],
)
def test_safe_failures(client, code, status):
    http, service = client
    service.get.side_effect = SpaceKnowledgeError(code)
    response = http.get("/api/spaces/7/knowledge-bases/kb-one")
    assert response.status_code == status
    assert response.json() == {
        "detail": {
            "code": code if code != "private error" else "DEPENDENCY_UNAVAILABLE"
        }
    }


@pytest.mark.parametrize(
    "query",
    ["page_size=0", "page_size=101", "cursor=folder%2Fnext", "cursor=white%20space"],
)
def test_list_rejects_invalid_pagination_without_calling_service(client, query):
    http, service = client
    assert http.get("/api/spaces/7/knowledge-bases?" + query).status_code == 422
    service.list_knowledge_bases.assert_not_called()


def test_list_forwards_pagination_but_not_actor_assertions(client):
    http, service = client
    response = http.get(
        "/api/spaces/7/knowledge-bases?page_size=2&cursor=kb-before&actor_id=attacker",
        headers={"X-Staff-Id": "attacker"},
    )
    assert response.status_code == 200
    service.list_knowledge_bases.assert_called_once_with(
        space_id=7, actor_id="001234", page_size=2, cursor="kb-before"
    )
