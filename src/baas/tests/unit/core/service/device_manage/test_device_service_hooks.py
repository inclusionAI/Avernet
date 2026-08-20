"""Unit tests for ARCA TTL renewal schedule hooks in DefaultDeviceService.

Covers the schedule_repo integration points added for DeadlineRenewalScheduler.
"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from secbaas.community.api.device_manage import (
    ArcaCreationResult,
    DeployConfig,
    DeviceConfig,
    DeviceStatus,
    LocalCreationResult,
)
from secbaas.community.api.template_manage import (
    ArcaTemplateConfig,
    DeviceTemplateManageService,
    LocalTemplateConfig,
    TemplateStatus,
)
from secbaas.community.core.repository.device import DeviceRecord
from secbaas.community.core.service.device_manage import DefaultDeviceService

DS = "secbaas.community.core.service.device_manage._device_service"


@pytest.fixture(autouse=True)
def _mock_env():
    with patch(f"{DS}.get_current_env", return_value="test"):
        yield


# ── Helpers ────────────────────────────────────────────────────────────


def _make_arca_record(**overrides):
    """Minimal ARCA ACTIVE DeviceRecord for stop/destroy hook tests."""
    defaults = {
        "id": 1,
        "device_uuid": "DEVICE-test-001",
        "tenant": "test-tenant",
        "env": "test",
        "domain": "default",
        "status": DeviceStatus.ACTIVE.value,
        "provider_type": "ARCA",
        "provider_device_id": "sandbox-abc123",
        "provider_device_props": {},
        "extra_config": None,
        "err_msg": None,
        "creator": "test-user",
        "modifier": "test-user",
        "gmt_create": datetime(2026, 1, 1),
        "gmt_modified": datetime(2026, 1, 1),
        "is_deleted": False,
    }
    defaults.update(overrides)
    return DeviceRecord(**defaults)


def _make_arca_template():
    config = ArcaTemplateConfig(
        type="ARCA",
        base_url="https://arca.example.com",
        api_key="test-key",
        arca_template_id="tpl-1",
    )
    mock = MagicMock(spec=DeviceTemplateManageService)
    mock.template_uuid = "tpl-uuid-arca"
    mock.tenant = "test-tenant"
    mock.config = config
    mock.status = TemplateStatus.ONLINE
    mock.name = "ARCA Template"
    return mock


def _make_arca_deploy_config():
    return DeployConfig(domain="default")


# ── Constructor ────────────────────────────────────────────────────────


class TestScheduleRepoInjection:
    def test_schedule_repo_defaults_to_none(self):
        svc = DefaultDeviceService(
            paas_facade=MagicMock(),
            repository=MagicMock(),
            device_template_service=MagicMock(),
            secret_plugin=MagicMock(),
            callback_handler=MagicMock(),
        )
        assert svc._schedule_repo is None

    def test_schedule_repo_stored_when_provided(self):
        mock_repo = MagicMock()
        svc = DefaultDeviceService(
            paas_facade=MagicMock(),
            repository=MagicMock(),
            device_template_service=MagicMock(),
            secret_plugin=MagicMock(),
            callback_handler=MagicMock(),
            schedule_repo=mock_repo,
        )
        assert svc._schedule_repo is mock_repo


# ── start_device hook ──────────────────────────────────────────────────


class TestStartDeviceHook:
    @pytest.mark.asyncio
    async def test_arca_start_device_registers_schedule(self):
        mock_repo = MagicMock()
        mock_template_svc = MagicMock()
        mock_paas_facade = MagicMock()
        mock_schedule_repo = MagicMock()
        mock_callback = MagicMock(handle=AsyncMock(return_value={"status": "ok"}))

        pending = DeviceRecord(
            id=1,
            device_uuid="DEVICE-test-001",
            tenant="test-tenant",
            env="test",
            domain="default",
            status=DeviceStatus.PENDING.value,
            provider_type=None,
            provider_device_id=None,
            provider_device_props=None,
            extra_config=DeviceConfig(
                deploy_config=_make_arca_deploy_config(),
                template_uuid="tpl-uuid-arca",
            ).model_dump(exclude_none=True),
            err_msg=None,
            creator="test-user",
            modifier="test-user",
            gmt_create=datetime(2026, 1, 1),
            gmt_modified=datetime(2026, 1, 1),
            is_deleted=False,
        )
        mock_repo.get_by_device_uuid.return_value = pending
        mock_repo.update_device_start_info = MagicMock()
        mock_repo.get_by_id.return_value = DeviceRecord(
            id=1,
            device_uuid="DEVICE-test-001",
            tenant="test-tenant",
            env="test",
            domain="default",
            status=DeviceStatus.ACTIVE.value,
            provider_type="ARCA",
            provider_device_id="sandbox-abc123",
            provider_device_props={"ttl_expiration_time": 1750000000000},
            extra_config=None,
            err_msg=None,
            creator="test-user",
            modifier="test-user",
            gmt_create=datetime(2026, 1, 1),
            gmt_modified=datetime(2026, 1, 1),
            is_deleted=False,
        )
        mock_template_svc.get_default_or_explicit_template.return_value = (
            _make_arca_template()
        )
        arca_result = ArcaCreationResult(
            platform="ARCA", status="ACTIVE", template_id="tpl-1",
            sandbox_id="sandbox-abc123",
        )
        mock_paas_facade.create_device = AsyncMock(return_value=arca_result)

        svc = DefaultDeviceService(
            paas_facade=mock_paas_facade,
            repository=mock_repo,
            device_template_service=mock_template_svc,
            secret_plugin=MagicMock(),
            callback_handler=mock_callback,
            schedule_repo=mock_schedule_repo,
        )

        # provider_device_props = creation_result.model_dump().
        # The real Arca API includes ttl_expiration_time; patch the class
        # method so the hook path sees a TTL in the dumped dict.
        _ttl_dump = {
            "platform": "ARCA",
            "status": "ACTIVE",
            "template_id": "tpl-1",
            "sandbox_id": "sandbox-abc123",
            "ttl_expiration_time": 1750000000000,
        }
        with patch.object(ArcaCreationResult, "model_dump", return_value=_ttl_dump):
            await svc.start_device(
                tenant="test-tenant", device_uuid="DEVICE-test-001"
            )

        mock_schedule_repo.register.assert_called_once()
        call_args = mock_schedule_repo.register.call_args
        assert call_args[1]["sandbox_id"] == "sandbox-abc123"
        assert call_args[1]["source_table"] == "baas_device"
        assert call_args[1]["source_id"] == 1

    @pytest.mark.asyncio
    async def test_non_arca_device_skips_schedule_register(self):
        mock_repo = MagicMock()
        mock_template_svc = MagicMock()
        mock_paas_facade = MagicMock()
        mock_schedule_repo = MagicMock()
        mock_callback = MagicMock(handle=AsyncMock(return_value={"status": "ok"}))

        pending = DeviceRecord(
            id=1,
            device_uuid="DEVICE-test-001",
            tenant="test-tenant",
            env="test",
            domain="default",
            status=DeviceStatus.PENDING.value,
            provider_type=None,
            provider_device_id=None,
            provider_device_props=None,
            extra_config=DeviceConfig(
                deploy_config=DeployConfig(domain="default"),
                template_uuid="tpl-uuid-local",
            ).model_dump(exclude_none=True),
            err_msg=None,
            creator="test-user",
            modifier="test-user",
            gmt_create=datetime(2026, 1, 1),
            gmt_modified=datetime(2026, 1, 1),
            is_deleted=False,
        )
        mock_repo.get_by_device_uuid.return_value = pending
        mock_repo.update_device_start_info = MagicMock()
        mock_repo.get_by_id.return_value = DeviceRecord(
            id=1,
            device_uuid="DEVICE-test-001",
            tenant="test-tenant",
            env="test",
            domain="default",
            status=DeviceStatus.ACTIVE.value,
            provider_type="LOCAL",
            provider_device_id="container-001",
            provider_device_props=None,
            extra_config=None,
            err_msg=None,
            creator="test-user",
            modifier="test-user",
            gmt_create=datetime(2026, 1, 1),
            gmt_modified=datetime(2026, 1, 1),
            is_deleted=False,
        )

        local_template = MagicMock()
        local_template.template_uuid = "tpl-uuid-local"
        local_template.tenant = "test-tenant"
        local_template.config = LocalTemplateConfig()
        local_template.status = TemplateStatus.ONLINE

        # For LOCAL, start_device validates required fields from deploy_config
        pending_extra = DeviceConfig.model_validate(pending.extra_config)
        pending_extra.deploy_config.machine_id = "m-001"
        pending_extra.deploy_config.user_id = "u-001"
        pending_extra.deploy_config.tc_bot_id = "tb-001"
        pending_extra.deploy_config.agent_code = "ac-001"

        mock_template_svc.get_default_or_explicit_template.return_value = local_template
        mock_paas_facade.create_device = AsyncMock(
            return_value=LocalCreationResult(
            platform="LOCAL", status="ACTIVE", container_id="container-001"
        )
        )

        svc = DefaultDeviceService(
            paas_facade=mock_paas_facade,
            repository=mock_repo,
            device_template_service=mock_template_svc,
            secret_plugin=MagicMock(),
            callback_handler=mock_callback,
            schedule_repo=mock_schedule_repo,
        )

        await svc.start_device(tenant="test-tenant", device_uuid="DEVICE-test-001")

        mock_schedule_repo.register.assert_not_called()

    @pytest.mark.asyncio
    async def test_schedule_register_failure_is_non_blocking(self):
        mock_repo = MagicMock()
        mock_template_svc = MagicMock()
        mock_paas_facade = MagicMock()
        mock_schedule_repo = MagicMock()
        mock_callback = MagicMock(handle=AsyncMock(return_value={"status": "ok"}))

        mock_schedule_repo.register.side_effect = RuntimeError("DB down")

        pending = DeviceRecord(
            id=1,
            device_uuid="DEVICE-test-001",
            tenant="test-tenant",
            env="test",
            domain="default",
            status=DeviceStatus.PENDING.value,
            provider_type=None,
            provider_device_id=None,
            provider_device_props=None,
            extra_config=DeviceConfig(
                deploy_config=_make_arca_deploy_config(),
                template_uuid="tpl-uuid-arca",
            ).model_dump(exclude_none=True),
            err_msg=None,
            creator="test-user",
            modifier="test-user",
            gmt_create=datetime(2026, 1, 1),
            gmt_modified=datetime(2026, 1, 1),
            is_deleted=False,
        )
        mock_repo.get_by_device_uuid.return_value = pending
        mock_repo.update_device_start_info = MagicMock()
        mock_repo.get_by_id.return_value = DeviceRecord(
            id=1,
            device_uuid="DEVICE-test-001",
            tenant="test-tenant",
            env="test",
            domain="default",
            status=DeviceStatus.ACTIVE.value,
            provider_type="ARCA",
            provider_device_id="sandbox-abc123",
            provider_device_props={"ttl_expiration_time": 1750000000000},
            extra_config=None,
            err_msg=None,
            creator="test-user",
            modifier="test-user",
            gmt_create=datetime(2026, 1, 1),
            gmt_modified=datetime(2026, 1, 1),
            is_deleted=False,
        )
        mock_template_svc.get_default_or_explicit_template.return_value = (
            _make_arca_template()
        )
        arca_result = ArcaCreationResult(
            platform="ARCA", status="ACTIVE", template_id="tpl-1",
            sandbox_id="sandbox-abc123",
        )
        mock_paas_facade.create_device = AsyncMock(return_value=arca_result)

        svc = DefaultDeviceService(
            paas_facade=mock_paas_facade,
            repository=mock_repo,
            device_template_service=mock_template_svc,
            secret_plugin=MagicMock(),
            callback_handler=mock_callback,
            schedule_repo=mock_schedule_repo,
        )

        # Need TTL in model_dump for register() to be called (and raise)
        _ttl_dump = {
            "platform": "ARCA",
            "status": "ACTIVE",
            "template_id": "tpl-1",
            "sandbox_id": "sandbox-abc123",
            "ttl_expiration_time": 1750000000000,
        }
        with patch.object(ArcaCreationResult, "model_dump", return_value=_ttl_dump):
            result = await svc.start_device(
                tenant="test-tenant", device_uuid="DEVICE-test-001"
            )
        assert result is not None


# ── stop_device / destroy_device hooks ─────────────────────────────────


class TestStopDestroyHooks:
    @pytest.mark.asyncio
    async def test_stop_device_set_status_stopped(self):
        mock_repo = MagicMock()
        mock_paas_facade = MagicMock()
        mock_schedule_repo = MagicMock()

        arca_record = _make_arca_record()
        mock_repo.get_active_or_updating_by_device_uuid.return_value = arca_record
        mock_paas_facade.destroy_device = AsyncMock(return_value=True)

        svc = DefaultDeviceService(
            paas_facade=mock_paas_facade,
            repository=mock_repo,
            device_template_service=MagicMock(),
            secret_plugin=MagicMock(),
            callback_handler=MagicMock(),
            schedule_repo=mock_schedule_repo,
        )

        await svc.stop_device_by_uuid(
            tenant="test-tenant", device_uuid="DEVICE-test-001", modifier="test"
        )

        mock_schedule_repo.set_status.assert_called_once()
        _, kwargs = mock_schedule_repo.set_status.call_args
        assert kwargs["source_table"] == "baas_device"
        assert kwargs["source_id"] == arca_record.id
        assert kwargs["status"] == "STOPPED"

    @pytest.mark.asyncio
    async def test_destroy_device_set_status_stopped(self):
        mock_repo = MagicMock()
        mock_paas_facade = MagicMock()
        mock_schedule_repo = MagicMock()

        arca_record = _make_arca_record()
        mock_repo.get_active_or_updating_by_device_uuid.return_value = arca_record
        mock_paas_facade.destroy_device = AsyncMock(return_value=True)

        svc = DefaultDeviceService(
            paas_facade=mock_paas_facade,
            repository=mock_repo,
            device_template_service=MagicMock(),
            secret_plugin=MagicMock(),
            callback_handler=MagicMock(),
            schedule_repo=mock_schedule_repo,
        )

        await svc.destroy_device_by_uuid(
            tenant="test-tenant",
            device_uuid="DEVICE-test-001",
            modifier="test",
            for_restart=False,
        )

        mock_schedule_repo.set_status.assert_called_once()
        _, kwargs = mock_schedule_repo.set_status.call_args
        assert kwargs["source_id"] == arca_record.id
        assert kwargs["status"] == "STOPPED"