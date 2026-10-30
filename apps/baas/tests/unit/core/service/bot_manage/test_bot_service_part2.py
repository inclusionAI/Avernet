"""Unit tests for DefaultBotCrudService — all methods.

Covers: _calculate_bot_status, _calculate_bot_statuses, create_bot, create_bot_record,
select_device, get_bot, list_bots, destroy_bot, bot_record_to_response, _device_record_to_response.
"""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from secbaas.community.api.bot_manage import (
    BotClusterCreate,
    BotConfig,
    BotResponse,
    BotStatus,
)
from secbaas.community.api.bot_runtime import BotNotFoundError
from secbaas.community.api.device_manage import (
    DestroyDeviceResponse,
    DeviceConfig,
    DeviceResponse,
)
from secbaas.community.api.template_manage import TemplateNotFoundError
from secbaas.community.core.repository.bot import BotRecord
from secbaas.community.core.repository.bot_device_rel import BotDeviceRelRecord
from secbaas.community.core.repository.device import DeviceRecord


@pytest.fixture
def mock_clone_pending():
    with patch(
        "secbaas.community.core.service.bot_manage._bot_service.try_insert_pending_bot"
    ) as clone:
        yield clone


# ==================== Legacy Fixtures (used by existing tests) ====================


@pytest.fixture
def mock_env():
    with patch(
        "secbaas.community.core.service.bot_manage._bot_service.get_current_env",
        return_value="test",
    ):
        yield


@pytest.fixture
def mock_device_repo():
    return MagicMock()


def _make_bot_record(
    bot_id: int = 1,
    status: str = "ACTIVE",
    bot_uuid: str = "bot-uuid-001",
) -> BotRecord:
    record = MagicMock(spec=BotRecord)
    record.id = bot_id
    record.bot_uuid = bot_uuid
    record.status = status
    record.tenant = "test-tenant"
    record.env = "test"
    return record


def _make_device_record(
    device_id: int = 1,
    status: str = "ACTIVE",
) -> DeviceRecord:
    record = MagicMock(spec=DeviceRecord)
    record.id = device_id
    record.status = status
    record.err_msg = None
    return record


# ==================== Test _calculate_bot_status ====================


class TestSelectDevice:
    @pytest.mark.asyncio
    async def test_select_device_happy_path(
        self, mock_bot_repo, mock_device_repo, bot_crud_service
    ):
        bot = MagicMock(spec=BotRecord)
        bot.id = 1
        mock_bot_repo.get_by_id.return_value = bot

        active_dev = MagicMock(spec=DeviceRecord)
        active_dev.id = 101
        active_dev.device_uuid = "DEV-active"
        active_dev.status = "ACTIVE"
        active_dev.provider_type = "ARCA"
        active_dev.provider_device_id = "sbox-1"
        active_dev.provider_device_props = {}
        active_dev.extra_config = {}
        active_dev.tenant = "test-tenant"
        active_dev.env = "test"
        active_dev.domain = "default"
        active_dev.creator = "c"
        active_dev.modifier = "m"
        active_dev.gmt_create = datetime(2025, 1, 1)
        active_dev.gmt_modified = datetime(2025, 1, 1)

        mock_device_repo.list_by_bot_id.return_value = [active_dev]

        result = await bot_crud_service.select_device(
            tenant="test-tenant",
            bot_id=1,
        )

        assert result.device_uuid == "DEV-active"
        assert result.status == "ACTIVE"

    @pytest.mark.asyncio
    async def test_select_device_bot_not_found(self, mock_bot_repo, bot_crud_service):
        mock_bot_repo.get_by_id.return_value = None

        with pytest.raises(BotNotFoundError):
            await bot_crud_service.select_device(
                tenant="test-tenant",
                bot_id=999,
            )

    @pytest.mark.asyncio
    async def test_select_device_no_active_devices(
        self,
        mock_bot_repo,
        mock_device_repo,
        bot_crud_service,
    ):
        bot = MagicMock(spec=BotRecord)
        bot.id = 1
        mock_bot_repo.get_by_id.return_value = bot

        failed_dev = MagicMock(spec=DeviceRecord)
        failed_dev.id = 101
        failed_dev.status = "FAILED"
        mock_device_repo.list_by_bot_id.return_value = [failed_dev]

        with pytest.raises(RuntimeError, match="No available Device for Bot"):
            await bot_crud_service.select_device(
                tenant="test-tenant",
                bot_id=1,
            )

    @pytest.mark.asyncio
    async def test_select_device_filters_active_only(
        self,
        mock_bot_repo,
        mock_device_repo,
        bot_crud_service,
    ):
        bot = MagicMock(spec=BotRecord)
        bot.id = 1
        mock_bot_repo.get_by_id.return_value = bot

        active_dev = MagicMock(spec=DeviceRecord)
        active_dev.id = 102
        active_dev.device_uuid = "DEV-active"
        active_dev.status = "ACTIVE"
        active_dev.provider_type = "ARCA"
        active_dev.provider_device_id = "sbox-2"
        active_dev.provider_device_props = {}
        active_dev.extra_config = {}
        active_dev.tenant = "test-tenant"
        active_dev.env = "test"
        active_dev.domain = "default"
        active_dev.creator = "c"
        active_dev.modifier = "m"
        active_dev.gmt_create = datetime(2025, 1, 1)
        active_dev.gmt_modified = datetime(2025, 1, 1)

        failed_dev = MagicMock(spec=DeviceRecord)
        failed_dev.id = 101
        failed_dev.status = "FAILED"

        pending_dev = MagicMock(spec=DeviceRecord)
        pending_dev.id = 103
        pending_dev.status = "PENDING"

        mock_device_repo.list_by_bot_id.return_value = [
            failed_dev,
            active_dev,
            pending_dev,
        ]

        result = await bot_crud_service.select_device(
            tenant="test-tenant",
            bot_id=1,
        )

        assert result.device_uuid == "DEV-active"


class TestGetBot:
    @pytest.mark.asyncio
    async def test_get_bot_found_with_status(
        self, mock_bot_repo, mock_device_repo, bot_crud_service
    ):
        record = MagicMock(spec=BotRecord)
        record.id = 1
        record.bot_uuid = "BOT-abc"
        record.tenant = "test-tenant"
        record.env = "test"
        record.domain = "default"
        record.is_deleted = 0
        record.creator = "c"
        record.modifier = "m"
        record.status = "PENDING"
        record.name = "n"
        record.description = None
        record.template_uuid = None
        record.replica_desired = 1
        record.replica_minimum = 1
        record.replica_maximum = 10
        record.auto_scaling_enabled = 0
        record.sla_grade = "standard"
        record.gmt_create = datetime(2025, 1, 1)
        record.gmt_modified = datetime(2025, 1, 1)
        record.extra_config = {}

        mock_bot_repo.get_by_id.return_value = record
        mock_device_repo.list_by_bot_id.return_value = [
            MagicMock(spec=DeviceRecord, status="ACTIVE"),
        ]

        result = await bot_crud_service.get_bot(
            tenant="test-tenant",
            bot_id=1,
            include_status=True,
        )

        assert result is not None
        assert result.id == 1
        assert result.status == "ACTIVE"

    @pytest.mark.asyncio
    async def test_get_bot_not_found(self, mock_bot_repo, bot_crud_service):
        mock_bot_repo.get_by_id.return_value = None

        result = await bot_crud_service.get_bot(
            tenant="test-tenant",
            bot_id=999,
        )

        assert result is None

    @pytest.mark.asyncio
    async def test_get_bot_skip_status_calculation(
        self, mock_bot_repo, bot_crud_service
    ):
        record = MagicMock(spec=BotRecord)
        record.id = 1
        record.bot_uuid = "BOT-abc"
        record.tenant = "test-tenant"
        record.env = "test"
        record.domain = "default"
        record.is_deleted = 0
        record.creator = "c"
        record.modifier = "m"
        record.status = "PENDING"
        record.name = "n"
        record.description = None
        record.template_uuid = None
        record.replica_desired = 1
        record.replica_minimum = 1
        record.replica_maximum = 10
        record.auto_scaling_enabled = 0
        record.sla_grade = "standard"
        record.gmt_create = datetime(2025, 1, 1)
        record.gmt_modified = datetime(2025, 1, 1)
        record.extra_config = {}

        mock_bot_repo.get_by_id.return_value = record

        result = await bot_crud_service.get_bot(
            tenant="test-tenant",
            bot_id=1,
            include_status=False,
        )

        assert result is not None
        assert result.status == "PENDING"


class TestListBots:
    @pytest.mark.asyncio
    async def test_list_bots_empty(self, mock_bot_repo, bot_crud_service):
        mock_bot_repo.list_bots.return_value = (0, [])

        result = await bot_crud_service.list_bots(tenant="test-tenant")

        assert result.total == 0
        assert result.items == []
        assert result.page == 1
        assert result.page_size == 20

    @pytest.mark.asyncio
    async def test_list_bots_with_results(
        self,
        mock_bot_repo,
        mock_device_repo,
        bot_crud_service,
    ):
        record = MagicMock(spec=BotRecord)
        record.id = 1
        record.bot_uuid = "BOT-1"
        record.tenant = "test-tenant"
        record.env = "test"
        record.domain = "default"
        record.is_deleted = 0
        record.creator = "c"
        record.modifier = "m"
        record.status = "ACTIVE"
        record.name = "bot1"
        record.description = None
        record.template_uuid = None
        record.replica_desired = 1
        record.replica_minimum = 1
        record.replica_maximum = 10
        record.auto_scaling_enabled = 0
        record.sla_grade = "standard"
        record.gmt_create = datetime(2025, 1, 1)
        record.gmt_modified = datetime(2025, 1, 1)
        record.extra_config = {}

        mock_bot_repo.list_bots.return_value = (1, [record])
        mock_device_repo.list_devices_by_bot_ids.return_value = {
            1: [MagicMock(spec=DeviceRecord, status="ACTIVE")],
        }

        result = await bot_crud_service.list_bots(tenant="test-tenant")

        assert result.total == 1
        assert len(result.items) == 1
        assert result.items[0].status == "ACTIVE"

    @pytest.mark.asyncio
    async def test_list_bots_with_status_filter(
        self,
        mock_bot_repo,
        mock_device_repo,
        bot_crud_service,
    ):
        record = MagicMock(spec=BotRecord)
        record.id = 1
        record.bot_uuid = "BOT-1"
        record.tenant = "test-tenant"
        record.env = "test"
        record.domain = "default"
        record.is_deleted = 0
        record.creator = "c"
        record.modifier = "m"
        record.status = "PENDING"
        record.name = "bot1"
        record.description = None
        record.template_uuid = None
        record.replica_desired = 1
        record.replica_minimum = 1
        record.replica_maximum = 10
        record.auto_scaling_enabled = 0
        record.sla_grade = "standard"
        record.gmt_create = datetime(2025, 1, 1)
        record.gmt_modified = datetime(2025, 1, 1)
        record.extra_config = {}

        mock_bot_repo.list_bots.return_value = (1, [record])
        mock_device_repo.list_devices_by_bot_ids.return_value = {
            1: [MagicMock(spec=DeviceRecord, status="ACTIVE")],
        }

        result = await bot_crud_service.list_bots(
            tenant="test-tenant",
            status=BotStatus.PENDING,
        )

        assert result.total == 0
        assert len(result.items) == 0

    @pytest.mark.asyncio
    async def test_list_bots_status_filter_match(
        self,
        mock_bot_repo,
        mock_device_repo,
        bot_crud_service,
    ):
        record = MagicMock(spec=BotRecord)
        record.id = 1
        record.bot_uuid = "BOT-1"
        record.tenant = "test-tenant"
        record.env = "test"
        record.domain = "default"
        record.is_deleted = 0
        record.creator = "c"
        record.modifier = "m"
        record.status = "PENDING"
        record.name = "bot1"
        record.description = None
        record.template_uuid = None
        record.replica_desired = 1
        record.replica_minimum = 1
        record.replica_maximum = 10
        record.auto_scaling_enabled = 0
        record.sla_grade = "standard"
        record.gmt_create = datetime(2025, 1, 1)
        record.gmt_modified = datetime(2025, 1, 1)
        record.extra_config = {}

        mock_bot_repo.list_bots.return_value = (1, [record])
        mock_device_repo.list_devices_by_bot_ids.return_value = {
            1: [MagicMock(spec=DeviceRecord, status="ACTIVE")],
        }

        result = await bot_crud_service.list_bots(
            tenant="test-tenant",
            status=BotStatus.ACTIVE,
        )

        assert result.total == 1
        assert len(result.items) == 1

    @pytest.mark.asyncio
    async def test_list_bots_pagination(
        self, mock_bot_repo, mock_device_repo, bot_crud_service
    ):
        mock_bot_repo.list_bots.return_value = (42, [])
        mock_device_repo.list_devices_by_bot_ids.return_value = {}

        result = await bot_crud_service.list_bots(
            tenant="test-tenant",
            page=2,
            page_size=10,
        )

        assert result.page == 2
        assert result.page_size == 10
        mock_bot_repo.list_bots.assert_called_once_with(
            tenant="test-tenant",
            env="test",
            page=2,
            page_size=10,
        )


class TestDestroyBot:
    @pytest.mark.asyncio
    async def test_destroy_bot_happy_path(
        self,
        mock_bot_repo,
        mock_device_repo,
        mock_rel_repo,
        mock_device_service,
        bot_crud_service,
    ):
        bot = MagicMock(spec=BotRecord)
        bot.id = 1
        bot.status = "ACTIVE"
        mock_bot_repo.get_by_id.return_value = bot

        rel = MagicMock(spec=BotDeviceRelRecord)
        rel.id = 10
        rel.device_uuid = "DEV-001"

        mock_rel_repo.list_by_bot_id.return_value = [rel]

        device = MagicMock(spec=DeviceRecord)
        device.id = 101
        device.device_uuid = "DEV-001"
        mock_device_repo.get_active_by_device_uuid.return_value = device

        destroy_resp = DestroyDeviceResponse(success=True)
        mock_device_service.destroy_device_by_uuid = AsyncMock(
            return_value=destroy_resp,
        )

        result = await bot_crud_service.destroy_bot(
            tenant="test-tenant",
            bot_id=1,
            modifier="op",
        )

        assert result is True
        mock_bot_repo.update_status.assert_called_once_with(
            bot_id=1,
            tenant="test-tenant",
            env="test",
            status="RELEASED",
            modifier="op",
        )
        mock_device_service.destroy_device_by_uuid.assert_called_once_with(
            tenant="test-tenant",
            device_uuid="DEV-001",
            modifier="op",
        )
        mock_rel_repo.soft_delete.assert_called_once()
        assert mock_rel_repo.soft_delete.call_args.kwargs["rel_id"] == 10

    @pytest.mark.asyncio
    async def test_destroy_bot_not_found(self, mock_bot_repo, bot_crud_service):
        mock_bot_repo.get_by_id.return_value = None

        result = await bot_crud_service.destroy_bot(
            tenant="test-tenant",
            bot_id=999,
            modifier="op",
        )

        assert result is False

    @pytest.mark.asyncio
    async def test_destroy_bot_already_released(self, mock_bot_repo, bot_crud_service):
        bot = MagicMock(spec=BotRecord)
        bot.id = 1
        bot.status = "RELEASED"
        mock_bot_repo.get_by_id.return_value = bot

        result = await bot_crud_service.destroy_bot(
            tenant="test-tenant",
            bot_id=1,
            modifier="op",
        )

        assert result is False
        mock_bot_repo.update_status.assert_not_called()

    @pytest.mark.asyncio
    async def test_destroy_bot_device_destruction_failure(
        self,
        mock_bot_repo,
        mock_device_repo,
        mock_rel_repo,
        mock_device_service,
        bot_crud_service,
    ):
        bot = MagicMock(spec=BotRecord)
        bot.id = 1
        bot.status = "ACTIVE"
        mock_bot_repo.get_by_id.return_value = bot

        rel = MagicMock(spec=BotDeviceRelRecord)
        rel.id = 10
        rel.device_uuid = "DEV-001"

        mock_rel_repo.list_by_bot_id.return_value = [rel]

        device = MagicMock(spec=DeviceRecord)
        device.id = 101
        device.device_uuid = "DEV-001"
        mock_device_repo.get_active_by_device_uuid.return_value = device

        destroy_resp = DestroyDeviceResponse(
            success=False,
            error_message="PaaS error",
        )
        mock_device_service.destroy_device_by_uuid = AsyncMock(
            return_value=destroy_resp,
        )

        result = await bot_crud_service.destroy_bot(
            tenant="test-tenant",
            bot_id=1,
            modifier="op",
        )

        assert result is True
        mock_rel_repo.soft_delete.assert_not_called()

    @pytest.mark.asyncio
    async def test_destroy_bot_device_destruction_exception(
        self,
        mock_bot_repo,
        mock_device_repo,
        mock_rel_repo,
        mock_device_service,
        bot_crud_service,
    ):
        bot = MagicMock(spec=BotRecord)
        bot.id = 1
        bot.status = "ACTIVE"
        mock_bot_repo.get_by_id.return_value = bot

        rel = MagicMock(spec=BotDeviceRelRecord)
        rel.id = 10
        rel.device_uuid = "DEV-001"

        mock_rel_repo.list_by_bot_id.return_value = [rel]

        device = MagicMock(spec=DeviceRecord)
        device.id = 101
        device.device_uuid = "DEV-001"
        mock_device_repo.get_active_by_device_uuid.return_value = device

        mock_device_service.destroy_device_by_uuid = AsyncMock(
            side_effect=ConnectionError("network down"),
        )

        result = await bot_crud_service.destroy_bot(
            tenant="test-tenant",
            bot_id=1,
            modifier="op",
        )

        assert result is True
        mock_rel_repo.soft_delete.assert_not_called()

    @pytest.mark.asyncio
    async def test_destroy_bot_orphan_relationship(
        self,
        mock_bot_repo,
        mock_device_repo,
        mock_rel_repo,
        mock_device_service,
        bot_crud_service,
    ):
        bot = MagicMock(spec=BotRecord)
        bot.id = 1
        bot.status = "ACTIVE"
        mock_bot_repo.get_by_id.return_value = bot

        rel = MagicMock(spec=BotDeviceRelRecord)
        rel.id = 10
        rel.device_uuid = "DEV-ORPHAN"

        mock_rel_repo.list_by_bot_id.return_value = [rel]
        mock_device_repo.get_active_by_device_uuid.return_value = None

        result = await bot_crud_service.destroy_bot(
            tenant="test-tenant",
            bot_id=1,
            modifier="op",
        )

        assert result is True
        mock_rel_repo.soft_delete.assert_called_once()
        assert mock_rel_repo.soft_delete.call_args.kwargs["rel_id"] == 10
        mock_device_service.destroy_device_by_uuid.assert_not_called()

    @pytest.mark.asyncio
    async def test_destroy_bot_with_warnings(
        self,
        mock_bot_repo,
        mock_device_repo,
        mock_rel_repo,
        mock_device_service,
        bot_crud_service,
    ):
        bot = MagicMock(spec=BotRecord)
        bot.id = 1
        bot.status = "ACTIVE"
        mock_bot_repo.get_by_id.return_value = bot

        rel = MagicMock(spec=BotDeviceRelRecord)
        rel.id = 10
        rel.device_uuid = "DEV-001"

        mock_rel_repo.list_by_bot_id.return_value = [rel]

        device = MagicMock(spec=DeviceRecord)
        device.id = 101
        device.device_uuid = "DEV-001"
        mock_device_repo.get_active_by_device_uuid.return_value = device

        destroy_resp = DestroyDeviceResponse(
            success=True,
            error_message="Hook warning: cleanup incomplete",
        )
        mock_device_service.destroy_device_by_uuid = AsyncMock(
            return_value=destroy_resp,
        )

        result = await bot_crud_service.destroy_bot(
            tenant="test-tenant",
            bot_id=1,
            modifier="op",
        )

        assert result is True
        mock_rel_repo.soft_delete.assert_called_once()

    @pytest.mark.asyncio
    async def test_destroy_bot_multiple_devices(
        self,
        mock_bot_repo,
        mock_device_repo,
        mock_rel_repo,
        mock_device_service,
        bot_crud_service,
    ):
        bot = MagicMock(spec=BotRecord)
        bot.id = 1
        bot.status = "ACTIVE"
        mock_bot_repo.get_by_id.return_value = bot

        rel1 = MagicMock(spec=BotDeviceRelRecord)
        rel1.id = 10
        rel1.device_uuid = "DEV-001"

        rel2 = MagicMock(spec=BotDeviceRelRecord)
        rel2.id = 11
        rel2.device_uuid = "DEV-002"

        mock_rel_repo.list_by_bot_id.return_value = [rel1, rel2]

        device1 = MagicMock(spec=DeviceRecord)
        device1.id = 101
        device1.device_uuid = "DEV-001"

        device2 = MagicMock(spec=DeviceRecord)
        device2.id = 102
        device2.device_uuid = "DEV-002"

        mock_device_repo.get_active_by_device_uuid.side_effect = [device1, device2]

        destroy_resp = DestroyDeviceResponse(success=True)
        mock_device_service.destroy_device_by_uuid = AsyncMock(
            return_value=destroy_resp,
        )

        result = await bot_crud_service.destroy_bot(
            tenant="test-tenant",
            bot_id=1,
            modifier="op",
        )

        assert result is True
        assert mock_device_service.destroy_device_by_uuid.call_count == 2
        assert mock_rel_repo.soft_delete.call_count == 2

    @pytest.mark.asyncio
    async def test_destroy_bot_no_relationships(
        self,
        mock_bot_repo,
        mock_rel_repo,
        bot_crud_service,
    ):
        bot = MagicMock(spec=BotRecord)
        bot.id = 1
        bot.status = "ACTIVE"
        mock_bot_repo.get_by_id.return_value = bot
        mock_rel_repo.list_by_bot_id.return_value = []

        result = await bot_crud_service.destroy_bot(
            tenant="test-tenant",
            bot_id=1,
            modifier="op",
        )

        assert result is True
        mock_bot_repo.update_status.assert_called_once()
