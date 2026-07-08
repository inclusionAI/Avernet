"""Unit tests for RenderScreenService — mock repository layer."""
from datetime import datetime
from unittest.mock import MagicMock

import pytest

from agentclaw.community.core.bot_management.render_screen.models import RenderScreenRecord
from agentclaw.community.core.bot_management.render_screen.services.render_screen_service import RenderScreenService


def _record(**overrides) -> RenderScreenRecord:
    defaults = dict(
        id=1,
        bot_id="bot_001",
        owner_id="user_001",
        name="数据看板",
        cdn_url="https://cdn.example.com/v1/index.js",
        env="dev",
        creator_id="user_001",
        is_delete=0,
        gmt_create=datetime(2026, 5, 12, 10, 0, 0),
        gmt_modified=datetime(2026, 5, 12, 10, 0, 0),
    )
    defaults.update(overrides)
    return RenderScreenRecord(**defaults)


@pytest.fixture
def mock_repo():
    repo = MagicMock()
    return repo


@pytest.fixture
def service(mock_repo):
    return RenderScreenService(repository=mock_repo)


class TestListRenderScreens:
    def test_returns_records(self, service, mock_repo):
        mock_repo.list_by_bot_id.return_value = [
            _record(id=1, name="看板1"),
            _record(id=2, name="看板2"),
        ]
        result = service.list_render_screens(bot_id="bot_001", owner_id="user_001")
        assert len(result) == 2
        mock_repo.list_by_bot_id.assert_called_once_with(bot_id="bot_001", owner_id="user_001")

    def test_returns_empty(self, service, mock_repo):
        mock_repo.list_by_bot_id.return_value = []
        result = service.list_render_screens(bot_id="bot_empty", owner_id="user_empty")
        assert result == []

    def test_default_bot_isolates_by_owner(self, service, mock_repo):
        """不同用户共享 bot_id='default' 时，需按 owner_id 隔离。"""
        mock_repo.list_by_bot_id.return_value = [_record(owner_id="user_A")]
        result = service.list_render_screens(bot_id="default", owner_id="user_A")
        assert len(result) == 1
        mock_repo.list_by_bot_id.assert_called_once_with(bot_id="default", owner_id="user_A")


class TestCreateRenderScreen:
    def test_create_returns_id(self, service, mock_repo):
        mock_repo.list_by_bot_id.return_value = []
        mock_repo.insert.return_value = 42
        record_id = service.create_render_screen(
            bot_id="bot_001", owner_id="user_001",
            name="看板", cdn_url="https://cdn.example.com/v1/index.js",
            creator_id="user_001",
        )
        assert record_id == 42
        mock_repo.list_by_bot_id.assert_called_once_with(bot_id="bot_001", owner_id="user_001")
        mock_repo.insert.assert_called_once_with(
            bot_id="bot_001", owner_id="user_001",
            name="看板", cdn_url="https://cdn.example.com/v1/index.js",
            creator_id="user_001",
        )

    def test_create_duplicate_name_raises(self, service, mock_repo):
        mock_repo.list_by_bot_id.return_value = [_record(name="看板")]
        with pytest.raises(ValueError, match="Duplicate name"):
            service.create_render_screen(
                bot_id="bot_001", owner_id="user_001",
                name="看板", cdn_url="https://cdn.example.com/v1/index.js",
                creator_id="user_001",
            )

    def test_create_duplicate_cdn_url_raises(self, service, mock_repo):
        mock_repo.list_by_bot_id.return_value = [_record(cdn_url="https://cdn.example.com/v1/index.js")]
        with pytest.raises(ValueError, match="Duplicate cdn_url"):
            service.create_render_screen(
                bot_id="bot_001", owner_id="user_001",
                name="不同名称", cdn_url="https://cdn.example.com/v1/index.js",
                creator_id="user_001",
            )

    def test_create_same_name_different_owner_ok(self, service, mock_repo):
        """不同用户在各自 default bot 上创建同名配置不应冲突。"""
        mock_repo.list_by_bot_id.return_value = []
        mock_repo.insert.return_value = 43
        record_id = service.create_render_screen(
            bot_id="default", owner_id="user_B",
            name="数据看板", cdn_url="https://cdn.example.com/v2/index.js",
            creator_id="user_B",
        )
        assert record_id == 43
        mock_repo.list_by_bot_id.assert_called_once_with(bot_id="default", owner_id="user_B")


class TestUpdateRenderScreen:
    def test_update_success(self, service, mock_repo):
        mock_repo.get_by_id.return_value = _record(id=1, owner_id="user_001")
        service.update_render_screen(record_id=1, name="新名称", cdn_url="https://new.url")
        mock_repo.update_by_id.assert_called_once_with(
            record_id=1, name="新名称", cdn_url="https://new.url",
        )

    def test_update_not_found_raises(self, service, mock_repo):
        mock_repo.get_by_id.return_value = None
        with pytest.raises(ValueError, match="not found"):
            service.update_render_screen(record_id=999, name="x", cdn_url="y")


class TestDeleteRenderScreen:
    def test_delete_success(self, service, mock_repo):
        mock_repo.get_by_id.return_value = _record(id=1)
        service.delete_render_screen(record_id=1)
        mock_repo.delete_by_id.assert_called_once_with(record_id=1)

    def test_delete_not_found_raises(self, service, mock_repo):
        mock_repo.get_by_id.return_value = None
        with pytest.raises(ValueError, match="not found"):
            service.delete_render_screen(record_id=999)


class TestGetRenderScreen:
    def test_get_found(self, service, mock_repo):
        expected = _record(id=1)
        mock_repo.get_by_id.return_value = expected
        result = service.get_render_screen(1)
        assert result == expected
        mock_repo.get_by_id.assert_called_once_with(1)

    def test_get_not_found(self, service, mock_repo):
        mock_repo.get_by_id.return_value = None
        result = service.get_render_screen(999)
        assert result is None