"""Unit tests for ArcaPaaSHealthProvider."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from secbaas.api.health_check.paas import (
    HealthCheckerStrategyResult,
    PaasHealthCheckerResult,
)
from secbaas.core.service.health_check.paas._arca_paas_health_provider import (
    ArcaPaaSHealthProvider,
)


class TestArcaPaaSHealthProviderCheckHealth:
    """Tests for ArcaPaaSHealthProvider.check_health."""

    @pytest.fixture
    def mock_facade(self) -> MagicMock:
        return MagicMock()

    @pytest.fixture
    def provider(self, mock_facade: MagicMock) -> ArcaPaaSHealthProvider:
        return ArcaPaaSHealthProvider(paas_facade=mock_facade, timeout_seconds=10)

    @pytest.mark.asyncio
    async def test_all_checkers_healthy(
        self, provider: ArcaPaaSHealthProvider, mock_facade: MagicMock
    ) -> None:
        """All checkers report healthy."""

        async def mock_execute(
            paas_device_id: str, cmd: str, **kwargs: object
        ) -> MagicMock:
            result = MagicMock()
            result.exit_code = 0
            result.stderr = ""
            if "engine/status" in cmd:
                result.stdout = '{"process": {"running": true}}'
            elif "health" in cmd and "18789" in cmd:
                result.stdout = '{"ok": true, "status": "live"}'
            elif "health" in cmd:
                result.stdout = '{"status": "ok"}'
            else:
                result.stdout = "{}"
            return result

        mock_facade.execute_command = AsyncMock(side_effect=mock_execute)

        result = await provider.check_health(
            paas_device_id="ARCA-SANDBOX-123@0",
            checkers=["engine", "adapter", "gateway"],
        )

        assert isinstance(result, PaasHealthCheckerResult)
        assert result.paas_device_id == "ARCA-SANDBOX-123@0"
        assert result.overall_healthy is True
        assert len(result.checkers) == 3
        assert result.checkers["engine"].healthy is True
        assert result.checkers["adapter"].healthy is True
        assert result.checkers["gateway"].healthy is True

    @pytest.mark.asyncio
    async def test_some_checkers_unhealthy(
        self, provider: ArcaPaaSHealthProvider, mock_facade: MagicMock
    ) -> None:
        """Partial checker failure results in overall_healthy=False."""

        async def mock_execute(
            paas_device_id: str, cmd: str, **kwargs: object
        ) -> MagicMock:
            result = MagicMock()
            result.exit_code = 0
            result.stderr = ""
            if "engine/status" in cmd:
                result.stdout = '{"process": {"running": true}}'
            elif "health" in cmd and "18789" not in cmd:
                result.stdout = '{"status": "ok"}'
            elif "health" in cmd:
                result.stdout = '{"ok": false, "status": "dead"}'
            elif "echo" in cmd:
                result.stdout = '{"code": 0, "status": "alive"}'
            else:
                result.stdout = "{}"
            return result

        mock_facade.execute_command = AsyncMock(side_effect=mock_execute)

        result = await provider.check_health(
            paas_device_id="ARCA-SANDBOX-123@0",
            checkers=["engine", "adapter", "gateway", "echo"],
        )

        assert result.overall_healthy is False
        assert result.checkers["engine"].healthy is True
        assert result.checkers["adapter"].healthy is True
        assert result.checkers["gateway"].healthy is False
        assert result.checkers["echo"].healthy is True

    @pytest.mark.asyncio
    async def test_empty_checkers(
        self, provider: ArcaPaaSHealthProvider, mock_facade: MagicMock
    ) -> None:
        """Empty checkers list returns overall_healthy=True with no checker results."""
        result = await provider.check_health(
            paas_device_id="ARCA-SANDBOX-123@0",
            checkers=[],
        )

        assert result.overall_healthy is True
        assert result.checkers == {}

    @pytest.mark.asyncio
    async def test_checker_times_out(
        self, provider: ArcaPaaSHealthProvider, mock_facade: MagicMock
    ) -> None:
        """A checker that times out should be caught by asyncio.wait_for wrapper."""

        async def mock_execute(**kwargs: object) -> MagicMock:
            raise TimeoutError("Command timed out")

        mock_facade.execute_command = AsyncMock(side_effect=mock_execute)

        result = await provider.check_health(
            paas_device_id="ARCA-SANDBOX-123@0",
            checkers=["engine"],
        )

        assert result.overall_healthy is False
        assert result.checkers["engine"].healthy is False
        assert result.checkers["engine"].timeout is True

    @pytest.mark.asyncio
    async def test_checker_exception(
        self, provider: ArcaPaaSHealthProvider, mock_facade: MagicMock
    ) -> None:
        """A checker that raises an exception is caught by the run_checker wrapper."""

        async def mock_execute(**kwargs: object) -> MagicMock:
            raise RuntimeError("Unexpected checker crash")

        mock_facade.execute_command = AsyncMock(side_effect=mock_execute)

        result = await provider.check_health(
            paas_device_id="ARCA-SANDBOX-123@0",
            checkers=["engine"],
        )

        assert result.overall_healthy is False
        assert result.checkers["engine"].healthy is False
        assert result.checkers["engine"].timeout is False
        assert "Unexpected checker crash" in result.checkers["engine"].error

    @pytest.mark.asyncio
    async def test_echo_checker(
        self, provider: ArcaPaaSHealthProvider, mock_facade: MagicMock
    ) -> None:
        """Echo checker works correctly."""
        mock_result = MagicMock()
        mock_result.exit_code = 0
        mock_result.stdout = '{"code": 0, "status": "alive"}'
        mock_result.stderr = ""
        mock_facade.execute_command = AsyncMock(return_value=mock_result)

        result = await provider.check_health(
            paas_device_id="ARCA-SANDBOX-123@0",
            checkers=["echo", "echo_aicoding", "echo_claude_code"],
        )

        assert result.overall_healthy is True
        assert len(result.checkers) == 3
        assert result.checkers["echo"].healthy is True
        assert result.checkers["echo_aicoding"].healthy is True
        assert result.checkers["echo_claude_code"].healthy is True

    @pytest.mark.asyncio
    async def test_concurrent_execution(
        self, provider: ArcaPaaSHealthProvider, mock_facade: MagicMock
    ) -> None:
        """Checkers are executed concurrently (asyncio.gather)."""
        import asyncio

        async def slow_execute(
            paas_device_id: str, cmd: str, **kwargs: object
        ) -> MagicMock:
            await asyncio.sleep(0.05)
            result = MagicMock()
            result.exit_code = 0
            result.stderr = ""
            if "engine/status" in cmd:
                result.stdout = '{"process": {"running": true}}'
            elif "health" in cmd and "18789" in cmd:
                result.stdout = '{"ok": true, "status": "live"}'
            elif "health" in cmd:
                result.stdout = '{"status": "ok"}'
            elif "echo" in cmd:
                result.stdout = '{"code": 0, "status": "alive"}'
            else:
                result.stdout = "{}"
            return result

        mock_facade.execute_command = AsyncMock(side_effect=slow_execute)

        import time

        start = time.time()
        result = await provider.check_health(
            paas_device_id="ARCA-SANDBOX-123@0",
            checkers=["engine", "adapter", "gateway", "echo"],
        )
        elapsed = time.time() - start

        # Should complete in roughly 0.05s (concurrent), not 0.20s (sequential)
        assert elapsed < 0.2  # 200ms generous threshold
        assert result.overall_healthy is True


class TestArcaPaaSHealthProviderCheckAlive:
    """Tests for ArcaPaaSHealthProvider.check_alive integration with checkers."""

    @pytest.fixture
    def mock_facade(self) -> MagicMock:
        return MagicMock()

    @pytest.fixture
    def provider(self, mock_facade: MagicMock) -> ArcaPaaSHealthProvider:
        return ArcaPaaSHealthProvider(paas_facade=mock_facade, timeout_seconds=10)

    @pytest.mark.asyncio
    async def test_active_checker_live(
        self, provider: ArcaPaaSHealthProvider, mock_facade: MagicMock
    ) -> None:
        """check_alive with active checker returns live status."""
        mock_result = MagicMock()
        mock_result.exit_code = 0
        mock_result.stdout = (
            '{"ok": true, "status": "live", "lastSessionTime": "2024-01-01 12:00:00"}'
        )
        mock_result.stderr = ""
        mock_facade.execute_command = AsyncMock(return_value=mock_result)

        result = await provider.check_alive(
            paas_device_id="ARCA-SANDBOX-123@0",
            minutes=1440,
            checkers=["active"],
        )

        assert isinstance(result, HealthCheckerStrategyResult)
        assert result.healthy is True

    @pytest.mark.asyncio
    async def test_active_checker_idle(
        self, provider: ArcaPaaSHealthProvider, mock_facade: MagicMock
    ) -> None:
        """check_alive with active checker returns idle status."""
        mock_result = MagicMock()
        mock_result.exit_code = 0
        mock_result.stdout = (
            '{"ok": true, "lastSessionTime": "", "hasEnabledCron": false}'
        )
        mock_result.stderr = ""
        mock_facade.execute_command = AsyncMock(return_value=mock_result)

        result = await provider.check_alive(
            paas_device_id="ARCA-SANDBOX-123@0",
            minutes=1440,
            checkers=["active"],
        )

        # ActiveHealthChecker always returns healthy=True (device alive), idle has no error
        assert result.healthy is True
        assert result.response.get("lastSessionTime") == ""
        assert result.response.get("hasEnabledCron") is False
        assert result.error is None

    @pytest.mark.asyncio
    async def test_check_alive_without_checkers_raises(
        self, provider: ArcaPaaSHealthProvider
    ) -> None:
        with pytest.raises(ValueError, match="check_alive requires checkers"):
            await provider.check_alive(
                paas_device_id="ARCA-SANDBOX-123@0",
                minutes=1440,
                checkers=None,
            )

    @pytest.mark.asyncio
    async def test_check_alive_with_empty_checkers_raises(
        self, provider: ArcaPaaSHealthProvider
    ) -> None:
        with pytest.raises(ValueError, match="check_alive requires checkers"):
            await provider.check_alive(
                paas_device_id="ARCA-SANDBOX-123@0",
                minutes=1440,
                checkers=[],
            )

    @pytest.mark.asyncio
    async def test_check_alive_with_invalid_checker_raises(
        self, provider: ArcaPaaSHealthProvider
    ) -> None:
        with pytest.raises(ValueError, match="No valid alive checkers found"):
            await provider.check_alive(
                paas_device_id="ARCA-SANDBOX-123@0",
                minutes=1440,
                checkers=["nonexistent"],
            )

    @pytest.mark.asyncio
    async def test_check_alive_passes_minutes_to_active_checker(
        self, provider: ArcaPaaSHealthProvider, mock_facade: MagicMock
    ) -> None:
        """verify minutes parameter is passed to ActiveHealthChecker."""
        mock_result = MagicMock()
        mock_result.exit_code = 0
        mock_result.stdout = (
            '{"ok": true, "status": "live", "lastSessionTime": "2024-01-01 12:00:00"}'
        )
        mock_result.stderr = ""
        mock_facade.execute_command = AsyncMock(return_value=mock_result)

        result = await provider.check_alive(
            paas_device_id="ARCA-SANDBOX-123@0",
            minutes=60,  # 1 hour instead of default 1440
            checkers=["active"],
        )

        assert result.healthy is True
        # The command should use MIN=60 (not the default 1440)
        cmd = mock_facade.execute_command.call_args.kwargs["cmd"]
        assert "MIN=60" in cmd

    @pytest.mark.asyncio
    async def test_check_alive_with_echo_checker(
        self, provider: ArcaPaaSHealthProvider, mock_facade: MagicMock
    ) -> None:
        """check_alive with echo checker executes the echo check."""
        mock_result = MagicMock()
        mock_result.exit_code = 0
        mock_result.stdout = '{"code": 0, "status": "alive"}'
        mock_result.stderr = ""
        mock_facade.execute_command = AsyncMock(return_value=mock_result)

        result = await provider.check_alive(
            paas_device_id="ARCA-SANDBOX-123@0",
            checkers=["echo"],
        )

        assert result.healthy is True
