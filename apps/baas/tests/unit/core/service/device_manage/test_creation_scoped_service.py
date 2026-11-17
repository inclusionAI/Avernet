"""Trusted creation scope is isolated from metadata and concurrent requests."""

import asyncio
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from secbaas.community.api.device_manage.creation_context import (
    DeviceCreationContext,
    device_creation_scope,
    get_device_creation_context,
)
from secbaas.community.core.repository.device import DeviceRecord
from secbaas.community.core.service.device_manage import DefaultDeviceService
from secbaas.community.core.service.device_manage import _device_service as lifecycle
from secbaas.community.core.service.device_manage._creation_scoped_service import (
    CreationScopedDeviceService,
)
from secbaas.community.core.service.device_manage._device_service_arca_ttl import (
    ArcaScheduleAwareDeviceService,
)


def record(device="DEVICE-one", tenant="tenant", env="test"):
    return DeviceRecord(
        id=1,
        gmt_create=datetime(2026, 1, 1),
        gmt_modified=datetime(2026, 1, 1),
        device_uuid=device,
        tenant=tenant,
        env=env,
        domain="",
        is_deleted=0,
        creator="test",
        modifier="test",
        status="PENDING",
        provider_type="ARCA",
        provider_device_id="old-sandbox@1",
        provider_device_props={},
        extra_config={"metadata": {"tenant": "forged", "device_uuid": "other"}},
        err_msg=None,
    )


def service(records):
    result = CreationScopedDeviceService.__new__(CreationScopedDeviceService)
    result._repository = MagicMock()
    result._repository.list_by_device_uuid.return_value = records
    return result


def test_bootstrap_and_schedule_variant_include_scope():
    assert DefaultDeviceService is CreationScopedDeviceService
    assert issubclass(ArcaScheduleAwareDeviceService, CreationScopedDeviceService)


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["start_device", "update_device"])
async def test_identity_comes_from_record_and_reaches_worker_thread(
    monkeypatch, method
):
    monkeypatch.setattr(
        "secbaas.community.core.service.device_manage._creation_scoped_service.get_current_env",
        lambda: "test",
    )
    seen = []

    async def operation(self, tenant, device_uuid, modifier, publish_id):
        seen.append(await asyncio.to_thread(get_device_creation_context))
        return "created"

    with patch.object(lifecycle.DefaultDeviceService, method, operation):
        result = await getattr(service([record()]), method)(
            "tenant", "DEVICE-one", publish_id=7
        )
    assert result == "created"
    assert seen == [
        DeviceCreationContext(
            "tenant", "test", "DEVICE-one", "publish:7:device:1", "old-sandbox@1"
        )
    ]
    assert get_device_creation_context() is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "records", [[], [record(), record()], [record(tenant="other")]]
)
async def test_ambiguous_records_do_not_inherit_an_outer_grant(monkeypatch, records):
    monkeypatch.setattr(
        "secbaas.community.core.service.device_manage._creation_scoped_service.get_current_env",
        lambda: "test",
    )

    async def operation(*args):
        assert get_device_creation_context() is None

    outer = DeviceCreationContext("tenant", "test", "DEVICE-other", "outer", None)
    with device_creation_scope(outer):
        with patch.object(lifecycle.DefaultDeviceService, "start_device", operation):
            await service(records).start_device("tenant", "DEVICE-one")
        assert get_device_creation_context() == outer
    assert get_device_creation_context() is None


@pytest.mark.asyncio
async def test_failure_restores_scope(monkeypatch):
    monkeypatch.setattr(
        "secbaas.community.core.service.device_manage._creation_scoped_service.get_current_env",
        lambda: "test",
    )

    async def operation(*args):
        assert get_device_creation_context() is not None
        raise RuntimeError("write failed")

    with patch.object(lifecycle.DefaultDeviceService, "update_device", operation):
        with pytest.raises(RuntimeError, match="write failed"):
            await service([record()]).update_device("tenant", "DEVICE-one")
    assert get_device_creation_context() is None


@pytest.mark.asyncio
async def test_concurrent_devices_do_not_share_identity(monkeypatch):
    monkeypatch.setattr(
        "secbaas.community.core.service.device_manage._creation_scoped_service.get_current_env",
        lambda: "test",
    )
    both_started = asyncio.Event()
    entered = []

    async def operation(self, tenant, device_uuid, modifier, publish_id):
        entered.append(device_uuid)
        if len(entered) == 2:
            both_started.set()
        await asyncio.wait_for(both_started.wait(), timeout=2)
        context = await asyncio.to_thread(get_device_creation_context)
        assert context.device_uuid == device_uuid

    with patch.object(lifecycle.DefaultDeviceService, "start_device", operation):
        await asyncio.gather(
            service([record("DEVICE-a")]).start_device("tenant", "DEVICE-a"),
            service([record("DEVICE-b")]).start_device("tenant", "DEVICE-b"),
        )
    assert get_device_creation_context() is None
