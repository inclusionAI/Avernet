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


def _bot(**overrides):
    defaults = dict(
        id=101,
        bot_id="bot_001",
        owner_id="owner_001",
        active_engine="claude_code",
        template_type="normalCC",
    )
    defaults.update(overrides)
    return defaults


def _dynamic_shared_bot(**overrides):
    defaults = dict(
        id=102,
        bot_id="bot_001",
        owner_id="owner_001",
        active_engine="claude_code",
        template_type="architect",
    )
    defaults.update(overrides)
    return defaults


def _dynamic_shared_template_config():
    return {
        "capabilities": {
            "member_management": True,
        },
    }


@pytest.fixture
def mock_repo():
    repo = MagicMock()
    return repo


@pytest.fixture
def mock_bot_repo():
    repo = MagicMock()
    repo.get_by_id.return_value = _bot()
    return repo


@pytest.fixture
def mock_collaborator_repo():
    repo = MagicMock()
    repo.get_by_bot_and_user.return_value = None
    return repo


@pytest.fixture
def mock_template_service():
    service = MagicMock()
    service.get_template_config.return_value = None
    return service


@pytest.fixture
def service(mock_repo, mock_bot_repo, mock_collaborator_repo, mock_template_service):
    return RenderScreenService(
        repository=mock_repo,
        bot_repository=mock_bot_repo,
        collaborator_repository=mock_collaborator_repo,
        template_service=mock_template_service,
    )


class TestListRenderScreens:
    def test_returns_records_for_owner_scoped_bot(self, service, mock_repo, mock_bot_repo):
        mock_repo.list_by_bot_id.return_value = [
            _record(id=1, name="看板1"),
            _record(id=2, name="看板2"),
        ]
        result = service.list_render_screens(
            bot_id="bot_001",
            owner_id="user_001",
            current_user_id="user_001",
        )
        assert len(result) == 2
        mock_repo.list_by_bot_id.assert_called_once_with(bot_id="bot_001", owner_id="user_001")
        mock_bot_repo.get_by_id.assert_called_once_with("bot_001")

    def test_shared_coding_bot_lists_without_owner_filter(self, service, mock_repo, mock_bot_repo, mock_collaborator_repo):
        mock_bot_repo.get_by_id.return_value = _bot(template_type="applicationCoding", active_engine="claude_code")
        mock_collaborator_repo.get_by_bot_and_user.return_value = _record(id=9)
        mock_repo.list_by_bot_id.return_value = [_record(id=1, owner_id="collab_001")]

        result = service.list_render_screens(
            bot_id="bot_001",
            owner_id="owner_001",
            current_user_id="collab_001",
        )

        assert len(result) == 1
        mock_repo.list_by_bot_id.assert_called_once_with(bot_id="bot_001", owner_id=None)

    def test_shared_dynamic_template_bot_lists_without_owner_filter(self, service, mock_repo, mock_bot_repo, mock_collaborator_repo, mock_template_service):
        mock_bot_repo.get_by_id.return_value = _dynamic_shared_bot()
        mock_template_service.get_template_config.return_value = _dynamic_shared_template_config()
        mock_collaborator_repo.get_by_bot_and_user.return_value = _record(id=9)
        mock_repo.list_by_bot_id.return_value = [_record(id=1, owner_id="collab_001")]

        result = service.list_render_screens(
            bot_id="bot_001",
            owner_id="owner_001",
            current_user_id="collab_001",
        )

        assert len(result) == 1
        mock_repo.list_by_bot_id.assert_called_once_with(bot_id="bot_001", owner_id=None)

    def test_shared_coding_bot_denies_non_collaborator(self, service, mock_repo, mock_bot_repo):
        mock_bot_repo.get_by_id.return_value = _bot(template_type="applicationCoding", active_engine="claude_code")
        with pytest.raises(PermissionError):
            service.list_render_screens(
                bot_id="bot_001",
                owner_id="owner_001",
                current_user_id="stranger",
            )


    def test_missing_bot_fails_closed(self, service, mock_bot_repo):
        mock_bot_repo.get_by_id.return_value = None
        with pytest.raises(PermissionError, match="无权查看此 Bot 的 CDN 配置"):
            service.list_render_screens(
                bot_id="bot_missing",
                owner_id="owner_001",
                current_user_id="user_001",
            )


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

    def test_create_shared_bot_uses_sharing_scope(self, service, mock_repo, mock_bot_repo, mock_collaborator_repo):
        mock_bot_repo.get_by_id.return_value = _bot(template_type="applicationCoding", active_engine="claude_code")
        mock_collaborator_repo.get_by_bot_and_user.return_value = _record(id=9)
        mock_repo.list_by_bot_id.return_value = []
        mock_repo.insert.return_value = 43

        record_id = service.create_render_screen(
            bot_id="bot_001",
            owner_id="user_001",
            name="数据看板",
            cdn_url="https://cdn.example.com/v2/index.js",
            creator_id="collab_001",
            current_user_id="collab_001",
        )

        assert record_id == 43
        mock_repo.list_by_bot_id.assert_called_once_with(bot_id="bot_001", owner_id=None)
        mock_repo.insert.assert_called_once_with(
            bot_id="bot_001",
            owner_id="owner_001",
            name="数据看板",
            cdn_url="https://cdn.example.com/v2/index.js",
            creator_id="collab_001",
        )

    def test_create_dynamic_shared_bot_uses_sharing_scope(self, service, mock_repo, mock_bot_repo, mock_collaborator_repo, mock_template_service):
        mock_bot_repo.get_by_id.return_value = _dynamic_shared_bot()
        mock_template_service.get_template_config.return_value = _dynamic_shared_template_config()
        mock_collaborator_repo.get_by_bot_and_user.return_value = _record(id=9)
        mock_repo.list_by_bot_id.return_value = []
        mock_repo.insert.return_value = 44

        record_id = service.create_render_screen(
            bot_id="bot_001",
            owner_id="user_001",
            name="共享看板",
            cdn_url="https://cdn.example.com/v3/index.js",
            creator_id="collab_002",
            current_user_id="collab_002",
        )

        assert record_id == 44
        mock_repo.list_by_bot_id.assert_called_once_with(bot_id="bot_001", owner_id=None)
        mock_repo.insert.assert_called_once_with(
            bot_id="bot_001",
            owner_id="owner_001",
            name="共享看板",
            cdn_url="https://cdn.example.com/v3/index.js",
            creator_id="collab_002",
        )

    def test_create_shared_bot_denies_non_collaborator(self, service, mock_bot_repo):
        mock_bot_repo.get_by_id.return_value = _bot(template_type="applicationCoding", active_engine="claude_code")
        with pytest.raises(PermissionError):
            service.create_render_screen(
                bot_id="bot_001",
                owner_id="user_001",
                name="看板",
                cdn_url="https://cdn.example.com/v1/index.js",
                creator_id="stranger",
                current_user_id="stranger",
            )


    def test_create_missing_bot_fails_closed(self, service, mock_bot_repo):
        mock_bot_repo.get_by_id.return_value = None
        with pytest.raises(PermissionError, match="无权操作此 Bot 的 CDN 配置"):
            service.create_render_screen(
                bot_id="bot_missing",
                owner_id="user_001",
                name="看板",
                cdn_url="https://cdn.example.com/v1/index.js",
                creator_id="user_001",
                current_user_id="user_001",
            )


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


class TestAuthorizeRenderScreenRecord:
    def test_authorize_record_owner_scoped(self, service, mock_repo):
        mock_repo.get_by_id.side_effect = [_record(id=1, owner_id="user_001"), _bot()]
        record = service.authorize_render_screen_record(record_id=1, user_id="user_001")
        assert record.id == 1

    def test_authorize_record_shared_bot_allows_collaborator(self, service, mock_repo, mock_bot_repo, mock_collaborator_repo):
        mock_repo.get_by_id.return_value = _record(id=1, owner_id="owner_001")
        mock_bot_repo.get_by_id.return_value = _bot(template_type="applicationCoding", active_engine="claude_code")
        mock_collaborator_repo.get_by_bot_and_user.return_value = _record(id=9)

        record = service.authorize_render_screen_record(record_id=1, user_id="collab_001")
        assert record.id == 1

    def test_authorize_record_dynamic_shared_bot_allows_collaborator(self, service, mock_repo, mock_bot_repo, mock_collaborator_repo, mock_template_service):
        mock_repo.get_by_id.return_value = _record(id=1, owner_id="owner_001")
        mock_bot_repo.get_by_id.return_value = _dynamic_shared_bot(template_type="architect")
        mock_template_service.get_template_config.return_value = _dynamic_shared_template_config()
        mock_collaborator_repo.get_by_bot_and_user.return_value = _record(id=9)

        record = service.authorize_render_screen_record(record_id=1, user_id="collab_002")
        assert record.id == 1

    def test_authorize_record_shared_bot_denies_stranger(self, service, mock_repo, mock_bot_repo):
        mock_repo.get_by_id.return_value = _record(id=1, owner_id="owner_001")
        mock_bot_repo.get_by_id.return_value = _bot(template_type="applicationCoding", active_engine="claude_code")
        with pytest.raises(PermissionError):
            service.authorize_render_screen_record(record_id=1, user_id="stranger")


    def test_authorize_record_missing_bot_fails_closed(self, service, mock_repo, mock_bot_repo):
        mock_repo.get_by_id.return_value = _record(id=1, owner_id="owner_001")
        mock_bot_repo.get_by_id.return_value = None
        with pytest.raises(PermissionError, match="无权操作此 Bot 的 CDN 配置"):
            service.authorize_render_screen_record(record_id=1, user_id="stranger")


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
