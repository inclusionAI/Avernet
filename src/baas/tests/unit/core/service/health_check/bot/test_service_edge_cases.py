"""Unit tests for BotHealthCheckerService edge cases and error handling.

Tests for list_all_active_bot_device with various bot_type filters,
Missing bot_type errors, and exception propagation.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from secbaas.community.api.health_check.bot import (
    BotHealthCheckerConfig,
    BotHealthCheckerError,
    DeviceAliveStatus,
    PaasDeviceInfo,
    SandboxNotFoundError,
    UnsupportedDeviceProviderError,
)
from secbaas.community.core.service.health_check.bot._service import (
    BotHealthCheckerService,
)


def _make_service(
    mock_binding_repo: MagicMock | None = None,
    mock_device_repo: MagicMock | None = None,
    mock_paas_facade: MagicMock | None = None,
    mock_health_factory: MagicMock | None = None,
) -> BotHealthCheckerService:
    return BotHealthCheckerService(
        device_binding_repo=mock_binding_repo or MagicMock(),
        device_repo=mock_device_repo or MagicMock(),
        paas_facade=mock_paas_facade or MagicMock(),
        config=BotHealthCheckerConfig(),
        health_provider_factory=mock_health_factory or MagicMock(),
    )


class TestListAllActiveBotDevice:
    """Tests for BotHealthCheckerService.list_all_active_bot_device."""

    @pytest.mark.asyncio
    async def test_all_types_no_filter(self) -> None:
        """bot_type=None queries Repository without filter."""
        service = _make_service()

        service._device_binding_repo.list_all_active_bot_device = MagicMock(
            return_value=(
                3,
                [
                    {
                        "bot_id": "b1",
                        "entity_id": "e1",
                        "binding_id": 10,
                        "bot_type": "personal",
                        "status": "ACTIVE",
                        "active_engine": "openclaw",
                    },
                    {
                        "bot_id": "b2",
                        "entity_id": "e2",
                        "binding_id": 20,
                        "bot_type": "service",
                        "status": "ACTIVE",
                        "active_engine": "openclaw",
                    },
                    {
                        "bot_id": "b3",
                        "entity_id": "e3",
                        "binding_id": None,
                        "bot_type": "personal",
                        "status": "ACTIVE",
                        "active_engine": None,
                    },
                ],
            )
        )

        total, items = await service.list_all_active_bot_device(
            bot_type=None, page=1, page_size=20, env="prod"
        )

        assert total == 3
        assert len(items) == 3
        service._device_binding_repo.list_all_active_bot_device.assert_called_with(
            page=1, page_size=20, env="prod", bot_type=None
        )

    @pytest.mark.asyncio
    async def test_personal_filter(self) -> None:
        """bot_type='personal' queries Repository with filter."""
        service = _make_service()

        service._device_binding_repo.list_all_active_bot_device = MagicMock(
            return_value=(
                1,
                [
                    {
                        "bot_id": "b1",
                        "entity_id": "e1",
                        "binding_id": 10,
                        "bot_type": "personal",
                        "status": "ACTIVE",
                        "active_engine": "openclaw",
                    }
                ],
            )
        )

        total, items = await service.list_all_active_bot_device(
            bot_type="personal", page=1, page_size=20, env="prod"
        )

        assert total == 1
        assert len(items) == 1
        service._device_binding_repo.list_all_active_bot_device.assert_called_with(
            page=1, page_size=20, env="prod", bot_type="personal"
        )

    @pytest.mark.asyncio
    async def test_service_filter(self) -> None:
        """bot_type='service' queries Repository with filter."""
        service = _make_service()

        service._device_binding_repo.list_all_active_bot_device = MagicMock(
            return_value=(
                2,
                [
                    {
                        "bot_id": "b1",
                        "entity_id": "e1",
                        "binding_id": 10,
                        "bot_type": "service",
                        "status": "ACTIVE",
                        "active_engine": "openclaw",
                    },
                    {
                        "bot_id": "b2",
                        "entity_id": "e2",
                        "binding_id": 20,
                        "bot_type": "service",
                        "status": "ACTIVE",
                        "active_engine": "aicoding",
                    },
                ],
            )
        )

        total, items = await service.list_all_active_bot_device(
            bot_type="service", page=1, page_size=20, env="prod"
        )

        assert total == 2
        assert len(items) == 2
        service._device_binding_repo.list_all_active_bot_device.assert_called_with(
            page=1, page_size=20, env="prod", bot_type="service"
        )

    @pytest.mark.asyncio
    async def test_exception_raises_bot_health_checker_error(self) -> None:
        """Repository exception wraps into BotHealthCheckerError."""
        service = _make_service()

        service._device_binding_repo.list_all_active_bot_device = MagicMock(
            side_effect=RuntimeError("DB error")
        )

        with pytest.raises(
            BotHealthCheckerError, match="Failed to list active bot devices"
        ):
            await service.list_all_active_bot_device(
                bot_type=None, page=1, page_size=20, env="prod"
            )


class TestEdgeCases:
    """Edge case and error handling tests."""

    @pytest.mark.asyncio
    async def test_check_health_bot_not_found(self) -> None:
        service = _make_service()
        service._device_binding_repo.get_bot_binding = MagicMock(return_value=None)

        with pytest.raises(SandboxNotFoundError):
            await service.check_health_by_bot(
                bot_id="nonexistent", entity_id="entity1", statuses=[], env="prod"
            )

    @pytest.mark.asyncio
    async def test_check_alive_bot_not_found(self) -> None:
        service = _make_service()
        service._device_binding_repo.get_bot_binding = MagicMock(return_value=None)

        with pytest.raises(SandboxNotFoundError):
            await service.check_alive_by_bot(
                bot_id="nonexistent", entity_id="entity1", minutes=1440, env="prod"
            )

    @pytest.mark.asyncio
    async def test_check_health_missing_bot_type(self) -> None:
        service = _make_service()
        service._device_binding_repo.get_bot_binding = MagicMock(
            return_value={
                "bot_type": None,
                "binding_id": 10,
                "active_engine": "openclaw",
            }
        )

        with pytest.raises(BotHealthCheckerError, match="bot_type is missing"):
            await service.check_health_by_bot(
                bot_id="bot1", entity_id="entity1", statuses=[], env="prod"
            )

    @pytest.mark.asyncio
    async def test_check_alive_missing_bot_type(self) -> None:
        service = _make_service()
        service._device_binding_repo.get_bot_binding = MagicMock(
            return_value={
                "bot_type": None,
                "binding_id": 10,
                "active_engine": "openclaw",
            }
        )

        with pytest.raises(BotHealthCheckerError, match="bot_type is missing"):
            await service.check_alive_by_bot(
                bot_id="bot1", entity_id="entity1", minutes=1440, env="prod"
            )

    @pytest.mark.asyncio
    async def test_extend_ttl_general_exception(self) -> None:
        """Non-sandbox exceptions wrap into BotHealthCheckerError."""
        service = _make_service()
        service._device_binding_repo.get_bot_binding = MagicMock(
            side_effect=RuntimeError("Unexpected DB error")
        )

        with pytest.raises(BotHealthCheckerError, match="Failed to extend TTL"):
            await service.extend_ttl_by_bot(
                bot_id="bot1", entity_id="entity1", env="prod"
            )

    @pytest.mark.asyncio
    async def test_check_health_general_exception(self) -> None:
        """Non-sandbox exceptions wrap into BotHealthCheckerError."""
        service = _make_service()
        service._device_binding_repo.get_bot_binding = MagicMock(
            side_effect=RuntimeError("Unexpected error")
        )

        with pytest.raises(BotHealthCheckerError, match="Failed to check health"):
            await service.check_health_by_bot(
                bot_id="bot1", entity_id="entity1", statuses=[], env="prod"
            )

    @pytest.mark.asyncio
    async def test_check_alive_general_exception(self) -> None:
        """Non-sandbox exceptions wrap into BotHealthCheckerError."""
        service = _make_service()
        service._device_binding_repo.get_bot_binding = MagicMock(
            side_effect=RuntimeError("Unexpected error")
        )

        with pytest.raises(BotHealthCheckerError, match="Failed to check alive"):
            await service.check_alive_by_bot(
                bot_id="bot1", entity_id="entity1", minutes=1440, env="prod"
            )

    @pytest.mark.asyncio
    async def test_missing_identity_empty_paas_device_id_is_error(self) -> None:
        """paas_device_id 为空时 status=ERROR（task 5.3/5.9 补充）。"""
        service = _make_service()
        service._device_binding_repo.get_bot_binding = MagicMock(
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
            bot_id="bot1", entity_id="entity1", minutes=1440, env="prod"
        )
        assert result.devices[0].status == DeviceAliveStatus.ERROR
        assert "paas_device_id is empty" in result.devices[0].error

    @pytest.mark.asyncio
    async def test_missing_identity_none_provider_type_is_error(self) -> None:
        """provider_type 为 None 时 status=ERROR（task 5.3/5.9 补充）。"""
        service = _make_service()
        service._device_binding_repo.get_bot_binding = MagicMock(
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
            bot_id="bot1", entity_id="entity1", minutes=1440, env="prod"
        )
        assert result.devices[0].status == DeviceAliveStatus.ERROR
        assert "provider_type is None" in result.devices[0].error

    @pytest.mark.asyncio
    async def test_list_paas_device_general_exception(self) -> None:
        """Non-sandbox exceptions wrap into BotHealthCheckerError."""
        service = _make_service()
        service._device_binding_repo.get_bot_binding = MagicMock(
            side_effect=RuntimeError("Unexpected error")
        )

        with pytest.raises(BotHealthCheckerError, match="Failed to list paas devices"):
            await service.list_paas_device_by_bot(
                bot_id="bot1", entity_id="entity1", statuses=[], env="prod"
            )


class TestExceptionsModule:
    """Tests for the exceptions module types."""

    def test_partial_success_error(self) -> None:
        from secbaas.community.api.health_check.bot import PartialSuccessError

        err = PartialSuccessError(
            success_count=3, failed_count=2, errors=["err1", "err2"]
        )
        assert err.success_count == 3
        assert err.failed_count == 2
        assert err.errors == ["err1", "err2"]
        assert "Partial success" in str(err)
        assert "3 succeeded" in str(err)

    def test_exception_hierarchy(self) -> None:
        from secbaas.community.api.health_check.bot import (
            BotHealthCheckerError,
            HealthCheckError,
            HealthCheckTimeoutError,
            SandboxNotFoundError,
            TTLExtendFailedError,
        )

        assert issubclass(SandboxNotFoundError, BotHealthCheckerError)
        assert issubclass(UnsupportedDeviceProviderError, BotHealthCheckerError)
        assert issubclass(HealthCheckError, BotHealthCheckerError)
        assert issubclass(HealthCheckTimeoutError, HealthCheckError)
        assert issubclass(TTLExtendFailedError, BotHealthCheckerError)
