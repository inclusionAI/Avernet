"""Single-pass metadata plugin mapping + recording fake, unit level.

Covers the production adapter that adapts the existing synchronous
``BcnService`` up-link onto the ``BcsCollaborationMetadataPlugin``
Protocol method semantics with ``Mock(spec=BcnService)`` only (no HTTP,
no network), plus the test-only recording fake's behavior. Every method
performs exactly one bounded transport send — the counts and the
"never registers" guards below pin that single-pass contract.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from typing import Any

import pytest
from unittest.mock import Mock

from agentclaw.community.kernel.bot_manager_sync import DeliveryFailure
from agentclaw.community.kernel.collaboration_repair import (
    MetadataDeliveryError,
    MetadataDeliveryErrorCode,
    ProfileSnapshot,
    RepairPreconditionError,
    RepairPreconditionErrorCode,
)
from agentclaw.community.core.bot_management.services.bcn_service import (
    BcnBotMissingError,
    BcnRequestError,
    BcnService,
    BcnServiceError,
    BcnUnavailableError,
)
from agentclaw.community.plugin_api.bcs_collaboration_metadata import (
    BcsCollaborationMetadataPlugin,
)
from agentclaw.community.plugin_api.impl_registry import IMPL_REGISTRY, Flavor, Mode
from agentclaw.community.plugins.local.bcs_collaboration_metadata import (
    RecordingBcsCollaborationMetadataPlugin,
)

from agentclaw.community.plugins.community.bcs_collaboration_metadata import (
    BcnCollaborationMetadataAdapter,
)

_PROFILE = ProfileSnapshot("b", "b:e", "N", "D", "")


def _assert_delivery(excinfo: Any, kind: DeliveryFailure, code: str) -> None:
    """Assert a raised ``MetadataDeliveryError`` carries the exact mapping."""
    error = excinfo.value
    assert isinstance(error, MetadataDeliveryError)
    assert error.kind == kind
    assert error.code == code


def _assert_precondition(excinfo: Any, code: str) -> None:
    error = excinfo.value
    assert isinstance(error, RepairPreconditionError)
    assert error.code == code


def _assert_single_send_only(bcn: Mock, operation: str) -> None:
    """One bounded send for ``operation``; no other transport method called."""
    for name in (
        "get_attributes",
        "onboard_bot",
        "sync_bot_caller_config",
        "register_provider_bot",
    ):
        transport_method = getattr(bcn, name)
        if name == operation:
            assert transport_method.call_count == 1, name
        else:
            transport_method.assert_not_called()


# ---------------------------------------------------------------------------
# exists — single provider-scoped get_attributes probe
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_exists_confirms_present_with_single_bounded_get():
    bcn = Mock(spec=BcnService)
    bcn.get_attributes.return_value = {"user_visibility": "public"}
    port = BcnCollaborationMetadataAdapter(bcn, profile_headers={})
    assert await port.exists("b:e", timeout_seconds=5) is True
    assert bcn.get_attributes.call_args.kwargs == {
        "bot_uuid": "b:e",
        "timeout_seconds": 5,
    }
    _assert_single_send_only(bcn, "get_attributes")


@pytest.mark.asyncio
async def test_exists_reports_absent_only_on_typed_missing_bot():
    bcn = Mock(spec=BcnService)
    bcn.get_attributes.side_effect = BcnBotMissingError("BCS attributes: 404")
    port = BcnCollaborationMetadataAdapter(bcn, profile_headers={})
    assert await port.exists("b:e", timeout_seconds=5) is False
    assert bcn.get_attributes.call_count == 1
    bcn.onboard_bot.assert_not_called()
    bcn.register_provider_bot.assert_not_called()


@pytest.mark.asyncio
async def test_exists_preserves_typed_unavailable_as_not_false():
    bcn = Mock(spec=BcnService)
    bcn.get_attributes.side_effect = BcnUnavailableError("BCN API: 503")
    port = BcnCollaborationMetadataAdapter(bcn, profile_headers={})
    with pytest.raises(MetadataDeliveryError) as excinfo:
        await port.exists("b:e", timeout_seconds=5)
    _assert_delivery(
        excinfo,
        DeliveryFailure.UNKNOWN,
        MetadataDeliveryErrorCode.COLLABORATION_SYNC_UNAVAILABLE,
    )
    assert bcn.get_attributes.call_count == 1


@pytest.mark.asyncio
async def test_exists_preserves_typed_timeout_as_not_false():
    bcn = Mock(spec=BcnService)
    bcn.get_attributes.side_effect = BcnRequestError(
        DeliveryFailure.UNKNOWN, "timeout", "BCS attributes get timeout"
    )
    port = BcnCollaborationMetadataAdapter(bcn, profile_headers={})
    with pytest.raises(MetadataDeliveryError) as excinfo:
        await port.exists("b:e", timeout_seconds=5)
    _assert_delivery(
        excinfo, DeliveryFailure.UNKNOWN, MetadataDeliveryErrorCode.DELIVERY_TIMEOUT
    )


@pytest.mark.asyncio
async def test_exists_skipped_environment_gate_is_precondition_not_absent():
    bcn = Mock(spec=BcnService)
    bcn.get_attributes.return_value = {"skipped": True}
    port = BcnCollaborationMetadataAdapter(bcn, profile_headers={})
    with pytest.raises(RepairPreconditionError) as excinfo:
        await port.exists("b:e", timeout_seconds=5)
    _assert_precondition(
        excinfo, RepairPreconditionErrorCode.COLLABORATION_SYNC_UNAVAILABLE
    )


@pytest.mark.asyncio
async def test_exists_rejects_malformed_2xx_body_as_unknown():
    bcn = Mock(spec=BcnService)
    bcn.get_attributes.return_value = ["not", "an", "attributes-object"]
    port = BcnCollaborationMetadataAdapter(bcn, profile_headers={})
    with pytest.raises(MetadataDeliveryError) as excinfo:
        await port.exists("b:e", timeout_seconds=5)
    _assert_delivery(
        excinfo,
        DeliveryFailure.UNKNOWN,
        MetadataDeliveryErrorCode.COLLABORATION_RESPONSE_MALFORMED,
    )


# ---------------------------------------------------------------------------
# update_profile — single onboard send of committed snapshots
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_profile_uses_existing_onboard_only():
    bcn = Mock(spec=BcnService)
    bcn.onboard_bot.return_value = {"bot_uuid": "b:e", "onboarded": True}
    port = BcnCollaborationMetadataAdapter(bcn, profile_headers={})
    await port.update_profile(ProfileSnapshot("b", "b:e", "N", "D", ""), timeout_seconds=2)
    assert bcn.onboard_bot.call_count == 1
    assert bcn.onboard_bot.call_args.kwargs["avatar_url"] == ""
    bcn.register_provider_bot.assert_not_called()


@pytest.mark.asyncio
async def test_profile_send_is_single_bounded_onboard_with_stable_reference():
    bcn = Mock(spec=BcnService)
    bcn.onboard_bot.return_value = {"bot_uuid": "b:e", "onboarded": True}
    port = BcnCollaborationMetadataAdapter(bcn, profile_headers={})
    await port.update_profile(_PROFILE, timeout_seconds=3)
    assert bcn.onboard_bot.call_args.kwargs == {
        "bot_id": "b:e",
        "name": "N",
        "summary": "D",
        "avatar_url": "",
        "timeout_seconds": 3,
    }
    _assert_single_send_only(bcn, "onboard_bot")


@pytest.mark.asyncio
async def test_profile_passes_request_headers_through_only_when_present():
    bcn = Mock(spec=BcnService)
    bcn.onboard_bot.return_value = {"bot_uuid": "b:e", "onboarded": True}
    headers = {"Cookie": "session=yes", "Authorization": "Bearer tok"}
    port = BcnCollaborationMetadataAdapter(bcn, profile_headers=headers)
    await port.update_profile(_PROFILE, timeout_seconds=3)
    assert bcn.onboard_bot.call_args.kwargs["request_headers"] == headers


@pytest.mark.asyncio
async def test_profile_confirm_rejects_2xx_not_onboarded_body():
    bcn = Mock(spec=BcnService)
    bcn.onboard_bot.return_value = {
        "bot_uuid": "b:e",
        "onboarded": False,
        "message": "Bot 未在协作网络注册",
    }
    port = BcnCollaborationMetadataAdapter(bcn, profile_headers={})
    with pytest.raises(MetadataDeliveryError) as excinfo:
        await port.update_profile(_PROFILE, timeout_seconds=3)
    _assert_delivery(
        excinfo,
        DeliveryFailure.REJECTED,
        MetadataDeliveryErrorCode.COLLABORATION_BOT_NOT_FOUND,
    )
    assert bcn.onboard_bot.call_count == 1


@pytest.mark.asyncio
async def test_profile_confirm_rejects_identity_mismatch():
    bcn = Mock(spec=BcnService)
    bcn.onboard_bot.return_value = {"bot_uuid": "other:e", "onboarded": True}
    port = BcnCollaborationMetadataAdapter(bcn, profile_headers={})
    with pytest.raises(MetadataDeliveryError) as excinfo:
        await port.update_profile(_PROFILE, timeout_seconds=3)
    _assert_delivery(
        excinfo,
        DeliveryFailure.UNKNOWN,
        MetadataDeliveryErrorCode.COLLABORATION_RESPONSE_MALFORMED,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    [
        {"onboarded": True},
        {},
        ["not-a-dict"],
        {"onboarded": "yes", "bot_uuid": "b:e"},
        {"onboarded": True, "bot_uuid": ""},
    ],
)
async def test_profile_confirm_rejects_malformed_success_bodies(body):
    bcn = Mock(spec=BcnService)
    bcn.onboard_bot.return_value = body
    port = BcnCollaborationMetadataAdapter(bcn, profile_headers={})
    with pytest.raises(MetadataDeliveryError) as excinfo:
        await port.update_profile(_PROFILE, timeout_seconds=3)
    _assert_delivery(
        excinfo,
        DeliveryFailure.UNKNOWN,
        MetadataDeliveryErrorCode.COLLABORATION_RESPONSE_MALFORMED,
    )


@pytest.mark.asyncio
async def test_profile_skipped_environment_gate_is_precondition():
    bcn = Mock(spec=BcnService)
    bcn.onboard_bot.return_value = {"skipped": True}
    port = BcnCollaborationMetadataAdapter(bcn, profile_headers={})
    with pytest.raises(RepairPreconditionError) as excinfo:
        await port.update_profile(_PROFILE, timeout_seconds=3)
    _assert_precondition(
        excinfo, RepairPreconditionErrorCode.COLLABORATION_SYNC_UNAVAILABLE
    )


@pytest.mark.asyncio
async def test_profile_preserves_typed_missing_bot_as_rejected_not_found():
    bcn = Mock(spec=BcnService)
    bcn.onboard_bot.side_effect = BcnBotMissingError("BCN onboard: 404")
    port = BcnCollaborationMetadataAdapter(bcn, profile_headers={})
    with pytest.raises(MetadataDeliveryError) as excinfo:
        await port.update_profile(_PROFILE, timeout_seconds=3)
    _assert_delivery(
        excinfo,
        DeliveryFailure.REJECTED,
        MetadataDeliveryErrorCode.COLLABORATION_BOT_NOT_FOUND,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "raised,kind,code",
    [
        # Typed specials keep their dedicated kernel code.
        (
            BcnUnavailableError("BCN API: 503"),
            DeliveryFailure.UNKNOWN,
            MetadataDeliveryErrorCode.COLLABORATION_SYNC_UNAVAILABLE,
        ),
        (
            BcnRequestError(DeliveryFailure.UNKNOWN, "timeout", "t"),
            DeliveryFailure.UNKNOWN,
            MetadataDeliveryErrorCode.DELIVERY_TIMEOUT,
        ),
        # ... while every other transport code is bucketed, so no
        # status-shaped token reaches the public report.
        (
            BcnRequestError(DeliveryFailure.REJECTED, "http_403", "m"),
            DeliveryFailure.REJECTED,
            MetadataDeliveryErrorCode.COLLABORATION_REJECTED,
        ),
        (
            BcnRequestError(DeliveryFailure.REJECTED, "http_409", "m"),
            DeliveryFailure.REJECTED,
            MetadataDeliveryErrorCode.COLLABORATION_REJECTED,
        ),
        (
            BcnRequestError(DeliveryFailure.UNKNOWN, "http_500", "m"),
            DeliveryFailure.UNKNOWN,
            MetadataDeliveryErrorCode.COLLABORATION_UPSTREAM_ERROR,
        ),
        (
            BcnRequestError(DeliveryFailure.UNKNOWN, "http_unknown", "m"),
            DeliveryFailure.UNKNOWN,
            MetadataDeliveryErrorCode.COLLABORATION_UPSTREAM_ERROR,
        ),
        (
            BcnRequestError(DeliveryFailure.UNKNOWN, "unknown", "m"),
            DeliveryFailure.UNKNOWN,
            MetadataDeliveryErrorCode.COLLABORATION_UPSTREAM_ERROR,
        ),
        # Defensive branch: the transport does not raise NOT_SENT today.
        (
            BcnRequestError(DeliveryFailure.NOT_SENT, "connect_error", "m"),
            DeliveryFailure.NOT_SENT,
            MetadataDeliveryErrorCode.COLLABORATION_SYNC_UNAVAILABLE,
        ),
        (
            BcnServiceError("BCN API error: upstream failed"),
            DeliveryFailure.UNKNOWN,
            MetadataDeliveryErrorCode.UNKNOWN,
        ),
    ],
)
async def test_profile_buckets_typed_transport_failures(raised, kind, code):
    bcn = Mock(spec=BcnService)
    bcn.onboard_bot.side_effect = raised
    port = BcnCollaborationMetadataAdapter(bcn, profile_headers={})
    with pytest.raises(MetadataDeliveryError) as excinfo:
        await port.update_profile(_PROFILE, timeout_seconds=3)
    _assert_delivery(excinfo, kind, code)
    assert bcn.onboard_bot.call_count == 1


# ---------------------------------------------------------------------------
# cancellation / lifecycle
# ---------------------------------------------------------------------------


class _BlockedGetAttributes:
    """Block the threadparked transport send until released by the test."""

    def __init__(self) -> None:
        self.call_started = threading.Event()
        self.release = threading.Event()
        self.call_finished = threading.Event()

    def __call__(self, **kwargs: Any) -> dict[str, Any]:
        self.call_started.set()
        self.release.wait(timeout=5)
        self.call_finished.set()
        return {"user_visibility": "public"}


async def _wait_for(event: threading.Event, message: str) -> None:
    for _ in range(500):
        if event.is_set():
            return
        await asyncio.sleep(0.01)
    pytest.fail(message)


@pytest.mark.asyncio
async def test_cancelled_probe_starts_no_further_sends():
    bcn = Mock(spec=BcnService)
    blocked = _BlockedGetAttributes()
    bcn.get_attributes.side_effect = blocked
    port = BcnCollaborationMetadataAdapter(bcn, profile_headers={})
    task = asyncio.create_task(port.exists("b:e", timeout_seconds=1))
    await _wait_for(blocked.call_started, "probe send never started")
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    # to_thread cannot hard-stop the in-flight synchronous HTTP send — it is
    # allowed to finish on its own; the adapter must not have started any
    # other transport method while it was cancelled.
    assert blocked.call_started.is_set()
    blocked.release.set()
    await _wait_for(blocked.call_finished, "in-flight send never finished")
    assert bcn.get_attributes.call_count == 1
    bcn.onboard_bot.assert_not_called()
    bcn.sync_bot_caller_config.assert_not_called()
    bcn.register_provider_bot.assert_not_called()


@pytest.mark.asyncio
async def test_aclose_never_closes_the_borrowed_transport_singletons():
    bcn = Mock(spec=BcnService)
    port = BcnCollaborationMetadataAdapter(bcn, profile_headers={})
    # The injector bindings (and the shared bcn HttpClient pool they own)
    # stay alive for the daily BCN path after any repair finishes, so the
    # borrowed transport must see no lifecycle call at all.
    await port.aclose()
    assert bcn.mock_calls == []


# ---------------------------------------------------------------------------
# log hygiene — classification only, never credentials or upstream bodies
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_failures_log_classification_without_headers_or_upstream_body(caplog):
    cookie = "session-secret"
    authorization = "Bearer tok"
    upstream_body = '{"error":"upstream-internal-detail"}'
    bcn = Mock(spec=BcnService)
    bcn.onboard_bot.side_effect = BcnRequestError(
        DeliveryFailure.UNKNOWN, "http_500", f"BCN API HTTP error: {upstream_body}"
    )
    bcn.get_attributes.return_value = {"skipped": True}
    port = BcnCollaborationMetadataAdapter(
        bcn,
        profile_headers={"Cookie": cookie, "Authorization": authorization},
    )

    with caplog.at_level(logging.INFO, logger="start"):
        with pytest.raises(MetadataDeliveryError):
            await port.update_profile(_PROFILE, timeout_seconds=3)
        with pytest.raises(RepairPreconditionError):
            await port.exists("b:e", timeout_seconds=1)

    text = caplog.text
    # Business identity and the finite classification stay loggable.
    assert "operation=profile" in text
    assert "operation=exists" in text
    assert "bcs_bot_id=b:e" in text
    assert "kind=unknown" in text
    assert "code=collaboration_upstream_error" in text
    assert "code=collaboration_sync_unavailable" in text
    # Credentials and raw upstream payloads never do.
    assert cookie not in text
    assert authorization not in text
    assert upstream_body not in text
    assert "upstream-internal-detail" not in text


# ---------------------------------------------------------------------------
# recording local fake (plugins/local) — existing-only fixtures
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fake_exists_answers_membership_only_never_mutating_existing():
    plugin = RecordingBcsCollaborationMetadataPlugin(existing={"b:e"})
    assert await plugin.exists("b:e", timeout_seconds=1) is True
    assert await plugin.exists("gone:x", timeout_seconds=1) is False
    assert plugin.calls == [("exists", "b:e"), ("exists", "gone:x")]
    assert plugin.existing == {"b:e"}


@pytest.mark.asyncio
async def test_fake_profile_stores_only_when_bot_existing():
    snapshot = ProfileSnapshot("b", "b:e", "N", "D", "")
    plugin = RecordingBcsCollaborationMetadataPlugin(existing={"b:e"})
    with pytest.raises(MetadataDeliveryError) as excinfo:
        await plugin.update_profile(
            ProfileSnapshot("g", "gone:x", "N", "D", ""), timeout_seconds=1
        )
    _assert_delivery(
        excinfo,
        DeliveryFailure.NOT_SENT,
        MetadataDeliveryErrorCode.COLLABORATION_BOT_NOT_FOUND,
    )
    assert plugin.profiles == {}
    await plugin.update_profile(snapshot, timeout_seconds=1)
    assert plugin.profiles == {"b:e": snapshot}
    assert plugin.existing == {"b:e"}
    assert plugin.calls[-1] == ("profile", snapshot)


@pytest.mark.asyncio
async def test_fake_injects_scripted_failures_after_recording():
    interrupted = MetadataDeliveryError(
        DeliveryFailure.UNKNOWN, MetadataDeliveryErrorCode.DELIVERY_TIMEOUT
    )
    gated = RepairPreconditionError(
        RepairPreconditionErrorCode.COLLABORATION_SYNC_UNAVAILABLE
    )
    plugin = RecordingBcsCollaborationMetadataPlugin(
        existing={"b:e"},
        failures={"exists": gated, "profile": interrupted},
    )
    with pytest.raises(RepairPreconditionError) as excinfo:
        await plugin.exists("b:e", timeout_seconds=1)
    _assert_precondition(
        excinfo, RepairPreconditionErrorCode.COLLABORATION_SYNC_UNAVAILABLE
    )
    with pytest.raises(MetadataDeliveryError) as excinfo:
        await plugin.update_profile(_PROFILE, timeout_seconds=1)
    _assert_delivery(
        excinfo, DeliveryFailure.UNKNOWN, MetadataDeliveryErrorCode.DELIVERY_TIMEOUT
    )
    assert [operation for operation, _ in plugin.calls] == [
        "exists",
        "profile",
    ]
    assert plugin.profiles == {}


@pytest.mark.asyncio
async def test_fake_call_names_pin_the_consumer_sequence():
    plugin = RecordingBcsCollaborationMetadataPlugin(existing={"b:e"})
    await plugin.exists("b:e", timeout_seconds=1)
    await plugin.update_profile(_PROFILE, timeout_seconds=1)
    assert [operation for operation, _ in plugin.calls] == ["exists", "profile"]


@pytest.mark.asyncio
async def test_fake_aclose_only_marks_closed():
    plugin = RecordingBcsCollaborationMetadataPlugin()
    assert plugin.closed is False
    await plugin.aclose()
    assert plugin.closed is True


def test_fake_provides_no_registration_surface():
    instance = RecordingBcsCollaborationMetadataPlugin()
    assert not hasattr(instance, "register_provider_bot")
    assert not hasattr(instance, "register")


def test_auto_registered_as_local_fake_collaboration_metadata_plugin():
    (entry,) = [
        entry for entry in IMPL_REGISTRY if entry.cls is RecordingBcsCollaborationMetadataPlugin
    ]
    assert entry.protocol is BcsCollaborationMetadataPlugin
    assert entry.mode is Mode.LOCAL and entry.flavor is Flavor.FAKE
