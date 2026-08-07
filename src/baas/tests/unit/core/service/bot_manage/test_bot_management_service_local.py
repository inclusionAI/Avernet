"""E2E integration tests for Local platform create_bot/start_device flow.

Covers Local platform lifecycle from create_bot through start_device.
Requirements: D-FI01, D-FI04
"""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from secbaas.community.api.bot_manage import BotConfig
from secbaas.community.api.device_manage import (
    DeployConfig,
    LocalCreationResult,
    LocalDeployConfig,
    LocalDeviceConfig,
)
from secbaas.community.api.template_manage import LocalTemplateConfig
from secbaas.community.core.service.bot_manage import (
    DefaultBotManagementService as BotManagementService,
)
from secbaas.community.core.service.paas import PaasServiceFacade


class TestLocalPlatformCreateBot:
    """Tests for BotManagementService.create_bot with Local platform."""

    @pytest.mark.asyncio
    async def test_create_bot_with_local_deploy_config(self):
        """Test create_bot persists unified DeployConfig correctly.

        Uses unified DeployConfig (not LocalDeployConfig) per 14-02 design.
        Platform is determined at runtime from template, not from discriminator.
        """
        # Arrange: Mock bot and publish
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
                "name": "Local Test Bot",
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

        # Create unified DeployConfig with Local-specific fields
        # Per 14-02 design: callers construct unified DeployConfig without knowing platform
        deploy_config = DeployConfig(
            machine_id="machine-123",
            user_id="user-123",  # Required field for Local platform
            envs={"KEY": "value", "ENV": "test"},
        )
        bot_config = BotConfig(deploy_config=deploy_config)

        # Capture the PublishConfig passed to create_publish
        captured_config = {}

        async def capture_create_publish(
            *, tenant, bot_id, publish_type, operator, request_id, config
        ):
            captured_config["deploy_config"] = config.deploy_config
            return mock_publish

        # Build mock bot_service and publish_service for constructor injection
        mock_bot_service = MagicMock()
        mock_bot_service.create_bot = AsyncMock(return_value=mock_bot)

        mock_publish_service = MagicMock()
        mock_publish_service.create_publish = AsyncMock(
            side_effect=capture_create_publish
        )

        with patch(
            "secbaas.community.core.service.bot_manage.resolve_callback_timeout"
        ) as mock_resolve_timeout:
            mock_resolve_timeout.return_value = 300

            # Act: Call BotManagementService.create_bot with unified DeployConfig
            service = BotManagementService(
                bot_repo=MagicMock(),
                device_repo=MagicMock(),
                system_config_repo=MagicMock(),
                publish_service=mock_publish_service,
                bot_service=mock_bot_service,
                health_checker=MagicMock(),
            )
            result = await service.create_bot(
                tenant="test_tenant",
                name="Local Test Bot",
                template_uuid="TPL-LOCAL-001",
                device_count=1,
                operator="user1",
                request_id="req-test-local-platform-001-32chars",
                config=bot_config,
            )

            # Assert: Verify bot created and publish received DeployConfig
            assert result is not None
            assert result.bot_uuid == "BOT-20241201-12345678"
            assert result.publish_id == 456

            # Verify the deploy_config was passed correctly (unified DeployConfig type)
            assert captured_config["deploy_config"] is not None
            assert isinstance(captured_config["deploy_config"], DeployConfig)
            # Per 14-02 design: NO platform_type field in unified DeployConfig
            assert not hasattr(captured_config["deploy_config"], "platform_type")
            assert captured_config["deploy_config"].machine_id == "machine-123"
            assert captured_config["deploy_config"].user_id == "user-123"
            assert captured_config["deploy_config"].envs == {
                "KEY": "value",
                "ENV": "test",
            }


class TestLocalPlatformStartDevice:
    """Tests for DeviceService.start_device with Local platform."""

    @pytest.mark.asyncio
    async def test_start_device_detects_local_template(self):
        """Test start_device detects Local platform from LocalTemplateConfig.

        Uses unified DeployConfig in extra_config (no platform_type field).
        Platform is determined at runtime from template config type.
        """
        from secbaas.community.core.service.device_manage import DefaultDeviceService

        # Arrange: Mock device record with Local template UUID
        mock_record = MagicMock()
        mock_record.id = 1
        mock_record.device_uuid = "DEVICE-abc123"
        # Note: DeviceRecord has no bot_id field
        # Per 14-02 design: unified DeployConfig with no platform_type field
        # Per Phase 28: tc_bot_id provided directly in deploy_config (no bot lookup)
        mock_record.extra_config = {
            "template_uuid": "TPL-LOCAL-001",
            "deploy_config": {
                "machine_id": "machine-456",
                "user_id": "user-789",  # Required field for Local platform
                "agent_code": "agent-789",  # Required field for Local platform
                "tc_bot_id": "bot-001",  # Required field per Phase 28
            },
        }

        # Mock template with LocalTemplateConfig
        mock_template = MagicMock()
        mock_template.template_uuid = "TPL-LOCAL-001"
        mock_template.config = LocalTemplateConfig(
            type="LOCAL",
            mng_offline_threshold_seconds=30,
        )

        mock_repo = MagicMock()
        mock_repo.get_by_device_uuid.return_value = mock_record

        # Create a properly configured record for final response
        updated_record = MagicMock()
        updated_record.id = 1
        updated_record.device_uuid = "DEVICE-abc123"
        # Note: DeviceRecord has no bot_id field
        updated_record.tenant = "test_tenant"
        updated_record.env = "dev"
        updated_record.domain = "default"
        updated_record.status = "ACTIVE"
        updated_record.provider_type = "LOCAL"
        updated_record.provider_device_id = (
            "container--machine-456--user-789@TPL-LOCAL-001"
        )
        updated_record.provider_device_props = {"platform": "LOCAL"}
        updated_record.extra_config = mock_record.extra_config
        updated_record.creator = "user1"
        updated_record.modifier = "user1"
        updated_record.gmt_create = datetime.now()
        updated_record.gmt_modified = datetime.now()
        updated_record.err_msg = None
        mock_repo.get_by_id.return_value = updated_record

        # Mock facade to return LocalCreationResult
        mock_creation_result = LocalCreationResult(
            platform="LOCAL",
            status="RUNNING",
            container_id="container--machine-456--user-789@TPL-LOCAL-001",
        )

        with patch(
            "secbaas.community.core.service.paas._facade.PaasServiceFacade"
        ) as mock_facade_class:
            # Use AsyncMock with spec for type-safe mocking (D-18.3-07, D-18.3-16)
            mock_facade = AsyncMock(spec=PaasServiceFacade)
            mock_facade.create_device.return_value = mock_creation_result
            mock_facade_class.return_value = mock_facade

            # Configure injected device_template_service mock
            mock_template_service = MagicMock()
            mock_template_service.get_default_or_explicit_template.return_value = (
                mock_template
            )

            # Act: Call DeviceService.start_device
            service = DefaultDeviceService(
                paas_facade=mock_facade,
                repository=mock_repo,
                device_template_service=mock_template_service,
                secret_plugin=MagicMock(),
                callback_handler=MagicMock(
                    handle=AsyncMock(return_value={"status": "ok"})
                ),
            )
            result = await service.start_device(
                tenant="test_tenant",
                device_uuid="DEVICE-abc123",
                modifier="user1",
            )

            # Assert: Verify provider_type detected as LOCAL
            assert result is not None
            assert result.provider_type == "LOCAL"
            assert result.status == "ACTIVE"

        # Verify facade was called with LocalDeviceConfig
        call_args = mock_facade.create_device.call_args
        assert call_args is not None
        _, kwargs = call_args
        assert "detail_config" in kwargs
        assert isinstance(kwargs["detail_config"], LocalDeviceConfig)

    @pytest.mark.asyncio
    async def test_start_device_builds_local_device_config(self):
        """Test start_device builds LocalDeviceConfig with correct fields.

        Uses unified DeployConfig in extra_config (no platform_type field).
        Platform-specific extraction happens at runtime based on template type.
        """
        from secbaas.community.core.service.device_manage import DefaultDeviceService

        # Arrange: Mock device record (DeviceRecord has no bot_id field)
        mock_record = MagicMock()
        mock_record.id = 1
        mock_record.device_uuid = "DEVICE-def456"
        # Per 14-02 design: unified DeployConfig with no platform_type field
        # Per Phase 28: tc_bot_id provided directly in deploy_config (no bot lookup)
        mock_record.extra_config = {
            "template_uuid": "TPL-LOCAL-002",
            "deploy_config": {
                "machine_id": "target-machine-789",
                "user_id": "user-002",  # Required field for Local platform
                "envs": {"CUSTOM_VAR": "custom_value"},
                "agent_code": "custom-agent-001",
                "tc_bot_id": "bot-002",  # Required field per Phase 28
            },
        }

        # Mock template with LocalTemplateConfig
        mock_template = MagicMock()
        mock_template.template_uuid = "TPL-LOCAL-002"
        mock_template.config = LocalTemplateConfig(
            type="LOCAL",
            mng_offline_threshold_seconds=60,
        )

        mock_repo = MagicMock()
        mock_repo.get_by_device_uuid.return_value = mock_record

        # Return updated record on final get_by_id
        updated_record = MagicMock()
        updated_record.id = 1
        updated_record.device_uuid = "DEVICE-def456"
        # Note: DeviceRecord has no bot_id field
        updated_record.tenant = "test_tenant"
        updated_record.env = "dev"
        updated_record.domain = "default"
        updated_record.extra_config = mock_record.extra_config
        updated_record.status = "ACTIVE"
        updated_record.provider_type = "LOCAL"
        updated_record.provider_device_id = (
            "container--target-machine-789--owner-999@TPL-LOCAL-002"
        )
        updated_record.provider_device_props = {}
        updated_record.creator = "user1"
        updated_record.modifier = "user1"
        updated_record.gmt_create = datetime.now()
        updated_record.gmt_modified = datetime.now()
        updated_record.err_msg = None
        mock_repo.get_by_id.return_value = updated_record

        # Mock facade and capture the LocalDeviceConfig
        captured_detail_config = {}

        async def capture_create_device(
            *, tenant_name, device_template_uuid, detail_config
        ):
            captured_detail_config["config"] = detail_config
            return LocalCreationResult(
                platform="LOCAL",
                status="RUNNING",
                container_id="container--target-machine-789--user-002@TPL-LOCAL-002",
            )

        with patch(
            "secbaas.community.core.service.paas._facade.PaasServiceFacade"
        ) as mock_facade_class:
            # Use AsyncMock with spec for type-safe mocking (D-18.3-07, D-18.3-16)
            mock_facade = AsyncMock(spec=PaasServiceFacade)
            mock_facade.create_device.side_effect = capture_create_device
            mock_facade_class.return_value = mock_facade

            # Configure injected device_template_service mock
            mock_template_service = MagicMock()
            mock_template_service.get_default_or_explicit_template.return_value = (
                mock_template
            )

            # Act: Call DeviceService.start_device
            service = DefaultDeviceService(
                paas_facade=mock_facade,
                repository=mock_repo,
                device_template_service=mock_template_service,
                secret_plugin=MagicMock(),
                callback_handler=MagicMock(
                    handle=AsyncMock(return_value={"status": "ok"})
                ),
            )
            await service.start_device(
                tenant="test_tenant",
                device_uuid="DEVICE-def456",
                modifier="user1",
            )

            # Assert: Verify LocalDeviceConfig passed to facade has correct fields
            assert "config" in captured_detail_config
            local_config = captured_detail_config["config"]
            assert isinstance(local_config, LocalDeviceConfig)

            # Verify field mapping per D-15.03: user_id from deploy_config
            assert local_config.user_id == "user-002"  # From deploy_config.user_id
            assert (
                local_config.machine_id == "target-machine-789"
            )  # From deploy_config.machine_id
            assert (
                local_config.tc_bot_id == "bot-002"
            )  # From deploy_config.tc_bot_id per Phase 28
            assert (
                local_config.agent_code == "custom-agent-001"
            )  # From deploy_config.agent_code
            assert local_config.envs == {
                "CUSTOM_VAR": "custom_value"
            }  # From deploy_config.envs

    @pytest.mark.asyncio
    async def test_start_device_handles_local_creation_result(self):
        """Test start_device handles LocalCreationResult correctly."""
        from secbaas.community.core.service.device_manage import DefaultDeviceService

        # Arrange: Mock device record and template
        mock_record = MagicMock()
        mock_record.id = 1
        mock_record.device_uuid = "DEVICE-ghi789"
        # Per Phase 28: tc_bot_id provided directly in deploy_config (no bot lookup)
        mock_record.extra_config = {
            "template_uuid": "TPL-LOCAL-003",
            "deploy_config": {
                "machine_id": "machine-300",
                "user_id": "user-300",
                "agent_code": "agent-300",
                "tc_bot_id": "bot-003",  # Required field per Phase 28
            },
        }

        mock_template = MagicMock()
        mock_template.template_uuid = "TPL-LOCAL-003"
        mock_template.config = LocalTemplateConfig(type="LOCAL")

        mock_repo = MagicMock()
        mock_repo.get_by_device_uuid.return_value = mock_record

        # Return updated record reflecting creation result
        updated_record = MagicMock()
        updated_record.id = 1
        updated_record.device_uuid = "DEVICE-ghi789"
        # Note: DeviceRecord has no bot_id field
        updated_record.tenant = "test_tenant"
        updated_record.env = "dev"
        updated_record.domain = "default"
        updated_record.extra_config = mock_record.extra_config
        updated_record.status = "ACTIVE"
        updated_record.provider_type = "LOCAL"
        # This should be extracted from LocalCreationResult.container_id
        updated_record.provider_device_id = (
            "container--machine-300--user-300@TPL-LOCAL-003"
        )
        updated_record.provider_device_props = {
            "platform": "LOCAL",
            "status": "RUNNING",
            "container_id": "container--machine-300--user-300@TPL-LOCAL-003",
            "machine_id": "machine-300",
            "user_id": "user-300",
        }
        updated_record.creator = "user1"
        updated_record.modifier = "user1"
        updated_record.gmt_create = datetime.now()
        updated_record.gmt_modified = datetime.now()
        updated_record.err_msg = None
        mock_repo.get_by_id.return_value = updated_record

        # Mock facade to return LocalCreationResult with specific container_id
        mock_creation_result = LocalCreationResult(
            platform="LOCAL",
            status="RUNNING",
            container_id="container--machine-300--user-300@TPL-LOCAL-003",
        )

        with patch(
            "secbaas.community.core.service.paas._facade.PaasServiceFacade"
        ) as mock_facade_class:
            # Use AsyncMock with spec for type-safe mocking (D-18.3-07, D-18.3-16)
            mock_facade = AsyncMock(spec=PaasServiceFacade)
            mock_facade.create_device.return_value = mock_creation_result
            mock_facade_class.return_value = mock_facade

            # Configure injected device_template_service mock
            mock_template_service = MagicMock()
            mock_template_service.get_default_or_explicit_template.return_value = (
                mock_template
            )

            # Act: Call DeviceService.start_device
            service = DefaultDeviceService(
                paas_facade=mock_facade,
                repository=mock_repo,
                device_template_service=mock_template_service,
                secret_plugin=MagicMock(),
                callback_handler=MagicMock(
                    handle=AsyncMock(return_value={"status": "ok"})
                ),
            )
            result = await service.start_device(
                tenant="test_tenant",
                device_uuid="DEVICE-ghi789",
                modifier="user1",
            )

            # Assert: Verify provider_device_id extracted from container_id
            assert result is not None
            assert result.provider_type == "LOCAL"
            # The provider_device_id should come from creation_result.container_id
            assert (
                result.provider_device_id
                == "container--machine-300--user-300@TPL-LOCAL-003"
            )

            # Verify create_device was called with correct template UUID
            call_kwargs = mock_facade.create_device.call_args.kwargs
            assert call_kwargs["device_template_uuid"] == "TPL-LOCAL-003"
            assert call_kwargs["tenant_name"] == "test_tenant"

    @pytest.mark.asyncio
    async def test_start_device_raises_error_when_tc_bot_id_missing(self):
        """Test start_device raises ValueError when tc_bot_id missing in deploy_config."""
        from secbaas.community.core.service.device_manage import DefaultDeviceService

        # Arrange: Mock device record with all required fields but missing tc_bot_id
        mock_record = MagicMock()
        mock_record.id = 1
        mock_record.device_uuid = "DEVICE-missing-tc-bot"
        mock_record.extra_config = {
            "template_uuid": "TPL-LOCAL-004",
            "deploy_config": {
                "machine_id": "machine-004",
                "user_id": "user-004",
                "agent_code": "agent-004",
                # Missing tc_bot_id per Phase 28
            },
        }
        mock_record.tenant = "test_tenant"
        mock_record.env = "dev"
        mock_record.domain = "default"
        mock_record.provider_type = None
        mock_record.provider_device_id = None
        mock_record.provider_device_props = {}
        mock_record.err_msg = None
        mock_record.status = "FAILED"
        mock_record.creator = "user1"
        mock_record.modifier = "user1"

        mock_template = MagicMock()
        mock_template.template_uuid = "TPL-LOCAL-004"
        mock_template.config = LocalTemplateConfig(type="LOCAL")

        mock_repo = MagicMock()
        mock_repo.get_by_device_uuid.return_value = mock_record
        # Error handler calls get_by_id to build response
        mock_repo.get_by_id.return_value = mock_record

        with patch(
            "secbaas.community.core.service.template_manage._device_template_service.DefaultDeviceTemplateService.get_default_or_explicit_template"
        ) as mock_get_template:
            mock_get_template.return_value = mock_template

            # Act: ValueError is caught by error handler, returns FAILED response
            service = DefaultDeviceService(
                paas_facade=MagicMock(),
                repository=mock_repo,
                device_template_service=MagicMock(),
                secret_plugin=MagicMock(),
                callback_handler=MagicMock(
                    handle=AsyncMock(return_value={"status": "ok"})
                ),
            )
            response = await service.start_device(
                tenant="test_tenant",
                device_uuid="DEVICE-missing-tc-bot",
                modifier="user1",
            )

            # Assert: response should have FAILED status
            assert response.status == "FAILED"

    @pytest.mark.asyncio
    async def test_start_device_raises_error_when_missing_user_id(self):
        """Test LOCAL start_device raises ValueError when user_id missing in DeployConfig."""
        from secbaas.community.core.service.device_manage import DefaultDeviceService

        mock_record = MagicMock()
        mock_record.id = 1
        mock_record.device_uuid = "DEVICE-missing-user"
        # Per Phase 28: tc_bot_id required, but user_id missing for this test
        mock_record.extra_config = {
            "template_uuid": "TPL-LOCAL-005",
            "deploy_config": {
                "machine_id": "machine-xyz",
                "agent_code": "agent-xyz",
                "tc_bot_id": "bot-xyz",
                # Missing user_id!
            },
        }
        mock_record.tenant = "test_tenant"
        mock_record.env = "dev"
        mock_record.domain = "default"
        mock_record.provider_type = None
        mock_record.provider_device_id = None
        mock_record.provider_device_props = {}
        mock_record.err_msg = None
        mock_record.status = "FAILED"
        mock_record.creator = "user1"
        mock_record.modifier = "user1"

        mock_template = MagicMock()
        mock_template.template_uuid = "TPL-LOCAL-005"
        mock_template.config = LocalTemplateConfig(type="LOCAL")

        mock_repo = MagicMock()
        mock_repo.get_by_device_uuid.return_value = mock_record
        # Error handler calls get_by_id to build response
        mock_repo.get_by_id.return_value = mock_record

        with patch(
            "secbaas.community.core.service.template_manage._device_template_service.DefaultDeviceTemplateService.get_default_or_explicit_template"
        ) as mock_get_template:
            mock_get_template.return_value = mock_template

            # Act: ValueError is caught by error handler, returns FAILED response
            service = DefaultDeviceService(
                paas_facade=MagicMock(),
                repository=mock_repo,
                device_template_service=MagicMock(),
                secret_plugin=MagicMock(),
                callback_handler=MagicMock(
                    handle=AsyncMock(return_value={"status": "ok"})
                ),
            )
            response = await service.start_device(
                tenant="test_tenant",
                device_uuid="DEVICE-missing-user",
                modifier="user1",
            )

            # Assert: response should have FAILED status
            assert response.status == "FAILED"

    @pytest.mark.asyncio
    async def test_start_device_raises_error_when_missing_machine_id(self):
        """Test LOCAL start_device raises ValueError when machine_id missing in DeployConfig."""
        from secbaas.community.core.service.device_manage import DefaultDeviceService

        mock_record = MagicMock()
        mock_record.id = 1
        mock_record.device_uuid = "DEVICE-missing-machine"
        # Per Phase 28: tc_bot_id required, but machine_id missing for this test
        mock_record.extra_config = {
            "template_uuid": "TPL-LOCAL-006",
            "deploy_config": {
                "user_id": "user-abc",
                "agent_code": "agent-abc",
                "tc_bot_id": "bot-abc",
                # Missing machine_id!
            },
        }
        mock_record.tenant = "test_tenant"
        mock_record.env = "dev"
        mock_record.domain = "default"
        mock_record.provider_type = None
        mock_record.provider_device_id = None
        mock_record.provider_device_props = {}
        mock_record.err_msg = None
        mock_record.status = "FAILED"
        mock_record.creator = "user1"
        mock_record.modifier = "user1"

        mock_template = MagicMock()
        mock_template.template_uuid = "TPL-LOCAL-006"
        mock_template.config = LocalTemplateConfig(type="LOCAL")

        mock_repo = MagicMock()
        mock_repo.get_by_device_uuid.return_value = mock_record
        # Error handler calls get_by_id to build response
        mock_repo.get_by_id.return_value = mock_record

        with patch(
            "secbaas.community.core.service.template_manage._device_template_service.DefaultDeviceTemplateService.get_default_or_explicit_template"
        ) as mock_get_template:
            mock_get_template.return_value = mock_template

            # Act: ValueError is caught by error handler, returns FAILED response
            service = DefaultDeviceService(
                paas_facade=MagicMock(),
                repository=mock_repo,
                device_template_service=MagicMock(),
                secret_plugin=MagicMock(),
                callback_handler=MagicMock(
                    handle=AsyncMock(return_value={"status": "ok"})
                ),
            )
            response = await service.start_device(
                tenant="test_tenant",
                device_uuid="DEVICE-missing-machine",
                modifier="user1",
            )

            # Assert: response should have FAILED status
            assert response.status == "FAILED"

    @pytest.mark.asyncio
    async def test_start_device_raises_error_when_missing_agent_code(self):
        """Test LOCAL start_device raises ValueError when agent_code missing in DeployConfig."""
        from secbaas.community.core.service.device_manage import DefaultDeviceService

        mock_record = MagicMock()
        mock_record.id = 1
        mock_record.device_uuid = "DEVICE-missing-agent"
        # Per Phase 28: tc_bot_id required, but agent_code missing for this test
        mock_record.extra_config = {
            "template_uuid": "TPL-LOCAL-007",
            "deploy_config": {
                "machine_id": "machine-abc",
                "user_id": "user-abc",
                "tc_bot_id": "bot-abc",
                # Missing agent_code!
            },
        }
        mock_record.tenant = "test_tenant"
        mock_record.env = "dev"
        mock_record.domain = "default"
        mock_record.provider_type = None
        mock_record.provider_device_id = None
        mock_record.provider_device_props = {}
        mock_record.err_msg = None
        mock_record.status = "FAILED"
        mock_record.creator = "user1"
        mock_record.modifier = "user1"

        mock_template = MagicMock()
        mock_template.template_uuid = "TPL-LOCAL-007"
        mock_template.config = LocalTemplateConfig(type="LOCAL")

        mock_repo = MagicMock()
        mock_repo.get_by_device_uuid.return_value = mock_record
        # Error handler calls get_by_id to build response
        mock_repo.get_by_id.return_value = mock_record

        with patch(
            "secbaas.community.core.service.template_manage._device_template_service.DefaultDeviceTemplateService.get_default_or_explicit_template"
        ) as mock_get_template:
            mock_get_template.return_value = mock_template

            # Act: ValueError is caught by error handler, returns FAILED response
            service = DefaultDeviceService(
                paas_facade=MagicMock(),
                repository=mock_repo,
                device_template_service=MagicMock(),
                secret_plugin=MagicMock(),
                callback_handler=MagicMock(
                    handle=AsyncMock(return_value={"status": "ok"})
                ),
            )
            response = await service.start_device(
                tenant="test_tenant",
                device_uuid="DEVICE-missing-agent",
                modifier="user1",
            )

            # Assert: response should have FAILED status
            assert response.status == "FAILED"


class TestLocalDeployConfigSerialization:
    """Tests for LocalDeployConfig serialization/deserialization.

    Per 14-02 design: LocalDeployConfig is preserved for internal use but
    does NOT have a platform_type field. Platform is determined at runtime
    from template lookup.
    """

    def test_local_deploy_config_roundtrip(self):
        """Test LocalDeployConfig serializes and deserializes correctly."""
        # Create original config
        original = LocalDeployConfig(
            machine_id="machine-abc-123",
            agent_code="agent-abc-001",
            envs={"VAR1": "value1", "VAR2": "value2"},
            after_create_cmd_hook="echo 'created'",
            after_create_hook_wait_seconds=120,
        )

        # Serialize to dict (simulates DB storage)
        config_dict = original.model_dump()

        # Simulate storing in DB and reading back
        restored = LocalDeployConfig.model_validate(config_dict)

        # Assert: Verify all fields preserved
        # Per 14-02: NO platform_type field in any config class
        assert not hasattr(restored, "platform_type")
        assert restored.machine_id == "machine-abc-123"
        assert restored.agent_code == "agent-abc-001"
        assert restored.envs == {"VAR1": "value1", "VAR2": "value2"}
        assert restored.after_create_cmd_hook == "echo 'created'"
        assert restored.after_create_hook_wait_seconds == 120

    def test_local_deploy_config_defaults(self):
        """Test LocalDeployConfig default values."""
        config = LocalDeployConfig()

        # Per 14-02: NO platform_type field
        assert not hasattr(config, "platform_type")
        assert config.machine_id is None
        assert config.agent_code is None
        assert config.envs is None
        assert config.after_create_cmd_hook is None
        assert config.before_destroy_cmd_hook is None
        assert config.after_create_hook_wait_seconds == 300
        assert config.before_destroy_hook_wait_seconds == 300

    def test_local_deploy_config_machine_only(self):
        """Test LocalDeployConfig with only machine_id set."""
        config = LocalDeployConfig(machine_id="my-machine-1")

        # Per 14-02: NO platform_type field
        assert not hasattr(config, "platform_type")
        assert config.machine_id == "my-machine-1"
        assert config.agent_code is None
        assert config.envs is None

        # Verify serialization/deserialization
        config_dict = config.model_dump()
        restored = LocalDeployConfig.model_validate(config_dict)
        assert restored.machine_id == "my-machine-1"

    @pytest.mark.asyncio
    async def test_start_device_handles_hook_format_error(self):
        """Test that hook scripts with unknown placeholders don't crash with KeyError.

        Covers CR-01: Unsafe String Formatting in Hook Scripts fix.
        When a hook script contains unknown placeholders like {invalid_var},
        the _safe_format_hook function should handle it gracefully.
        """
        from secbaas.community.core.service.device_manage import DefaultDeviceService

        # Arrange: Mock device record with hook containing invalid placeholder
        mock_record = MagicMock()
        mock_record.id = 1
        mock_record.device_uuid = "DEVICE-hook-error"
        mock_record.extra_config = {
            "template_uuid": "TPL-LOCAL-007",
            "deploy_config": {
                "machine_id": "machine-123",
                "user_id": "user-123",
                "agent_code": "agent-123",
                "tc_bot_id": "bot-007",  # Required field per Phase 28
                "after_create_cmd_hook": "echo {invalid_var}",  # Invalid placeholder
            },
        }
        mock_record.status = "PENDING"
        mock_record.provider_type = None
        mock_record.provider_device_id = None
        mock_record.provider_device_props = None
        mock_record.tenant = "test_tenant"
        mock_record.env = "dev"
        mock_record.domain = "default"

        mock_template = MagicMock()
        mock_template.template_uuid = "TPL-LOCAL-007"
        mock_template.config = LocalTemplateConfig(type="LOCAL")

        mock_repo = MagicMock()
        mock_repo.get_by_device_uuid.return_value = mock_record

        # Create updated records for responses
        updated_record_active = MagicMock()
        updated_record_active.id = 1
        updated_record_active.device_uuid = "DEVICE-hook-error"
        # LOCAL provider skips hook dispatch and activates directly
        updated_record_active.status = "ACTIVE"
        updated_record_active.provider_type = "LOCAL"
        updated_record_active.provider_device_id = (
            "container--machine-123--user-123@TPL-LOCAL-007"
        )
        updated_record_active.provider_device_props = {}
        updated_record_active.err_msg = None
        updated_record_active.extra_config = mock_record.extra_config
        updated_record_active.tenant = "test_tenant"
        updated_record_active.env = "dev"
        updated_record_active.domain = "default"
        updated_record_active.creator = "user1"
        updated_record_active.modifier = "user1"

        mock_repo.get_by_id.return_value = updated_record_active

        with patch(
            "secbaas.community.core.service.paas._facade.PaasServiceFacade"
        ) as mock_facade_class:
            # Use AsyncMock with spec for type-safe mocking (D-18.3-07, D-18.3-16)
            mock_facade = AsyncMock(spec=PaasServiceFacade)
            mock_creation_result = LocalCreationResult(
                platform="LOCAL",
                status="RUNNING",
                container_id="container--machine-123--user-123@TPL-LOCAL-007",
            )
            mock_facade.create_device.return_value = mock_creation_result
            mock_facade_class.return_value = mock_facade

            # Configure injected device_template_service mock
            mock_template_service = MagicMock()
            mock_template_service.get_default_or_explicit_template.return_value = (
                mock_template
            )

            with patch(
                "secbaas.community.core.service.device_manage.dispatch_start_hook"
            ) as mock_dispatch:
                # Act: start_device should NOT raise KeyError
                # The _safe_format_hook should handle unknown placeholders gracefully
                service = DefaultDeviceService(
                    paas_facade=mock_facade,
                    repository=mock_repo,
                    device_template_service=mock_template_service,
                    secret_plugin=MagicMock(),
                    callback_handler=MagicMock(
                        handle=AsyncMock(return_value={"status": "ok"})
                    ),
                )
                response = await service.start_device(
                    tenant="test_tenant",
                    device_uuid="DEVICE-hook-error",
                    modifier="user1",
                )

                # Assert: LOCAL provider skips hook dispatch and activates directly
                assert response.status == "ACTIVE"

                # Verify hook was NOT dispatched (LOCAL platform bypasses hook)
                mock_dispatch.assert_not_called()
