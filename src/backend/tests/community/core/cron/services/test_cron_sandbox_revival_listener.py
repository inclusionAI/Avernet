"""DeviceActivatedEvent clears a binding's sandbox-destroyed verdict.

PR #1635 review (P0): a sandbox rebuild under the same binding fires
DeviceActivatedEvent, and the old destroyed verdict must not blind the
auto_setup idempotency check (``cron_auto_setup.py`` reads only
``list_all_crons``'s ``data`` — skips are invisible) or the listings.

Covered: clearing removes the verdict and un-skips fetches; clearing an
unknown binding is a no-op; the listener subscribes exactly once; an event
clears the matching verdict; relay errors are swallowed (an event handler
must never break the bus).
"""
from __future__ import annotations

import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from agentclaw.community.core.cron.services.cron_relay import CronRelayService
from agentclaw.community.core.cron.services.cron_runtime_targets import (
    SANDBOX_DESTROYED_TTL_SECONDS,
    CronRuntimeTarget,
)
from agentclaw.community.core.cron.services.cron_sandbox_revival_listener import (
    CronSandboxRevivalListener,
)
from agentclaw.community.core.events.bus import get_event_bus, reset_event_bus
from agentclaw.community.core.events.types import DeviceActivatedEvent


def _make_service() -> CronRelayService:
    svc = CronRelayService(
        bot_provider=MagicMock(),
        device_provider=MagicMock(),
        transport=MagicMock(),
        resolver=MagicMock(),
        template_repo=MagicMock(),
        publish_repo=MagicMock(),
    )
    invoke = AsyncMock(return_value={"success": True, "data": []})
    ctx = SimpleNamespace(conn_info={"url": "http://adapter"})
    svc._prepare_runtime_query_async = AsyncMock(return_value=(ctx, None))
    svc._invoke_transport = invoke
    return svc


def _target(binding_id: int) -> CronRuntimeTarget:
    return CronRuntimeTarget(
        bot_id="bot-1",
        bot_name="bot-1",
        owner_id="user-1",
        bot_type="service",
        runtime_stage="draft",
        binding_id=binding_id,
    )


def _event(binding_id: int) -> DeviceActivatedEvent:
    return DeviceActivatedEvent(
        device_id="BOT-1",
        binding_id=binding_id,
        entity_id="staff-u1",
        entity_type="staff",
        device_provider="baas",
    )


@pytest.mark.asyncio
async def test_clear_verdict_un_skips_subsequent_fetch():
    svc = _make_service()
    svc._sandbox_down_until[9] = time.monotonic() + SANDBOX_DESTROYED_TTL_SECONDS

    cleared = svc.clear_sandbox_down_verdict(9)

    assert cleared is True
    assert 9 not in svc._sandbox_down_until
    # binding is fetchable again — transport invoked
    result = await svc._fetch_runtime_target_crons(_target(9), "user-1")
    assert result["success"] is True
    svc._invoke_transport.assert_awaited_once()


def test_clear_unknown_binding_is_noop():
    svc = _make_service()

    assert svc.clear_sandbox_down_verdict(404) is False
    assert svc._sandbox_down_until == {}


@pytest.mark.asyncio
async def test_startup_subscribes_exactly_once():
    reset_event_bus()
    relay = _make_service()
    listener = CronSandboxRevivalListener(cron_relay=relay)

    await listener.startup()
    await listener.startup()

    from agentclaw.community.core.events.types import DeviceActivatedEvent as E

    assert get_event_bus()._handlers[E] == [listener._handle]
    reset_event_bus()


def test_event_clears_matching_verdict():
    relay = _make_service()
    relay._sandbox_down_until[11] = time.monotonic() + SANDBOX_DESTROYED_TTL_SECONDS
    listener = CronSandboxRevivalListener(cron_relay=relay)

    listener._handle(_event(11))

    assert 11 not in relay._sandbox_down_until


def test_event_for_unmarked_binding_is_noop():
    relay = _make_service()
    listener = CronSandboxRevivalListener(cron_relay=relay)

    listener._handle(_event(12))

    assert relay._sandbox_down_until == {}


def test_handle_swallows_relay_errors():
    relay = _make_service()
    relay.clear_sandbox_down_verdict = MagicMock(
        side_effect=RuntimeError("relay exploded")
    )
    listener = CronSandboxRevivalListener(cron_relay=relay)

    # must not raise — one broken handler must never break the bus loop
    listener._handle(_event(13))
