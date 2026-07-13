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


class TestCalculateBotStatus:
    """Tests for DefaultBotCrudService._calculate_bot_status method."""

    def test_destroying_returns_destroying(
        self, mock_env, mock_device_repo, bot_crud_service
    ):
        """DESTROYING status is stored, not calculated."""
        bot = _make_bot_record(status="DESTROYING")
        result = bot_crud_service._calculate_bot_status(bot, "test-tenant")
        assert result == BotStatus.DESTROYING
        mock_device_repo.list_by_bot_id.assert_not_called()

    def test_no_devices_returns_stored_status(
        self, mock_env, mock_device_repo, bot_crud_service
    ):
        """When no devices exist, return stored status."""
        mock_device_repo.list_by_bot_id.return_value = []
        bot = _make_bot_record(status="PENDING")
        result = bot_crud_service._calculate_bot_status(bot, "test-tenant")
        assert result == BotStatus.PENDING
        mock_device_repo.list_by_bot_id.assert_called_once()

    def test_any_active_returns_active(
        self, mock_env, mock_device_repo, bot_crud_service
    ):
        """>=1 active device → ACTIVE."""
        mock_device_repo.list_by_bot_id.return_value = [
            _make_device_record(device_id=1, status="ACTIVE"),
            _make_device_record(device_id=2, status="FAILED"),
        ]
        bot = _make_bot_record(status="PENDING")
        result = bot_crud_service._calculate_bot_status(bot, "test-tenant")
        assert result == BotStatus.ACTIVE

    def test_all_failed_returns_failed(
        self, mock_env, mock_device_repo, bot_crud_service
    ):
        """All devices FAILED → FAILED."""
        mock_device_repo.list_by_bot_id.return_value = [
            _make_device_record(device_id=1, status="FAILED"),
            _make_device_record(device_id=2, status="FAILED"),
        ]
        bot = _make_bot_record(status="PENDING")
        result = bot_crud_service._calculate_bot_status(bot, "test-tenant")
        assert result == BotStatus.FAILED

    def test_mixed_pending_failed_returns_pending(
        self, mock_env, mock_device_repo, bot_crud_service
    ):
        """Mix of PENDING/FAILED but no ACTIVE → PENDING."""
        mock_device_repo.list_by_bot_id.return_value = [
            _make_device_record(device_id=1, status="PENDING"),
            _make_device_record(device_id=2, status="FAILED"),
        ]
        bot = _make_bot_record(status="PENDING")
        result = bot_crud_service._calculate_bot_status(bot, "test-tenant")
        assert result == BotStatus.PENDING

    def test_all_pending_returns_pending(
        self, mock_env, mock_device_repo, bot_crud_service
    ):
        """All devices PENDING → PENDING."""
        mock_device_repo.list_by_bot_id.return_value = [
            _make_device_record(device_id=1, status="PENDING"),
            _make_device_record(device_id=2, status="PENDING"),
        ]
        bot = _make_bot_record(status="ACTIVE")
        result = bot_crud_service._calculate_bot_status(bot, "test-tenant")
        assert result == BotStatus.PENDING


# ==================== Test _calculate_bot_statuses ====================


class TestCalculateBotStatuses:
    """Tests for DefaultBotCrudService._calculate_bot_statuses method."""

    def test_empty_records(self, mock_env, mock_device_repo, bot_crud_service):
        """Empty records list returns empty dict."""
        result = bot_crud_service._calculate_bot_statuses([], "test-tenant")
        assert result == {}

    def test_destroying_returned_directly(
        self, mock_env, mock_device_repo, bot_crud_service
    ):
        """DESTROYING status stored, not calculated (batch)."""
        mock_device_repo.list_devices_by_bot_ids.return_value = {}
        bot = _make_bot_record(bot_id=1, status="DESTROYING")
        result = bot_crud_service._calculate_bot_statuses([bot], "test-tenant")
        assert result[1] == BotStatus.DESTROYING

    def test_no_devices_uses_stored_status(
        self, mock_env, mock_device_repo, bot_crud_service
    ):
        """When no devices for a bot, return stored status (batch)."""
        mock_device_repo.list_devices_by_bot_ids.return_value = {}
        bot = _make_bot_record(bot_id=1, status="PENDING")
        result = bot_crud_service._calculate_bot_statuses([bot], "test-tenant")
        assert result[1] == BotStatus.PENDING

    def test_active_device_returns_active(
        self, mock_env, mock_device_repo, bot_crud_service
    ):
        """>=1 active device → ACTIVE (batch)."""
        mock_device_repo.list_devices_by_bot_ids.return_value = {
            1: [_make_device_record(device_id=10, status="ACTIVE")],
        }
        bot = _make_bot_record(bot_id=1, status="PENDING")
        result = bot_crud_service._calculate_bot_statuses([bot], "test-tenant")
        assert result[1] == BotStatus.ACTIVE

    def test_all_failed_returns_failed(
        self, mock_env, mock_device_repo, bot_crud_service
    ):
        """All devices FAILED → FAILED (batch)."""
        mock_device_repo.list_devices_by_bot_ids.return_value = {
            1: [
                _make_device_record(device_id=10, status="FAILED"),
                _make_device_record(device_id=11, status="FAILED"),
            ],
        }
        bot = _make_bot_record(bot_id=1, status="PENDING")
        result = bot_crud_service._calculate_bot_statuses([bot], "test-tenant")
        assert result[1] == BotStatus.FAILED

    def test_pending_returns_pending(
        self, mock_env, mock_device_repo, bot_crud_service
    ):
        """Mix of PENDING/FAILED but no ACTIVE → PENDING (batch)."""
        mock_device_repo.list_devices_by_bot_ids.return_value = {
            1: [
                _make_device_record(device_id=10, status="PENDING"),
                _make_device_record(device_id=11, status="FAILED"),
            ],
        }
        bot = _make_bot_record(bot_id=1, status="PENDING")
        result = bot_crud_service._calculate_bot_statuses([bot], "test-tenant")
        assert result[1] == BotStatus.PENDING

    def test_multiple_bots_mixed_statuses(
        self, mock_env, mock_device_repo, bot_crud_service
    ):
        mock_device_repo.list_devices_by_bot_ids.return_value = {
            1: [_make_device_record(device_id=10, status="ACTIVE")],
            2: [_make_device_record(device_id=20, status="FAILED")],
            3: [],
        }
        bots = [
            _make_bot_record(bot_id=1, status="PENDING"),
            _make_bot_record(bot_id=2, status="PENDING"),
            _make_bot_record(bot_id=3, status="PENDING"),
        ]
        result = bot_crud_service._calculate_bot_statuses(bots, "test-tenant")
        assert result[1] == BotStatus.ACTIVE
        assert result[2] == BotStatus.FAILED
        assert result[3] == BotStatus.PENDING


# ==================== Test bot_record_to_response / _device_record_to_response ====================


class TestRecordToResponse:
    def test_record_to_response_complete(self):
        from secbaas.community.core.service.bot_manage._bot_service import (
            bot_record_to_response,
        )

        record = MagicMock(spec=BotRecord)
        record.id = 42
        record.bot_uuid = "BOT-test123"
        record.tenant = "t"
        record.env = "e"
        record.domain = "d"
        record.is_deleted = 0
        record.creator = "c"
        record.modifier = "m"
        record.status = "ACTIVE"
        record.name = "n"
        record.description = "desc"
        record.template_uuid = "TPL-001"
        record.replica_desired = 3
        record.replica_minimum = 1
        record.replica_maximum = 10
        record.auto_scaling_enabled = 0
        record.sla_grade = "standard"
        record.gmt_create = datetime(2025, 1, 1)
        record.gmt_modified = datetime(2025, 1, 2)
        record.extra_config = {"sla_grade": "standard", "share_policy": {}}

        result = bot_record_to_response(record)
        assert isinstance(result, BotResponse)
        assert result.id == 42
        assert result.bot_uuid == "BOT-test123"
        assert result.status == "ACTIVE"
        assert result.name == "n"
        assert result.replica_desired == 3
        assert result.config is not None
        assert result.config.sla_grade == "standard"

    def test_record_to_response_none_raises(self):
        from secbaas.community.core.service.bot_manage._bot_service import (
            bot_record_to_response,
        )

        with pytest.raises(RuntimeError, match="Bot record is None"):
            bot_record_to_response(None)

    def test_record_to_response_empty_extra_config(self):
        from secbaas.community.core.service.bot_manage._bot_service import (
            bot_record_to_response,
        )

        record = MagicMock(spec=BotRecord)
        record.id = 1
        record.bot_uuid = "BOT-x"
        record.tenant = "t"
        record.env = "e"
        record.domain = "d"
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

        result = bot_record_to_response(record)
        assert result.config == BotConfig()

    def test_device_record_to_response_complete(self):
        from secbaas.community.core.service.device_manage import (
            device_record_to_response,
        )

        record = MagicMock(spec=DeviceRecord)
        record.id = 101
        record.device_uuid = "DEV-abc"
        record.tenant = "t"
        record.env = "e"
        record.domain = "d"
        record.status = "ACTIVE"
        record.provider_type = "ARCA"
        record.provider_device_id = "sbox-1"
        record.provider_device_props = {"key": "val"}
        record.extra_config = {}
        record.creator = "c"
        record.modifier = "m"
        record.gmt_create = datetime(2025, 1, 1)
        record.gmt_modified = datetime(2025, 1, 1)

        result = device_record_to_response(record)
        assert isinstance(result, DeviceResponse)
        assert result.id == 101
        assert result.device_uuid == "DEV-abc"
        assert result.provider_type == "ARCA"

    def test_device_record_to_response_nullable_fields(self):
        from secbaas.community.core.service.device_manage import (
            device_record_to_response,
        )

        record = MagicMock(spec=DeviceRecord)
        record.id = 101
        record.device_uuid = "DEV-abc"
        record.tenant = "t"
        record.env = "e"
        record.domain = "d"
        record.status = "PENDING"
        record.provider_type = None
        record.provider_device_id = None
        record.provider_device_props = None
        record.extra_config = None
        record.creator = "c"
        record.modifier = "m"
        record.gmt_create = datetime(2025, 1, 1)
        record.gmt_modified = datetime(2025, 1, 1)

        result = device_record_to_response(record)
        assert result.provider_type is None
        assert result.provider_device_id is None
        assert result.provider_device_props is None
        assert result.extra_config == DeviceConfig()


# ==================== Test create_bot_record ====================


class TestCreateBotRecord:
    @pytest.mark.asyncio
    async def test_create_bot_record_happy_path(self, mock_bot_repo, bot_crud_service):
        new_record = MagicMock(spec=BotRecord)
        new_record.id = 99
        new_record.bot_uuid = "BOT-cloned"
        new_record.tenant = "test-tenant"
        new_record.env = "test"
        new_record.domain = "default"
        new_record.is_deleted = 0
        new_record.creator = "operator"
        new_record.modifier = "operator"
        new_record.status = "PENDING"
        new_record.name = "cloned-bot"
        new_record.description = None
        new_record.template_uuid = None
        new_record.replica_desired = 1
        new_record.replica_minimum = 1
        new_record.replica_maximum = 10
        new_record.auto_scaling_enabled = 0
        new_record.sla_grade = "standard"
        new_record.gmt_create = datetime(2025, 1, 1)
        new_record.gmt_modified = datetime(2025, 1, 1)
        new_record.extra_config = {}

        mock_bot_repo.insert_bot_record.return_value = 99
        mock_bot_repo.get_by_id.return_value = new_record

        result = await bot_crud_service.create_bot_record(
            tenant="test-tenant",
            source_bot_id=1,
            operator="operator",
        )

        assert result.id == 99
        assert result.bot_uuid == "BOT-cloned"
        mock_bot_repo.insert_bot_record.assert_called_once_with(
            source_bot_id=1,
            tenant="test-tenant",
            env="test",
            status="PENDING",
            extra_config=None,
            name=None,
            modifier="operator",
        )
        mock_bot_repo.get_by_id.assert_called_once_with(99, "test-tenant", "test")

    @pytest.mark.asyncio
    async def test_create_bot_record_with_new_config(
        self, mock_bot_repo, bot_crud_service
    ):
        new_record = MagicMock(spec=BotRecord)
        new_record.id = 99
        new_record.bot_uuid = "BOT-cloned"
        new_record.tenant = "test-tenant"
        new_record.env = "test"
        new_record.domain = "default"
        new_record.is_deleted = 0
        new_record.creator = "op"
        new_record.modifier = "op"
        new_record.status = "PENDING"
        new_record.name = "new-name"
        new_record.description = None
        new_record.template_uuid = None
        new_record.replica_desired = 1
        new_record.replica_minimum = 1
        new_record.replica_maximum = 10
        new_record.auto_scaling_enabled = 0
        new_record.sla_grade = "enterprise"
        new_record.gmt_create = datetime(2025, 1, 1)
        new_record.gmt_modified = datetime(2025, 1, 1)
        new_record.extra_config = {}

        mock_bot_repo.insert_bot_record.return_value = 99
        mock_bot_repo.get_by_id.return_value = new_record

        new_config = BotConfig(sla_grade="enterprise", share_policy={"public": True})
        result = await bot_crud_service.create_bot_record(
            tenant="test-tenant",
            source_bot_id=1,
            new_config=new_config,
            new_name="new-name",
            operator="op",
        )

        assert result.name == "new-name"
        call_kwargs = mock_bot_repo.insert_bot_record.call_args.kwargs
        assert call_kwargs["extra_config"] is not None
        assert call_kwargs["name"] == "new-name"

    @pytest.mark.asyncio
    async def test_create_bot_record_new_record_not_found_raises(
        self, mock_bot_repo, bot_crud_service
    ):
        mock_bot_repo.insert_bot_record.return_value = 99
        mock_bot_repo.get_by_id.return_value = None

        with pytest.raises(RuntimeError, match="New bot record not found: 99"):
            await bot_crud_service.create_bot_record(
                tenant="test-tenant",
                source_bot_id=1,
                operator="op",
            )


# ==================== Test create_bot ====================


class TestCreateBot:
    @pytest.mark.asyncio
    async def test_create_bot_happy_path(
        self,
        mock_bot_repo,
        mock_device_repo,
        mock_rel_repo,
        mock_device_service,
        mock_template_service,
        bot_crud_service,
    ):
        mock_template = MagicMock()
        mock_template.template_uuid = "TPL-001"
        mock_template_service.get_online_template_by_uuid.return_value = mock_template

        mock_bot_repo.insert_bot.return_value = 10

        mock_device = MagicMock(spec=DeviceResponse)
        mock_device.device_uuid = "DEV-001"
        mock_device.id = 101
        mock_device_service.create_device.return_value = mock_device

        mock_rel_repo.insert_rel.return_value = 1

        saved_record = MagicMock(spec=BotRecord)
        saved_record.id = 10
        saved_record.bot_uuid = "BOT-abc"
        saved_record.tenant = "test-tenant"
        saved_record.env = "test"
        saved_record.domain = "default"
        saved_record.is_deleted = 0
        saved_record.creator = "op"
        saved_record.modifier = "op"
        saved_record.status = "PENDING"
        saved_record.name = "my-bot"
        saved_record.description = "desc"
        saved_record.template_uuid = "TPL-001"
        saved_record.replica_desired = 2
        saved_record.replica_minimum = 1
        saved_record.replica_maximum = 10
        saved_record.auto_scaling_enabled = 0
        saved_record.sla_grade = "standard"
        saved_record.gmt_create = datetime(2025, 1, 1)
        saved_record.gmt_modified = datetime(2025, 1, 1)
        saved_record.extra_config = {}

        mock_bot_repo.get_by_id.return_value = saved_record

        data = BotClusterCreate(
            bot_name="my-bot",
            bot_desc="desc",
            template_uuid="TPL-001",
            device_count=2,
            domain="default",
            operator="op",
        )

        result = await bot_crud_service.create_bot(
            tenant="test-tenant",
            data=data,
        )

        assert result.id == 10
        assert result.status == "PENDING"
        mock_bot_repo.insert_bot.assert_called_once()
        assert mock_device_service.create_device.call_count == 2
        assert mock_rel_repo.insert_rel.call_count == 2
        mock_bot_repo.update_status.assert_called_once()

    @pytest.mark.asyncio
    async def test_create_bot_template_not_found(
        self,
        mock_bot_repo,
        mock_template_service,
        bot_crud_service,
    ):
        mock_template_service.get_online_template_by_uuid.return_value = None

        data = BotClusterCreate(
            bot_name="my-bot",
            template_uuid="TPL-NOEXIST",
            device_count=1,
            domain="default",
            operator="op",
        )

        with pytest.raises(TemplateNotFoundError):
            await bot_crud_service.create_bot(
                tenant="test-tenant",
                data=data,
            )

        mock_bot_repo.insert_bot.assert_not_called()

    @pytest.mark.asyncio
    async def test_create_bot_all_devices_fail(
        self,
        mock_bot_repo,
        mock_device_repo,
        mock_rel_repo,
        mock_device_service,
        mock_template_service,
        bot_crud_service,
    ):
        mock_template = MagicMock()
        mock_template.template_uuid = "TPL-001"
        mock_template_service.get_online_template_by_uuid.return_value = mock_template

        mock_bot_repo.insert_bot.return_value = 10
        mock_device_service.create_device.side_effect = RuntimeError("PaaS unavailable")

        saved_record = MagicMock(spec=BotRecord)
        saved_record.id = 10
        saved_record.bot_uuid = "BOT-abc"
        saved_record.tenant = "test-tenant"
        saved_record.env = "test"
        saved_record.domain = "default"
        saved_record.is_deleted = 0
        saved_record.creator = "op"
        saved_record.modifier = "op"
        saved_record.status = "FAILED"
        saved_record.name = "my-bot"
        saved_record.description = "desc"
        saved_record.template_uuid = "TPL-001"
        saved_record.replica_desired = 2
        saved_record.replica_minimum = 1
        saved_record.replica_maximum = 10
        saved_record.auto_scaling_enabled = 0
        saved_record.sla_grade = "standard"
        saved_record.gmt_create = datetime(2025, 1, 1)
        saved_record.gmt_modified = datetime(2025, 1, 1)
        saved_record.extra_config = {}

        mock_bot_repo.get_by_id.return_value = saved_record

        data = BotClusterCreate(
            bot_name="my-bot",
            template_uuid="TPL-001",
            device_count=2,
            domain="default",
            operator="op",
        )

        result = await bot_crud_service.create_bot(
            tenant="test-tenant",
            data=data,
        )

        assert result.status == "FAILED"
        mock_bot_repo.update_status.assert_called_once()
        update_args = mock_bot_repo.update_status.call_args.kwargs
        assert update_args["status"] == "FAILED"
        mock_rel_repo.insert_rel.assert_not_called()

    @pytest.mark.asyncio
    async def test_create_bot_partial_device_failure(
        self,
        mock_bot_repo,
        mock_device_repo,
        mock_rel_repo,
        mock_device_service,
        mock_template_service,
        bot_crud_service,
    ):
        mock_template = MagicMock()
        mock_template.template_uuid = "TPL-001"
        mock_template_service.get_online_template_by_uuid.return_value = mock_template

        mock_bot_repo.insert_bot.return_value = 10

        good_device = MagicMock(spec=DeviceResponse)
        good_device.device_uuid = "DEV-001"
        good_device.id = 101

        mock_device_service.create_device.side_effect = [
            good_device,
            RuntimeError("PaaS unavailable"),
        ]

        mock_rel_repo.insert_rel.return_value = 1

        saved_record = MagicMock(spec=BotRecord)
        saved_record.id = 10
        saved_record.bot_uuid = "BOT-abc"
        saved_record.tenant = "test-tenant"
        saved_record.env = "test"
        saved_record.domain = "default"
        saved_record.is_deleted = 0
        saved_record.creator = "op"
        saved_record.modifier = "op"
        saved_record.status = "PENDING"
        saved_record.name = "my-bot"
        saved_record.description = None
        saved_record.template_uuid = "TPL-001"
        saved_record.replica_desired = 2
        saved_record.replica_minimum = 1
        saved_record.replica_maximum = 10
        saved_record.auto_scaling_enabled = 0
        saved_record.sla_grade = "standard"
        saved_record.gmt_create = datetime(2025, 1, 1)
        saved_record.gmt_modified = datetime(2025, 1, 1)
        saved_record.extra_config = {}

        mock_bot_repo.get_by_id.return_value = saved_record

        data = BotClusterCreate(
            bot_name="my-bot",
            template_uuid="TPL-001",
            device_count=2,
            domain="default",
            operator="op",
        )

        result = await bot_crud_service.create_bot(
            tenant="test-tenant",
            data=data,
        )

        assert result.status == "PENDING"
        mock_rel_repo.insert_rel.assert_called_once()
        update_args = mock_bot_repo.update_status.call_args.kwargs
        assert update_args["status"] == "PENDING"

    @pytest.mark.asyncio
    async def test_create_bot_with_bot_config(
        self,
        mock_bot_repo,
        mock_device_repo,
        mock_rel_repo,
        mock_device_service,
        mock_template_service,
        bot_crud_service,
    ):
        mock_template = MagicMock()
        mock_template.template_uuid = "TPL-001"
        mock_template_service.get_online_template_by_uuid.return_value = mock_template

        mock_bot_repo.insert_bot.return_value = 10

        mock_device = MagicMock(spec=DeviceResponse)
        mock_device.device_uuid = "DEV-001"
        mock_device_service.create_device.return_value = mock_device

        saved_record = MagicMock(spec=BotRecord)
        saved_record.id = 10
        saved_record.bot_uuid = "BOT-abc"
        saved_record.tenant = "test-tenant"
        saved_record.env = "test"
        saved_record.domain = "default"
        saved_record.is_deleted = 0
        saved_record.creator = "op"
        saved_record.modifier = "op"
        saved_record.status = "PENDING"
        saved_record.name = "my-bot"
        saved_record.description = None
        saved_record.template_uuid = "TPL-001"
        saved_record.replica_desired = 1
        saved_record.replica_minimum = 1
        saved_record.replica_maximum = 20
        saved_record.auto_scaling_enabled = 0
        saved_record.sla_grade = "enterprise"
        saved_record.gmt_create = datetime(2025, 1, 1)
        saved_record.gmt_modified = datetime(2025, 1, 1)
        saved_record.extra_config = {}

        mock_bot_repo.get_by_id.return_value = saved_record

        bot_config = BotConfig(sla_grade="enterprise")
        data = BotClusterCreate(
            bot_name="my-bot",
            template_uuid="TPL-001",
            device_count=1,
            domain="default",
            operator="op",
            config=bot_config,
        )

        result = await bot_crud_service.create_bot(
            tenant="test-tenant",
            data=data,
        )

        assert result.sla_grade == "enterprise"
        insert_call = mock_bot_repo.insert_bot.call_args.kwargs
        assert insert_call["sla_grade"] == "enterprise"

    @pytest.mark.asyncio
    async def test_create_bot_template_without_template_uuid_attr(
        self,
        mock_bot_repo,
        mock_device_repo,
        mock_rel_repo,
        mock_device_service,
        mock_template_service,
        bot_crud_service,
    ):
        mock_template = MagicMock(spec=[])
        del mock_template.template_uuid
        mock_template_service.get_online_template_by_uuid.return_value = mock_template

        mock_bot_repo.insert_bot.return_value = 10

        mock_device = MagicMock(spec=DeviceResponse)
        mock_device.device_uuid = "DEV-001"
        mock_device_service.create_device.return_value = mock_device

        saved_record = MagicMock(spec=BotRecord)
        saved_record.id = 10
        saved_record.bot_uuid = "BOT-abc"
        saved_record.tenant = "test-tenant"
        saved_record.env = "test"
        saved_record.domain = "default"
        saved_record.is_deleted = 0
        saved_record.creator = "op"
        saved_record.modifier = "op"
        saved_record.status = "PENDING"
        saved_record.name = "my-bot"
        saved_record.description = None
        saved_record.template_uuid = None
        saved_record.replica_desired = 1
        saved_record.replica_minimum = 1
        saved_record.replica_maximum = 10
        saved_record.auto_scaling_enabled = 0
        saved_record.sla_grade = "standard"
        saved_record.gmt_create = datetime(2025, 1, 1)
        saved_record.gmt_modified = datetime(2025, 1, 1)
        saved_record.extra_config = {}

        mock_bot_repo.get_by_id.return_value = saved_record

        data = BotClusterCreate(
            bot_name="my-bot",
            template_uuid="TPL-001",
            device_count=1,
            domain="default",
            operator="op",
        )

        await bot_crud_service.create_bot(
            tenant="test-tenant",
            data=data,
        )

        insert_call = mock_bot_repo.insert_bot.call_args.kwargs
        assert insert_call["template_uuid"] is None


# ==================== Test select_device ====================


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


# ==================== Test get_bot ====================


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


# ==================== Test list_bots ====================


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


# ==================== Test destroy_bot ====================


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
