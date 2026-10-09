from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import Any

from agentclaw.community.core.agentcompute.domain.models import (
    AgentId, AgentStatus, EpochMillis, IdempotencyKey, RunId, Run,
    SubagentResult, TaskEnvelope, TenantId, AgentSpecRaw, GuardKind,
    EventId, GuardEvent, GuardDetails,
)
from dataclasses import dataclass

from agentclaw.community.core.agentcompute.spi.subagent_factory import SubagentFactory
from agentclaw.community.core.agentcompute.spi.artifact_store import ArtifactStore

logger = logging.getLogger(__name__)


@dataclass
class _WorkItem:
    run: Run
    envelope: TaskEnvelope
    agent_id: AgentId
    idempotency_key: IdempotencyKey
    tenant: TenantId


def _now_ms() -> int:
    return int(time.time() * 1000)



class SubagentRuntime:

    def __init__(
        self,
        factory: SubagentFactory,
        artifact_store: ArtifactStore | None = None,
        max_width: int = 8,
        max_attempts: int = 3,
        per_task_timeout: float = 120.0,
        artifact_inline_threshold: int = 16384,
    ) -> None:
        from dataclasses import dataclass

        self._factory = factory
        self._artifact_store = artifact_store
        self._max_width = max_width
        self._max_attempts = max_attempts
        self._per_task_timeout = per_task_timeout
        self._artifact_inline_threshold = artifact_inline_threshold
        self._work: asyncio.Queue = asyncio.Queue(maxsize=max_width)
        self._completions: asyncio.Queue = asyncio.Queue()
        self._workers: list[asyncio.Task] = []
        self._running: set[asyncio.Task] = set()
        self._slice_lost = asyncio.Event()
        self._shard_handler: Any = None

    def set_shard_handler(self, handler: Any) -> None:
        self._shard_handler = handler

    def set_slice_lost(self, lost: bool) -> None:
        if lost:
            self._slice_lost.set()
        else:
            self._slice_lost.clear()

    async def start(self) -> None:
        for _ in range(self._max_width):
            worker = asyncio.create_task(self._worker_loop())
            self._workers.append(worker)
            self._running.add(worker)
            worker.add_done_callback(self._workers.remove)

    async def stop(self) -> None:
        for w in self._workers:
            w.cancel()
        if self._workers:
            await asyncio.gather(*self._workers, return_exceptions=True)
        self._workers.clear()
        self._running.clear()

    async def spawn(self, run: Run, envelope: TaskEnvelope) -> AgentId:
        agent_id = AgentId(uuid.uuid4().hex)
        idem_key = IdempotencyKey(f"{run.run_id}:{agent_id}")
        item = _WorkItem(
            run=run,
            envelope=envelope,
            agent_id=agent_id,
            idempotency_key=idem_key,
            tenant=run.tenant,
        )
        await self._work.put(item)
        return agent_id

    async def wait_for_completions(self) -> list[dict[str, Any]]:
        await self._work.join()
        results = []
        while not self._completions.empty():
            results.append(await self._completions.get())
        return results

    async def get_pending_count(self) -> int:
        return self._work.qsize()

    async def _worker_loop(self) -> None:
        while not self._slice_lost.is_set():
            try:
                item = await asyncio.wait_for(self._work.get(), timeout=0.5)
            except asyncio.TimeoutError:
                continue
            try:
                result = await self._execute_with_retry(item)
                await self._completions.put(result)
            except asyncio.CancelledError:
                raise
            finally:
                self._work.task_done()

    async def _execute_with_retry(self, item: _WorkItem) -> dict[str, Any]:
        spec = self._find_spec(item.run, item.envelope)
        if spec is None:
            spec = AgentSpecRaw(name=item.envelope.name, role=item.envelope.objective)

        last_error: str | None = None
        for attempt in range(1, self._max_attempts + 1):
            if self._slice_lost.is_set():
                return self._mk_result(item, AgentStatus.FAILED, None, "slice_lost", attempt)

            subagent = self._factory.create(spec, item.run)
            try:
                await subagent.setup()
                raw = await asyncio.wait_for(
                    subagent.run_task(item.envelope),
                    timeout=self._per_task_timeout,
                )
                return self._mk_result(item, AgentStatus.COMPLETED, raw, None, attempt, raw)
            except asyncio.TimeoutError:
                last_error = "timeout"
                logger.warning("[agentcompute][runtime] agent=%s attempt=%d timeout", item.agent_id, attempt)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                last_error = str(exc)
                logger.warning("[agentcompute][runtime] agent=%s attempt=%d error=%s", item.agent_id, attempt, exc)
            finally:
                try:
                    await subagent.teardown()
                except Exception:
                    pass

        return self._mk_result(item, AgentStatus.FAILED, None, last_error, self._max_attempts)

    @staticmethod
    def _find_spec(run: Run, envelope: TaskEnvelope) -> AgentSpecRaw | None:
        for spec in run.agent_catalog:
            if spec.name == envelope.name:
                return spec
        return None

    def _mk_result(self, item: _WorkItem, status: AgentStatus,
                   raw: SubagentResult | None, error: str | None,
                   attempts: int, _raw: SubagentResult | None = None) -> dict[str, Any]:
        result_payload = None
        artifact_key = None
        if _raw is not None and _raw.output is not None:
            output_str = str(_raw.output)
            if len(output_str) <= self._artifact_inline_threshold:
                result_payload = _raw.output
            elif self._artifact_store is not None:
                key = f"{item.run.run_id}/{item.agent_id}/result"
                self._artifact_store.put(key, output_str)
                artifact_key = key

        return {
            "agent_id": item.agent_id,
            "run_id": item.run.run_id,
            "tenant": item.tenant,
            "status": status,
            "attempts": attempts,
            "error": error,
            "idempotency_key": item.idempotency_key,
            "result": result_payload,
            "artifact_key": artifact_key,
            "token_usage": raw.token_usage if raw else None,
        }