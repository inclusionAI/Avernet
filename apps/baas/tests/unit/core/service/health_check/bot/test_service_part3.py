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
        query_service=MagicMock(),
        health_provider_factory=mock_health_factory or MagicMock(),
    )


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
        service._query_service.get_bot_binding = MagicMock(
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
        service._query_service.get_bot_binding = MagicMock(
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
        service._query_service.get_bot_binding = MagicMock(
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
        service._query_service.get_bot_binding = MagicMock(
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
        service._query_service.get_bot_binding = MagicMock(
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


class TestCountStatistics:
    """unknown_count 和 error_count 统计正确性（task 5.8）。"""

    @pytest.mark.asyncio
    async def test_error_count_from_checker_exception(self) -> None:
        """checker 抛异常 → error_count +1。"""
        service = _make_service()
        service._query_service.get_bot_binding = MagicMock(
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
        service._query_service.get_bot_binding = MagicMock(
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
        service._query_service.get_bot_binding = MagicMock(
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
        service._query_service.get_bot_binding = MagicMock(
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


class TestMissingIdentityAsError:
    """missing identity 判定为 error 而非 unknown（task 5.9）。"""

    @pytest.mark.asyncio
    async def test_empty_paas_device_id_is_error(self) -> None:
        """paas_device_id 为空 → status=ERROR。"""
        service = _make_service()
        service._query_service.get_bot_binding = MagicMock(
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
        service._query_service.get_bot_binding = MagicMock(
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
