"""Unit tests for BotHealthCheckerService.check_single_device().

Tests the extracted public method that performs health checks on a single device.
Mocks the health_provider_factory to avoid real PaaS calls.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from secbaas.api.health_check.bot import (
    BotHealthCheckerConfig,
    PaasDeviceInfo,
)
from secbaas.api.health_check.bot import (
    BotHealthCheckerService as BotHealthCheckerServiceProtocol,
)
from secbaas.api.health_check.paas import PaasHealthCheckerResult
from secbaas.core.service.health_check.bot import BotHealthCheckerService


def _make_service(health_provider_factory=None):
    """Create a BotHealthCheckerService with mocked repos and optional factory."""
    return BotHealthCheckerService(
        device_binding_repo=MagicMock(),
        device_repo=MagicMock(),
        paas_facade=MagicMock(),
        config=BotHealthCheckerConfig(),
        health_provider_factory=health_provider_factory or MagicMock(),
    )


class TestCheckSingleDeviceSkipConditions:
    """check_single_device returns None for devices that cannot be checked."""

    @pytest.mark.asyncio
    async def test_skips_device_with_empty_paas_device_id(self):
        """Empty paas_device_id should return None."""
        service = _make_service()
        device = PaasDeviceInfo(paas_device_id="", status="ACTIVE")
        result = await service.check_single_device(device)
        assert result is None

    @pytest.mark.asyncio
    async def test_skips_device_with_none_provider_type(self):
        """Provider_type=None should return None."""
        service = _make_service()
        device = PaasDeviceInfo(
            paas_device_id="sandbox-123@0",
            provider_type=None,
            status="ACTIVE",
        )
        result = await service.check_single_device(device)
        assert result is None


class TestCheckSingleDeviceWithMockedProvider:
    """check_single_device with a mocked health provider."""

    @pytest.fixture
    def mock_health_provider(self):
        """Create a mock health provider that returns a healthy result."""
        from secbaas.api.health_check.paas import HealthCheckerStrategyResult

        provider = MagicMock()
        provider.check_health = AsyncMock(
            return_value=PaasHealthCheckerResult(
                paas_device_id="sandbox-123@0",
                overall_healthy=True,
                checkers={
                    "engine": HealthCheckerStrategyResult(
                        healthy=True, duration_ms=100
                    ),
                },
            )
        )
        return provider

    @pytest.fixture
    def mock_factory(self, mock_health_provider):
        """Create a mock factory that returns the mock provider."""
        factory = MagicMock()
        factory.get.return_value = mock_health_provider
        return factory

    @pytest.mark.asyncio
    async def test_arca_device_returns_paas_device_id_and_result(
        self, mock_factory, mock_health_provider
    ):
        """ARCA device with valid ID should return (paas_device_id, result)."""
        service = _make_service(health_provider_factory=mock_factory)
        device = PaasDeviceInfo(
            paas_device_id="sandbox-123@0",
            provider_type="ARCA",
            status="ACTIVE",
        )
        result = await service.check_single_device(device, active_engine="openclaw")

        assert result is not None
        pid, health_result = result
        assert pid == "sandbox-123@0"
        assert health_result.overall_healthy is True

    @pytest.mark.asyncio
    async def test_calls_factory_get_with_provider_type(
        self, mock_factory, mock_health_provider
    ):
        """Factory.get() should be called with the device's provider_type."""
        service = _make_service(health_provider_factory=mock_factory)
        device = PaasDeviceInfo(
            paas_device_id="sandbox-456@0",
            provider_type="ARCA",
            status="ACTIVE",
        )
        await service.check_single_device(device, active_engine="openclaw")

        mock_factory.get.assert_called_once_with("ARCA")

    @pytest.mark.asyncio
    async def test_calls_check_health_with_correct_args(
        self, mock_factory, mock_health_provider
    ):
        """check_health should be called with the device ID and resolved checkers."""
        service = _make_service(health_provider_factory=mock_factory)
        device = PaasDeviceInfo(
            paas_device_id="sandbox-789@0",
            provider_type="ARCA",
            status="ACTIVE",
        )
        await service.check_single_device(device, active_engine="openclaw")

        mock_health_provider.check_health.assert_called_once_with(
            paas_device_id="sandbox-789@0",
            checkers=["engine", "adapter", "gateway"],
        )

    @pytest.mark.asyncio
    async def test_none_active_engine_uses_fallback_checkers(
        self, mock_factory, mock_health_provider
    ):
        """None active_engine should resolve to fallback checkers (e.g. ['echo'])."""
        service = _make_service(health_provider_factory=mock_factory)
        device = PaasDeviceInfo(
            paas_device_id="sandbox-abc@0",
            provider_type="ARCA",
            status="ACTIVE",
        )
        await service.check_single_device(device, active_engine=None)

        mock_health_provider.check_health.assert_called_once_with(
            paas_device_id="sandbox-abc@0",
            checkers=["echo"],
        )

    @pytest.mark.asyncio
    async def test_unknown_engine_uses_fallback_checkers(
        self, mock_factory, mock_health_provider
    ):
        """Unknown engine should resolve to fallback checkers."""
        service = _make_service(health_provider_factory=mock_factory)
        device = PaasDeviceInfo(
            paas_device_id="sandbox-xyz@0",
            provider_type="ARCA",
            status="ACTIVE",
        )
        await service.check_single_device(device, active_engine="nonexistent_engine")

        mock_health_provider.check_health.assert_called_once_with(
            paas_device_id="sandbox-xyz@0",
            checkers=["echo"],
        )


class TestCheckSingleDeviceUnhealthyResult:
    """check_single_device returns the actual health status from the provider."""

    @pytest.mark.asyncio
    async def test_returns_unhealthy_result_when_provider_reports_unhealthy(self):
        """When provider returns unhealthy, the result should reflect that."""
        from secbaas.api.health_check.paas import HealthCheckerStrategyResult

        provider = MagicMock()
        provider.check_health = AsyncMock(
            return_value=PaasHealthCheckerResult(
                paas_device_id="sandbox-fail@0",
                overall_healthy=False,
                checkers={
                    "engine": HealthCheckerStrategyResult(
                        healthy=False, duration_ms=50
                    ),
                },
            )
        )
        factory = MagicMock()
        factory.get.return_value = provider
        service = _make_service(health_provider_factory=factory)

        device = PaasDeviceInfo(
            paas_device_id="sandbox-fail@0",
            provider_type="ARCA",
            status="ACTIVE",
        )
        result = await service.check_single_device(device, active_engine="openclaw")

        assert result is not None
        _, health_result = result
        assert health_result.overall_healthy is False
        assert health_result.paas_device_id == "sandbox-fail@0"
