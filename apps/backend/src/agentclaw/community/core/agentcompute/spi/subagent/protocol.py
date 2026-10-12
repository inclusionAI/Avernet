from __future__ import annotations

from typing import Protocol, runtime_checkable

from agentclaw.community.core.agentcompute.domain.models import (
    AgentSpec, Run, SubagentResult, TaskEnvelope,
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

    def create(self, spec: AgentSpec, run: Run) -> Subagent: ...
