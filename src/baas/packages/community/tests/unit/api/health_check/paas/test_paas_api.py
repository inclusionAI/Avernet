"""Unit tests for paas health check API layer (models, enums, protocols)."""

from typing import Any

import pytest
from pydantic import ValidationError

from secbaas.api.health_check.paas import (
    HealthCheckerStrategyResult,
    PaasHealthCheckerResult,
    PaaSHealthProvider,
    PaaSProviderType,
)


class TestPaaSProviderType:
    """PaaSProviderType enum tests."""

    def test_members(self) -> None:
        assert PaaSProviderType.ARCA == "ARCA"
        assert PaaSProviderType.SIGMA == "SIGMA"
        assert PaaSProviderType.LOCAL == "local"

    def test_unique_values(self) -> None:
        values = {m.value for m in PaaSProviderType}
        assert values == {"ARCA", "POOLAB", "SIGMA", "TECLAW", "K8S", "local", "DOCKER"}


class TestHealthCheckerStrategyResult:
    """HealthCheckerStrategyResult model tests."""

    def test_all_fields(self) -> None:
        result = HealthCheckerStrategyResult(
            healthy=True,
            response={"ok": True},
            error=None,
            timeout=False,
            duration_ms=42,
        )
        assert result.healthy is True
        assert result.response == {"ok": True}
        assert result.duration_ms == 42

    def test_missing_required_field_raises(self) -> None:
        with pytest.raises(ValidationError):
            HealthCheckerStrategyResult()  # type: ignore[call-arg]


class TestPaasHealthCheckerResult:
    """PaasHealthCheckerResult model tests."""

    def test_minimal(self) -> None:
        result = PaasHealthCheckerResult(
            paas_device_id="dev-1",
            overall_healthy=True,
        )
        assert result.paas_device_id == "dev-1"
        assert result.overall_healthy is True
        assert result.checkers == {}

    def test_with_checkers(self) -> None:
        checkers = {
            "engine": HealthCheckerStrategyResult(healthy=True, duration_ms=10),
        }
        result = PaasHealthCheckerResult(
            paas_device_id="dev-2",
            overall_healthy=True,
            checkers=checkers,
        )
        assert result.checkers["engine"].healthy is True

    def test_missing_required_raises(self) -> None:
        with pytest.raises(ValidationError):
            PaasHealthCheckerResult()  # type: ignore[call-arg]


class TestPaaSHealthProviderProtocol:
    """PaaSHealthProvider runtime_checkable protocol test."""

    def test_duck_type_compliance(self) -> None:
        class MinimalProvider:
            async def check_health(
                self, paas_device_id: str, checkers: list[str]
            ) -> Any:
                return PaasHealthCheckerResult(
                    paas_device_id=paas_device_id,
                    overall_healthy=True,
                )

            async def check_alive(
                self,
                paas_device_id: str,
                minutes: int = 1440,
                checkers: list[str] | None = None,
            ) -> Any:
                return HealthCheckerStrategyResult(healthy=True, duration_ms=0)

        provider = MinimalProvider()
        assert isinstance(provider, PaaSHealthProvider)
