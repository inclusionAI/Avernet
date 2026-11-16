"""Tests for DefaultPublishAdminService.update_device_status — admin device repair.

Covers:
- status + provider_device_id written in one repository call
- provider_device_id only, leaving status unchanged
- status only, leaving the provider binding unchanged
- empty update rejected before any repository access
- missing device raises PublishNotFoundError without a repository write
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from secbaas.community.api.device_manage import DeviceStatus
from secbaas.community.api.publish_manage import PublishNotFoundError
from secbaas.community.core.service.publish_manage._admin_service import (
    DefaultPublishAdminService,
)


def _make_service(device_repo: MagicMock) -> DefaultPublishAdminService:
    return DefaultPublishAdminService(
        publish_repo=MagicMock(),
        batch_repo=MagicMock(),
        record_repo=MagicMock(),
        device_repo=device_repo,
        bot_repo=MagicMock(),
        rel_repo=MagicMock(),
    )


def _make_repair_device(
    *,
    device_uuid: str = "DEV-1",
    status: str = "PENDING",
    provider_device_id: str | None = "sandbox-old",
    tenant: str = "acme",
    env: str = "prod",
) -> MagicMock:
    device = MagicMock()
    device.device_uuid = device_uuid
    device.status = status
    device.provider_device_id = provider_device_id
    device.tenant = tenant
    device.env = env
    return device


# ============== update_device_status ==============


def _make_repair_device(
    *,
    device_uuid: str = "DEV-1",
    status: str = "PENDING",
    provider_device_id: str | None = "sandbox-old",
    tenant: str = "acme",
    env: str = "prod",
) -> MagicMock:
    device = MagicMock()
    device.device_uuid = device_uuid
    device.status = status
    device.provider_device_id = provider_device_id
    device.tenant = tenant
    device.env = env
    return device


class TestUpdateDeviceStatus:
    """Device repair: status and/or provider binding plus operator audit stamp."""

    async def test_status_and_provider_device_id_written_in_one_call(self):
        device = _make_repair_device()
        device_repo = MagicMock()
        device_repo.get_by_device_uuid_only.return_value = device
        service = _make_service(device_repo=device_repo)

        result = await service.update_device_status(
            device_uuid="DEV-1",
            tenant="acme",
            status=DeviceStatus.ACTIVE,
            provider_device_id="sandbox-1",
            operator="ops-user",
        )

        device_repo.update_status_by_device_uuid.assert_called_once_with(
            device_uuid="DEV-1",
            tenant="acme",
            env="prod",
            status="ACTIVE",
            modifier="ops-user",
            provider_device_id="sandbox-1",
        )
        assert result.previous_status == "PENDING"
        assert result.new_status == "ACTIVE"
        assert result.previous_provider_device_id == "sandbox-old"
        assert result.new_provider_device_id == "sandbox-1"

    async def test_provider_device_id_only_leaves_status_unchanged(self):
        device = _make_repair_device(status="ACTIVE")
        device_repo = MagicMock()
        device_repo.get_by_device_uuid_only.return_value = device
        service = _make_service(device_repo=device_repo)

        result = await service.update_device_status(
            device_uuid="DEV-1",
            tenant="acme",
            status=None,
            provider_device_id="sandbox-2",
            operator="ops-user",
        )

        device_repo.update_status_by_device_uuid.assert_called_once_with(
            device_uuid="DEV-1",
            tenant="acme",
            env="prod",
            status="ACTIVE",
            modifier="ops-user",
            provider_device_id="sandbox-2",
        )
        assert result.new_status == "ACTIVE"
        assert result.previous_status == "ACTIVE"
        assert result.new_provider_device_id == "sandbox-2"

    async def test_status_only_preserves_provider_binding(self):
        device = _make_repair_device(provider_device_id="sandbox-keep")
        device_repo = MagicMock()
        device_repo.get_by_device_uuid_only.return_value = device
        service = _make_service(device_repo=device_repo)

        result = await service.update_device_status(
            device_uuid="DEV-1",
            tenant="acme",
            status=DeviceStatus.FAILED,
            operator="ops-user",
        )

        device_repo.update_status_by_device_uuid.assert_called_once_with(
            device_uuid="DEV-1",
            tenant="acme",
            env="prod",
            status="FAILED",
            modifier="ops-user",
            provider_device_id=None,
        )
        assert result.new_provider_device_id == "sandbox-keep"

    async def test_empty_update_is_rejected_without_repo_write(self):
        device_repo = MagicMock()
        service = _make_service(device_repo=device_repo)

        with pytest.raises(ValueError, match="status or provider_device_id"):
            await service.update_device_status(
                device_uuid="DEV-1",
                tenant="acme",
                status=None,
                operator="ops-user",
            )

        device_repo.get_by_device_uuid_only.assert_not_called()
        device_repo.update_status_by_device_uuid.assert_not_called()

    async def test_missing_device_raises_not_found_without_repo_write(self):
        device_repo = MagicMock()
        device_repo.get_by_device_uuid_only.return_value = None
        service = _make_service(device_repo=device_repo)

        with pytest.raises(PublishNotFoundError, match="Device DEV-x not found"):
            await service.update_device_status(
                device_uuid="DEV-x",
                tenant="acme",
                status=DeviceStatus.ACTIVE,
                operator="ops-user",
            )

        device_repo.update_status_by_device_uuid.assert_not_called()
