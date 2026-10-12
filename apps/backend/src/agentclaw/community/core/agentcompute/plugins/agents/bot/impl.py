"""Avernet-kind subagent driven through the in-process BotPlatform.

No socket leaves this module: the composition root binds the platform's
platform adapter, the subagent talks to it directly. This replaces the
removed HTTP implementation, which dialed backend_base_url over httpx for
endpoints that do not exist on this backend's surface and 404'd in
production-shaped runs.
"""
from __future__ import annotations

import asyncio
import json
import time

from agentclaw.community.core.agentcompute.domain.models import (
    AgentSpecRaw, BotState, Run, SubagentResult, TaskEnvelope,
)
from agentclaw.community.core.agentcompute.runtime.observability import (
    AgentComputeObservability,
)
from agentclaw.community.core.agentcompute.spi.bot import BotPlatform
from agentclaw.community.log import get_logger

logger = get_logger(__name__)

_TERMINAL_FAILURE = frozenset({
    BotState.AUTHORIZATION_REJECTED.value,
    BotState.AUTHORIZATION_EXPIRED.value,
    BotState.CREATE_FAILED.value,
    BotState.APPLY_FAILED.value,
})


class AvernetSubagent:

    def __init__(self, spec: AgentSpecRaw, run: Run,
                 platform: BotPlatform,
                 observability: AgentComputeObservability | None = None) -> None:
        self._spec = spec
        self._run = run
        self._platform = platform
        self._obs = observability
        self._bot_id: str | None = None
        self.name = spec.name

    async def setup(self) -> None:
        # One persona per (run, spec): retries rebind the same platform
        # identity instead of leaking duplicates (D23 fresh context).
        try:
            self._bot_id = self._platform.create_bot(
                name=self._spec.name,
                instructions=self._spec.instructions or "Complete the assigned task.",
                idempotency_key=f"{self._run.run_id}:{self._spec.name}",
            )
        except Exception:
            if self._obs is not None:
                self._obs.llm_call("non_retryable")
            raise
        state = await self._poll_ready(self._bot_id)
        if state != BotState.READY.value:
            if self._obs is not None:
                self._obs.llm_call("exhausted")
            raise RuntimeError(f"bot {self._bot_id} did not reach READY: {state}")

    async def run_task(self, envelope: TaskEnvelope) -> SubagentResult:
        if self._bot_id is None:
            raise RuntimeError("setup not completed")
        prompt = f"objective={envelope.objective!r} boundaries={envelope.boundaries!r}"
        try:
            text = self._platform.chat(bot_id=self._bot_id, message=prompt)
        except Exception:
            if self._obs is not None:
                self._obs.llm_call("exhausted")
            raise
        if self._obs is not None:
            self._obs.llm_call("ok")
        try:
            output = json.loads(text)
        except Exception:
            output = {"text": text}
        return SubagentResult(output=output)

    async def teardown(self) -> None:
        # Best-effort by contract: a failed delete never fails the slice,
        # the platform's own TTL/lifecycle owns the orphan.
        if self._bot_id is not None:
            try:
                self._platform.delete_bot(self._bot_id)
            except Exception as exc:
                logger.warning("[agentcompute][avernet] bot delete failed: %s", exc)
            finally:
                self._bot_id = None

    async def halt(self) -> None:
        await self.teardown()

    async def _poll_ready(self, bot_id: str, timeout: float = 300.0,
                          interval: float = 1.0) -> str:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            state = self._platform.bot_status(bot_id)
            if state == BotState.READY.value:
                return state
            if state in _TERMINAL_FAILURE:
                raise RuntimeError(f"bot {bot_id} creation failed: {state}")
            await asyncio.sleep(interval)
        return "TIMEOUT"