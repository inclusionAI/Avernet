"""Tests for skills router security fix (#110).

Verifies that update_skill_mcp_dependencies requires authentication
and uses authenticated user ID instead of request body user_id.
"""
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from fastapi_injector import attach_injector
from injector import Injector, Module

from agentclaw.community.adapters.http.auth.dependencies import get_current_user
from agentclaw.community.core.auth.models import AuthenticatedIdentity
from agentclaw.community.core.skill_center.factories import SkillServiceFactory


# --- Helpers ---

def _bind_skill_service_factory(mock_skill_service):
    """Bind a mock SkillServiceFactory that returns mock_skill_service."""
    mock_factory = MagicMock()
    mock_factory.create = MagicMock(return_value=mock_skill_service)

    class _M(Module):
        def configure(self, binder):
            from agentclaw.community.api.skill_service_factory import SkillServiceFactoryProtocol
            binder.bind(SkillServiceFactory, to=mock_factory)
            binder.bind(SkillServiceFactoryProtocol, to=mock_factory)
    return _M(), mock_factory


# --- Fixtures ---

@pytest.fixture
def user_a():
    return AuthenticatedIdentity(id="1", operatorName="user_a", outUserNo="100011", nickName="UserA")


@pytest.fixture
def mock_skill_service():
    svc = MagicMock()
    svc.get_skill = MagicMock(return_value={
        "id": 42,
        "name": "test_skill",
        "user_id": "100011",
    })
    svc.update_skill = MagicMock(return_value={
        "id": 42,
        "name": "test_skill",
        "description": "A test skill",
        "git_path": "git://test",
        "link_name": "test_skill",
        "category": "general",
        "tags": "[]",
        "risk_tags": [],
        "mcp_dependencies": [{"name": "mcp_server_1"}],
        "input_schema": None,
        "output_schema": None,
        "is_public": True,
        "is_builtin": False,
        "user_id": "100011",
        "bolt_id": "default",
        "gmt_created": "2026-01-01T00:00:00",
        "gmt_modified": "2026-01-01T00:00:00",
    })
    return svc


@pytest.fixture
def client(user_a, mock_skill_service):
    from agentclaw.community.adapters.http.skill_center.skills import router

    module, _ = _bind_skill_service_factory(mock_skill_service)
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user] = lambda: user_a
    attach_injector(app, Injector([module]))
    return TestClient(app)


@pytest.fixture
def client_no_auth(mock_skill_service):
    """Client without auth to simulate unauthenticated request."""
    from agentclaw.community.adapters.http.skill_center.skills import router

    module, _ = _bind_skill_service_factory(mock_skill_service)
    app = FastAPI()
    app.include_router(router)
    attach_injector(app, Injector([module]))
    # No get_current_user override — auth will fail
    return TestClient(app, raise_server_exceptions=False)


# --- Tests for #110: update_skill_mcp_dependencies auth + user_id enforcement ---

class TestUpdateSkillMcpDependenciesAuth:
    """POST /api/skills/{skill_id}/mcp-dependencies — auth and user_id enforcement."""

    def test_unauthenticated_request_rejected(self, client_no_auth):
        """Unauthenticated requests should be rejected."""
        resp = client_no_auth.post(
            "/api/skills/42/mcp-dependencies",
            json={"user_id": "100011", "mcp_dependencies": []},
        )
        assert resp.status_code in (401, 403) or resp.status_code >= 400

    def test_authenticated_request_succeeds(self, client, mock_skill_service):
        """Authenticated user can update their own skill's MCP dependencies."""
        resp = client.post(
            "/api/skills/42/mcp-dependencies",
            json={"user_id": "100011", "mcp_dependencies": [{"name": "mcp_server_1"}]},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True

    def test_uses_authenticated_user_id_not_request_body(self, client, mock_skill_service):
        """user_id from request body should be ignored; authenticated user's staffId used instead."""
        resp = client.post(
            "/api/skills/42/mcp-dependencies",
            json={
                "user_id": "100012",  # Attempt to impersonate another user
                "mcp_dependencies": [{"name": "mcp_server_1"}],
            },
        )
        assert resp.status_code == 200
        # Verify the service was called with the authenticated user's staffId, not the request body user_id
        call_kwargs = mock_skill_service.update_skill.call_args
        assert call_kwargs[1].get("user_id") == "100011" or call_kwargs[0][1] == "100011"

    def test_skill_not_found_returns_404(self, client, mock_skill_service):
        """Should return 404 if skill doesn't exist."""
        mock_skill_service.get_skill.return_value = None
        mock_skill_service.update_skill.return_value = None
        resp = client.post(
            "/api/skills/99999/mcp-dependencies",
            json={"user_id": "100011", "mcp_dependencies": []},
        )
        assert resp.status_code == 404