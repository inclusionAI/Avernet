"""Tests for bot_public_noauth router.

Tests for the public endpoints:
- GET /api/public/bots/{bot_id}/appcoding-bots
- PATCH /api/public/bots/{bot_id}/ext

"""
import json
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from fastapi_injector import attach_injector
from injector import Injector, Module

from agentclaw.community.adapters.http.bot_public.public_noauth_router import (
    _scrub_sensitive,
    router,
)
from agentclaw.community.core.bot_management.services.bot_service import BotService
from agentclaw.community.core.repository.protocols.bot import BotRepository


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
            "ext": {
                "is_domain_bot": False,
                "arch_domain": "测试架构域",
                "iam_token": "secret-jwt-1",
                "token": "enc:v1:abc",
            },
            "template_config": {"architect_bot_id": "arch_001"},
        },
        {
            "id": 3,
            "bot_id": "app_bot_2",
            "bot_name": "App Coding Bot 2",
            "owner_id": "another_user",
            "status": "PENDING",
            "public": "0",
            "ext": '{"is_domain_bot": true, "iam_token": "secret-jwt-2", "token": "enc:v1:def"}',
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
    """TestClient with mocked services."""
    app = FastAPI()
    app.include_router(router)
    attach_injector(app, Injector([_bind_services(mock_bot_service, mock_bot_repo)]))
    return TestClient(app)


# --- Tests for GET /api/public/bots/{bot_id}/appcoding-bots ---

class TestListCodingBotsPublic:
    """GET /api/public/bots/{bot_id}/appcoding-bots."""

    def test_no_auth_required(self, client):
        """Should respond successfully on a basic request."""
        resp = client.get("/api/public/bots/arch_001/appcoding-bots")
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert len(data["data"]) == 2

    def test_returns_non_sensitive_fields(self, client):
        """非敏感字段保留返回。"""
        resp = client.get("/api/public/bots/arch_001/appcoding-bots")
        assert resp.status_code == 200
        items = resp.json().get("data", [])
        assert len(items) > 0

        first = items[0]
        for non_sensitive in (
            "id",
            "bot_id",
            "bot_name",
            "bot_desc",
            "status",
            "public",
            "entity_id",
            "entity_type",
            "owner_id",
            "owner_name",
            "engine_types",
            "active_engine",
            "ext",
            "template_config",
        ):
            assert non_sensitive in first, f"{non_sensitive} 应保留"

    def test_sensitive_fields_scrubbed_top_level(self, client):
        """顶层敏感字段必须被移除。"""
        resp = client.get("/api/public/bots/arch_001/appcoding-bots")
        assert resp.status_code == 200
        items = resp.json().get("data", [])
        assert len(items) > 0

        for sensitive in (
            "iam_token",
            "token",
            "device_id",
            "binding_id",
        ):
            for item in items:
                assert sensitive not in item, f"{sensitive} 不得出现在响应中"

    def test_sensitive_fields_scrubbed_in_ext(self, client):
        """ext 内的敏感字段必须被递归移除（含 JSON 字符串 ext）。"""
        resp = client.get("/api/public/bots/arch_001/appcoding-bots")
        assert resp.status_code == 200
        items = resp.json().get("data", [])

        first = items[0]
        assert isinstance(first["ext"], dict)
        assert "iam_token" not in first["ext"]
        assert "token" not in first["ext"]
        # 非敏感 ext 字段保留
        assert first["ext"]["arch_domain"] == "测试架构域"

        # 第二个 bot 的 ext 是 JSON 字符串，机密字段需在解码层被移除
        second = items[1]
        assert isinstance(second["ext"], str)
        decoded = json.loads(second["ext"])
        assert "iam_token" not in decoded
        assert "token" not in decoded
        assert decoded["is_domain_bot"] is True

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


# --- Unit tests for _scrub_sensitive (security regression) ---

class TestScrubSensitiveUnit:
    """直接覆盖 _scrub_sensitive；重点回归"JSON 字符串带前导空白绕过脱敏"。"""

    def test_dict_top_level_sensitive_removed(self):
        assert _scrub_sensitive({"iam_token": "x", "keep": 1}) == {"keep": 1}

    def test_nested_dict_sensitive_removed(self):
        assert _scrub_sensitive({"a": {"token": "t", "b": 2}}) == {"a": {"b": 2}}

    def test_json_string_without_whitespace_scrubbed(self):
        s = '{"iam_token": "t", "keep": 1}'
        out = _scrub_sensitive(s)
        assert isinstance(out, str)
        dec = json.loads(out)
        assert "iam_token" not in dec
        assert dec["keep"] == 1

    def test_json_string_with_leading_whitespace_scrubbed(self):
        """regression: JSON 字符串带前导空白不得绕过脱敏。"""
        s = '  \n {"iam_token": "t", "token": "enc:v1:x", "keep": 1}'
        out = _scrub_sensitive(s)
        assert isinstance(out, str)
        dec = json.loads(out)
        assert "iam_token" not in dec
        assert "token" not in dec
        assert dec["keep"] == 1

    def test_non_json_string_returned_as_is(self):
        assert _scrub_sensitive("enc:v1:plain-token") == "enc:v1:plain-token"

    def test_tuple_handled_as_sequence(self):
        out = _scrub_sensitive(({"token": "t"}, {"keep": 1}))
        assert isinstance(out, list)
        assert out == [{}, {"keep": 1}]

    def test_list_scrubbed_recursively(self):
        assert _scrub_sensitive([{"token": "t"}, [{"iam_token": "i"}]]) == [{}, [{}]]


# --- Tests for PATCH /api/public/bots/{bot_id}/ext ---

class TestUpdateBotExtPublic:
    """PATCH /api/public/bots/{bot_id}/ext — whitelist enforced."""

    def test_no_auth_required(self, client):
        """Should respond successfully on a basic PATCH."""
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
        assert updated_ext["is_domain_bot"] is False  # Preserved

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


# --- Comparison tests: field structure parity ---

class TestNoAuthVsAuthParity:
    """Ensure endpoints return expected data format."""

    def test_appcoding_bots_field_structure_matches(
        self, client, mock_bot_service
    ):
        """Fields in response should match expected structure."""
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
