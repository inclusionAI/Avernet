"""Shared fixtures for bot_manage service tests.

Provides reusable mocks for repository layer, DeviceService, and environment
to keep individual test files focused on business logic.
"""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from secbaas.api.bot_manage import BotStatus
from secbaas.api.device_manage import (
    DeviceConfig,
    DeviceResponse,
    DeviceStatus,
)
from secbaas.core.repository.bot import BotRecord
from secbaas.core.repository.bot_device_rel import BotDeviceRelRecord
from secbaas.core.repository.device import DeviceRecord

# ==================== Book Builders ====================


def make_bot_record(
    bot_id: int = 1,
    bot_uuid: str = "BOT-aabbccdd",
    tenant: str = "test-tenant",
    env: str = "test",
    status: BotStatus = BotStatus.ACTIVE,
    name: str = "test-bot",
    description: str | None = "A test bot",
    template_uuid: str | None = "TPL-001",
    replica_desired: int = 1,
    replica_minimum: int = 1,
    replica_maximum: int = 10,
    auto_scaling_enabled: int = 0,
    sla_grade: str = "standard",
    extra_config: dict | None = None,
    domain: str = "default",
    creator: str = "user1",
    modifier: str = "user1",
    is_deleted: int = 0,
) -> BotRecord:
    """Build a BotRecord dataclass instance for testing."""
    return BotRecord(
        id=bot_id,
        gmt_create=datetime(2025, 1, 1),
        gmt_modified=datetime(2025, 1, 1),
        bot_uuid=bot_uuid,
        tenant=tenant,
        env=env,
        domain=domain,
        is_deleted=is_deleted,
        creator=creator,
        modifier=modifier,
        status=status.value if isinstance(status, BotStatus) else status,
        name=name,
        description=description,
        template_uuid=template_uuid,
        replica_desired=replica_desired,
        replica_minimum=replica_minimum,
        replica_maximum=replica_maximum,
        auto_scaling_enabled=auto_scaling_enabled,
        sla_grade=sla_grade,
        extra_config=extra_config or {},
    )


def make_device_record(
    device_id: int = 101,
    device_uuid: str = "DEV-aabbccdd",
    tenant: str = "test-tenant",
    env: str = "test",
    status: DeviceStatus = DeviceStatus.ACTIVE,
    domain: str = "default",
    provider_type: str | None = "ARCA",
    provider_device_id: str | None = "sandbox-123",
) -> DeviceRecord:
    """Build a DeviceRecord dataclass instance for testing."""
    return DeviceRecord(
        id=device_id,
        gmt_create=datetime(2025, 1, 1),
        gmt_modified=datetime(2025, 1, 1),
        device_uuid=device_uuid,
        tenant=tenant,
        env=env,
        domain=domain,
        is_deleted=0,
        creator="user1",
        modifier="user1",
        status=status.value if isinstance(status, DeviceStatus) else status,
        provider_type=provider_type,
        provider_device_id=provider_device_id,
        provider_device_props={},
        extra_config={},
    )


def make_device_response(
    device_id: int = 101,
    device_uuid: str = "DEV-aabbccdd",
    status: str = "ACTIVE",
) -> DeviceResponse:
    """Build a minimal DeviceResponse for testing."""
    return DeviceResponse(
        id=device_id,
        device_uuid=device_uuid,
        tenant="test-tenant",
        env="test",
        domain="default",
        status=status,
        provider_type="ARCA",
        provider_device_id="sandbox-123",
        provider_device_props={},
        extra_config=DeviceConfig(),
        creator="user1",
        modifier="user1",
        gmt_create=datetime(2025, 1, 1),
        gmt_modified=datetime(2025, 1, 1),
    )


def make_rel_record(
    rel_id: int = 1,
    bot_id: int = 1,
    device_uuid: str = "DEV-aabbccdd",
    tenant: str = "test-tenant",
    env: str = "test",
) -> BotDeviceRelRecord:
    """Build a BotDeviceRelRecord dataclass instance for testing."""
    return BotDeviceRelRecord(
        id=rel_id,
        gmt_create=datetime(2025, 1, 1),
        gmt_modified=datetime(2025, 1, 1),
        tenant=tenant,
        env=env,
        domain="default",
        is_deleted=0,
        creator="user1",
        modifier="user1",
        bot_id=bot_id,
        device_uuid=device_uuid,
    )


# ==================== Module-level patches (autouse) ====================


@pytest.fixture(autouse=True)
def _patch_env():
    """Autouse: patch get_current_env so tests never depend on real env."""
    with patch(
        "secbaas.core.service.bot_manage._bot_service.get_current_env",
        return_value="test",
    ):
        yield


# ==================== Repository mocks ====================


@pytest.fixture
def mock_bot_repo():
    """Mock BotRepository."""
    return MagicMock()


@pytest.fixture
def mock_device_repo():
    """Mock DeviceRepository."""
    return MagicMock()


@pytest.fixture
def mock_rel_repo():
    """Mock BotDeviceRelRepository."""
    return MagicMock()


# ==================== Service instance fixture ====================


@pytest.fixture
def bot_crud_service(
    mock_bot_repo,
    mock_device_repo,
    mock_rel_repo,
    mock_device_service,
    mock_template_service,
):
    """Build a DefaultBotCrudService with mocked dependencies."""
    from secbaas.core.service.bot_manage import DefaultBotCrudService

    return DefaultBotCrudService(
        bot_repo=mock_bot_repo,
        device_repo=mock_device_repo,
        rel_repo=mock_rel_repo,
        device_template_service=mock_template_service,
        device_service=mock_device_service,
    )


# ==================== DeviceService mock ====================


@pytest.fixture
def mock_device_service():
    """Mock DefaultDeviceService used by create_bot and destroy_bot."""
    svc = MagicMock()
    yield svc


# ==================== Template service mock ====================


@pytest.fixture
def mock_template_service():
    """Mock DefaultDeviceTemplateService."""
    svc = MagicMock()
    yield svc


# ==================== Helper for async context ====================


@pytest.fixture
def make_async(mocker):
    """Convert a MagicMock method into an AsyncMock."""

    def _wrap(mock_obj, method_name, return_value=None, side_effect=None):
        async_mock = AsyncMock(return_value=return_value, side_effect=side_effect)
        setattr(mock_obj, method_name, async_mock)
        return async_mock

    return _wrap
