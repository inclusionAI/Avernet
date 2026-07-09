"""Unit tests for BotService.search_bots.

Tests the service layer for searching bots with publish info.
"""
from __future__ import annotations

from unittest.mock import MagicMock


def _make_service():
    """Construct BotService with mock repository."""
    from agentclaw.community.core.bot_management.services.bot_service import BotService

    svc = BotService.__new__(BotService)
    svc._repository = MagicMock()
    svc._device_binding_repo = None
    # Cycle-breaker provider attributes (set by __init__); tests using
    # ``__new__`` bypass __init__ so install permissive defaults — individual
    # tests override ``svc._bot_publish_provider`` when they need to assert
    # on the resolved service.
    svc._bot_publish_provider = lambda: MagicMock()
    svc._device_service_provider = lambda: MagicMock()
    return svc


def _make_bot_with_publish():
    """Create a bot dict with publish info."""
    return {
        "id": 1,
        "bot_id": "bot-001",
        "bot_name": "Test Bot",
        "owner_id": "owner-1",
        "owner_name": "Owner One",
        "status": "ACTIVE",
        "public": "1",
        "entity_id": "ent-001",
        "entity_type": "staff",
        "engine_types": ["moltis", "openclaw"],
        "device_id": "dev-001",
        "gmt_create": "2024-01-01T00:00:00",
        "gmt_modified": "2024-01-02T00:00:00",
        "ext": {"extra": "data"},
        "bot_type": "personal",
        "publish": {
            "id": 100,
            "source_bot_pk": 1,
            "source_bot_id": "bot-001",
            "publish_bot_id": "bot-001_pub_1",
            "name": "Test Bot Pub",
            "description": "Published version",
            "owner_id": "owner-1",
            "owner_name": "Owner One",
            "status": "success",
            "version": 1,
            "last_pub_id": 0,
            "env": "dev",
            "ext": None,
            "permission_owner": "owner-1",
            "gmt_create": "2024-01-03T00:00:00",
            "gmt_modified": "2024-01-04T00:00:00",
        },
    }


def _make_bot_without_publish():
    """Create a bot dict without publish info."""
    return {
        "id": 2,
        "bot_id": "bot-002",
        "bot_name": "Another Bot",
        "owner_id": "owner-2",
        "owner_name": "Owner Two",
        "status": "PENDING",
        "public": "0",
        "entity_id": "ent-002",
        "entity_type": "staff",
        "engine_types": ["moltis"],
        "device_id": None,
        "gmt_create": "2024-01-05T00:00:00",
        "gmt_modified": "2024-01-06T00:00:00",
        "ext": None,
        "bot_type": "service",
        "publish": None,
    }


# ===========================================================================
# search_bots tests
# ===========================================================================


class TestSearchBots:
    """BotService.search_bots()"""

    def test_returns_total_and_items_from_repository(self):
        """返回 repository 的结果。"""
        svc = _make_service()
        svc._repository.search_bots.return_value = (2, [_make_bot_with_publish(), _make_bot_without_publish()])

        result = svc.search_bots()

        assert result["total"] == 2
        assert len(result["items"]) == 2
        assert result["items"][0]["bot_id"] == "bot-001"
        assert result["items"][1]["bot_id"] == "bot-002"

    def test_passes_all_parameters_to_repository(self):
        """所有参数传递给 repository。"""
        svc = _make_service()
        svc._repository.search_bots.return_value = (0, [])

        svc.search_bots(
            key="Test",
            bot_status="ACTIVE",
            public="1",
            owner_id="owner-1",
            service_status_list=["success", "init"],
            bot_type="service",
            collaborator_user_id="user-123",
            page=2,
            page_size=50,
        )

        svc._repository.search_bots.assert_called_once_with(
            key="Test",
            bot_status="ACTIVE",
            public="1",
            owner_id="owner-1",
            service_status_list=["success", "init"],
            bot_type="service",
            active_engine=None,
            collaborator_user_id="user-123",
            bot_id=None,
            provider=None,
            template_type=None,
            page=2,
            page_size=50,
        )

    def test_default_parameters(self):
        """默认参数正确传递。"""
        svc = _make_service()
        svc._repository.search_bots.return_value = (0, [])

        svc.search_bots()

        svc._repository.search_bots.assert_called_once_with(
            key=None,
            bot_status=None,
            public=None,
            owner_id=None,
            service_status_list=None,
            bot_type=None,
            active_engine=None,
            collaborator_user_id=None,
            bot_id=None,
            provider=None,
            template_type=None,
            page=1,
            page_size=20,
        )

    def test_returns_empty_results(self):
        """空结果正确返回。"""
        svc = _make_service()
        svc._repository.search_bots.return_value = (0, [])

        result = svc.search_bots(key="NonExistent")

        assert result["total"] == 0
        assert result["items"] == []

    def test_returns_bot_with_publish(self):
        """返回包含 publish 信息的 bot。"""
        svc = _make_service()
        bot_with_publish = _make_bot_with_publish()
        svc._repository.search_bots.return_value = (1, [bot_with_publish])

        result = svc.search_bots(service_status_list=["success"])

        assert result["total"] == 1
        assert len(result["items"]) == 1
        item = result["items"][0]
        assert item["publish"] is not None
        assert item["publish"]["status"] == "success"
        assert item["publish"]["version"] == 1

    def test_returns_bot_without_publish(self):
        """返回没有 publish 信息的 bot。"""
        svc = _make_service()
        bot_without_publish = _make_bot_without_publish()
        svc._repository.search_bots.return_value = (1, [bot_without_publish])

        result = svc.search_bots()

        assert result["total"] == 1
        assert len(result["items"]) == 1
        item = result["items"][0]
        assert item["publish"] is None

    def test_mixed_results_with_and_without_publish(self):
        """混合结果正确返回。"""
        svc = _make_service()
        svc._repository.search_bots.return_value = (2, [_make_bot_with_publish(), _make_bot_without_publish()])

        result = svc.search_bots()

        assert result["total"] == 2
        assert len(result["items"]) == 2
        assert result["items"][0]["publish"] is not None
        assert result["items"][1]["publish"] is None

    def test_service_status_list_filter(self):
        """service_status_list 正确传递。"""
        svc = _make_service()
        svc._repository.search_bots.return_value = (0, [])

        svc.search_bots(service_status_list=["success", "init", "release"])

        call_args = svc._repository.search_bots.call_args
        assert call_args.kwargs["service_status_list"] == ["success", "init", "release"]

    def test_pagination_parameters(self):
        """分页参数正确传递。"""
        svc = _make_service()
        svc._repository.search_bots.return_value = (100, [])

        svc.search_bots(page=5, page_size=50)

        call_args = svc._repository.search_bots.call_args
        assert call_args.kwargs["page"] == 5
        assert call_args.kwargs["page_size"] == 50

    def test_key_search_parameter(self):
        """key 参数正确传递。"""
        svc = _make_service()
        svc._repository.search_bots.return_value = (0, [])

        svc.search_bots(key="测试")

        call_args = svc._repository.search_bots.call_args
        assert call_args.kwargs["key"] == "测试"

    def test_all_filters_combined(self):
        """所有过滤条件组合使用。"""
        svc = _make_service()
        svc._repository.search_bots.return_value = (1, [_make_bot_with_publish()])

        result = svc.search_bots(
            key="Test",
            bot_status="ACTIVE",
            public="1",
            owner_id="owner-1",
            service_status_list=["success"],
            bot_type="service",
            page=1,
            page_size=10,
        )

        svc._repository.search_bots.assert_called_once_with(
            key="Test",
            bot_status="ACTIVE",
            public="1",
            owner_id="owner-1",
            service_status_list=["success"],
            bot_type="service",
            active_engine=None,
            collaborator_user_id=None,
            bot_id=None,
            provider=None,
            template_type=None,
            page=1,
            page_size=10,
        )
        assert result["total"] == 1

    def test_bot_type_filter(self):
        """bot_type 参数正确传递。"""
        svc = _make_service()
        svc._repository.search_bots.return_value = (0, [])

        svc.search_bots(bot_type="personal")

        call_args = svc._repository.search_bots.call_args
        assert call_args.kwargs["bot_type"] == "personal"

    def test_bot_type_returned_in_result(self):
        """bot_type 字段在结果中返回。"""
        svc = _make_service()
        bot_with_publish = _make_bot_with_publish()
        svc._repository.search_bots.return_value = (1, [bot_with_publish])

        result = svc.search_bots()

        assert result["total"] == 1
        assert result["items"][0]["bot_type"] == "personal"

    def test_bot_type_service_returned_in_result(self):
        """bot_type 为 'service' 时在结果中正确返回。"""
        svc = _make_service()
        bot_without_publish = _make_bot_without_publish()
        svc._repository.search_bots.return_value = (1, [bot_without_publish])

        result = svc.search_bots()

        assert result["total"] == 1
        assert result["items"][0]["bot_type"] == "service"


class TestSearchBotsCanDeleteAndUpgrade:
    """BotService.search_bots() can_delete_bot 和 can_upgrade_publish 字段测试。"""

    def _make_service_bot_with_publish(self, publish_id=100, publish_status="success"):
        """创建服务型 bot 带 publish 信息。"""
        return {
            "id": 1,
            "bot_id": "bot-001",
            "bot_name": "Service Bot",
            "owner_id": "owner-1",
            "status": "ACTIVE",
            "bot_type": "service",
            "publish": {
                "id": publish_id,
                "status": publish_status,
                "source_bot_pk": 1,
                "source_bot_id": "bot-001",
                "publish_bot_id": "bot-001_pub",
                "name": "Service Bot Pub",
                "owner_id": "owner-1",
                "version": 1,
                "last_pub_id": 0,
                "env": "dev",
                "permission_owner": "owner",
            },
        }

    def _make_personal_bot(self):
        """创建个人型 bot。"""
        return {
            "id": 2,
            "bot_id": "bot-002",
            "bot_name": "Personal Bot",
            "owner_id": "owner-1",
            "status": "ACTIVE",
            "bot_type": "personal",
            "publish": None,
        }

    def test_service_bot_with_publish_adds_can_delete_and_upgrade(self):
        """服务型 bot 带 publish 时添加 can_delete_bot 和 can_upgrade_publish 字段。"""
        svc = _make_service()
        bot = self._make_service_bot_with_publish(publish_id=100, publish_status="success")
        svc._repository.search_bots.return_value = (1, [bot])

        result = svc.search_bots()

        assert result["total"] == 1
        item = result["items"][0]
        assert "can_delete_bot" in item
        assert "can_upgrade_publish" in item

    def test_service_bot_without_publish_has_false_values(self):
        """服务型 bot 无 publish 时 can_delete_bot 和 can_upgrade_publish 为 False。"""
        svc = _make_service()
        bot = {
            "id": 1,
            "bot_id": "bot-001",
            "bot_name": "Service Bot",
            "bot_type": "service",
            "publish": None,
        }
        svc._repository.search_bots.return_value = (1, [bot])

        result = svc.search_bots()

        assert result["items"][0]["can_delete_bot"] is False
        assert result["items"][0]["can_upgrade_publish"] is False

    def test_personal_bot_has_false_values(self):
        """个人型 bot 的 can_delete_bot 和 can_upgrade_publish 为 False。"""
        svc = _make_service()
        bot = self._make_personal_bot()
        svc._repository.search_bots.return_value = (1, [bot])

        result = svc.search_bots()

        assert result["items"][0]["can_delete_bot"] is False
        assert result["items"][0]["can_upgrade_publish"] is False

    def test_publish_without_id_has_false_values(self):
        """publish 存在但无 id 时 can_delete_bot 和 can_upgrade_publish 为 False。"""
        svc = _make_service()
        bot = {
            "id": 1,
            "bot_id": "bot-001",
            "bot_name": "Service Bot",
            "bot_type": "service",
            "publish": {"status": "draft"},  # 无 id
        }
        svc._repository.search_bots.return_value = (1, [bot])

        result = svc.search_bots()

        assert result["items"][0]["can_delete_bot"] is False
        assert result["items"][0]["can_upgrade_publish"] is False

    def test_mixed_bots_correct_values(self):
        """混合 bot 类型时各自由正确值。"""
        svc = _make_service()
        service_bot = self._make_service_bot_with_publish(publish_id=100, publish_status="draft")
        personal_bot = self._make_personal_bot()
        svc._repository.search_bots.return_value = (2, [service_bot, personal_bot])

        result = svc.search_bots()

        # 服务型 bot 有字段
        assert "can_delete_bot" in result["items"][0]
        assert "can_upgrade_publish" in result["items"][0]
        # 个人型 bot 为 False
        assert result["items"][1]["can_delete_bot"] is False
        assert result["items"][1]["can_upgrade_publish"] is False

    def test_exception_during_publish_service_call_returns_false(self):
        """publish_service 调用异常时返回 False。"""
        svc = _make_service()
        bot = self._make_service_bot_with_publish(publish_id=100, publish_status="success")
        svc._repository.search_bots.return_value = (1, [bot])

        mock_publish_svc = MagicMock()
        mock_publish_svc.can_delete_bot.side_effect = Exception("Service error")
        mock_publish_svc.can_upgrade_publish.side_effect = Exception("Service error")
        svc._bot_publish_provider = lambda: mock_publish_svc

        result = svc.search_bots()

        # 异常时返回 False
        assert result["items"][0]["can_delete_bot"] is False
        assert result["items"][0]["can_upgrade_publish"] is False

    def test_get_publish_service_exception_returns_false(self):
        """get_bot_publish_service 异常时返回 False。"""
        svc = _make_service()
        bot = self._make_service_bot_with_publish(publish_id=100, publish_status="success")
        svc._repository.search_bots.return_value = (1, [bot])

        def _raise():
            raise Exception("Service not available")

        svc._bot_publish_provider = _raise

        result = svc.search_bots()

        # 异常时不会添加字段，所以检查不存在或为 False
        assert result["items"][0].get("can_delete_bot", False) is False
        assert result["items"][0].get("can_upgrade_publish", False) is False


class TestListBotsByOwnerCanEditBot:
    """BotService.list_bots_by_owner() can_edit_bot 字段测试。"""

    def _make_service(self):
        """Construct BotService with mock repository."""
        from agentclaw.community.core.bot_management.services.bot_service import BotService

        svc = BotService.__new__(BotService)
        svc._repository = MagicMock()
        svc._device_binding_repo = None
        svc._bot_publish_provider = lambda: MagicMock()
        svc._device_service_provider = lambda: MagicMock()
        return svc

    def _make_service_bot(self, bot_id="bot-001", owner_id="owner-1"):
        """创建服务型 bot。"""
        return {
            "id": 1,
            "bot_id": bot_id,
            "bot_name": "Service Bot",
            "owner_id": owner_id,
            "status": "ACTIVE",
            "bot_type": "service",
        }

    def _make_personal_bot(self, bot_id="bot-002", owner_id="owner-1"):
        """创建个人型 bot。"""
        return {
            "id": 2,
            "bot_id": bot_id,
            "bot_name": "Personal Bot",
            "owner_id": owner_id,
            "status": "ACTIVE",
            "bot_type": "personal",
        }

    def test_service_bot_can_edit_bot_true(self):
        """服务型 bot 有草稿发布单时 can_edit_bot 为 True。"""
        svc = self._make_service()
        service_bot = self._make_service_bot()
        svc._repository.list_by_owner.return_value = (1, [service_bot])

        mock_publish_svc = MagicMock()
        mock_publish_svc.can_edit_bot.return_value = True
        svc._bot_publish_provider = lambda: mock_publish_svc

        result = svc.list_bots_by_owner(owner_id="owner-1")

        assert result["total"] == 1
        assert result["items"][0]["can_edit_bot"] is True
        mock_publish_svc.can_edit_bot.assert_called_once_with("bot-001", "owner-1")

    def test_service_bot_can_edit_bot_false(self):
        """服务型 bot 无草稿发布单时 can_edit_bot 为 False。"""
        svc = self._make_service()
        service_bot = self._make_service_bot()
        svc._repository.list_by_owner.return_value = (1, [service_bot])

        mock_publish_svc = MagicMock()
        mock_publish_svc.can_edit_bot.return_value = False
        svc._bot_publish_provider = lambda: mock_publish_svc

        result = svc.list_bots_by_owner(owner_id="owner-1")

        assert result["items"][0]["can_edit_bot"] is False

    def test_personal_bot_can_edit_bot_true(self):
        """个人型 bot 的 can_edit_bot 为 True。"""
        svc = self._make_service()
        personal_bot = self._make_personal_bot()
        svc._repository.list_by_owner.return_value = (1, [personal_bot])

        result = svc.list_bots_by_owner(owner_id="owner-1")

        assert result["total"] == 1
        assert result["items"][0]["can_edit_bot"] is True

    def test_mixed_bots_can_edit_bot(self):
        """混合 bot 类型时 can_edit_bot 各自正确。"""
        svc = self._make_service()
        service_bot = self._make_service_bot(bot_id="bot-service")
        personal_bot = self._make_personal_bot(bot_id="bot-personal")
        svc._repository.list_by_owner.return_value = (2, [service_bot, personal_bot])

        mock_publish_svc = MagicMock()
        mock_publish_svc.can_edit_bot.return_value = True
        svc._bot_publish_provider = lambda: mock_publish_svc

        result = svc.list_bots_by_owner(owner_id="owner-1")

        # 服务型 bot 调用 can_edit_bot
        assert result["items"][0]["can_edit_bot"] is True
        # 个人型 bot 默认为 True
        assert result["items"][1]["can_edit_bot"] is True

    def test_service_bot_without_bot_id_can_edit_bot_false(self):
        """服务型 bot 无 bot_id 时 can_edit_bot 为 False。"""
        svc = self._make_service()
        service_bot = {
            "id": 1,
            "bot_name": "Service Bot",
            "owner_id": "owner-1",
            "bot_type": "service",
            # 缺少 bot_id
        }
        svc._repository.list_by_owner.return_value = (1, [service_bot])

        result = svc.list_bots_by_owner(owner_id="owner-1")

        assert result["items"][0]["can_edit_bot"] is False

    def test_publish_service_exception_returns_false(self):
        """publish_service 调用异常时 can_edit_bot 为 False。"""
        svc = self._make_service()
        service_bot = self._make_service_bot()
        svc._repository.list_by_owner.return_value = (1, [service_bot])

        mock_publish_svc = MagicMock()
        mock_publish_svc.can_edit_bot.side_effect = Exception("Service error")
        svc._bot_publish_provider = lambda: mock_publish_svc

        result = svc.list_bots_by_owner(owner_id="owner-1")

        # 异常时返回 False
        assert result["items"][0]["can_edit_bot"] is False

    def test_get_publish_service_exception_skips_field(self):
        """get_bot_publish_service 异常时 can_edit_bot 为 False。"""
        svc = self._make_service()
        service_bot = self._make_service_bot()
        svc._repository.list_by_owner.return_value = (1, [service_bot])

        def _raise():
            raise Exception("Service not available")

        svc._bot_publish_provider = _raise

        result = svc.list_bots_by_owner(owner_id="owner-1")

        # 异常时返回 False
        assert result["items"][0].get("can_edit_bot", False) is False

    def test_pagination_parameters(self):
        """分页参数正确传递。"""
        svc = self._make_service()
        svc._repository.list_by_owner.return_value = (100, [])

        svc.list_bots_by_owner(owner_id="owner-1", page=3, page_size=20)

        svc._repository.list_by_owner.assert_called_once_with(
            owner_id="owner-1",
            page=3,
            page_size=20,
        )

    def test_empty_results(self):
        """空结果正确返回。"""
        svc = self._make_service()
        svc._repository.list_by_owner.return_value = (0, [])

        result = svc.list_bots_by_owner(owner_id="owner-1")

        assert result["total"] == 0
        assert result["items"] == []
