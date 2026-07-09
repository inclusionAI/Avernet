"""Unit tests for DeviceSourceProvider base class."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from secbaas.api.health_check.bot import (
    BotHealthCheckerConfig,
    DeviceProviderType,
    PaasDeviceInfo,
)
from secbaas.core.service.health_check.bot._device_source_provider import (
    DeviceSourceProvider,
)


class TestDeviceSourceProviderBase:
    """Tests for DeviceSourceProvider abstract methods and shared logic."""

    @pytest.fixture
    def mock_binding_repo(self) -> MagicMock:
        return MagicMock()

    @pytest.fixture
    def mock_facade(self) -> MagicMock:
        return MagicMock()

    @pytest.fixture
    def config(self) -> BotHealthCheckerConfig:
        return BotHealthCheckerConfig(
            extend_when_remaining_hours=16,
            target_ttl_hours=24,
        )

    @pytest.fixture
    def provider(
        self,
        mock_binding_repo: MagicMock,
        mock_facade: MagicMock,
        config: BotHealthCheckerConfig,
    ) -> DeviceSourceProvider:
        """Concrete subclass for testing base class logic."""

        class TestProvider(DeviceSourceProvider):
            @property
            def provider_type(self) -> DeviceProviderType:
                return DeviceProviderType.ARCA

            async def list_all_active_bot_device(
                self, bot_type=None, page=1, page_size=20, env="prod"
            ):
                return 0, []

            async def list_paas_device_by_bot(self, bot_id, entity_id, **kwargs):
                return []

            async def extend_ttl_by_bot(self, bot_id, entity_id, binding_id=None):
                from secbaas.api.health_check.bot import TTLExtendResult

                return TTLExtendResult(
                    bot_id=bot_id,
                    bot_type="test",
                    total_devices=0,
                    extended_count=0,
                    skipped_count=0,
                    failed_count=0,
                    details=[],
                )

        return TestProvider(mock_binding_repo, mock_facade, config)

    # --- _should_extend_ttl tests ---

    def test_should_extend_ttl_when_none(self, provider: DeviceSourceProvider) -> None:
        """None TTL should always require extension."""
        assert provider._should_extend_ttl(None) is True

    def test_should_extend_ttl_when_expired(
        self, provider: DeviceSourceProvider
    ) -> None:
        """Expired TTL should require extension."""
        import time

        expired_ts = int((time.time() - 3600) * 1000)  # 1 hour ago
        assert provider._should_extend_ttl(expired_ts) is True

    def test_should_extend_ttl_when_about_to_expire(
        self, provider: DeviceSourceProvider
    ) -> None:
        """TTL within the extend window should require extension."""
        import time

        # 10 hours from now (less than 16 hour threshold)
        soon_ts = int((time.time() + 10 * 3600) * 1000)
        assert provider._should_extend_ttl(soon_ts) is True

    def test_should_extend_ttl_when_far_enough(
        self, provider: DeviceSourceProvider
    ) -> None:
        """TTL beyond the extend window should not require extension."""
        import time

        # 20 hours from now (more than 16 hour threshold)
        far_ts = int((time.time() + 20 * 3600) * 1000)
        assert provider._should_extend_ttl(far_ts) is False

    # --- refresh_device_ttl tests ---

    @pytest.mark.asyncio
    async def test_refresh_ttl_empty_paas_device_id(
        self, provider: DeviceSourceProvider
    ) -> None:
        """refresh_device_ttl returns None for empty paas_device_id."""
        device = PaasDeviceInfo(
            paas_device_id="", provider_type="ARCA", status="online"
        )
        result = await provider.refresh_device_ttl(device)
        assert result is None

    @pytest.mark.asyncio
    async def test_refresh_ttl_non_arca_returns_none(
        self, provider: DeviceSourceProvider
    ) -> None:
        """Non-ARCA devices skip refresh."""
        device = PaasDeviceInfo(
            paas_device_id="SIGMA-DEVICE-001", provider_type="SIGMA", status="online"
        )
        result = await provider.refresh_device_ttl(device)
        assert result is None

    @pytest.mark.asyncio
    async def test_refresh_ttl_success(
        self,
        provider: DeviceSourceProvider,
        mock_facade: MagicMock,
        mock_binding_repo: MagicMock,
    ) -> None:
        """Successful TTL refresh from Arca updates device and DB."""
        import time

        ttl_ts = int((time.time() + 24 * 3600) * 1000)

        mock_device_info = MagicMock()
        mock_device_info.ttl_timestamp = ttl_ts
        mock_facade.get_device_info = AsyncMock(return_value=mock_device_info)

        device = PaasDeviceInfo(
            paas_device_id="ARCA-SANDBOX-123@0",
            provider_type="ARCA",
            status="online",
            source_table="ac_binding",
            source_table_id="42",
        )

        result = await provider.refresh_device_ttl(device)

        assert result == ttl_ts
        assert device.ttl_expiration_timestamp == ttl_ts
        assert device.refresh_fail_count == 0
        mock_binding_repo.update_device_props_ttl.assert_called_once()

    @pytest.mark.asyncio
    async def test_refresh_ttl_device_info_none(
        self,
        provider: DeviceSourceProvider,
        mock_facade: MagicMock,
        mock_binding_repo: MagicMock,
    ) -> None:
        """When get_device_info returns None, refresh_fail_count increments."""
        mock_facade.get_device_info = AsyncMock(return_value=None)

        device = PaasDeviceInfo(
            paas_device_id="ARCA-SANDBOX-123@0",
            provider_type="ARCA",
            status="online",
            source_table="ac_binding",
            source_table_id="42",
        )

        result = await provider.refresh_device_ttl(device)

        assert result is None
        assert device.refresh_fail_count == 1
        mock_binding_repo.update_device_props_refresh_fail_count.assert_called_once()

    @pytest.mark.asyncio
    async def test_refresh_ttl_exception(
        self,
        provider: DeviceSourceProvider,
        mock_facade: MagicMock,
        mock_binding_repo: MagicMock,
    ) -> None:
        """Exception in get_device_info increments refresh_fail_count."""
        mock_facade.get_device_info = AsyncMock(
            side_effect=RuntimeError("Arca unavailable")
        )

        device = PaasDeviceInfo(
            paas_device_id="ARCA-SANDBOX-123@0",
            provider_type="ARCA",
            status="online",
            source_table="ac_binding",
            source_table_id="42",
        )

        result = await provider.refresh_device_ttl(device)

        assert result is None
        assert device.refresh_fail_count == 1
        mock_binding_repo.update_device_props_refresh_fail_count.assert_called_once()

    # --- _update_device_ttl_to_db tests ---

    def test_update_ttl_db_no_source_table(
        self, provider: DeviceSourceProvider, mock_binding_repo: MagicMock
    ) -> None:
        """_update_device_ttl_to_db silently skips when no source_table."""
        device = PaasDeviceInfo(
            paas_device_id="ARCA-SANDBOX-123@0", provider_type="ARCA", status="online"
        )
        provider._update_device_ttl_to_db(
            device,
            ttl_expiration_timestamp=123456,
            ttl_expiration_time="2024-01-01 12:00:00",
        )
        mock_binding_repo.update_device_props_ttl.assert_not_called()

    def test_update_ttl_db_ac_binding(
        self, provider: DeviceSourceProvider, mock_binding_repo: MagicMock
    ) -> None:
        """_update_device_ttl_to_db routes to ac_binding update."""
        device = PaasDeviceInfo(
            paas_device_id="ARCA-SANDBOX-123@0",
            provider_type="ARCA",
            status="online",
            source_table="ac_binding",
            source_table_id="42",
        )
        provider._update_device_ttl_to_db(
            device,
            ttl_expiration_timestamp=123456,
            ttl_expiration_time="2024-01-01 12:00:00",
            refresh_fail_count=0,
        )
        mock_binding_repo.update_device_props_ttl.assert_called_with(
            binding_id=42,
            ttl_expiration_timestamp=123456,
            ttl_expiration_time="2024-01-01 12:00:00",
            refresh_fail_count=0,
        )

    def test_update_ttl_db_baas_device(
        self, provider: DeviceSourceProvider, mock_binding_repo: MagicMock
    ) -> None:
        """_update_device_ttl_to_db routes to baas_device update."""
        device = PaasDeviceInfo(
            paas_device_id="ARCA-SANDBOX-123@0",
            provider_type="ARCA",
            status="online",
            source_table="baas_device",
            source_table_id="99",
        )
        provider._update_device_ttl_to_db(
            device,
            ttl_expiration_timestamp=123456,
            ttl_expiration_time="2024-01-01 12:00:00",
            refresh_fail_count=0,
        )
        mock_binding_repo.update_baas_device_ttl_by_id.assert_called_with(
            baas_device_id=99,
            ttl_expiration_time="2024-01-01 12:00:00",
            ttl_expiration_timestamp=123456,
            refresh_fail_count=0,
        )

    # --- _update_device_refresh_fail_count_to_db tests ---

    def test_update_fail_count_no_source(
        self, provider: DeviceSourceProvider, mock_binding_repo: MagicMock
    ) -> None:
        """_update_device_refresh_fail_count_to_db silently skips when no source_table."""
        device = PaasDeviceInfo(
            paas_device_id="ARCA-SANDBOX-123@0", provider_type="ARCA", status="online"
        )
        provider._update_device_refresh_fail_count_to_db(device)
        mock_binding_repo.update_device_props_refresh_fail_count.assert_not_called()

    def test_update_fail_count_ac_binding(
        self, provider: DeviceSourceProvider, mock_binding_repo: MagicMock
    ) -> None:
        device = PaasDeviceInfo(
            paas_device_id="ARCA-SANDBOX-123@0",
            provider_type="ARCA",
            status="online",
            source_table="ac_binding",
            source_table_id="42",
            refresh_fail_count=3,
        )
        provider._update_device_refresh_fail_count_to_db(device)
        mock_binding_repo.update_device_props_refresh_fail_count.assert_called_with(
            binding_id=42, refresh_fail_count=3
        )

    def test_update_fail_count_baas_device(
        self, provider: DeviceSourceProvider, mock_binding_repo: MagicMock
    ) -> None:
        device = PaasDeviceInfo(
            paas_device_id="ARCA-SANDBOX-123@0",
            provider_type="ARCA",
            status="online",
            source_table="baas_device",
            source_table_id="99",
            refresh_fail_count=5,
        )
        provider._update_device_refresh_fail_count_to_db(device)
        mock_binding_repo.update_baas_device_refresh_fail_count_by_id.assert_called_with(
            baas_device_id=99, refresh_fail_count=5
        )
