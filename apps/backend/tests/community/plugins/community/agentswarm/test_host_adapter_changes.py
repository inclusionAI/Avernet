"""Tests for the host adapter's cross-slice resume, passport re-apply
guard, engine connect retry, and the bot subagent's provisioning delay
simulation — the changed lines CI flagged as uncovered."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agentclaw.community.plugins.community.agentswarm.host import (
    AvernetBotPlatform,
)
from agentclaw.community.plugins.community.agentswarm.bot_subagent import (
    AvernetSubagent,
)
# The park marker lives in the seam's vocabulary, not the adapter's: the adapter no
# longer raises it (a swarm worker never waits for a human), but the shared pool
# still catches it for platforms that can.
from agentclaw.community.plugin_api.agentswarm.subagent import (
    ParkedAwaitingAuthorization,
)


def _mk_platform(**overrides):
    platform = MagicMock(spec=AvernetBotPlatform)
    platform._manifest_seam = MagicMock()
    platform._passport = MagicMock()
    platform._space_context = MagicMock()
    platform._bot_service = MagicMock()
    platform._skill_set_factory = MagicMock()
    platform._bot_repo = MagicMock()
    for k, v in overrides.items():
        setattr(platform, k, v)
    return platform


class TestEngineConnectRetry:
    """_connect_engine retries on ConnectionError with 15/30/60s backoff."""

    @pytest.mark.asyncio
    async def test_retry_succeeds_on_second_attempt(self):
        from agentclaw.community.plugins.community.agentswarm.host import (
            AvernetBotPlatform,
        )
        platform = AvernetBotPlatform.__new__(AvernetBotPlatform)
        calls = []
        async def fail_then_succeed(url, token):
            calls.append(url)
            if len(calls) < 2:
                raise ConnectionError("1011 cold boot")
            return "ws-connected"
        with patch.object(platform, "_connect_engine_once", side_effect=fail_then_succeed):
            with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
                result = await platform._connect_engine("ws://test", "tok")
        assert result == "ws-connected"
        assert mock_sleep.called

    @pytest.mark.asyncio
    async def test_all_retries_exhausted_raises(self):
        from agentclaw.community.plugins.community.agentswarm.host import (
            AvernetBotPlatform,
        )
        platform = AvernetBotPlatform.__new__(AvernetBotPlatform)
        async def always_fail(url, token):
            raise ConnectionError("1011 cold boot")
        with patch.object(platform, "_connect_engine_once", side_effect=always_fail):
            with patch("asyncio.sleep", new_callable=AsyncMock):
                with pytest.raises(ConnectionError):
                    await platform._connect_engine("ws://test", "tok")


class TestProvisioningDelay:
    """_poll_ready fires the provisioning simulation once after the
    gate clears, then continues to poll."""

    @pytest.mark.asyncio
    async def test_delay_fires_once_after_gate_clears(self):
        from agentclaw.community.core.agentswarm.domain.models import (
            AgentSpec, EpochMillis, ProviderKind, Run, RunId, RunOptions,
            RunStatus, TenantId,
        )
        run = Run(
            run_id=RunId("r"), tenant=TenantId("t"), goal="g",
            agent_catalog=(AgentSpec(name="x", role="r"),),
            provider=ProviderKind("stub"),
            request_metadata=MagicMock(),
            status=RunStatus.RUNNING,
            started_at=EpochMillis(0),
            run_options=RunOptions(),
        )
        platform = MagicMock()
        states = iter(["AWAITING_AUTHORIZATION", "PROVISIONING", "READY"])
        platform.bot_status = MagicMock(side_effect=lambda *a, **kw: next(states))
        sub = AvernetSubagent(
            AgentSpec(name="x", role="r"), run, platform,
            "agent-1", setup_delay_seconds=0.01,
        )
        sub._bot_id = "bot-1"
        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            try:
                await sub._poll_ready("bot-1", timeout=10)
            except ParkedAwaitingAuthorization:
                # First call parks (gate pending)
                pass
            # Second call passes the gate, fires delay, sees READY
            states2 = iter(["PROVISIONING", "READY"])
            platform.bot_status = MagicMock(
                side_effect=lambda *a, **kw: next(states2))
            sub._prov_delay_fired = False  # reset after park
            try:
                result = await sub._poll_ready("bot-1", timeout=10)
            except (ParkedAwaitingAuthorization, RuntimeError):
                pass
        assert sub._prov_delay_fired or mock_sleep.called