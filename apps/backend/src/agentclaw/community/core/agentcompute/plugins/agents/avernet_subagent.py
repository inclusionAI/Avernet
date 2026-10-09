from __future__ import annotations

import json
import uuid
from typing import Any

import httpx

from agentclaw.community.core.agentcompute.config.settings import AgentComputeAvernetConfig
from agentclaw.community.core.agentcompute.domain.models import (
    AgentSpecRaw, BotState, ModelId, Run, SubagentResult, TaskEnvelope,
)
from agentclaw.community.core.agentcompute.observability import (
    AgentComputeObservability,
)
from agentclaw.community.log import get_logger

logger = get_logger(__name__)

_TERMINAL_FAILURE = frozenset({
    BotState.AUTHORIZATION_REJECTED.value,
    BotState.AUTHORIZATION_EXPIRED.value,
    BotState.CREATE_FAILED.value,
    BotState.APPLY_FAILED.value,
})


class AvernetSubagent:

    def __init__(self, spec: AgentSpecRaw, run: Run, llm: Any,
                 config: AgentComputeAvernetConfig | None = None,
                 observability: AgentComputeObservability | None = None,
                 egress: Any = None) -> None:
        from agentclaw.community.core.agentcompute.security import validate_egress_url
        self._spec = spec
        self._run = run
        self._config = config or AgentComputeAvernetConfig()
        self._obs = observability
        if egress is not None:
            validate_egress_url(
                self._config.backend_base_url,
                allowed_hosts=egress.allowed_hosts,
                require_https=egress.require_https,
                surface="avernet",
            )
        self._client = httpx.AsyncClient(
            base_url=self._config.backend_base_url,
            timeout=self._config.http_timeout,
        )
        self._bot_id: str | None = None
        self.name = spec.name

    async def setup(self) -> None:
        manifest = json.dumps({
            "name": self._spec.name,
            "instructions": self._spec.instructions or "Complete the assigned task.",
            "idempotency_key": f"agentcompute:{self._run.run_id}:{self._spec.name}",
        })
        resp = await self._client.post(
            self._config.bot_create_path,
            json={"manifest": manifest},
        )
        resp.raise_for_status()
        data = resp.json()
        self._bot_id = data.get("data", {}).get("bot_id")
        if not self._bot_id:
            raise RuntimeError(f"bot creation did not return bot_id: {data}")
        state = await self._poll_ready(self._bot_id)
        if state != BotState.READY.value:
            raise RuntimeError(f"bot {self._bot_id} did not reach READY: {state}")

    async def run_task(self, envelope: TaskEnvelope) -> SubagentResult:
        if self._bot_id is None:
            raise RuntimeError("setup not completed")
        prompt = f"objective={envelope.objective!r} boundaries={envelope.boundaries!r}"
        try:
            resp = await self._client.post(
                "/openapi/v1/chat/stream",
                json={"bot_id": self._bot_id, "message": prompt},
            )
            resp.raise_for_status()
        except httpx.HTTPStatusError:
            if self._obs is not None:
                self._obs.llm_call("non_retryable")
            raise
        except (httpx.TimeoutException, httpx.ConnectError, httpx.ReadError):
            if self._obs is not None:
                self._obs.llm_call("exhausted")
            raise
        if self._obs is not None:
            self._obs.llm_call("ok")
        text = resp.text
        try:
            output = json.loads(text)
        except Exception:
            output = {"text": text}
        return SubagentResult(output=output)

    async def teardown(self) -> None:
        if self._bot_id is not None:
            try:
                path = self._config.bot_delete_path.format(bot_id=self._bot_id)
                await self._client.delete(path)
            except Exception as exc:
                logger.warning("[agentcompute][avernet] bot delete failed: %s", exc)
            finally:
                self._bot_id = None
        await self._client.aclose()

    async def halt(self) -> None:
        await self.teardown()

    async def _poll_ready(self, bot_id: str, timeout: float = 300.0,
                          interval: float = 1.0) -> str:
        import asyncio
        import time as _time
        deadline = _time.monotonic() + timeout
        path = self._config.bot_status_path.format(bot_id=bot_id)
        while _time.monotonic() < deadline:
            resp = await self._client.get(path)
            resp.raise_for_status()
            state = resp.json().get("data", {}).get("state", "UNKNOWN")
            if state == BotState.READY.value:
                return state
            if state in _TERMINAL_FAILURE:
                raise RuntimeError(f"bot {bot_id} creation failed: {state}")
            await asyncio.sleep(interval)
        return "TIMEOUT"