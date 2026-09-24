"""Conformance suite for SessionInitiator (Rule 25).

Validates that the LOCAL impl (``CronRelaySessionInitiator``, bound by the
base ``TaskDiscoveryModule`` provider) satisfies the SessionInitiator Plugin
Protocol when injected via the ``world`` fixture.

The PROD impl (``OpenApiBotSessionInitiator``, BaaS Open API) lives corp-side
under ``corp/plugins/prod`` and is exercised by corp-side contract tests.
"""
from __future__ import annotations

import asyncio
import os
from unittest.mock import AsyncMock, MagicMock

import pytest

from agentclaw.community.core.task.task_discovery.models import (
    DiscoveredTask,
    DiscoverySession,
)
from agentclaw.community.core.task.task_discovery.session_initiator import (
    CronRelaySessionInitiator,
)
from agentclaw.community.plugin_api.session_initiator import SessionInitiator

# Skip under corp profiles — the corp column rebinds this key to the corp
# OpenApiBot impl, so the CronRelay contract is not the bound executable spec.
_allow_profiles = {"test", "community", "singlebox"}
_current_profile = os.environ.get("DEPLOY_PROFILE", "test").lower()
pytestmark = pytest.mark.skipif(
    _current_profile not in _allow_profiles,
    reason=f"CronRelaySessionInitiator contract only valid under {_allow_profiles}, "
    f"got DEPLOY_PROFILE={_current_profile!r}",
)

_DT = "2026-09-14"


def _make_task(*, title: str = "Contract task") -> DiscoveredTask:
    return DiscoveredTask(
        task_id="td-contract-1",
        bot_id="bot-1",
        owner_id="owner-1",
        dt=_DT,
        title=title,
        instruction="Do the thing",
        background="ctx",
        discovery_basis="basis",
    )


def test_local_impl_satisfies_protocol(world):
    """The bound impl is the LOCAL CronRelay impl and satisfies the Protocol."""
    initiator = world.get(SessionInitiator)
    assert isinstance(initiator, CronRelaySessionInitiator)
    assert isinstance(initiator, SessionInitiator)


def test_local_impl_creates_session_via_relay(world, monkeypatch):
    """Consumer ↔ Protocol conformance: with a stubbed relay (forward_request
    returns a session id) and engine-target resolution disabled, the local
    impl returns a DiscoverySession tied to the first task."""
    initiator = world.get(SessionInitiator)
    assert isinstance(initiator, CronRelaySessionInitiator)

    stub_relay = MagicMock()
    stub_relay.forward_request = AsyncMock(
        return_value={"success": True, "data": {"id": "sess-contract-1"}}
    )
    monkeypatch.setattr(initiator, "_cron_relay", stub_relay, raising=False)
    # Skip engine-target resolution → WS injection path is skipped by design.
    monkeypatch.setattr(
        initiator, "_extract_engine_target", AsyncMock(return_value=None)
    )

    result = asyncio.run(
        initiator.initiate_session(
            [_make_task()],
            bot_id="bot-1",
            owner_id="owner-1",
            agent_id="agent-1",
        )
    )
    assert isinstance(result, DiscoverySession)
    assert result.session_id == "sess-contract-1"
    assert result.task_id == "td-contract-1"
    stub_relay.forward_request.assert_awaited_once()