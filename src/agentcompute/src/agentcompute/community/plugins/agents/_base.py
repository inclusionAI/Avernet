"""Generic LLM-backed agent driven by a caller-supplied specification."""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable, Iterator

from ..._logger import get_logger
from ...spi._agent import Agent, AgentContext, AgentSpec, NodeResult
from ...spi._llm import LLMProvider

__all__ = ["LLMBackedAgent"]

logger = get_logger("executor")


class LLMBackedAgent(Agent):
    """Agent that discharges an ``AgentSpec`` via a single LLM completion."""

    def __init__(self, spec: AgentSpec, provider: LLMProvider) -> None:
        self._spec = spec
        self._provider = provider
        self.name = spec.name
        self.instance_id = f"{spec.name}-{uuid.uuid4().hex[:8]}"

    def setup(self) -> None:
        logger.info("agent %s setup", self.instance_id)

    def execute(self, ctx: AgentContext) -> NodeResult:
        logger.info(
            "agent %s executing node=%s goal=%r",
            self.instance_id,
            ctx.node_id,
            ctx.goal,
        )
        ctx.report_progress(0.5)
        prompt = self._prompt(ctx)
        logger.debug("agent %s prompt=%s", self.instance_id, prompt)
        parts: list[str] = []
        stream: Callable[..., Iterator[str]] | None = getattr(self._provider, "stream", None)
        if stream is not None:
            for chunk in stream(prompt, kind=self.name):
                parts.append(chunk)
                logger.debug("agent %s node=%s chunk=%r", self.instance_id, ctx.node_id, chunk)
        else:
            parts.append(self._provider.complete(prompt, kind=self.name))
        raw = "".join(parts)
        logger.info(
            "agent %s node=%s done chunks=%d bytes=%d",
            self.instance_id, ctx.node_id, len(parts), len(raw),
        )
        ctx.report_progress(1.0)
        output = self._parse(raw)
        return NodeResult(node_id=ctx.node_id, output=output)

    def teardown(self) -> None:
        logger.info("agent %s teardown", self.instance_id)

    def _prompt(self, ctx: AgentContext) -> str:
        upstream = json.dumps(ctx.upstream, ensure_ascii=False)
        return (
            f"role={self._spec.role!r} "
            f"instructions={self._spec.instructions!r} "
            f"goal={ctx.goal!r} upstream={upstream}"
        )

    def _parse(self, raw: str) -> object:
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return raw
