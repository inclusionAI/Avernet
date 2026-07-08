"""Tests for skill_auth API router."""
from contextlib import contextmanager
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from fastapi_injector import attach_injector
from injector import Injector, Module

from agentclaw.community.adapters.http.dependencies import RequestContext, get_request_context
from agentclaw.community.adapters.http.skill_center.skill_auth import router as skill_auth_router
from agentclaw.community.adapters.http.skill_center.schemas import (
    SkillPermissionResponse,
    ApplySkillPermissionResponse,
    SkillSetPermissionResponse,
    ApplySkillSetPermissionResponse,
    BotPermissionResponse,
    ApplyBotPermissionResponse,
)
from agentclaw.community.core.skill_center.services.skill_auth_service import SkillAuthService


@pytest.fixture
def mock_ctx():
    ctx = RequestContext(user_id="user_001", bot_id="default")
    return ctx


@pytest.fixture
def mock_svc():
    """Shared MagicMock service. Tests configure return_value/side_effect on it."""
    return MagicMock(spec=SkillAuthService)


@pytest.fixture
def client(mock_ctx, mock_svc):
    app = FastAPI()
    app.include_router(skill_auth_router)
    app.dependency_overrides[get_request_context] = lambda: mock_ctx

    class _TestModule(Module):
        def configure(self, binder):
            from agentclaw.community.api.skill_auth_service import SkillAuthServiceProtocol
            binder.bind(SkillAuthService, to=mock_svc)
            binder.bind(SkillAuthServiceProtocol, to=mock_svc)

    injector = Injector([_TestModule()])
    attach_injector(app, injector)
    return TestClient(app, raise_server_exceptions=False)


@contextmanager
def _patch_svc(mock_svc):
    """Backward-compat shim: yields an object whose .return_value is mock_svc."""
    class _Stub:
        return_value = mock_svc
    yield _Stub


# ==================== check_skill_permission ====================

class TestCheckSkillPermission:
    def test_success(self, client, mock_svc):
        result = SkillPermissionResponse(
            skill_id="sk1", skill_name="skill one", authorized=True, mcp_details={}
        )
        if True:
            mock_svc.check_skill_permission.return_value = result
            resp = client.get("/api/skill/permission?skill_id=sk1")
        assert resp.status_code == 200

    def test_value_error_returns_404(self, client, mock_svc):
        if True:
            mock_svc.check_skill_permission.side_effect = ValueError("not found")
            resp = client.get("/api/skill/permission?skill_id=sk_missing")
        assert resp.status_code == 404
        assert "not found" in resp.json()["detail"]

    def test_unexpected_error_returns_500(self, client, mock_svc):
        if True:
            mock_svc.check_skill_permission.side_effect = RuntimeError("boom")
            resp = client.get("/api/skill/permission?skill_id=sk1")
        assert resp.status_code == 500

    def test_missing_skill_id_returns_422(self, client):
        resp = client.get("/api/skill/permission")
        assert resp.status_code == 422


# ==================== apply_skill_permission ====================

class TestApplySkillPermission:
    def test_success(self, client, mock_svc):
        result = ApplySkillPermissionResponse(
            skill_id="sk1", skill_name="skill one", all_authorized=True, apply_results={}
        )
        if True:
            mock_svc.apply_skill_permission.return_value = result
            resp = client.post(
                "/api/skill/permission/apply",
                json={"skill_id": "sk1", "reason": "testing"}
            )
        assert resp.status_code == 200

    def test_value_error_returns_404(self, client, mock_svc):
        if True:
            mock_svc.apply_skill_permission.side_effect = ValueError("missing skill")
            resp = client.post(
                "/api/skill/permission/apply",
                json={"skill_id": "sk_missing", "reason": "testing"}
            )
        assert resp.status_code == 404

    def test_unexpected_error_returns_500(self, client, mock_svc):
        if True:
            mock_svc.apply_skill_permission.side_effect = RuntimeError("db error")
            resp = client.post(
                "/api/skill/permission/apply",
                json={"skill_id": "sk1", "reason": "testing"}
            )
        assert resp.status_code == 500


# ==================== check_skill_set_permission ====================

class TestCheckSkillSetPermission:
    def test_success(self, client, mock_svc):
        result = SkillSetPermissionResponse(
            skill_set_id="ss1", authorized=True, skills={}, mcp_summary={}
        )
        if True:
            mock_svc.check_skill_set_permission.return_value = result
            resp = client.get("/api/skill/set/permission?skill_set_id=ss1")
        assert resp.status_code == 200

    def test_not_found(self, client, mock_svc):
        if True:
            mock_svc.check_skill_set_permission.side_effect = ValueError("not found")
            resp = client.get("/api/skill/set/permission?skill_set_id=ss_missing")
        assert resp.status_code == 404

    def test_missing_param_returns_422(self, client):
        resp = client.get("/api/skill/set/permission")
        assert resp.status_code == 422


# ==================== apply_skill_set_permission ====================

class TestApplySkillSetPermission:
    def test_success(self, client, mock_svc):
        result = ApplySkillSetPermissionResponse(
            skill_set_id="ss1", all_authorized=True, skills={}, apply_results={}
        )
        if True:
            mock_svc.apply_skill_set_permission.return_value = result
            resp = client.post(
                "/api/skill/set/permission/apply",
                json={"skill_set_id": "ss1", "reason": "testing"}
            )
        assert resp.status_code == 200

    def test_error_500(self, client, mock_svc):
        if True:
            mock_svc.apply_skill_set_permission.side_effect = RuntimeError("fail")
            resp = client.post(
                "/api/skill/set/permission/apply",
                json={"skill_set_id": "ss1", "reason": "testing"}
            )
        assert resp.status_code == 500


# ==================== check_bot_permission ====================

class TestCheckBotPermission:
    def test_success(self, client, mock_svc):
        result = BotPermissionResponse(bot_id="bot1", authorized=True, mcp_details={})
        if True:
            mock_svc.check_bot_permission.return_value = result
            resp = client.get("/api/skill/bot/permission?bot_id=bot1")
        assert resp.status_code == 200

    def test_not_found(self, client, mock_svc):
        if True:
            mock_svc.check_bot_permission.side_effect = ValueError("bot not found")
            resp = client.get("/api/skill/bot/permission?bot_id=bot_missing")
        assert resp.status_code == 404

    def test_error_500(self, client, mock_svc):
        if True:
            mock_svc.check_bot_permission.side_effect = RuntimeError("crash")
            resp = client.get("/api/skill/bot/permission?bot_id=bot1")
        assert resp.status_code == 500


# ==================== apply_bot_permission ====================

class TestApplyBotPermission:
    def test_success(self, client, mock_svc):
        result = ApplyBotPermissionResponse(bot_id="bot1", all_authorized=True, apply_results={})
        if True:
            mock_svc.apply_bot_permission.return_value = result
            resp = client.post(
                "/api/skill/bot/permission/apply",
                json={"bot_id": "bot1", "reason": "testing"}
            )
        assert resp.status_code == 200

    def test_not_found(self, client, mock_svc):
        if True:
            mock_svc.apply_bot_permission.side_effect = ValueError("no bot")
            resp = client.post(
                "/api/skill/bot/permission/apply",
                json={"bot_id": "bot_missing", "reason": "testing"}
            )
        assert resp.status_code == 404

    def test_error_500(self, client, mock_svc):
        if True:
            mock_svc.apply_bot_permission.side_effect = Exception("unexpected")
            resp = client.post(
                "/api/skill/bot/permission/apply",
                json={"bot_id": "bot1", "reason": "testing"}
            )
        assert resp.status_code == 500
