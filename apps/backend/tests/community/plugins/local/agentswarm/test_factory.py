"""SPI contract pins for DefaultSubagentFactory.

The SubagentFactory protocol requires ``agent_id: AgentId`` with no
default: platform idempotency keys derive from it, so a name fallback
would collide same-named spawns (one's teardown could delete the
other's bot). These tests pin the required signature and the identity
derivation through the factory itself.
"""
from __future__ import annotations

import asyncio
import inspect
import time

import pytest

from agentclaw.community.core.agentswarm.domain import models as dm
from agentclaw.community.core.agentswarm.domain.models import (
    AgentSpec, EpochMillis, ProviderKind, RequestMetadata, Run, RunId,
    RunStatus, TenantId,
)
from agentclaw.community.plugins.community.agentswarm.avernet_subagent import (
    AvernetSubagent,
)
from agentclaw.community.plugins.local.agentswarm.factory import (
    DefaultSubagentFactory,
)
from agentclaw.community.plugins.local.agentswarm.llm_stub import StubLLMProvider
from agentclaw.community.plugins.local.agentswarm.subagent_llm import (
    LLMBackedSubagent,
)

_LLM_SPEC = dm.AgentSpec(name="s1", role="research", kind=dm.AgentKind.LLM)
_AVENET_SPEC = dm.AgentSpec(name="scout", role="worker", kind=dm.AgentKind.AVENET)


def _mk_run(run_id="run-42") -> Run:
    return Run(
        run_id=RunId(run_id),
        tenant=TenantId("tenant-a"),
        goal="goal",
        agent_catalog=(),
        provider=ProviderKind("avernet"),
        request_metadata=RequestMetadata(),
        status=RunStatus.RUNNING,
        started_at=EpochMillis(int(time.time() * 1000)),
    )


class _RecordingPlatform:
    """Minimal BotPlatform double: records (spec, idempotency_key)."""

    def __init__(self) -> None:
        self.created: list[tuple] = []

    def create_bot(self, *, spec, idempotency_key):
        self.created.append((spec, idempotency_key))
        return "bot-42"

    def bot_status(self, bot_id):
        return dm.BotState.READY.value

    def chat(self, bot_id, message):
        return "{}"

    def delete_bot(self, bot_id):
        return True


class TestFactorySignature:

    def test_inspect_pins_agent_id_as_required(self):
        params = inspect.signature(DefaultSubagentFactory.create).parameters
        assert "agent_id" in params
        assert params["agent_id"].default is inspect.Parameter.empty

    def test_create_without_agent_id_raises_type_error(self):
        factory = DefaultSubagentFactory(
            StubLLMProvider(), platform=_RecordingPlatform())
        run = _mk_run()
        with pytest.raises(TypeError):
            factory.create(_LLM_SPEC, run)
        with pytest.raises(TypeError):
            factory.create(_AVENET_SPEC, run)

    def test_create_with_agent_id_builds_subagents(self):
        factory = DefaultSubagentFactory(
            StubLLMProvider(), platform=_RecordingPlatform())
        run = _mk_run()
        llm_sub = factory.create(_LLM_SPEC, run, dm.AgentId("agent-s1"))
        assert isinstance(llm_sub, LLMBackedSubagent)
        avernet_sub = factory.create(
            _AVENET_SPEC, run, dm.AgentId("agent-scout"))
        assert isinstance(avernet_sub, AvernetSubagent)

    def test_factory_requires_agent_id_protocol_conformance(self):
        # The runtime_checkable protocol must accept the concrete factory —
        # runtime_checkable checks method presence, not signatures, so the
        # signature pin above is the enforcement point for the contract.
        from agentclaw.community.plugin_api.agentswarm.subagent import (
            SubagentFactory,
        )

        assert isinstance(DefaultSubagentFactory(StubLLMProvider()),
                          SubagentFactory)


class TestSpawnIdentity:

    def test_same_named_avernet_spawns_get_distinct_idempotency_keys(self):
        platform = _RecordingPlatform()
        factory = DefaultSubagentFactory(llm=None, platform=platform)
        run = _mk_run()
        spec = dm.AgentSpec(name="scout", role="worker",
                            kind=dm.AgentKind.AVENET)

        first = factory.create(spec, run, dm.AgentId("agent-one"))
        second = factory.create(spec, run, dm.AgentId("agent-two"))

        async def _setup_both():
            await first.setup()
            await second.setup()

        asyncio.run(_setup_both())
        keys = [key for _, key in platform.created]
        assert keys == ["run-42:agent-one", "run-42:agent-two"]

    def test_same_identity_across_run_respawn_rebinds_one_key(self):
        platform = _RecordingPlatform()
        factory = DefaultSubagentFactory(llm=None, platform=platform)
        run = _mk_run()
        spec = dm.AgentSpec(name="scout", role="worker",
                            kind=dm.AgentKind.AVENET)

        first = factory.create(spec, run, dm.AgentId("agent-1"))
        asyncio.run(first.setup())
        asyncio.run(first.teardown())

        respawn = factory.create(spec, run, dm.AgentId("agent-1"))
        asyncio.run(respawn.setup())

        keys = [key for _, key in platform.created]
        assert keys == ["run-42:agent-1", "run-42:agent-1"]