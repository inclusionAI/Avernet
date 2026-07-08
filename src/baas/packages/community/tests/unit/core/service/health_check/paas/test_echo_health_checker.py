"""Unit tests for EchoHealthChecker and ArcaPaaSHealthProvider.check_alive with checkers."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from secbaas.api.health_check.paas import (
    HealthCheckerStrategyResult,
)
from secbaas.core.service.health_check.paas._arca_paas_health_provider import (
    EchoHealthChecker,
)


class TestEchoHealthChecker:
    """Tests for EchoHealthChecker."""

    def test_name_without_engine(self) -> None:
        checker = EchoHealthChecker()
        assert checker.name == "echo"

    def test_name_with_engine(self) -> None:
        checker = EchoHealthChecker(engine_name="aicoding")
        assert checker.name == "echo_aicoding"

    def test_name_with_claude_code(self) -> None:
        checker = EchoHealthChecker(engine_name="claude_code")
        assert checker.name == "echo_claude_code"

    @pytest.mark.asyncio
    async def test_default_echo_success(self) -> None:
        checker = EchoHealthChecker()
        mock_facade = MagicMock()
        mock_result = MagicMock()
        mock_result.exit_code = 0
        mock_result.stdout = '{"code": 0, "status": "alive"}'
        mock_result.stderr = ""
        mock_facade.execute_command = AsyncMock(return_value=mock_result)

        result = await checker.check("ARCA-SANDBOX-123@0", mock_facade)

        assert result.healthy is True
        assert result.response == {"code": 0, "status": "alive"}
        assert result.error is None
        assert result.timeout is False
        # Verify command sent
        mock_facade.execute_command.assert_called_once()
        call_args = mock_facade.execute_command.call_args
        assert "echo" in call_args.kwargs.get("cmd", call_args[1].get("cmd", ""))

    @pytest.mark.asyncio
    async def test_engine_name_echo_success(self) -> None:
        checker = EchoHealthChecker(engine_name="aicoding")
        mock_facade = MagicMock()
        mock_result = MagicMock()
        mock_result.exit_code = 0
        mock_result.stdout = '{"code": 0, "engine": "aicoding"}'
        mock_result.stderr = ""
        mock_facade.execute_command = AsyncMock(return_value=mock_result)

        result = await checker.check("ARCA-SANDBOX-123@0", mock_facade)

        assert result.healthy is True
        assert result.response == {"code": 0, "engine": "aicoding"}
        assert result.error is None
        # Verify command contains engine payload
        call_args = mock_facade.execute_command.call_args
        cmd = call_args.kwargs.get("cmd", call_args[1].get("cmd", ""))
        assert "aicoding" in cmd

    @pytest.mark.asyncio
    async def test_echo_failure(self) -> None:
        checker = EchoHealthChecker()
        mock_facade = MagicMock()
        mock_result = MagicMock()
        mock_result.exit_code = 1
        mock_result.stdout = ""
        mock_result.stderr = "command not found"
        mock_facade.execute_command = AsyncMock(return_value=mock_result)

        result = await checker.check("ARCA-SANDBOX-123@0", mock_facade)

        assert result.healthy is False
        assert result.response is None
        assert "exit code 1" in result.error
        assert result.timeout is False

    @pytest.mark.asyncio
    async def test_echo_timeout(self) -> None:
        checker = EchoHealthChecker()
        mock_facade = MagicMock()
        mock_facade.execute_command = AsyncMock(side_effect=TimeoutError())

        result = await checker.check("ARCA-SANDBOX-123@0", mock_facade)

        assert result.healthy is False
        assert result.response is None
        assert result.timeout is True
        assert "Timeout" in result.error

    @pytest.mark.asyncio
    async def test_echo_exception(self) -> None:
        checker = EchoHealthChecker()
        mock_facade = MagicMock()
        mock_facade.execute_command = AsyncMock(
            side_effect=RuntimeError("connection lost")
        )

        result = await checker.check("ARCA-SANDBOX-123@0", mock_facade)

        assert result.healthy is False
        assert result.response is None
        assert result.timeout is False
        assert "connection lost" in result.error


class TestArcaPaaSHealthProviderCheckAliveWithCheckers:
    """Tests for ArcaPaaSHealthProvider.check_alive with checkers parameter."""

    @pytest.mark.asyncio
    async def test_check_alive_with_active_checker(self) -> None:
        """check_alive with checkers=["active"] should use ActiveHealthChecker."""
        from secbaas.core.service.health_check.paas._arca_paas_health_provider import (
            ArcaPaaSHealthProvider,
        )

        mock_facade = MagicMock()
        mock_result = MagicMock()
        mock_result.exit_code = 0
        mock_result.stdout = (
            '{"ok": true, "status": "live", "lastSessionTime": "2024-01-01 12:00:00"}'
        )
        mock_result.stderr = ""
        mock_facade.execute_command = AsyncMock(return_value=mock_result)

        provider = ArcaPaaSHealthProvider(paas_facade=mock_facade, timeout_seconds=10)
        result = await provider.check_alive(
            paas_device_id="ARCA-SANDBOX-123@0",
            minutes=1440,
            checkers=["active"],
        )

        assert isinstance(result, HealthCheckerStrategyResult)
        assert result.healthy is True

    @pytest.mark.asyncio
    async def test_check_alive_without_checkers_raises(self) -> None:
        """check_alive with checkers=None should raise ValueError."""
        from secbaas.core.service.health_check.paas._arca_paas_health_provider import (
            ArcaPaaSHealthProvider,
        )

        mock_facade = MagicMock()
        provider = ArcaPaaSHealthProvider(paas_facade=mock_facade, timeout_seconds=10)
        with pytest.raises(ValueError, match="check_alive requires checkers"):
            await provider.check_alive(
                paas_device_id="ARCA-SANDBOX-123@0",
                minutes=1440,
                checkers=None,
            )

    @pytest.mark.asyncio
    async def test_check_alive_with_empty_checkers_raises(self) -> None:
        """check_alive with checkers=[] should raise ValueError."""
        from secbaas.core.service.health_check.paas._arca_paas_health_provider import (
            ArcaPaaSHealthProvider,
        )

        mock_facade = MagicMock()
        provider = ArcaPaaSHealthProvider(paas_facade=mock_facade, timeout_seconds=10)
        with pytest.raises(ValueError, match="check_alive requires checkers"):
            await provider.check_alive(
                paas_device_id="ARCA-SANDBOX-123@0",
                minutes=1440,
                checkers=[],
            )

    @pytest.mark.asyncio
    async def test_check_alive_with_invalid_checker_raises(self) -> None:
        """check_alive with unknown checker name should raise ValueError."""
        from secbaas.core.service.health_check.paas._arca_paas_health_provider import (
            ArcaPaaSHealthProvider,
        )

        mock_facade = MagicMock()
        provider = ArcaPaaSHealthProvider(paas_facade=mock_facade, timeout_seconds=10)
        with pytest.raises(ValueError, match="No valid alive checkers found"):
            await provider.check_alive(
                paas_device_id="ARCA-SANDBOX-123@0",
                minutes=1440,
                checkers=["nonexistent"],
            )
