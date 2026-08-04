"""
Unit tests for Bot service Passport integration.

Tests for:
- bot_service.create_bot with bot_id parameter (idempotency)
- bot_service.update_bot_ext method
"""
from concurrent.futures import ThreadPoolExecutor as RealThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agentclaw.community.core.bot_management.services.bot_service import (
    BotServiceError,
)
from agentclaw.community.core.devices.errors import DeviceServiceError
from agentclaw.community.core.service_bot.repository.models import PublishStatus


class TestBotServicePassportIntegration:
    """Tests for Bot service Passport integration."""

    @pytest.fixture
    def mock_bot_repository(self):
        """Mock bot repository."""
        repo = MagicMock()
        return repo

    @pytest.fixture
    def mock_device_service(self):
        """Mock device service."""
        service = MagicMock()
        return service

    @pytest.fixture
    def bot_service(self, mock_bot_repository):
        """Create BotService with mocked dependencies."""
        from agentclaw.community.core.bot_management.services.bot_service import BotService
        service = BotService(
            drm_reader=MagicMock(),
            repository=mock_bot_repository,
            allocation_config=MagicMock(mode="multi", max_devices_per_entity=5),
            device_binding_repo=MagicMock(),
            skill_set_factory=MagicMock(),
            cleanup_service=MagicMock(),
            bcn_service=MagicMock(),
            bot_publish_repo=MagicMock(),
            passport_plugin=MagicMock(),
            oss_record_repo=MagicMock(),
            bot_publish_service_provider=lambda: MagicMock(),
            device_service_provider=lambda: MagicMock(),
            path_factory=MagicMock(),
            template_service=MagicMock(),
            workspace_hosting_service=MagicMock(),
            collaborator_repo=MagicMock(),
            restart_lock_repo=MagicMock(),
            teclaw_provision_service_provider=lambda: MagicMock(is_teclaw=MagicMock(return_value=False)),
            device_status_client=MagicMock(),
            cron_auto_setup_service_provider=lambda: MagicMock(),
        )
        return service


def _caller_binding(
    binding_id: int,
    device_id: str,
    applied_by: str,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=binding_id,
        entity_id="owner-1",
        entity_type="staff",
        device_id=device_id,
        device_provider="baas",
        env="prod",
        device_props={},
        status="ACTIVE",
        apply_reason="caller_instance:service-bot-1",
        applied_by=applied_by,
    )


class TestServiceBotPassportRefresh(TestBotServicePassportIntegration):
    @staticmethod
    def _set_publish_records(bot_service, *, online_id: int | None, verify_id: int | None):
        records = {
            PublishStatus.SUCCESS.value: (
                SimpleNamespace(id=101, ext={"binding": {"online": online_id}})
                if online_id is not None
                else None
            ),
            PublishStatus.VALIDATING.value: (
                SimpleNamespace(id=102, ext={"binding": {"verify": verify_id}})
                if verify_id is not None
                else None
            ),
        }
        bot_service._bot_publish_repo.get_by_publish_bot_id.side_effect = (
            lambda **kwargs: records[kwargs["publish_status"]]
        )

    def test_updates_standard_and_caller_targets_with_owner_token_at_fixed_concurrency(
        self,
        bot_service,
    ):
        self._set_publish_records(bot_service, online_id=21, verify_id=22)
        bot_service._hot_update_by_device_binding = MagicMock(
            return_value={"binding_id": 20, "devices": [{"device_uuid": "draft"}]}
        )
        bot_service._hot_update_by_publish_binding = MagicMock(
            side_effect=[
                {"binding_id": 21, "devices": [{"device_uuid": "online"}]},
                {"binding_id": 22, "devices": [{"device_uuid": "verify"}]},
            ]
        )
        caller_bindings = [
            _caller_binding(30 + index, f"BOT-caller-{index}", f"caller-{index}")
            for index in range(6)
        ]
        bot_service._device_binding_repo.list_active_caller_instance_bindings.return_value = (
            caller_bindings
        )
        device_service = MagicMock()
        device_service.update_device_headers.side_effect = lambda **kwargs: [
            {"device_uuid": f"DEVICE-{kwargs['device'].device_id}"}
        ]
        bot_service._device_service_provider = lambda: device_service

        worker_counts: list[int] = []

        def executor_factory(*, max_workers: int):
            worker_counts.append(max_workers)
            return RealThreadPoolExecutor(max_workers=max_workers)

        with (
            patch(
                "agentclaw.community.core.bot_management.services.bot_service.get_current_env",
                return_value="prod",
            ),
            patch(
                "agentclaw.community.core.bot_management.services.bot_service.ThreadPoolExecutor",
                side_effect=executor_factory,
            ),
        ):
            result = bot_service._hot_update_service_bot_passport_token(
                bot_id="service-bot-1",
                user_id="owner-1",
                token="fake-owner-passport-token",
                bot={"binding_id": 20},
                agent_code="owner-agent-code",
            )

        assert worker_counts == [5]
        assert {item["type"] for item in result} == {
            "draft",
            "online",
            "verify",
            "caller",
        }
        assert sum(item["type"] == "caller" for item in result) == 6
        bot_service._device_binding_repo.list_active_caller_instance_bindings.assert_called_once_with(
            bot_id="service-bot-1",
            owner_id="owner-1",
            env="prod",
        )
        assert len(device_service.update_device_headers.call_args_list) == 6
        for call in device_service.update_device_headers.call_args_list:
            assert call.kwargs["agent_pass_token"] == "fake-owner-passport-token"
            assert call.kwargs["agent_code"] == "owner-agent-code"
            assert call.kwargs["active_only"] is True

    def test_device_service_error_does_not_stop_later_stages_or_callers(self, bot_service):
        self._set_publish_records(bot_service, online_id=21, verify_id=22)
        bot_service._hot_update_by_device_binding = MagicMock(
            side_effect=DeviceServiceError("draft unavailable")
        )
        bot_service._hot_update_by_publish_binding = MagicMock(
            side_effect=[
                {"binding_id": 21, "devices": [{"device_uuid": "online"}]},
                {"binding_id": 22, "devices": [{"device_uuid": "verify"}]},
            ]
        )
        bot_service._device_binding_repo.list_active_caller_instance_bindings.return_value = [
            _caller_binding(30, "BOT-caller-1", "caller-1")
        ]
        device_service = MagicMock()
        device_service.update_device_headers.return_value = [
            {"device_uuid": "DEVICE-caller-1"}
        ]
        bot_service._device_service_provider = lambda: device_service

        with (
            patch(
                "agentclaw.community.core.bot_management.services.bot_service.get_current_env",
                return_value="prod",
            ),
            pytest.raises(BotServiceError, match="draft unavailable"),
        ):
            bot_service._hot_update_service_bot_passport_token(
                bot_id="service-bot-1",
                user_id="owner-1",
                token="fake-owner-passport-token",
                bot={"binding_id": 20},
            )

        assert bot_service._hot_update_by_publish_binding.call_count == 2
        device_service.update_device_headers.assert_called_once()

    def test_caller_failure_does_not_stop_other_callers_but_fails_overall(self, bot_service):
        self._set_publish_records(bot_service, online_id=None, verify_id=None)
        bot_service._device_binding_repo.list_active_caller_instance_bindings.return_value = [
            _caller_binding(30, "BOT-caller-a", "caller-a"),
            _caller_binding(31, "BOT-caller-b", "caller-b"),
            _caller_binding(32, "BOT-caller-c", "caller-c"),
        ]
        attempted: list[str] = []

        def update_caller(**kwargs):
            device_id = kwargs["device"].device_id
            attempted.append(device_id)
            if device_id == "BOT-caller-a":
                raise DeviceServiceError("caller a unavailable")
            return [{"device_uuid": f"DEVICE-{device_id}"}]

        device_service = MagicMock()
        device_service.update_device_headers.side_effect = update_caller
        bot_service._device_service_provider = lambda: device_service

        with (
            patch(
                "agentclaw.community.core.bot_management.services.bot_service.get_current_env",
                return_value="prod",
            ),
            pytest.raises(BotServiceError, match="caller a unavailable"),
        ):
            bot_service._hot_update_service_bot_passport_token(
                bot_id="service-bot-1",
                user_id="owner-1",
                token="fake-owner-passport-token",
                bot={},
            )

        assert set(attempted) == {
            "BOT-caller-a",
            "BOT-caller-b",
            "BOT-caller-c",
        }

    def test_caller_binding_with_zero_active_devices_fails_overall(self, bot_service):
        self._set_publish_records(bot_service, online_id=None, verify_id=None)
        bot_service._device_binding_repo.list_active_caller_instance_bindings.return_value = [
            _caller_binding(30, "BOT-caller-1", "caller-1")
        ]
        device_service = MagicMock()
        device_service.update_device_headers.return_value = []
        bot_service._device_service_provider = lambda: device_service

        with (
            patch(
                "agentclaw.community.core.bot_management.services.bot_service.get_current_env",
                return_value="prod",
            ),
            pytest.raises(BotServiceError, match="没有 ACTIVE 物理设备"),
        ):
            bot_service._hot_update_service_bot_passport_token(
                bot_id="service-bot-1",
                user_id="owner-1",
                token="fake-owner-passport-token",
                bot={},
            )

    def test_no_callers_preserves_existing_standard_target_result(self, bot_service):
        self._set_publish_records(bot_service, online_id=None, verify_id=None)
        bot_service._device_binding_repo.list_active_caller_instance_bindings.return_value = []
        bot_service._hot_update_by_device_binding = MagicMock(
            return_value={"binding_id": 20, "devices": [{"device_uuid": "draft"}]}
        )

        with patch(
            "agentclaw.community.core.bot_management.services.bot_service.get_current_env",
            return_value="prod",
        ):
            result = bot_service._hot_update_service_bot_passport_token(
                bot_id="service-bot-1",
                user_id="owner-1",
                token="fake-owner-passport-token",
                bot={"binding_id": 20},
            )

        assert result == [
            {
                "binding_id": 20,
                "type": "draft",
                "device_count": 1,
                "devices": [{"device_uuid": "draft"}],
            }
        ]

class TestCreateBotWithBotId(TestBotServicePassportIntegration):
    """Tests for create_bot with bot_id parameter."""

    def test_hot_update_passport_token_skips_unsupported_bot_type(self, bot_service, mock_bot_repository):
        """Unsupported bot types should be logged and skipped, not raised as errors."""
        mock_bot_repository.get_by_id_and_owner.return_value = {
            "bot_id": "desktop_bot_001",
            "owner_id": "123456",
            "bot_type": "desktop",
        }

        result = bot_service.hot_update_passport_token_to_device(
            bot_id="desktop_bot_001",
            user_id="123456",
            token="passport_token_abcdefghijklmnopqrstuvwxyz",
        )

        assert result == {
            "token_prefix": "passport_token_abcde",
            "bindings": [],
            "skipped": True,
            "reason": "unsupported bot_type: desktop",
        }

    def test_create_bot_with_bot_id_existing_bot(self, bot_service, mock_bot_repository):
        """Test creating bot with existing bot_id returns existing bot (idempotent)."""
        # Setup: mock existing bot
        existing_bot = {
            "bot_id": "default",
            "owner_id": "123456",
            "status": "ACTIVE",
            "bot_name": "Test Bot",
        }
        mock_bot_repository.get_by_id_and_owner.return_value = existing_bot

        # Execute
        result = bot_service.create_bot(
            user_id="123456",
            nick_name="Test User",
            bot_id="default",  # 传入已存在的 bot_id
            bot_type="personal",
        )

        # Verify: 返回已存在的 bot，不创建新记录
        assert result == existing_bot
        mock_bot_repository.get_by_id_and_owner.assert_called_once_with("default", "123456")
        mock_bot_repository.insert.assert_not_called()

    def test_create_bot_with_bot_id_new_bot(self, bot_service, mock_bot_repository, mock_device_service):
        """Test creating bot with new bot_id creates new bot."""
        # Setup: bot 不存在（第一次调用返回 None，第二次调用返回新创建的 bot）
        new_bot = {
            "bot_id": "20260408_abc123",
            "owner_id": "123456",
            "status": "ACTIVE",
            "bot_name": "20260408_abc123",
        }

        # 第一次调用 get_by_id_and_owner 返回 None（bot 不存在）
        # 后续调用返回新创建的 bot
        mock_bot_repository.get_by_id_and_owner.side_effect = [None, new_bot, new_bot]
        mock_bot_repository.exists_by_owner_and_bot_id.return_value = False
        mock_bot_repository.list_by_owner.return_value = (0, [])
        mock_bot_repository.exists_by_bot_name.return_value = False

        # Mock 设备分配
        mock_device_result = MagicMock()
        mock_device_result.id = "binding_123"
        mock_device_result.device_id = "device_123"
        mock_device_result.status = "ACTIVE"
        mock_device_service.apply_device.return_value = mock_device_result

        # Mock insert 返回新 bot
        mock_bot_repository.insert.return_value = new_bot
        mock_bot_repository.update_by_owner.return_value = new_bot

        bot_service._device_service_provider = lambda: mock_device_service
        with patch('agentclaw.community.core.bot_management.services.bot_service._get_engine_types', return_value=['moltis']), \
             patch('agentclaw.community.core.bot_management.services.bot_service.DEFAULT_ENGINE_TYPE', 'moltis'):

            result = bot_service.create_bot(
                user_id="123456",
                nick_name="Test User",
                bot_id="20260408_abc123",  # 传入新的 bot_id
                bot_type="personal",
            )

        # Verify: 使用传入的 bot_id 创建新 bot
        assert result["bot_id"] == "20260408_abc123"
        mock_bot_repository.insert.assert_called_once()
        call_args = mock_bot_repository.insert.call_args[0][0]
        assert call_args["bot_id"] == "20260408_abc123"


class TestUpdateBotExt(TestBotServicePassportIntegration):
    """Tests for update_bot_ext method."""

    def test_update_bot_ext_adds_new_field(self, bot_service, mock_bot_repository):
        """Test updating ext field adds new field."""
        # Setup
        existing_bot = {
            "bot_id": "default",
            "owner_id": "123456",
            "ext": {"avatar_url": "https://example.com/avatar.png"},
        }
        mock_bot_repository.get_by_id_and_owner.return_value = existing_bot

        # Execute
        bot_service.update_bot_ext(
            bot_id="default",
            user_id="123456",
            ext_update={"passport": {"token": "abc123", "status": "ISSUED"}},
        )

        # Verify
        mock_bot_repository.update_by_owner.assert_called_once()
        call_args = mock_bot_repository.update_by_owner.call_args
        assert call_args[0][0] == "default"  # bot_id
        assert call_args[0][1] == "123456"   # user_id
        updated_ext = call_args[0][2]["ext"]
        assert "passport" in updated_ext
        assert updated_ext["passport"]["token"] == "abc123"
        assert updated_ext["avatar_url"] == "https://example.com/avatar.png"  # 保留原字段

    def test_update_bot_ext_with_empty_ext(self, bot_service, mock_bot_repository):
        """Test updating ext when bot has no ext field."""
        # Setup
        existing_bot = {
            "bot_id": "default",
            "owner_id": "123456",
            "ext": None,
        }
        mock_bot_repository.get_by_id_and_owner.return_value = existing_bot

        # Execute
        bot_service.update_bot_ext(
            bot_id="default",
            user_id="123456",
            ext_update={"passport": {"token": "abc123"}},
        )

        # Verify
        call_args = mock_bot_repository.update_by_owner.call_args
        updated_ext = call_args[0][2]["ext"]
        assert updated_ext["passport"]["token"] == "abc123"

    def test_update_bot_ext_with_string_ext(self, bot_service, mock_bot_repository):
        """Test updating ext when ext is a JSON string."""
        # Setup
        existing_bot = {
            "bot_id": "default",
            "owner_id": "123456",
            "ext": '{"avatar_url": "https://example.com/avatar.png"}',
        }
        mock_bot_repository.get_by_id_and_owner.return_value = existing_bot

        # Execute
        bot_service.update_bot_ext(
            bot_id="default",
            user_id="123456",
            ext_update={"passport": {"token": "abc123"}},
        )

        # Verify
        call_args = mock_bot_repository.update_by_owner.call_args
        updated_ext = call_args[0][2]["ext"]
        assert updated_ext["passport"]["token"] == "abc123"
        assert updated_ext["avatar_url"] == "https://example.com/avatar.png"


class TestGetBotMcpCodes:
    """Tests for the _get_bot_mcp_codes helper (factory passed as parameter)."""

    def test_get_bot_mcp_codes(self):
        """Test resolving MCP codes via an injected SkillSetServiceFactory."""
        from agentclaw.community.adapters.http.bot_management.router import _get_bot_mcp_codes

        mock_skill_set_service = MagicMock()
        mock_skill_set_service.get_bot_mcp_codes.return_value = ["mcp_1", "mcp_2", "mcp_3"]

        factory = MagicMock()
        factory.create.return_value = mock_skill_set_service

        result = _get_bot_mcp_codes(
            factory,
            user_id="123456",
            bot_id="default",
            entity_id="staff_123456",
            entity_type="staff",
            engine_type="openclaw",
        )

        assert result == ["mcp_1", "mcp_2", "mcp_3"]
        factory.create.assert_called_once_with(
            user_id="123456",
            entity_id="staff_123456",
            bot_id="default",
            entity_type="staff",
            engine_type="openclaw",
        )
        mock_skill_set_service.get_bot_mcp_codes.assert_called_once_with(
            entity_id="staff_123456",
            bot_id="default",
            user_id="123456",
            entity_type="staff",
        )


class TestGenerateBotId:
    """Tests for generate_bot_id function."""

    @pytest.fixture
    def mock_bot_repository(self):
        """Mock bot repository."""
        repo = MagicMock()
        return repo

    def test_generate_first_bot_id(self, mock_bot_repository):
        """generate_bot_id always returns a globally-unique id (never 'default')."""
        from agentclaw.community.core.bot_management.services.bot_service import generate_bot_id

        # Regression pin: scenario that used to yield 'default'; new impl must still not.
        mock_bot_repository.exists_by_owner_and_bot_id.return_value = False

        result = generate_bot_id("123456", mock_bot_repository)

        assert result != "default"
        assert len(result) == 17  # 8位日期 + 1位下划线 + 8位随机字符
        assert "_" in result
        # id 分配不再依赖 owner 历史
        mock_bot_repository.exists_by_owner_and_bot_id.assert_not_called()

    def test_generate_non_first_bot_id(self, mock_bot_repository):
        """Test generating bot_id for non-first bot returns date-based id."""
        from agentclaw.community.core.bot_management.services.bot_service import generate_bot_id

        mock_bot_repository.exists_by_owner_and_bot_id.return_value = True

        result = generate_bot_id("123456", mock_bot_repository)

        # 非首Bot：bot_id 格式为 "yyyymmdd_xxxxxxxx"
        assert result != "default"
        assert len(result) == 17  # 8位日期 + 1位下划线 + 8位随机字符
        assert "_" in result


class TestAuthStatusEndpoint:
    """Tests for GET /api/bots/auth-status endpoint."""

    @staticmethod
    def _make_mock_request(data: dict):
        """Create a mock Request whose .json() returns the given data."""
        mock_request = AsyncMock()
        mock_request.json.return_value = data
        return mock_request

    @pytest.mark.asyncio
    async def test_auth_status_pending(self):
        """Test auth_status returns PENDING status."""
        from agentclaw.community.adapters.http.bot_management.router import get_auth_status
        from agentclaw.community.adapters.http.dependencies import RequestContext

        # Setup
        mock_passport_plugin = MagicMock()
        mock_passport_plugin.query_auth_status.return_value = {
            "status": "PENDING",
            "token": None,
        }

        mock_ctx = MagicMock(spec=RequestContext)
        mock_ctx.user_id = "123456"
        mock_ctx.nick_name = "Test User"

        mock_request = self._make_mock_request({"bot_id": "default"})

        result = await get_auth_status(
            request=mock_request,
            ctx=mock_ctx,
            passport_plugin=mock_passport_plugin,
            auth_rel_plugin=MagicMock(),
        )

        assert result.success is True
        assert result.data["status"] == "PENDING"
        assert result.data["message"] == "授权处理中"

    @pytest.mark.asyncio
    async def test_auth_status_issued_creates_bot(self):
        """Test auth_status ISSUED triggers bot creation."""
        from agentclaw.community.adapters.http.bot_management.router import get_auth_status
        from agentclaw.community.adapters.http.dependencies import RequestContext

        # Setup
        mock_passport_plugin = MagicMock()
        mock_passport_plugin.query_auth_status.return_value = {
            "status": "ISSUED",
            "token": "passport_token_123",
        }

        mock_ctx = MagicMock(spec=RequestContext)
        mock_ctx.user_id = "123456"
        mock_ctx.nick_name = "Test User"

        # Mock bot_service
        mock_bot_service = MagicMock()
        mock_bot_service.create_bot.return_value = {
            "bot_id": "default",
            "owner_id": "123456",
            "status": "ACTIVE",
        }

        mock_request = self._make_mock_request({"bot_id": "default"})

        result = await get_auth_status(
            request=mock_request,
            ctx=mock_ctx,
            bot_service=mock_bot_service,
            passport_plugin=mock_passport_plugin,
            auth_rel_plugin=MagicMock(),
        )

        assert result.success is True
        assert result.data["status"] == "ISSUED"
        assert "bot" in result.data

        # Verify create_bot was called with bot_id
        mock_bot_service.create_bot.assert_called_once()
        call_kwargs = mock_bot_service.create_bot.call_args[1]
        assert call_kwargs["bot_id"] == "default"
        assert call_kwargs["user_id"] == "123456"

    @pytest.mark.asyncio
    async def test_auth_status_rejected(self):
        """Test auth_status REJECTED returns error."""
        from agentclaw.community.adapters.http.bot_management.router import get_auth_status
        from agentclaw.community.adapters.http.dependencies import RequestContext

        # Setup
        mock_passport_plugin = MagicMock()
        mock_passport_plugin.query_auth_status.return_value = {
            "status": "REJECTED",
            "token": None,
        }

        mock_ctx = MagicMock(spec=RequestContext)
        mock_ctx.user_id = "123456"
        mock_ctx.nick_name = "Test User"

        mock_request = self._make_mock_request({"bot_id": "default"})

        result = await get_auth_status(
            request=mock_request,
            ctx=mock_ctx,
            passport_plugin=mock_passport_plugin,
            auth_rel_plugin=MagicMock(),
        )

        assert result.success is False
        assert result.data["status"] == "REJECTED"


class TestCreateServiceBotPublish:
    """Tests for create_bot with service bot type creates publish record."""

    @pytest.fixture
    def mock_bot_repository(self):
        """Mock bot repository."""
        repo = MagicMock()
        return repo

    @pytest.fixture
    def mock_device_service(self):
        """Mock device service."""
        service = MagicMock()
        return service

    @pytest.fixture
    def bot_service(self, mock_bot_repository):
        """Create BotService with mocked dependencies."""
        from agentclaw.community.core.bot_management.services.bot_service import BotService
        service = BotService(
            drm_reader=MagicMock(),
            repository=mock_bot_repository,
            allocation_config=MagicMock(mode="multi", max_devices_per_entity=5),
            device_binding_repo=MagicMock(),
            skill_set_factory=MagicMock(),
            cleanup_service=MagicMock(),
            bcn_service=MagicMock(),
            bot_publish_repo=MagicMock(),
            passport_plugin=MagicMock(),
            oss_record_repo=MagicMock(),
            bot_publish_service_provider=lambda: MagicMock(),
            device_service_provider=lambda: MagicMock(),
            path_factory=MagicMock(),
            template_service=MagicMock(),
            workspace_hosting_service=MagicMock(),
            collaborator_repo=MagicMock(),
            restart_lock_repo=MagicMock(),
            teclaw_provision_service_provider=lambda: MagicMock(is_teclaw=MagicMock(return_value=False)),
            device_status_client=MagicMock(),
            cron_auto_setup_service_provider=lambda: MagicMock(),
        )
        return service

    def test_create_service_bot_creates_publish_record(self, bot_service, mock_bot_repository, mock_device_service):
        """Test creating service bot should create publish record."""
        # Setup: bot 不存在
        new_bot = {
            "id": 123,  # 数据库主键
            "bot_id": "service_bot_001",
            "owner_id": "123456",
            "status": "ACTIVE",
            "bot_name": "Service Bot",
            "bot_type": "service",
        }

        mock_bot_repository.get_by_id_and_owner.side_effect = [None, new_bot, new_bot]
        mock_bot_repository.exists_by_owner_and_bot_id.return_value = False
        mock_bot_repository.list_by_owner.return_value = (0, [])
        mock_bot_repository.exists_by_bot_name.return_value = False

        # Mock 设备分配
        mock_device_result = MagicMock()
        mock_device_result.id = "binding_123"
        mock_device_result.device_id = "device_123"
        mock_device_result.status = "ACTIVE"
        mock_device_service.apply_device.return_value = mock_device_result

        # Mock insert 返回新 bot
        mock_bot_repository.insert.return_value = new_bot
        mock_bot_repository.update_by_owner.return_value = new_bot

        # Mock BotPublishRecord
        mock_publish_record = MagicMock()
        mock_publish_record.to_dict.return_value = {
            "id": 456,
            "source_bot_pk": 123,
            "source_bot_id": "service_bot_001",
            "publish_bot_id": "service_bot_001",
            "name": "Service Bot",
            "owner_id": "123456",
            "status": "init",
        }

        bot_service._device_service_provider = lambda: mock_device_service
        mock_publish_service = MagicMock()
        mock_publish_service.create_publish.return_value = mock_publish_record
        bot_service._bot_publish_provider = lambda: mock_publish_service

        with patch('agentclaw.community.core.bot_management.services.bot_service._get_engine_types', return_value=['moltis']), \
             patch('agentclaw.community.core.bot_management.services.bot_service.DEFAULT_ENGINE_TYPE', 'moltis'):

            result = bot_service.create_bot(
                user_id="123456",
                nick_name="Test User",
                bot_name="Service Bot",
                bot_id="service_bot_001",  # 指定 bot_id，跳过自动生成
                bot_type="service",
            )

        # Verify: bot 创建成功
        assert result["bot_id"] == "service_bot_001"
        assert result["bot_type"] == "service"

        # Verify: 发布单被创建
        mock_publish_service.create_publish.assert_called_once()
        call_kwargs = mock_publish_service.create_publish.call_args[1]
        assert call_kwargs["source_bot_pk"] == 123
        assert call_kwargs["source_bot_id"] == "service_bot_001"
        assert call_kwargs["publish_bot_id"] == "service_bot_001"
        assert call_kwargs["name"] == "Service Bot"
        assert call_kwargs["owner_id"] == "123456"
        assert call_kwargs["permission_owner"] == "owner"

        # Verify: 发布单信息保存到 bot_record
        assert "publish" in result
        assert result["publish"]["id"] == 456

    def test_create_personal_bot_no_publish_record(self, bot_service, mock_bot_repository, mock_device_service):
        """Test creating personal bot should NOT create publish record."""
        # Setup: bot 不存在
        new_bot = {
            "id": 124,
            "bot_id": "default",
            "owner_id": "123456",
            "status": "ACTIVE",
            "bot_name": "Personal Bot",
            "bot_type": "personal",
        }

        mock_bot_repository.get_by_id_and_owner.return_value = new_bot
        mock_bot_repository.exists_by_owner_and_bot_id.return_value = False
        mock_bot_repository.list_by_owner.return_value = (0, [])
        mock_bot_repository.exists_by_bot_name.return_value = False

        # Mock 设备分配：ACTIVE + 空 provider 避免触发 update_bot_ext
        mock_device_result = MagicMock()
        mock_device_result.id = "binding_124"
        mock_device_result.device_id = "device_124"
        mock_device_result.status = "ACTIVE"
        mock_device_result.device_provider = ""
        mock_device_service.apply_device.return_value = mock_device_result

        # Mock insert 返回新 bot
        mock_bot_repository.insert.return_value = new_bot
        mock_bot_repository.update_by_owner.return_value = new_bot

        bot_service._device_service_provider = lambda: mock_device_service
        mock_publish_service = MagicMock()
        bot_service._bot_publish_provider = lambda: mock_publish_service

        with patch('agentclaw.community.core.bot_management.services.bot_service._get_engine_types', return_value=['moltis']), \
             patch('agentclaw.community.core.bot_management.services.bot_service.DEFAULT_ENGINE_TYPE', 'moltis'):

            result = bot_service.create_bot(
                user_id="123456",
                nick_name="Test User",
                bot_type="personal",
            )

        # Verify: bot 创建成功
        assert result["bot_id"] == "default"
        assert result["bot_type"] == "personal"

        # Verify: 发布单未被创建
        mock_publish_service.create_publish.assert_not_called()

        # Verify: bot_record 中没有 publish 字段
        assert "publish" not in result

    def test_create_service_bot_publish_failure_not_block_bot_creation(self, bot_service, mock_bot_repository, mock_device_service):
        """Test publish record creation failure should NOT block bot creation."""
        # Setup: bot 不存在
        new_bot = {
            "id": 125,
            "bot_id": "service_bot_002",
            "owner_id": "123456",
            "status": "ACTIVE",
            "bot_name": "Service Bot 2",
            "bot_type": "service",
        }

        # 第一次调用（幂等性检查）返回 None；后续调用（update_bot_ext → get_bot）返回 new_bot
        mock_bot_repository.get_by_id_and_owner.side_effect = [None, new_bot, new_bot]
        mock_bot_repository.exists_by_owner_and_bot_id.return_value = False
        mock_bot_repository.list_by_owner.return_value = (0, [])
        mock_bot_repository.exists_by_bot_name.return_value = False

        # Mock 设备分配
        mock_device_result = MagicMock()
        mock_device_result.id = "binding_125"
        mock_device_result.device_id = "device_125"
        mock_device_result.status = "ACTIVE"
        mock_device_service.apply_device.return_value = mock_device_result

        # Mock insert 返回新 bot
        mock_bot_repository.insert.return_value = new_bot
        mock_bot_repository.update_by_owner.return_value = new_bot

        bot_service._device_service_provider = lambda: mock_device_service
        mock_publish_service = MagicMock()
        mock_publish_service.create_publish.side_effect = Exception("Database error")
        bot_service._bot_publish_provider = lambda: mock_publish_service

        with patch('agentclaw.community.core.bot_management.services.bot_service._get_engine_types', return_value=['moltis']), \
             patch('agentclaw.community.core.bot_management.services.bot_service.DEFAULT_ENGINE_TYPE', 'moltis'):

            result = bot_service.create_bot(
                user_id="123456",
                nick_name="Test User",
                bot_name="Service Bot 2",
                bot_id="service_bot_002",  # 指定 bot_id，跳过自动生成
                bot_type="service",
            )

        # Verify: bot 仍然创建成功
        assert result["bot_id"] == "service_bot_002"
        assert result["bot_type"] == "service"

        # Verify: 发布单创建被尝试调用
        mock_publish_service.create_publish.assert_called_once()

        # Verify: bot_record 中没有 publish 字段（因为创建失败）
        assert "publish" not in result
        assert "publish" not in result
