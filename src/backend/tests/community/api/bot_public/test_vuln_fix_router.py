"""Tests for bot_public router security fix (#108).

Verifies that search_bots_by_conditions requires authentication
and filters sensitive fields from response.
"""
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from fastapi_injector import attach_injector
from injector import Injector, Module

from agentclaw.community.adapters.http.auth.dependencies import get_current_user
from agentclaw.community.core.auth.models import AuthenticatedIdentity
from agentclaw.community.core.bot_management.services.bot_service import BotService
from agentclaw.community.core.bot_public.services.bot_public_service import BotPublicService


# --- Helpers ---

def _bind_services(mock_bot_service, mock_bot_public_service=None):
    """Bind mock services via injector Module."""
    class _M(Module):
        def configure(self, binder):
            binder.bind(BotService, to=mock_bot_service)
            from agentclaw.community.api.bot_service import BotServiceProtocol
            binder.bind(BotServiceProtocol, to=mock_bot_service)
            if mock_bot_public_service is not None:
                binder.bind(BotPublicService, to=mock_bot_public_service)
                from agentclaw.community.api.bot_public_service import BotPublicServiceProtocol
                binder.bind(BotPublicServiceProtocol, to=mock_bot_public_service)
    return _M()


# --- Fixtures ---

@pytest.fixture
def user_a():
    return AuthenticatedIdentity(id="1", operatorName="user_a", outUserNo="100011", nickName="UserA")


@pytest.fixture
def mock_bot_service():
    svc = MagicMock()
    svc.list_bots_by_conditions = MagicMock(return_value={
        "total": 2,
        "items": [
            {
                "id": 1,
                "bot_id": "bot_001",
                "bot_name": "Public Bot",
                "bot_desc": "A public bot",
                "entity_id": "100012",
                "entity_type": "staff",
                "creator_id": "100012",
                "owner_id": "100012",
                "owner_name": "贤睦",
                "status": "ACTIVE",
                "binding_id": "200001",
                "device_id": "staff_100012_default_abc",
                "public": "1",
                "engine_types": ["openclaw"],
                "active_engine": "openclaw",
                "env": "dev",
                "bot_type": "personal",
                "gmt_create": "2026-01-01T00:00:00",
                "gmt_modified": "2026-01-01T00:00:00",
                "modifier_id": None,
                "share_policy": None,
                "is_delete": 0,
                "ext": None,
            },
            {
                "id": 2,
                "bot_id": "bot_002",
                "bot_name": "Private Bot",
                "bot_desc": "A private bot",
                "entity_id": "285382",
                "entity_type": "staff",
                "creator_id": "285382",
                "owner_id": "285382",
                "owner_name": "张三",
                "status": "ACTIVE",
                "binding_id": "200002",
                "device_id": "staff_285382_default_xyz",
                "public": "0",
                "engine_types": ["openclaw"],
                "active_engine": "openclaw",
                "env": "dev",
                "bot_type": "personal",
                "gmt_create": "2026-01-01T00:00:00",
                "gmt_modified": "2026-01-01T00:00:00",
                "modifier_id": None,
                "share_policy": None,
                "is_delete": 0,
                "ext": None,
            },
        ],
    })
    return svc


@pytest.fixture
def mock_bot_public_service():
    return MagicMock()


@pytest.fixture
def client(user_a, mock_bot_service, mock_bot_public_service):
    from agentclaw.community.adapters.http.bot_public.router import router

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user] = lambda: user_a
    attach_injector(app, Injector([_bind_services(mock_bot_service, mock_bot_public_service)]))
    return TestClient(app)


@pytest.fixture
def client_no_auth(mock_bot_service, mock_bot_public_service):
    """Client without auth to simulate unauthenticated request."""
    from agentclaw.community.adapters.http.bot_public.router import router

    app = FastAPI()
    app.include_router(router)
    attach_injector(app, Injector([_bind_services(mock_bot_service, mock_bot_public_service)]))
    # No get_current_user override — auth will fail
    return TestClient(app, raise_server_exceptions=False)


# --- Tests for #108: search_bots_by_conditions auth + field filtering ---

class TestSearchBotsByConditionsAuth:
    """GET /api/bots/search/by-conditions — auth and field filtering."""

    def test_unauthenticated_request_rejected(self, client_no_auth):
        """Unauthenticated requests should be rejected."""
        resp = client_no_auth.get("/api/bots/search/by-conditions")
        assert resp.status_code in (401, 403) or resp.status_code >= 400

    def test_authenticated_request_succeeds(self, client):
        """Authenticated requests should succeed."""
        resp = client.get("/api/bots/search/by-conditions", params={"public": "1"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True

    def test_sensitive_fields_filtered(self, client, mock_bot_service):
        """Sensitive fields should be removed from response items."""
        resp = client.get("/api/bots/search/by-conditions", params={"public": "1"})
        assert resp.status_code == 200
        data = resp.json()
        items = data.get("data", {}).get("items", [])
        assert len(items) > 0

        # Whitelist excludes these sensitive fields
        sensitive_fields = {"binding_id", "device_id", "ext"}
        for item in items:
            for field in sensitive_fields:
                assert field not in item, f"Sensitive field '{field}' should be filtered out"

    def test_default_queries_public_bots_only(self, client, mock_bot_service):
        """Without explicit public param, should default to public=1."""
        resp = client.get("/api/bots/search/by-conditions")
        assert resp.status_code == 200
        # Verify the service was called with public="1" (default)
        call_args = mock_bot_service.list_bots_by_conditions.call_args
        assert call_args[1].get("public") == "1" or call_args[0][0] == "1"

    def test_non_sensitive_fields_preserved(self, client, mock_bot_service):
        """Non-sensitive fields should still be present."""
        resp = client.get("/api/bots/search/by-conditions", params={"public": "1"})
        assert resp.status_code == 200
        data = resp.json()
        items = data.get("data", {}).get("items", [])
        assert len(items) > 0

        # These fields should remain
        assert "id" in items[0]
        assert "bot_id" in items[0]
        assert "bot_name" in items[0]
        assert "status" in items[0]
        assert "public" in items[0]