"""Unit tests for EngineHealthChecker, AdapterHealthChecker, GatewayHealthChecker."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from secbaas.api.health_check.paas import HealthCheckerStrategyResult
from secbaas.core.service.health_check.paas._arca_paas_health_provider import (
    AdapterHealthChecker,
    EchoHealthChecker,
    EngineHealthChecker,
    GatewayHealthChecker,
)


class TestEngineHealthChecker:
    """Tests for EngineHealthChecker."""

    def test_name(self) -> None:
        checker = EngineHealthChecker()
        assert checker.name == "engine"

    @pytest.mark.asyncio
    async def test_check_success(self) -> None:
        checker = EngineHealthChecker()
        mock_facade = MagicMock()
        mock_result = MagicMock()
        mock_result.exit_code = 0
        mock_result.stdout = '{"process": {"running": true}}'
        mock_result.stderr = ""
        mock_facade.execute_command = AsyncMock(return_value=mock_result)

        result = await checker.check("ARCA-SANDBOX-123@0", mock_facade)

        assert isinstance(result, HealthCheckerStrategyResult)
        assert result.healthy is True
        assert result.response == {"process": {"running": True}}
        assert result.error is None
        assert result.timeout is False

    @pytest.mark.asyncio
    async def test_check_not_running(self) -> None:
        checker = EngineHealthChecker()
        mock_facade = MagicMock()
        mock_result = MagicMock()
        mock_result.exit_code = 0
        mock_result.stdout = '{"process": {"running": false}}'
        mock_result.stderr = ""
        mock_facade.execute_command = AsyncMock(return_value=mock_result)

        result = await checker.check("ARCA-SANDBOX-123@0", mock_facade)

        assert result.healthy is False
        assert "not running" in result.error

    @pytest.mark.asyncio
    async def test_check_command_failure(self) -> None:
        checker = EngineHealthChecker()
        mock_facade = MagicMock()
        mock_result = MagicMock()
        mock_result.exit_code = 1
        mock_result.stdout = ""
        mock_result.stderr = "connection refused"
        mock_facade.execute_command = AsyncMock(return_value=mock_result)

        result = await checker.check("ARCA-SANDBOX-123@0", mock_facade)

        assert result.healthy is False
        assert "exit code 1" in result.error

    @pytest.mark.asyncio
    async def test_check_timeout(self) -> None:
        checker = EngineHealthChecker()
        mock_facade = MagicMock()
        mock_facade.execute_command = AsyncMock(side_effect=TimeoutError())

        result = await checker.check("ARCA-SANDBOX-123@0", mock_facade)

        assert result.healthy is False
        assert result.timeout is True
        assert "Timeout" in result.error

    @pytest.mark.asyncio
    async def test_check_exception(self) -> None:
        checker = EngineHealthChecker()
        mock_facade = MagicMock()
        mock_facade.execute_command = AsyncMock(
            side_effect=RuntimeError("unexpected error")
        )

        result = await checker.check("ARCA-SANDBOX-123@0", mock_facade)

        assert result.healthy is False
        assert result.timeout is False
        assert "unexpected error" in result.error


class TestAdapterHealthChecker:
    """Tests for AdapterHealthChecker."""

    def test_name(self) -> None:
        checker = AdapterHealthChecker()
        assert checker.name == "adapter"

    @pytest.mark.asyncio
    async def test_check_success(self) -> None:
        checker = AdapterHealthChecker()
        mock_facade = MagicMock()
        mock_result = MagicMock()
        mock_result.exit_code = 0
        mock_result.stdout = '{"status": "ok"}'
        mock_result.stderr = ""
        mock_facade.execute_command = AsyncMock(return_value=mock_result)

        result = await checker.check("ARCA-SANDBOX-123@0", mock_facade)

        assert result.healthy is True
        assert result.response == {"status": "ok"}
        assert result.error is None

    @pytest.mark.asyncio
    async def test_check_unhealthy(self) -> None:
        checker = AdapterHealthChecker()
        mock_facade = MagicMock()
        mock_result = MagicMock()
        mock_result.exit_code = 0
        mock_result.stdout = '{"status": "error"}'
        mock_result.stderr = ""
        mock_facade.execute_command = AsyncMock(return_value=mock_result)

        result = await checker.check("ARCA-SANDBOX-123@0", mock_facade)

        assert result.healthy is False
        assert "unhealthy" in result.error

    @pytest.mark.asyncio
    async def test_check_command_failure(self) -> None:
        checker = AdapterHealthChecker()
        mock_facade = MagicMock()
        mock_result = MagicMock()
        mock_result.exit_code = 1
        mock_result.stderr = "not found"
        mock_facade.execute_command = AsyncMock(return_value=mock_result)

        result = await checker.check("ARCA-SANDBOX-123@0", mock_facade)

        assert result.healthy is False
        assert "exit code 1" in result.error

    @pytest.mark.asyncio
    async def test_check_timeout(self) -> None:
        checker = AdapterHealthChecker()
        mock_facade = MagicMock()
        mock_facade.execute_command = AsyncMock(side_effect=TimeoutError())

        result = await checker.check("ARCA-SANDBOX-123@0", mock_facade)

        assert result.healthy is False
        assert result.timeout is True

    @pytest.mark.asyncio
    async def test_check_exception(self) -> None:
        checker = AdapterHealthChecker()
        mock_facade = MagicMock()
        mock_facade.execute_command = AsyncMock(
            side_effect=RuntimeError("adapter crash")
        )

        result = await checker.check("ARCA-SANDBOX-123@0", mock_facade)

        assert result.healthy is False
        assert "adapter crash" in result.error


class TestGatewayHealthChecker:
    """Tests for GatewayHealthChecker."""

    def test_name(self) -> None:
        checker = GatewayHealthChecker()
        assert checker.name == "gateway"

    @pytest.mark.asyncio
    async def test_check_success_with_ok(self) -> None:
        checker = GatewayHealthChecker()
        mock_facade = MagicMock()
        mock_result = MagicMock()
        mock_result.exit_code = 0
        mock_result.stdout = '{"ok": true, "status": "live"}'
        mock_result.stderr = ""
        mock_facade.execute_command = AsyncMock(return_value=mock_result)

        result = await checker.check("ARCA-SANDBOX-123@0", mock_facade)

        assert result.healthy is True
        assert result.response == {"ok": True, "status": "live"}

    @pytest.mark.asyncio
    async def test_check_success_with_status_live(self) -> None:
        """Gateway is healthy if status is 'live' even without ok."""
        checker = GatewayHealthChecker()
        mock_facade = MagicMock()
        mock_result = MagicMock()
        mock_result.exit_code = 0
        mock_result.stdout = '{"ok": false, "status": "live"}'
        mock_result.stderr = ""
        mock_facade.execute_command = AsyncMock(return_value=mock_result)

        result = await checker.check("ARCA-SANDBOX-123@0", mock_facade)

        assert result.healthy is True

    @pytest.mark.asyncio
    async def test_check_unhealthy(self) -> None:
        checker = GatewayHealthChecker()
        mock_facade = MagicMock()
        mock_result = MagicMock()
        mock_result.exit_code = 0
        mock_result.stdout = '{"ok": false, "status": "dead"}'
        mock_result.stderr = ""
        mock_facade.execute_command = AsyncMock(return_value=mock_result)

        result = await checker.check("ARCA-SANDBOX-123@0", mock_facade)

        assert result.healthy is False
        assert "unhealthy" in result.error

    @pytest.mark.asyncio
    async def test_check_command_failure(self) -> None:
        checker = GatewayHealthChecker()
        mock_facade = MagicMock()
        mock_result = MagicMock()
        mock_result.exit_code = 1
        mock_result.stderr = "timeout"
        mock_facade.execute_command = AsyncMock(return_value=mock_result)

        result = await checker.check("ARCA-SANDBOX-123@0", mock_facade)

        assert result.healthy is False
        assert "exit code 1" in result.error

    @pytest.mark.asyncio
    async def test_check_timeout(self) -> None:
        checker = GatewayHealthChecker()
        mock_facade = MagicMock()
        mock_facade.execute_command = AsyncMock(side_effect=TimeoutError())

        result = await checker.check("ARCA-SANDBOX-123@0", mock_facade)

        assert result.healthy is False
        assert result.timeout is True

    @pytest.mark.asyncio
    async def test_check_exception(self) -> None:
        checker = GatewayHealthChecker()
        mock_facade = MagicMock()
        mock_facade.execute_command = AsyncMock(
            side_effect=RuntimeError("gateway error")
        )

        result = await checker.check("ARCA-SANDBOX-123@0", mock_facade)

        assert result.healthy is False
        assert "gateway error" in result.error


class TestEchoHealthChecker:
    """Tests for EchoHealthChecker."""

    def test_name(self) -> None:
        checker = EchoHealthChecker()
        assert checker.name == "echo"

    def test_name_with_engine(self) -> None:
        checker = EchoHealthChecker(engine_name="aicoding")
        assert checker.name == "echo_aicoding"

    @pytest.mark.asyncio
    async def test_check_success(self) -> None:
        checker = EchoHealthChecker()
        mock_facade = MagicMock()
        mock_result = MagicMock()
        mock_result.exit_code = 0
        mock_result.stdout = '{"code": 0, "status": "alive"}'
        mock_result.stderr = ""
        mock_facade.execute_command = AsyncMock(return_value=mock_result)

        result = await checker.check("ARCA-SANDBOX-123@0", mock_facade)

        assert isinstance(result, HealthCheckerStrategyResult)
        assert result.healthy is True
        assert result.response == {"code": 0, "status": "alive"}
        assert result.error is None
        assert result.timeout is False

    @pytest.mark.asyncio
    async def test_check_success_with_engine(self) -> None:
        """With engine_name, payload contains engine field."""
        checker = EchoHealthChecker(engine_name="claude_code")
        mock_facade = MagicMock()
        mock_result = MagicMock()
        mock_result.exit_code = 0
        mock_result.stdout = '{"code": 0, "engine": "claude_code"}'
        mock_result.stderr = ""
        mock_facade.execute_command = AsyncMock(return_value=mock_result)

        result = await checker.check("ARCA-SANDBOX-123@0", mock_facade)

        assert result.healthy is True
        assert result.response == {"code": 0, "engine": "claude_code"}

    @pytest.mark.asyncio
    async def test_check_command_failure(self) -> None:
        checker = EchoHealthChecker()
        mock_facade = MagicMock()
        mock_result = MagicMock()
        mock_result.exit_code = 1
        mock_result.stdout = ""
        mock_result.stderr = "connection refused"
        mock_facade.execute_command = AsyncMock(return_value=mock_result)

        result = await checker.check("ARCA-SANDBOX-123@0", mock_facade)

        assert result.healthy is False
        assert "exit code 1" in result.error

    @pytest.mark.asyncio
    async def test_check_timeout(self) -> None:
        checker = EchoHealthChecker()
        mock_facade = MagicMock()
        mock_facade.execute_command = AsyncMock(side_effect=TimeoutError())

        result = await checker.check("ARCA-SANDBOX-123@0", mock_facade)

        assert result.healthy is False
        assert result.timeout is True
        assert "Timeout" in result.error

    @pytest.mark.asyncio
    async def test_check_exception(self) -> None:
        checker = EchoHealthChecker()
        mock_facade = MagicMock()
        mock_facade.execute_command = AsyncMock(
            side_effect=RuntimeError("echo check failed")
        )

        result = await checker.check("ARCA-SANDBOX-123@0", mock_facade)

        assert result.healthy is False
        assert result.timeout is False
        assert "echo check failed" in result.error
