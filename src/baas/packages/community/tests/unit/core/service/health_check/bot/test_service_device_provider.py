"""Unit tests for ServiceDeviceProvider."""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from secbaas.api.health_check.bot import (
    BotHealthCheckerConfig,
    DeviceProviderType,
    TTLInfo,
)
from secbaas.core.service.health_check.bot._service_device_provider import (
    ServiceDeviceProvider,
)


class TestServiceDeviceProviderWithQueryService:
    """Tests for ServiceDeviceProvider using the query_service path."""

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
    ) -> ServiceDeviceProvider:
        return ServiceDeviceProvider(
            mock_binding_repo, mock_facade, config, mock_query_service
        )

    @pytest.mark.asyncio
    async def test_list_paas_device_by_bot_uses_query_service(
        self, provider, mock_query_service
    ) -> None:
        """When query_service is injected, it should be used instead of binding_repo."""
        mock_query_service.list_paas_device_by_bot_service.return_value = [
            {
                "paas_device_id": "ARCA-SANDBOX-001@0",
                "device_uuid": "uuid-123",
                "provider_type": "ARCA",
                "status": "ACTIVE",
                "query_status": "online",
                "ttl_expiration_time": "2024-01-02 12:00:00",
                "ttl_expiration_timestamp": 1704196800000,
                "source_table": "baas_device",
                "source_table_id": "99",
                "refresh_fail_count": 0,
            }
        ]

        devices = await provider.list_paas_device_by_bot(
            bot_id="bot1", entity_id="entity1", statuses=["online"], env="prod"
        )

        assert len(devices) == 1
        assert devices[0].paas_device_id == "ARCA-SANDBOX-001@0"
        assert devices[0].query_status == "online"
        mock_query_service.list_paas_device_by_bot_service.assert_called_once_with(
            bot_id="bot1", entity_id="entity1", statuses=["online"], env="prod"
        )

    @pytest.mark.asyncio
    async def test_list_paas_device_by_bot_query_service_draft(
        self, provider, mock_query_service
    ) -> None:
        """Draft devices returned via query_service should have query_status='draft'."""
        mock_query_service.list_paas_device_by_bot_service.return_value = [
            {
                "paas_device_id": "ARCA-SANDBOX-DRAFT-001@0",
                "device_uuid": None,
                "provider_type": "arca",
                "status": "ACTIVE",
                "query_status": "draft",
                "ttl_expiration_time": None,
                "ttl_expiration_timestamp": None,
                "source_table": "ac_binding",
                "source_table_id": "100",
                "refresh_fail_count": 0,
            }
        ]

        devices = await provider.list_paas_device_by_bot(
            bot_id="bot1", entity_id="entity1", statuses=["draft"], env="prod"
        )

        assert len(devices) == 1
        assert devices[0].query_status == "draft"
        assert devices[0].source_table == "ac_binding"

    @pytest.mark.asyncio
    async def test_list_paas_device_by_bot_query_service_online(
        self, provider, mock_query_service
    ) -> None:
        """Online devices returned via query_service should have query_status='online'."""
        mock_query_service.list_paas_device_by_bot_service.return_value = [
            {
                "paas_device_id": "ARCA-SANDBOX-ONLINE-001@0",
                "device_uuid": "uuid-456",
                "provider_type": "ARCA",
                "status": "ACTIVE",
                "query_status": "online",
                "ttl_expiration_time": "2024-01-02 12:00:00",
                "ttl_expiration_timestamp": 1704196800000,
                "source_table": "baas_device",
                "source_table_id": "200",
                "refresh_fail_count": 0,
            }
        ]

        devices = await provider.list_paas_device_by_bot(
            bot_id="bot1", entity_id="entity1", statuses=["online"], env="prod"
        )

        assert len(devices) == 1
        assert devices[0].query_status == "online"
        assert devices[0].source_table == "baas_device"


class TestServiceDeviceProvider:
    """Tests for ServiceDeviceProvider."""

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
    ) -> ServiceDeviceProvider:
        return ServiceDeviceProvider(mock_binding_repo, mock_facade, config)

    def test_provider_type(self, provider: ServiceDeviceProvider) -> None:
        assert provider.provider_type == DeviceProviderType.BAAS

    @pytest.mark.asyncio
    async def test_list_paas_device_by_bot(
        self, provider: ServiceDeviceProvider, mock_binding_repo: MagicMock
    ) -> None:
        mock_binding_repo.list_paas_device_by_bot_service.return_value = [
            {
                "paas_device_id": "ARCA-SANDBOX-001@0",
                "device_uuid": "uuid-123",
                "provider_type": "ARCA",
                "status": "online",
                "query_status": "online",
                "ttl_expiration_time": "2024-01-02 12:00:00",
                "ttl_expiration_timestamp": 1704196800000,
                "source_table": "baas_device",
                "source_table_id": "99",
                "refresh_fail_count": 0,
            }
        ]

        devices = await provider.list_paas_device_by_bot(
            bot_id="bot1", entity_id="entity1", statuses=["online"]
        )

        assert len(devices) == 1
        assert devices[0].paas_device_id == "ARCA-SANDBOX-001@0"
        assert devices[0].provider_type == "ARCA"
        assert devices[0].query_status == "online"

    @pytest.mark.asyncio
    async def test_extend_ttl_all_statuses(
        self, provider: ServiceDeviceProvider, mock_binding_repo: MagicMock
    ) -> None:
        """extend_ttl_by_bot queries all statuses (draft/validating/online)."""
        mock_binding_repo.list_paas_device_by_bot_service.return_value = []

        result = await provider.extend_ttl_by_bot(bot_id="bot1", entity_id="entity1")

        assert result.total_devices == 0
        mock_binding_repo.list_paas_device_by_bot_service.assert_called_with(
            bot_id="bot1",
            entity_id="entity1",
            statuses=["draft", "validating", "online"],
        )

    @pytest.mark.asyncio
    async def test_extend_ttl_success_with_source_table(
        self,
        provider: ServiceDeviceProvider,
        mock_facade: MagicMock,
        mock_binding_repo: MagicMock,
    ) -> None:
        import time

        expired_ts = int((time.time() - 3600) * 1000)

        mock_binding_repo.list_paas_device_by_bot_service.return_value = [
            {
                "paas_device_id": "ARCA-SANDBOX-001@0",
                "provider_type": "ARCA",
                "status": "online",
                "query_status": "online",
                "ttl_expiration_timestamp": expired_ts,
                "ttl_expiration_time": "2024-01-01 11:00:00",
                "source_table": "baas_device",
                "source_table_id": "99",
                "refresh_fail_count": 0,
            }
        ]

        mock_facade.update_device_ttl = AsyncMock(
            return_value=TTLInfo(
                paas_device_id="ARCA-SANDBOX-001@0",
                old_expiration_time=datetime.fromtimestamp(expired_ts / 1000),
                new_expiration_time=datetime(2024, 1, 2, 12, 0, 0),
                success=True,
            )
        )

        result = await provider.extend_ttl_by_bot(bot_id="bot1", entity_id="entity1")

        assert result.total_devices == 1
        assert result.extended_count == 1
        assert result.failed_count == 0
        mock_binding_repo.update_baas_device_ttl_by_id.assert_called_once()

    @pytest.mark.asyncio
    async def test_extend_ttl_skip_empty_paas_device_id(
        self, provider: ServiceDeviceProvider, mock_binding_repo: MagicMock
    ) -> None:
        mock_binding_repo.list_paas_device_by_bot_service.return_value = [
            {
                "paas_device_id": "",
                "provider_type": "ARCA",
                "status": "unknown",
                "query_status": "draft",
                "source_table": "baas_device",
                "source_table_id": "99",
                "refresh_fail_count": 0,
            }
        ]

        result = await provider.extend_ttl_by_bot(bot_id="bot1", entity_id="entity1")

        assert result.total_devices == 1
        assert result.skipped_count == 1

    @pytest.mark.asyncio
    async def test_extend_ttl_failure(
        self,
        provider: ServiceDeviceProvider,
        mock_facade: MagicMock,
        mock_binding_repo: MagicMock,
    ) -> None:
        import time

        expired_ts = int((time.time() - 3600) * 1000)

        mock_binding_repo.list_paas_device_by_bot_service.return_value = [
            {
                "paas_device_id": "ARCA-SANDBOX-001@0",
                "provider_type": "ARCA",
                "status": "online",
                "query_status": "online",
                "ttl_expiration_timestamp": expired_ts,
                "ttl_expiration_time": "2024-01-01 11:00:00",
                "source_table": "baas_device",
                "source_table_id": "99",
                "refresh_fail_count": 0,
            }
        ]

        mock_ttl_info = TTLInfo(
            paas_device_id="ARCA-SANDBOX-001@0",
            old_expiration_time=datetime.fromtimestamp(expired_ts / 1000),
            new_expiration_time=None,
            success=False,
            error="Service unavailable",
        )
        mock_facade.update_device_ttl = AsyncMock(return_value=mock_ttl_info)

        result = await provider.extend_ttl_by_bot(bot_id="bot1", entity_id="entity1")

        assert result.total_devices == 1
        assert result.extended_count == 0
        assert result.failed_count == 1
        mock_binding_repo.update_baas_device_refresh_fail_count_by_id.assert_called_once()

    @pytest.mark.asyncio
    async def test_extend_ttl_sufficient_ttl_skips(
        self,
        provider: ServiceDeviceProvider,
        mock_facade: MagicMock,
        mock_binding_repo: MagicMock,
    ) -> None:
        """TTL with sufficient remaining time is skipped."""
        import time

        future_ts = int((time.time() + 86400 * 7) * 1000)  # 7 days in future

        mock_binding_repo.list_paas_device_by_bot_service.return_value = [
            {
                "paas_device_id": "ARCA-SANDBOX-001@0",
                "provider_type": "ARCA",
                "status": "online",
                "query_status": "online",
                "ttl_expiration_timestamp": future_ts,
                "ttl_expiration_time": "2099-01-01 12:00:00",
                "source_table": "baas_device",
                "source_table_id": "99",
                "refresh_fail_count": 0,
            }
        ]

        result = await provider.extend_ttl_by_bot(bot_id="bot1", entity_id="entity1")

        assert result.total_devices == 1
        assert result.skipped_count == 1
        assert result.extended_count == 0
        assert result.failed_count == 0

    @pytest.mark.asyncio
    async def test_extend_ttl_refresh_when_ttl_none(
        self,
        provider: ServiceDeviceProvider,
        mock_facade: MagicMock,
        mock_binding_repo: MagicMock,
    ) -> None:
        """When ttl_expiration_timestamp is None, refresh_device_ttl is called."""
        import time
        from unittest.mock import AsyncMock

        mock_binding_repo.list_paas_device_by_bot_service.return_value = [
            {
                "paas_device_id": "ARCA-SANDBOX-001@0",
                "provider_type": "ARCA",
                "status": "online",
                "query_status": "online",
                "ttl_expiration_timestamp": None,
                "ttl_expiration_time": None,
                "source_table": "baas_device",
                "source_table_id": "99",
                "refresh_fail_count": 0,
            }
        ]

        future_ts = int((time.time() + 86400 * 7) * 1000)
        provider.refresh_device_ttl = AsyncMock(return_value=future_ts)

        result = await provider.extend_ttl_by_bot(bot_id="bot1", entity_id="entity1")

        provider.refresh_device_ttl.assert_called_once()
        assert result.total_devices == 1
        assert result.skipped_count == 1  # after refresh, TTL is sufficient

    @pytest.mark.asyncio
    async def test_extend_ttl_exception(
        self,
        provider: ServiceDeviceProvider,
        mock_facade: MagicMock,
        mock_binding_repo: MagicMock,
    ) -> None:
        """Exception during update_device_ttl is caught."""
        import time

        expired_ts = int((time.time() - 3600) * 1000)

        mock_binding_repo.list_paas_device_by_bot_service.return_value = [
            {
                "paas_device_id": "ARCA-SANDBOX-001@0",
                "provider_type": "ARCA",
                "status": "online",
                "query_status": "online",
                "ttl_expiration_timestamp": expired_ts,
                "ttl_expiration_time": "2024-01-01 11:00:00",
                "source_table": "baas_device",
                "source_table_id": "99",
                "refresh_fail_count": 0,
            }
        ]

        mock_facade.update_device_ttl = AsyncMock(
            side_effect=RuntimeError("Service unavailable")
        )

        result = await provider.extend_ttl_by_bot(bot_id="bot1", entity_id="entity1")

        assert result.total_devices == 1
        assert result.extended_count == 0
        assert result.failed_count == 1
        mock_binding_repo.update_baas_device_refresh_fail_count_by_id.assert_called_once()
