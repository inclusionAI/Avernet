"""Unit tests for PaaSHealthProvider base class."""

import pytest

from secbaas.core.service.health_check.paas._paas_health_provider import (
    PaaSHealthProvider,
)


class TestPaaSHealthProviderBase:
    """Tests for PaaSHealthProvider abstract base class."""

    @pytest.mark.asyncio
    async def test_check_alive_default_raises_not_implemented(self) -> None:
        """Base class check_alive raises NotImplementedError."""

        class MinimalProvider(PaaSHealthProvider):
            async def check_health(
                self, paas_device_id: str, checkers: list[str]
            ) -> object:
                return None

        provider = MinimalProvider()
        with pytest.raises(NotImplementedError) as exc:
            await provider.check_alive(
                paas_device_id="test-device",
                minutes=1440,
                checkers=["active"],
            )
        assert "does not implement check_alive" in str(exc.value)
