"""Tests for bot_public_noauth router.

Tests for the no-auth endpoints:
- GET /api/public/bots/{bot_id}/appcoding-bots
- PATCH /api/public/bots/{bot_id}/ext

These endpoints should work without any authentication.
"""
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from fastapi_injector import attach_injector
from injector import Injector, Module

from agentclaw.community.adapters.http.bot_public.public_noauth_router import router
from agentclaw.community.core.bot_management.services.bot_service import BotService
from agentclaw.community.core.bot_management.repository.protocol import BotRepository


# --- Helpers ---

def _bind_services(mock_bot_service=None, mock_bot_repo=None):
    """Bind mock services via injector Module."""
    class _M(Module):
        def configure(self, binder):
            if mock_bot_service is not None:
                binder.bind(BotService, to=mock_bot_service)
                from agentclaw.community.api.bot_service import BotServiceProtocol
                binder.bind(BotServiceProtocol, to=mock_bot_service)
            if mock_bot_repo is not None:
                binder.bind(BotRepository, to=mock_bot_repo)
    return _M()


@pytest.fixture
def mock_bot_service():
    """Mock BotService with sample coding bots data."""
    svc = MagicMock()
    svc.list_coding_bots_by_architect = MagicMock(return_value=[
        {
            "id": 2,
            "bot_id": "app_bot_1",
            "bot_name": "App Coding Bot 1",
            "bot_desc": "A coding bot",
            "owner_id": "test_user",
            "owner_name": "Test User",
            "status": "ACTIVE",
            "public": "1",
            "entity_id": "test_user",
            "entity_type": "staff",
            "creator_id": "test_user",
            "modifier_id": None,
            "engine_types": ["openclaw"],
            "active_engine": "openclaw",
            "binding_id": 1001,
            "device_id": "device_001",
            "is_delete": 0,
            "env": "dev",
            "bot_type": "personal",
            "template_type": "applicationCoding",
            "gmt_create": "2026-01-01T00:00:00",
            "gmt_modified": "2026-01-01T00:00:00",
            "share_policy": None,
            "ext": {"is_domain_bot": False, "arch_domain": "测试架构域"},
            "template_config": {"architect_bot_id": "arch_001"},
        },
        {
            "id": 3,
            "bot_id": "app_bot_2",
            "bot_name": "App Coding Bot 2",
            "owner_id": "another_user",
            "status": "PENDING",
            "public": "0",
            "ext": {"is_domain_bot": True},
        },
    ])
    return svc


@pytest.fixture
def mock_bot_repo():
    """Mock BotRepository for ext update tests."""
    repo = MagicMock()
    # Mock list_by_conditions to return a bot
    repo.list_by_conditions = MagicMock(return_value=(
        1,
        [{
            "id": 1,
            "bot_id": "default",
            "owner_id": "test_owner",
            "ext": {"existing_key": "existing_value"},
        }]
    ))
    repo.update_by_owner = MagicMock(return_value={"id": 1, "bot_id": "default"})
    return repo


@pytest.fixture
def client(mock_bot_service, mock_bot_repo):
    """TestClient with mocked services - no auth required."""
    app = FastAPI()
    app.include_router(router)
    attach_injector(app, Injector([_bind_services(mock_bot_service, mock_bot_repo)]))
    return TestClient(app)


# --- Tests for GET /api/public/bots/{bot_id}/appcoding-bots ---

class TestListCodingBotsPublic:
    """GET /api/public/bots/{bot_id}/appcoding-bots — no auth required."""

    def test_no_auth_required(self, client):
        """Should work without any authentication headers or cookies."""
        resp = client.get("/api/public/bots/arch_001/appcoding-bots")
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert len(data["data"]) == 2

    def test_returns_full_fields(self, client):
        """Should return all bot fields without filtering."""
        resp = client.get("/api/public/bots/arch_001/appcoding-bots")
        assert resp.status_code == 200
        data = resp.json()
        items = data.get("data", [])
        assert len(items) > 0

        first = items[0]
        # Check that all fields are present
        assert "id" in first
        assert "bot_id" in first
        assert "bot_name" in first
        assert "bot_desc" in first
        assert "owner_id" in first
        assert "owner_name" in first
        assert "status" in first
        assert "public" in first
        assert "entity_id" in first
        assert "entity_type" in first
        assert "engine_types" in first
        assert "active_engine" in first
        assert "binding_id" in first
        assert "device_id" in first
        assert "ext" in first
        assert "template_config" in first

    def test_returns_ext_with_arch_domain(self, client):
        """Should return ext field containing arch_domain and is_domain_bot."""
        resp = client.get("/api/public/bots/arch_001/appcoding-bots")
        assert resp.status_code == 200
        data = resp.json()
        items = data.get("data", [])
        assert len(items) > 0

        first = items[0]
        ext = first.get("ext", {})
        assert "arch_domain" in ext or "is_domain_bot" in ext

    def test_service_error_handled(self, client, mock_bot_service):
        """Should handle service errors gracefully."""
        from agentclaw.community.core.bot_management.services.bot_service import BotServiceError
        mock_bot_service.list_coding_bots_by_architect.side_effect = BotServiceError("Database error")

        resp = client.get("/api/public/bots/arch_001/appcoding-bots")
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is False
        assert data["error_code"] == 500

    def test_unexpected_error_handled(self, client, mock_bot_service):
        """Should handle unexpected errors gracefully."""
        mock_bot_service.list_coding_bots_by_architect.side_effect = Exception("Unexpected error")

        resp = client.get("/api/public/bots/arch_001/appcoding-bots")
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is False
        assert data["error_code"] == 500


# --- Tests for PATCH /api/public/bots/{bot_id}/ext ---

class TestUpdateBotExtPublic:
    """PATCH /api/public/bots/{bot_id}/ext — no auth required, whitelist enforced."""

    def test_no_auth_required(self, client):
        """Should work without any authentication."""
        resp = client.patch(
            "/api/public/bots/default/ext",
            json={"is_domain_bot": True, "arch_domain": "新架构域"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True

    def test_whitelist_fields_accepted(self, client, mock_bot_repo):
        """Should accept whitelisted fields."""
        resp = client.patch(
            "/api/public/bots/default/ext",
            json={"is_domain_bot": True, "arch_domain": "盼祺架构域"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert "is_domain_bot" in data["data"]["updated_fields"]
        assert "arch_domain" in data["data"]["updated_fields"]

    def test_non_whitelist_fields_rejected(self, client):
        """Should reject non-whitelisted fields."""
        resp = client.patch(
            "/api/public/bots/default/ext",
            json={"unauthorized_field": "value", "another_bad_field": "value"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is False
        assert data["error_code"] == 400
        assert "allowed_fields" in data["data"]

    def test_mixed_whitelist_and_non_whitelist(self, client, mock_bot_repo):
        """Should filter out non-whitelisted fields and only update allowed ones."""
        resp = client.patch(
            "/api/public/bots/default/ext",
            json={
                "is_domain_bot": True,
                "unauthorized_field": "should_be_filtered",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        # Only is_domain_bot should be in updated fields
        assert "is_domain_bot" in data["data"]["updated_fields"]
        assert "unauthorized_field" not in data["data"]["updated_fields"]

    def test_bot_not_found(self, client, mock_bot_repo):
        """Should return 404 when bot not found."""
        mock_bot_repo.list_by_conditions.return_value = (0, [])

        resp = client.patch(
            "/api/public/bots/nonexistent/ext",
            json={"is_domain_bot": True},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is False
        assert data["error_code"] == 404

    def test_invalid_body_not_dict(self, client):
        """Should reject non-dict request body."""
        resp = client.patch(
            "/api/public/bots/default/ext",
            json=["not", "a", "dict"],
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is False
        assert data["error_code"] == 400
        assert "JSON 对象" in data["message"]

    def test_ext_merge_with_existing(self, client, mock_bot_repo):
        """Should merge new ext fields with existing ext."""
        # Setup: existing ext has some fields
        mock_bot_repo.list_by_conditions.return_value = (
            1,
            [{
                "id": 1,
                "bot_id": "default",
                "owner_id": "test_owner",
                "ext": {"existing_key": "existing_value", "is_domain_bot": False},
            }]
        )

        resp = client.patch(
            "/api/public/bots/default/ext",
            json={"arch_domain": "新架构域"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True

        # Verify update_by_owner was called with merged ext
        call_args = mock_bot_repo.update_by_owner.call_args
        updated_ext = call_args[0][2]["ext"]
        assert updated_ext["existing_key"] == "existing_value"  # Preserved
        assert updated_ext["arch_domain"] == "新架构域"  # New field added
        assert updated_ext["is_domain_bot"] == False  # Preserved

    def test_error_handled(self, client, mock_bot_repo):
        """Should handle repository errors gracefully."""
        mock_bot_repo.list_by_conditions.side_effect = Exception("Database error")

        resp = client.patch(
            "/api/public/bots/default/ext",
            json={"is_domain_bot": True},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is False
        assert data["error_code"] == 500


# --- Comparison tests: ensure no-auth and auth versions return similar data ---

class TestNoAuthVsAuthParity:
    """Ensure no-auth endpoints return same data format as auth versions."""

    def test_appcoding_bots_field_structure_matches(
        self, client, mock_bot_service
    ):
        """Fields in no-auth response should match auth version structure."""
        resp = client.get("/api/public/bots/arch_001/appcoding-bots")
        assert resp.status_code == 200
        data = resp.json()

        assert data["success"] is True
        assert "data" in data
        if len(data["data"]) > 0:
            item = data["data"][0]
            # Verify common fields exist
            assert "bot_id" in item
            assert "bot_name" in item
            assert "status" in item
            assert "ext" in item
