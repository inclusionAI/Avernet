from __future__ import annotations

import asyncio
import logging

from agentclaw.community.core.agentswarm.domain.models import (
    AgentId, AgentSpec, CompletionRequest, LlmCallKind,
    Run, SubagentResult, TaskEnvelope, ModelId, spawn_digest,
)
from agentclaw.community.plugin_api.agentswarm.llm import LLMProvider

logger = logging.getLogger(__name__)


class LLMBackedSubagent:

    def __init__(self, spec: AgentSpec, run: Run, llm: LLMProvider,
                  agent_id: AgentId, attempt: int = 1,
                  setup_delay_seconds: float = 0.0) -> None:
        self._spec = spec
        self._run = run
        self._llm = llm
        self._agent_id = agent_id
        self._attempt = attempt
        self._setup_delay_seconds = float(setup_delay_seconds)
        self.name = spec.name
        # Executor surfaces (D-J, additive SPI): the bare per-attempt
        # digest — an execution identity even for an llm runtime, but
        # with no platform binding: nothing to provision, nothing to
        # destroy, and the terminal sweep skips journal rows of this kind.
        self.executor_id = spawn_digest(
            str(run.run_id), str(agent_id), attempt, 16)
        self.executor_name = (
            f"{spec.name}_"
            f"{spawn_digest(str(run.run_id), str(agent_id), attempt, 12)}"
        )

    async def setup(self) -> None:
        if self._setup_delay_seconds > 0:
            await asyncio.sleep(self._setup_delay_seconds)
        logger.debug("[agentswarm][llm-subagent] %s setup", self.name)

    async def run_task(self, envelope: TaskEnvelope) -> SubagentResult:
        prompt = (
            f"role={self._spec.role!r} "
            f"instructions={self._spec.instructions!r} "
            f"objective={envelope.objective!r} "
            f"boundaries={envelope.boundaries!r} "
            f"parent_goal={envelope.parent_goal!r}"
        )
        request = CompletionRequest(
            kind=LlmCallKind.SUBAGENT,
            model=ModelId(self._spec.model) if self._spec.model
                else ModelId(self._run.run_options.model_name) if getattr(self._run, 'run_options', None) and self._run.run_options.model_name
                else None,
            system_prompt=prompt,
            user_prompt=envelope.objective,
        )
        response = await self._llm.complete(request)
        import json
        try:
            output = json.loads(response.text)
        except Exception:
            output = {"text": response.text}
        return SubagentResult(
            output=output,
            token_usage=response.token_usage,
        )

    async def teardown(self) -> None:
        logger.debug("[agentswarm][llm-subagent] %s teardown", self.name)

    async def halt(self) -> None:
        pass