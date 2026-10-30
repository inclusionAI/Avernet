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


class TestListPaasDeviceByBot:
    """Tests for BotHealthCheckerService.list_paas_device_by_bot."""

    @pytest.mark.asyncio
    async def test_personal_device(self) -> None:
        service = _make_service()
        service._query_service.get_bot_binding = MagicMock(
            return_value={
                "bot_type": "personal",
                "binding_id": 10,
                "active_engine": "openclaw",
            }
        )

        from secbaas.community.api.health_check.bot import DeviceProviderType

        mock_devices = [
            PaasDeviceInfo(
                paas_device_id="ARCA-SANDBOX-001@0",
                provider_type="ARCA",
                status="online",
                ttl_expiration_timestamp=1000000,
            )
        ]
        mock_provider = MagicMock()
        mock_provider.list_paas_device_by_bot = AsyncMock(return_value=mock_devices)
        service._device_providers = {
            DeviceProviderType.ARCA: mock_provider,
            DeviceProviderType.BAAS: MagicMock(),
        }

        result = await service.list_paas_device_by_bot(
            bot_id="bot1", entity_id="entity1", statuses=["online"], env="prod"
        )

        assert isinstance(result, PaasDeviceListResponse)
        assert result.bot_id == "bot1"
        assert result.bot_type == "personal"
        assert result.active_engine == "openclaw"
        assert len(result.paas_devices) == 1

    @pytest.mark.asyncio
    async def test_service_device(self) -> None:
        service = _make_service()
        service._query_service.get_bot_binding = MagicMock(
            return_value={
                "bot_type": "service",
                "binding_id": 20,
                "active_engine": "openclaw",
            }
        )

        from secbaas.community.api.health_check.bot import DeviceProviderType

        service._device_providers = {
            DeviceProviderType.ARCA: MagicMock(),
            DeviceProviderType.BAAS: MagicMock(),
        }
        mock_devices = [
            PaasDeviceInfo(
                paas_device_id="ARCA-SANDBOX-002@0",
                provider_type="ARCA",
                status="online",
                ttl_expiration_timestamp=1000000,
            )
        ]
        service._device_providers[
            DeviceProviderType.BAAS
        ].list_paas_device_by_bot = AsyncMock(return_value=mock_devices)

        result = await service.list_paas_device_by_bot(
            bot_id="bot1", entity_id="entity1", statuses=["online"], env="prod"
        )

        assert result.bot_type == "service"
        assert len(result.paas_devices) == 1

    @pytest.mark.asyncio
    async def test_missing_bot_type(self) -> None:
        service = _make_service()
        service._query_service.get_bot_binding = MagicMock(
            return_value={
                "bot_type": None,
                "binding_id": 10,
                "active_engine": "openclaw",
            }
        )

        with pytest.raises(BotHealthCheckerError, match="bot_type is missing"):
            await service.list_paas_device_by_bot(
                bot_id="bot1", entity_id="entity1", statuses=[], env="prod"
            )

    @pytest.mark.asyncio
    async def test_bot_not_found(self) -> None:
        service = _make_service()
        service._query_service.get_bot_binding = MagicMock(return_value=None)

        with pytest.raises(SandboxNotFoundError):
            await service.list_paas_device_by_bot(
                bot_id="nonexistent", entity_id="entity1", statuses=[], env="prod"
            )

    @pytest.mark.asyncio
    async def test_refresh_ttl_for_devices_without_ttl(self) -> None:
        """Devices without TTL should get refreshed."""
        service = _make_service()
        service._query_service.get_bot_binding = MagicMock(
            return_value={
                "bot_type": "personal",
                "binding_id": 10,
                "active_engine": "openclaw",
            }
        )

        from secbaas.community.api.health_check.bot import DeviceProviderType

        device_no_ttl = PaasDeviceInfo(
            paas_device_id="ARCA-SANDBOX-001@0",
            provider_type="ARCA",
            status="online",
            ttl_expiration_timestamp=None,
        )
        device_with_ttl = PaasDeviceInfo(
            paas_device_id="ARCA-SANDBOX-002@0",
            provider_type="ARCA",
            status="online",
            ttl_expiration_timestamp=1000000,
        )

        mock_provider = MagicMock()
        mock_provider.list_paas_device_by_bot = AsyncMock(
            return_value=[device_no_ttl, device_with_ttl]
        )
        mock_provider.refresh_device_ttl = AsyncMock(return_value=999999999)
        service._device_providers = {
            DeviceProviderType.ARCA: mock_provider,
            DeviceProviderType.BAAS: MagicMock(),
        }

        await service.list_paas_device_by_bot(
            bot_id="bot1", entity_id="entity1", statuses=[], env="prod"
        )

        mock_provider.refresh_device_ttl.assert_called_once_with(device_no_ttl)

    @pytest.mark.asyncio
    async def test_refresh_ttl_exception_logged(self) -> None:
        """Exception during TTL refresh should be logged, not propagated."""
        service = _make_service()
        service._query_service.get_bot_binding = MagicMock(
            return_value={
                "bot_type": "personal",
                "binding_id": 10,
                "active_engine": "openclaw",
            }
        )

        from secbaas.community.api.health_check.bot import DeviceProviderType

        device = PaasDeviceInfo(
            paas_device_id="ARCA-SANDBOX-001@0",
            provider_type="ARCA",
            status="online",
            ttl_expiration_timestamp=None,
        )

        mock_provider = MagicMock()
        mock_provider.list_paas_device_by_bot = AsyncMock(return_value=[device])
        mock_provider.refresh_device_ttl = AsyncMock(
            side_effect=RuntimeError("Arca down")
        )
        service._device_providers = {
            DeviceProviderType.ARCA: mock_provider,
            DeviceProviderType.BAAS: MagicMock(),
        }

        # Should not raise
        result = await service.list_paas_device_by_bot(
            bot_id="bot1", entity_id="entity1", statuses=[], env="prod"
        )

        assert len(result.paas_devices) == 1


class TestExtendTtlByBot:
    """Tests for BotHealthCheckerService.extend_ttl_by_bot."""

    @pytest.mark.asyncio
    async def test_personal_device_ttl_extend(self) -> None:
        service = _make_service()
        service._query_service.get_bot_binding = MagicMock(
            return_value={
                "bot_type": "personal",
                "binding_id": 10,
                "active_engine": "openclaw",
            }
        )

        from secbaas.community.api.health_check.bot import DeviceProviderType

        mock_provider = MagicMock()
        mock_provider.extend_ttl_by_bot = AsyncMock(
            return_value=TTLExtendResult(
                bot_id="bot1",
                bot_type="personal",
                total_devices=1,
                extended_count=1,
                skipped_count=0,
                failed_count=0,
                details=[],
            )
        )
        service._device_providers = {
            DeviceProviderType.ARCA: mock_provider,
            DeviceProviderType.BAAS: MagicMock(),
        }

        result = await service.extend_ttl_by_bot(
            bot_id="bot1", entity_id="entity1", env="prod"
        )

        assert result.extended_count == 1

    @pytest.mark.asyncio
    async def test_service_device_ttl_extend(self) -> None:
        service = _make_service()
        service._query_service.get_bot_binding = MagicMock(
            return_value={
                "bot_type": "service",
                "binding_id": 20,
                "active_engine": "openclaw",
            }
        )

        from secbaas.community.api.health_check.bot import DeviceProviderType

        mock_provider = MagicMock()
        mock_provider.extend_ttl_by_bot = AsyncMock(
            return_value=TTLExtendResult(
                bot_id="bot1",
                bot_type="service",
                total_devices=2,
                extended_count=2,
                skipped_count=0,
                failed_count=0,
                details=[],
            )
        )
        service._device_providers = {
            DeviceProviderType.ARCA: MagicMock(),
            DeviceProviderType.BAAS: mock_provider,
        }

        result = await service.extend_ttl_by_bot(
            bot_id="bot1", entity_id="entity1", env="prod"
        )

        assert result.extended_count == 2

    @pytest.mark.asyncio
    async def test_bot_not_found(self) -> None:
        service = _make_service()
        service._query_service.get_bot_binding = MagicMock(return_value=None)

        with pytest.raises(SandboxNotFoundError):
            await service.extend_ttl_by_bot(
                bot_id="nonexistent", entity_id="entity1", env="prod"
            )

    @pytest.mark.asyncio
    async def test_missing_bot_type(self) -> None:
        service = _make_service()
        service._query_service.get_bot_binding = MagicMock(
            return_value={"bot_type": None, "binding_id": 10}
        )

        with pytest.raises(BotHealthCheckerError, match="bot_type is missing"):
            await service.extend_ttl_by_bot(
                bot_id="bot1", entity_id="entity1", env="prod"
            )


class TestCheckHealthByBot:
    """Tests for BotHealthCheckerService.check_health_by_bot."""

    @pytest.mark.asyncio
    async def test_all_devices_healthy(self) -> None:
        service = _make_service()
        service._query_service.get_bot_binding = MagicMock(
            return_value={
                "bot_type": "personal",
                "binding_id": 10,
                "active_engine": "openclaw",
            }
        )

        from secbaas.community.api.health_check.bot import DeviceProviderType

        device = PaasDeviceInfo(
            paas_device_id="ARCA-SANDBOX-001@0",
            provider_type="ARCA",
            status="online",
        )
        mock_provider = MagicMock()
        mock_provider.list_paas_device_by_bot = AsyncMock(return_value=[device])
        service._device_providers = {
            DeviceProviderType.ARCA: mock_provider,
            DeviceProviderType.BAAS: MagicMock(),
        }

        mock_health_provider = MagicMock()
        mock_health_provider.check_health = AsyncMock(
            return_value=PaasHealthCheckerResult(
                paas_device_id="ARCA-SANDBOX-001@0",
                overall_healthy=True,
                checkers={
                    "engine": HealthCheckerStrategyResult(
                        healthy=True,
                        response=None,
                        error=None,
                        timeout=False,
                        duration_ms=10,
                    ),
                },
            )
        )
        service._health_provider_factory.get = MagicMock(
            return_value=mock_health_provider
        )

        result = await service.check_health_by_bot(
            bot_id="bot1", entity_id="entity1", statuses=["online"], env="prod"
        )

        assert isinstance(result, BotHealthCheckResult)
        assert result.overall_healthy is True
        assert result.healthy_count == 1
        assert result.unhealthy_count == 0
        assert len(result.failed_devices) == 0

    @pytest.mark.asyncio
    async def test_some_devices_unhealthy(self) -> None:
        service = _make_service()
        service._query_service.get_bot_binding = MagicMock(
            return_value={
                "bot_type": "personal",
                "binding_id": 10,
                "active_engine": "openclaw",
            }
        )

        from secbaas.community.api.health_check.bot import DeviceProviderType

        healthy_device = PaasDeviceInfo(
            paas_device_id="ARCA-SANDBOX-001@0",
            provider_type="ARCA",
            status="online",
        )
        unhealthy_device = PaasDeviceInfo(
            paas_device_id="ARCA-SANDBOX-002@0",
            provider_type="ARCA",
            status="online",
        )

        mock_provider = MagicMock()
        mock_provider.list_paas_device_by_bot = AsyncMock(
            return_value=[healthy_device, unhealthy_device]
        )
        service._device_providers = {
            DeviceProviderType.ARCA: mock_provider,
            DeviceProviderType.BAAS: MagicMock(),
        }

        async def mock_check_health(
            paas_device_id: str, **kwargs: object
        ) -> PaasHealthCheckerResult:
            if "001" in paas_device_id:
                return PaasHealthCheckerResult(
                    paas_device_id=paas_device_id,
                    overall_healthy=True,
                    checkers={
                        "engine": HealthCheckerStrategyResult(
                            healthy=True,
                            response=None,
                            error=None,
                            timeout=False,
                            duration_ms=5,
                        )
                    },
                )
            return PaasHealthCheckerResult(
                paas_device_id=paas_device_id,
                overall_healthy=False,
                checkers={
                    "engine": HealthCheckerStrategyResult(
                        healthy=False,
                        response=None,
                        error="Engine not running",
                        timeout=False,
                        duration_ms=5,
                    )
                },
            )

        mock_health_provider = MagicMock()
        mock_health_provider.check_health = AsyncMock(side_effect=mock_check_health)
        service._health_provider_factory.get = MagicMock(
            return_value=mock_health_provider
        )

        result = await service.check_health_by_bot(
            bot_id="bot1", entity_id="entity1", statuses=["online"], env="prod"
        )

        assert result.overall_healthy is False
        assert result.healthy_count == 1
        assert result.unhealthy_count == 1
        assert len(result.failed_devices) == 1

    @pytest.mark.asyncio
    async def test_no_devices_raises(self) -> None:
        service = _make_service()
        service._query_service.get_bot_binding = MagicMock(
            return_value={
                "bot_type": "personal",
                "binding_id": 10,
                "active_engine": "openclaw",
            }
        )

        from secbaas.community.api.health_check.bot import DeviceProviderType

        mock_provider = MagicMock()
        mock_provider.list_paas_device_by_bot = AsyncMock(return_value=[])
        service._device_providers = {
            DeviceProviderType.ARCA: mock_provider,
            DeviceProviderType.BAAS: MagicMock(),
        }

        with pytest.raises(SandboxNotFoundError, match="No devices found"):
            await service.check_health_by_bot(
                bot_id="bot1", entity_id="entity1", statuses=[], env="prod"
            )

    @pytest.mark.asyncio
    async def test_skip_device_with_empty_paas_id(self) -> None:
        """Devices with empty paas_device_id are skipped (not checked)."""
        service = _make_service()
        service._query_service.get_bot_binding = MagicMock(
            return_value={
                "bot_type": "personal",
                "binding_id": 10,
                "active_engine": "openclaw",
            }
        )

        from secbaas.community.api.health_check.bot import DeviceProviderType

        device = PaasDeviceInfo(
            paas_device_id="",
            provider_type="ARCA",
            status="unknown",
        )
        mock_provider = MagicMock()
        mock_provider.list_paas_device_by_bot = AsyncMock(return_value=[device])
        service._device_providers = {
            DeviceProviderType.ARCA: mock_provider,
            DeviceProviderType.BAAS: MagicMock(),
        }

        result = await service.check_health_by_bot(
            bot_id="bot1", entity_id="entity1", statuses=[], env="prod"
        )

        assert result.overall_healthy is False
        assert result.unhealthy_count == 1
        assert "paas_device_id is empty" in result.failed_devices[0].error_message

    @pytest.mark.asyncio
    async def test_skip_device_with_none_provider_type(self) -> None:
        """Devices with provider_type=None are skipped."""
        service = _make_service()
        service._query_service.get_bot_binding = MagicMock(
            return_value={
                "bot_type": "personal",
                "binding_id": 10,
                "active_engine": "openclaw",
            }
        )

        from secbaas.community.api.health_check.bot import DeviceProviderType

        device = PaasDeviceInfo(
            paas_device_id="ARCA-SANDBOX-001@0",
            provider_type=None,
            status="online",
        )
        mock_provider = MagicMock()
        mock_provider.list_paas_device_by_bot = AsyncMock(return_value=[device])
        service._device_providers = {
            DeviceProviderType.ARCA: mock_provider,
            DeviceProviderType.BAAS: MagicMock(),
        }

        result = await service.check_health_by_bot(
            bot_id="bot1", entity_id="entity1", statuses=[], env="prod"
        )

        assert result.overall_healthy is False
        assert "provider_type is None" in result.failed_devices[0].error_message

    @pytest.mark.asyncio
    async def test_checker_raises_exception(self) -> None:
        """Exception in health check is caught and recorded."""
        service = _make_service()
        service._query_service.get_bot_binding = MagicMock(
            return_value={
                "bot_type": "personal",
                "binding_id": 10,
                "active_engine": "openclaw",
            }
        )

        from secbaas.community.api.health_check.bot import DeviceProviderType

        device = PaasDeviceInfo(
            paas_device_id="ARCA-SANDBOX-001@0",
            provider_type="ARCA",
            status="online",
        )
        mock_provider = MagicMock()
        mock_provider.list_paas_device_by_bot = AsyncMock(return_value=[device])
        service._device_providers = {
            DeviceProviderType.ARCA: mock_provider,
            DeviceProviderType.BAAS: MagicMock(),
        }

        mock_health_provider = MagicMock()
        mock_health_provider.check_health = AsyncMock(
            side_effect=RuntimeError("Unexpected error in health check")
        )
        service._health_provider_factory.get = MagicMock(
            return_value=mock_health_provider
        )

        result = await service.check_health_by_bot(
            bot_id="bot1", entity_id="entity1", statuses=[], env="prod"
        )

        assert result.overall_healthy is False
        assert result.unhealthy_count == 1
        assert len(result.failed_devices) == 1
        assert "Unexpected error" in result.failed_devices[0].error_message


class TestGetSandboxInfo:
    """Tests for BotHealthCheckerService.get_sandbox_info."""

    @pytest.mark.asyncio
    async def test_found_in_binding_table(self) -> None:
        """Sandbox found in ac_entity_device_binding table."""
        service = _make_service()

        mock_binding = MagicMock()
        mock_binding.device_props = {"sandbox_id": "ARCA-SANDBOX-001@0"}
        mock_binding.status = "online"
        mock_binding.id = 42
        mock_binding.device_provider = "arca"
        mock_binding.env = "prod"

        service._device_binding_repo.get_binding_by_sandbox_id_like = MagicMock(
            return_value=mock_binding
        )

        result = await service.get_sandbox_info("ARCA-SANDBOX-001@0")

        assert result is not None
        assert result["sandbox_id"] == "ARCA-SANDBOX-001"
        assert result["source_table"] == "ac_binding"
        assert result["source_table_id"] == "42"
        assert result["device_provider"] == "arca"
        assert result["env"] == "prod"

    @pytest.mark.asyncio
    async def test_found_in_baas_device_table(self) -> None:
        """Sandbox found in baas_device table (fallback)."""
        service = _make_service()

        service._device_binding_repo.get_binding_by_sandbox_id_like = MagicMock(
            return_value=None
        )

        mock_device = MagicMock()
        mock_device.provider_device_id = "ARCA-SANDBOX-002@0"
        mock_device.status = "online"
        mock_device.id = 99
        mock_device.env = "prod"
        service._device_repo.get_by_provider_device_id_like = MagicMock(
            return_value=mock_device
        )

        result = await service.get_sandbox_info("ARCA-SANDBOX-002@0")

        assert result is not None
        assert result["sandbox_id"] == "ARCA-SANDBOX-002"
        assert result["source_table"] == "baas_device"
        assert result["device_provider"] == "baas"

    @pytest.mark.asyncio
    async def test_not_found(self) -> None:
        """Sandbox not found in either table returns None."""
        service = _make_service()
        service._device_binding_repo.get_binding_by_sandbox_id_like = MagicMock(
            return_value=None
        )
        service._device_repo.get_by_provider_device_id_like = MagicMock(
            return_value=None
        )

        result = await service.get_sandbox_info("nonexistent-sandbox")

        assert result is None

    @pytest.mark.asyncio
    async def test_strips_at_suffix(self) -> None:
        """@0 suffix should be stripped for lookup and results."""
        service = _make_service()

        mock_binding = MagicMock()
        mock_binding.device_props = {}
        mock_binding.status = "online"
        mock_binding.id = 42
        mock_binding.device_provider = "arca"
        mock_binding.env = "prod"

        service._device_binding_repo.get_binding_by_sandbox_id_like = MagicMock(
            return_value=mock_binding
        )

        result = await service.get_sandbox_info("ARCA-SANDBOX-001@0")

        # Should strip @0 suffix
        assert result["sandbox_id"] == "ARCA-SANDBOX-001"
        # Should query with prefix (without @0)
        service._device_binding_repo.get_binding_by_sandbox_id_like.assert_called_with(
            sandbox_id_prefix="ARCA-SANDBOX-001"
        )

    @pytest.mark.asyncio
    async def test_binding_device_props_none(self) -> None:
        """When binding has no device_props, paas_device_id defaults to sandbox_id."""
        service = _make_service()

        mock_binding = MagicMock()
        mock_binding.device_props = None
        mock_binding.status = "online"
        mock_binding.id = 42
        mock_binding.device_provider = "arca"
        mock_binding.env = "prod"

        service._device_binding_repo.get_binding_by_sandbox_id_like = MagicMock(
            return_value=mock_binding
        )

        result = await service.get_sandbox_info("ARCA-SANDBOX-001")

        assert result["sandbox_id"] == "ARCA-SANDBOX-001"

    @pytest.mark.asyncio
    async def test_exception_raises_bot_health_checker_error(self) -> None:
        """Repository exception raises BotHealthCheckerError."""
        service = _make_service()
        service._device_binding_repo.get_binding_by_sandbox_id_like = MagicMock(
            side_effect=RuntimeError("DB connection failed")
        )

        with pytest.raises(BotHealthCheckerError, match="Failed to get sandbox info"):
            await service.get_sandbox_info("ARCA-SANDBOX-001")
