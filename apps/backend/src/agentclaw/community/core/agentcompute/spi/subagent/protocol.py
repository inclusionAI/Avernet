from __future__ import annotations

from typing import Protocol, runtime_checkable

from agentclaw.community.core.agentcompute.domain.models import (
    AgentId, AgentSpec, Run, SubagentResult, TaskEnvelope,
)


@runtime_checkable
class Subagent(Protocol):
    name: str

    async def setup(self) -> None: ...
    async def run_task(self, envelope: TaskEnvelope) -> SubagentResult: ...
    async def teardown(self) -> None: ...
    async def halt(self) -> None: ...

@runtime_checkable
class SubagentFactory(Protocol):

    def create(self, spec: AgentSpec, run: Run,
               agent_id: AgentId) -> Subagent:
        """Build one subagent for a spawned agent row.

        ``agent_id`` is the spawn\'s durable identity: stable across attempts
        AND across D16 respawns of an interrupted agent, and unique between
        two same-named spawns. Subagents that provision remote identities
        (the bot platform kind) derive their platform idempotency key from
        it — the name alone collides: two spawns of one catalog persona
        would share a single remote identity, and one\'s teardown would
        delete the other\'s.
        """
        ...
