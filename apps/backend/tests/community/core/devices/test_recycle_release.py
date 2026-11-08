"""Pinned provider release proof must not inherit legacy DB-only success."""

from unittest.mock import MagicMock

import pytest

from agentclaw.community.core.devices.services.baas_device_service import (
    BaasDeviceService,
)
from agentclaw.community.core.devices.services.local_device_service import (
    LocalDeviceService,
)
from agentclaw.community.core.devices.services.device_service import DeviceService
from agentclaw.community.core.devices.services.device_service_router import (
    DeviceServiceRouter,
)
from agentclaw.community.core.devices.errors import DeviceServiceError


@pytest.fixture(params=[BaasDeviceService, LocalDeviceService])
def baas(request):
    service = object.__new__(request.param)
    service._baas_service = MagicMock()
    service._baas_service.get_recycle_release_status.return_value = "ACTIVE"
    service._baas_service.list_devices_by_bot_uuid.return_value = []
    return service


def test_baas_receipt_is_not_release_confirmation(baas):
    baas._baas_service.destroy_bot.return_value = {
        "publish_id": 42,
        "secret": "not persisted",
    }
    result = baas.submit_recycle_release(
        target={"bot_uuid": "b1"}, request_id="op1", owner_id="u1"
    )
    assert result == {"publish_id": 42, "request_id": "op1"}
    baas._baas_service.get_publish_progress.return_value = {"status": "APPROVING"}
    assert (
        baas.observe_recycle_release(target={"bot_uuid": "b1"}, release=result)
        == "UNKNOWN"
    )


def test_baas_empty_inventory_alone_is_unknown(baas):
    assert (
        baas.observe_recycle_release(target={"bot_uuid": "b1"}, release=None)
        == "UNKNOWN"
    )


def test_baas_workflow_success_does_not_hide_live_device(baas):
    baas._baas_service.get_publish_progress.return_value = {"status": "SUCCESS"}
    baas._baas_service.list_devices_by_bot_uuid.return_value = [{"status": "ACTIVE"}]
    assert (
        baas.observe_recycle_release(
            target={"bot_uuid": "b1"}, release={"publish_id": 42}
        )
        == "UNKNOWN"
    )


def test_baas_explicit_terminal_inventory_and_release_can_recover_lost_post(baas):
    baas._baas_service.get_recycle_release_status.return_value = "RELEASED"
    assert (
        baas.observe_recycle_release(target={"bot_uuid": "b1"}, release=None)
        == "RELEASED"
    )


def test_baas_failed_query_is_not_absence(baas):
    baas._baas_service.get_recycle_release_status.side_effect = TimeoutError(
        "query timed out"
    )
    with pytest.raises(TimeoutError):
        baas.observe_recycle_release(target={"bot_uuid": "b1"}, release=None)


def test_router_never_falls_back_to_a_different_provider():
    router = object.__new__(DeviceServiceRouter)
    router._providers = {"arca": MagicMock()}
    with pytest.raises(DeviceServiceError):
        router.submit_recycle_release(
            target={"provider": "missing"}, request_id="op1", owner_id="u1"
        )
    router._providers["arca"].submit_recycle_release.assert_not_called()


def test_unsupported_provider_fails_closed_without_legacy_release():
    service = object.__new__(DeviceService)
    service._do_release = MagicMock()
    with pytest.raises(DeviceServiceError):
        service.submit_recycle_release(target={}, request_id="op1", owner_id="u1")
    service._do_release.assert_not_called()


@pytest.mark.parametrize("typed", [False, True])
def test_baas_untyped_404_is_not_release_evidence(typed):
    import httpx
    from agentclaw.community.core.service_bot.services.baas_service import BaasService

    service = object.__new__(BaasService)
    request = httpx.Request("GET", "https://example.invalid/api/v1/bots/b1")
    response = httpx.Response(
        404,
        request=request,
        json={
            "detail": {"error_code": "BOT_NOT_FOUND" if typed else "NOT_FOUND"},
        },
    )
    service._get_bots_api = MagicMock(
        side_effect=httpx.HTTPStatusError(
            "not found", request=request, response=response
        )
    )
    if typed:
        assert service.get_recycle_release_status(bot_uuid="b1") == "RELEASED"
    else:
        with pytest.raises(httpx.HTTPStatusError):
            service.get_recycle_release_status(bot_uuid="b1")


def test_missing_logical_bot_does_not_hide_orphan_physical_device(baas):
    from agentclaw.community.core.service_bot.services.baas_service import (
        BaasBotNotFoundError,
    )

    baas._baas_service.destroy_bot.side_effect = BaasBotNotFoundError("absent")
    receipt = baas.submit_recycle_release(
        target={"bot_uuid": "b1"}, request_id="op1", owner_id="u1"
    )
    baas._baas_service.get_recycle_release_status.return_value = "RELEASED"
    baas._baas_service.list_devices_by_bot_uuid.return_value = [{"status": "ACTIVE"}]
    assert (
        baas.observe_recycle_release(target={"bot_uuid": "b1"}, release=receipt)
        == "UNKNOWN"
    )
