"""Tests for cron_noauth_router — 手动触发 autoInitiate。

覆盖场景:
1. 成功触发 → 200 + success=True
2. 通过 query 参数触发
3. ValueError → 400
4. 通用异常 → 500
5. nick_name 缺省时用 user_id 填充
"""
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from fastapi_injector import attach_injector
from injector import Injector, Module

from agentclaw.community.adapters.http.cron.cron_noauth_router import router
from agentclaw.community.api.cron_relay_service import CronRelayServiceProtocol


# --- Helpers ---

def _bind_services(mock_relay_service):
    """Bind mock CronRelayServiceProtocol via injector Module."""
    class _M(Module):
        def configure(self, binder):
            if mock_relay_service is not None:
                binder.bind(CronRelayServiceProtocol, to=mock_relay_service)
    return _M()


@pytest.fixture
def mock_relay_service():
    """Mock CronRelayServiceProtocol with default success response."""
    svc = MagicMock()
    svc.find_auto_initiate_and_run = AsyncMock(return_value={
        "success": True,
        "data": {"job_id": "auto-1", "bot_id": "bot-1", "bot_name": "TestBot"},
    })
    return svc


@pytest.fixture
def client(mock_relay_service):
    """TestClient with mocked relay service — no auth required."""
    app = FastAPI()
    app.include_router(router)
    attach_injector(app, Injector([_bind_services(mock_relay_service)]))
    return TestClient(app)


# --- Tests ---

class TestRunAutoInitiate:
    """POST /api/public/cron/auto-initiate/run"""

    def test_no_auth_required(self, client):
        """通过 query 参数即可触发。"""
        resp = client.post(
            "/api/public/cron/auto-initiate/run",
            params={"bot_id": "bot-1", "user_id": "user-1"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True

    def test_success(self, client, mock_relay_service):
        """成功触发 autoInitiate。"""
        resp = client.post(
            "/api/public/cron/auto-initiate/run",
            params={"bot_id": "bot-1", "user_id": "user-1"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["data"]["job_id"] == "auto-1"

        # 验证 relay service 被正确调用
        mock_relay_service.find_auto_initiate_and_run.assert_awaited_once_with(
            bot_id="bot-1",
            user_id="user-1",
            nick_name="user-1",  # 缺省用 user_id
            force=True,
        )

    def test_nick_name_provided(self, client, mock_relay_service):
        """显式传入 nick_name。"""
        resp = client.post(
            "/api/public/cron/auto-initiate/run",
            params={"bot_id": "bot-1", "user_id": "user-1", "nick_name": "张三"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True

        mock_relay_service.find_auto_initiate_and_run.assert_awaited_once_with(
            bot_id="bot-1",
            user_id="user-1",
            nick_name="张三",
            force=True,
        )

    def test_nick_name_empty_falls_back_to_user_id(self, client, mock_relay_service):
        """nick_name 为空字符串时用 user_id 填充。"""
        resp = client.post(
            "/api/public/cron/auto-initiate/run",
            params={"bot_id": "bot-1", "user_id": "user-1", "nick_name": ""},
        )
        assert resp.status_code == 200

        call_kwargs = mock_relay_service.find_auto_initiate_and_run.call_args[1]
        assert call_kwargs["nick_name"] == "user-1"

    def test_force_false(self, client, mock_relay_service):
        """force=false 传入。"""
        resp = client.post(
            "/api/public/cron/auto-initiate/run",
            params={"bot_id": "bot-1", "user_id": "user-1", "force": "false"},
        )
        assert resp.status_code == 200

        call_kwargs = mock_relay_service.find_auto_initiate_and_run.call_args[1]
        assert call_kwargs["force"] is False

    def test_value_error_returns_400(self, client, mock_relay_service):
        """ValueError → 400。"""
        mock_relay_service.find_auto_initiate_and_run.side_effect = ValueError(
            "No autoInitiate cron job found for bot bot-1"
        )

        resp = client.post(
            "/api/public/cron/auto-initiate/run",
            params={"bot_id": "bot-1", "user_id": "user-1"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is False
        assert data["error_code"] == 400
        assert "No autoInitiate" in data["message"]

    def test_generic_error_returns_500(self, client, mock_relay_service):
        """通用异常 → 500。"""
        mock_relay_service.find_auto_initiate_and_run.side_effect = Exception(
            "Unexpected error"
        )

        resp = client.post(
            "/api/public/cron/auto-initiate/run",
            params={"bot_id": "bot-1", "user_id": "user-1"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is False
        assert data["error_code"] == 500
        assert "Unexpected error" in data["message"]

    def test_missing_bot_id_returns_422(self, client):
        """缺少必填参数 bot_id → 422。"""
        resp = client.post(
            "/api/public/cron/auto-initiate/run",
            params={"user_id": "user-1"},
        )
        assert resp.status_code == 422

    def test_missing_user_id_returns_422(self, client):
        """缺少必填参数 user_id → 422。"""
        resp = client.post(
            "/api/public/cron/auto-initiate/run",
            params={"bot_id": "bot-1"},
        )
        assert resp.status_code == 422


class TestRunSingleAutoInitiate:
    """POST /api/public/cron/auto-initiate/run-single"""

    def test_success(self, client, mock_relay_service):
        """成功为单个需求发起会话。"""
        mock_relay_service.run_single_auto_initiate = AsyncMock(return_value={
            "success": True,
            "data": {"total": 1, "created": 1, "errors": []},
        })

        resp = client.post(
            "/api/public/cron/auto-initiate/run-single",
            params={
                "bot_id": "bot-1",
                "user_id": "user-1",
                "dima_url": "https://project.teamclaw.com/space/W26001121848/requirement?openWorkItemId=123",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["data"]["created"] == 1

        mock_relay_service.run_single_auto_initiate.assert_awaited_once_with(
            bot_id="bot-1",
            user_id="user-1",
            nick_name="user-1",
            dima_url="https://project.teamclaw.com/space/W26001121848/requirement?openWorkItemId=123",
            append_message="",
            model=None,
        )

    def test_with_append_message(self, client, mock_relay_service):
        """传入 append_message。"""
        mock_relay_service.run_single_auto_initiate = AsyncMock(return_value={
            "success": True,
            "data": {"total": 1, "created": 1, "errors": []},
        })

        resp = client.post(
            "/api/public/cron/auto-initiate/run-single",
            params={
                "bot_id": "bot-1",
                "user_id": "user-1",
                "dima_url": "https://project.teamclaw.com/space/W1/requirement?openWorkItemId=456",
                "append_message": "优先处理核心逻辑",
            },
        )
        assert resp.status_code == 200
        call_kwargs = mock_relay_service.run_single_auto_initiate.call_args[1]
        assert call_kwargs["append_message"] == "优先处理核心逻辑"

    def test_no_auth_required(self, client, mock_relay_service):
        """通过 query 参数即可触发。"""
        mock_relay_service.run_single_auto_initiate = AsyncMock(return_value={
            "success": True, "data": {},
        })

        resp = client.post(
            "/api/public/cron/auto-initiate/run-single",
            params={
                "bot_id": "bot-1",
                "user_id": "user-1",
                "dima_url": "https://project.teamclaw.com/space/W1/requirement?openWorkItemId=789",
            },
        )
        assert resp.status_code == 200

    def test_value_error_returns_400(self, client, mock_relay_service):
        """ValueError → 400。"""
        mock_relay_service.run_single_auto_initiate = AsyncMock(
            side_effect=ValueError("Bot bot-1 has no device binding")
        )

        resp = client.post(
            "/api/public/cron/auto-initiate/run-single",
            params={
                "bot_id": "bot-1",
                "user_id": "user-1",
                "dima_url": "https://project.teamclaw.com/space/W1/requirement?openWorkItemId=789",
            },
        )
        data = resp.json()
        assert data["success"] is False
        assert data["error_code"] == 400

    def test_missing_dima_url_returns_422(self, client):
        """缺少必填参数 dima_url → 422。"""
        resp = client.post(
            "/api/public/cron/auto-initiate/run-single",
            params={"bot_id": "bot-1", "user_id": "user-1"},
        )
        assert resp.status_code == 422
