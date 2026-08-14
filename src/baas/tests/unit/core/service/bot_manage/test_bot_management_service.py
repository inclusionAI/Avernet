"""Tests for BotManagementService.

Covers Bot CRUD operations and session query methods.
Requirements: API-01, API-05
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from secbaas.community.api.bot_manage import (
    BotConfig,
    BotStatus,
    StopBotResponse,
    UpdateDevicesResponse,
)
from secbaas.community.api.bot_runtime import BotNotFoundError
from secbaas.community.api.device_manage import DeployConfig
from secbaas.community.api.device_manage._models import ResourceSpecification
from secbaas.community.api.publish_manage import (
    DEFAULT_CALLBACK_TIMEOUT_SECONDS,
    PublishStatus,
    PublishType,
    RestartScope,
)
from secbaas.community.core.service.bot_manage import (
    DefaultBotManagementService as BotManagementService,
)
from secbaas.community.core.service.bot_manage import (
    merge_deploy_config,
    resolve_callback_timeout,
)


def _make_service(
    bot_repo=None,
    device_repo=None,
    system_config_repo=None,
    publish_service=None,
    bot_service=None,
    health_checker=None,
):
    """Create a DefaultBotManagementService instance with mocked dependencies."""
    if bot_repo is None:
        bot_repo = MagicMock()
        # Ensure list_by_bot_uuid returns records with valid extra_config for config resolution
        default_record = MagicMock()
        default_record.extra_config = {}
        bot_repo.list_by_bot_uuid.return_value = [default_record]
    return BotManagementService(
        bot_repo=bot_repo,
        device_repo=device_repo if device_repo is not None else MagicMock(),
        system_config_repo=system_config_repo
        if system_config_repo is not None
        else MagicMock(),
        publish_service=publish_service if publish_service is not None else MagicMock(),
        bot_service=bot_service if bot_service is not None else MagicMock(),
        health_checker=health_checker if health_checker is not None else MagicMock(),
    )


class TestCreateBot:
    """Tests for BotManagementService.create_bot"""

    @pytest.mark.asyncio
    async def test_create_bot_success(self):
        """Test successful bot creation through publish workflow"""
        mock_bot = MagicMock()
        mock_bot.id = 1
        mock_bot.bot_uuid = "BOT-20241201-12345678"
        mock_bot.model_dump = MagicMock(
            return_value={
                "id": 1,
                "bot_uuid": "BOT-20241201-12345678",
                "tenant": "test_tenant",
                "env": "dev",
                "domain": "default",
                "is_deleted": 0,
                "creator": "user1",
                "modifier": "user1",
                "status": "PENDING",
                "name": "Test Bot",
                "description": None,
                "template_uuid": None,
                "replica_desired": 1,
                "replica_minimum": 1,
                "replica_maximum": 10,
                "auto_scaling_enabled": 0,
                "sla_grade": "standard",
                "gmt_create": "2024-01-01T00:00:00",
                "gmt_modified": "2024-01-01T00:00:00",
                "config": None,
            }
        )

        mock_publish = MagicMock()
        mock_publish.id = 123  # Publish ID for workflow tracking

        mock_sys_config = MagicMock()
        mock_sys_config.id = 1
        mock_sys_config.conf_key = "publish.callback_timeout_seconds"
        mock_sys_config.conf_value = "60"
        mock_sys_config.env = "dev"
        mock_sys_config.name = ""
        mock_sys_config.description = ""
        mock_sys_config.creator = "system"
        mock_sys_config.modifier = "system"

        mock_bot_service = MagicMock()
        mock_bot_service.create_bot = AsyncMock(return_value=mock_bot)

        mock_publish_service = MagicMock()
        mock_publish_service.create_publish = AsyncMock(return_value=mock_publish)

        mock_system_config_repo = MagicMock()
        mock_system_config_repo.get_by_env_and_key.return_value = mock_sys_config

        service = _make_service(
            bot_service=mock_bot_service,
            publish_service=mock_publish_service,
            system_config_repo=mock_system_config_repo,
        )
        result = await service.create_bot(
            tenant="test_tenant",
            name="Test Bot",
            template_uuid="TPL-12345678",
            device_count=2,
            operator="user1",
            request_id="test-request-id-12345678901234567890",
        )

        assert result is not None
        assert result.bot_uuid == "BOT-20241201-12345678"
        assert result.publish_id == 123

    @pytest.mark.asyncio
    async def test_create_bot_with_description(self):
        """Test bot creation with optional description"""
        mock_bot = MagicMock()
        mock_bot.id = 1
        mock_bot.bot_uuid = "BOT-20241201-12345678"
        mock_bot.model_dump = MagicMock(
            return_value={
                "id": 1,
                "bot_uuid": "BOT-20241201-12345678",
                "tenant": "test_tenant",
                "env": "dev",
                "domain": "default",
                "is_deleted": 0,
                "creator": "user1",
                "modifier": "user1",
                "status": "PENDING",
                "name": "Test Bot",
                "description": "Test description",
                "template_uuid": None,
                "replica_desired": 1,
                "replica_minimum": 1,
                "replica_maximum": 10,
                "auto_scaling_enabled": 0,
                "sla_grade": "standard",
                "gmt_create": "2024-01-01T00:00:00",
                "gmt_modified": "2024-01-01T00:00:00",
                "config": None,
            }
        )

        mock_publish = MagicMock()
        mock_publish.id = 456  # Publish ID for workflow tracking

        mock_sys_config = MagicMock()
        mock_sys_config.id = 1
        mock_sys_config.conf_key = "publish.callback_timeout_seconds"
        mock_sys_config.conf_value = "60"
        mock_sys_config.env = "dev"
        mock_sys_config.name = ""
        mock_sys_config.description = ""
        mock_sys_config.creator = "system"
        mock_sys_config.modifier = "system"

        mock_bot_service = MagicMock()
        mock_bot_service.create_bot = AsyncMock(return_value=mock_bot)

        mock_publish_service = MagicMock()
        mock_publish_service.create_publish = AsyncMock(return_value=mock_publish)

        mock_system_config_repo = MagicMock()
        mock_system_config_repo.get_by_env_and_key.return_value = mock_sys_config

        service = _make_service(
            bot_service=mock_bot_service,
            publish_service=mock_publish_service,
            system_config_repo=mock_system_config_repo,
        )
        result = await service.create_bot(
            tenant="test_tenant",
            name="Test Bot",
            template_uuid="TPL-12345678",
            device_count=2,
            operator="user1",
            request_id="test-request-id-12345678901234567890",
            description="Test description",
        )

        assert result is not None
        assert result.publish_id == 456


class TestDestroyBot:
    """Tests for BotManagementService.destroy_bot"""

    @pytest.mark.asyncio
    async def test_destroy_bot_success(self):
        """Test successful bot destruction returns DestroyBotResponse with publish_id"""
        mock_bot_response = MagicMock()
        mock_bot_response.id = 1
        mock_bot_response.bot_uuid = "BOT-001"
        mock_bot_response.model_dump = MagicMock(
            return_value={
                "id": 1,
                "bot_uuid": "BOT-001",
                "tenant": "test_tenant",
                "env": "dev",
                "domain": "default",
                "is_deleted": 0,
                "creator": "user1",
                "modifier": "user1",
                "status": "ACTIVE",
                "name": "Test Bot",
                "description": None,
                "template_uuid": None,
                "replica_desired": 1,
                "replica_minimum": 1,
                "replica_maximum": 10,
                "auto_scaling_enabled": 0,
                "sla_grade": "standard",
                "gmt_create": "2024-01-01T00:00:00",
                "gmt_modified": "2024-01-01T00:00:00",
                "config": None,
            }
        )

        mock_publish = MagicMock()
        mock_publish.id = 456

        mock_bot_repo = MagicMock()

        mock_bot_service = MagicMock()
        mock_bot_service.get_bot = AsyncMock(return_value=mock_bot_response)

        mock_publish_service = MagicMock()
        mock_publish_service.create_publish = AsyncMock(return_value=mock_publish)

        service = _make_service(
            bot_repo=mock_bot_repo,
            publish_service=mock_publish_service,
            bot_service=mock_bot_service,
        )
        with patch.object(
            service, "get_bot", new_callable=AsyncMock, return_value=mock_bot_response
        ):
            result = await service.destroy_bot(
                tenant="test_tenant",
                bot_uuid="BOT-001",
                operator="user1",
                request_id="test-request-id-12345678901234567890",
            )

            assert result is not None
            assert result.publish_id == 456
            assert result.bot_uuid == "BOT-001"
            mock_publish_service.create_publish.assert_called_once()
            call_kwargs = mock_publish_service.create_publish.call_args.kwargs
            assert call_kwargs["publish_type"] == PublishType.DESTROY
            # Verify update_status was called with DESTROYING
            mock_bot_repo.update_status.assert_called_once()

    @pytest.mark.asyncio
    async def test_destroy_bot_not_found(self):
        """Test destroy returns None when bot not found"""
        service = _make_service()
        with patch.object(
            service, "get_bot", new_callable=AsyncMock, return_value=None
        ):
            result = await service.destroy_bot(
                tenant="test_tenant",
                bot_uuid="BOT-NOT-FOUND",
                operator="user1",
                request_id="test-request-id-12345678901234567890",
            )

            assert result is None


class TestGetBot:
    """Tests for BotManagementService.get_bot"""

    @pytest.mark.asyncio
    async def test_get_bot_success(self):
        """Test successful bot retrieval"""
        mock_bot = MagicMock()
        mock_bot.id = 1
        mock_bot.bot_uuid = "BOT-001"
        mock_bot.tenant = "test_tenant"
        mock_bot.env = "dev"
        mock_bot.domain = "default"
        mock_bot.name = "Test Bot"
        mock_bot.description = None
        mock_bot.template_uuid = "tpl-123"
        mock_bot.sla_grade = "standard"
        mock_bot.replica_desired = 1
        mock_bot.replica_minimum = 1
        mock_bot.replica_maximum = 10
        mock_bot.auto_scaling_enabled = 0
        mock_bot.status = BotStatus.ACTIVE.value
        mock_bot.extra_config = {}
        mock_bot.is_deleted = 0
        mock_bot.creator = "user1"
        mock_bot.modifier = "user1"
        mock_bot.gmt_create = "2024-01-01T00:00:00"
        mock_bot.gmt_modified = "2024-01-01T00:00:00"

        mock_bot_repo = MagicMock()
        mock_bot_repo.list_by_bot_uuid.return_value = [mock_bot]

        mock_device_repo = MagicMock()
        mock_device_repo.list_by_bot_id.return_value = []

        service = _make_service(
            bot_repo=mock_bot_repo,
            device_repo=mock_device_repo,
        )
        result = await service.get_bot(
            tenant="test_tenant",
            bot_uuid="BOT-001",
        )

        assert result is not None
        assert result.bot_uuid == "BOT-001"

    @pytest.mark.asyncio
    async def test_get_bot_not_found(self):
        mock_bot_repo = MagicMock()
        mock_bot_repo.list_by_bot_uuid.return_value = []

        service = _make_service(bot_repo=mock_bot_repo)
        result = await service.get_bot(
            tenant="test_tenant",
            bot_uuid="BOT-NOT-FOUND",
        )

        assert result is None


class TestGetBotWithHealthCheck:
    """Tests for BotManagementService.get_bot with health_check parameter."""

    @pytest.mark.asyncio
    async def test_get_bot_health_check_false_does_not_call_health_checker(self):
        """health_check=False (default) should not call health checker."""
        mock_bot = MagicMock()
        mock_bot.id = 1
        mock_bot.bot_uuid = "BOT-001"
        mock_bot.tenant = "test_tenant"
        mock_bot.env = "dev"
        mock_bot.domain = "default"
        mock_bot.name = "Test Bot"
        mock_bot.description = None
        mock_bot.template_uuid = "tpl-123"
        mock_bot.sla_grade = "standard"
        mock_bot.replica_desired = 1
        mock_bot.replica_minimum = 1
        mock_bot.replica_maximum = 10
        mock_bot.auto_scaling_enabled = 0
        mock_bot.status = BotStatus.ACTIVE.value
        mock_bot.extra_config = {}
        mock_bot.is_deleted = 0
        mock_bot.creator = "user1"
        mock_bot.modifier = "user1"
        mock_bot.gmt_create = "2024-01-01T00:00:00"
        mock_bot.gmt_modified = "2024-01-01T00:00:00"

        mock_bot_repo = MagicMock()
        mock_bot_repo.list_by_bot_uuid.return_value = [mock_bot]

        mock_device_repo = MagicMock()
        mock_device_repo.list_by_bot_id.return_value = []

        mock_health_checker = MagicMock()
        mock_health_checker.check_single_device = AsyncMock()

        service = _make_service(
            bot_repo=mock_bot_repo,
            device_repo=mock_device_repo,
            health_checker=mock_health_checker,
        )
        result = await service.get_bot(
            tenant="test_tenant",
            bot_uuid="BOT-001",
            health_check=False,
        )

        assert result is not None
        assert result.bot_uuid == "BOT-001"
        mock_health_checker.check_single_device.assert_not_called()

    @pytest.mark.asyncio
    async def test_get_bot_health_check_true_calls_health_checker(self):
        """health_check=True should call check_single_device on each device."""
        mock_bot = MagicMock()
        mock_bot.id = 1
        mock_bot.bot_uuid = "BOT-001"
        mock_bot.tenant = "test_tenant"
        mock_bot.env = "dev"
        mock_bot.domain = "default"
        mock_bot.name = "Test Bot"
        mock_bot.description = None
        mock_bot.template_uuid = "tpl-123"
        mock_bot.sla_grade = "standard"
        mock_bot.replica_desired = 1
        mock_bot.replica_minimum = 1
        mock_bot.replica_maximum = 10
        mock_bot.auto_scaling_enabled = 0
        mock_bot.status = BotStatus.ACTIVE.value
        mock_bot.extra_config = {}
        mock_bot.is_deleted = 0
        mock_bot.creator = "user1"
        mock_bot.modifier = "user1"
        mock_bot.gmt_create = "2024-01-01T00:00:00"
        mock_bot.gmt_modified = "2024-01-01T00:00:00"

        mock_bot_repo = MagicMock()
        mock_bot_repo.list_by_bot_uuid.return_value = [mock_bot]

        mock_device_repo = MagicMock()
        mock_device_repo.list_by_bot_id.return_value = []

        mock_health_checker = MagicMock()
        mock_health_checker.check_single_device = AsyncMock()

        service = _make_service(
            bot_repo=mock_bot_repo,
            device_repo=mock_device_repo,
            health_checker=mock_health_checker,
        )
        result = await service.get_bot(
            tenant="test_tenant",
            bot_uuid="BOT-001",
            health_check=True,
        )

        assert result is not None
        assert result.bot_uuid == "BOT-001"
        # No devices means health checker should not be called
        mock_health_checker.check_single_device.assert_not_called()

    @pytest.mark.asyncio
    async def test_get_bot_health_check_true_with_devices_calls_checker(self):
        """health_check=True with devices should call check_single_device."""
        mock_bot = MagicMock()
        mock_bot.id = 1
        mock_bot.bot_uuid = "BOT-001"
        mock_bot.tenant = "test_tenant"
        mock_bot.env = "dev"
        mock_bot.domain = "default"
        mock_bot.name = "Test Bot"
        mock_bot.description = None
        mock_bot.template_uuid = "tpl-123"
        mock_bot.sla_grade = "standard"
        mock_bot.replica_desired = 1
        mock_bot.replica_minimum = 1
        mock_bot.replica_maximum = 10
        mock_bot.auto_scaling_enabled = 0
        mock_bot.status = BotStatus.ACTIVE.value
        mock_bot.extra_config = {}
        mock_bot.is_deleted = 0
        mock_bot.creator = "user1"
        mock_bot.modifier = "user1"
        mock_bot.gmt_create = "2024-01-01T00:00:00"
        mock_bot.gmt_modified = "2024-01-01T00:00:00"

        mock_device = MagicMock()
        mock_device.device_uuid = "DEV-001"
        mock_device.provider_device_id = "sandbox-123@0"
        mock_device.provider_type = "ARCA"
        mock_device.status = "ACTIVE"
        mock_device.gmt_create = "2024-01-01T00:00:00"

        mock_bot_repo = MagicMock()
        mock_bot_repo.list_by_bot_uuid.return_value = [mock_bot]

        mock_device_repo = MagicMock()
        mock_device_repo.list_by_bot_id.return_value = [mock_device]

        mock_health_checker = MagicMock()
        mock_health_checker.check_single_device = AsyncMock(
            return_value=(
                "sandbox-123@0",
                MagicMock(overall_healthy=True),
            )
        )

        service = _make_service(
            bot_repo=mock_bot_repo,
            device_repo=mock_device_repo,
            health_checker=mock_health_checker,
        )
        result = await service.get_bot(
            tenant="test_tenant",
            bot_uuid="BOT-001",
            health_check=True,
        )

        assert result is not None
        assert result.bot_uuid == "BOT-001"
        mock_health_checker.check_single_device.assert_called_once()
        call_args = mock_health_checker.check_single_device.call_args
        # First positional arg is the device
        assert call_args[0][0] is not None
        # Second positional arg is active_engine (None)
        assert len(call_args[0]) >= 2
        assert call_args[0][1] is None

    @pytest.mark.asyncio
    async def test_get_bot_health_check_true_passes_engine_type(self):
        """engine_type param should be passed to check_single_device."""
        mock_bot = MagicMock()
        mock_bot.id = 1
        mock_bot.bot_uuid = "BOT-001"
        mock_bot.tenant = "test_tenant"
        mock_bot.env = "dev"
        mock_bot.domain = "default"
        mock_bot.name = "Test Bot"
        mock_bot.description = None
        mock_bot.template_uuid = "tpl-123"
        mock_bot.sla_grade = "standard"
        mock_bot.replica_desired = 1
        mock_bot.replica_minimum = 1
        mock_bot.replica_maximum = 10
        mock_bot.auto_scaling_enabled = 0
        mock_bot.status = BotStatus.ACTIVE.value
        mock_bot.extra_config = {}
        mock_bot.is_deleted = 0
        mock_bot.creator = "user1"
        mock_bot.modifier = "user1"
        mock_bot.gmt_create = "2024-01-01T00:00:00"
        mock_bot.gmt_modified = "2024-01-01T00:00:00"

        mock_device = MagicMock()
        mock_device.device_uuid = "DEV-001"
        mock_device.provider_device_id = "sandbox-123@0"
        mock_device.provider_type = "ARCA"
        mock_device.status = "ACTIVE"
        mock_device.gmt_create = "2024-01-01T00:00:00"

        mock_bot_repo = MagicMock()
        mock_bot_repo.list_by_bot_uuid.return_value = [mock_bot]

        mock_device_repo = MagicMock()
        mock_device_repo.list_by_bot_id.return_value = [mock_device]

        mock_health_checker = MagicMock()
        mock_health_checker.check_single_device = AsyncMock(
            return_value=(
                "sandbox-123@0",
                MagicMock(overall_healthy=True),
            )
        )

        service = _make_service(
            bot_repo=mock_bot_repo,
            device_repo=mock_device_repo,
            health_checker=mock_health_checker,
        )
        result = await service.get_bot(
            tenant="test_tenant",
            bot_uuid="BOT-001",
            health_check=True,
            engine_type="openclaw",
        )

        assert result is not None
        assert result.bot_uuid == "BOT-001"
        mock_health_checker.check_single_device.assert_called_once()
        call_args = mock_health_checker.check_single_device.call_args
        # Second positional arg is active_engine
        assert len(call_args[0]) >= 2
        assert call_args[0][1] == "openclaw"


class TestListBots:
    """Tests for BotManagementService.list_bots"""

    @pytest.mark.asyncio
    async def test_list_bots_success(self):
        """Test successful bot listing"""
        mock_bot1 = MagicMock()
        mock_bot1.bot_uuid = "BOT-001"
        mock_bot2 = MagicMock()
        mock_bot2.bot_uuid = "BOT-002"

        mock_result = MagicMock()
        mock_result.items = [mock_bot1, mock_bot2]
        mock_result.total = 2
        mock_result.page = 1
        mock_result.page_size = 20

        mock_bot_service = MagicMock()
        mock_bot_service.list_bots = AsyncMock(return_value=mock_result)

        service = _make_service(bot_service=mock_bot_service)
        result = await service.list_bots(
            tenant="test_tenant",
            page=1,
            page_size=20,
        )

        assert result.total == 2
        assert len(result.items) == 2
        mock_bot_service.list_bots.assert_called_once()

    @pytest.mark.asyncio
    async def test_list_bots_pagination_limit(self):
        """Test page_size > 100 is capped at 100"""
        mock_result = MagicMock()
        mock_result.items = []
        mock_result.total = 0
        mock_result.page = 1
        mock_result.page_size = 100

        mock_bot_service = MagicMock()
        mock_bot_service.list_bots = AsyncMock(return_value=mock_result)

        service = _make_service(bot_service=mock_bot_service)
        await service.list_bots(
            tenant="test_tenant",
            page=1,
            page_size=200,  # Should be capped to 100
        )

        call_kwargs = mock_bot_service.list_bots.call_args.kwargs
        assert call_kwargs["page_size"] == 100


class TestMergeDeployConfig:
    """Tests for merge_deploy_config field-level merge behavior."""

    def test_merge_current_none_returns_override_only(self):
        """When current is None, result is equivalent to the override alone."""
        override = DeployConfig(ttl_in_minutes=120, docker_image="img:v2")
        result = merge_deploy_config(None, override)
        assert result.ttl_in_minutes == 120
        assert result.docker_image == "img:v2"
        assert result.envs is None
        assert result.mount_points is None

    def test_merge_override_none_fields_preserved(self):
        """Current fields NOT in override are preserved."""
        current = DeployConfig(
            envs={"KEY": "val"},
            mount_points=[],
            ttl_in_minutes=60,
            resource_spec=ResourceSpecification(cpu=2, memory=4096),
        )
        override = DeployConfig(docker_image="img:v2", ttl_in_minutes=120)
        result = merge_deploy_config(current, override)
        assert result.envs == {"KEY": "val"}
        assert result.mount_points == []
        assert result.resource_spec == ResourceSpecification(cpu=2, memory=4096)
        assert result.ttl_in_minutes == 120
        assert result.docker_image == "img:v2"

    def test_merge_override_only_fields(self):
        """Override-only fields are set without touching current fields."""
        current = DeployConfig(ttl_in_minutes=60)
        override = DeployConfig(docker_image="img:v2")
        result = merge_deploy_config(current, override)
        assert result.ttl_in_minutes == 60
        assert result.docker_image == "img:v2"

    def test_merge_dict_override(self):
        """Plain dict override works the same as DeployConfig instance."""
        current = DeployConfig(envs={"K": "V"}, ttl_in_minutes=60)
        override_dict = {"ttl_in_minutes": 120, "docker_image": "img:v2"}
        result = merge_deploy_config(current, override_dict)
        assert result.envs == {"K": "V"}
        assert result.ttl_in_minutes == 120
        assert result.docker_image == "img:v2"

    def test_merge_current_empty_none_fields(self):
        """When current's fields are all None, override fills everything."""
        current = DeployConfig()
        override = DeployConfig(ttl_in_minutes=120)
        result = merge_deploy_config(current, override)
        assert result.ttl_in_minutes == 120
        assert result.docker_image is None


class TestScaleBot:
    """Tests for BotManagementService.scale_bot"""

    @pytest.mark.asyncio
    async def test_scale_bot_up_success(self):
        """Test SCALE_UP publish creation returns ScaleBotResponse with publish_id"""
        mock_bot_response = MagicMock()
        mock_bot_response.id = 1
        mock_bot_response.bot_uuid = "BOT-001"
        mock_bot_response.model_dump = MagicMock(
            return_value={
                "id": 1,
                "bot_uuid": "BOT-001",
                "tenant": "test_tenant",
                "env": "dev",
                "domain": "default",
                "is_deleted": 0,
                "creator": "user1",
                "modifier": "user1",
                "status": "ACTIVE",
                "name": "Test Bot",
                "description": None,
                "template_uuid": None,
                "replica_desired": 1,
                "replica_minimum": 1,
                "replica_maximum": 10,
                "auto_scaling_enabled": 0,
                "sla_grade": "standard",
                "gmt_create": "2024-01-01T00:00:00",
                "gmt_modified": "2024-01-01T00:00:00",
                "config": None,
            }
        )

        mock_publish = MagicMock()
        mock_publish.id = 789

        mock_device_repo = MagicMock()
        mock_device_repo.list_by_bot_id.return_value = [MagicMock()]  # 1 device

        mock_publish_service = MagicMock()
        mock_publish_service.create_publish = AsyncMock(return_value=mock_publish)

        service = _make_service(
            device_repo=mock_device_repo,
            publish_service=mock_publish_service,
        )
        with patch.object(
            service, "get_bot", new_callable=AsyncMock, return_value=mock_bot_response
        ):
            result = await service.scale_bot(
                tenant="test_tenant",
                bot_uuid="BOT-001",
                target_count=3,  # Scale from 1 to 3
                operator="user1",
                request_id="test-request-id-12345678901234567890",
            )

            assert result.publish_id == 789
            assert result.target_count == 3
            assert result.bot_uuid == "BOT-001"
            call_kwargs = mock_publish_service.create_publish.call_args.kwargs
            assert call_kwargs["publish_type"] == PublishType.SCALE_UP

    @pytest.mark.asyncio
    async def test_scale_bot_invalid_target(self):
        """Test scale_bot raises ValueError for target < 1"""
        mock_bot_response = MagicMock()
        mock_bot_response.id = 1
        mock_bot_response.bot_uuid = "BOT-001"
        mock_bot_response.model_dump = MagicMock(
            return_value={
                "id": 1,
                "bot_uuid": "BOT-001",
                "tenant": "test_tenant",
                "env": "dev",
                "domain": "default",
                "is_deleted": 0,
                "creator": "user1",
                "modifier": "user1",
                "status": "ACTIVE",
                "name": "Test Bot",
                "description": None,
                "template_uuid": None,
                "replica_desired": 1,
                "replica_minimum": 1,
                "replica_maximum": 10,
                "auto_scaling_enabled": 0,
                "sla_grade": "standard",
                "gmt_create": "2024-01-01T00:00:00",
                "gmt_modified": "2024-01-01T00:00:00",
                "config": None,
            }
        )

        service = _make_service()
        with patch.object(
            service, "get_bot", new_callable=AsyncMock, return_value=mock_bot_response
        ):
            with pytest.raises(ValueError, match="Target count must be at least 1"):
                await service.scale_bot(
                    tenant="test_tenant",
                    bot_uuid="BOT-001",
                    target_count=0,
                    operator="user1",
                    request_id="test-request-id-12345678901234567890",
                )

    @pytest.mark.asyncio
    async def test_scale_bot_no_change(self):
        """Test scale_bot raises ValueError when target equals current"""
        mock_bot_response = MagicMock()
        mock_bot_response.id = 1
        mock_bot_response.bot_uuid = "BOT-001"
        mock_bot_response.model_dump = MagicMock(
            return_value={
                "id": 1,
                "bot_uuid": "BOT-001",
                "tenant": "test_tenant",
                "env": "dev",
                "domain": "default",
                "is_deleted": 0,
                "creator": "user1",
                "modifier": "user1",
                "status": "ACTIVE",
                "name": "Test Bot",
                "description": None,
                "template_uuid": None,
                "replica_desired": 1,
                "replica_minimum": 1,
                "replica_maximum": 10,
                "auto_scaling_enabled": 0,
                "sla_grade": "standard",
                "gmt_create": "2024-01-01T00:00:00",
                "gmt_modified": "2024-01-01T00:00:00",
                "config": None,
            }
        )

        mock_device_repo = MagicMock()
        mock_device_repo.list_by_bot_id.return_value = [
            MagicMock(),
            MagicMock(),
        ]

        service = _make_service(device_repo=mock_device_repo)
        with patch.object(
            service, "get_bot", new_callable=AsyncMock, return_value=mock_bot_response
        ):
            with pytest.raises(ValueError, match="no scaling needed"):
                await service.scale_bot(
                    tenant="test_tenant",
                    bot_uuid="BOT-001",
                    target_count=2,  # Same as current
                    operator="user1",
                    request_id="test-request-id-12345678901234567890",
                )

    @pytest.mark.asyncio
    async def test_scale_bot_up_with_auto_approve(self):
        """scale_bot with auto_approve_publish=True calls approve_stage."""
        mock_bot_response = MagicMock()
        mock_bot_response.id = 1
        mock_bot_response.bot_uuid = "BOT-001"
        mock_bot_response.model_dump = MagicMock(
            return_value={
                "id": 1,
                "bot_uuid": "BOT-001",
                "tenant": "test_tenant",
                "env": "dev",
                "domain": "default",
                "is_deleted": 0,
                "creator": "user1",
                "modifier": "user1",
                "status": "ACTIVE",
                "name": "Test Bot",
                "description": None,
                "template_uuid": None,
                "replica_desired": 1,
                "replica_minimum": 1,
                "replica_maximum": 10,
                "auto_scaling_enabled": 0,
                "sla_grade": "standard",
                "gmt_create": "2024-01-01T00:00:00",
                "gmt_modified": "2024-01-01T00:00:00",
                "config": None,
            }
        )

        mock_publish = MagicMock()
        mock_publish.id = 789

        mock_device_repo = MagicMock()
        mock_device_repo.list_by_bot_id.return_value = [MagicMock()]  # 1 device

        mock_publish_service = MagicMock()
        mock_publish_service.create_publish = AsyncMock(return_value=mock_publish)
        # Simulate PENDING → SUCCESS for the auto-approve loop
        mock_publish_pending = MagicMock()
        mock_publish_pending.id = 789
        mock_publish_pending.status = PublishStatus.PENDING.value
        mock_publish_success = MagicMock()
        mock_publish_success.id = 789
        mock_publish_success.status = PublishStatus.SUCCESS.value
        mock_publish_service.get_publish = AsyncMock(
            side_effect=[mock_publish_pending, mock_publish_success]
        )
        mock_publish_service.approve_stage = AsyncMock()

        service = _make_service(
            device_repo=mock_device_repo,
            publish_service=mock_publish_service,
        )
        with patch.object(
            service, "get_bot", new_callable=AsyncMock, return_value=mock_bot_response
        ):
            result = await service.scale_bot(
                tenant="test_tenant",
                bot_uuid="BOT-001",
                target_count=3,
                operator="user1",
                request_id="test-request-id-12345678901234567890",
                auto_approve_publish=True,
            )

        assert result.publish_id == 789
        assert result.target_count == 3

        # Verify PublishConfig has auto_approve=True
        call_kwargs = mock_publish_service.create_publish.call_args.kwargs
        assert call_kwargs["config"].auto_approve is True

        # background task spawned by _auto_approve_publish needs an event loop tick
        await asyncio.sleep(0)
        # Verify approve_stage was called by the auto-approve loop
        mock_publish_service.approve_stage.assert_called_once()
        approve_kwargs = mock_publish_service.approve_stage.call_args.kwargs
        assert approve_kwargs["tenant"] == "test_tenant"
        assert approve_kwargs["publish_id"] == 789
        assert approve_kwargs["operator"] == "user1"

    @pytest.mark.asyncio
    async def test_scale_bot_up_without_auto_approve(self):
        """scale_bot with default auto_approve_publish does NOT call approve_stage."""
        mock_bot_response = MagicMock()
        mock_bot_response.id = 1
        mock_bot_response.bot_uuid = "BOT-001"
        mock_bot_response.model_dump = MagicMock(
            return_value={
                "id": 1,
                "bot_uuid": "BOT-001",
                "tenant": "test_tenant",
                "env": "dev",
                "domain": "default",
                "is_deleted": 0,
                "creator": "user1",
                "modifier": "user1",
                "status": "ACTIVE",
                "name": "Test Bot",
                "description": None,
                "template_uuid": None,
                "replica_desired": 1,
                "replica_minimum": 1,
                "replica_maximum": 10,
                "auto_scaling_enabled": 0,
                "sla_grade": "standard",
                "gmt_create": "2024-01-01T00:00:00",
                "gmt_modified": "2024-01-01T00:00:00",
                "config": None,
            }
        )

        mock_publish = MagicMock()
        mock_publish.id = 789

        mock_device_repo = MagicMock()
        mock_device_repo.list_by_bot_id.return_value = [MagicMock()]

        mock_publish_service = MagicMock()
        mock_publish_service.create_publish = AsyncMock(return_value=mock_publish)
        mock_publish_service.approve_stage = AsyncMock()

        service = _make_service(
            device_repo=mock_device_repo,
            publish_service=mock_publish_service,
        )
        with patch.object(
            service, "get_bot", new_callable=AsyncMock, return_value=mock_bot_response
        ):
            result = await service.scale_bot(
                tenant="test_tenant",
                bot_uuid="BOT-001",
                target_count=3,
                operator="user1",
                request_id="test-request-id-12345678901234567890",
            )

        assert result.publish_id == 789
        assert result.target_count == 3

        # Verify PublishConfig has auto_approve=False (default)
        call_kwargs = mock_publish_service.create_publish.call_args.kwargs
        assert call_kwargs["config"].auto_approve is False

        # Verify approve_stage was NOT called (no auto-approve)
        mock_publish_service.approve_stage.assert_not_called()

    @pytest.mark.asyncio
    async def test_scale_bot_up_with_auto_approve_false_explicit(self):
        """scale_bot with auto_approve_publish=False explicitly does NOT call approve_stage."""
        mock_bot_response = MagicMock()
        mock_bot_response.id = 1
        mock_bot_response.bot_uuid = "BOT-001"
        mock_bot_response.model_dump = MagicMock(
            return_value={
                "id": 1,
                "bot_uuid": "BOT-001",
                "tenant": "test_tenant",
                "env": "dev",
                "domain": "default",
                "is_deleted": 0,
                "creator": "user1",
                "modifier": "user1",
                "status": "ACTIVE",
                "name": "Test Bot",
                "description": None,
                "template_uuid": None,
                "replica_desired": 1,
                "replica_minimum": 1,
                "replica_maximum": 10,
                "auto_scaling_enabled": 0,
                "sla_grade": "standard",
                "gmt_create": "2024-01-01T00:00:00",
                "gmt_modified": "2024-01-01T00:00:00",
                "config": None,
            }
        )

        mock_publish = MagicMock()
        mock_publish.id = 789

        mock_device_repo = MagicMock()
        mock_device_repo.list_by_bot_id.return_value = [MagicMock()]

        mock_publish_service = MagicMock()
        mock_publish_service.create_publish = AsyncMock(return_value=mock_publish)
        mock_publish_service.approve_stage = AsyncMock()

        service = _make_service(
            device_repo=mock_device_repo,
            publish_service=mock_publish_service,
        )
        with patch.object(
            service, "get_bot", new_callable=AsyncMock, return_value=mock_bot_response
        ):
            result = await service.scale_bot(
                tenant="test_tenant",
                bot_uuid="BOT-001",
                target_count=3,
                operator="user1",
                request_id="test-request-id-12345678901234567890",
                auto_approve_publish=False,
            )

        assert result.publish_id == 789
        assert result.target_count == 3

        call_kwargs = mock_publish_service.create_publish.call_args.kwargs
        assert call_kwargs["config"].auto_approve is False
        mock_publish_service.approve_stage.assert_not_called()

    @pytest.mark.asyncio
    async def test_scale_bot_with_bot_config_provided(self):
        """scale_bot with bot_config merges config and passes deploy_config to PublishConfig."""
        mock_bot_response = MagicMock()
        mock_bot_response.id = 1
        mock_bot_response.bot_uuid = "BOT-001"
        mock_bot_response.model_dump = MagicMock(
            return_value={
                "id": 1,
                "bot_uuid": "BOT-001",
                "tenant": "test_tenant",
                "env": "dev",
                "domain": "default",
                "is_deleted": 0,
                "creator": "user1",
                "modifier": "user1",
                "status": "ACTIVE",
                "name": "Test Bot",
                "description": None,
                "template_uuid": None,
                "replica_desired": 1,
                "replica_minimum": 1,
                "replica_maximum": 10,
                "auto_scaling_enabled": 0,
                "sla_grade": "standard",
                "gmt_create": "2024-01-01T00:00:00",
                "gmt_modified": "2024-01-01T00:00:00",
                "config": None,
            }
        )

        mock_publish = MagicMock()
        mock_publish.id = 789

        mock_device_repo = MagicMock()
        mock_device_repo.list_by_bot_id.return_value = [MagicMock()]

        mock_publish_service = MagicMock()
        mock_publish_service.create_publish = AsyncMock(return_value=mock_publish)

        mock_bot_repo = MagicMock()
        mock_record = MagicMock()
        mock_record.extra_config = {}
        mock_bot_repo.list_by_bot_uuid.return_value = [mock_record]

        service = _make_service(
            bot_repo=mock_bot_repo,
            device_repo=mock_device_repo,
            publish_service=mock_publish_service,
        )
        bot_config = BotConfig(
            sla_grade="enterprise",
            callback_timeout_seconds=600,
            auto_approve_publish=True,
            entity_id="entity-123",
            entity_type="workspace",
            share_policy={"public": True},
            deploy_config=DeployConfig(ttl_in_minutes=120),
        )

        with patch.object(
            service, "get_bot", new_callable=AsyncMock, return_value=mock_bot_response
        ):
            result = await service.scale_bot(
                tenant="test_tenant",
                bot_uuid="BOT-001",
                target_count=3,
                operator="user1",
                request_id="test-request-id-12345678901234567890",
                auto_approve_publish=True,
                bot_config=bot_config,
            )

        assert result.publish_id == 789
        assert result.target_count == 3

        call_kwargs = mock_publish_service.create_publish.call_args.kwargs
        publish_config = call_kwargs["config"]
        assert publish_config.auto_approve is True
        assert publish_config.callback_timeout_seconds == 600
        assert publish_config.deploy_config is not None
        assert publish_config.deploy_config.ttl_in_minutes == 120

    @pytest.mark.asyncio
    async def test_scale_bot_deploy_config_field_level_merge(self):
        """scale_bot with partial deploy_config override preserves non-overridden fields."""
        mock_bot_response = MagicMock()
        mock_bot_response.id = 1
        mock_bot_response.bot_uuid = "BOT-001"
        mock_bot_response.model_dump = MagicMock(
            return_value={
                "id": 1,
                "bot_uuid": "BOT-001",
                "tenant": "test_tenant",
                "env": "dev",
                "domain": "default",
                "is_deleted": 0,
                "creator": "user1",
                "modifier": "user1",
                "status": "ACTIVE",
                "name": "Test Bot",
                "description": None,
                "template_uuid": None,
                "replica_desired": 1,
                "replica_minimum": 1,
                "replica_maximum": 10,
                "auto_scaling_enabled": 0,
                "sla_grade": "standard",
                "gmt_create": "2024-01-01T00:00:00",
                "gmt_modified": "2024-01-01T00:00:00",
                "config": None,
            }
        )

        mock_publish = MagicMock()
        mock_publish.id = 789

        mock_device_repo = MagicMock()
        mock_device_repo.list_by_bot_id.return_value = [MagicMock()]

        mock_publish_service = MagicMock()
        mock_publish_service.create_publish = AsyncMock(return_value=mock_publish)

        mock_bot_repo = MagicMock()
        mock_record = MagicMock()
        mock_record.extra_config = {
            "deploy_config": {
                "envs": {"EXISTING": "val"},
                "mount_points": [],
                "ttl_in_minutes": 60,
            }
        }
        mock_bot_repo.list_by_bot_uuid.return_value = [mock_record]

        service = _make_service(
            bot_repo=mock_bot_repo,
            device_repo=mock_device_repo,
            publish_service=mock_publish_service,
        )

        bot_config = BotConfig(
            deploy_config=DeployConfig(ttl_in_minutes=120, docker_image="img:v2"),
        )

        with patch.object(
            service, "get_bot", new_callable=AsyncMock, return_value=mock_bot_response
        ):
            await service.scale_bot(
                tenant="test_tenant",
                bot_uuid="BOT-001",
                target_count=3,
                operator="user1",
                request_id="test-request-id-12345678901234567890",
                bot_config=bot_config,
            )

        call_kwargs = mock_publish_service.create_publish.call_args.kwargs
        publish_config = call_kwargs["config"]
        assert publish_config.deploy_config is not None
        assert publish_config.deploy_config.envs == {"EXISTING": "val"}
        assert publish_config.deploy_config.mount_points == []
        assert publish_config.deploy_config.ttl_in_minutes == 120
        assert publish_config.deploy_config.docker_image == "img:v2"

    @pytest.mark.asyncio
    async def test_scale_bot_without_bot_config_reads_db_config(self):
        """scale_bot without bot_config reads extra_config from DB for PublishConfig."""
        mock_bot_response = MagicMock()
        mock_bot_response.id = 1
        mock_bot_response.bot_uuid = "BOT-001"
        mock_bot_response.model_dump = MagicMock(
            return_value={
                "id": 1,
                "bot_uuid": "BOT-001",
                "tenant": "test_tenant",
                "env": "dev",
                "domain": "default",
                "is_deleted": 0,
                "creator": "user1",
                "modifier": "user1",
                "status": "ACTIVE",
                "name": "Test Bot",
                "description": None,
                "template_uuid": None,
                "replica_desired": 1,
                "replica_minimum": 1,
                "replica_maximum": 10,
                "auto_scaling_enabled": 0,
                "sla_grade": "standard",
                "gmt_create": "2024-01-01T00:00:00",
                "gmt_modified": "2024-01-01T00:00:00",
                "config": None,
            }
        )

        mock_publish = MagicMock()
        mock_publish.id = 789

        mock_device_repo = MagicMock()
        mock_device_repo.list_by_bot_id.return_value = [MagicMock()]

        mock_publish_service = MagicMock()
        mock_publish_service.create_publish = AsyncMock(return_value=mock_publish)

        mock_bot_repo = MagicMock()
        mock_record = MagicMock()
        mock_record.extra_config = {
            "callback_timeout_seconds": 300,
            "sla_grade": "enterprise",
        }
        mock_bot_repo.list_by_bot_uuid.return_value = [mock_record]

        service = _make_service(
            bot_repo=mock_bot_repo,
            device_repo=mock_device_repo,
            publish_service=mock_publish_service,
        )

        with patch.object(
            service, "get_bot", new_callable=AsyncMock, return_value=mock_bot_response
        ):
            result = await service.scale_bot(
                tenant="test_tenant",
                bot_uuid="BOT-001",
                target_count=3,
                operator="user1",
                request_id="test-request-id-12345678901234567890",
            )

        assert result.publish_id == 789

        call_kwargs = mock_publish_service.create_publish.call_args.kwargs
        publish_config = call_kwargs["config"]
        assert (
            publish_config.callback_timeout_seconds == DEFAULT_CALLBACK_TIMEOUT_SECONDS
        )
        assert publish_config.auto_approve is False

    @pytest.mark.asyncio
    async def test_scale_bot_with_partial_bot_config_merges(self):
        """scale_bot with partial bot_config merges: provided field overrides, missing keeps DB."""
        mock_bot_response = MagicMock()
        mock_bot_response.id = 1
        mock_bot_response.bot_uuid = "BOT-001"
        mock_bot_response.model_dump = MagicMock(
            return_value={
                "id": 1,
                "bot_uuid": "BOT-001",
                "tenant": "test_tenant",
                "env": "dev",
                "domain": "default",
                "is_deleted": 0,
                "creator": "user1",
                "modifier": "user1",
                "status": "ACTIVE",
                "name": "Test Bot",
                "description": None,
                "template_uuid": None,
                "replica_desired": 1,
                "replica_minimum": 1,
                "replica_maximum": 10,
                "auto_scaling_enabled": 0,
                "sla_grade": "standard",
                "gmt_create": "2024-01-01T00:00:00",
                "gmt_modified": "2024-01-01T00:00:00",
                "config": None,
            }
        )

        mock_publish = MagicMock()
        mock_publish.id = 789

        mock_device_repo = MagicMock()
        mock_device_repo.list_by_bot_id.return_value = [MagicMock()]

        mock_publish_service = MagicMock()
        mock_publish_service.create_publish = AsyncMock(return_value=mock_publish)

        mock_bot_repo = MagicMock()
        mock_record = MagicMock()
        mock_record.extra_config = {
            "callback_timeout_seconds": 300,
            "sla_grade": "standard",
        }
        mock_bot_repo.list_by_bot_uuid.return_value = [mock_record]

        service = _make_service(
            bot_repo=mock_bot_repo,
            device_repo=mock_device_repo,
            publish_service=mock_publish_service,
        )

        bot_config = BotConfig(sla_grade="enterprise")

        with patch.object(
            service, "get_bot", new_callable=AsyncMock, return_value=mock_bot_response
        ):
            result = await service.scale_bot(
                tenant="test_tenant",
                bot_uuid="BOT-001",
                target_count=3,
                operator="user1",
                request_id="test-request-id-12345678901234567890",
                bot_config=bot_config,
            )

        assert result.publish_id == 789

        call_kwargs = mock_publish_service.create_publish.call_args.kwargs
        publish_config = call_kwargs["config"]
        assert (
            publish_config.callback_timeout_seconds == DEFAULT_CALLBACK_TIMEOUT_SECONDS
        )


class TestUpdateBot:
    """Tests for BotManagementService.update_bot"""

    @pytest.mark.asyncio
    async def test_update_bot_success(self):
        """Test successful metadata update (name only, no publish)"""
        from secbaas.community.api.bot_manage import BotResponse

        mock_bot = MagicMock()
        mock_bot.id = 1
        mock_bot.bot_uuid = "BOT-001"

        mock_record = MagicMock()
        mock_record.extra_config = {}

        mock_updated = MagicMock(spec=BotResponse)
        mock_updated.model_dump.return_value = {
            "id": 1,
            "bot_uuid": "BOT-001",
            "tenant": "test_tenant",
            "env": "dev",
            "domain": "default",
            "is_deleted": 0,
            "creator": "user1",
            "modifier": "user1",
            "status": "ACTIVE",
            "name": "Updated Name",
            "description": None,
            "template_uuid": None,
            "replica_desired": 1,
            "replica_minimum": 1,
            "replica_maximum": 10,
            "auto_scaling_enabled": 0,
            "sla_grade": "standard",
            "gmt_create": "2024-01-01T00:00:00",
            "gmt_modified": "2024-01-01T00:00:00",
            "config": None,
        }

        mock_bot_repo = MagicMock()
        # _get_operational_bot_record_by_uuid_for_update calls get_by_bot_uuid with status
        mock_bot_repo.get_by_bot_uuid.return_value = mock_bot
        mock_bot_repo.get_by_id.return_value = mock_record

        mock_device_repo = MagicMock()
        mock_device_repo.list_by_bot_id.return_value = [MagicMock()]

        mock_bot_service = MagicMock()
        mock_bot_service.get_bot = AsyncMock(return_value=mock_updated)

        service = _make_service(
            bot_repo=mock_bot_repo,
            device_repo=mock_device_repo,
            bot_service=mock_bot_service,
        )
        result = await service.update_bot(
            tenant="test_tenant",
            bot_uuid="BOT-001",
            operator="user1",
            bot_name="Updated Name",
        )

        assert result is not None
        mock_bot_repo.update_bot.assert_called_once()


class TestRestartBot:
    """Tests for BotManagementService.restart_bot"""

    @pytest.mark.asyncio
    async def test_restart_bot_success(self):
        """Test RESTART publish creation returns RestartBotResponse with publish_id"""
        mock_bot_response = MagicMock()
        mock_bot_response.id = 1
        mock_bot_response.bot_uuid = "BOT-001"
        mock_bot_response.model_dump = MagicMock(
            return_value={
                "id": 1,
                "bot_uuid": "BOT-001",
                "tenant": "test_tenant",
                "env": "dev",
                "domain": "default",
                "is_deleted": 0,
                "creator": "user1",
                "modifier": "user1",
                "status": "ACTIVE",
                "name": "Test Bot",
                "description": None,
                "template_uuid": None,
                "replica_desired": 1,
                "replica_minimum": 1,
                "replica_maximum": 10,
                "auto_scaling_enabled": 0,
                "sla_grade": "standard",
                "gmt_create": "2024-01-01T00:00:00",
                "gmt_modified": "2024-01-01T00:00:00",
                "config": None,
            }
        )

        mock_publish = MagicMock()
        mock_publish.id = 999

        mock_publish_service = MagicMock()
        mock_publish_service.create_publish = AsyncMock(return_value=mock_publish)

        service = _make_service(publish_service=mock_publish_service)
        with patch.object(
            service, "get_bot", new_callable=AsyncMock, return_value=mock_bot_response
        ):
            result = await service.restart_bot(
                tenant="test_tenant",
                bot_uuid="BOT-001",
                operator="user1",
                request_id="test-request-id-12345678901234567890",
                scope=RestartScope.ALL,
            )

            assert result.publish_id == 999
            assert result.bot_uuid == "BOT-001"
            call_kwargs = mock_publish_service.create_publish.call_args.kwargs
            assert call_kwargs["config"].restart_scope == "all"
            assert call_kwargs["config"].restart_scope == RestartScope.ALL
            # Verify auto_approve defaults to False
            assert call_kwargs["config"].auto_approve is False

            # Verify approve_stage was NOT called (auto_approve_publish defaults to False)
            mock_publish_service.approve_stage.assert_not_called()

    @pytest.mark.asyncio
    async def test_restart_bot_scope_unhealthy(self):
        """Test RESTART with scope='unhealthy' passes it to PublishConfig"""
        mock_bot_response = MagicMock()
        mock_bot_response.id = 1
        mock_bot_response.bot_uuid = "BOT-001"
        mock_bot_response.model_dump = MagicMock(
            return_value={
                "id": 1,
                "bot_uuid": "BOT-001",
                "tenant": "test_tenant",
                "env": "dev",
                "domain": "default",
                "is_deleted": 0,
                "creator": "user1",
                "modifier": "user1",
                "status": "ACTIVE",
                "name": "Test Bot",
                "description": None,
                "template_uuid": None,
                "replica_desired": 1,
                "replica_minimum": 1,
                "replica_maximum": 10,
                "auto_scaling_enabled": 0,
                "sla_grade": "standard",
                "gmt_create": "2024-01-01T00:00:00",
                "gmt_modified": "2024-01-01T00:00:00",
                "config": None,
            }
        )

        mock_publish = MagicMock()
        mock_publish.id = 999

        mock_publish_service = MagicMock()
        mock_publish_service.create_publish = AsyncMock(return_value=mock_publish)

        service = _make_service(publish_service=mock_publish_service)
        with patch.object(
            service, "get_bot", new_callable=AsyncMock, return_value=mock_bot_response
        ):
            result = await service.restart_bot(
                tenant="test_tenant",
                bot_uuid="BOT-001",
                operator="user1",
                request_id="test-request-id-12345678901234567890",
                scope=RestartScope.UNHEALTHY,
            )

            assert result.publish_id == 999
            call_kwargs = mock_publish_service.create_publish.call_args.kwargs
            assert call_kwargs["config"].restart_scope == "unhealthy"
            assert call_kwargs["config"].restart_scope == RestartScope.UNHEALTHY

    @pytest.mark.asyncio
    async def test_restart_bot_invalid_scope(self):
        """Test restart_bot raises ValueError for invalid scope"""
        mock_bot_response = MagicMock()
        mock_bot_response.id = 1
        mock_bot_response.bot_uuid = "BOT-001"
        mock_bot_response.model_dump = MagicMock(
            return_value={
                "id": 1,
                "bot_uuid": "BOT-001",
                "tenant": "test_tenant",
                "env": "dev",
                "domain": "default",
                "is_deleted": 0,
                "creator": "user1",
                "modifier": "user1",
                "status": "ACTIVE",
                "name": "Test Bot",
                "description": None,
                "template_uuid": None,
                "replica_desired": 1,
                "replica_minimum": 1,
                "replica_maximum": 10,
                "auto_scaling_enabled": 0,
                "sla_grade": "standard",
                "gmt_create": "2024-01-01T00:00:00",
                "gmt_modified": "2024-01-01T00:00:00",
                "config": None,
            }
        )

        service = _make_service()
        with patch.object(
            service, "get_bot", new_callable=AsyncMock, return_value=mock_bot_response
        ):
            with pytest.raises(ValueError, match="Invalid scope|Input should be"):
                await service.restart_bot(
                    tenant="test_tenant",
                    bot_uuid="BOT-001",
                    operator="user1",
                    request_id="test-request-id-12345678901234567890",
                    scope="invalid",  # type: ignore[arg-type]
                )

    @pytest.mark.asyncio
    async def test_restart_bot_with_auto_approve(self):
        """restart_bot with auto_approve_publish=True calls approve_stage."""
        mock_bot_response = MagicMock()
        mock_bot_response.id = 1
        mock_bot_response.bot_uuid = "BOT-001"
        mock_bot_response.model_dump = MagicMock(
            return_value={
                "id": 1,
                "bot_uuid": "BOT-001",
                "tenant": "test_tenant",
                "env": "dev",
                "domain": "default",
                "is_deleted": 0,
                "creator": "user1",
                "modifier": "user1",
                "status": "ACTIVE",
                "name": "Test Bot",
                "description": None,
                "template_uuid": None,
                "replica_desired": 1,
                "replica_minimum": 1,
                "replica_maximum": 10,
                "auto_scaling_enabled": 0,
                "sla_grade": "standard",
                "gmt_create": "2024-01-01T00:00:00",
                "gmt_modified": "2024-01-01T00:00:00",
                "config": None,
            }
        )

        mock_publish = MagicMock()
        mock_publish.id = 789

        mock_publish_service = MagicMock()
        mock_publish_service.create_publish = AsyncMock(return_value=mock_publish)
        # Simulate PENDING -> SUCCESS for the auto-approve loop
        mock_publish_pending = MagicMock()
        mock_publish_pending.id = 789
        mock_publish_pending.status = PublishStatus.PENDING.value
        mock_publish_success = MagicMock()
        mock_publish_success.id = 789
        mock_publish_success.status = PublishStatus.SUCCESS.value
        mock_publish_service.get_publish = AsyncMock(
            side_effect=[mock_publish_pending, mock_publish_success]
        )
        mock_publish_service.approve_stage = AsyncMock()

        service = _make_service(publish_service=mock_publish_service)
        with patch.object(
            service, "get_bot", new_callable=AsyncMock, return_value=mock_bot_response
        ):
            result = await service.restart_bot(
                tenant="test_tenant",
                bot_uuid="BOT-001",
                operator="user1",
                request_id="test-request-id-12345678901234567890",
                scope=RestartScope.ALL,
                auto_approve_publish=True,
            )

        assert result.publish_id == 789

        # Verify PublishConfig has auto_approve=True
        call_kwargs = mock_publish_service.create_publish.call_args.kwargs
        assert call_kwargs["config"].auto_approve is True

        # background task spawned by _auto_approve_publish needs an event loop tick
        await asyncio.sleep(0)
        # Verify approve_stage was called by the auto-approve loop
        mock_publish_service.approve_stage.assert_called_once()
        approve_kwargs = mock_publish_service.approve_stage.call_args.kwargs
        assert approve_kwargs["tenant"] == "test_tenant"
        assert approve_kwargs["publish_id"] == 789
        assert approve_kwargs["operator"] == "user1"

    @pytest.mark.asyncio
    async def test_restart_bot_without_auto_approve(self):
        """restart_bot with default auto_approve_publish does NOT call approve_stage."""
        mock_bot_response = MagicMock()
        mock_bot_response.id = 1
        mock_bot_response.bot_uuid = "BOT-001"
        mock_bot_response.model_dump = MagicMock(
            return_value={
                "id": 1,
                "bot_uuid": "BOT-001",
                "tenant": "test_tenant",
                "env": "dev",
                "domain": "default",
                "is_deleted": 0,
                "creator": "user1",
                "modifier": "user1",
                "status": "ACTIVE",
                "name": "Test Bot",
                "description": None,
                "template_uuid": None,
                "replica_desired": 1,
                "replica_minimum": 1,
                "replica_maximum": 10,
                "auto_scaling_enabled": 0,
                "sla_grade": "standard",
                "gmt_create": "2024-01-01T00:00:00",
                "gmt_modified": "2024-01-01T00:00:00",
                "config": None,
            }
        )

        mock_publish = MagicMock()
        mock_publish.id = 789

        mock_publish_service = MagicMock()
        mock_publish_service.create_publish = AsyncMock(return_value=mock_publish)
        mock_publish_service.approve_stage = AsyncMock()

        service = _make_service(publish_service=mock_publish_service)
        with patch.object(
            service, "get_bot", new_callable=AsyncMock, return_value=mock_bot_response
        ):
            result = await service.restart_bot(
                tenant="test_tenant",
                bot_uuid="BOT-001",
                operator="user1",
                request_id="test-request-id-12345678901234567890",
                scope=RestartScope.ALL,
            )

        assert result.publish_id == 789

        # Verify PublishConfig has auto_approve=False (default)
        call_kwargs = mock_publish_service.create_publish.call_args.kwargs
        assert call_kwargs["config"].auto_approve is False

        # Verify approve_stage was NOT called (no auto-approve)
        mock_publish_service.approve_stage.assert_not_called()

    @pytest.mark.asyncio
    async def test_restart_bot_with_auto_approve_false_explicit(self):
        """restart_bot with auto_approve_publish=False explicitly does NOT call approve_stage."""
        mock_bot_response = MagicMock()
        mock_bot_response.id = 1
        mock_bot_response.bot_uuid = "BOT-001"
        mock_bot_response.model_dump = MagicMock(
            return_value={
                "id": 1,
                "bot_uuid": "BOT-001",
                "tenant": "test_tenant",
                "env": "dev",
                "domain": "default",
                "is_deleted": 0,
                "creator": "user1",
                "modifier": "user1",
                "status": "ACTIVE",
                "name": "Test Bot",
                "description": None,
                "template_uuid": None,
                "replica_desired": 1,
                "replica_minimum": 1,
                "replica_maximum": 10,
                "auto_scaling_enabled": 0,
                "sla_grade": "standard",
                "gmt_create": "2024-01-01T00:00:00",
                "gmt_modified": "2024-01-01T00:00:00",
                "config": None,
            }
        )

        mock_publish = MagicMock()
        mock_publish.id = 789

        mock_publish_service = MagicMock()
        mock_publish_service.create_publish = AsyncMock(return_value=mock_publish)
        mock_publish_service.approve_stage = AsyncMock()

        service = _make_service(publish_service=mock_publish_service)
        with patch.object(
            service, "get_bot", new_callable=AsyncMock, return_value=mock_bot_response
        ):
            result = await service.restart_bot(
                tenant="test_tenant",
                bot_uuid="BOT-001",
                operator="user1",
                request_id="test-request-id-12345678901234567890",
                scope=RestartScope.ALL,
                auto_approve_publish=False,
            )

        assert result.publish_id == 789

        # Verify PublishConfig has auto_approve=False (explicit)
        call_kwargs = mock_publish_service.create_publish.call_args.kwargs
        assert call_kwargs["config"].auto_approve is False

        # Verify approve_stage was NOT called
        mock_publish_service.approve_stage.assert_not_called()


class TestDestroyBotDestroyingStatus:
    """Tests for DESTROYING status behavior in destroy_bot"""

    @pytest.mark.asyncio
    async def test_destroy_bot_sets_destroying_status(self):
        """Test destroy_bot sets bot status to DESTROYING after publish creation"""
        mock_bot_response = MagicMock()
        mock_bot_response.id = 1
        mock_bot_response.bot_uuid = "BOT-001"
        mock_bot_response.status = BotStatus.ACTIVE.value
        mock_bot_response.model_dump = MagicMock(
            return_value={
                "id": 1,
                "bot_uuid": "BOT-001",
                "tenant": "test_tenant",
                "env": "dev",
                "domain": "default",
                "is_deleted": 0,
                "creator": "user1",
                "modifier": "user1",
                "status": BotStatus.DESTROYING.value,
                "name": "Test Bot",
                "description": None,
                "template_uuid": None,
                "replica_desired": 1,
                "replica_minimum": 1,
                "replica_maximum": 10,
                "auto_scaling_enabled": 0,
                "sla_grade": "standard",
                "gmt_create": "2024-01-01T00:00:00",
                "gmt_modified": "2024-01-01T00:00:00",
                "config": None,
            }
        )

        mock_publish = MagicMock()
        mock_publish.id = 456

        mock_bot_repo = MagicMock()
        mock_bot_service = MagicMock()
        mock_bot_service.get_bot = AsyncMock(return_value=mock_bot_response)
        mock_publish_service = MagicMock()
        mock_publish_service.create_publish = AsyncMock(return_value=mock_publish)
        service = _make_service(
            bot_repo=mock_bot_repo,
            bot_service=mock_bot_service,
            publish_service=mock_publish_service,
        )

        with patch.object(
            service,
            "get_bot",
            new_callable=AsyncMock,
            return_value=mock_bot_response,
        ):
            result = await service.destroy_bot(
                tenant="test_tenant",
                bot_uuid="BOT-001",
                operator="user1",
                request_id="test-request-id-12345678901234567890",
            )

            assert result is not None
            assert result.publish_id == 456
            # Verify update_status was called with DESTROYING
            mock_bot_repo.update_status.assert_called_once()
            call_args = mock_bot_repo.update_status.call_args
            assert call_args.kwargs["status"] == BotStatus.DESTROYING.value

    @pytest.mark.asyncio
    async def test_destroy_bot_rejects_already_destroying_bot(self):
        """Test destroy_bot raises ValueError when bot is already DESTROYING"""
        mock_bot_response = MagicMock()
        mock_bot_response.id = 1
        mock_bot_response.bot_uuid = "BOT-001"
        mock_bot_response.status = BotStatus.DESTROYING.value

        service = _make_service()

        with patch.object(
            service,
            "get_bot",
            new_callable=AsyncMock,
            return_value=mock_bot_response,
        ):
            with pytest.raises(ValueError, match="already being destroyed"):
                await service.destroy_bot(
                    tenant="test_tenant",
                    bot_uuid="BOT-001",
                    operator="user1",
                    request_id="test-request-id-12345678901234567890",
                )


class TestScaleBotDestroyingStatus:
    """Tests for DESTROYING status rejection in scale_bot"""

    @pytest.mark.asyncio
    async def test_scale_bot_rejects_destroying_bot(self):
        """Test scale_bot raises ValueError when bot is DESTROYING"""
        mock_bot_response = MagicMock()
        mock_bot_response.id = 1
        mock_bot_response.bot_uuid = "BOT-001"
        mock_bot_response.status = BotStatus.DESTROYING.value
        mock_bot_response.model_dump = MagicMock(
            return_value={
                "id": 1,
                "bot_uuid": "BOT-001",
                "tenant": "test_tenant",
                "env": "dev",
                "domain": "default",
                "is_deleted": 0,
                "creator": "user1",
                "modifier": "user1",
                "status": BotStatus.DESTROYING.value,
                "name": "Test Bot",
                "description": None,
                "template_uuid": None,
                "replica_desired": 1,
                "replica_minimum": 1,
                "replica_maximum": 10,
                "auto_scaling_enabled": 0,
                "sla_grade": "standard",
                "gmt_create": "2024-01-01T00:00:00",
                "gmt_modified": "2024-01-01T00:00:00",
                "config": None,
            }
        )

        service = _make_service()

        with patch.object(
            service,
            "get_bot",
            new_callable=AsyncMock,
            return_value=mock_bot_response,
        ):
            with pytest.raises(
                ValueError, match="Cannot scale bot in DESTROYING status"
            ):
                await service.scale_bot(
                    tenant="test_tenant",
                    bot_uuid="BOT-001",
                    target_count=3,
                    operator="user1",
                    request_id="test-request-id-12345678901234567890",
                )


class TestRestartBotDestroyingStatus:
    """Tests for DESTROYING status rejection in restart_bot"""

    @pytest.mark.asyncio
    async def test_restart_bot_rejects_destroying_bot(self):
        """Test restart_bot raises ValueError when bot is DESTROYING"""
        mock_bot_response = MagicMock()
        mock_bot_response.id = 1
        mock_bot_response.bot_uuid = "BOT-001"
        mock_bot_response.status = BotStatus.DESTROYING.value
        mock_bot_response.model_dump = MagicMock(
            return_value={
                "id": 1,
                "bot_uuid": "BOT-001",
                "tenant": "test_tenant",
                "env": "dev",
                "domain": "default",
                "is_deleted": 0,
                "creator": "user1",
                "modifier": "user1",
                "status": BotStatus.DESTROYING.value,
                "name": "Test Bot",
                "description": None,
                "template_uuid": None,
                "replica_desired": 1,
                "replica_minimum": 1,
                "replica_maximum": 10,
                "auto_scaling_enabled": 0,
                "sla_grade": "standard",
                "gmt_create": "2024-01-01T00:00:00",
                "gmt_modified": "2024-01-01T00:00:00",
                "config": None,
            }
        )

        service = _make_service()

        with patch.object(
            service,
            "get_bot",
            new_callable=AsyncMock,
            return_value=mock_bot_response,
        ):
            with pytest.raises(
                ValueError, match="Cannot restart bot in DESTROYING status"
            ):
                await service.restart_bot(
                    tenant="test_tenant",
                    bot_uuid="BOT-001",
                    operator="user1",
                    request_id="test-request-id-12345678901234567890",
                    scope=RestartScope.ALL,
                )


class TestBotStatusDestroyingEnum:
    """Tests for BotStatus.DESTROYING enum value"""

    def test_destroying_status_exists(self):
        """Test DESTROYING status exists in BotStatus enum"""
        assert hasattr(BotStatus, "DESTROYING")
        assert BotStatus.DESTROYING.value == "DESTROYING"

    def test_destroying_status_is_valid(self):
        """Test DESTROYING is a valid status string"""
        status = BotStatus.DESTROYING
        assert status.value == "DESTROYING"
        assert status in BotStatus


class TestResolveCallbackTimeout:
    """Tests for resolve_callback_timeout() - 3-tier priority"""

    def test_user_config_value_is_used_when_provided(self):
        """4.1 User config value is used when provided"""
        result = resolve_callback_timeout(user_value=300)
        assert result == 300

    def test_system_config_value_when_user_config_is_none(self):
        """4.2 System config value is used when user config is None"""
        mock_config = MagicMock()
        mock_config.conf_value = "600"
        mock_sys_repo = MagicMock()
        mock_sys_repo.get_by_env_and_key.return_value = mock_config

        result = resolve_callback_timeout(
            user_value=None, system_config_repo=mock_sys_repo
        )
        assert result == 600
        mock_sys_repo.get_by_env_and_key.assert_called_once()

    def test_code_constant_when_both_absent(self):
        """4.3 Code constant used when both user and system config absent"""
        mock_sys_repo = MagicMock()
        mock_sys_repo.get_by_env_and_key.return_value = None

        result = resolve_callback_timeout(
            user_value=None, system_config_repo=mock_sys_repo
        )
        assert result == DEFAULT_CALLBACK_TIMEOUT_SECONDS

    def test_invalid_system_config_value_falls_back(self):
        """4.4 Invalid system config value (non-int) falls back to code constant"""
        mock_config = MagicMock()
        mock_config.conf_value = "not-an-int"
        mock_sys_repo = MagicMock()
        mock_sys_repo.get_by_env_and_key.return_value = mock_config

        result = resolve_callback_timeout(
            user_value=None, system_config_repo=mock_sys_repo
        )
        assert result == DEFAULT_CALLBACK_TIMEOUT_SECONDS

    def test_missing_system_config_returns_none(self):
        """4.5 Missing system config returns None from get_config -> falls to constant"""
        mock_sys_repo = MagicMock()
        mock_sys_repo.get_by_env_and_key.return_value = None

        result = resolve_callback_timeout(
            user_value=None, system_config_repo=mock_sys_repo
        )
        assert result == DEFAULT_CALLBACK_TIMEOUT_SECONDS
        mock_sys_repo.get_by_env_and_key.assert_called_once()


class TestGetBotDeviceStatus:
    """Tests for BotManagementService.get_bot_device_status"""

    @pytest.mark.asyncio
    async def test_all_online(self):
        """ALL_ONLINE when all devices are ACTIVE"""
        mock_record = MagicMock()
        mock_record.id = 1
        mock_record.status = "ACTIVE"
        mock_record.bot_uuid = "BOT-001"

        mock_device_active1 = MagicMock()
        mock_device_active1.status = "ACTIVE"
        mock_device_active2 = MagicMock()
        mock_device_active2.status = "ACTIVE"
        mock_device_active3 = MagicMock()
        mock_device_active3.status = "ACTIVE"

        mock_device_repo = MagicMock()
        mock_device_repo.list_by_bot_id.return_value = [
            mock_device_active1,
            mock_device_active2,
            mock_device_active3,
        ]
        service = _make_service(device_repo=mock_device_repo)

        with patch.object(
            service,
            "_get_operational_bot_record_by_uuid_for_update",
            return_value=mock_record,
        ):
            result = await service.get_bot_device_status(
                tenant="test_tenant", bot_uuid="BOT-001"
            )

            assert result.device_status == "ALL_ONLINE"
            assert result.device_count == 3
            assert result.active_count == 3
            assert result.failed_count == 0
            assert result.bot_uuid == "BOT-001"
            assert result.bot_id == 1
            assert result.bot_status == "ACTIVE"

    @pytest.mark.asyncio
    async def test_all_offline(self):
        """ALL_OFFLINE when all devices are FAILED"""
        mock_record = MagicMock()
        mock_record.id = 1
        mock_record.status = "ACTIVE"

        mock_device1 = MagicMock()
        mock_device1.status = "FAILED"
        mock_device2 = MagicMock()
        mock_device2.status = "FAILED"

        mock_device_repo = MagicMock()
        mock_device_repo.list_by_bot_id.return_value = [
            mock_device1,
            mock_device2,
        ]
        service = _make_service(device_repo=mock_device_repo)

        with patch.object(
            service,
            "_get_operational_bot_record_by_uuid_for_update",
            return_value=mock_record,
        ):
            result = await service.get_bot_device_status(
                tenant="test_tenant", bot_uuid="BOT-001"
            )

            assert result.device_status == "ALL_OFFLINE"
            assert result.device_count == 2
            assert result.failed_count == 2

    @pytest.mark.asyncio
    async def test_all_offline_no_devices(self):
        """ALL_OFFLINE when no devices exist"""
        mock_record = MagicMock()
        mock_record.id = 1
        mock_record.status = "ACTIVE"

        mock_device_repo = MagicMock()
        mock_device_repo.list_by_bot_id.return_value = []
        service = _make_service(device_repo=mock_device_repo)

        with patch.object(
            service,
            "_get_operational_bot_record_by_uuid_for_update",
            return_value=mock_record,
        ):
            result = await service.get_bot_device_status(
                tenant="test_tenant", bot_uuid="BOT-001"
            )

            assert result.device_status == "ALL_OFFLINE"
            assert result.device_count == 0

    @pytest.mark.asyncio
    async def test_partial_online(self):
        """PARTIAL_ONLINE for mixed device statuses"""
        mock_record = MagicMock()
        mock_record.id = 1
        mock_record.status = "ACTIVE"

        mock_device_active = MagicMock()
        mock_device_active.status = "ACTIVE"
        mock_device_failed = MagicMock()
        mock_device_failed.status = "FAILED"

        mock_device_repo = MagicMock()
        mock_device_repo.list_by_bot_id.return_value = [
            mock_device_active,
            mock_device_failed,
        ]
        service = _make_service(device_repo=mock_device_repo)

        with patch.object(
            service,
            "_get_operational_bot_record_by_uuid_for_update",
            return_value=mock_record,
        ):
            result = await service.get_bot_device_status(
                tenant="test_tenant", bot_uuid="BOT-001"
            )

            assert result.device_status == "PARTIAL_ONLINE"
            assert result.device_count == 2
            assert result.active_count == 1
            assert result.failed_count == 1

    @pytest.mark.asyncio
    async def test_with_pending_and_other(self):
        """Device counts include pending and other statuses correctly"""
        mock_record = MagicMock()
        mock_record.id = 1
        mock_record.status = "ACTIVE"

        mock_device_active = MagicMock()
        mock_device_active.status = "ACTIVE"
        mock_device_pending = MagicMock()
        mock_device_pending.status = "PENDING"
        mock_device_updating = MagicMock()
        mock_device_updating.status = "UPDATING"

        mock_device_repo = MagicMock()
        mock_device_repo.list_by_bot_id.return_value = [
            mock_device_active,
            mock_device_pending,
            mock_device_updating,
        ]
        service = _make_service(device_repo=mock_device_repo)

        with patch.object(
            service,
            "_get_operational_bot_record_by_uuid_for_update",
            return_value=mock_record,
        ):
            result = await service.get_bot_device_status(
                tenant="test_tenant", bot_uuid="BOT-001"
            )

            assert result.device_status == "PARTIAL_ONLINE"
            assert result.device_count == 3
            assert result.active_count == 1
            assert result.pending_count == 1
            assert result.other_count == 1

    @pytest.mark.asyncio
    async def test_bot_not_found(self):
        """Raises BotNotFoundError for nonexistent bot_uuid"""
        service = _make_service()
        with patch.object(
            service,
            "_get_operational_bot_record_by_uuid_for_update",
            return_value=None,
        ):
            with patch.object(
                service,
                "_get_bot_record_by_uuid",
                return_value=None,
            ):
                with pytest.raises(BotNotFoundError, match="BOT-NOT-FOUND"):
                    await service.get_bot_device_status(
                        tenant="test_tenant", bot_uuid="BOT-NOT-FOUND"
                    )

    @pytest.mark.asyncio
    async def test_fallback_to_any_record(self):
        """Fallback to any bot record when operational lookup returns None"""
        mock_fallback_record = MagicMock()
        mock_fallback_record.id = 99
        mock_fallback_record.status = "RELEASED"

        mock_device = MagicMock()
        mock_device.status = "ACTIVE"

        mock_device_repo = MagicMock()
        mock_device_repo.list_by_bot_id.return_value = [mock_device]
        service = _make_service(device_repo=mock_device_repo)

        with patch.object(
            service,
            "_get_operational_bot_record_by_uuid_for_update",
            return_value=None,
        ):
            with patch.object(
                service,
                "_get_bot_record_by_uuid",
                return_value=mock_fallback_record,
            ):
                result = await service.get_bot_device_status(
                    tenant="test_tenant", bot_uuid="BOT-001"
                )

                assert result.bot_status == "RELEASED"
                assert result.device_count == 1
                assert result.active_count == 1
                assert result.device_status == "ALL_ONLINE"

    @pytest.mark.asyncio
    async def test_destroying_record_with_active_devices(self):
        """DESTROYING bot with ALL active devices returns ALL_ONLINE"""
        mock_record = MagicMock()
        mock_record.id = 5
        mock_record.status = "DESTROYING"

        mock_device1 = MagicMock()
        mock_device1.status = "ACTIVE"
        mock_device2 = MagicMock()
        mock_device2.status = "ACTIVE"

        mock_device_repo = MagicMock()
        mock_device_repo.list_by_bot_id.return_value = [
            mock_device1,
            mock_device2,
        ]
        service = _make_service(device_repo=mock_device_repo)

        with patch.object(
            service,
            "_get_operational_bot_record_by_uuid_for_update",
            return_value=mock_record,
        ):
            result = await service.get_bot_device_status(
                tenant="test_tenant", bot_uuid="BOT-001"
            )

            assert result.bot_status == "DESTROYING"
            assert result.device_status == "ALL_ONLINE"
            assert result.device_count == 2
            assert result.active_count == 2

    @pytest.mark.asyncio
    async def test_with_offline_devices(self):
        """Device counts include offline devices correctly"""
        mock_record = MagicMock()
        mock_record.id = 1
        mock_record.status = "ACTIVE"

        mock_device_active = MagicMock()
        mock_device_active.status = "ACTIVE"
        mock_device_offline = MagicMock()
        mock_device_offline.status = "OFFLINE"
        mock_device_failed = MagicMock()
        mock_device_failed.status = "FAILED"

        mock_device_repo = MagicMock()
        mock_device_repo.list_by_bot_id.return_value = [
            mock_device_active,
            mock_device_offline,
            mock_device_failed,
        ]
        service = _make_service(device_repo=mock_device_repo)

        with patch.object(
            service,
            "_get_operational_bot_record_by_uuid_for_update",
            return_value=mock_record,
        ):
            result = await service.get_bot_device_status(
                tenant="test_tenant", bot_uuid="BOT-001"
            )

            assert result.device_status == "PARTIAL_ONLINE"
            assert result.device_count == 3
            assert result.active_count == 1
            assert result.failed_count == 1
            assert result.offline_count == 1
            assert result.pending_count == 0
            assert result.other_count == 0


class TestScaleBotDown:
    """Tests for SCALE_DOWN publish in scale_bot"""

    @pytest.mark.asyncio
    async def test_scale_bot_down_success(self):
        """Test SCALE_DOWN publish created when target_count < current_count"""
        mock_bot_response = MagicMock()
        mock_bot_response.id = 1
        mock_bot_response.bot_uuid = "BOT-001"
        mock_bot_response.status = "ACTIVE"
        mock_bot_response.model_dump = MagicMock(
            return_value={
                "id": 1,
                "bot_uuid": "BOT-001",
                "tenant": "test_tenant",
                "env": "dev",
                "domain": "default",
                "is_deleted": 0,
                "creator": "user1",
                "modifier": "user1",
                "status": "ACTIVE",
                "name": "Test Bot",
                "description": None,
                "template_uuid": None,
                "replica_desired": 1,
                "replica_minimum": 1,
                "replica_maximum": 10,
                "auto_scaling_enabled": 0,
                "sla_grade": "standard",
                "gmt_create": "2024-01-01T00:00:00",
                "gmt_modified": "2024-01-01T00:00:00",
                "config": None,
            }
        )

        mock_publish = MagicMock()
        mock_publish.id = 555

        mock_device_repo = MagicMock()
        # 3 devices currently, scale down to 1
        mock_device_repo.list_by_bot_id.return_value = [
            MagicMock(),
            MagicMock(),
            MagicMock(),
        ]
        mock_publish_service = MagicMock()
        mock_publish_service.create_publish = AsyncMock(return_value=mock_publish)
        service = _make_service(
            device_repo=mock_device_repo,
            publish_service=mock_publish_service,
        )

        with patch.object(
            service,
            "get_bot",
            new_callable=AsyncMock,
            return_value=mock_bot_response,
        ):
            result = await service.scale_bot(
                tenant="test_tenant",
                bot_uuid="BOT-001",
                target_count=1,  # Scale from 3 to 1
                operator="user1",
                request_id="test-request-id-12345678901234567890",
            )

            assert result.publish_id == 555
            assert result.target_count == 1
            call_kwargs = mock_publish_service.create_publish.call_args.kwargs
            assert call_kwargs["publish_type"] == PublishType.SCALE_DOWN
            assert call_kwargs["config"].replica_desired == 1


class TestListBotsStatusFilter:
    """Tests for BotManagementService.list_bots with status filter"""

    @pytest.mark.asyncio
    async def test_list_bots_with_active_status(self):
        """Test list_bots passes ACTIVE status filter correctly"""
        mock_bot = MagicMock()
        mock_bot.bot_uuid = "BOT-001"

        mock_result = MagicMock()
        mock_result.items = [mock_bot]
        mock_result.total = 1
        mock_result.page = 1
        mock_result.page_size = 20

        mock_bot_service = MagicMock()
        mock_bot_service.list_bots = AsyncMock(return_value=mock_result)
        service = _make_service(bot_service=mock_bot_service)

        result = await service.list_bots(
            tenant="test_tenant",
            status="ACTIVE",
            page=1,
            page_size=20,
        )

        assert result.total == 1
        call_kwargs = mock_bot_service.list_bots.call_args.kwargs
        assert call_kwargs["status"] == BotStatus.ACTIVE

    @pytest.mark.asyncio
    async def test_list_bots_with_failed_status(self):
        """Test list_bots passes FAILED status filter correctly"""
        mock_result = MagicMock()
        mock_result.items = []
        mock_result.total = 0
        mock_result.page = 1
        mock_result.page_size = 20

        mock_bot_service = MagicMock()
        mock_bot_service.list_bots = AsyncMock(return_value=mock_result)
        service = _make_service(bot_service=mock_bot_service)

        await service.list_bots(
            tenant="test_tenant",
            status="FAILED",
            page=1,
            page_size=20,
        )

        call_kwargs = mock_bot_service.list_bots.call_args.kwargs
        assert call_kwargs["status"] == BotStatus.FAILED

    @pytest.mark.asyncio
    async def test_list_bots_with_destroying_status(self):
        """Test list_bots with DESTROYING status (not in enum, passes None)"""
        mock_result = MagicMock()
        mock_result.items = []
        mock_result.total = 0
        mock_result.page = 1
        mock_result.page_size = 20

        mock_bot_service = MagicMock()
        mock_bot_service.list_bots = AsyncMock(return_value=mock_result)
        service = _make_service(bot_service=mock_bot_service)

        await service.list_bots(
            tenant="test_tenant",
            status="DESTROYING",
            page=1,
            page_size=20,
        )

        call_kwargs = mock_bot_service.list_bots.call_args.kwargs
        assert call_kwargs["status"] is None

    @pytest.mark.asyncio
    async def test_list_bots_negative_page(self):
        """Test negative page is capped at 1"""
        mock_result = MagicMock()
        mock_result.items = []
        mock_result.total = 0
        mock_result.page = 1
        mock_result.page_size = 20

        mock_bot_service = MagicMock()
        mock_bot_service.list_bots = AsyncMock(return_value=mock_result)
        service = _make_service(bot_service=mock_bot_service)

        await service.list_bots(
            tenant="test_tenant",
            page=-5,
            page_size=20,
        )

        call_kwargs = mock_bot_service.list_bots.call_args.kwargs
        assert call_kwargs["page"] == 1


class TestGetBotWithDevices:
    """Tests for BotManagementService.get_bot_with_devices"""

    @pytest.mark.asyncio
    async def test_get_bot_with_devices_success(self):
        """Test get_bot_with_devices returns bot with device list"""
        mock_record = MagicMock()
        mock_record.id = 1
        mock_record.bot_uuid = "BOT-001"
        mock_record.tenant = "test_tenant"
        mock_record.env = "dev"
        mock_record.domain = "default"
        mock_record.name = "Test Bot"
        mock_record.description = None
        mock_record.template_uuid = None
        mock_record.sla_grade = "standard"
        mock_record.replica_desired = 1
        mock_record.replica_minimum = 1
        mock_record.replica_maximum = 10
        mock_record.auto_scaling_enabled = 0
        mock_record.status = BotStatus.ACTIVE.value
        mock_record.extra_config = {}
        mock_record.is_deleted = 0
        mock_record.creator = "user1"
        mock_record.modifier = "user1"
        mock_record.gmt_create = "2024-01-01T00:00:00"
        mock_record.gmt_modified = "2024-01-01T00:00:00"

        mock_device = MagicMock()
        mock_device.device_uuid = "DEV-001"
        mock_device.status = "ACTIVE"
        mock_device.provider_type = "ARCA"
        mock_device.provider_device_id = "sandbox-123"
        mock_device.gmt_create = "2024-01-01T00:00:00"

        mock_bot_repo = MagicMock()
        mock_bot_repo.get_by_id.return_value = mock_record
        mock_device_repo = MagicMock()
        mock_device_repo.list_by_bot_id.return_value = [mock_device]
        service = _make_service(
            bot_repo=mock_bot_repo,
            device_repo=mock_device_repo,
        )

        result = await service.get_bot_with_devices(
            tenant="test_tenant",
            bot_id=1,
        )

        assert result is not None
        assert result.id == 1
        assert len(result.devices) == 1
        assert result.devices[0].device_uuid == "DEV-001"
        assert result.devices[0].status == "ACTIVE"

    @pytest.mark.asyncio
    async def test_get_bot_with_devices_not_found(self):
        """Test get_bot_with_devices returns None for nonexistent bot_id"""
        mock_bot_repo = MagicMock()
        mock_bot_repo.get_by_id.return_value = None
        service = _make_service(bot_repo=mock_bot_repo)

        result = await service.get_bot_with_devices(
            tenant="test_tenant",
            bot_id=999,
        )

        assert result is None

    @pytest.mark.asyncio
    async def test_get_bot_with_devices_no_devices(self):
        """Test get_bot_with_devices with empty device list"""
        mock_record = MagicMock()
        mock_record.id = 1
        mock_record.bot_uuid = "BOT-001"
        mock_record.tenant = "test_tenant"
        mock_record.env = "dev"
        mock_record.domain = "default"
        mock_record.name = "Test Bot"
        mock_record.description = None
        mock_record.template_uuid = None
        mock_record.sla_grade = "standard"
        mock_record.replica_desired = 1
        mock_record.replica_minimum = 1
        mock_record.replica_maximum = 10
        mock_record.auto_scaling_enabled = 0
        mock_record.status = BotStatus.ACTIVE.value
        mock_record.extra_config = {}
        mock_record.is_deleted = 0
        mock_record.creator = "user1"
        mock_record.modifier = "user1"
        mock_record.gmt_create = "2024-01-01T00:00:00"
        mock_record.gmt_modified = "2024-01-01T00:00:00"

        mock_bot_repo = MagicMock()
        mock_bot_repo.get_by_id.return_value = mock_record
        mock_device_repo = MagicMock()
        mock_device_repo.list_by_bot_id.return_value = []
        service = _make_service(
            bot_repo=mock_bot_repo,
            device_repo=mock_device_repo,
        )

        result = await service.get_bot_with_devices(
            tenant="test_tenant",
            bot_id=1,
        )

        assert result is not None
        assert result.devices == []


class TestListBotsWithDevices:
    """Tests for BotManagementService.list_bots_with_devices"""

    @pytest.mark.asyncio
    async def test_list_bots_with_devices_success(self):
        """Test list_bots_with_devices returns bots with devices"""
        mock_bot1 = MagicMock()
        mock_bot1.id = 1
        mock_bot1.bot_uuid = "BOT-001"
        mock_bot2 = MagicMock()
        mock_bot2.id = 2
        mock_bot2.bot_uuid = "BOT-002"

        mock_result = MagicMock()
        mock_result.items = [mock_bot1, mock_bot2]
        mock_result.total = 2
        mock_result.page = 1
        mock_result.page_size = 20

        mock_device1 = MagicMock()
        mock_device1.device_uuid = "DEV-001"
        mock_device1.status = "ACTIVE"
        mock_device1.provider_type = "ARCA"
        mock_device1.provider_device_id = "sandbox-1"
        mock_device1.gmt_create = "2024-01-01T00:00:00"

        mock_device2 = MagicMock()
        mock_device2.device_uuid = "DEV-002"
        mock_device2.status = "ACTIVE"
        mock_device2.provider_type = "ARCA"
        mock_device2.provider_device_id = "sandbox-2"
        mock_device2.gmt_create = "2024-01-01T00:00:00"

        mock_bot_service = MagicMock()
        mock_bot_service.list_bots = AsyncMock(return_value=mock_result)
        mock_device_repo = MagicMock()
        mock_device_repo.list_devices_by_bot_ids.return_value = {
            1: [mock_device1],
            2: [mock_device2],
        }
        service = _make_service(
            bot_service=mock_bot_service,
            device_repo=mock_device_repo,
        )

        result = await service.list_bots_with_devices(
            tenant="test_tenant",
        )

        assert result.total == 2
        assert len(result.items) == 2
        assert len(result.items[0].devices) == 1
        assert result.items[0].devices[0].device_uuid == "DEV-001"
        assert len(result.items[1].devices) == 1
        assert result.items[1].devices[0].device_uuid == "DEV-002"

    @pytest.mark.asyncio
    async def test_list_bots_with_devices_pagination_limit(self):
        """Test page_size > 100 gets capped in list_bots_with_devices"""
        mock_result = MagicMock()
        mock_result.items = []
        mock_result.total = 0
        mock_result.page = 1
        mock_result.page_size = 100

        mock_bot_service = MagicMock()
        mock_bot_service.list_bots = AsyncMock(return_value=mock_result)
        mock_device_repo = MagicMock()
        mock_device_repo.list_devices_by_bot_ids.return_value = {}
        service = _make_service(
            bot_service=mock_bot_service,
            device_repo=mock_device_repo,
        )

        await service.list_bots_with_devices(
            tenant="test_tenant",
            page_size=200,
        )

        call_kwargs = mock_bot_service.list_bots.call_args.kwargs
        assert call_kwargs["page_size"] == 100

    @pytest.mark.asyncio
    async def test_list_bots_with_devices_status_filter(self):
        """Test list_bots_with_devices with status filter"""
        mock_result = MagicMock()
        mock_result.items = []
        mock_result.total = 0
        mock_result.page = 1
        mock_result.page_size = 20

        mock_bot_service = MagicMock()
        mock_bot_service.list_bots = AsyncMock(return_value=mock_result)
        mock_device_repo = MagicMock()
        mock_device_repo.list_devices_by_bot_ids.return_value = {}
        service = _make_service(
            bot_service=mock_bot_service,
            device_repo=mock_device_repo,
        )

        await service.list_bots_with_devices(
            tenant="test_tenant",
            status="ACTIVE",
        )

        call_kwargs = mock_bot_service.list_bots.call_args.kwargs
        assert call_kwargs["status"] == BotStatus.ACTIVE

    @pytest.mark.asyncio
    async def test_list_bots_with_devices_bot_without_devices(self):
        """Test bot without any devices gets empty devices list"""
        mock_bot = MagicMock()
        mock_bot.id = 1
        mock_bot.bot_uuid = "BOT-001"

        mock_result = MagicMock()
        mock_result.items = [mock_bot]
        mock_result.total = 1
        mock_result.page = 1
        mock_result.page_size = 20

        mock_bot_service = MagicMock()
        mock_bot_service.list_bots = AsyncMock(return_value=mock_result)
        mock_device_repo = MagicMock()
        # bot 1 has no devices (not in dict)
        mock_device_repo.list_devices_by_bot_ids.return_value = {}
        service = _make_service(
            bot_service=mock_bot_service,
            device_repo=mock_device_repo,
        )

        result = await service.list_bots_with_devices(
            tenant="test_tenant",
        )

        assert result.total == 1
        assert result.items[0].devices == []


class TestListBotsWithDevicesByUuid:
    """Tests for BotManagementService.list_bots_with_devices_by_uuid"""

    @pytest.mark.asyncio
    async def test_single_record(self):
        """Test single bot record with devices"""
        mock_record = MagicMock()
        mock_record.id = 1
        mock_record.bot_uuid = "BOT-001"
        mock_record.tenant = "test_tenant"
        mock_record.env = "dev"
        mock_record.domain = "default"
        mock_record.name = "Test Bot"
        mock_record.description = None
        mock_record.template_uuid = None
        mock_record.sla_grade = "standard"
        mock_record.replica_desired = 1
        mock_record.replica_minimum = 1
        mock_record.replica_maximum = 10
        mock_record.auto_scaling_enabled = 0
        mock_record.status = BotStatus.ACTIVE.value
        mock_record.extra_config = {}
        mock_record.is_deleted = 0
        mock_record.creator = "user1"
        mock_record.modifier = "user1"
        mock_record.gmt_create = "2024-01-01T00:00:00"
        mock_record.gmt_modified = "2024-01-01T00:00:00"

        mock_device = MagicMock()
        mock_device.device_uuid = "DEV-001"
        mock_device.status = "ACTIVE"
        mock_device.provider_type = "ARCA"
        mock_device.provider_device_id = "sandbox-1"
        mock_device.gmt_create = "2024-01-01T00:00:00"

        mock_bot_repo = MagicMock()
        mock_bot_repo.list_by_bot_uuid.return_value = [mock_record]
        mock_device_repo = MagicMock()
        mock_device_repo.list_by_bot_id.return_value = [mock_device]
        service = _make_service(
            bot_repo=mock_bot_repo,
            device_repo=mock_device_repo,
        )

        results = await service.list_bots_with_devices_by_uuid(
            tenant="test_tenant",
            bot_uuid="BOT-001",
        )

        assert len(results) == 1
        assert results[0].id == 1
        assert len(results[0].devices) == 1
        assert results[0].devices[0].device_uuid == "DEV-001"

    @pytest.mark.asyncio
    async def test_not_found(self):
        """Test raises BotNotFoundError when no records match"""
        mock_bot_repo = MagicMock()
        mock_bot_repo.list_by_bot_uuid.return_value = []
        service = _make_service(bot_repo=mock_bot_repo)

        with pytest.raises(BotNotFoundError, match="BOT-NOT-FOUND"):
            await service.list_bots_with_devices_by_uuid(
                tenant="test_tenant",
                bot_uuid="BOT-NOT-FOUND",
            )

    @pytest.mark.asyncio
    async def test_multiple_records(self):
        """Test multiple records for same UUID (different statuses)"""
        mock_record1 = MagicMock()
        mock_record1.id = 1
        mock_record1.bot_uuid = "BOT-001"
        mock_record1.tenant = "test_tenant"
        mock_record1.env = "dev"
        mock_record1.domain = "default"
        mock_record1.name = "Test Bot"
        mock_record1.description = None
        mock_record1.template_uuid = None
        mock_record1.sla_grade = "standard"
        mock_record1.replica_desired = 1
        mock_record1.replica_minimum = 1
        mock_record1.replica_maximum = 10
        mock_record1.auto_scaling_enabled = 0
        mock_record1.status = BotStatus.ACTIVE.value
        mock_record1.extra_config = {}
        mock_record1.is_deleted = 0
        mock_record1.creator = "user1"
        mock_record1.modifier = "user1"
        mock_record1.gmt_create = "2024-01-01T00:00:00"
        mock_record1.gmt_modified = "2024-01-01T00:00:00"

        mock_record2 = MagicMock()
        mock_record2.id = 2
        mock_record2.bot_uuid = "BOT-001"
        mock_record2.tenant = "test_tenant"
        mock_record2.env = "dev"
        mock_record2.domain = "default"
        mock_record2.name = "Test Bot"
        mock_record2.description = None
        mock_record2.template_uuid = None
        mock_record2.sla_grade = "standard"
        mock_record2.replica_desired = 1
        mock_record2.replica_minimum = 1
        mock_record2.replica_maximum = 10
        mock_record2.auto_scaling_enabled = 0
        mock_record2.status = BotStatus.FAILED.value
        mock_record2.extra_config = {}
        mock_record2.is_deleted = 0
        mock_record2.creator = "user1"
        mock_record2.modifier = "user1"
        mock_record2.gmt_create = "2024-01-01T00:00:00"
        mock_record2.gmt_modified = "2024-01-01T00:00:00"

        mock_bot_repo = MagicMock()
        mock_bot_repo.list_by_bot_uuid.return_value = [mock_record1, mock_record2]
        mock_device_repo = MagicMock()
        mock_device_repo.list_by_bot_id.return_value = []
        mock_bot_service = MagicMock()
        # _calculate_bot_status returns a BotStatus enum value
        mock_status_active = MagicMock()
        mock_status_active.value = "ACTIVE"
        mock_status_failed = MagicMock()
        mock_status_failed.value = "FAILED"
        mock_bot_service._calculate_bot_status.side_effect = [
            mock_status_active,
            mock_status_failed,
        ]
        service = _make_service(
            bot_repo=mock_bot_repo,
            device_repo=mock_device_repo,
            bot_service=mock_bot_service,
        )

        results = await service.list_bots_with_devices_by_uuid(
            tenant="test_tenant",
            bot_uuid="BOT-001",
        )

        assert len(results) == 2
        assert results[0].status == "ACTIVE"
        assert results[1].status == "FAILED"


class TestListDevicesByBotUuid:
    """Tests for BotManagementService.list_devices_by_bot_uuid"""

    @pytest.mark.asyncio
    async def test_single_record(self):
        """Test return device list response for a single bot record"""
        mock_record = MagicMock()
        mock_record.id = 1
        mock_record.status = "ACTIVE"

        mock_device = MagicMock()
        mock_device.device_uuid = "DEV-001"
        mock_device.status = "ACTIVE"
        mock_device.provider_type = "ARCA"
        mock_device.provider_device_id = "sandbox-1"
        mock_device.gmt_create = "2024-01-01T00:00:00"
        mock_device.id = 1
        mock_device.bot_id = 1
        mock_device.name = "device-1"
        mock_device.description = ""
        mock_device.env = "dev"
        mock_device.domain = "default"
        mock_device.is_deleted = 0
        mock_device.tenant = "test_tenant"
        mock_device.creator = "user1"
        mock_device.modifier = "user1"
        mock_device.bot_uuid = "BOT-001"
        mock_device.gmt_modified = "2024-01-01T00:00:00"
        mock_device.provider_props = {}
        mock_device.device_props = {}
        mock_device.extra_config = None
        mock_device.provider_device_props = {}
        mock_device.err_msg = None

        mock_bot_repo = MagicMock()
        mock_bot_repo.list_by_bot_uuid.return_value = [mock_record]
        mock_device_repo = MagicMock()
        mock_device_repo.list_by_bot_id.return_value = [mock_device]
        service = _make_service(
            bot_repo=mock_bot_repo,
            device_repo=mock_device_repo,
        )

        results = await service.list_devices_by_bot_uuid(
            tenant="test_tenant",
            bot_uuid="BOT-001",
        )

        assert len(results) == 1
        assert results[0].total == 1
        assert len(results[0].items) == 1

    @pytest.mark.asyncio
    async def test_not_found(self):
        """Test raises BotNotFoundError"""
        mock_bot_repo = MagicMock()
        mock_bot_repo.list_by_bot_uuid.return_value = []
        service = _make_service(bot_repo=mock_bot_repo)

        with pytest.raises(BotNotFoundError, match="BOT-NOT-FOUND"):
            await service.list_devices_by_bot_uuid(
                tenant="test_tenant",
                bot_uuid="BOT-NOT-FOUND",
            )

    @pytest.mark.asyncio
    async def test_multiple_records(self):
        """Test returns separate DeviceListResponse per record"""
        mock_record1 = MagicMock()
        mock_record1.id = 1
        mock_record1.status = "ACTIVE"
        mock_record2 = MagicMock()
        mock_record2.id = 2
        mock_record2.status = "DESTROYING"

        mock_device1 = MagicMock()
        mock_device1.device_uuid = "DEV-001"
        mock_device1.status = "ACTIVE"
        mock_device1.provider_type = "ARCA"
        mock_device1.provider_device_id = "sandbox-1"
        mock_device1.gmt_create = "2024-01-01T00:00:00"
        mock_device1.id = 1
        mock_device1.bot_id = 1
        mock_device1.name = "device-1"
        mock_device1.description = ""
        mock_device1.env = "dev"
        mock_device1.domain = "default"
        mock_device1.is_deleted = 0
        mock_device1.tenant = "test_tenant"
        mock_device1.creator = "user1"
        mock_device1.modifier = "user1"
        mock_device1.bot_uuid = "BOT-001"
        mock_device1.gmt_modified = "2024-01-01T00:00:00"
        mock_device1.provider_props = {}
        mock_device1.device_props = {}
        mock_device1.extra_config = None
        mock_device1.provider_device_props = {}
        mock_device1.err_msg = None

        mock_bot_repo = MagicMock()
        mock_bot_repo.list_by_bot_uuid.return_value = [mock_record1, mock_record2]
        mock_device_repo = MagicMock()
        mock_device_repo.list_by_bot_id.side_effect = [
            [mock_device1],
            [],
        ]
        service = _make_service(
            bot_repo=mock_bot_repo,
            device_repo=mock_device_repo,
        )

        results = await service.list_devices_by_bot_uuid(
            tenant="test_tenant",
            bot_uuid="BOT-001",
        )

        assert len(results) == 2
        assert results[0].total == 1
        assert results[1].total == 0


class TestListDevicesByBotId:
    """Tests for BotManagementService.list_devices_by_bot_id"""

    @pytest.mark.asyncio
    async def test_success(self):
        """Test list_devices_by_bot_id returns paginated device list"""
        mock_device = MagicMock()
        mock_device.device_uuid = "DEV-001"
        mock_device.status = "ACTIVE"
        mock_device.provider_type = "ARCA"
        mock_device.provider_device_id = "sandbox-1"
        mock_device.gmt_create = "2024-01-01T00:00:00"
        mock_device.id = 1
        mock_device.bot_id = 1
        mock_device.name = "device-1"
        mock_device.description = ""
        mock_device.env = "dev"
        mock_device.domain = "default"
        mock_device.is_deleted = 0
        mock_device.tenant = "test_tenant"
        mock_device.creator = "user1"
        mock_device.modifier = "user1"
        mock_device.bot_uuid = "BOT-001"
        mock_device.gmt_modified = "2024-01-01T00:00:00"
        mock_device.provider_props = {}
        mock_device.device_props = {}
        mock_device.extra_config = None
        mock_device.provider_device_props = {}
        mock_device.err_msg = None

        mock_device_repo = MagicMock()
        mock_device_repo.list_by_bot_id.return_value = [mock_device]
        service = _make_service(device_repo=mock_device_repo)

        result = await service.list_devices_by_bot_id(
            tenant="test_tenant",
            bot_id=1,
            page=1,
            page_size=10,
        )

        assert result.total == 1
        assert result.page == 1
        assert result.page_size == 10
        assert len(result.items) == 1

    @pytest.mark.asyncio
    async def test_empty(self):
        """Test returns empty list when no devices"""
        mock_device_repo = MagicMock()
        mock_device_repo.list_by_bot_id.return_value = []
        service = _make_service(device_repo=mock_device_repo)

        result = await service.list_devices_by_bot_id(
            tenant="test_tenant",
            bot_id=1,
        )

        assert result.total == 0
        assert result.items == []

    @pytest.mark.asyncio
    async def test_pagination(self):
        """Test pagination works correctly"""
        mock_devices = []
        for i in range(25):
            d = MagicMock()
            d.device_uuid = f"DEV-{i:03d}"
            d.status = "ACTIVE"
            d.provider_type = "ARCA"
            d.provider_device_id = f"sandbox-{i}"
            d.gmt_create = "2024-01-01T00:00:00"
            d.id = i + 1
            d.bot_id = 1
            d.name = f"device-{i}"
            d.description = ""
            d.env = "dev"
            d.domain = "default"
            d.is_deleted = 0
            d.tenant = "test_tenant"
            d.creator = "user1"
            d.modifier = "user1"
            d.bot_uuid = "BOT-001"
            d.gmt_modified = "2024-01-01T00:00:00"
            d.provider_props = {}
            d.device_props = {}
            d.extra_config = None
            d.provider_device_props = {}
            d.err_msg = None
            mock_devices.append(d)

        mock_device_repo = MagicMock()
        mock_device_repo.list_by_bot_id.return_value = mock_devices
        service = _make_service(device_repo=mock_device_repo)

        # Page 1: items 0-9
        result1 = await service.list_devices_by_bot_id(
            tenant="test_tenant",
            bot_id=1,
            page=1,
            page_size=10,
        )
        assert result1.total == 25
        assert len(result1.items) == 10
        assert result1.page == 1

        # Page 3: items 20-24
        result3 = await service.list_devices_by_bot_id(
            tenant="test_tenant",
            bot_id=1,
            page=3,
            page_size=10,
        )
        assert len(result3.items) == 5
        assert result3.page == 3


class TestUpdateBotConfigUpdate:
    """Tests for BotManagementService.update_bot with config changes"""

    @pytest.mark.asyncio
    async def test_update_bot_with_config_triggers_publish(self):
        """Test update_bot with config creates UPDATE publish"""
        mock_record = MagicMock()
        mock_record.id = 1
        mock_record.bot_uuid = "BOT-001"
        mock_record.status = "ACTIVE"
        mock_record.extra_config = {}
        mock_record.name = "Test Bot"

        mock_publish = MagicMock()
        mock_publish.id = 777

        mock_updated_bot = MagicMock()
        mock_updated_bot.model_dump.return_value = {
            "id": 1,
            "bot_uuid": "BOT-001",
            "tenant": "test_tenant",
            "env": "dev",
            "domain": "default",
            "is_deleted": 0,
            "creator": "user1",
            "modifier": "user1",
            "status": "ACTIVE",
            "name": "Updated Bot",
            "description": None,
            "template_uuid": None,
            "replica_desired": 1,
            "replica_minimum": 1,
            "replica_maximum": 10,
            "auto_scaling_enabled": 0,
            "sla_grade": "standard",
            "gmt_create": "2024-01-01T00:00:00",
            "gmt_modified": "2024-01-01T00:00:00",
            "config": None,
        }

        mock_bot_repo = MagicMock()
        mock_bot_repo.get_by_bot_uuid.return_value = mock_record
        mock_device_repo = MagicMock()
        mock_device_repo.list_by_bot_id.return_value = [MagicMock()]
        mock_publish_service = MagicMock()
        mock_publish_service.create_publish = AsyncMock(return_value=mock_publish)
        mock_bot_service = MagicMock()
        mock_bot_service.get_bot = AsyncMock(return_value=mock_updated_bot)
        service = _make_service(
            bot_repo=mock_bot_repo,
            device_repo=mock_device_repo,
            publish_service=mock_publish_service,
            bot_service=mock_bot_service,
        )

        with patch.object(
            service,
            "_get_operational_bot_record_by_uuid_for_update",
            return_value=mock_record,
        ):
            bot_config = MagicMock(spec=BotConfig)
            bot_config.share_policy = {"key": "value"}
            bot_config.deploy_config = None
            bot_config.entity_id = ""
            bot_config.entity_type = ""
            bot_config.sla_grade = ""
            bot_config.callback_timeout_seconds = None
            bot_config.auto_approve_publish = False

            result = await service.update_bot(
                tenant="test_tenant",
                bot_uuid="BOT-001",
                operator="user1",
                bot_config=bot_config,
                request_id="test-request-id-12345678901234567890",
            )

            assert result is not None
            assert result.publish_id == 777
            call_kwargs = mock_publish_service.create_publish.call_args.kwargs
            assert call_kwargs["publish_type"] == PublishType.UPDATE

    @pytest.mark.asyncio
    async def test_update_bot_config_without_request_id(self):
        """Test update_bot raises ValueError when config without request_id"""
        mock_record = MagicMock()
        mock_record.id = 1
        mock_record.bot_uuid = "BOT-001"
        mock_record.status = "ACTIVE"
        mock_record.extra_config = {}
        mock_record.name = "Test Bot"

        mock_bot_repo = MagicMock()
        mock_device_repo = MagicMock()
        mock_device_repo.list_by_bot_id.return_value = []
        service = _make_service(
            bot_repo=mock_bot_repo,
            device_repo=mock_device_repo,
        )

        with patch.object(
            service,
            "_get_operational_bot_record_by_uuid_for_update",
            return_value=mock_record,
        ):
            with pytest.raises(ValueError, match="request_id is required"):
                bot_config = MagicMock(spec=BotConfig)
                bot_config.share_policy = None
                bot_config.deploy_config = None
                bot_config.entity_id = ""
                bot_config.entity_type = ""
                bot_config.sla_grade = ""
                bot_config.callback_timeout_seconds = None
                bot_config.auto_approve_publish = False

                await service.update_bot(
                    tenant="test_tenant",
                    bot_uuid="BOT-001",
                    operator="user1",
                    bot_config=bot_config,
                    # request_id intentionally omitted
                )

    @pytest.mark.asyncio
    async def test_update_bot_template_only_triggers_publish(self):
        """A template-only change creates UPDATE while preserving stored config."""
        mock_record = MagicMock()
        mock_record.id = 1
        mock_record.bot_uuid = "BOT-001"
        mock_record.status = "ACTIVE"
        mock_record.name = "Test Bot"
        mock_record.extra_config = {"entity_type": "staff"}
        mock_publish = MagicMock()
        mock_publish.id = 778
        mock_updated_bot = MagicMock()
        mock_updated_bot.model_dump.return_value = {
            "id": 1,
            "bot_uuid": "BOT-001",
            "tenant": "test_tenant",
            "env": "dev",
            "domain": "default",
            "is_deleted": 0,
            "creator": "user1",
            "modifier": "user1",
            "status": "ACTIVE",
            "name": "Test Bot",
            "description": None,
            "template_uuid": "TEMPLATE-old",
            "replica_desired": 1,
            "replica_minimum": 1,
            "replica_maximum": 10,
            "auto_scaling_enabled": 0,
            "sla_grade": "standard",
            "gmt_create": "2024-01-01T00:00:00",
            "gmt_modified": "2024-01-01T00:00:00",
            "config": None,
        }
        mock_device_repo = MagicMock()
        mock_device_repo.list_by_bot_id.return_value = [MagicMock()]
        mock_publish_service = MagicMock()
        mock_publish_service.create_publish = AsyncMock(return_value=mock_publish)
        mock_bot_service = MagicMock()
        mock_bot_service.get_bot = AsyncMock(return_value=mock_updated_bot)
        mock_system_config_repo = MagicMock()
        mock_system_config_repo.get_by_env_and_key.return_value = None
        service = _make_service(
            device_repo=mock_device_repo,
            publish_service=mock_publish_service,
            bot_service=mock_bot_service,
            system_config_repo=mock_system_config_repo,
        )

        with patch.object(
            service,
            "_get_operational_bot_record_by_uuid_for_update",
            return_value=mock_record,
        ):
            result = await service.update_bot(
                tenant="test_tenant",
                bot_uuid="BOT-001",
                operator="user1",
                request_id="template-update-request",
                template_uuid="TEMPLATE-new",
            )

        assert result.publish_id == 778
        publish_config = mock_publish_service.create_publish.call_args.kwargs["config"]
        assert publish_config.template_uuid == "TEMPLATE-new"
        assert publish_config.deploy_config is None

    @pytest.mark.asyncio
    async def test_update_bot_template_without_request_id(self):
        """A template change requires a correlated UPDATE request."""
        mock_record = MagicMock(
            id=1,
            bot_uuid="BOT-001",
            status="ACTIVE",
            name="Test Bot",
            extra_config={},
        )
        service = _make_service()

        with patch.object(
            service,
            "_get_operational_bot_record_by_uuid_for_update",
            return_value=mock_record,
        ):
            with pytest.raises(
                ValueError, match="request_id is required when updating template_uuid"
            ):
                await service.update_bot(
                    tenant="test_tenant",
                    bot_uuid="BOT-001",
                    operator="user1",
                    template_uuid="TEMPLATE-new",
                )

    @pytest.mark.asyncio
    async def test_update_bot_destroying_name_only(self):
        """Test update_bot allows name update for DESTROYING bot"""
        mock_record = MagicMock()
        mock_record.id = 1
        mock_record.bot_uuid = "BOT-001"
        mock_record.status = "DESTROYING"
        mock_record.extra_config = {}

        mock_updated_bot = MagicMock()
        mock_updated_bot.model_dump.return_value = {
            "id": 1,
            "bot_uuid": "BOT-001",
            "tenant": "test_tenant",
            "env": "dev",
            "domain": "default",
            "is_deleted": 0,
            "creator": "user1",
            "modifier": "user1",
            "status": "DESTROYING",
            "name": "Updated During Destroy",
            "description": None,
            "template_uuid": None,
            "replica_desired": 1,
            "replica_minimum": 1,
            "replica_maximum": 10,
            "auto_scaling_enabled": 0,
            "sla_grade": "standard",
            "gmt_create": "2024-01-01T00:00:00",
            "gmt_modified": "2024-01-01T00:00:00",
            "config": None,
        }

        mock_bot_repo = MagicMock()
        mock_bot_repo.get_by_bot_uuid.return_value = mock_record
        mock_device_repo = MagicMock()
        mock_device_repo.list_by_bot_id.return_value = [MagicMock()]
        mock_bot_service = MagicMock()
        mock_bot_service.get_bot = AsyncMock(return_value=mock_updated_bot)
        service = _make_service(
            bot_repo=mock_bot_repo,
            device_repo=mock_device_repo,
            bot_service=mock_bot_service,
        )

        with patch.object(
            service,
            "_get_operational_bot_record_by_uuid_for_update",
            return_value=mock_record,
        ):
            result = await service.update_bot(
                tenant="test_tenant",
                bot_uuid="BOT-001",
                operator="user1",
                bot_name="Updated During Destroy",
            )

            assert result is not None
            assert result.publish_id is None
            mock_bot_repo.update_bot.assert_called_once()

    @pytest.mark.asyncio
    async def test_update_bot_destroying_rejects_config(self):
        """Test update_bot rejects config update for DESTROYING bot"""
        mock_record = MagicMock()
        mock_record.id = 1
        mock_record.bot_uuid = "BOT-001"
        mock_record.status = "DESTROYING"
        mock_record.extra_config = {}

        service = _make_service()

        with patch.object(
            service,
            "_get_operational_bot_record_by_uuid_for_update",
            return_value=mock_record,
        ):
            with pytest.raises(
                ValueError, match="Cannot update bot config while bot is in DESTROYING"
            ):
                bot_config = MagicMock(spec=BotConfig)
                await service.update_bot(
                    tenant="test_tenant",
                    bot_uuid="BOT-001",
                    operator="user1",
                    bot_config=bot_config,
                    request_id="test-request-id-12345678901234567890",
                )

    @pytest.mark.asyncio
    async def test_update_bot_destroying_rejects_template(self):
        """A DESTROYING bot cannot start a template migration."""
        mock_record = MagicMock(
            id=1,
            bot_uuid="BOT-001",
            status="DESTROYING",
            extra_config={},
        )
        service = _make_service()

        with patch.object(
            service,
            "_get_operational_bot_record_by_uuid_for_update",
            return_value=mock_record,
        ):
            with pytest.raises(
                ValueError,
                match="Cannot update bot template while bot is in DESTROYING",
            ):
                await service.update_bot(
                    tenant="test_tenant",
                    bot_uuid="BOT-001",
                    operator="user1",
                    request_id="template-update-request",
                    template_uuid="TEMPLATE-new",
                )

    @pytest.mark.asyncio
    async def test_update_bot_merges_config_with_existing(self):
        """Test update_bot merges shared_policy from existing stored config"""
        mock_record = MagicMock()
        mock_record.id = 1
        mock_record.bot_uuid = "BOT-001"
        mock_record.status = "ACTIVE"
        mock_record.name = "Test Bot"
        # Existing stored config with share_policy
        mock_record.extra_config = {"share_policy": {"existing": "value"}}

        mock_publish = MagicMock()
        mock_publish.id = 888

        mock_updated_bot = MagicMock()
        mock_updated_bot.model_dump.return_value = {
            "id": 1,
            "bot_uuid": "BOT-001",
            "tenant": "test_tenant",
            "env": "dev",
            "domain": "default",
            "is_deleted": 0,
            "creator": "user1",
            "modifier": "user1",
            "status": "ACTIVE",
            "name": "Test Bot",
            "description": None,
            "template_uuid": None,
            "replica_desired": 1,
            "replica_minimum": 1,
            "replica_maximum": 10,
            "auto_scaling_enabled": 0,
            "sla_grade": "standard",
            "gmt_create": "2024-01-01T00:00:00",
            "gmt_modified": "2024-01-01T00:00:00",
            "config": None,
        }

        mock_bot_repo = MagicMock()
        mock_device_repo = MagicMock()
        mock_device_repo.list_by_bot_id.return_value = [MagicMock()]
        mock_publish_service = MagicMock()
        mock_publish_service.create_publish = AsyncMock(return_value=mock_publish)
        mock_bot_service = MagicMock()
        mock_bot_service.get_bot = AsyncMock(return_value=mock_updated_bot)
        service = _make_service(
            bot_repo=mock_bot_repo,
            device_repo=mock_device_repo,
            publish_service=mock_publish_service,
            bot_service=mock_bot_service,
        )

        with patch.object(
            service,
            "_get_operational_bot_record_by_uuid_for_update",
            return_value=mock_record,
        ):
            # New config only changes entity_id, existing share_policy should be preserved
            bot_config = MagicMock(spec=BotConfig)
            bot_config.share_policy = None
            bot_config.deploy_config = None
            bot_config.entity_id = "new-entity"
            bot_config.entity_type = ""
            bot_config.sla_grade = ""
            bot_config.callback_timeout_seconds = None
            bot_config.auto_approve_publish = False

            result = await service.update_bot(
                tenant="test_tenant",
                bot_uuid="BOT-001",
                operator="user1",
                bot_config=bot_config,
                request_id="test-request-id-12345678901234567890",
            )

        assert result is not None
        assert result.publish_id == 888

    @pytest.mark.asyncio
    async def test_update_devices_merges_callback_timeout_seconds(self):
        mock_record = MagicMock()
        mock_record.id = 1
        mock_record.bot_uuid = "BOT-001"
        mock_record.status = "ACTIVE"
        mock_record.extra_config = {
            "share_policy": {"old": "value"},
            "callback_timeout_seconds": 1800,
        }
        mock_record.model_dump = MagicMock(
            return_value={"id": 1, "bot_uuid": "BOT-001"}
        )

        mock_publish = MagicMock()
        mock_publish.id = 889

        mock_bot_response = MagicMock()
        mock_bot_response.id = 1
        mock_bot_response.status = "ACTIVE"
        mock_bot_response.model_dump = MagicMock(
            return_value={
                "id": 1,
                "bot_uuid": "BOT-001",
                "tenant": "test_tenant",
                "env": "dev",
                "domain": "default",
                "is_deleted": 0,
                "creator": "user1",
                "modifier": "user1",
                "status": "ACTIVE",
                "name": "Test Bot",
                "description": None,
                "template_uuid": None,
                "replica_desired": 1,
                "replica_minimum": 1,
                "replica_maximum": 10,
                "auto_scaling_enabled": 0,
                "sla_grade": "standard",
                "gmt_create": "2024-01-01T00:00:00",
                "gmt_modified": "2024-01-01T00:00:00",
            }
        )

        mock_bot_repo = MagicMock()
        mock_bot_repo.get_by_bot_uuid.return_value = mock_record
        mock_device_repo = MagicMock()
        mock_device = MagicMock()
        mock_device.device_uuid = "DEV-001"
        mock_device_repo.list_by_bot_id.return_value = [mock_device]
        mock_publish_service = MagicMock()
        mock_publish_service.create_publish = AsyncMock(return_value=mock_publish)
        service = _make_service(
            bot_repo=mock_bot_repo,
            device_repo=mock_device_repo,
            publish_service=mock_publish_service,
        )

        record_for_get = MagicMock()
        record_for_get.id = 1
        record_for_get.extra_config = {
            "share_policy": {"old": "value"},
            "callback_timeout_seconds": 1800,
        }

        with (
            patch.object(
                service,
                "get_bot",
                new_callable=AsyncMock,
                return_value=mock_bot_response,
            ),
            patch.object(
                service, "_get_bot_record_by_uuid", return_value=record_for_get
            ),
        ):
            bot_config = BotConfig(
                share_policy={"new": "policy"},
                deploy_config=None,
                entity_id="",
                entity_type="",
                sla_grade="standard",
                callback_timeout_seconds=300,
                auto_approve_publish=False,
            )

            result = await service.update_devices(
                tenant="test_tenant",
                bot_uuid="BOT-001",
                operator="user1",
                request_id="test-request-id-12345678901234567890",
                device_uuids=["DEV-001"],
                config=bot_config,
            )

        assert result is not None
        assert result.publish_id == 889

    @pytest.mark.asyncio
    async def test_update_bot_config_with_name_and_desc(self):
        """Test update_bot config publish also updates name and description"""
        mock_record = MagicMock()
        mock_record.id = 1
        mock_record.bot_uuid = "BOT-001"
        mock_record.status = "ACTIVE"
        mock_record.extra_config = {}
        mock_record.name = "Old Name"

        mock_publish = MagicMock()
        mock_publish.id = 999

        mock_updated_bot = MagicMock()
        mock_updated_bot.model_dump.return_value = {
            "id": 1,
            "bot_uuid": "BOT-001",
            "tenant": "test_tenant",
            "env": "dev",
            "domain": "default",
            "is_deleted": 0,
            "creator": "user1",
            "modifier": "user1",
            "status": "ACTIVE",
            "name": "New Name",
            "description": "New Desc",
            "template_uuid": None,
            "replica_desired": 1,
            "replica_minimum": 1,
            "replica_maximum": 10,
            "auto_scaling_enabled": 0,
            "sla_grade": "standard",
            "gmt_create": "2024-01-01T00:00:00",
            "gmt_modified": "2024-01-01T00:00:00",
            "config": None,
        }

        mock_bot_repo = MagicMock()
        mock_device_repo = MagicMock()
        mock_device_repo.list_by_bot_id.return_value = [MagicMock()]
        mock_publish_service = MagicMock()
        mock_publish_service.create_publish = AsyncMock(return_value=mock_publish)
        mock_bot_service = MagicMock()
        mock_bot_service.get_bot = AsyncMock(return_value=mock_updated_bot)
        service = _make_service(
            bot_repo=mock_bot_repo,
            device_repo=mock_device_repo,
            publish_service=mock_publish_service,
            bot_service=mock_bot_service,
        )

        with patch.object(
            service,
            "_get_operational_bot_record_by_uuid_for_update",
            return_value=mock_record,
        ):
            bot_config = MagicMock(spec=BotConfig)
            bot_config.share_policy = None
            bot_config.deploy_config = None
            bot_config.entity_id = "entity-123"
            bot_config.entity_type = ""
            bot_config.sla_grade = ""
            bot_config.callback_timeout_seconds = None
            bot_config.auto_approve_publish = False

            result = await service.update_bot(
                tenant="test_tenant",
                bot_uuid="BOT-001",
                operator="user1",
                bot_name="New Name",
                bot_desc="New Desc",
                bot_config=bot_config,
                request_id="test-request-id-12345678901234567890",
            )

            assert result is not None
            assert result.publish_id == 999

    @pytest.mark.asyncio
    async def test_update_bot_with_empty_config(self):
        """Test update_bot with config that has no changes (all None/empty fields)"""
        mock_record = MagicMock()
        mock_record.id = 1
        mock_record.bot_uuid = "BOT-001"
        mock_record.status = "ACTIVE"
        mock_record.extra_config = {}
        mock_record.name = "Test Bot"

        mock_publish = MagicMock()
        mock_publish.id = 1000

        mock_updated_bot = MagicMock()
        mock_updated_bot.model_dump.return_value = {
            "id": 1,
            "bot_uuid": "BOT-001",
            "tenant": "test_tenant",
            "env": "dev",
            "domain": "default",
            "is_deleted": 0,
            "creator": "user1",
            "modifier": "user1",
            "status": "ACTIVE",
            "name": "Test Bot",
            "description": None,
            "template_uuid": None,
            "replica_desired": 1,
            "replica_minimum": 1,
            "replica_maximum": 10,
            "auto_scaling_enabled": 0,
            "sla_grade": "standard",
            "gmt_create": "2024-01-01T00:00:00",
            "gmt_modified": "2024-01-01T00:00:00",
            "config": None,
        }

        mock_bot_repo = MagicMock()
        mock_device_repo = MagicMock()
        mock_device_repo.list_by_bot_id.return_value = [MagicMock()]
        mock_publish_service = MagicMock()
        mock_publish_service.create_publish = AsyncMock(return_value=mock_publish)
        mock_bot_service = MagicMock()
        mock_bot_service.get_bot = AsyncMock(return_value=mock_updated_bot)
        service = _make_service(
            bot_repo=mock_bot_repo,
            device_repo=mock_device_repo,
            publish_service=mock_publish_service,
            bot_service=mock_bot_service,
        )

        with patch.object(
            service,
            "_get_operational_bot_record_by_uuid_for_update",
            return_value=mock_record,
        ):
            # Config with all None defaults - still triggers publish
            bot_config = MagicMock(spec=BotConfig)
            bot_config.share_policy = None
            bot_config.deploy_config = None
            bot_config.entity_id = ""
            bot_config.entity_type = ""
            bot_config.sla_grade = ""
            bot_config.callback_timeout_seconds = None
            bot_config.auto_approve_publish = False

            result = await service.update_bot(
                tenant="test_tenant",
                bot_uuid="BOT-001",
                operator="user1",
                bot_config=bot_config,
                request_id="test-request-id-12345678901234567890",
            )

            assert result is not None
            assert result.publish_id == 1000


class TestResolveCallbackTimeoutException:
    """Tests for resolve_callback_timeout exception handling"""

    def test_exception_in_get_config_falls_back_to_default(self):
        """Exception in get_config falls back to code constant"""
        mock_sys_repo = MagicMock()
        mock_sys_repo.get_by_env_and_key.side_effect = RuntimeError(
            "DB connection error"
        )

        result = resolve_callback_timeout(
            user_value=None, system_config_repo=mock_sys_repo
        )
        assert result == DEFAULT_CALLBACK_TIMEOUT_SECONDS


class TestUpdateBotNotFound:
    """Tests for update_bot when bot not found"""

    @pytest.mark.asyncio
    async def test_raises_not_found_error(self):
        """Test update_bot raises BotNotFoundError when record not found"""
        service = _make_service()

        with patch.object(
            service,
            "_get_operational_bot_record_by_uuid_for_update",
            return_value=None,
        ):
            with pytest.raises(BotNotFoundError, match="BOT-NOT-FOUND"):
                await service.update_bot(
                    tenant="test_tenant",
                    bot_uuid="BOT-NOT-FOUND",
                    operator="user1",
                    bot_name="New Name",
                )


# ==================== Tests for uncovered lines ====================


class TestListBotsPendingAndReleased:
    """Tests for list_bots with PENDING and RELEASED status filters"""

    @pytest.mark.asyncio
    async def test_list_bots_with_pending_status(self):
        """Test list_bots passes PENDING status filter correctly"""
        mock_result = MagicMock()
        mock_result.items = []
        mock_result.total = 0
        mock_result.page = 1
        mock_result.page_size = 20

        mock_bot_service = MagicMock()
        mock_bot_service.list_bots = AsyncMock(return_value=mock_result)
        service = _make_service(bot_service=mock_bot_service)

        await service.list_bots(
            tenant="test_tenant",
            status="PENDING",
            page=1,
            page_size=20,
        )

        call_kwargs = mock_bot_service.list_bots.call_args.kwargs
        assert call_kwargs["status"] == BotStatus.PENDING

    @pytest.mark.asyncio
    async def test_list_bots_with_released_status(self):
        """Test list_bots passes RELEASED status filter correctly"""
        mock_result = MagicMock()
        mock_result.items = []
        mock_result.total = 0
        mock_result.page = 1
        mock_result.page_size = 20

        mock_bot_service = MagicMock()
        mock_bot_service.list_bots = AsyncMock(return_value=mock_result)
        service = _make_service(bot_service=mock_bot_service)

        await service.list_bots(
            tenant="test_tenant",
            status="RELEASED",
            page=1,
            page_size=20,
        )

        call_kwargs = mock_bot_service.list_bots.call_args.kwargs
        assert call_kwargs["status"] == BotStatus.RELEASED

    @pytest.mark.asyncio
    async def test_list_bots_with_invalid_status(self):
        """Test list_bots with status string that is not a BotStatus enum value"""
        mock_result = MagicMock()
        mock_result.items = []
        mock_result.total = 0
        mock_result.page = 1
        mock_result.page_size = 20

        mock_bot_service = MagicMock()
        mock_bot_service.list_bots = AsyncMock(return_value=mock_result)
        service = _make_service(bot_service=mock_bot_service)

        await service.list_bots(
            tenant="test_tenant",
            status="INVALID_STATUS",
            page=1,
            page_size=20,
        )

        call_kwargs = mock_bot_service.list_bots.call_args.kwargs
        assert call_kwargs["status"] is None


class TestListBotsWithDevicesStatusFilter:
    """Tests for list_bots_with_devices with PENDING/FAILED/RELEASED status"""

    @pytest.mark.asyncio
    async def test_list_bots_with_devices_pending_status(self):
        """Test list_bots_with_devices passes PENDING status"""
        mock_result = MagicMock()
        mock_result.items = []
        mock_result.total = 0
        mock_result.page = 1
        mock_result.page_size = 20

        mock_bot_service = MagicMock()
        mock_bot_service.list_bots = AsyncMock(return_value=mock_result)
        mock_device_repo = MagicMock()
        mock_device_repo.list_devices_by_bot_ids.return_value = {}
        service = _make_service(
            bot_service=mock_bot_service,
            device_repo=mock_device_repo,
        )

        await service.list_bots_with_devices(
            tenant="test_tenant",
            status="PENDING",
        )

        call_kwargs = mock_bot_service.list_bots.call_args.kwargs
        assert call_kwargs["status"] == BotStatus.PENDING

    @pytest.mark.asyncio
    async def test_list_bots_with_devices_failed_status(self):
        """Test list_bots_with_devices passes FAILED status"""
        mock_result = MagicMock()
        mock_result.items = []
        mock_result.total = 0
        mock_result.page = 1
        mock_result.page_size = 20

        mock_bot_service = MagicMock()
        mock_bot_service.list_bots = AsyncMock(return_value=mock_result)
        mock_device_repo = MagicMock()
        mock_device_repo.list_devices_by_bot_ids.return_value = {}
        service = _make_service(
            bot_service=mock_bot_service,
            device_repo=mock_device_repo,
        )

        await service.list_bots_with_devices(
            tenant="test_tenant",
            status="FAILED",
        )

        call_kwargs = mock_bot_service.list_bots.call_args.kwargs
        assert call_kwargs["status"] == BotStatus.FAILED

    @pytest.mark.asyncio
    async def test_list_bots_with_devices_released_status(self):
        """Test list_bots_with_devices passes RELEASED status"""
        mock_result = MagicMock()
        mock_result.items = []
        mock_result.total = 0
        mock_result.page = 1
        mock_result.page_size = 20

        mock_bot_service = MagicMock()
        mock_bot_service.list_bots = AsyncMock(return_value=mock_result)
        mock_device_repo = MagicMock()
        mock_device_repo.list_devices_by_bot_ids.return_value = {}
        service = _make_service(
            bot_service=mock_bot_service,
            device_repo=mock_device_repo,
        )

        await service.list_bots_with_devices(
            tenant="test_tenant",
            status="RELEASED",
        )

        call_kwargs = mock_bot_service.list_bots.call_args.kwargs
        assert call_kwargs["status"] == BotStatus.RELEASED

    @pytest.mark.asyncio
    async def test_list_bots_with_devices_invalid_status(self):
        """Test list_bots_with_devices with invalid status string passes None"""
        mock_result = MagicMock()
        mock_result.items = []
        mock_result.total = 0
        mock_result.page = 1
        mock_result.page_size = 20

        mock_bot_service = MagicMock()
        mock_bot_service.list_bots = AsyncMock(return_value=mock_result)
        mock_device_repo = MagicMock()
        mock_device_repo.list_devices_by_bot_ids.return_value = {}
        service = _make_service(
            bot_service=mock_bot_service,
            device_repo=mock_device_repo,
        )

        await service.list_bots_with_devices(
            tenant="test_tenant",
            status="INVALID",
        )

        call_kwargs = mock_bot_service.list_bots.call_args.kwargs
        assert call_kwargs["status"] is None

    @pytest.mark.asyncio
    async def test_list_bots_with_devices_negative_page(self):
        """Test list_bots_with_devices caps negative page to 1"""
        mock_result = MagicMock()
        mock_result.items = []
        mock_result.total = 0
        mock_result.page = 1
        mock_result.page_size = 100

        mock_bot_service = MagicMock()
        mock_bot_service.list_bots = AsyncMock(return_value=mock_result)
        mock_device_repo = MagicMock()
        mock_device_repo.list_devices_by_bot_ids.return_value = {}
        service = _make_service(
            bot_service=mock_bot_service,
            device_repo=mock_device_repo,
        )

        await service.list_bots_with_devices(
            tenant="test_tenant",
            page=-5,
        )

        call_kwargs = mock_bot_service.list_bots.call_args.kwargs
        assert call_kwargs["page"] == 1


class TestScaleBotNotFound:
    """Tests for scale_bot when bot is not found"""

    @pytest.mark.asyncio
    async def test_scale_bot_raises_not_found(self):
        """Test scale_bot raises BotNotFoundError when get_bot returns None"""
        service = _make_service()

        with patch.object(
            service,
            "get_bot",
            new_callable=AsyncMock,
            return_value=None,
        ):
            with pytest.raises(BotNotFoundError, match="BOT-NOT-FOUND"):
                await service.scale_bot(
                    tenant="test_tenant",
                    bot_uuid="BOT-NOT-FOUND",
                    target_count=5,
                    operator="user1",
                    request_id="test-request-id-12345678901234567890",
                )


class TestRestartBotNotFound:
    """Tests for restart_bot when bot is not found"""

    @pytest.mark.asyncio
    async def test_restart_bot_raises_not_found(self):
        """Test restart_bot raises BotNotFoundError when get_bot returns None"""
        service = _make_service()

        with patch.object(
            service,
            "get_bot",
            new_callable=AsyncMock,
            return_value=None,
        ):
            with pytest.raises(BotNotFoundError, match="BOT-NOT-FOUND"):
                await service.restart_bot(
                    tenant="test_tenant",
                    bot_uuid="BOT-NOT-FOUND",
                    operator="user1",
                    request_id="test-request-id-12345678901234567890",
                )


class TestDestroyBotRuntimeError:
    """Tests for destroy_bot RuntimeError edge case"""

    @pytest.mark.asyncio
    async def test_destroy_bot_raises_runtime_error_after_status_update(self):
        """Test destroy_bot raises RuntimeError when bot disappears after status update"""
        mock_bot_response = MagicMock()
        mock_bot_response.id = 1
        mock_bot_response.bot_uuid = "BOT-001"
        mock_bot_response.status = BotStatus.ACTIVE.value

        mock_publish = MagicMock()
        mock_publish.id = 123

        mock_bot_repo = MagicMock()
        mock_bot_service = MagicMock()
        mock_bot_service.get_bot = AsyncMock(return_value=None)  # Bot disappears
        mock_publish_service = MagicMock()
        mock_publish_service.create_publish = AsyncMock(return_value=mock_publish)
        service = _make_service(
            bot_repo=mock_bot_repo,
            bot_service=mock_bot_service,
            publish_service=mock_publish_service,
        )

        with patch.object(
            service,
            "get_bot",
            new_callable=AsyncMock,
            return_value=mock_bot_response,
        ):
            with pytest.raises(RuntimeError, match="Bot not found after status update"):
                await service.destroy_bot(
                    tenant="test_tenant",
                    bot_uuid="BOT-001",
                    operator="user1",
                    request_id="test-request-id-12345678901234567890",
                )


class TestUpdateBotRuntimeError:
    """Tests for update_bot RuntimeError edge case"""

    @pytest.mark.asyncio
    async def test_update_bot_raises_runtime_error_after_update(self):
        """Test update_bot raises RuntimeError when bot disappears after update"""
        mock_record = MagicMock()
        mock_record.id = 1
        mock_record.bot_uuid = "BOT-001"
        mock_record.status = "ACTIVE"
        mock_record.extra_config = {}
        mock_record.name = "Test Bot"

        mock_bot_repo = MagicMock()
        mock_bot_service = MagicMock()
        mock_bot_service.get_bot = AsyncMock(return_value=None)  # Bot disappears
        service = _make_service(
            bot_repo=mock_bot_repo,
            bot_service=mock_bot_service,
        )

        with patch.object(
            service,
            "_get_operational_bot_record_by_uuid_for_update",
            return_value=mock_record,
        ):
            with pytest.raises(RuntimeError, match="Bot not found after update"):
                await service.update_bot(
                    tenant="test_tenant",
                    bot_uuid="BOT-001",
                    operator="user1",
                    bot_name="New Name",
                )


class TestUpdateBotConfigMerges:
    """Tests for update_bot config merge branches"""

    @pytest.mark.asyncio
    async def test_update_bot_merges_deploy_config(self):
        """Test update_bot merges deploy_config into stored config"""
        mock_record = MagicMock()
        mock_record.id = 1
        mock_record.bot_uuid = "BOT-001"
        mock_record.status = "ACTIVE"
        mock_record.extra_config = {}
        mock_record.name = "Test Bot"

        mock_publish = MagicMock()
        mock_publish.id = 888

        mock_updated_bot = MagicMock()
        mock_updated_bot.model_dump.return_value = {
            "id": 1,
            "bot_uuid": "BOT-001",
            "tenant": "test_tenant",
            "env": "dev",
            "domain": "default",
            "is_deleted": 0,
            "creator": "user1",
            "modifier": "user1",
            "status": "ACTIVE",
            "name": "Test Bot",
            "description": None,
            "template_uuid": None,
            "replica_desired": 1,
            "replica_minimum": 1,
            "replica_maximum": 10,
            "auto_scaling_enabled": 0,
            "sla_grade": "standard",
            "gmt_create": "2024-01-01T00:00:00",
            "gmt_modified": "2024-01-01T00:00:00",
            "config": None,
        }

        mock_bot_repo = MagicMock()
        mock_device_repo = MagicMock()
        mock_device_repo.list_by_bot_id.return_value = [MagicMock()]
        mock_publish_service = MagicMock()
        mock_publish_service.create_publish = AsyncMock(return_value=mock_publish)
        mock_bot_service = MagicMock()
        mock_bot_service.get_bot = AsyncMock(return_value=mock_updated_bot)
        service = _make_service(
            bot_repo=mock_bot_repo,
            device_repo=mock_device_repo,
            publish_service=mock_publish_service,
            bot_service=mock_bot_service,
        )

        with patch.object(
            service,
            "_get_operational_bot_record_by_uuid_for_update",
            return_value=mock_record,
        ):
            bot_config = MagicMock(spec=BotConfig)
            bot_config.share_policy = None
            bot_config.deploy_config = {"image": "v2"}
            bot_config.entity_id = ""
            bot_config.entity_type = ""
            bot_config.sla_grade = ""
            bot_config.callback_timeout_seconds = None
            bot_config.auto_approve_publish = False

            result = await service.update_bot(
                tenant="test_tenant",
                bot_uuid="BOT-001",
                operator="user1",
                bot_config=bot_config,
                request_id="test-request-id-12345678901234567890",
            )

            assert result.publish_id == 888

    @pytest.mark.asyncio
    async def test_update_bot_merges_entity_type(self):
        """Test update_bot merges entity_type into stored config"""
        mock_record = MagicMock()
        mock_record.id = 1
        mock_record.bot_uuid = "BOT-001"
        mock_record.status = "ACTIVE"
        mock_record.extra_config = {}
        mock_record.name = "Test Bot"

        mock_publish = MagicMock()
        mock_publish.id = 889

        mock_updated_bot = MagicMock()
        mock_updated_bot.model_dump.return_value = {
            "id": 1,
            "bot_uuid": "BOT-001",
            "tenant": "test_tenant",
            "env": "dev",
            "domain": "default",
            "is_deleted": 0,
            "creator": "user1",
            "modifier": "user1",
            "status": "ACTIVE",
            "name": "Test Bot",
            "description": None,
            "template_uuid": None,
            "replica_desired": 1,
            "replica_minimum": 1,
            "replica_maximum": 10,
            "auto_scaling_enabled": 0,
            "sla_grade": "standard",
            "gmt_create": "2024-01-01T00:00:00",
            "gmt_modified": "2024-01-01T00:00:00",
            "config": None,
        }

        mock_bot_repo = MagicMock()
        mock_device_repo = MagicMock()
        mock_device_repo.list_by_bot_id.return_value = [MagicMock()]
        mock_publish_service = MagicMock()
        mock_publish_service.create_publish = AsyncMock(return_value=mock_publish)
        mock_bot_service = MagicMock()
        mock_bot_service.get_bot = AsyncMock(return_value=mock_updated_bot)
        service = _make_service(
            bot_repo=mock_bot_repo,
            device_repo=mock_device_repo,
            publish_service=mock_publish_service,
            bot_service=mock_bot_service,
        )

        with patch.object(
            service,
            "_get_operational_bot_record_by_uuid_for_update",
            return_value=mock_record,
        ):
            bot_config = MagicMock(spec=BotConfig)
            bot_config.share_policy = None
            bot_config.deploy_config = None
            bot_config.entity_id = ""
            bot_config.entity_type = "NEW_TYPE"
            bot_config.sla_grade = ""
            bot_config.callback_timeout_seconds = None
            bot_config.auto_approve_publish = False

            result = await service.update_bot(
                tenant="test_tenant",
                bot_uuid="BOT-001",
                operator="user1",
                bot_config=bot_config,
                request_id="test-request-id-12345678901234567890",
            )

            assert result.publish_id == 889

    @pytest.mark.asyncio
    async def test_update_bot_merges_sla_grade(self):
        """Test update_bot merges sla_grade into stored config"""
        mock_record = MagicMock()
        mock_record.id = 1
        mock_record.bot_uuid = "BOT-001"
        mock_record.status = "ACTIVE"
        mock_record.extra_config = {}
        mock_record.name = "Test Bot"

        mock_publish = MagicMock()
        mock_publish.id = 890

        mock_updated_bot = MagicMock()
        mock_updated_bot.model_dump.return_value = {
            "id": 1,
            "bot_uuid": "BOT-001",
            "tenant": "test_tenant",
            "env": "dev",
            "domain": "default",
            "is_deleted": 0,
            "creator": "user1",
            "modifier": "user1",
            "status": "ACTIVE",
            "name": "Test Bot",
            "description": None,
            "template_uuid": None,
            "replica_desired": 1,
            "replica_minimum": 1,
            "replica_maximum": 10,
            "auto_scaling_enabled": 0,
            "sla_grade": "standard",
            "gmt_create": "2024-01-01T00:00:00",
            "gmt_modified": "2024-01-01T00:00:00",
            "config": None,
        }

        mock_bot_repo = MagicMock()
        mock_device_repo = MagicMock()
        mock_device_repo.list_by_bot_id.return_value = [MagicMock()]
        mock_publish_service = MagicMock()
        mock_publish_service.create_publish = AsyncMock(return_value=mock_publish)
        mock_bot_service = MagicMock()
        mock_bot_service.get_bot = AsyncMock(return_value=mock_updated_bot)
        service = _make_service(
            bot_repo=mock_bot_repo,
            device_repo=mock_device_repo,
            publish_service=mock_publish_service,
            bot_service=mock_bot_service,
        )

        with patch.object(
            service,
            "_get_operational_bot_record_by_uuid_for_update",
            return_value=mock_record,
        ):
            bot_config = MagicMock(spec=BotConfig)
            bot_config.share_policy = None
            bot_config.deploy_config = None
            bot_config.entity_id = ""
            bot_config.entity_type = ""
            bot_config.sla_grade = "premium"
            bot_config.callback_timeout_seconds = None
            bot_config.auto_approve_publish = False

            result = await service.update_bot(
                tenant="test_tenant",
                bot_uuid="BOT-001",
                operator="user1",
                bot_config=bot_config,
                request_id="test-request-id-12345678901234567890",
            )

            assert result.publish_id == 890

    @pytest.mark.asyncio
    async def test_update_bot_merges_callback_timeout_seconds(self):
        """Test update_bot merges callback_timeout_seconds into stored config."""
        mock_record = MagicMock()
        mock_record.id = 1
        mock_record.bot_uuid = "BOT-001"
        mock_record.status = "ACTIVE"
        mock_record.extra_config = {"callback_timeout_seconds": 1800}
        mock_record.name = "Test Bot"

        mock_publish = MagicMock()
        mock_publish.id = 891

        mock_updated_bot = MagicMock()
        mock_updated_bot.model_dump.return_value = {
            "id": 1,
            "bot_uuid": "BOT-001",
            "tenant": "test_tenant",
            "env": "dev",
            "domain": "default",
            "is_deleted": 0,
            "creator": "user1",
            "modifier": "user1",
            "status": "ACTIVE",
            "name": "Test Bot",
            "description": None,
            "template_uuid": None,
            "replica_desired": 1,
            "replica_minimum": 1,
            "replica_maximum": 10,
            "auto_scaling_enabled": 0,
            "sla_grade": "standard",
            "gmt_create": "2024-01-01T00:00:00",
            "gmt_modified": "2024-01-01T00:00:00",
            "config": None,
        }

        mock_bot_repo = MagicMock()
        mock_device_repo = MagicMock()
        mock_device_repo.list_by_bot_id.return_value = [MagicMock()]
        mock_publish_service = MagicMock()
        mock_publish_service.create_publish = AsyncMock(return_value=mock_publish)
        mock_bot_service = MagicMock()
        mock_bot_service.get_bot = AsyncMock(return_value=mock_updated_bot)
        service = _make_service(
            bot_repo=mock_bot_repo,
            device_repo=mock_device_repo,
            publish_service=mock_publish_service,
            bot_service=mock_bot_service,
        )

        with patch.object(
            service,
            "_get_operational_bot_record_by_uuid_for_update",
            return_value=mock_record,
        ):
            bot_config = MagicMock(spec=BotConfig)
            bot_config.share_policy = None
            bot_config.deploy_config = None
            bot_config.entity_id = ""
            bot_config.entity_type = ""
            bot_config.sla_grade = ""
            bot_config.callback_timeout_seconds = 600
            bot_config.auto_approve_publish = False

            result = await service.update_bot(
                tenant="test_tenant",
                bot_uuid="BOT-001",
                operator="user1",
                bot_config=bot_config,
                request_id="test-request-id-12345678901234567890",
            )

            assert result.publish_id == 891

    @pytest.mark.asyncio
    async def test_update_bot_preserves_existing_callback_timeout_when_none(self):
        """Test update_bot uses default 1800s when incoming callback_timeout is None."""
        mock_record = MagicMock()
        mock_record.id = 1
        mock_record.bot_uuid = "BOT-001"
        mock_record.status = "ACTIVE"
        mock_record.extra_config = {"callback_timeout_seconds": 1200}
        mock_record.name = "Test Bot"

        mock_publish = MagicMock()
        mock_publish.id = 892

        mock_updated_bot = MagicMock()
        mock_updated_bot.model_dump.return_value = {
            "id": 1,
            "bot_uuid": "BOT-001",
            "tenant": "test_tenant",
            "env": "dev",
            "domain": "default",
            "is_deleted": 0,
            "creator": "user1",
            "modifier": "user1",
            "status": "ACTIVE",
            "name": "Test Bot",
            "description": None,
            "template_uuid": None,
            "replica_desired": 1,
            "replica_minimum": 1,
            "replica_maximum": 10,
            "auto_scaling_enabled": 0,
            "sla_grade": "standard",
            "gmt_create": "2024-01-01T00:00:00",
            "gmt_modified": "2024-01-01T00:00:00",
            "config": None,
        }

        mock_bot_repo = MagicMock()
        mock_device_repo = MagicMock()
        mock_device_repo.list_by_bot_id.return_value = [MagicMock()]
        mock_publish_service = MagicMock()
        mock_publish_service.create_publish = AsyncMock(return_value=mock_publish)
        mock_bot_service = MagicMock()
        mock_bot_service.get_bot = AsyncMock(return_value=mock_updated_bot)
        service = _make_service(
            bot_repo=mock_bot_repo,
            device_repo=mock_device_repo,
            publish_service=mock_publish_service,
            bot_service=mock_bot_service,
        )

        with patch.object(
            service,
            "_get_operational_bot_record_by_uuid_for_update",
            return_value=mock_record,
        ):
            bot_config = MagicMock(spec=BotConfig)
            bot_config.share_policy = None
            bot_config.deploy_config = None
            bot_config.entity_id = ""
            bot_config.entity_type = ""
            bot_config.sla_grade = ""
            bot_config.callback_timeout_seconds = None
            bot_config.auto_approve_publish = False

            result = await service.update_bot(
                tenant="test_tenant",
                bot_uuid="BOT-001",
                operator="user1",
                bot_config=bot_config,
                request_id="test-request-id-12345678901234567890",
            )

            assert result.publish_id == 892
            # Verify that None input → default 1800s (not old 1200)
            create_publish_call = mock_publish_service.create_publish.call_args
            publish_config = create_publish_call.kwargs["config"]
            assert (
                publish_config.callback_timeout_seconds
                == DEFAULT_CALLBACK_TIMEOUT_SECONDS
            )


class TestAutoApprovePublish:
    """Tests for auto_approve_publish flag in create_bot lifecycle."""

    @pytest.mark.asyncio
    async def test_create_bot_with_auto_approve_calls_approve_stage(self):
        """create_bot with auto_approve_publish=True calls approve_stage until non-approvable."""
        mock_bot = MagicMock()
        mock_bot.id = 1
        mock_bot.bot_uuid = "BOT-001"
        mock_bot.model_dump = MagicMock(
            return_value={
                "id": 1,
                "bot_uuid": "BOT-001",
                "tenant": "test_tenant",
                "env": "dev",
                "domain": "default",
                "is_deleted": 0,
                "creator": "user1",
                "modifier": "user1",
                "status": "PENDING",
                "name": "AutoBot",
                "description": None,
                "template_uuid": None,
                "replica_desired": 1,
                "replica_minimum": 1,
                "replica_maximum": 10,
                "auto_scaling_enabled": 0,
                "sla_grade": "standard",
                "gmt_create": "2024-01-01T00:00:00",
                "gmt_modified": "2024-01-01T00:00:00",
                "config": None,
            }
        )

        mock_publish = MagicMock()
        mock_publish.id = 123

        # Publish goes PENDING → SUCCESS after auto-approve loop
        mock_publish_pending = MagicMock()
        mock_publish_pending.id = 123
        mock_publish_pending.status = PublishStatus.PENDING.value

        mock_publish_success = MagicMock()
        mock_publish_success.id = 123
        mock_publish_success.status = PublishStatus.SUCCESS.value

        mock_bot_service = MagicMock()
        mock_bot_service.create_bot = AsyncMock(return_value=mock_bot)

        mock_publish_service = MagicMock()
        mock_publish_service.create_publish = AsyncMock(return_value=mock_publish)
        # Return PENDING first (→ approve_stage called), then SUCCESS (loop exits)
        mock_publish_service.get_publish = AsyncMock(
            side_effect=[mock_publish_pending, mock_publish_success]
        )
        mock_publish_service.approve_stage = AsyncMock()

        service = _make_service(
            bot_service=mock_bot_service,
            publish_service=mock_publish_service,
        )

        config = BotConfig(auto_approve_publish=True)
        result = await service.create_bot(
            tenant="test_tenant",
            name="AutoBot",
            template_uuid="TPL-001",
            device_count=1,
            operator="user1",
            request_id="test-request-id-12345678901234567890",
            config=config,
        )

        assert result is not None
        assert result.publish_id == 123
        # background task spawned by _auto_approve_publish needs an event loop tick
        await asyncio.sleep(0)
        mock_publish_service.approve_stage.assert_called_once()
        call_kwargs = mock_publish_service.approve_stage.call_args.kwargs
        assert call_kwargs["tenant"] == "test_tenant"
        assert call_kwargs["publish_id"] == 123
        assert call_kwargs["operator"] == "user1"

    @pytest.mark.asyncio
    async def test_create_bot_without_auto_approve_does_not_call_approve(self):
        """create_bot with auto_approve_publish=False(default) does NOT auto-approve."""
        mock_bot = MagicMock()
        mock_bot.id = 1
        mock_bot.bot_uuid = "BOT-001"
        mock_bot.model_dump = MagicMock(
            return_value={
                "id": 1,
                "bot_uuid": "BOT-001",
                "tenant": "test_tenant",
                "env": "dev",
                "domain": "default",
                "is_deleted": 0,
                "creator": "user1",
                "modifier": "user1",
                "status": "PENDING",
                "name": "ManualBot",
                "description": None,
                "template_uuid": None,
                "replica_desired": 1,
                "replica_minimum": 1,
                "replica_maximum": 10,
                "auto_scaling_enabled": 0,
                "sla_grade": "standard",
                "gmt_create": "2024-01-01T00:00:00",
                "gmt_modified": "2024-01-01T00:00:00",
                "config": None,
            }
        )

        mock_publish = MagicMock()
        mock_publish.id = 456

        mock_bot_service = MagicMock()
        mock_bot_service.create_bot = AsyncMock(return_value=mock_bot)

        mock_publish_service = MagicMock()
        mock_publish_service.create_publish = AsyncMock(return_value=mock_publish)
        mock_publish_service.approve_stage = AsyncMock()

        service = _make_service(
            bot_service=mock_bot_service,
            publish_service=mock_publish_service,
        )

        result = await service.create_bot(
            tenant="test_tenant",
            name="ManualBot",
            template_uuid="TPL-001",
            device_count=1,
            operator="user1",
            request_id="test-request-id-12345678901234567890",
        )

        assert result is not None
        assert result.publish_id == 456
        mock_publish_service.approve_stage.assert_not_called()

    @pytest.mark.asyncio
    async def test_create_bot_auto_approve_stops_on_success(self):
        """create_bot auto_approve stops when publish reaches SUCCESS."""
        mock_bot = MagicMock()
        mock_bot.id = 1
        mock_bot.bot_uuid = "BOT-001"
        mock_bot.model_dump = MagicMock(
            return_value={
                "id": 1,
                "bot_uuid": "BOT-001",
                "tenant": "test_tenant",
                "env": "dev",
                "domain": "default",
                "is_deleted": 0,
                "creator": "user1",
                "modifier": "user1",
                "status": "PENDING",
                "name": "AutoBot",
                "description": None,
                "template_uuid": None,
                "replica_desired": 1,
                "replica_minimum": 1,
                "replica_maximum": 10,
                "auto_scaling_enabled": 0,
                "sla_grade": "standard",
                "gmt_create": "2024-01-01T00:00:00",
                "gmt_modified": "2024-01-01T00:00:00",
                "config": None,
            }
        )

        mock_publish = MagicMock()
        mock_publish.id = 789

        # Already SUCCESS — approve_stage should never be called
        mock_publish_success = MagicMock()
        mock_publish_success.id = 789
        mock_publish_success.status = PublishStatus.SUCCESS.value

        mock_bot_service = MagicMock()
        mock_bot_service.create_bot = AsyncMock(return_value=mock_bot)

        mock_publish_service = MagicMock()
        mock_publish_service.create_publish = AsyncMock(return_value=mock_publish)
        mock_publish_service.get_publish = AsyncMock(return_value=mock_publish_success)
        mock_publish_service.approve_stage = AsyncMock()

        service = _make_service(
            bot_service=mock_bot_service,
            publish_service=mock_publish_service,
        )

        config = BotConfig(auto_approve_publish=True)
        result = await service.create_bot(
            tenant="test_tenant",
            name="AutoBot",
            template_uuid="TPL-001",
            device_count=1,
            operator="user1",
            request_id="test-request-id-12345678901234567890",
            config=config,
        )

        assert result is not None
        assert result.publish_id == 789
        # approve_stage should NOT be called since get_publish returns SUCCESS immediately
        mock_publish_service.approve_stage.assert_not_called()

    @pytest.mark.asyncio
    async def test_auto_approve_loop_exits_cleanly_at_max_iterations(self):
        """_auto_approve_publish exits without throwing when max iterations reached."""
        mock_publish_active = MagicMock()
        mock_publish_active.id = 999
        mock_publish_active.status = PublishStatus.ACTIVE.value

        mock_publish_service = MagicMock()
        # Always ACTIVE — loop iterates up to max_iterations, exits cleanly
        mock_publish_service.get_publish = AsyncMock(return_value=mock_publish_active)
        mock_publish_service.approve_stage = AsyncMock()

        service = _make_service(publish_service=mock_publish_service)

        # Call directly with small max_iterations — should exit without throwing
        await service._auto_approve_publish(
            tenant="test_tenant",
            publish_id=999,
            operator="user1",
            max_iterations=3,
            sleep_seconds=0,
        )

        # Background task needs event loop ticks to execute (max_iterations=3,
        # each iteration: get_publish + sleep(0) → ~7 yields)
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        await asyncio.sleep(0)

        # No exception raised; loop exited cleanly after max_iterations
        # get_publish is called max_iterations times (all returning ACTIVE)
        assert mock_publish_service.get_publish.call_count == 3

    @pytest.mark.asyncio
    async def test_update_bot_with_auto_approve_calls_approve_stage(self):
        """update_bot with auto_approve_publish=True triggers auto-approve loop."""
        mock_record = MagicMock()
        mock_record.id = 1
        mock_record.bot_uuid = "BOT-001"
        mock_record.status = "ACTIVE"
        mock_record.extra_config = {"auto_approve_publish": True}
        mock_record.name = "AutoBot"

        mock_publish = MagicMock()
        mock_publish.id = 888

        mock_publish_pending = MagicMock()
        mock_publish_pending.id = 888
        mock_publish_pending.status = PublishStatus.PENDING.value

        mock_publish_success = MagicMock()
        mock_publish_success.id = 888
        mock_publish_success.status = PublishStatus.SUCCESS.value

        mock_updated_bot = MagicMock()
        mock_updated_bot.model_dump.return_value = {
            "id": 1,
            "bot_uuid": "BOT-001",
            "tenant": "test_tenant",
            "env": "dev",
            "domain": "default",
            "is_deleted": 0,
            "creator": "user1",
            "modifier": "user1",
            "status": "ACTIVE",
            "name": "AutoBot",
            "description": None,
            "template_uuid": None,
            "replica_desired": 1,
            "replica_minimum": 1,
            "replica_maximum": 10,
            "auto_scaling_enabled": 0,
            "sla_grade": "standard",
            "gmt_create": "2024-01-01T00:00:00",
            "gmt_modified": "2024-01-01T00:00:00",
            "config": None,
        }

        mock_bot_repo = MagicMock()
        mock_device_repo = MagicMock()
        mock_device_repo.list_by_bot_id.return_value = [MagicMock()]
        mock_publish_service = MagicMock()
        mock_publish_service.create_publish = AsyncMock(return_value=mock_publish)
        mock_publish_service.get_publish = AsyncMock(
            side_effect=[mock_publish_pending, mock_publish_success]
        )
        mock_publish_service.approve_stage = AsyncMock()
        mock_bot_service = MagicMock()
        mock_bot_service.get_bot = AsyncMock(return_value=mock_updated_bot)
        service = _make_service(
            bot_repo=mock_bot_repo,
            device_repo=mock_device_repo,
            publish_service=mock_publish_service,
            bot_service=mock_bot_service,
        )

        with patch.object(
            service,
            "_get_operational_bot_record_by_uuid_for_update",
            return_value=mock_record,
        ):
            bot_config = MagicMock(spec=BotConfig)
            bot_config.share_policy = None
            bot_config.deploy_config = None
            bot_config.entity_id = ""
            bot_config.entity_type = ""
            bot_config.sla_grade = ""
            bot_config.callback_timeout_seconds = None
            bot_config.auto_approve_publish = True

            result = await service.update_bot(
                tenant="test_tenant",
                bot_uuid="BOT-001",
                operator="user1",
                bot_config=bot_config,
                request_id="test-request-id-12345678901234567890",
            )

        assert result is not None
        assert result.publish_id == 888
        # background task spawned by _auto_approve_publish needs an event loop tick
        await asyncio.sleep(0)
        mock_publish_service.approve_stage.assert_called_once()
        call_kwargs = mock_publish_service.approve_stage.call_args.kwargs
        assert call_kwargs["tenant"] == "test_tenant"
        assert call_kwargs["publish_id"] == 888
        assert call_kwargs["operator"] == "user1"

    @pytest.mark.asyncio
    async def test_update_bot_without_auto_approve_does_not_call_approve(self):
        """update_bot with auto_approve_publish=False does NOT trigger auto-approve."""
        mock_record = MagicMock()
        mock_record.id = 1
        mock_record.bot_uuid = "BOT-001"
        mock_record.status = "ACTIVE"
        mock_record.extra_config = {}
        mock_record.name = "Test Bot"

        mock_publish = MagicMock()
        mock_publish.id = 999

        mock_updated_bot = MagicMock()
        mock_updated_bot.model_dump.return_value = {
            "id": 1,
            "bot_uuid": "BOT-001",
            "tenant": "test_tenant",
            "env": "dev",
            "domain": "default",
            "is_deleted": 0,
            "creator": "user1",
            "modifier": "user1",
            "status": "ACTIVE",
            "name": "Test Bot",
            "description": None,
            "template_uuid": None,
            "replica_desired": 1,
            "replica_minimum": 1,
            "replica_maximum": 10,
            "auto_scaling_enabled": 0,
            "sla_grade": "standard",
            "gmt_create": "2024-01-01T00:00:00",
            "gmt_modified": "2024-01-01T00:00:00",
            "config": None,
        }

        mock_bot_repo = MagicMock()
        mock_device_repo = MagicMock()
        mock_device_repo.list_by_bot_id.return_value = [MagicMock()]
        mock_publish_service = MagicMock()
        mock_publish_service.create_publish = AsyncMock(return_value=mock_publish)
        mock_publish_service.approve_stage = AsyncMock()
        mock_bot_service = MagicMock()
        mock_bot_service.get_bot = AsyncMock(return_value=mock_updated_bot)
        service = _make_service(
            bot_repo=mock_bot_repo,
            device_repo=mock_device_repo,
            publish_service=mock_publish_service,
            bot_service=mock_bot_service,
        )

        with patch.object(
            service,
            "_get_operational_bot_record_by_uuid_for_update",
            return_value=mock_record,
        ):
            bot_config = MagicMock(spec=BotConfig)
            bot_config.share_policy = None
            bot_config.deploy_config = None
            bot_config.entity_id = ""
            bot_config.entity_type = ""
            bot_config.sla_grade = ""
            bot_config.callback_timeout_seconds = None
            bot_config.auto_approve_publish = False

            result = await service.update_bot(
                tenant="test_tenant",
                bot_uuid="BOT-001",
                operator="user1",
                bot_config=bot_config,
                request_id="test-request-id-12345678901234567890",
            )

        assert result is not None
        assert result.publish_id == 999
        mock_publish_service.approve_stage.assert_not_called()

    @pytest.mark.asyncio
    async def test_update_bot_passes_auto_approve_to_publish_config(self):
        """update_bot with auto_approve_publish=True sets auto_approve on PublishConfig."""
        mock_record = MagicMock()
        mock_record.id = 1
        mock_record.bot_uuid = "BOT-001"
        mock_record.status = "ACTIVE"
        mock_record.extra_config = {"auto_approve_publish": True}
        mock_record.name = "AutoBot"

        mock_publish = MagicMock()
        mock_publish.id = 777

        mock_publish_success = MagicMock()
        mock_publish_success.id = 777
        mock_publish_success.status = PublishStatus.SUCCESS.value

        mock_updated_bot = MagicMock()
        mock_updated_bot.model_dump.return_value = {
            "id": 1,
            "bot_uuid": "BOT-001",
            "tenant": "test_tenant",
            "env": "dev",
            "domain": "default",
            "is_deleted": 0,
            "creator": "user1",
            "modifier": "user1",
            "status": "ACTIVE",
            "name": "AutoBot",
            "description": None,
            "template_uuid": None,
            "replica_desired": 1,
            "replica_minimum": 1,
            "replica_maximum": 10,
            "auto_scaling_enabled": 0,
            "sla_grade": "standard",
            "gmt_create": "2024-01-01T00:00:00",
            "gmt_modified": "2024-01-01T00:00:00",
            "config": None,
        }

        mock_bot_repo = MagicMock()
        mock_device_repo = MagicMock()
        mock_device_repo.list_by_bot_id.return_value = [MagicMock()]
        mock_publish_service = MagicMock()
        mock_publish_service.create_publish = AsyncMock(return_value=mock_publish)
        mock_publish_service.get_publish = AsyncMock(return_value=mock_publish_success)
        mock_publish_service.approve_stage = AsyncMock()
        mock_bot_service = MagicMock()
        mock_bot_service.get_bot = AsyncMock(return_value=mock_updated_bot)
        service = _make_service(
            bot_repo=mock_bot_repo,
            device_repo=mock_device_repo,
            publish_service=mock_publish_service,
            bot_service=mock_bot_service,
        )

        with patch.object(
            service,
            "_get_operational_bot_record_by_uuid_for_update",
            return_value=mock_record,
        ):
            bot_config = MagicMock(spec=BotConfig)
            bot_config.share_policy = None
            bot_config.deploy_config = None
            bot_config.entity_id = ""
            bot_config.entity_type = ""
            bot_config.sla_grade = ""
            bot_config.callback_timeout_seconds = None
            bot_config.auto_approve_publish = True

            await service.update_bot(
                tenant="test_tenant",
                bot_uuid="BOT-001",
                operator="user1",
                bot_config=bot_config,
                request_id="test-request-id-12345678901234567890",
            )

        call_kwargs = mock_publish_service.create_publish.call_args.kwargs
        publish_config = call_kwargs["config"]
        assert publish_config.auto_approve is True

        call_kwargs = mock_publish_service.create_publish.call_args.kwargs
        publish_config = call_kwargs["config"]
        assert publish_config.auto_approve is True


class TestApproveStageNoOp:
    """Tests for approve_stage no-op behavior on non-approvable statuses.

    These tests verify the approve API is idempotent: it returns success
    for ACTIVE/SUCCESS and errors for terminal states (REJECTED, FAILED, REVOKED).
    """

    @pytest.mark.asyncio
    async def test_approve_on_success_is_noop(self):
        """approve_stage returns immediately when publish is already SUCCESS."""
        mock_publish = MagicMock()
        mock_publish.id = 100
        mock_publish.status = PublishStatus.SUCCESS.value
        mock_publish.bot_id = 1
        mock_publish.publish_type = "CREATE"
        mock_publish.extra_config = {}
        mock_publish.creator = "user1"
        mock_publish.modifier = "user1"
        mock_publish.gmt_create = "2024-01-01T00:00:00"
        mock_publish.gmt_modified = "2024-01-01T00:00:00"

        mock_bot = MagicMock()
        mock_bot.id = 1

        mock_publish_repo = MagicMock()
        mock_publish_repo.get_by_id.return_value = mock_publish

        mock_bot_repo = MagicMock()
        mock_bot_repo.get_by_id_including_deleted.return_value = mock_bot

        from secbaas.community.core.service.publish_manage import DefaultPublishService

        service = DefaultPublishService(
            bot_repo=mock_bot_repo,
            device_repo=MagicMock(),
            rel_repo=MagicMock(),
            session_repo=MagicMock(),
            publish_repo=mock_publish_repo,
            batch_repo=MagicMock(),
            publish_record_repo=MagicMock(),
            template_service=MagicMock(),
            bot_service=MagicMock(),
            device_service=MagicMock(),
        )

        result = await service.approve_stage(
            tenant="test_tenant",
            publish_id=100,
            operator="user1",
        )

        assert result is not None
        assert result.status == PublishStatus.SUCCESS.value

    @pytest.mark.asyncio
    async def test_approve_on_active_continues_execution(self):
        """approve_stage on ACTIVE publish continues auto-execution."""
        mock_publish = MagicMock()
        mock_publish.id = 100
        mock_publish.status = PublishStatus.ACTIVE.value
        mock_publish.bot_id = 1
        mock_publish.publish_type = "CREATE"
        mock_publish.extra_config = {}
        mock_publish.creator = "user1"
        mock_publish.modifier = "user1"
        mock_publish.gmt_create = "2024-01-01T00:00:00"
        mock_publish.gmt_modified = "2024-01-01T00:00:00"

        mock_bot = MagicMock()
        mock_bot.id = 1

        mock_publish_repo = MagicMock()
        mock_publish_repo.get_by_id.return_value = mock_publish

        mock_bot_repo = MagicMock()
        mock_bot_repo.get_by_id_including_deleted.return_value = mock_bot

        from secbaas.community.core.service.publish_manage import DefaultPublishService

        service = DefaultPublishService(
            bot_repo=mock_bot_repo,
            device_repo=MagicMock(),
            rel_repo=MagicMock(),
            session_repo=MagicMock(),
            publish_repo=mock_publish_repo,
            batch_repo=MagicMock(),
            publish_record_repo=MagicMock(),
            template_service=MagicMock(),
            bot_service=MagicMock(),
            device_service=MagicMock(),
        )

        # Patch _auto_execute_stages to avoid full execution
        with patch.object(service, "_auto_execute_stages", new_callable=AsyncMock):
            result = await service.approve_stage(
                tenant="test_tenant",
                publish_id=100,
                operator="user1",
            )

        assert result is not None
        assert result.status == PublishStatus.ACTIVE.value


class TestStopBot:
    """Tests for BotManagementService.stop_bot"""

    @pytest.mark.asyncio
    async def test_stop_bot_success(self):
        mock_bot_response = MagicMock()
        mock_bot_response.id = 1
        mock_bot_response.bot_uuid = "BOT-001"
        mock_bot_response.status = BotStatus.ACTIVE.value
        mock_bot_response.model_dump = MagicMock(
            return_value={
                "id": 1,
                "bot_uuid": "BOT-001",
                "tenant": "test_tenant",
                "env": "dev",
                "domain": "default",
                "is_deleted": 0,
                "creator": "user1",
                "modifier": "user1",
                "status": BotStatus.ACTIVE.value,
                "name": "Test Bot",
                "description": None,
                "template_uuid": None,
                "replica_desired": 1,
                "replica_minimum": 1,
                "replica_maximum": 10,
                "auto_scaling_enabled": 0,
                "sla_grade": "standard",
                "gmt_create": "2024-01-01T00:00:00",
                "gmt_modified": "2024-01-01T00:00:00",
                "config": None,
            }
        )

        mock_publish = MagicMock()
        mock_publish.id = 789
        mock_updated_bot = MagicMock()
        mock_updated_bot.model_dump = MagicMock(
            return_value={
                "id": 1,
                "bot_uuid": "BOT-001",
                "tenant": "test_tenant",
                "env": "dev",
                "domain": "default",
                "is_deleted": 0,
                "creator": "user1",
                "modifier": "user1",
                "status": BotStatus.STOPPING.value,
                "name": "Test Bot",
                "description": None,
                "template_uuid": None,
                "replica_desired": 1,
                "replica_minimum": 1,
                "replica_maximum": 10,
                "auto_scaling_enabled": 0,
                "sla_grade": "standard",
                "gmt_create": "2024-01-01T00:00:00",
                "gmt_modified": "2024-01-01T00:00:00",
                "config": None,
            }
        )

        mock_bot_repo = MagicMock()
        mock_bot_service = MagicMock()
        mock_bot_service.get_bot = AsyncMock(return_value=mock_updated_bot)
        mock_publish_service = MagicMock()
        mock_publish_service.create_publish = AsyncMock(return_value=mock_publish)

        service = _make_service(
            bot_repo=mock_bot_repo,
            publish_service=mock_publish_service,
            bot_service=mock_bot_service,
        )
        with patch.object(
            service, "get_bot", new_callable=AsyncMock, return_value=mock_bot_response
        ):
            result = await service.stop_bot(
                tenant="test_tenant",
                bot_uuid="BOT-001",
                operator="user1",
                request_id="test-request-id-12345678901234567890",
            )

        assert result is not None
        assert result.publish_id == 789
        assert result.bot_uuid == "BOT-001"
        mock_publish_service.create_publish.assert_called_once()
        call_kwargs = mock_publish_service.create_publish.call_args.kwargs
        assert call_kwargs["publish_type"] == PublishType.STOP
        mock_bot_repo.update_status.assert_called_once()
        status_call_kwargs = mock_bot_repo.update_status.call_args.kwargs
        assert status_call_kwargs["status"] == BotStatus.STOPPING.value

    @pytest.mark.asyncio
    async def test_stop_bot_failed_bot(self):
        mock_bot_response = MagicMock()
        mock_bot_response.id = 1
        mock_bot_response.bot_uuid = "BOT-001"
        mock_bot_response.status = BotStatus.FAILED.value
        mock_bot_response.model_dump = MagicMock(
            return_value={
                "id": 1,
                "bot_uuid": "BOT-001",
                "tenant": "test_tenant",
                "env": "dev",
                "domain": "default",
                "is_deleted": 0,
                "creator": "user1",
                "modifier": "user1",
                "status": BotStatus.FAILED.value,
                "name": "Test Bot",
                "description": None,
                "template_uuid": None,
                "replica_desired": 1,
                "replica_minimum": 1,
                "replica_maximum": 10,
                "auto_scaling_enabled": 0,
                "sla_grade": "standard",
                "gmt_create": "2024-01-01T00:00:00",
                "gmt_modified": "2024-01-01T00:00:00",
                "config": None,
            }
        )

        mock_publish = MagicMock()
        mock_publish.id = 789

        mock_bot_repo = MagicMock()
        mock_updated_bot = MagicMock()
        mock_updated_bot.model_dump = MagicMock(
            return_value={
                "id": 1,
                "bot_uuid": "BOT-001",
                "tenant": "test_tenant",
                "env": "dev",
                "domain": "default",
                "is_deleted": 0,
                "creator": "user1",
                "modifier": "user1",
                "status": BotStatus.STOPPING.value,
                "name": "Test Bot",
                "description": None,
                "template_uuid": None,
                "replica_desired": 1,
                "replica_minimum": 1,
                "replica_maximum": 10,
                "auto_scaling_enabled": 0,
                "sla_grade": "standard",
                "gmt_create": "2024-01-01T00:00:00",
                "gmt_modified": "2024-01-01T00:00:00",
                "config": None,
            }
        )
        mock_bot_service = MagicMock()
        mock_bot_service.get_bot = AsyncMock(return_value=mock_updated_bot)
        mock_publish_service = MagicMock()
        mock_publish_service.create_publish = AsyncMock(return_value=mock_publish)

        service = _make_service(
            bot_repo=mock_bot_repo,
            publish_service=mock_publish_service,
            bot_service=mock_bot_service,
        )
        with patch.object(
            service, "get_bot", new_callable=AsyncMock, return_value=mock_bot_response
        ):
            result = await service.stop_bot(
                tenant="test_tenant",
                bot_uuid="BOT-001",
                operator="user1",
                request_id="test-request-id-12345678901234567890",
            )
        assert result is not None

    @pytest.mark.asyncio
    async def test_stop_bot_already_stopped(self):
        mock_bot_response = MagicMock()
        mock_bot_response.status = BotStatus.STOPPED.value
        service = _make_service()
        with patch.object(
            service, "get_bot", new_callable=AsyncMock, return_value=mock_bot_response
        ):
            with pytest.raises(ValueError, match="Cannot stop bot in STOPPED status"):
                await service.stop_bot(
                    tenant="test_tenant",
                    bot_uuid="BOT-001",
                    operator="user1",
                    request_id="test-request-id-12345678901234567890",
                )

    @pytest.mark.asyncio
    async def test_stop_bot_stopping(self):
        mock_bot_response = MagicMock()
        mock_bot_response.status = BotStatus.STOPPING.value
        service = _make_service()
        with patch.object(
            service, "get_bot", new_callable=AsyncMock, return_value=mock_bot_response
        ):
            with pytest.raises(ValueError, match="Cannot stop bot in"):
                await service.stop_bot(
                    tenant="test_tenant",
                    bot_uuid="BOT-001",
                    operator="user1",
                    request_id="test-request-id-12345678901234567890",
                )

    @pytest.mark.asyncio
    async def test_stop_bot_destroying(self):
        mock_bot_response = MagicMock()
        mock_bot_response.status = BotStatus.DESTROYING.value
        service = _make_service()
        with patch.object(
            service, "get_bot", new_callable=AsyncMock, return_value=mock_bot_response
        ):
            with pytest.raises(ValueError, match="Cannot stop bot in"):
                await service.stop_bot(
                    tenant="test_tenant",
                    bot_uuid="BOT-001",
                    operator="user1",
                    request_id="test-request-id-12345678901234567890",
                )

    @pytest.mark.asyncio
    async def test_stop_bot_released(self):
        mock_bot_response = MagicMock()
        mock_bot_response.status = BotStatus.RELEASED.value
        service = _make_service()
        with patch.object(
            service, "get_bot", new_callable=AsyncMock, return_value=mock_bot_response
        ):
            with pytest.raises(ValueError, match="Cannot stop bot in"):
                await service.stop_bot(
                    tenant="test_tenant",
                    bot_uuid="BOT-001",
                    operator="user1",
                    request_id="test-request-id-12345678901234567890",
                )

    @pytest.mark.asyncio
    async def test_stop_bot_not_found(self):
        service = _make_service()
        with patch.object(
            service, "get_bot", new_callable=AsyncMock, return_value=None
        ):
            with pytest.raises(BotNotFoundError):
                await service.stop_bot(
                    tenant="test_tenant",
                    bot_uuid="BOT-NOT-FOUND",
                    operator="user1",
                    request_id="test-request-id-12345678901234567890",
                )

    @pytest.mark.asyncio
    async def test_stop_bot_with_auto_approve(self):
        mock_bot_response = MagicMock()
        mock_bot_response.id = 1
        mock_bot_response.bot_uuid = "BOT-001"
        mock_bot_response.status = BotStatus.ACTIVE.value
        mock_bot_response.model_dump = MagicMock(
            return_value={
                "id": 1,
                "bot_uuid": "BOT-001",
                "tenant": "test_tenant",
                "env": "dev",
                "domain": "default",
                "is_deleted": 0,
                "creator": "user1",
                "modifier": "user1",
                "status": BotStatus.ACTIVE.value,
                "name": "Test Bot",
                "description": None,
                "template_uuid": None,
                "replica_desired": 1,
                "replica_minimum": 1,
                "replica_maximum": 10,
                "auto_scaling_enabled": 0,
                "sla_grade": "standard",
                "gmt_create": "2024-01-01T00:00:00",
                "gmt_modified": "2024-01-01T00:00:00",
                "config": None,
            }
        )

        mock_publish = MagicMock()
        mock_publish.id = 789

        mock_publish_success = MagicMock()
        mock_publish_success.id = 789
        mock_publish_success.status = PublishStatus.SUCCESS.value

        mock_bot_repo = MagicMock()
        mock_updated_bot = MagicMock()
        mock_updated_bot.model_dump = MagicMock(
            return_value={
                "id": 1,
                "bot_uuid": "BOT-001",
                "tenant": "test_tenant",
                "env": "dev",
                "domain": "default",
                "is_deleted": 0,
                "creator": "user1",
                "modifier": "user1",
                "status": BotStatus.STOPPING.value,
                "name": "Test Bot",
                "description": None,
                "template_uuid": None,
                "replica_desired": 1,
                "replica_minimum": 1,
                "replica_maximum": 10,
                "auto_scaling_enabled": 0,
                "sla_grade": "standard",
                "gmt_create": "2024-01-01T00:00:00",
                "gmt_modified": "2024-01-01T00:00:00",
                "config": None,
            }
        )

        mock_bot_service = MagicMock()
        mock_bot_service.get_bot = AsyncMock(return_value=mock_updated_bot)
        mock_publish_service = MagicMock()
        mock_publish_service.create_publish = AsyncMock(return_value=mock_publish)
        mock_publish_service.get_publish = AsyncMock(return_value=mock_publish_success)
        mock_publish_service.approve_stage = AsyncMock()

        service = _make_service(
            bot_repo=mock_bot_repo,
            publish_service=mock_publish_service,
            bot_service=mock_bot_service,
        )
        with patch.object(
            service, "get_bot", new_callable=AsyncMock, return_value=mock_bot_response
        ):
            result = await service.stop_bot(
                tenant="test_tenant",
                bot_uuid="BOT-001",
                operator="user1",
                request_id="test-request-id-12345678901234567890",
                auto_approve_publish=True,
            )
        assert result is not None
        call_kwargs = mock_publish_service.create_publish.call_args.kwargs
        assert call_kwargs["config"].auto_approve is True

    @pytest.mark.asyncio
    async def test_destroy_bot_with_auto_approve(self):
        mock_bot_response = MagicMock()
        mock_bot_response.id = 1
        mock_bot_response.bot_uuid = "BOT-001"
        mock_bot_response.status = BotStatus.ACTIVE.value
        mock_bot_response.model_dump = MagicMock(
            return_value={
                "id": 1,
                "bot_uuid": "BOT-001",
                "tenant": "test_tenant",
                "env": "dev",
                "domain": "default",
                "is_deleted": 0,
                "creator": "user1",
                "modifier": "user1",
                "status": BotStatus.ACTIVE.value,
                "name": "Test Bot",
                "description": None,
                "template_uuid": None,
                "replica_desired": 1,
                "replica_minimum": 1,
                "replica_maximum": 10,
                "auto_scaling_enabled": 0,
                "sla_grade": "standard",
                "gmt_create": "2024-01-01T00:00:00",
                "gmt_modified": "2024-01-01T00:00:00",
                "config": None,
            }
        )

        mock_publish = MagicMock()
        mock_publish.id = 456

        mock_publish_success = MagicMock()
        mock_publish_success.id = 456
        mock_publish_success.status = PublishStatus.SUCCESS.value

        mock_bot_repo = MagicMock()
        mock_updated_bot = MagicMock()
        mock_updated_bot.model_dump = MagicMock(
            return_value={
                "id": 1,
                "bot_uuid": "BOT-001",
                "tenant": "test_tenant",
                "env": "dev",
                "domain": "default",
                "is_deleted": 0,
                "creator": "user1",
                "modifier": "user1",
                "status": BotStatus.DESTROYING.value,
                "name": "Test Bot",
                "description": None,
                "template_uuid": None,
                "replica_desired": 1,
                "replica_minimum": 1,
                "replica_maximum": 10,
                "auto_scaling_enabled": 0,
                "sla_grade": "standard",
                "gmt_create": "2024-01-01T00:00:00",
                "gmt_modified": "2024-01-01T00:00:00",
                "config": None,
            }
        )

        mock_bot_service = MagicMock()
        mock_bot_service.get_bot = AsyncMock(return_value=mock_updated_bot)
        mock_publish_service = MagicMock()
        mock_publish_service.create_publish = AsyncMock(return_value=mock_publish)
        mock_publish_service.get_publish = AsyncMock(return_value=mock_publish_success)
        mock_publish_service.approve_stage = AsyncMock()

        service = _make_service(
            bot_repo=mock_bot_repo,
            publish_service=mock_publish_service,
            bot_service=mock_bot_service,
        )
        with patch.object(
            service, "get_bot", new_callable=AsyncMock, return_value=mock_bot_response
        ):
            result = await service.destroy_bot(
                tenant="test_tenant",
                bot_uuid="BOT-001",
                operator="user1",
                request_id="test-request-id-12345678901234567890",
                auto_approve_publish=True,
            )
        assert result is not None
        call_kwargs = mock_publish_service.create_publish.call_args.kwargs
        assert call_kwargs["config"].auto_approve is True


class TestUpdateDevicesConfigMerge:
    """Tests for BotManagementService.update_devices with config merge."""

    @pytest.mark.asyncio
    async def test_update_devices_merges_config(self):
        mock_record = MagicMock()
        mock_record.id = 1
        mock_record.bot_uuid = "BOT-001"
        mock_record.status = "ACTIVE"
        mock_record.extra_config = {
            "share_policy": {"old": "value"},
            "deploy_config": None,
        }
        mock_record.model_dump = MagicMock(
            return_value={"id": 1, "bot_uuid": "BOT-001"}
        )

        mock_publish = MagicMock()
        mock_publish.id = 888

        mock_bot_response = MagicMock()
        mock_bot_response.id = 1
        mock_bot_response.status = "ACTIVE"
        mock_bot_response.model_dump = MagicMock(
            return_value={
                "id": 1,
                "bot_uuid": "BOT-001",
                "tenant": "test_tenant",
                "env": "dev",
                "domain": "default",
                "is_deleted": 0,
                "creator": "user1",
                "modifier": "user1",
                "status": "ACTIVE",
                "name": "Test Bot",
                "description": None,
                "template_uuid": None,
                "replica_desired": 1,
                "replica_minimum": 1,
                "replica_maximum": 10,
                "auto_scaling_enabled": 0,
                "sla_grade": "standard",
                "gmt_create": "2024-01-01T00:00:00",
                "gmt_modified": "2024-01-01T00:00:00",
            }
        )

        mock_bot_repo = MagicMock()
        mock_bot_repo.get_by_bot_uuid.return_value = mock_record
        mock_device_repo = MagicMock()
        mock_device = MagicMock()
        mock_device.device_uuid = "DEV-001"
        mock_device_repo.list_by_bot_id.return_value = [mock_device]
        mock_publish_service = MagicMock()
        mock_publish_service.create_publish = AsyncMock(return_value=mock_publish)
        service = _make_service(
            bot_repo=mock_bot_repo,
            device_repo=mock_device_repo,
            publish_service=mock_publish_service,
        )

        record_for_get = MagicMock()
        record_for_get.id = 1
        record_for_get.extra_config = {
            "share_policy": {"old": "value"},
            "deploy_config": None,
        }

        with (
            patch.object(
                service,
                "get_bot",
                new_callable=AsyncMock,
                return_value=mock_bot_response,
            ),
            patch.object(
                service, "_get_bot_record_by_uuid", return_value=record_for_get
            ),
        ):
            bot_config = BotConfig(
                share_policy={"new": "policy"},
                deploy_config=None,
                entity_id="",
                entity_type="",
                sla_grade="standard",
                callback_timeout_seconds=None,
                auto_approve_publish=False,
            )

            result = await service.update_devices(
                tenant="test_tenant",
                bot_uuid="BOT-001",
                operator="user1",
                request_id="test-request-id-12345678901234567890",
                device_uuids=["DEV-001"],
                config=bot_config,
            )

        assert result is not None
        assert result.publish_id == 888

    @pytest.mark.asyncio
    async def test_update_devices_merges_deploy_config(self):
        """update_devices with deploy_config override triggers field-level merge."""
        mock_record = MagicMock()
        mock_record.id = 1
        mock_record.bot_uuid = "BOT-001"
        mock_record.status = "ACTIVE"
        mock_record.extra_config = {
            "deploy_config": {
                "envs": {"EXISTING": "val"},
                "ttl_in_minutes": 60,
            },
        }
        mock_record.model_dump = MagicMock(
            return_value={"id": 1, "bot_uuid": "BOT-001"}
        )

        mock_publish = MagicMock()
        mock_publish.id = 888

        mock_bot_response = MagicMock()
        mock_bot_response.id = 1
        mock_bot_response.status = "ACTIVE"
        mock_bot_response.model_dump = MagicMock(
            return_value={
                "id": 1,
                "bot_uuid": "BOT-001",
                "tenant": "test_tenant",
                "env": "dev",
                "domain": "default",
                "is_deleted": 0,
                "creator": "user1",
                "modifier": "user1",
                "status": "ACTIVE",
                "name": "Test Bot",
                "description": None,
                "template_uuid": None,
                "replica_desired": 1,
                "replica_minimum": 1,
                "replica_maximum": 10,
                "auto_scaling_enabled": 0,
                "sla_grade": "standard",
                "gmt_create": "2024-01-01T00:00:00",
                "gmt_modified": "2024-01-01T00:00:00",
            }
        )

        mock_bot_repo = MagicMock()
        mock_bot_repo.get_by_bot_uuid.return_value = mock_record
        mock_device_repo = MagicMock()
        mock_device = MagicMock()
        mock_device.device_uuid = "DEV-001"
        mock_device_repo.list_by_bot_id.return_value = [mock_device]
        mock_publish_service = MagicMock()
        mock_publish_service.create_publish = AsyncMock(return_value=mock_publish)
        service = _make_service(
            bot_repo=mock_bot_repo,
            device_repo=mock_device_repo,
            publish_service=mock_publish_service,
        )

        record_for_get = MagicMock()
        record_for_get.id = 1
        record_for_get.extra_config = {
            "deploy_config": {
                "envs": {"EXISTING": "val"},
                "ttl_in_minutes": 60,
            },
        }

        with (
            patch.object(
                service,
                "get_bot",
                new_callable=AsyncMock,
                return_value=mock_bot_response,
            ),
            patch.object(
                service, "_get_bot_record_by_uuid", return_value=record_for_get
            ),
        ):
            bot_config = BotConfig(
                share_policy=None,
                deploy_config=DeployConfig(ttl_in_minutes=120, docker_image="img:v2"),
                entity_id="",
                entity_type="",
                sla_grade="standard",
                callback_timeout_seconds=None,
                auto_approve_publish=False,
            )

            result = await service.update_devices(
                tenant="test_tenant",
                bot_uuid="BOT-001",
                operator="user1",
                request_id="test-request-id-12345678901234567890",
                device_uuids=["DEV-001"],
                config=bot_config,
            )

        assert result is not None
        assert result.publish_id == 888
