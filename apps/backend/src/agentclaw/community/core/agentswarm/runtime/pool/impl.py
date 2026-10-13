from __future__ import annotations

import asyncio
import json
import time
import uuid
from typing import Any

from agentclaw.community.core.agentswarm.domain.models import (
    AgentId, AgentStatus, IdempotencyKey, Run,
    SubagentResult, TaskEnvelope, TenantId, AgentSpec,
)
from dataclasses import dataclass

from agentclaw.community.plugin_api.agentswarm.subagent import SubagentFactory
from agentclaw.community.plugin_api.agentswarm.artifact import ArtifactStore
from agentclaw.community.core.agentswarm.runtime.breaker import (
    UpstreamBreaker,
)
from agentclaw.community.log import get_logger

logger = get_logger(__name__)


class EnvelopeSchemaInvalid(Exception):
    """Raised when a spawn envelope's output_schema is itself malformed (D42)."""


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
    """Bounded subagent worker pool (D23) with optional upstream guard.

        Constructor contract for the optional knobs:

        - ``upstream_breaker=None`` means the breaker is disabled (every
          attempt allowed) — a valid domain state for profiles that run
          without a real LLM upstream; the DI composition root wires a
          breaker built from ``config.upstream_breaker`` when one exists.
        - ``metrics=None`` means no metrics backend is configured
          (local/test profiles); a private no-op fills the seam so call
          sites never branch on instrumentation.
        """

    def __init__(
        self,
        factory: SubagentFactory,
        artifact_store: ArtifactStore | None = None,
        max_width: int = 8,
        max_attempts: int = 3,
        per_task_timeout: float = 120.0,
        artifact_inline_threshold: int = 16384,
        upstream_breaker: UpstreamBreaker | None = None,
    ) -> None:
        self._factory = factory
        self._artifact_store = artifact_store
        self._max_width = max_width
        self._max_attempts = max_attempts
        self._per_task_timeout = per_task_timeout
        self._artifact_inline_threshold = artifact_inline_threshold
        self._upstream_breaker = upstream_breaker
        self._work: asyncio.Queue = asyncio.Queue(maxsize=max_width)
        self._completions: asyncio.Queue = asyncio.Queue()
        self._workers: list[asyncio.Task] = []
        self._running: set[asyncio.Task] = set()
        self._slice_lost = asyncio.Event()
        self._shard_handler: Any = None
        self._validator_cache: dict[str, Any] = {}

    def set_shard_handler(self, handler: Any) -> None:
        self._shard_handler = handler

    def set_slice_lost(self, lost: bool) -> None:
        if lost:
            self._slice_lost.set()
        else:
            self._slice_lost.clear()

    def _validator_for(self, schema: dict[str, Any]) -> Any:
        from jsonschema import Draft202012Validator
        from jsonschema.exceptions import SchemaError

        cache_key = json.dumps(schema, sort_keys=True, default=str)
        cached = self._validator_cache.get(cache_key)
        if cached is None:
            try:
                Draft202012Validator.check_schema(schema)
            except SchemaError as exc:
                raise EnvelopeSchemaInvalid(str(exc)) from exc
            cached = Draft202012Validator(schema)
            self._validator_cache[cache_key] = cached
        return cached

    @staticmethod
    def _first_schema_error(validator: Any, output: Any) -> str | None:
        for error in validator.iter_errors(output):
            return error.message
        return None

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
        # A cancelled slice can leave queued work whose join() would otherwise
        # block the next slice forever; drop it so a fresh slice starts clean.
        while not self._work.empty():
            try:
                self._work.get_nowait()
                self._work.task_done()
            except asyncio.QueueEmpty:
                break
        while not self._completions.empty():
            try:
                self._completions.get_nowait()
            except asyncio.QueueEmpty:
                break

    async def spawn(self, run: Run, envelope: TaskEnvelope,
                    agent_id: AgentId | None = None,
                    idempotency_key: IdempotencyKey | None = None) -> AgentId:
        minted = agent_id or AgentId(uuid.uuid4().hex)
        item = _WorkItem(
            run=run,
            envelope=envelope,
            agent_id=minted,
            idempotency_key=idempotency_key or IdempotencyKey(f"{run.run_id}:{minted}"),
            tenant=run.tenant,
        )
        await self._work.put(item)
        return minted

    async def wait_for_completions(self, timeout: float | None = None) -> list[dict[str, Any]]:
        if timeout is None or timeout < 0:
            await self._work.join()
        else:
            started = time.monotonic()
            try:
                await asyncio.wait_for(self._work.join(), timeout)
            except asyncio.TimeoutError:
                pass
            # An idle work queue settles join() instantly; a caller asked to
            # wait `timeout` for completions would hot-spin on that instant
            # return (starving the event loop and every timer on it, e.g.
            # the drain grace window). Sleep out the remainder — but never
            # delay draining when this call was woken by a completion.
            remaining = timeout - (time.monotonic() - started)
            if remaining > 0 and self._work.empty() and self._completions.empty():
                await asyncio.sleep(remaining)
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
                if self._shard_handler is not None:
                    try:
                        self._shard_handler(result)
                    except Exception as exc:
                        logger.warning(
                            "[agentswarm][runtime] shard handler failed for agent=%s: %s",
                            item.agent_id, exc,
                        )
            except asyncio.CancelledError:
                raise
            finally:
                self._work.task_done()

    async def _execute_with_retry(self, item: _WorkItem) -> dict[str, Any]:
        spec = self._find_spec(item.run, item.envelope)
        if spec is None:
            spec = AgentSpec(name=item.envelope.name, role=item.envelope.objective)

        schema = item.envelope.output_schema
        validator = None
        if schema:
            try:
                validator = self._validator_for(schema)
            except EnvelopeSchemaInvalid as exc:
                logger.error(
                    "[agentswarm][runtime] agent=%s envelope_schema_invalid: %s",
                    item.agent_id, exc,
                )
                return self._mk_result(
                    item, AgentStatus.FAILED, None, "envelope_schema_invalid", 1,
                )

        last_error: str | None = None
        for attempt in range(1, self._max_attempts + 1):
            if self._slice_lost.is_set():
                return self._mk_result(item, AgentStatus.FAILED, None, "slice_lost", attempt)

            if (self._upstream_breaker is not None
                    and not self._upstream_breaker.allow(time.monotonic())):
                # 12.5: fail fast without calling the subagent — a run degrades
                # through its normal failure path instead of burning the
                # attempt budget waiting out timeouts against a dead upstream.
                return self._mk_result(
                    item, AgentStatus.FAILED, None, "upstream_breaker_open", attempt,
                )

            subagent = None
            try:
                # D23 fresh context per attempt; factory construction stays
                # inside the attempt's guard so a malformed spec degrades to
                # a FAILED result instead of killing the worker task
                # (its exception would never surface in a fire-and-forget loop).
                subagent = self._factory.create(spec, item.run, item.agent_id)
                await subagent.setup()
                raw = await asyncio.wait_for(
                    subagent.run_task(item.envelope),
                    timeout=self._per_task_timeout,
                )
                if validator is not None:
                    schema_error = self._first_schema_error(validator, raw.output)
                    if schema_error is not None:
                        # D42: schema-invalid results are retried, never routed.
                        last_error = f"schema_validation: {schema_error}"
                        logger.warning(
                            "[agentswarm][runtime] agent=%s attempt=%d schema_validation=: %s",
                            item.agent_id, attempt, schema_error,
                        )
                        continue
                if self._upstream_breaker is not None:
                    self._upstream_breaker.record_success()
                return self._mk_result(item, AgentStatus.COMPLETED, raw, None, attempt, raw)
            except asyncio.TimeoutError:
                last_error = "timeout"
                self._record_upstream_failure()
                logger.warning("[agentswarm][runtime] agent=%s attempt=%d timeout", item.agent_id, attempt)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                last_error = str(exc)
                self._record_upstream_failure()
                logger.warning("[agentswarm][runtime] agent=%s attempt=%d error=%s", item.agent_id, attempt, exc)
            finally:
                if subagent is not None:
                    try:
                        await subagent.teardown()
                    except Exception:
                        pass

        return self._mk_result(item, AgentStatus.FAILED, None, last_error, self._max_attempts)

    def _record_upstream_failure(self) -> None:
        if self._upstream_breaker is not None:
            self._upstream_breaker.record_failure(time.monotonic())

    @staticmethod
    def _find_spec(run: Run, envelope: TaskEnvelope) -> AgentSpec | None:
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
            else:
                # No artifact store configured: keep the result inline so the
                # shard XOR contract (D31) still has exactly one carrier.
                result_payload = _raw.output

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