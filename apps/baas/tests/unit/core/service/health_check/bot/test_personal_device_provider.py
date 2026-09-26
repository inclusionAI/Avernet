"""Unit tests for PersonalDeviceProvider."""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from secbaas.community.api.health_check.bot import (
    BotHealthCheckerConfig,
    DeviceProviderType,
    TTLInfo,
)
from secbaas.community.core.service.health_check.bot._personal_device_provider import (
    PersonalDeviceProvider,
)


class TestPersonalDeviceProviderWithQueryService:
    """Tests for PersonalDeviceProvider using the query_service path."""

    @pytest.fixture
    def mock_binding_repo(self) -> MagicMock:
        return MagicMock()

    @pytest.fixture
    def mock_facade(self) -> MagicMock:
        return MagicMock()

    @pytest.fixture
    def mock_query_service(self) -> MagicMock:
        return MagicMock()

    @pytest.fixture
    def config(self) -> BotHealthCheckerConfig:
        return BotHealthCheckerConfig()

    @pytest.fixture
    def provider(
        self,
        mock_binding_repo: MagicMock,
        mock_facade: MagicMock,
        config: BotHealthCheckerConfig,
        mock_query_service: MagicMock,
    ) -> PersonalDeviceProvider:
        return PersonalDeviceProvider(
            mock_binding_repo, mock_facade, config, mock_query_service
        )

    @pytest.mark.asyncio
    async def test_list_paas_device_by_bot_uses_query_service(
        self, provider, mock_query_service
    ) -> None:
        """When query_service is injected, it should be used instead of binding_repo."""
        mock_query_service.list_paas_device_by_bot_personal.return_value = [
            {
                "paas_device_id": "ARCA-SANDBOX-001@0",
                "device_uuid": None,
                "provider_type": "ARCA",
                "status": "ACTIVE",
                "query_status": "personal",
                "ttl_expiration_time": "2024-01-02 12:00:00",
                "ttl_expiration_timestamp": 1704196800000,
                "source_table": "ac_binding",
                "source_table_id": 10,
                "refresh_fail_count": 0,
            }
        ]

        devices = await provider.list_paas_device_by_bot(
            bot_id="bot1", entity_id="entity1", binding_id=10
        )

        assert len(devices) == 1
        assert devices[0].paas_device_id == "ARCA-SANDBOX-001@0"
        assert devices[0].query_status == "personal"
        mock_query_service.list_paas_device_by_bot_personal.assert_called_once_with(
            bot_id="bot1", binding_id=10
        )

    @pytest.mark.asyncio
    async def test_list_paas_device_by_bot_returns_query_status_personal(
        self, provider, mock_query_service
    ) -> None:
        """query_status should be 'personal' for personal bot devices."""
        mock_query_service.list_paas_device_by_bot_personal.return_value = [
            {
                "paas_device_id": "ARCA-SANDBOX-002@0",
                "device_uuid": None,
                "provider_type": "arca",
                "status": "ACTIVE",
                "query_status": "personal",
                "ttl_expiration_time": None,
                "ttl_expiration_timestamp": None,
                "source_table": "ac_binding",
                "source_table_id": 20,
                "refresh_fail_count": 0,
            }
        ]

        devices = await provider.list_paas_device_by_bot(
            bot_id="bot2", entity_id="entity2", binding_id=20
        )

        assert len(devices) == 1
        assert devices[0].query_status == "personal"

    @pytest.mark.asyncio
    async def test_list_paas_device_by_bot_empty_when_not_active(
        self, provider, mock_query_service
    ) -> None:
        """When query_service returns empty (non-ACTIVE binding filtered out), result is empty."""
        mock_query_service.list_paas_device_by_bot_personal.return_value = []

        devices = await provider.list_paas_device_by_bot(
            bot_id="bot1", entity_id="entity1", binding_id=10
        )

        assert devices == []


class TestPersonalDeviceProvider:
    """Tests for PersonalDeviceProvider."""

    @pytest.fixture
    def mock_binding_repo(self) -> MagicMock:
        return MagicMock()

    @pytest.fixture
    def mock_facade(self) -> MagicMock:
        return MagicMock()

    @pytest.fixture
    def config(self) -> BotHealthCheckerConfig:
        return BotHealthCheckerConfig()

    @pytest.fixture
    def provider(
        self,
        mock_binding_repo: MagicMock,
        mock_facade: MagicMock,
        config: BotHealthCheckerConfig,
    ) -> PersonalDeviceProvider:
        return PersonalDeviceProvider(mock_binding_repo, mock_facade, config)

    def test_provider_type(self, provider: PersonalDeviceProvider) -> None:
        assert provider.provider_type == DeviceProviderType.ARCA

    @pytest.mark.asyncio
    async def test_list_paas_device_by_bot(
        self, provider: PersonalDeviceProvider, mock_binding_repo: MagicMock
    ) -> None:
        mock_binding_repo.list_paas_device_by_bot_personal.return_value = [
            {
                "paas_device_id": "ARCA-SANDBOX-001@0",
                "device_uuid": None,
                "provider_type": "ARCA",
                "status": "online",
                "ttl_expiration_time": "2024-01-02 12:00:00",
                "ttl_expiration_timestamp": 1704196800000,
                "source_table": "ac_binding",
                "source_table_id": 10,
                "refresh_fail_count": 0,
            }
        ]

        devices = await provider.list_paas_device_by_bot(
            bot_id="bot1", entity_id="entity1", binding_id=10
        )

        assert len(devices) == 1
        assert devices[0].paas_device_id == "ARCA-SANDBOX-001@0"
        assert devices[0].provider_type == "ARCA"

    @pytest.mark.asyncio
    async def test_extend_ttl_no_devices(
        self, provider: PersonalDeviceProvider, mock_binding_repo: MagicMock
    ) -> None:
        mock_binding_repo.list_paas_device_by_bot_personal.return_value = []

        result = await provider.extend_ttl_by_bot(
            bot_id="bot1", entity_id="entity1", binding_id=10
        )

        assert result.total_devices == 0
        assert result.error == "No devices found"

    @pytest.mark.asyncio
    async def test_extend_ttl_skipped_due_to_sufficient_ttl(
        self,
        provider: PersonalDeviceProvider,
        mock_facade: MagicMock,
        mock_binding_repo: MagicMock,
    ) -> None:
        import time

        far_ts = int((time.time() + 20 * 3600) * 1000)  # 20 hours away

        mock_binding_repo.list_paas_device_by_bot_personal.return_value = [
            {
                "paas_device_id": "ARCA-SANDBOX-001@0",
                "provider_type": "ARCA",
                "status": "online",
                "ttl_expiration_timestamp": far_ts,
                "ttl_expiration_time": "2024-01-02 12:00:00",
                "source_table": "ac_binding",
                "source_table_id": 10,
                "refresh_fail_count": 0,
            }
        ]

        result = await provider.extend_ttl_by_bot(
            bot_id="bot1", entity_id="entity1", binding_id=10
        )

        assert result.total_devices == 1
        assert result.extended_count == 0
        assert result.skipped_count == 1  # TTL still sufficient
        assert result.failed_count == 0

    @pytest.mark.asyncio
    async def test_extend_ttl_success(
        self,
        provider: PersonalDeviceProvider,
        mock_facade: MagicMock,
        mock_binding_repo: MagicMock,
    ) -> None:
        import time

        expired_ts = int((time.time() - 3600) * 1000)  # expired 1 hour ago

        mock_binding_repo.list_paas_device_by_bot_personal.return_value = [
            {
                "paas_device_id": "ARCA-SANDBOX-001@0",
                "provider_type": "ARCA",
                "status": "online",
                "ttl_expiration_timestamp": expired_ts,
                "ttl_expiration_time": "2024-01-01 11:00:00",
                "source_table": "ac_binding",
                "source_table_id": 10,
                "refresh_fail_count": 0,
            }
        ]

        mock_ttl_info = TTLInfo(
            paas_device_id="ARCA-SANDBOX-001@0",
            old_expiration_time=datetime.fromtimestamp(expired_ts / 1000),
            new_expiration_time=datetime(2024, 1, 2, 12, 0, 0),
            success=True,
        )
        mock_facade.update_device_ttl = AsyncMock(return_value=mock_ttl_info)

        result = await provider.extend_ttl_by_bot(
            bot_id="bot1", entity_id="entity1", binding_id=10
        )

        assert result.total_devices == 1
        assert result.extended_count == 1
        assert result.error is None
        mock_binding_repo.update_device_props_ttl.assert_called_once()

    @pytest.mark.asyncio
    async def test_extend_ttl_failure(
        self,
        provider: PersonalDeviceProvider,
        mock_facade: MagicMock,
        mock_binding_repo: MagicMock,
    ) -> None:
        import time

        expired_ts = int((time.time() - 3600) * 1000)

        mock_binding_repo.list_paas_device_by_bot_personal.return_value = [
            {
                "paas_device_id": "ARCA-SANDBOX-001@0",
                "provider_type": "ARCA",
                "status": "online",
                "ttl_expiration_timestamp": expired_ts,
                "ttl_expiration_time": "2024-01-01 11:00:00",
                "source_table": "ac_binding",
                "source_table_id": 10,
                "refresh_fail_count": 0,
            }
        ]

        mock_ttl_info = TTLInfo(
            paas_device_id="ARCA-SANDBOX-001@0",
            old_expiration_time=datetime.fromtimestamp(expired_ts / 1000),
            new_expiration_time=None,
            success=False,
            error="Rate limit exceeded",
        )
        mock_facade.update_device_ttl = AsyncMock(return_value=mock_ttl_info)

        result = await provider.extend_ttl_by_bot(
            bot_id="bot1", entity_id="entity1", binding_id=10
        )

        assert result.total_devices == 1
        assert result.extended_count == 0
        assert result.failed_count == 1
        assert result.error is not None
        mock_binding_repo.update_device_props_refresh_fail_count.assert_called_once()

    @pytest.mark.asyncio
    async def test_extend_ttl_exception(
        self,
        provider: PersonalDeviceProvider,
        mock_facade: MagicMock,
        mock_binding_repo: MagicMock,
    ) -> None:
        import time

        expired_ts = int((time.time() - 3600) * 1000)

        mock_binding_repo.list_paas_device_by_bot_personal.return_value = [
            {
                "paas_device_id": "ARCA-SANDBOX-001@0",
                "provider_type": "ARCA",
                "status": "online",
                "ttl_expiration_timestamp": expired_ts,
                "ttl_expiration_time": "2024-01-01 11:00:00",
                "source_table": "ac_binding",
                "source_table_id": 10,
                "refresh_fail_count": 0,
            }
        ]

        mock_facade.update_device_ttl = AsyncMock(
            side_effect=RuntimeError("Arca unavailable")
        )

        result = await provider.extend_ttl_by_bot(
            bot_id="bot1", entity_id="entity1", binding_id=10
        )

        assert result.total_devices == 1
        assert result.extended_count == 0
        assert result.failed_count == 1
        assert result.error is not None
        mock_binding_repo.update_device_props_refresh_fail_count.assert_called_once()

    @pytest.mark.asyncio
    async def test_extend_ttl_skip_empty_paas_device_id(
        self, provider: PersonalDeviceProvider, mock_binding_repo: MagicMock
    ) -> None:
        mock_binding_repo.list_paas_device_by_bot_personal.return_value = [
            {
                "paas_device_id": "",
                "provider_type": "ARCA",
                "status": "unknown",
                "source_table": "ac_binding",
                "source_table_id": 10,
                "refresh_fail_count": 0,
            }
        ]

        result = await provider.extend_ttl_by_bot(
            bot_id="bot1", entity_id="entity1", binding_id=10
        )

        assert result.total_devices == 1
        assert result.skipped_count == 1
        assert result.failed_count == 0
