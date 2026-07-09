"""Unit tests for LocalPaaSHealthProvider and SigmaPaaSHealthProvider."""

import pytest

from secbaas.api.health_check.paas import PaasHealthCheckerResult
from secbaas.core.service.health_check.paas._local_paas_health_provider import (
    LocalPaaSHealthProvider,
)
from secbaas.core.service.health_check.paas._sigma_paas_health_provider import (
    SigmaPaaSHealthProvider,
)


class TestLocalPaaSHealthProvider:
    """Tests for LocalPaaSHealthProvider."""

    @pytest.mark.asyncio
    async def test_check_health_always_healthy(self) -> None:
        provider = LocalPaaSHealthProvider()
        result = await provider.check_health(
            paas_device_id="local-device-001",
            checkers=["engine", "adapter"],
        )

        assert isinstance(result, PaasHealthCheckerResult)
        assert result.paas_device_id == "local-device-001"
        assert result.overall_healthy is True
        assert result.checkers == {}

    @pytest.mark.asyncio
    async def test_check_health_empty_checkers(self) -> None:
        provider = LocalPaaSHealthProvider()
        result = await provider.check_health(
            paas_device_id="local-device-002",
            checkers=[],
        )

        assert result.overall_healthy is True
        assert result.checkers == {}

    @pytest.mark.asyncio
    async def test_check_health_any_device_id(self) -> None:
        provider = LocalPaaSHealthProvider()
        result = await provider.check_health(
            paas_device_id="any-id-here",
            checkers=["whatever"],
        )

        assert result.overall_healthy is True


class TestSigmaPaaSHealthProvider:
    """Tests for SigmaPaaSHealthProvider."""

    @pytest.mark.asyncio
    async def test_check_health_raises_not_implemented(self) -> None:
        provider = SigmaPaaSHealthProvider()
        with pytest.raises(NotImplementedError, match="Sigma platform health check"):
            await provider.check_health(
                paas_device_id="sigma-device-001",
                checkers=[],
            )
