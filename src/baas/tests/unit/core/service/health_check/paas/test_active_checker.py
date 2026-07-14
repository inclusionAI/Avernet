"""Unit tests for ActiveHealthChecker."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from secbaas.community.api.health_check.paas import HealthCheckerStrategyResult
from secbaas.community.core.service.health_check.paas._arca_paas_health_provider import (
    ActiveHealthChecker,
)


class TestActiveHealthChecker:
    """Tests for ActiveHealthChecker."""

    def test_name(self) -> None:
        checker = ActiveHealthChecker()
        assert checker.name == "active"

    def test_name_with_custom_minutes(self) -> None:
        checker = ActiveHealthChecker(minutes=60)
        assert checker.name == "active"

    @pytest.mark.asyncio
    async def test_check_live(self) -> None:
        checker = ActiveHealthChecker()
        mock_facade = MagicMock()
        mock_result = MagicMock()
        mock_result.exit_code = 0
        mock_result.stdout = (
            '{"ok": true, "status": "live", "lastSessionTime": "2024-01-01 12:00:00"}'
        )
        mock_result.stderr = ""
        mock_facade.execute_command = AsyncMock(return_value=mock_result)

        result = await checker.check("ARCA-SANDBOX-123@0", mock_facade)

        assert isinstance(result, HealthCheckerStrategyResult)
        assert result.healthy is True
        assert result.response == {
            "ok": True,
            "status": "live",
            "lastSessionTime": "2024-01-01 12:00:00",
        }
        assert result.error is None
        assert result.timeout is False

    @pytest.mark.asyncio
    async def test_check_idle(self) -> None:
        checker = ActiveHealthChecker()
        mock_facade = MagicMock()
        mock_result = MagicMock()
        mock_result.exit_code = 0
        mock_result.stdout = (
            '{"ok": true, "lastSessionTime": "", "hasEnabledCron": false}'
        )
        mock_result.stderr = ""
        mock_facade.execute_command = AsyncMock(return_value=mock_result)

        result = await checker.check("ARCA-SANDBOX-123@0", mock_facade)

        # idle is still healthy (device is responsive), no error text
        assert result.healthy is True
        assert result.response == {
            "ok": True,
            "lastSessionTime": "",
            "hasEnabledCron": False,
        }
        assert result.error is None

    @pytest.mark.asyncio
    async def test_check_command_failure(self) -> None:
        checker = ActiveHealthChecker()
        mock_facade = MagicMock()
        mock_result = MagicMock()
        mock_result.exit_code = 1
        mock_result.stderr = "permission denied"
        mock_facade.execute_command = AsyncMock(return_value=mock_result)

        result = await checker.check("ARCA-SANDBOX-123@0", mock_facade)

        assert result.healthy is False
        assert "exit code 1" in result.error

    @pytest.mark.asyncio
    async def test_check_timeout(self) -> None:
        checker = ActiveHealthChecker()
        mock_facade = MagicMock()
        mock_facade.execute_command = AsyncMock(side_effect=TimeoutError())

        result = await checker.check("ARCA-SANDBOX-123@0", mock_facade)

        assert result.healthy is False
        assert result.timeout is True

    @pytest.mark.asyncio
    async def test_check_exception(self) -> None:
        checker = ActiveHealthChecker()
        mock_facade = MagicMock()
        mock_facade.execute_command = AsyncMock(
            side_effect=RuntimeError("active check failed")
        )

        result = await checker.check("ARCA-SANDBOX-123@0", mock_facade)

        assert result.healthy is False
        assert "active check failed" in result.error
