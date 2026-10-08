"""Unit tests for BotHealthCheckerService core methods.

Covers the untested service methods: check_health_by_bot (full flow),
list_paas_device_by_bot, extend_ttl_by_bot, get_sandbox_info.
"""

from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from secbaas.community.api.health_check.bot import (
    BotAliveCheckResult,
    BotHealthCheckerConfig,
    BotHealthCheckerError,
    BotHealthCheckResult,
    DeviceAliveStatus,
    PaasDeviceInfo,
    PaasDeviceListResponse,
    SandboxNotFoundError,
    TTLExtendResult,
)
from secbaas.community.api.health_check.paas import (
    HealthCheckerStrategyResult,
    PaasHealthCheckerResult,
)
from secbaas.community.core.service.health_check.bot._service import (
    BotHealthCheckerService,
    _determine_alive_status,
)


def _make_service(
    mock_binding_repo: MagicMock | None = None,
    mock_device_repo: MagicMock | None = None,
    mock_paas_facade: MagicMock | None = None,
    mock_health_factory: MagicMock | None = None,
) -> BotHealthCheckerService:
    """Create a BotHealthCheckerService with mocked dependencies."""
    return BotHealthCheckerService(
        device_binding_repo=mock_binding_repo or MagicMock(),
        device_repo=mock_device_repo or MagicMock(),
        paas_facade=mock_paas_facade or MagicMock(),
        config=BotHealthCheckerConfig(),
        health_provider_factory=mock_health_factory or MagicMock(),
    )


class TestListPaasDeviceByBot:
    """Tests for BotHealthCheckerService.list_paas_device_by_bot."""

    @pytest.mark.asyncio
    async def test_personal_device(self) -> None:
        service = _make_service()
        service._device_binding_repo.get_bot_binding = MagicMock(
            return_value={
                "bot_type": "personal",
                "binding_id": 10,
                "active_engine": "openclaw",
            }
        )

        from secbaas.community.api.health_check.bot import DeviceProviderType

        mock_devices = [
            PaasDeviceInfo(
                paas_device_id="ARCA-SANDBOX-001@0",
                provider_type="ARCA",
                status="online",
                ttl_expiration_timestamp=1000000,
            )
        ]
        mock_provider = MagicMock()
        mock_provider.list_paas_device_by_bot = AsyncMock(return_value=mock_devices)
        service._device_providers = {
            DeviceProviderType.ARCA: mock_provider,
            DeviceProviderType.BAAS: MagicMock(),
        }

        result = await service.list_paas_device_by_bot(
            bot_id="bot1", entity_id="entity1", statuses=["online"], env="prod"
        )

        assert isinstance(result, PaasDeviceListResponse)
        assert result.bot_id == "bot1"
        assert result.bot_type == "personal"
        assert result.active_engine == "openclaw"
        assert len(result.paas_devices) == 1

    @pytest.mark.asyncio
    async def test_service_device(self) -> None:
        service = _make_service()
        service._device_binding_repo.get_bot_binding = MagicMock(
            return_value={
                "bot_type": "service",
                "binding_id": 20,
                "active_engine": "openclaw",
            }
        )

        from secbaas.community.api.health_check.bot import DeviceProviderType

        service._device_providers = {
            DeviceProviderType.ARCA: MagicMock(),
            DeviceProviderType.BAAS: MagicMock(),
        }
        mock_devices = [
            PaasDeviceInfo(
                paas_device_id="ARCA-SANDBOX-002@0",
                provider_type="ARCA",
                status="online",
                ttl_expiration_timestamp=1000000,
            )
        ]
        service._device_providers[
            DeviceProviderType.BAAS
        ].list_paas_device_by_bot = AsyncMock(return_value=mock_devices)

        result = await service.list_paas_device_by_bot(
            bot_id="bot1", entity_id="entity1", statuses=["online"], env="prod"
        )

        assert result.bot_type == "service"
        assert len(result.paas_devices) == 1

    @pytest.mark.asyncio
    async def test_missing_bot_type(self) -> None:
        service = _make_service()
        service._device_binding_repo.get_bot_binding = MagicMock(
            return_value={
                "bot_type": None,
                "binding_id": 10,
                "active_engine": "openclaw",
            }
        )

        with pytest.raises(BotHealthCheckerError, match="bot_type is missing"):
            await service.list_paas_device_by_bot(
                bot_id="bot1", entity_id="entity1", statuses=[], env="prod"
            )

    @pytest.mark.asyncio
    async def test_bot_not_found(self) -> None:
        service = _make_service()
        service._device_binding_repo.get_bot_binding = MagicMock(return_value=None)

        with pytest.raises(SandboxNotFoundError):
            await service.list_paas_device_by_bot(
                bot_id="nonexistent", entity_id="entity1", statuses=[], env="prod"
            )

    @pytest.mark.asyncio
    async def test_refresh_ttl_for_devices_without_ttl(self) -> None:
        """Devices without TTL should get refreshed."""
        service = _make_service()
        service._device_binding_repo.get_bot_binding = MagicMock(
            return_value={
                "bot_type": "personal",
                "binding_id": 10,
                "active_engine": "openclaw",
            }
        )

        from secbaas.community.api.health_check.bot import DeviceProviderType

        device_no_ttl = PaasDeviceInfo(
            paas_device_id="ARCA-SANDBOX-001@0",
            provider_type="ARCA",
            status="online",
            ttl_expiration_timestamp=None,
        )
        device_with_ttl = PaasDeviceInfo(
            paas_device_id="ARCA-SANDBOX-002@0",
            provider_type="ARCA",
            status="online",
            ttl_expiration_timestamp=1000000,
        )

        mock_provider = MagicMock()
        mock_provider.list_paas_device_by_bot = AsyncMock(
            return_value=[device_no_ttl, device_with_ttl]
        )
        mock_provider.refresh_device_ttl = AsyncMock(return_value=999999999)
        service._device_providers = {
            DeviceProviderType.ARCA: mock_provider,
            DeviceProviderType.BAAS: MagicMock(),
        }

        await service.list_paas_device_by_bot(
            bot_id="bot1", entity_id="entity1", statuses=[], env="prod"
        )

        mock_provider.refresh_device_ttl.assert_called_once_with(device_no_ttl)

    @pytest.mark.asyncio
    async def test_refresh_ttl_exception_logged(self) -> None:
        """Exception during TTL refresh should be logged, not propagated."""
        service = _make_service()
        service._device_binding_repo.get_bot_binding = MagicMock(
            return_value={
                "bot_type": "personal",
                "binding_id": 10,
                "active_engine": "openclaw",
            }
        )

        from secbaas.community.api.health_check.bot import DeviceProviderType

        device = PaasDeviceInfo(
            paas_device_id="ARCA-SANDBOX-001@0",
            provider_type="ARCA",
            status="online",
            ttl_expiration_timestamp=None,
        )

        mock_provider = MagicMock()
        mock_provider.list_paas_device_by_bot = AsyncMock(return_value=[device])
        mock_provider.refresh_device_ttl = AsyncMock(
            side_effect=RuntimeError("Arca down")
        )
        service._device_providers = {
            DeviceProviderType.ARCA: mock_provider,
            DeviceProviderType.BAAS: MagicMock(),
        }

        # Should not raise
        result = await service.list_paas_device_by_bot(
            bot_id="bot1", entity_id="entity1", statuses=[], env="prod"
        )

        assert len(result.paas_devices) == 1


class TestExtendTtlByBot:
    """Tests for BotHealthCheckerService.extend_ttl_by_bot."""

    @pytest.mark.asyncio
    async def test_personal_device_ttl_extend(self) -> None:
        service = _make_service()
        service._device_binding_repo.get_bot_binding = MagicMock(
            return_value={
                "bot_type": "personal",
                "binding_id": 10,
                "active_engine": "openclaw",
            }
        )

        from secbaas.community.api.health_check.bot import DeviceProviderType

        mock_provider = MagicMock()
        mock_provider.extend_ttl_by_bot = AsyncMock(
            return_value=TTLExtendResult(
                bot_id="bot1",
                bot_type="personal",
                total_devices=1,
                extended_count=1,
                skipped_count=0,
                failed_count=0,
                details=[],
            )
        )
        service._device_providers = {
            DeviceProviderType.ARCA: mock_provider,
            DeviceProviderType.BAAS: MagicMock(),
        }

        result = await service.extend_ttl_by_bot(
            bot_id="bot1", entity_id="entity1", env="prod"
        )

        assert result.extended_count == 1

    @pytest.mark.asyncio
    async def test_service_device_ttl_extend(self) -> None:
        service = _make_service()
        service._device_binding_repo.get_bot_binding = MagicMock(
            return_value={
                "bot_type": "service",
                "binding_id": 20,
                "active_engine": "openclaw",
            }
        )

        from secbaas.community.api.health_check.bot import DeviceProviderType

        mock_provider = MagicMock()
        mock_provider.extend_ttl_by_bot = AsyncMock(
            return_value=TTLExtendResult(
                bot_id="bot1",
                bot_type="service",
                total_devices=2,
                extended_count=2,
                skipped_count=0,
                failed_count=0,
                details=[],
            )
        )
        service._device_providers = {
            DeviceProviderType.ARCA: MagicMock(),
            DeviceProviderType.BAAS: mock_provider,
        }

        result = await service.extend_ttl_by_bot(
            bot_id="bot1", entity_id="entity1", env="prod"
        )

        assert result.extended_count == 2

    @pytest.mark.asyncio
    async def test_bot_not_found(self) -> None:
        service = _make_service()
        service._device_binding_repo.get_bot_binding = MagicMock(return_value=None)

        with pytest.raises(SandboxNotFoundError):
            await service.extend_ttl_by_bot(
                bot_id="nonexistent", entity_id="entity1", env="prod"
            )

    @pytest.mark.asyncio
    async def test_missing_bot_type(self) -> None:
        service = _make_service()
        service._device_binding_repo.get_bot_binding = MagicMock(
            return_value={"bot_type": None, "binding_id": 10}
        )

        with pytest.raises(BotHealthCheckerError, match="bot_type is missing"):
            await service.extend_ttl_by_bot(
                bot_id="bot1", entity_id="entity1", env="prod"
            )


class TestCheckHealthByBot:
    """Tests for BotHealthCheckerService.check_health_by_bot."""

    @pytest.mark.asyncio
    async def test_all_devices_healthy(self) -> None:
        service = _make_service()
        service._device_binding_repo.get_bot_binding = MagicMock(
            return_value={
                "bot_type": "personal",
                "binding_id": 10,
                "active_engine": "openclaw",
            }
        )

        from secbaas.community.api.health_check.bot import DeviceProviderType

        device = PaasDeviceInfo(
            paas_device_id="ARCA-SANDBOX-001@0",
            provider_type="ARCA",
            status="online",
        )
        mock_provider = MagicMock()
        mock_provider.list_paas_device_by_bot = AsyncMock(return_value=[device])
        service._device_providers = {
            DeviceProviderType.ARCA: mock_provider,
            DeviceProviderType.BAAS: MagicMock(),
        }

        mock_health_provider = MagicMock()
        mock_health_provider.check_health = AsyncMock(
            return_value=PaasHealthCheckerResult(
                paas_device_id="ARCA-SANDBOX-001@0",
                overall_healthy=True,
                checkers={
                    "engine": HealthCheckerStrategyResult(
                        healthy=True,
                        response=None,
                        error=None,
                        timeout=False,
                        duration_ms=10,
                    ),
                },
            )
        )
        service._health_provider_factory.get = MagicMock(
            return_value=mock_health_provider
        )

        result = await service.check_health_by_bot(
            bot_id="bot1", entity_id="entity1", statuses=["online"], env="prod"
        )

        assert isinstance(result, BotHealthCheckResult)
        assert result.overall_healthy is True
        assert result.healthy_count == 1
        assert result.unhealthy_count == 0
        assert len(result.failed_devices) == 0

    @pytest.mark.asyncio
    async def test_some_devices_unhealthy(self) -> None:
        service = _make_service()
        service._device_binding_repo.get_bot_binding = MagicMock(
            return_value={
                "bot_type": "personal",
                "binding_id": 10,
                "active_engine": "openclaw",
            }
        )

        from secbaas.community.api.health_check.bot import DeviceProviderType

        healthy_device = PaasDeviceInfo(
            paas_device_id="ARCA-SANDBOX-001@0",
            provider_type="ARCA",
            status="online",
        )
        unhealthy_device = PaasDeviceInfo(
            paas_device_id="ARCA-SANDBOX-002@0",
            provider_type="ARCA",
            status="online",
        )

        mock_provider = MagicMock()
        mock_provider.list_paas_device_by_bot = AsyncMock(
            return_value=[healthy_device, unhealthy_device]
        )
        service._device_providers = {
            DeviceProviderType.ARCA: mock_provider,
            DeviceProviderType.BAAS: MagicMock(),
        }

        async def mock_check_health(
            paas_device_id: str, **kwargs: object
        ) -> PaasHealthCheckerResult:
            if "001" in paas_device_id:
                return PaasHealthCheckerResult(
                    paas_device_id=paas_device_id,
                    overall_healthy=True,
                    checkers={
                        "engine": HealthCheckerStrategyResult(
                            healthy=True,
                            response=None,
                            error=None,
                            timeout=False,
                            duration_ms=5,
                        )
                    },
                )
            return PaasHealthCheckerResult(
                paas_device_id=paas_device_id,
                overall_healthy=False,
                checkers={
                    "engine": HealthCheckerStrategyResult(
                        healthy=False,
                        response=None,
                        error="Engine not running",
                        timeout=False,
                        duration_ms=5,
                    )
                },
            )

        mock_health_provider = MagicMock()
        mock_health_provider.check_health = AsyncMock(side_effect=mock_check_health)
        service._health_provider_factory.get = MagicMock(
            return_value=mock_health_provider
        )

        result = await service.check_health_by_bot(
            bot_id="bot1", entity_id="entity1", statuses=["online"], env="prod"
        )

        assert result.overall_healthy is False
        assert result.healthy_count == 1
        assert result.unhealthy_count == 1
        assert len(result.failed_devices) == 1

    @pytest.mark.asyncio
    async def test_no_devices_raises(self) -> None:
        service = _make_service()
        service._device_binding_repo.get_bot_binding = MagicMock(
            return_value={
                "bot_type": "personal",
                "binding_id": 10,
                "active_engine": "openclaw",
            }
        )

        from secbaas.community.api.health_check.bot import DeviceProviderType

        mock_provider = MagicMock()
        mock_provider.list_paas_device_by_bot = AsyncMock(return_value=[])
        service._device_providers = {
            DeviceProviderType.ARCA: mock_provider,
            DeviceProviderType.BAAS: MagicMock(),
        }

        with pytest.raises(SandboxNotFoundError, match="No devices found"):
            await service.check_health_by_bot(
                bot_id="bot1", entity_id="entity1", statuses=[], env="prod"
            )

    @pytest.mark.asyncio
    async def test_skip_device_with_empty_paas_id(self) -> None:
        """Devices with empty paas_device_id are skipped (not checked)."""
        service = _make_service()
        service._device_binding_repo.get_bot_binding = MagicMock(
            return_value={
                "bot_type": "personal",
                "binding_id": 10,
                "active_engine": "openclaw",
            }
        )

        from secbaas.community.api.health_check.bot import DeviceProviderType

        device = PaasDeviceInfo(
            paas_device_id="",
            provider_type="ARCA",
            status="unknown",
        )
        mock_provider = MagicMock()
        mock_provider.list_paas_device_by_bot = AsyncMock(return_value=[device])
        service._device_providers = {
            DeviceProviderType.ARCA: mock_provider,
            DeviceProviderType.BAAS: MagicMock(),
        }

        result = await service.check_health_by_bot(
            bot_id="bot1", entity_id="entity1", statuses=[], env="prod"
        )

        assert result.overall_healthy is False
        assert result.unhealthy_count == 1
        assert "paas_device_id is empty" in result.failed_devices[0].error_message

    @pytest.mark.asyncio
    async def test_skip_device_with_none_provider_type(self) -> None:
        """Devices with provider_type=None are skipped."""
        service = _make_service()
        service._device_binding_repo.get_bot_binding = MagicMock(
            return_value={
                "bot_type": "personal",
                "binding_id": 10,
                "active_engine": "openclaw",
            }
        )

        from secbaas.community.api.health_check.bot import DeviceProviderType

        device = PaasDeviceInfo(
            paas_device_id="ARCA-SANDBOX-001@0",
            provider_type=None,
            status="online",
        )
        mock_provider = MagicMock()
        mock_provider.list_paas_device_by_bot = AsyncMock(return_value=[device])
        service._device_providers = {
            DeviceProviderType.ARCA: mock_provider,
            DeviceProviderType.BAAS: MagicMock(),
        }

        result = await service.check_health_by_bot(
            bot_id="bot1", entity_id="entity1", statuses=[], env="prod"
        )

        assert result.overall_healthy is False
        assert "provider_type is None" in result.failed_devices[0].error_message

    @pytest.mark.asyncio
    async def test_checker_raises_exception(self) -> None:
        """Exception in health check is caught and recorded."""
        service = _make_service()
        service._device_binding_repo.get_bot_binding = MagicMock(
            return_value={
                "bot_type": "personal",
                "binding_id": 10,
                "active_engine": "openclaw",
            }
        )

        from secbaas.community.api.health_check.bot import DeviceProviderType

        device = PaasDeviceInfo(
            paas_device_id="ARCA-SANDBOX-001@0",
            provider_type="ARCA",
            status="online",
        )
        mock_provider = MagicMock()
        mock_provider.list_paas_device_by_bot = AsyncMock(return_value=[device])
        service._device_providers = {
            DeviceProviderType.ARCA: mock_provider,
            DeviceProviderType.BAAS: MagicMock(),
        }

        mock_health_provider = MagicMock()
        mock_health_provider.check_health = AsyncMock(
            side_effect=RuntimeError("Unexpected error in health check")
        )
        service._health_provider_factory.get = MagicMock(
            return_value=mock_health_provider
        )

        result = await service.check_health_by_bot(
            bot_id="bot1", entity_id="entity1", statuses=[], env="prod"
        )

        assert result.overall_healthy is False
        assert result.unhealthy_count == 1
        assert len(result.failed_devices) == 1
        assert "Unexpected error" in result.failed_devices[0].error_message


class TestGetSandboxInfo:
    """Tests for BotHealthCheckerService.get_sandbox_info."""

    @pytest.mark.asyncio
    async def test_found_in_binding_table(self) -> None:
        """Sandbox found in ac_entity_device_binding table."""
        service = _make_service()

        mock_binding = MagicMock()
        mock_binding.device_props = {"sandbox_id": "ARCA-SANDBOX-001@0"}
        mock_binding.status = "online"
        mock_binding.id = 42
        mock_binding.device_provider = "arca"
        mock_binding.env = "prod"

        service._device_binding_repo.get_binding_by_sandbox_id_like = MagicMock(
            return_value=mock_binding
        )

        result = await service.get_sandbox_info("ARCA-SANDBOX-001@0")

        assert result is not None
        assert result["sandbox_id"] == "ARCA-SANDBOX-001"
        assert result["source_table"] == "ac_binding"
        assert result["source_table_id"] == "42"
        assert result["device_provider"] == "arca"
        assert result["env"] == "prod"

    @pytest.mark.asyncio
    async def test_found_in_baas_device_table(self) -> None:
        """Sandbox found in baas_device table (fallback)."""
        service = _make_service()

        service._device_binding_repo.get_binding_by_sandbox_id_like = MagicMock(
            return_value=None
        )

        mock_device = MagicMock()
        mock_device.provider_device_id = "ARCA-SANDBOX-002@0"
        mock_device.status = "online"
        mock_device.id = 99
        mock_device.env = "prod"
        service._device_repo.get_by_provider_device_id_like = MagicMock(
            return_value=mock_device
        )

        result = await service.get_sandbox_info("ARCA-SANDBOX-002@0")

        assert result is not None
        assert result["sandbox_id"] == "ARCA-SANDBOX-002"
        assert result["source_table"] == "baas_device"
        assert result["device_provider"] == "baas"

    @pytest.mark.asyncio
    async def test_not_found(self) -> None:
        """Sandbox not found in either table returns None."""
        service = _make_service()
        service._device_binding_repo.get_binding_by_sandbox_id_like = MagicMock(
            return_value=None
        )
        service._device_repo.get_by_provider_device_id_like = MagicMock(
            return_value=None
        )

        result = await service.get_sandbox_info("nonexistent-sandbox")

        assert result is None

    @pytest.mark.asyncio
    async def test_strips_at_suffix(self) -> None:
        """@0 suffix should be stripped for lookup and results."""
        service = _make_service()

        mock_binding = MagicMock()
        mock_binding.device_props = {}
        mock_binding.status = "online"
        mock_binding.id = 42
        mock_binding.device_provider = "arca"
        mock_binding.env = "prod"

        service._device_binding_repo.get_binding_by_sandbox_id_like = MagicMock(
            return_value=mock_binding
        )

        result = await service.get_sandbox_info("ARCA-SANDBOX-001@0")

        # Should strip @0 suffix
        assert result["sandbox_id"] == "ARCA-SANDBOX-001"
        # Should query with prefix (without @0)
        service._device_binding_repo.get_binding_by_sandbox_id_like.assert_called_with(
            sandbox_id_prefix="ARCA-SANDBOX-001"
        )

    @pytest.mark.asyncio
    async def test_binding_device_props_none(self) -> None:
        """When binding has no device_props, paas_device_id defaults to sandbox_id."""
        service = _make_service()

        mock_binding = MagicMock()
        mock_binding.device_props = None
        mock_binding.status = "online"
        mock_binding.id = 42
        mock_binding.device_provider = "arca"
        mock_binding.env = "prod"

        service._device_binding_repo.get_binding_by_sandbox_id_like = MagicMock(
            return_value=mock_binding
        )

        result = await service.get_sandbox_info("ARCA-SANDBOX-001")

        assert result["sandbox_id"] == "ARCA-SANDBOX-001"

    @pytest.mark.asyncio
    async def test_exception_raises_bot_health_checker_error(self) -> None:
        """Repository exception raises BotHealthCheckerError."""
        service = _make_service()
        service._device_binding_repo.get_binding_by_sandbox_id_like = MagicMock(
            side_effect=RuntimeError("DB connection failed")
        )

        with pytest.raises(BotHealthCheckerError, match="Failed to get sandbox info"):
            await service.get_sandbox_info("ARCA-SANDBOX-001")


class TestCheckAliveByBotExtended:
    """Extended tests for check_alive_by_bot covering more edge cases."""

    @pytest.mark.asyncio
    async def test_exception_in_alive_check(self) -> None:
        """Exception during alive check is caught and recorded."""
        service = _make_service()
        service._device_binding_repo.get_bot_binding = MagicMock(
            return_value={
                "bot_type": "personal",
                "binding_id": 10,
                "active_engine": "openclaw",
            }
        )

        from secbaas.community.api.health_check.bot import DeviceProviderType

        device = PaasDeviceInfo(
            paas_device_id="ARCA-SANDBOX-001@0",
            provider_type="ARCA",
            status="online",
        )
        mock_provider = MagicMock()
        mock_provider.list_paas_device_by_bot = AsyncMock(return_value=[device])
        service._device_providers = {
            DeviceProviderType.ARCA: mock_provider,
            DeviceProviderType.BAAS: MagicMock(),
        }

        mock_health_provider = MagicMock()
        mock_health_provider.check_alive = AsyncMock(
            side_effect=RuntimeError("Alive check failed")
        )
        service._health_provider_factory.get = MagicMock(
            return_value=mock_health_provider
        )

        result = await service.check_alive_by_bot(
            bot_id="bot1", entity_id="entity1", minutes=1440, env="prod"
        )

        assert isinstance(result, BotAliveCheckResult)
        assert result.overall_alive is None
        assert result.error_count == 1
        assert "Alive check failed" in result.devices[0].error

    @pytest.mark.asyncio
    async def test_empty_paas_id_skipped(self) -> None:
        """Device with empty paas_device_id is recorded as skipped."""
        service = _make_service()
        service._device_binding_repo.get_bot_binding = MagicMock(
            return_value={
                "bot_type": "personal",
                "binding_id": 10,
                "active_engine": "openclaw",
            }
        )

        from secbaas.community.api.health_check.bot import DeviceProviderType

        device = PaasDeviceInfo(
            paas_device_id="",
            provider_type="ARCA",
            status="unknown",
        )
        mock_provider = MagicMock()
        mock_provider.list_paas_device_by_bot = AsyncMock(return_value=[device])
        service._device_providers = {
            DeviceProviderType.ARCA: mock_provider,
            DeviceProviderType.BAAS: MagicMock(),
        }

        result = await service.check_alive_by_bot(
            bot_id="bot1", entity_id="entity1", minutes=1440, env="prod"
        )

        assert result.overall_alive is None
        assert "paas_device_id is empty" in result.devices[0].error

    @pytest.mark.asyncio
    async def test_resource_alive_check_no_devices(self) -> None:
        """No devices found raises SandboxNotFoundError."""
        service = _make_service()
        service._device_binding_repo.get_bot_binding = MagicMock(
            return_value={
                "bot_type": "personal",
                "binding_id": 10,
                "active_engine": "openclaw",
            }
        )

        from secbaas.community.api.health_check.bot import DeviceProviderType

        mock_provider = MagicMock()
        mock_provider.list_paas_device_by_bot = AsyncMock(return_value=[])
        service._device_providers = {
            DeviceProviderType.ARCA: mock_provider,
            DeviceProviderType.BAAS: MagicMock(),
        }

        with pytest.raises(SandboxNotFoundError, match="No devices found"):
            await service.check_alive_by_bot(
                bot_id="bot1", entity_id="entity1", minutes=1440, env="prod"
            )

    @pytest.mark.asyncio
    async def test_alive_check_live_status(self) -> None:
        """Device with live status is counted as alive."""
        service = _make_service()
        service._device_binding_repo.get_bot_binding = MagicMock(
            return_value={
                "bot_type": "personal",
                "binding_id": 10,
                "active_engine": "openclaw",
            }
        )

        from secbaas.community.api.health_check.bot import DeviceProviderType

        device = PaasDeviceInfo(
            paas_device_id="ARCA-SANDBOX-001@0",
            provider_type="ARCA",
            status="online",
        )
        mock_provider = MagicMock()
        mock_provider.list_paas_device_by_bot = AsyncMock(return_value=[device])
        service._device_providers = {
            DeviceProviderType.ARCA: mock_provider,
            DeviceProviderType.BAAS: MagicMock(),
        }

        mock_health_provider = MagicMock()
        mock_health_provider.check_alive = AsyncMock(
            return_value=HealthCheckerStrategyResult(
                healthy=True,
                response={
                    "lastSessionTime": (
                        datetime.now() - timedelta(minutes=10)
                    ).strftime("%Y-%m-%d %H:%M:%S"),
                    "hasEnabledCron": False,
                },
                error=None,
                timeout=False,
                duration_ms=50,
            )
        )
        service._health_provider_factory.get = MagicMock(
            return_value=mock_health_provider
        )

        result = await service.check_alive_by_bot(
            bot_id="bot1", entity_id="entity1", minutes=1440, env="prod"
        )

        assert result.live_count == 1
        assert result.devices[0].status == DeviceAliveStatus.LIVE
        assert result.devices[0].last_session_time is not None

    @pytest.mark.asyncio
    async def test_alive_check_idle_status(self) -> None:
        """Device with idle status is counted as idle (not alive)."""
        service = _make_service()
        service._device_binding_repo.get_bot_binding = MagicMock(
            return_value={
                "bot_type": "personal",
                "binding_id": 10,
                "active_engine": "openclaw",
            }
        )

        from secbaas.community.api.health_check.bot import DeviceProviderType

        device = PaasDeviceInfo(
            paas_device_id="ARCA-SANDBOX-001@0",
            provider_type="ARCA",
            status="online",
        )
        mock_provider = MagicMock()
        mock_provider.list_paas_device_by_bot = AsyncMock(return_value=[device])
        service._device_providers = {
            DeviceProviderType.ARCA: mock_provider,
            DeviceProviderType.BAAS: MagicMock(),
        }

        mock_health_provider = MagicMock()
        mock_health_provider.check_alive = AsyncMock(
            return_value=HealthCheckerStrategyResult(
                healthy=True,
                response={
                    "lastSessionTime": (datetime.now() - timedelta(days=2)).strftime(
                        "%Y-%m-%d %H:%M:%S"
                    ),
                    "hasEnabledCron": False,
                },
                error=None,
                timeout=False,
                duration_ms=50,
            )
        )
        service._health_provider_factory.get = MagicMock(
            return_value=mock_health_provider
        )

        result = await service.check_alive_by_bot(
            bot_id="bot1", entity_id="entity1", minutes=1440, env="prod"
        )

        assert result.live_count == 0
        assert result.idle_count == 1
        assert result.devices[0].status == DeviceAliveStatus.IDLE

    @pytest.mark.asyncio
    async def test_service_bot_alive_check(self) -> None:
        """Service bot type routes to BAAS provider."""
        service = _make_service()
        service._device_binding_repo.get_bot_binding = MagicMock(
            return_value={
                "bot_type": "service",
                "binding_id": 20,
                "active_engine": "openclaw",
            }
        )

        from secbaas.community.api.health_check.bot import DeviceProviderType

        device = PaasDeviceInfo(
            paas_device_id="ARCA-SANDBOX-001@0",
            provider_type="ARCA",
            status="online",
        )
        mock_provider = MagicMock()
        mock_provider.list_paas_device_by_bot = AsyncMock(return_value=[device])
        service._device_providers = {
            DeviceProviderType.ARCA: MagicMock(),
            DeviceProviderType.BAAS: mock_provider,
        }

        mock_health_provider = MagicMock()
        mock_health_provider.check_alive = AsyncMock(
            return_value=HealthCheckerStrategyResult(
                healthy=True,
                response={
                    "lastSessionTime": (
                        datetime.now() - timedelta(minutes=10)
                    ).strftime("%Y-%m-%d %H:%M:%S"),
                    "hasEnabledCron": False,
                },
                error=None,
                timeout=False,
                duration_ms=50,
            )
        )
        service._health_provider_factory.get = MagicMock(
            return_value=mock_health_provider
        )

        result = await service.check_alive_by_bot(
            bot_id="bot1", entity_id="entity1", minutes=1440, env="prod"
        )

        assert result.bot_type == "service"
        assert result.live_count == 1

    @pytest.mark.asyncio
    async def test_alive_check_unsupported_strategy(self) -> None:
        """Unsupported alive check strategy returns unsupported result."""
        service = _make_service()
        service._device_binding_repo.get_bot_binding = MagicMock(
            return_value={
                "bot_type": "personal",
                "binding_id": 10,
                "active_engine": "unknown_engine",
            }
        )

        from secbaas.community.api.health_check.bot import DeviceProviderType

        device = PaasDeviceInfo(
            paas_device_id="ARCA-SANDBOX-001@0",
            provider_type="ARCA",
            status="online",
        )
        mock_provider = MagicMock()
        mock_provider.list_paas_device_by_bot = AsyncMock(return_value=[device])
        service._device_providers = {
            DeviceProviderType.ARCA: mock_provider,
            DeviceProviderType.BAAS: MagicMock(),
        }

        result = await service.check_alive_by_bot(
            bot_id="bot1", entity_id="entity1", minutes=1440, env="prod"
        )

        assert result.overall_alive is None
        assert result.unknown_count == 1
        assert result.devices[0].status == DeviceAliveStatus.UNKNOWN

    @pytest.mark.asyncio
    async def test_alive_check_exception_at_service_level(self) -> None:
        """Exception raised outside the checker loop is wrapped in BotHealthCheckerError."""
        service = _make_service()
        service._device_binding_repo.get_bot_binding = MagicMock(
            side_effect=RuntimeError("DB connection lost")
        )

        with pytest.raises(BotHealthCheckerError, match="Failed to check alive"):
            await service.check_alive_by_bot(
                bot_id="bot1", entity_id="entity1", minutes=1440, env="prod"
            )

    @pytest.mark.asyncio
    async def test_alive_check_missing_bot_type(self) -> None:
        """Missing bot_type raises BotHealthCheckerError."""
        service = _make_service()
        service._device_binding_repo.get_bot_binding = MagicMock(
            return_value={
                "bot_type": None,
                "binding_id": 10,
            }
        )

        with pytest.raises(BotHealthCheckerError, match="bot_type is missing"):
            await service.check_alive_by_bot(
                bot_id="bot1", entity_id="entity1", minutes=1440, env="prod"
            )


class TestListAllActiveBotDevice:
    """Tests for BotHealthCheckerService.list_all_active_bot_device."""

    @pytest.mark.asyncio
    async def test_personal_bot_type(self) -> None:
        service = _make_service()

        from secbaas.community.api.health_check.bot import BotDeviceInfo

        service._device_binding_repo.list_all_active_bot_device = MagicMock(
            return_value=(
                1,
                [
                    {
                        "bot_id": "b1",
                        "entity_id": "e1",
                        "binding_id": 10,
                        "bot_type": "personal",
                        "status": "ACTIVE",
                        "active_engine": "openclaw",
                    }
                ],
            )
        )

        total, items = await service.list_all_active_bot_device(
            page=1, page_size=20, bot_type="personal", env="prod"
        )

        assert total == 1
        assert len(items) == 1
        assert isinstance(items[0], BotDeviceInfo)
        service._device_binding_repo.list_all_active_bot_device.assert_called_with(
            page=1, page_size=20, env="prod", bot_type="personal"
        )

    @pytest.mark.asyncio
    async def test_service_bot_type(self) -> None:
        service = _make_service()

        service._device_binding_repo.list_all_active_bot_device = MagicMock(
            return_value=(
                2,
                [
                    {
                        "bot_id": "b1",
                        "entity_id": "e1",
                        "binding_id": 10,
                        "bot_type": "service",
                        "status": "ACTIVE",
                        "active_engine": "openclaw",
                    },
                    {
                        "bot_id": "b2",
                        "entity_id": "e2",
                        "binding_id": 20,
                        "bot_type": "service",
                        "status": "ACTIVE",
                        "active_engine": "aicoding",
                    },
                ],
            )
        )

        total, items = await service.list_all_active_bot_device(
            page=1, page_size=20, bot_type="service", env="prod"
        )

        assert total == 2
        assert len(items) == 2

    @pytest.mark.asyncio
    async def test_no_bot_type_all(self) -> None:
        service = _make_service()

        service._device_binding_repo.list_all_active_bot_device = MagicMock(
            return_value=(
                3,
                [
                    {
                        "bot_id": "b1",
                        "entity_id": "e1",
                        "binding_id": 10,
                        "bot_type": "personal",
                        "status": "ACTIVE",
                        "active_engine": "openclaw",
                    },
                    {
                        "bot_id": "b2",
                        "entity_id": "e2",
                        "binding_id": 20,
                        "bot_type": "service",
                        "status": "ACTIVE",
                        "active_engine": "openclaw",
                    },
                    {
                        "bot_id": "b3",
                        "entity_id": "e3",
                        "binding_id": None,
                        "bot_type": "personal",
                        "status": "ACTIVE",
                        "active_engine": None,
                    },
                ],
            )
        )

        total, items = await service.list_all_active_bot_device(
            page=1, page_size=20, bot_type=None, env="prod"
        )

        assert total == 3
        assert len(items) == 3

    @pytest.mark.asyncio
    async def test_exception_raises(self) -> None:
        service = _make_service()

        service._device_binding_repo.list_all_active_bot_device = MagicMock(
            side_effect=RuntimeError("query failed")
        )

        with pytest.raises(
            BotHealthCheckerError, match="Failed to list active bot devices"
        ):
            await service.list_all_active_bot_device(
                page=1, page_size=20, bot_type="personal", env="prod"
            )


class TestCheckHealthByBotExtended:
    """Extended tests for check_health_by_bot covering more edge cases."""

    @pytest.mark.asyncio
    async def test_service_bot_health_check(self) -> None:
        """Service bot type routes to BAAS provider."""
        service = _make_service()
        service._device_binding_repo.get_bot_binding = MagicMock(
            return_value={
                "bot_type": "service",
                "binding_id": 20,
                "active_engine": "openclaw",
            }
        )

        from secbaas.community.api.health_check.bot import DeviceProviderType

        device = PaasDeviceInfo(
            paas_device_id="ARCA-SANDBOX-001@0",
            provider_type="ARCA",
            status="online",
        )
        mock_provider = MagicMock()
        mock_provider.list_paas_device_by_bot = AsyncMock(return_value=[device])
        service._device_providers = {
            DeviceProviderType.ARCA: MagicMock(),
            DeviceProviderType.BAAS: mock_provider,
        }

        mock_health_provider = MagicMock()
        mock_health_provider.check_health = AsyncMock(
            return_value=PaasHealthCheckerResult(
                paas_device_id="ARCA-SANDBOX-001@0",
                overall_healthy=True,
                checkers={},
            )
        )
        service._health_provider_factory.get = MagicMock(
            return_value=mock_health_provider
        )

        result = await service.check_health_by_bot(
            bot_id="bot1", entity_id="entity1", statuses=["online"], env="prod"
        )

        assert result.bot_type == "service"
        assert result.overall_healthy is True

    @pytest.mark.asyncio
    async def test_missing_bot_type(self) -> None:
        """Missing bot_type raises BotHealthCheckerError."""
        service = _make_service()
        service._device_binding_repo.get_bot_binding = MagicMock(
            return_value={
                "bot_type": None,
                "binding_id": 10,
            }
        )

        with pytest.raises(BotHealthCheckerError, match="bot_type is missing"):
            await service.check_health_by_bot(
                bot_id="bot1", entity_id="entity1", statuses=[], env="prod"
            )

    @pytest.mark.asyncio
    async def test_exception_at_service_level(self) -> None:
        """Exception raised outside the checker loop is wrapped in BotHealthCheckerError."""
        service = _make_service()
        service._device_binding_repo.get_bot_binding = MagicMock(
            side_effect=RuntimeError("DB connection lost")
        )

        with pytest.raises(BotHealthCheckerError, match="Failed to check health"):
            await service.check_health_by_bot(
                bot_id="bot1", entity_id="entity1", statuses=[], env="prod"
            )


# ============ Desktop bot type early return ============


class TestDesktopBotAliveCheck:
    """desktop 类型无需 alive 检查，直接返回 overall_alive=None。"""

    @pytest.mark.asyncio
    async def test_desktop_returns_null_overall_alive(self) -> None:
        """desktop bot_type → overall_alive=None, devices=[], 所有计数=0。"""
        service = _make_service()
        service._device_binding_repo.get_bot_binding = MagicMock(
            return_value={
                "bot_type": "desktop",
                "binding_id": 10,
                "active_engine": "openclaw",
            }
        )

        result = await service.check_alive_by_bot(
            bot_id="desktop_bot_1", entity_id="entity1", minutes=1440, env="prod"
        )
        assert result.overall_alive is None
        assert result.devices == []
        assert result.live_count == 0
        assert result.idle_count == 0
        assert result.unknown_count == 0
        assert result.error_count == 0
        assert result.bot_type == "desktop"

    @pytest.mark.asyncio
    async def test_desktop_does_not_query_devices(self) -> None:
        """desktop 类型不应触发设备列表查询。"""
        service = _make_service()
        service._device_binding_repo.get_bot_binding = MagicMock(
            return_value={
                "bot_type": "desktop",
                "binding_id": 10,
                "active_engine": "openclaw",
            }
        )
        # 如果触发了设备查询，这些 mock 会被调用，测试就会失败
        service._device_providers = {}

        result = await service.check_alive_by_bot(
            bot_id="desktop_bot_1", entity_id="entity1", minutes=1440, env="prod"
        )
        assert result.overall_alive is None


# ============ Task 5.5: _determine_alive_status 辅助函数测试 ============


class TestDetermineAliveStatusArca:
    """_determine_alive_status Arca 分支覆盖（task 5.5）。"""

    def test_healthy_false_returns_error(self) -> None:
        """Arca: healthy=False → error。"""
        result = HealthCheckerStrategyResult(
            healthy=False,
            response={},
            error="cmd failed",
            timeout=False,
            duration_ms=10,
        )
        status, err = _determine_alive_status("ARCA", result, minutes=1440)
        assert status == DeviceAliveStatus.ERROR
        assert err == "cmd failed"

    def test_last_session_time_in_window_returns_live(self) -> None:
        """Arca: lastSessionTime 在窗口内 → live。"""
        now = datetime(2024, 6, 1, 12, 0, 0)
        session_time = (now - timedelta(minutes=10)).strftime("%Y-%m-%d %H:%M:%S")
        result = HealthCheckerStrategyResult(
            healthy=True,
            response={"lastSessionTime": session_time, "hasEnabledCron": False},
            error=None,
            timeout=False,
            duration_ms=10,
        )
        status, err = _determine_alive_status("ARCA", result, minutes=1440, now=now)
        assert status == DeviceAliveStatus.LIVE
        assert err is None

    def test_has_enabled_cron_returns_live(self) -> None:
        """Arca: hasEnabledCron=true → live（即使 lastSessionTime 为空）。"""
        result = HealthCheckerStrategyResult(
            healthy=True,
            response={"lastSessionTime": "", "hasEnabledCron": True},
            error=None,
            timeout=False,
            duration_ms=10,
        )
        status, err = _determine_alive_status("ARCA", result, minutes=1440)
        assert status == DeviceAliveStatus.LIVE
        assert err is None

    def test_last_session_time_out_of_window_no_cron_returns_idle(self) -> None:
        """Arca: lastSessionTime 超出窗口 + 无 cron → idle。"""
        now = datetime(2024, 6, 1, 12, 0, 0)
        session_time = (now - timedelta(days=2)).strftime("%Y-%m-%d %H:%M:%S")
        result = HealthCheckerStrategyResult(
            healthy=True,
            response={"lastSessionTime": session_time, "hasEnabledCron": False},
            error=None,
            timeout=False,
            duration_ms=10,
        )
        status, err = _determine_alive_status("ARCA", result, minutes=1440, now=now)
        assert status == DeviceAliveStatus.IDLE
        assert err is None

    def test_empty_session_no_cron_returns_unknown(self) -> None:
        """Arca: lastSessionTime 为空 + 无 cron → unknown。"""
        result = HealthCheckerStrategyResult(
            healthy=True,
            response={"lastSessionTime": "", "hasEnabledCron": False},
            error=None,
            timeout=False,
            duration_ms=10,
        )
        status, err = _determine_alive_status("ARCA", result, minutes=1440)
        assert status == DeviceAliveStatus.UNKNOWN
        assert err is None

    def test_invalid_session_time_no_cron_returns_unknown(self) -> None:
        """Arca: lastSessionTime 解析失败 + 无 cron → unknown。"""
        result = HealthCheckerStrategyResult(
            healthy=True,
            response={"lastSessionTime": "not-a-date", "hasEnabledCron": False},
            error=None,
            timeout=False,
            duration_ms=10,
        )
        status, err = _determine_alive_status("ARCA", result, minutes=1440)
        assert status == DeviceAliveStatus.UNKNOWN
        assert err is None

    def test_invalid_session_time_with_cron_returns_live(self) -> None:
        """Arca: lastSessionTime 解析失败但有 cron → live（cron 优先级更高）。"""
        result = HealthCheckerStrategyResult(
            healthy=True,
            response={"lastSessionTime": "not-a-date", "hasEnabledCron": True},
            error=None,
            timeout=False,
            duration_ms=10,
        )
        status, err = _determine_alive_status("ARCA", result, minutes=1440)
        assert status == DeviceAliveStatus.LIVE
        assert err is None

    def test_cron_overrides_idle(self) -> None:
        """Arca: lastSessionTime 超出窗口但有 cron → live。"""
        now = datetime(2024, 6, 1, 12, 0, 0)
        session_time = (now - timedelta(days=2)).strftime("%Y-%m-%d %H:%M:%S")
        result = HealthCheckerStrategyResult(
            healthy=True,
            response={"lastSessionTime": session_time, "hasEnabledCron": True},
            error=None,
            timeout=False,
            duration_ms=10,
        )
        status, err = _determine_alive_status("ARCA", result, minutes=1440, now=now)
        assert status == DeviceAliveStatus.LIVE
        assert err is None


class TestDetermineAliveStatusNonArca:
    """_determine_alive_status 非 Arca 分支覆盖（task 5.6）。"""

    def test_k8s_healthy_true_returns_live(self) -> None:
        """K8S: healthy=True → live。"""
        result = HealthCheckerStrategyResult(
            healthy=True, response={}, error=None, timeout=False, duration_ms=10
        )
        status, err = _determine_alive_status("K8S", result, minutes=1440)
        assert status == DeviceAliveStatus.LIVE
        assert err is None

    def test_k8s_healthy_false_returns_error(self) -> None:
        """K8S: healthy=False → error。"""
        result = HealthCheckerStrategyResult(
            healthy=False, response={}, error="unhealthy", timeout=False, duration_ms=10
        )
        status, err = _determine_alive_status("K8S", result, minutes=1440)
        assert status == DeviceAliveStatus.ERROR
        assert err == "unhealthy"

    def test_docker_healthy_true_returns_live(self) -> None:
        """Docker: healthy=True → live。"""
        result = HealthCheckerStrategyResult(
            healthy=True, response={}, error=None, timeout=False, duration_ms=10
        )
        status, err = _determine_alive_status("Docker", result, minutes=1440)
        assert status == DeviceAliveStatus.LIVE
        assert err is None

    def test_docker_healthy_false_returns_error(self) -> None:
        """Docker: healthy=False → error。"""
        result = HealthCheckerStrategyResult(
            healthy=False,
            response={},
            error="container dead",
            timeout=False,
            duration_ms=10,
        )
        status, err = _determine_alive_status("Docker", result, minutes=1440)
        assert status == DeviceAliveStatus.ERROR
        assert err == "container dead"

    def test_none_provider_type_treated_as_non_arca(self) -> None:
        """provider_type=None 走非 Arca 分支。"""
        result = HealthCheckerStrategyResult(
            healthy=True, response={}, error=None, timeout=False, duration_ms=10
        )
        status, err = _determine_alive_status(None, result, minutes=1440)
        assert status == DeviceAliveStatus.LIVE
        assert err is None

    def test_lowercase_arca_still_arca(self) -> None:
        """provider_type='arca'（小写）仍走 Arca 分支。"""
        now = datetime(2024, 6, 1, 12, 0, 0)
        session_time = (now - timedelta(minutes=10)).strftime("%Y-%m-%d %H:%M:%S")
        result = HealthCheckerStrategyResult(
            healthy=True,
            response={"lastSessionTime": session_time, "hasEnabledCron": False},
            error=None,
            timeout=False,
            duration_ms=10,
        )
        status, err = _determine_alive_status("arca", result, minutes=1440, now=now)
        assert status == DeviceAliveStatus.LIVE


# ============ Task 5.7: overall_alive 三值逻辑测试 ============


def _make_arca_device(
    paas_device_id: str = "ARCA-001@0",
    provider_type: str = "ARCA",
) -> PaasDeviceInfo:
    return PaasDeviceInfo(
        paas_device_id=paas_device_id,
        provider_type=provider_type,
        status="online",
    )


def _make_alive_result(
    status: DeviceAliveStatus,
    last_session_time: str | None = None,
    error: str | None = None,
) -> HealthCheckerStrategyResult:
    """构造一个 checker 返回，让 _determine_alive_status 产出指定 status。"""
    if status == DeviceAliveStatus.LIVE:
        now = datetime.now()
        return HealthCheckerStrategyResult(
            healthy=True,
            response={
                "lastSessionTime": (now - timedelta(minutes=10)).strftime(
                    "%Y-%m-%d %H:%M:%S"
                ),
                "hasEnabledCron": False,
            },
            error=None,
            timeout=False,
            duration_ms=10,
        )
    elif status == DeviceAliveStatus.IDLE:
        now = datetime.now()
        return HealthCheckerStrategyResult(
            healthy=True,
            response={
                "lastSessionTime": (now - timedelta(days=2)).strftime(
                    "%Y-%m-%d %H:%M:%S"
                ),
                "hasEnabledCron": False,
            },
            error=None,
            timeout=False,
            duration_ms=10,
        )
    elif status == DeviceAliveStatus.UNKNOWN:
        return HealthCheckerStrategyResult(
            healthy=True,
            response={"lastSessionTime": "", "hasEnabledCron": False},
            error=None,
            timeout=False,
            duration_ms=10,
        )
    else:  # ERROR
        return HealthCheckerStrategyResult(
            healthy=False,
            response={},
            error=error or "check failed",
            timeout=False,
            duration_ms=10,
        )


class TestOverallAliveThreeValued:
    """overall_alive 三值逻辑覆盖（task 5.7）。"""

    @pytest.mark.asyncio
    async def test_all_live_returns_true(self) -> None:
        """全部 live → overall_alive=True。"""
        service = _make_service()
        service._device_binding_repo.get_bot_binding = MagicMock(
            return_value={
                "bot_type": "personal",
                "binding_id": 1,
                "active_engine": "openclaw",
            }
        )
        from secbaas.community.api.health_check.bot import DeviceProviderType

        device = _make_arca_device()
        mock_provider = MagicMock()
        mock_provider.list_paas_device_by_bot = AsyncMock(return_value=[device])
        service._device_providers = {
            DeviceProviderType.ARCA: mock_provider,
            DeviceProviderType.BAAS: MagicMock(),
        }

        mock_hp = MagicMock()
        mock_hp.check_alive = AsyncMock(
            return_value=_make_alive_result(DeviceAliveStatus.LIVE)
        )
        service._health_provider_factory.get = MagicMock(return_value=mock_hp)

        result = await service.check_alive_by_bot(
            bot_id="bot1", entity_id="e1", minutes=1440, env="prod"
        )
        assert result.overall_alive is True

    @pytest.mark.asyncio
    async def test_all_idle_returns_false(self) -> None:
        """全部 idle → overall_alive=False。"""
        service = _make_service()
        service._device_binding_repo.get_bot_binding = MagicMock(
            return_value={
                "bot_type": "personal",
                "binding_id": 1,
                "active_engine": "openclaw",
            }
        )
        from secbaas.community.api.health_check.bot import DeviceProviderType

        device = _make_arca_device()
        mock_provider = MagicMock()
        mock_provider.list_paas_device_by_bot = AsyncMock(return_value=[device])
        service._device_providers = {
            DeviceProviderType.ARCA: mock_provider,
            DeviceProviderType.BAAS: MagicMock(),
        }

        mock_hp = MagicMock()
        mock_hp.check_alive = AsyncMock(
            return_value=_make_alive_result(DeviceAliveStatus.IDLE)
        )
        service._health_provider_factory.get = MagicMock(return_value=mock_hp)

        result = await service.check_alive_by_bot(
            bot_id="bot1", entity_id="e1", minutes=1440, env="prod"
        )
        assert result.overall_alive is False

    @pytest.mark.asyncio
    async def test_mixed_live_idle_returns_none(self) -> None:
        """混合 live + idle → overall_alive=None。"""
        service = _make_service()
        service._device_binding_repo.get_bot_binding = MagicMock(
            return_value={
                "bot_type": "personal",
                "binding_id": 1,
                "active_engine": "openclaw",
            }
        )
        from secbaas.community.api.health_check.bot import DeviceProviderType

        dev1 = _make_arca_device("ARCA-001@0")
        dev2 = _make_arca_device("ARCA-002@0")
        mock_provider = MagicMock()
        mock_provider.list_paas_device_by_bot = AsyncMock(return_value=[dev1, dev2])
        service._device_providers = {
            DeviceProviderType.ARCA: mock_provider,
            DeviceProviderType.BAAS: MagicMock(),
        }

        mock_hp = MagicMock()
        mock_hp.check_alive = AsyncMock(
            side_effect=[
                _make_alive_result(DeviceAliveStatus.LIVE),
                _make_alive_result(DeviceAliveStatus.IDLE),
            ]
        )
        service._health_provider_factory.get = MagicMock(return_value=mock_hp)

        result = await service.check_alive_by_bot(
            bot_id="bot1", entity_id="e1", minutes=1440, env="prod"
        )
        assert result.overall_alive is None

    @pytest.mark.asyncio
    async def test_contains_unknown_returns_none(self) -> None:
        """含 unknown → overall_alive=None。"""
        service = _make_service()
        service._device_binding_repo.get_bot_binding = MagicMock(
            return_value={
                "bot_type": "personal",
                "binding_id": 1,
                "active_engine": "openclaw",
            }
        )
        from secbaas.community.api.health_check.bot import DeviceProviderType

        dev1 = _make_arca_device("ARCA-001@0")
        dev2 = _make_arca_device("ARCA-002@0")
        mock_provider = MagicMock()
        mock_provider.list_paas_device_by_bot = AsyncMock(return_value=[dev1, dev2])
        service._device_providers = {
            DeviceProviderType.ARCA: mock_provider,
            DeviceProviderType.BAAS: MagicMock(),
        }

        mock_hp = MagicMock()
        mock_hp.check_alive = AsyncMock(
            side_effect=[
                _make_alive_result(DeviceAliveStatus.LIVE),
                _make_alive_result(DeviceAliveStatus.UNKNOWN),
            ]
        )
        service._health_provider_factory.get = MagicMock(return_value=mock_hp)

        result = await service.check_alive_by_bot(
            bot_id="bot1", entity_id="e1", minutes=1440, env="prod"
        )
        assert result.overall_alive is None

    @pytest.mark.asyncio
    async def test_contains_error_returns_none(self) -> None:
        """含 error → overall_alive=None。"""
        service = _make_service()
        service._device_binding_repo.get_bot_binding = MagicMock(
            return_value={
                "bot_type": "personal",
                "binding_id": 1,
                "active_engine": "openclaw",
            }
        )
        from secbaas.community.api.health_check.bot import DeviceProviderType

        dev1 = _make_arca_device("ARCA-001@0")
        dev2 = _make_arca_device("ARCA-002@0")
        mock_provider = MagicMock()
        mock_provider.list_paas_device_by_bot = AsyncMock(return_value=[dev1, dev2])
        service._device_providers = {
            DeviceProviderType.ARCA: mock_provider,
            DeviceProviderType.BAAS: MagicMock(),
        }

        mock_hp = MagicMock()
        mock_hp.check_alive = AsyncMock(
            side_effect=[
                _make_alive_result(DeviceAliveStatus.LIVE),
                _make_alive_result(DeviceAliveStatus.ERROR),
            ]
        )
        service._health_provider_factory.get = MagicMock(return_value=mock_hp)

        result = await service.check_alive_by_bot(
            bot_id="bot1", entity_id="e1", minutes=1440, env="prod"
        )
        assert result.overall_alive is None


# ============ Task 5.8: unknown_count 和 error_count 统计测试 ============


class TestCountStatistics:
    """unknown_count 和 error_count 统计正确性（task 5.8）。"""

    @pytest.mark.asyncio
    async def test_error_count_from_checker_exception(self) -> None:
        """checker 抛异常 → error_count +1。"""
        service = _make_service()
        service._device_binding_repo.get_bot_binding = MagicMock(
            return_value={
                "bot_type": "personal",
                "binding_id": 1,
                "active_engine": "openclaw",
            }
        )
        from secbaas.community.api.health_check.bot import DeviceProviderType

        device = _make_arca_device()
        mock_provider = MagicMock()
        mock_provider.list_paas_device_by_bot = AsyncMock(return_value=[device])
        service._device_providers = {
            DeviceProviderType.ARCA: mock_provider,
            DeviceProviderType.BAAS: MagicMock(),
        }

        mock_hp = MagicMock()
        mock_hp.check_alive = AsyncMock(side_effect=RuntimeError("boom"))
        service._health_provider_factory.get = MagicMock(return_value=mock_hp)

        result = await service.check_alive_by_bot(
            bot_id="bot1", entity_id="e1", minutes=1440, env="prod"
        )
        assert result.error_count == 1
        assert result.live_count == 0
        assert result.idle_count == 0
        assert result.unknown_count == 0

    @pytest.mark.asyncio
    async def test_error_count_from_healthy_false(self) -> None:
        """checker 返回 healthy=False → error_count +1。"""
        service = _make_service()
        service._device_binding_repo.get_bot_binding = MagicMock(
            return_value={
                "bot_type": "personal",
                "binding_id": 1,
                "active_engine": "openclaw",
            }
        )
        from secbaas.community.api.health_check.bot import DeviceProviderType

        device = _make_arca_device()
        mock_provider = MagicMock()
        mock_provider.list_paas_device_by_bot = AsyncMock(return_value=[device])
        service._device_providers = {
            DeviceProviderType.ARCA: mock_provider,
            DeviceProviderType.BAAS: MagicMock(),
        }

        mock_hp = MagicMock()
        mock_hp.check_alive = AsyncMock(
            return_value=_make_alive_result(DeviceAliveStatus.ERROR)
        )
        service._health_provider_factory.get = MagicMock(return_value=mock_hp)

        result = await service.check_alive_by_bot(
            bot_id="bot1", entity_id="e1", minutes=1440, env="prod"
        )
        assert result.error_count == 1

    @pytest.mark.asyncio
    async def test_unknown_count_from_unsupported_strategy(self) -> None:
        """策略返回空列表 → unknown_count +1。"""
        service = _make_service()
        service._device_binding_repo.get_bot_binding = MagicMock(
            return_value={
                "bot_type": "personal",
                "binding_id": 1,
                "active_engine": "unknown_engine",
            }
        )
        from secbaas.community.api.health_check.bot import DeviceProviderType

        device = _make_arca_device()
        mock_provider = MagicMock()
        mock_provider.list_paas_device_by_bot = AsyncMock(return_value=[device])
        service._device_providers = {
            DeviceProviderType.ARCA: mock_provider,
            DeviceProviderType.BAAS: MagicMock(),
        }

        result = await service.check_alive_by_bot(
            bot_id="bot1", entity_id="e1", minutes=1440, env="prod"
        )
        assert result.unknown_count == 1
        assert result.error_count == 0

    @pytest.mark.asyncio
    async def test_mixed_counts(self) -> None:
        """多设备混合：1 live + 1 idle + 1 unknown + 1 error。"""
        service = _make_service()
        service._device_binding_repo.get_bot_binding = MagicMock(
            return_value={
                "bot_type": "personal",
                "binding_id": 1,
                "active_engine": "openclaw",
            }
        )
        from secbaas.community.api.health_check.bot import DeviceProviderType

        dev1 = _make_arca_device("ARCA-001@0")
        dev2 = _make_arca_device("ARCA-002@0")
        dev3 = _make_arca_device("ARCA-003@0")
        dev4 = _make_arca_device("ARCA-004@0")
        mock_provider = MagicMock()
        mock_provider.list_paas_device_by_bot = AsyncMock(
            return_value=[dev1, dev2, dev3, dev4]
        )
        service._device_providers = {
            DeviceProviderType.ARCA: mock_provider,
            DeviceProviderType.BAAS: MagicMock(),
        }

        mock_hp = MagicMock()
        mock_hp.check_alive = AsyncMock(
            side_effect=[
                _make_alive_result(DeviceAliveStatus.LIVE),
                _make_alive_result(DeviceAliveStatus.IDLE),
                _make_alive_result(DeviceAliveStatus.UNKNOWN),
                _make_alive_result(DeviceAliveStatus.ERROR),
            ]
        )
        service._health_provider_factory.get = MagicMock(return_value=mock_hp)

        result = await service.check_alive_by_bot(
            bot_id="bot1", entity_id="e1", minutes=1440, env="prod"
        )
        assert result.live_count == 1
        assert result.idle_count == 1
        assert result.unknown_count == 1
        assert result.error_count == 1
        assert result.overall_alive is None


# ============ Task 5.9: missing identity 判定为 error ============


class TestMissingIdentityAsError:
    """missing identity 判定为 error 而非 unknown（task 5.9）。"""

    @pytest.mark.asyncio
    async def test_empty_paas_device_id_is_error(self) -> None:
        """paas_device_id 为空 → status=ERROR。"""
        service = _make_service()
        service._device_binding_repo.get_bot_binding = MagicMock(
            return_value={
                "bot_type": "personal",
                "binding_id": 1,
                "active_engine": "openclaw",
            }
        )
        from secbaas.community.api.health_check.bot import DeviceProviderType

        device = PaasDeviceInfo(
            paas_device_id="", provider_type="ARCA", status="online"
        )
        mock_provider = MagicMock()
        mock_provider.list_paas_device_by_bot = AsyncMock(return_value=[device])
        service._device_providers = {
            DeviceProviderType.ARCA: mock_provider,
            DeviceProviderType.BAAS: MagicMock(),
        }

        result = await service.check_alive_by_bot(
            bot_id="bot1", entity_id="e1", minutes=1440, env="prod"
        )
        assert result.devices[0].status == DeviceAliveStatus.ERROR
        assert "paas_device_id is empty" in result.devices[0].error
        assert result.error_count == 1

    @pytest.mark.asyncio
    async def test_none_provider_type_is_error(self) -> None:
        """provider_type 为 None → status=ERROR。"""
        service = _make_service()
        service._device_binding_repo.get_bot_binding = MagicMock(
            return_value={
                "bot_type": "personal",
                "binding_id": 1,
                "active_engine": "openclaw",
            }
        )
        from secbaas.community.api.health_check.bot import DeviceProviderType

        device = PaasDeviceInfo(
            paas_device_id="ARCA-001@0", provider_type=None, status="online"
        )
        mock_provider = MagicMock()
        mock_provider.list_paas_device_by_bot = AsyncMock(return_value=[device])
        service._device_providers = {
            DeviceProviderType.ARCA: mock_provider,
            DeviceProviderType.BAAS: MagicMock(),
        }

        result = await service.check_alive_by_bot(
            bot_id="bot1", entity_id="e1", minutes=1440, env="prod"
        )
        assert result.devices[0].status == DeviceAliveStatus.ERROR
        assert "provider_type is None" in result.devices[0].error
        assert result.error_count == 1
