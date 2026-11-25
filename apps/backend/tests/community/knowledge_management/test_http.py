import pytest
from agentclaw.community.adapters.http.auth.dependencies import get_current_user
from agentclaw.community.adapters.http.auth.models import AuthenticatedUser
from agentclaw.community.core.knowledge_management.repository.source import (
    ExistingKnowledgeEnablementReader,
)
from fastapi import HTTPException

PREFIX = "/api/knowledge/spaces/1/knowledge-bases"


@pytest.mark.parametrize(
    "query", ["", "?limit=1", "?page_size=1", "?limit=1&page_size=1"]
)
def test_http_old_and_new_pagination_contracts(client, world, query):
    response = client.get(PREFIX + query)
    assert response.status_code == 200
    body = response.json()
    assert body["items"] == body["knowledge_bases"]
    assert body["items"][0]["updated_at"] is None
    assert body["items"][0]["updated_at_basis"] == "unavailable"


@pytest.mark.parametrize(
    "query,code",
    [("?limit=1&page_size=2", 400), ("?limit=101", 422), ("?page_size=0", 422)],
)
def test_http_invalid_pagination_rejected_before_backend(client, world, query, code):
    assert client.get(PREFIX + query).status_code == code
    assert world.catalog.calls == []


def test_http_actor_only_from_session_and_old_detail_not_mounted(client, world):
    response = client.get(
        PREFIX + "/kb/catalog-detail?actor_staff_no=admin&actor_id=admin"
    )
    assert response.status_code == 200
    assert world.catalog.calls[0][1]["actor_id"] == "actor"
    assert client.get(PREFIX + "/kb").status_code == 404
    assert client.get("/api/knowledge/spaces/1/bots").status_code == 404


def test_http_authorized_bot_display_for_noneditor(client):
    response = client.get(PREFIX + "/kb/authorized-bots")
    assert response.status_code == 200
    assert response.json()["items"][0]["bot_name"] == "Real bot"
    assert response.json()["items"][0]["enabled"] is False


def test_http_enablement_model_missing_is_503_not_false(client, world):
    world.service.enablement = ExistingKnowledgeEnablementReader(world.db, "pre")
    response = client.get(PREFIX + "/kb/authorized-bots")
    assert response.status_code == 503
    assert response.json() == {"detail": {"code": "KNOWLEDGE_ENABLEMENT_UNAVAILABLE"}}


def test_http_native_acl_denial_preserved(client, world):
    world.catalog.denied = True
    response = client.get(PREFIX + "/kb/authorized-bots")
    assert response.status_code == 403
    assert response.json() == {"detail": {"code": "KNOWLEDGE_REMOTE_ACCESS_DENIED"}}


def test_http_read_without_manage_cannot_enumerate_candidates(client, world):
    world.catalog.kb["can_manage"] = False
    assert client.get(PREFIX + "/kb/catalog-detail").status_code == 200
    assert client.get(PREFIX + "/kb/bot-candidates").status_code == 403


def test_http_candidate_search_pagination(client):
    response = client.get(PREFIX + "/kb/bot-candidates?keyword=Stored%20owner&limit=1")
    assert response.status_code == 200
    assert response.json()["items"][0]["owner_name"] == "Stored owner"


@pytest.mark.parametrize(
    "suffix", ["", "/kb/catalog-detail", "/kb/authorized-bots", "/kb/bot-candidates"]
)
def test_http_requires_auth_before_backend(client, world, suffix):
    def unauthenticated():
        raise HTTPException(401)

    client.app.dependency_overrides[get_current_user] = unauthenticated
    assert client.get(PREFIX + suffix).status_code == 401
    assert world.catalog.calls == []


@pytest.mark.parametrize("method", ["put", "post", "delete"])
def test_no_write_endpoint_added(client, method):
    assert getattr(client, method)(PREFIX + "/kb/authorized-bots").status_code == 405


def test_http_missing_metadata_keeps_grant_null_enabled_and_cursor(client, world):
    from .conftest import grant

    world.catalog.grants = {"items": [grant("deleted")], "next_cursor": 17}
    response = client.get(PREFIX + "/kb/authorized-bots?limit=1")
    assert response.status_code == 200
    item = response.json()["items"][0]
    assert item["granted"] is True and item["metadata_missing"] is True
    assert item["enabled"] is None and item["bot_name"] is None
    assert response.json()["next_cursor"] == 17


@pytest.mark.parametrize(
    "remote_status,code",
    [
        (400, "KNOWLEDGE_REMOTE_INVALID_INPUT"),
        (403, "KNOWLEDGE_REMOTE_ACCESS_DENIED"),
        (404, "KNOWLEDGE_REMOTE_NOT_FOUND"),
        (409, "KNOWLEDGE_REMOTE_CONFLICT"),
        (410, "KNOWLEDGE_REMOTE_RETIRED"),
        (503, "KNOWLEDGE_DEPENDENCY_UNAVAILABLE"),
    ],
)
def test_http_maps_transport_semantic_error_codes_without_status_in_core(
    client, world, remote_status, code
):
    from types import SimpleNamespace

    from agentclaw.community.kernel.knowledge_management import KnowledgeManagementError

    def rejected(**kwargs):
        raise KnowledgeManagementError(code)

    world.service.catalog = SimpleNamespace(detail=rejected)
    response = client.get(PREFIX + "/kb/catalog-detail")
    assert response.status_code == remote_status
    assert response.json() == {"detail": {"code": code}}


def test_http_maps_core_invalid_input_to_bad_request(client):
    response = client.get("/api/knowledge/spaces/0/knowledge-bases")
    assert response.status_code == 400
    assert response.json() == {"detail": {"code": "INVALID_KNOWLEDGE_INPUT"}}


def test_http_maps_core_membership_denial_to_forbidden(client, world):
    def _callback():
        return AuthenticatedUser(id="SSO", staffId="stranger", operatorName="login")

    client.app.dependency_overrides[get_current_user] = _callback
    response = client.get(PREFIX)
    assert response.status_code == 403
    assert response.json() == {"detail": {"code": "SPACE_MEMBER_REQUIRED"}}
    assert world.catalog.calls == []


@pytest.mark.parametrize(
    "can_manage,denied", [(True, False), (False, False), (False, True)]
)
def test_unscoped_bot_directory_is_not_exposed(client, world, can_manage, denied):
    world.catalog.kb["can_manage"] = can_manage
    world.catalog.denied = denied
    world.sql.clear()
    response = client.get("/api/knowledge/spaces/1/bots?limit=100&keyword=bot")
    assert response.status_code == 404
    assert world.catalog.calls == []
    assert not any("ac_bots" in sql for sql in world.sql)


def test_candidates_require_kb_read_before_local_bot_query(client, world):
    world.catalog.denied = True
    world.sql.clear()
    response = client.get(PREFIX + "/kb/bot-candidates")
    assert response.status_code == 403
    assert not any("ac_bots" in sql for sql in world.sql)
