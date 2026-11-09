"""Pinned provider release proof must not inherit legacy DB-only success."""

from unittest.mock import MagicMock

import pytest
from agentclaw.community.core.bot_management.engines.aicoding.recycle_release import (
    ArcaRecycleTransport,
    observe_baas_release,
    submit_baas_release,
    submit_release,
)


@pytest.fixture
def baas():
    service = MagicMock()
    service.get_recycle_release_status.return_value = "ACTIVE"
    service.list_devices_by_bot_uuid.return_value = []
    return service


def test_baas_receipt_is_not_release_confirmation(baas):
    baas.destroy_bot.return_value = {
        "publish_id": 42,
        "secret": "not persisted",
    }
    result = submit_baas_release(
        baas, target={"bot_uuid": "b1"}, request_id="op1", owner_id="u1"
    )
    assert result == {"publish_id": 42, "request_id": "op1"}
    baas.get_publish_progress.return_value = {"status": "APPROVING"}
    assert (
        observe_baas_release(baas, target={"bot_uuid": "b1"}, release=result)
        == "UNKNOWN"
    )


def test_baas_empty_inventory_alone_is_unknown(baas):
    assert (
        observe_baas_release(baas, target={"bot_uuid": "b1"}, release=None) == "UNKNOWN"
    )


def test_baas_workflow_success_does_not_hide_live_device(baas):
    baas.get_publish_progress.return_value = {"status": "SUCCESS"}
    baas.list_devices_by_bot_uuid.return_value = [{"status": "ACTIVE"}]
    assert (
        observe_baas_release(
            baas, target={"bot_uuid": "b1"}, release={"publish_id": 42}
        )
        == "UNKNOWN"
    )


def test_baas_explicit_terminal_inventory_and_release_can_recover_lost_post(baas):
    baas.get_recycle_release_status.return_value = "RELEASED"
    assert (
        observe_baas_release(baas, target={"bot_uuid": "b1"}, release=None)
        == "RELEASED"
    )


def test_baas_failed_query_is_not_absence(baas):
    baas.get_recycle_release_status.side_effect = TimeoutError("query timed out")
    with pytest.raises(TimeoutError):
        observe_baas_release(baas, target={"bot_uuid": "b1"}, release=None)


def test_unknown_provider_never_resolves_a_different_runtime():
    dependencies, arca = MagicMock(), MagicMock()
    with pytest.raises(RuntimeError, match="provider unavailable"):
        submit_release(
            dependencies,
            arca,
            target={"provider": "missing"},
            request_id="op1",
            owner_id="u1",
        )
    dependencies.baas_runtime.assert_not_called()
    arca.assert_not_called()


def test_unconfigured_arca_transport_fails_closed():
    transport = ArcaRecycleTransport()
    with pytest.raises(RuntimeError):
        transport.submit({"sandbox_id": "s1"}, "op1")
    assert transport.observe({"sandbox_id": "s1"}) == "UNKNOWN"


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

    baas.destroy_bot.side_effect = BaasBotNotFoundError("absent")
    receipt = submit_baas_release(
        baas, target={"bot_uuid": "b1"}, request_id="op1", owner_id="u1"
    )
    baas.get_recycle_release_status.return_value = "RELEASED"
    baas.list_devices_by_bot_uuid.return_value = [{"status": "ACTIVE"}]
    assert (
        observe_baas_release(baas, target={"bot_uuid": "b1"}, release=receipt)
        == "UNKNOWN"
    )
