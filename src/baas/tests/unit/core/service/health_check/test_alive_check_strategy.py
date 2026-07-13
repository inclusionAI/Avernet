"""Unit tests for check_alive_by_bot strategy resolution and unsupported handling."""

from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from secbaas.community.api.health_check.bot import (
    BotAliveCheckResult,
    BotHealthCheckerConfig,
    DeviceAliveStatus,
)
from secbaas.community.api.health_check.paas import HealthCheckerStrategyResult
from secbaas.community.core.service.health_check.bot._service import (
    BotHealthCheckerService,
)


def _make_service() -> BotHealthCheckerService:
    """Create a BotHealthCheckerService with mocked dependencies."""
    mock_binding_repo = MagicMock()
    mock_device_repo = MagicMock()
    mock_paas_facade = MagicMock()
    mock_health_factory = MagicMock()
    return BotHealthCheckerService(
        device_binding_repo=mock_binding_repo,
        device_repo=mock_device_repo,
        paas_facade=mock_paas_facade,
        config=BotHealthCheckerConfig(),
        health_provider_factory=mock_health_factory,
    )


class TestCheckAliveByBotUnsupported:
    """Tests for check_alive_by_bot handling unsupported engine/provider combos."""

    @pytest.mark.asyncio
    async def test_sigma_device_returns_unsupported(self) -> None:
        """SIGMA devices should return status=UNKNOWN."""
        service = _make_service()

        # Mock binding
        service._device_binding_repo = MagicMock()
        service._device_binding_repo.get_bot_binding = MagicMock(
            return_value={
                "bot_type": "service",
                "binding_id": 1,
                "active_engine": "openclaw",
            }
        )

        # Mock device list with a SIGMA device
        from secbaas.community.api.health_check.bot import PaasDeviceInfo

        sigma_device = PaasDeviceInfo(
            paas_device_id="SIGMA-DEVICE-001",
            provider_type="SIGMA",
            status="online",
        )

        mock_provider = MagicMock()
        mock_provider.list_paas_device_by_bot = AsyncMock(return_value=[sigma_device])
        service._device_providers = {
            MagicMock(value="arca"): mock_provider,
            MagicMock(value="baas"): mock_provider,
        }
        # Use enum properly
        from secbaas.community.api.health_check.bot import DeviceProviderType

        service._device_providers = {
            DeviceProviderType.ARCA: mock_provider,
            DeviceProviderType.BAAS: mock_provider,
        }

        result = await service.check_alive_by_bot(
            bot_id="bot1",
            entity_id="entity1",
            minutes=1440,
            statuses=["online"],
            env="prod",
        )

        assert isinstance(result, BotAliveCheckResult)
        assert len(result.devices) == 1
        device_info = result.devices[0]
        assert device_info.status == DeviceAliveStatus.UNKNOWN
        assert "not supported" in device_info.error
        assert result.unknown_count == 1
        assert result.overall_alive is None

    @pytest.mark.asyncio
    async def test_local_device_returns_unsupported(self) -> None:
        """LOCAL devices should return status=UNKNOWN."""
        service = _make_service()

        service._device_binding_repo = MagicMock()
        service._device_binding_repo.get_bot_binding = MagicMock(
            return_value={
                "bot_type": "personal",
                "binding_id": 1,
                "active_engine": "openclaw",
            }
        )

        from secbaas.community.api.health_check.bot import PaasDeviceInfo

        local_device = PaasDeviceInfo(
            paas_device_id="LOCAL-DEVICE-001",
            provider_type="local",
            status="online",
        )

        mock_provider = MagicMock()
        mock_provider.list_paas_device_by_bot = AsyncMock(return_value=[local_device])
        from secbaas.community.api.health_check.bot import DeviceProviderType

        service._device_providers = {
            DeviceProviderType.ARCA: mock_provider,
            DeviceProviderType.BAAS: mock_provider,
        }

        result = await service.check_alive_by_bot(
            bot_id="bot1",
            entity_id="entity1",
            minutes=1440,
            env="prod",
        )

        assert len(result.devices) == 1
        assert result.devices[0].status == DeviceAliveStatus.UNKNOWN
        assert result.unknown_count == 1

    @pytest.mark.asyncio
    async def test_arca_openclaw_normal_check(self) -> None:
        """ARCA + openclaw should perform normal alive check (not unsupported)."""
        service = _make_service()

        service._device_binding_repo = MagicMock()
        service._device_binding_repo.get_bot_binding = MagicMock(
            return_value={
                "bot_type": "personal",
                "binding_id": 1,
                "active_engine": "openclaw",
            }
        )

        from secbaas.community.api.health_check.bot import (
            DeviceProviderType,
            PaasDeviceInfo,
        )

        arca_device = PaasDeviceInfo(
            paas_device_id="ARCA-SANDBOX-001@0",
            provider_type="ARCA",
            status="online",
        )

        mock_provider = MagicMock()
        mock_provider.list_paas_device_by_bot = AsyncMock(return_value=[arca_device])
        service._device_providers = {
            DeviceProviderType.ARCA: mock_provider,
            DeviceProviderType.BAAS: mock_provider,
        }

        # Mock health provider
        mock_arca_provider = MagicMock()
        mock_arca_provider.check_alive = AsyncMock(
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
                duration_ms=100,
            )
        )
        service._health_provider_factory = MagicMock()
        service._health_provider_factory.get = MagicMock(
            return_value=mock_arca_provider
        )

        result = await service.check_alive_by_bot(
            bot_id="bot1",
            entity_id="entity1",
            minutes=1440,
            env="prod",
        )

        assert len(result.devices) == 1
        assert result.devices[0].status == DeviceAliveStatus.LIVE
        assert result.unknown_count == 0
        # Verify check_alive was called with checkers
        mock_arca_provider.check_alive.assert_called_once()
        call_kwargs = mock_arca_provider.check_alive.call_args
        assert call_kwargs.kwargs.get("checkers") == ["active"] or (
            len(call_kwargs.args) >= 3 and call_kwargs.args[2] == ["active"]
        )

    @pytest.mark.asyncio
    async def test_arca_unknown_engine_unsupported(self) -> None:
        """ARCA + unknown engine should return unsupported (no alive checkers configured)."""
        service = _make_service()

        service._device_binding_repo = MagicMock()
        service._device_binding_repo.get_bot_binding = MagicMock(
            return_value={
                "bot_type": "personal",
                "binding_id": 1,
                "active_engine": "unknown_engine",
            }
        )

        from secbaas.community.api.health_check.bot import (
            DeviceProviderType,
            PaasDeviceInfo,
        )

        arca_device = PaasDeviceInfo(
            paas_device_id="ARCA-SANDBOX-002@0",
            provider_type="ARCA",
            status="online",
        )

        mock_provider = MagicMock()
        mock_provider.list_paas_device_by_bot = AsyncMock(return_value=[arca_device])
        service._device_providers = {
            DeviceProviderType.ARCA: mock_provider,
            DeviceProviderType.BAAS: mock_provider,
        }

        result = await service.check_alive_by_bot(
            bot_id="bot1",
            entity_id="entity1",
            minutes=1440,
            env="prod",
        )

        assert len(result.devices) == 1
        assert result.devices[0].status == DeviceAliveStatus.UNKNOWN
        assert result.unknown_count == 1

    @pytest.mark.asyncio
    async def test_mixed_devices_unknown_count(self) -> None:
        """Mix of supported and unsupported devices should count correctly."""
        service = _make_service()

        service._device_binding_repo = MagicMock()
        service._device_binding_repo.get_bot_binding = MagicMock(
            return_value={
                "bot_type": "personal",
                "binding_id": 1,
                "active_engine": "openclaw",
            }
        )

        from secbaas.community.api.health_check.bot import (
            DeviceProviderType,
            PaasDeviceInfo,
        )

        arca_device = PaasDeviceInfo(
            paas_device_id="ARCA-SANDBOX-001@0",
            provider_type="ARCA",
            status="online",
        )
        sigma_device = PaasDeviceInfo(
            paas_device_id="SIGMA-DEVICE-001",
            provider_type="SIGMA",
            status="online",
        )

        mock_provider = MagicMock()
        mock_provider.list_paas_device_by_bot = AsyncMock(
            return_value=[arca_device, sigma_device]
        )
        service._device_providers = {
            DeviceProviderType.ARCA: mock_provider,
            DeviceProviderType.BAAS: mock_provider,
        }

        # Mock health provider for ARCA
        mock_arca_provider = MagicMock()
        mock_arca_provider.check_alive = AsyncMock(
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
                duration_ms=100,
            )
        )
        service._health_provider_factory = MagicMock()
        service._health_provider_factory.get = MagicMock(
            return_value=mock_arca_provider
        )

        result = await service.check_alive_by_bot(
            bot_id="bot1",
            entity_id="entity1",
            minutes=1440,
            env="prod",
        )

        assert result.unknown_count == 1  # Only SIGMA is unsupported
        assert result.live_count == 1  # ARCA is alive
        assert result.devices[0].status == DeviceAliveStatus.LIVE
        assert result.devices[1].status == DeviceAliveStatus.UNKNOWN
