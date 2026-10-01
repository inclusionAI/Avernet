"""Tests for Bot Render Screen API router."""
from datetime import datetime
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agentclaw.community.core.bot_management.render_screen.models import RenderScreenRecord


def _record(**overrides) -> RenderScreenRecord:
    defaults = dict(
        id=1,
        bot_id="bot_001",
        owner_id="testuser",
        name="数据看板",
        cdn_url="https://cdn.example.com/v1/index.js",
        env="dev",
        creator_id="testuser",
        is_delete=0,
        gmt_create=datetime(2026, 5, 12, 10, 0, 0),
        gmt_modified=datetime(2026, 5, 12, 10, 0, 0),
    )
    defaults.update(overrides)
    return RenderScreenRecord(**defaults)


@pytest.fixture
def mock_service():
    service = MagicMock()
    service.list_render_screens = MagicMock(return_value=[])
    service.create_render_screen = MagicMock(return_value=1)
    service.update_render_screen = MagicMock(return_value=None)
    service.delete_render_screen = MagicMock(return_value=None)
    service.get_render_screen = MagicMock(return_value=None)
    service.authorize_render_screen_bot = MagicMock(return_value="owner")
    service.authorize_render_screen_record = MagicMock(return_value=_record())
    return service


@pytest.fixture
def client(mock_service):
    from agentclaw.community.adapters.http.bot_render_screen.router import router
    from agentclaw.community.adapters.http.bot_render_screen.dependencies import get_render_screen_service
    from agentclaw.community.adapters.http.auth.dependencies import get_current_user
    from agentclaw.community.core.auth.models import AuthenticatedIdentity

    app = FastAPI()
    app.include_router(router)

    mock_user = MagicMock(spec=AuthenticatedIdentity)
    mock_user.staffId = "testuser"
    mock_user.operatorName = "Test User"
    mock_user.nickName = "Test User"

    app.dependency_overrides[get_render_screen_service] = lambda: mock_service
    app.dependency_overrides[get_current_user] = lambda: mock_user

    return TestClient(app)


class TestListRenderScreens:
    def test_list_empty(self, client, mock_service):
        mock_service.list_render_screens.return_value = []
        resp = client.get("/api/bot-render-screens?bot_id=bot_001")
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["data"] == []
        # 读放开：list 不再走 bot 级鉴权，写操作仍校验
        mock_service.authorize_render_screen_bot.assert_not_called()
        mock_service.list_render_screens.assert_called_once_with(
            bot_id="bot_001",
            owner_id=None,
            current_user_id="testuser",
        )

    def test_list_returns_records(self, client, mock_service):
        mock_service.list_render_screens.return_value = [
            _record(id=1, name="看板1"),
            _record(id=2, name="看板2"),
        ]
        resp = client.get("/api/bot-render-screens?bot_id=bot_001")
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert len(data["data"]) == 2
        assert data["data"][0]["name"] == "看板1"
        mock_service.list_render_screens.assert_called_once_with(
            bot_id="bot_001",
            owner_id=None,
            current_user_id="testuser",
        )

    def test_list_with_owner_id_keeps_compatibility(self, client, mock_service):
        mock_service.list_render_screens.return_value = [_record(owner_id="botowner")]
        resp = client.get(
            "/api/bot-render-screens?bot_id=bot_001&owner_id=botowner"
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert len(data["data"]) == 1
        mock_service.list_render_screens.assert_called_once_with(
            bot_id="bot_001",
            owner_id="botowner",
            current_user_id="testuser",
        )


class TestCreateRenderScreen:
    def test_create_success(self, client, mock_service):
        mock_service.create_render_screen.return_value = 42
        resp = client.post(
            "/api/bot-render-screens",
            json={"bot_id": "bot_001", "name": "数据看板", "cdn_url": "https://cdn.example.com/v1/index.js"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["data"]["id"] == 42
        mock_service.authorize_render_screen_bot.assert_called_once_with(bot_id="bot_001", user_id="testuser")
        mock_service.create_render_screen.assert_called_once_with(
            bot_id="bot_001", owner_id="testuser",
            name="数据看板", cdn_url="https://cdn.example.com/v1/index.js",
            creator_id="testuser", current_user_id="testuser",
        )

    def test_create_duplicate_name_returns_error_code_409(self, client, mock_service):
        mock_service.create_render_screen.side_effect = ValueError("Duplicate name")
        resp = client.post(
            "/api/bot-render-screens",
            json={"bot_id": "bot_001", "name": "重复名", "cdn_url": "https://cdn.example.com/v1/index.js"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is False
        assert data["error_code"] == 409

    def test_create_duplicate_cdn_url_returns_error_code_409(self, client, mock_service):
        mock_service.create_render_screen.side_effect = ValueError("Duplicate cdn_url")
        resp = client.post(
            "/api/bot-render-screens",
            json={"bot_id": "bot_001", "name": "不同名称", "cdn_url": "https://cdn.example.com/v1/index.js"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is False
        assert data["error_code"] == 409


class TestUpdateRenderScreen:
    def test_update_success(self, client, mock_service):
        mock_service.authorize_render_screen_record.return_value = _record(id=1, owner_id="testuser")
        resp = client.put(
            "/api/bot-render-screens/1",
            json={"name": "新名称", "cdn_url": "https://new.url"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        mock_service.authorize_render_screen_record.assert_called_once_with(record_id=1, user_id="testuser")
        mock_service.update_render_screen.assert_called_once_with(
            record_id=1, name="新名称", cdn_url="https://new.url",
        )

    def test_update_not_found_returns_error_code_404(self, client, mock_service):
        mock_service.authorize_render_screen_record.side_effect = ValueError("RenderScreen not found")
        resp = client.put(
            "/api/bot-render-screens/999",
            json={"name": "x", "cdn_url": "https://x.url"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is False
        assert data["error_code"] == 404

    def test_update_forbidden_returns_error_code_403(self, client, mock_service):
        mock_service.authorize_render_screen_record.side_effect = PermissionError("无权操作此 Bot 的 CDN 配置")
        resp = client.put(
            "/api/bot-render-screens/1",
            json={"name": "hack", "cdn_url": "https://evil.url"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is False
        assert data["error_code"] == 403


class TestDeleteRenderScreen:
    def test_delete_success(self, client, mock_service):
        mock_service.authorize_render_screen_record.return_value = _record(id=1, owner_id="testuser")
        resp = client.delete("/api/bot-render-screens/1")
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        mock_service.authorize_render_screen_record.assert_called_once_with(record_id=1, user_id="testuser")
        mock_service.delete_render_screen.assert_called_once_with(record_id=1)

    def test_delete_not_found_returns_error_code_404(self, client, mock_service):
        mock_service.authorize_render_screen_record.side_effect = ValueError("RenderScreen not found")
        resp = client.delete("/api/bot-render-screens/999")
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is False
        assert data["error_code"] == 404

    def test_delete_forbidden_returns_error_code_403(self, client, mock_service):
        mock_service.authorize_render_screen_record.side_effect = PermissionError("无权操作此 Bot 的 CDN 配置")
        resp = client.delete("/api/bot-render-screens/1")
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is False
        assert data["error_code"] == 403
