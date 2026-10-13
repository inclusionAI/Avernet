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

from agentclaw.community.core.agentswarm.domain.models import (
    AgentId, AgentSpec, BotCreateSpec, BotState, IdempotencyKey, Run,
    SubagentResult, TaskEnvelope,
)
from agentclaw.community.plugin_api.agentswarm.bot import BotPlatform
from agentclaw.community.log import get_logger

logger = get_logger(__name__)

_TERMINAL_FAILURE = frozenset({
    BotState.AUTHORIZATION_REJECTED.value,
    BotState.AUTHORIZATION_EXPIRED.value,
    BotState.CREATE_FAILED.value,
    BotState.APPLY_FAILED.value,
})


class AvernetSubagent:

    def __init__(self, spec: AgentSpec, run: Run,
                 platform: BotPlatform,
                 agent_id: AgentId | None = None) -> None:
        self._spec = spec
        self._run = run
        self._platform = platform
        self._agent_id = agent_id
        self._bot_id: str | None = None
        self.name = spec.name

    async def setup(self) -> None:
        # One persona per (run, spec): retries rebind the same platform
        # identity instead of leaking duplicates (D23 fresh context).
        try:
            self._bot_id = self._platform.create_bot(
                spec=BotCreateSpec(
                    name=self._spec.name,
                    description=self._spec.role,
                    instructions=self._spec.instructions,
                    model=self._spec.model,
                    engine=self._spec.engine,
                    bot_type=self._spec.bot_type,
                    identity_file=self._spec.identity_file,
                    metadata=dict(self._spec.metadata),
                ),
                # Platform identity follows the spawn's durable identity:
                # agent_id is stable across attempts AND across D16
                # respawns (rebind, not duplicate), and unique between two
                # same-named spawns (each spawns its own bot; one's
                # teardown can never delete the other's). The name remains
                # only as the fallback for hand construction with no agent
                # row behind it.
                idempotency_key=IdempotencyKey(
                    f"{self._run.run_id}:"
                    f"{self._agent_id or self._spec.name}"),
            )
        except Exception:
            raise
        if not (self._bot_id or "").strip():
            # A blank identifier is a contract violation, not a slow boot:
            # fail now instead of polling bot_status(None) to the deadline.
            raise RuntimeError(
                f"platform returned invalid bot_id: {self._bot_id!r}")
        state = await self._poll_ready(self._bot_id)
        if state != BotState.READY.value:
            raise RuntimeError(f"bot {self._bot_id} did not reach READY: {state}")

    async def run_task(self, envelope: TaskEnvelope) -> SubagentResult:
        if self._bot_id is None:
            raise RuntimeError("setup not completed")
        prompt = f"objective={envelope.objective!r} boundaries={envelope.boundaries!r}"
        try:
            # The seam is sync by contract; the chat leg is an engine round
            # trip measured in seconds-to-minutes, so awaiting it inline
            # would stall the loop's slice and starve the lease heartbeat
            # until a spurious reclaim rips the job away mid-chat (#4):
            # offload the call to a thread, keep the loop breathing.
            text = await asyncio.to_thread(
                self._platform.chat, bot_id=self._bot_id, message=prompt)
        except Exception:
            raise
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
                logger.warning("[agentswarm][avernet] bot delete failed: %s", exc)
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