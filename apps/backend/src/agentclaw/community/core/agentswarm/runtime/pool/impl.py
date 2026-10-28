from __future__ import annotations

import asyncio
import json
import time
import uuid
from typing import Any

from agentclaw.community.core.agentswarm.domain.models import (
    AgentId, AgentStatus, BotState, IdempotencyKey,
    NonRetryableProvisioningError, Run,
    SubagentResult, TaskEnvelope, TenantId, AgentSpec, AgentKind,
)
from dataclasses import dataclass

from agentclaw.community.plugin_api.agentswarm.subagent import (
    ParkedAwaitingAuthorization, SubagentFactory,
)
from agentclaw.community.plugin_api.agentswarm.artifact import ArtifactStore
from agentclaw.community.core.agentswarm.runtime.breaker import (
    UpstreamBreaker,
)
from agentclaw.community.core.agentswarm.runtime.slice_lost import SliceLost
from agentclaw.community.core.agentswarm.runtime.store import SliceStore
from agentclaw.community.log import get_logger

logger = get_logger("agentswarm")


class EnvelopeSchemaInvalid(Exception):
    """Raised when a spawn envelope's output_schema is itself malformed (D42)."""


# Hard cap on a result the runtime keeps inline in a completion (and so in
# the persisted result ``payload`` column). Since round-4 capped artifacts at
# 8 MiB, an oversize output can no longer spill; the inline fallback that
# catches the spill failure used to write the raw output — of unbounded
# size — into the DB, converting the bounded-disk vector into an
# unbounded-DB-write vector. 1,000,000 chars ≈ 1 MB: far above any sane
# routed result, far below the artifact cap, bounded in serialized form.
_MAX_INLINE_RESULT_CHARS = 1_000_000

# Parked-gate recheck cadence: while an attempt is parked awaiting human authorization, each
# recheck is the subagent's own cheap status continuation, and this
# interval only paces those rechecks. The park's bound is the job's
# authorization deadline — never a new timeout knob — and parked time
# never counts against ``setup_timeout``.
_PARK_RECHECK_INTERVAL_SECONDS = 2.0

# Ledger spelling for the parked wait-state transition: one
# diagnostic write on entry to the gate (the same attempt row a later
# COMPLETED/FAILED result overwrites via journal_attempt_result) — never
# a FAILED/TIMEOUT entry, never per-recheck spam.
_PARK_LEDGER_STATUS = "PARKED_AWAITING_AUTHORIZATION"


@dataclass
class _WorkItem:
    run: Run
    envelope: TaskEnvelope
    agent_id: AgentId
    idempotency_key: IdempotencyKey
    tenant: TenantId
    claim_epoch: int = 0
    max_attempts: int = 3


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
        - ``park_recheck_interval`` paces the rechecks of an attempt
          parked at a human authorization gate: the
          default matches the module constant, it is not a settings knob,
          and parked time is never counted against ``setup_timeout``.
        """

    def __init__(
        self,
        factory: SubagentFactory,
        artifact_store: ArtifactStore | None = None,
        max_width: int = 8,
        max_attempts: int = 3,
        per_task_timeout: float = 120.0,
        setup_timeout: float = 240.0,
        setup_ceiling: float = 600.0,
        artifact_inline_threshold: int = 16384,
        upstream_breaker: UpstreamBreaker | None = None,
        binding_store: "SliceStore | None" = None,
        park_recheck_interval: float = _PARK_RECHECK_INTERVAL_SECONDS,
    ) -> None:
        self._factory = factory
        self._artifact_store = artifact_store
        self._max_width = max_width
        self._max_attempts = max_attempts
        self._per_task_timeout = per_task_timeout
        self._setup_timeout = setup_timeout
        self._setup_ceiling = setup_ceiling
        self._artifact_inline_threshold = artifact_inline_threshold
        self._upstream_breaker = upstream_breaker
        self._binding_store = binding_store
        self._park_recheck_interval = park_recheck_interval
        self._claim_epoch = 0
        self._work: asyncio.Queue = asyncio.Queue(maxsize=max_width)
        self._completions: asyncio.Queue = asyncio.Queue()
        self._workers: list[asyncio.Task] = []
        self._running: set[asyncio.Task] = set()
        self._slice_lost = asyncio.Event()
        self._result_handler: Any = None
        self._validator_cache: dict[str, Any] = {}

    def set_result_handler(self, handler: Any) -> None:
        self._result_handler = handler

    def set_slice_lost(self, lost: bool) -> None:
        if lost:
            self._slice_lost.set()
        else:
            self._slice_lost.clear()

    def set_claim_epoch(self, claim_epoch: int) -> None:
        """Current slice's fencing token (D28) for spawn-time journaling.

        One pod executes at most one slice at a time; the handler sets
        this at slice start and every WorkItem enqueued during the slice
        snapshots it, so a terminal cleanup journaling decision and the
        fenced binding write both see the epoch the slice owns.
        """
        self._claim_epoch = claim_epoch

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
            claim_epoch=self._claim_epoch,
            max_attempts=self._resolve_max_attempts(run),
        )
        await self._work.put(item)
        return minted

    def _resolve_max_attempts(self, run: Run) -> int:
        # Per-run override wins when present; absent falls to this pod's
        # configured default. Snapshotted at spawn so every attempt of the
        # item sees one value even though options are re-read per slice.
        opts = getattr(run, "run_options", None)
        override = getattr(opts, "max_attempts", None) if opts is not None else None
        if override is None:
            return self._max_attempts
        return max(1, min(int(override), self._max_attempts))

    def _resolve_setup_timeout(self, run: Run) -> float:
        # Same snapshot rule as _resolve_max_attempts: the deployment's
        # setup budget is the fallback, a requested value wins up to the
        # slice-room ceiling (see RunOptions.resolved_setup_timeout).
        opts = getattr(run, "run_options", None)
        if opts is None:
            return self._setup_timeout
        return opts.resolved_setup_timeout(self._setup_timeout,
                                           self._setup_ceiling)

    def _resolve_per_task_timeout(self, run: Run) -> float:
        opts = getattr(run, "run_options", None)
        if opts is None:
            return self._per_task_timeout
        return opts.resolved_per_attempt_timeout(self._per_task_timeout)

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

    def dispose_run_agents_bots(self, run: Run, agents) -> list[str]:
        # Run-terminal bot reconciliation via the factory (no-op for
        # factories without the method, e.g. test doubles).
        disposer = getattr(self._factory, "destroy_run_agents_bots", None)
        if disposer is None:
            return []
        return disposer(run, agents)

    async def _worker_loop(self) -> None:
        while not self._slice_lost.is_set():
            try:
                item = await asyncio.wait_for(self._work.get(), timeout=0.5)
            except asyncio.TimeoutError:
                continue
            try:
                result = await self._execute_with_retry(item)
                await self._completions.put(result)
                if self._result_handler is not None:
                    try:
                        self._result_handler(result)
                    except Exception as exc:
                        logger.warning(
                            "[agentswarm][runtime] result handler failed for agent=%s: %s",
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
                await self._journal_result(
                    item, 1, "FAILED", "envelope_schema_invalid",
                )
                return self._mk_result(
                    item, AgentStatus.FAILED, None, "envelope_schema_invalid", 1,
                )

        last_error: str | None = None
        attempts_used = item.max_attempts
        for attempt in range(1, item.max_attempts + 1):
            attempts_used = attempt
            if self._slice_lost.is_set():
                await self._journal_result(item, attempt, "SLICE_LOST", "slice_lost")
                return self._mk_result(item, AgentStatus.FAILED, None, "slice_lost", attempt)

            if (self._upstream_breaker is not None
                    and not self._upstream_breaker.allow(time.monotonic())):
                # 12.5: fail fast without calling the subagent — a run degrades
                # through its normal failure path instead of burning the
                # attempt budget waiting out timeouts against a dead upstream.
                await self._journal_result(
                    item, attempt, "FAILED", "upstream_breaker_open")
                return self._mk_result(
                    item, AgentStatus.FAILED, None, "upstream_breaker_open", attempt,
                )

            subagent = None
            # Park state of this attempt: True only while the
            # attempt sits at the human authorization gate, so the finally
            # below can distinguish "cancelled while parked" — where tearing
            # the persona down would discard the pending authorization and
            # force the respawn to open a fresh one — from every ordinary
            # attempt outcome, which keeps the pre-change teardown contract.
            parked = False
            try:
                # D23 fresh context per attempt; factory construction stays
                # inside the attempt's guard so a malformed spec degrades to
                # a FAILED result instead of killing the worker task
                # (its exception would never surface in a fire-and-forget loop).
                # ``attempt`` rides in so the spawn's persona identity is
                # per-attempt (D-J) — a retry mints a fresh executor and
                # can never collide with the torn-down attempt's row.
                subagent = self._factory.create(
                    spec, item.run, item.agent_id, attempt,
                )
                # Write-ahead: the binding INTENT lands before setup()
                # so a setup failure leaves a reconcilable ledger row.
                await self._journal_intent(subagent, spec, item, attempt)
                # Park-aware setup wait: a ParkedAwaitingAuthorization
                # outcome is the human gate's wait state, not an attempt
                # failure — no failed-attempt increment, no new attempt key,
                # no replacement persona, and no setup clock against the
                # wait. Each recheck re-invokes the SAME subagent's setup()
                # (its park continuation: one cheap status poll); the
                # ``wait_for`` re-arms per recheck so only genuine
                # provisioning time inside one continuation is ever bounded
                # by setup_timeout. Slice-end cancellation unwinds through
                # the CancelledError clause mid-park; the persona survives
                # for the next slice's D16 respawn.
                while True:
                    try:
                        await asyncio.wait_for(
                            subagent.setup(),
                            timeout=self._resolve_setup_timeout(item.run),
                        )
                        break
                    except ParkedAwaitingAuthorization as exc:
                        if not parked:
                            parked = True
                            logger.info(
                                "[agentswarm][runtime] agent=%s attempt=%d "
                                "parked at authorization gate: %s",
                                item.agent_id, attempt, exc,
                            )
                            await self._journal_result(
                                item, attempt, _PARK_LEDGER_STATUS, str(exc),
                            )
                        # Write bot_id + AWAITING on EVERY recheck cycle:
                        # concurrent workers hit the DB simultaneously and
                        # a first _record_gate_state can be silently lost
                        # (fenced, thread pool race) — the detail can only
                        # show the gate when bot_id is on the row, so
                        # idempotent repeat writes are the recovery path.
                        await self._record_gate_state(
                            item, getattr(exc, "bot_id", ""),
                            BotState.AWAITING_AUTHORIZATION.value,
                        )
                        await asyncio.sleep(self._park_recheck_interval)
                parked = False
                await self._journal_binding(subagent, spec, item, attempt)
                raw = await asyncio.wait_for(
                    subagent.run_task(item.envelope),
                    timeout=self._resolve_per_task_timeout(item.run),
                )
                if validator is not None:
                    schema_error = self._first_schema_error(validator, raw.output)
                    if schema_error is not None:
                        # D42: schema-invalid results are retried, never routed.
                        last_error = f"schema_validation: {schema_error}"
                        await self._journal_result(
                            item, attempt, "FAILED", last_error)
                        logger.warning(
                            "[agentswarm][runtime] agent=%s attempt=%d schema_validation=: %s",
                            item.agent_id, attempt, schema_error,
                        )
                        continue
                result = self._mk_result(item, AgentStatus.COMPLETED, raw, None, attempt, raw)
                if self._upstream_breaker is not None:
                    # 12.5: success only counts once the completed result is
                    # materialized — a spill failure must not pair a recorded
                    # success with the attempt's recorded failure.
                    self._upstream_breaker.record_success()
                await self._journal_result(item, attempt, "COMPLETED", None)
                return result
            except asyncio.TimeoutError:
                parked = False
                last_error = "timeout"
                await self._journal_result(item, attempt, "TIMEOUT", "timeout")
                self._record_upstream_failure()
                logger.warning("[agentswarm][runtime] agent=%s attempt=%d timeout", item.agent_id, attempt)
            except asyncio.CancelledError:
                # Mid-park cancellation is the ONLY path that leaves ``parked``
                # set: a parked persona awaiting its human must survive
                # the slice boundary so the next slice's D16 respawn adopts
                # the same authorization window instead of opening a new
                # one. Every other outcome below clears the flag and keeps
                # the pre-change teardown contract.
                raise
            except SliceLost:
                parked = False
                # The fenced binding journal rejected this slice's claim —
                # that is authoritative evidence the claim is gone, so set
                # the cooperative-unwind event: never feed the breaker
                # (lost claim ≠ upstream failure), never mint a fresh
                # persona for a slice whose completions can never persist.
                # The persona's own teardown (finally below) still runs
                # and the next attempt's top-of-loop check ends the item.
                self.set_slice_lost(True)
                last_error = "slice_lost"
                logger.info(
                    "[agentswarm][runtime] agent=%s attempt=%d binding journal fenced out",
                    item.agent_id, attempt,
                )
                # Terminal cooperative unwind: the item ends at the next
                # top-of-loop check, so journal this attempt's outcome now.
                await self._journal_result(item, attempt, "SLICE_LOST", "slice_lost")
            except Exception as exc:
                parked = False
                if self._is_non_retryable(exc):
                    # Deterministic provisioning failures (a space quota
                    # ceiling) will fail every retry the same way — fail
                    # now without burning the attempt budget, and never
                    # feed the LLM upstream breaker.
                    last_error = f"{type(exc).__name__}: {exc}"
                    logger.warning(
                        "[agentswarm][runtime] agent=%s attempt=%d "
                        "non_retryable=%s error=%s",
                        item.agent_id, attempt, type(exc).__name__, exc,
                    )
                    if getattr(exc, "bot_state", ""):
                        # The authorization's terminal verdict overrides the
                        # parked state on the agent row so the run surface
                        # stops serving the consent URL for a bot that can
                        # no longer be confirmed.
                        await self._record_gate_state(
                            item, getattr(exc, "bot_id", ""),
                            str(getattr(exc, "bot_state", "")),
                        )
                    await self._journal_result(item, attempt, "FAILED", last_error)
                    break
                last_error = str(exc)
                await self._journal_result(item, attempt, "FAILED", last_error)
                self._record_upstream_failure()
                logger.warning("[agentswarm][runtime] agent=%s attempt=%d error=%s", item.agent_id, attempt, exc)
            finally:
                if subagent is not None and not parked:
                    # Parked (mid-park cancellation, D3): the persona's
                    # authorization window is the wait itself — teardown
                    # would discard the pending application and force the
                    # respawn to open a fresh gate, so the awaiting persona
                    # survives for the next slice's adopt-on-respawn.
                    try:
                        await subagent.teardown()
                    except Exception:
                        # Best-effort stays best-effort, but never silent: a
                        # leaked host-platform bot must be diagnosable.
                        logger.exception(
                            "[agentswarm][runtime] agent=%s subagent teardown failed",
                            item.agent_id,
                        )

        await self._journal_result(
            item, attempts_used, "FAILED", last_error,
        )
        return self._mk_result(
            item, AgentStatus.FAILED, None, last_error, attempts_used,
        )

    @staticmethod
    def _is_non_retryable(exc: BaseException) -> bool:
        # Agentswarm-owned marker only — the pool never imports the host
        # platform that raised the original error (D17).
        return isinstance(exc, NonRetryableProvisioningError)

    async def _journal_intent(self, subagent: Any, spec: AgentSpec,
                               item: _WorkItem, attempt: int) -> None:
        """Write-ahead binding INTENT journal, before ``setup()``.

        The row lands keyed by the attempt UK under the same D28 claim
        fence as every other slice write, with whatever executor
        surface the subagent exposes at spawn time (a bot-kind persona
        is still blank — the terminal destroy sweep reconciles blank
        rows through the attempt's derived identity). A pre-D-J double
        without the executor attribute journals nothing, exactly like
        the post-setup settle. A fenced-out insert unwinds as
        ``SliceLost`` so a dead claim never provisions a persona.
        """
        if self._binding_store is None or self._slice_lost.is_set():
            return
        if getattr(subagent, "executor_id", None) is None:
            return
        executor_id = str(getattr(subagent, "executor_id", "") or "")
        executor_name = str(getattr(subagent, "executor_name", "") or spec.name)
        await self._binding_store.journal_attempt_intent(
            item.run, item.agent_id, item.claim_epoch, attempt,
            self._executor_kind(spec), executor_id, executor_name,
        )

    async def _journal_binding(self, subagent: Any, spec: AgentSpec,
                                item: _WorkItem, attempt: int) -> None:
        """Settle the attempt's executor identity after setup (D-L).

        The store call is insert-or-settle by the attempt UK: with the
        write-ahead intent row already landed it becomes the fenced
        UPDATE that fills in the provisioned persona's executor id and
        flips ``binding_state`` to BOUND; every attempt still converges
        on exactly one row. The read stays additive SPI getattr: a blank
        executor_id (a pre-D-J double, or a setup that surfaced no
        identity) skips the settle — the intent row the destroy sweep
        reconciles stays.
        """
        if self._binding_store is None or self._slice_lost.is_set():
            return
        executor_id = str(getattr(subagent, "executor_id", "") or "")
        executor_name = str(getattr(subagent, "executor_name", "") or spec.name)
        if not executor_id.strip():
            return
        await self._binding_store.record_agent_binding(
            item.run, item.agent_id, item.claim_epoch, attempt,
            self._executor_kind(spec), executor_id, executor_name,
        )

    async def _journal_result(self, item: _WorkItem, attempt: int,
                              result_status: str,
                              result_message: str | None) -> None:
        """Best-effort per-attempt outcome write on the ledger.

        Every terminal outcome of an attempt (COMPLETED/FAILED/SLICE_LOST/
        TIMEOUT) is recorded so the ledger's ``binding_state`` — which is
        provisioning-only — no longer hides a failed attempt behind a
        SPAWNED row. The write is diagnostic: a journal failure must never
        raise or change the attempt's control flow, and the store call is
        itself best-effort (SliceStore.journal_attempt_result).
        """
        if self._binding_store is None:
            return
        await self._binding_store.journal_attempt_result(
            item.run, item.agent_id, item.claim_epoch, attempt,
            result_status, result_message,
        )

    async def _record_gate_state(self, item: _WorkItem, bot_id: str,
                                 bot_state: str) -> None:
        """Mirror the human gate onto the agent row.

        Additive SPI reads only: a marker double carrying no identity
        skips the write. The store call is fenced and best-effort, so a
        park or terminal verdict never unwinds its own attempt because its
        durable-column twin could not land.
        """
        if self._binding_store is None or self._slice_lost.is_set():
            return
        bot_id = str(bot_id or "").strip()
        bot_state = str(bot_state or "").strip()
        if not bot_id or not bot_state:
            return
        await self._binding_store.record_agent_bot_state(
            item.run, item.agent_id, item.claim_epoch, bot_id, bot_state,
        )

    @staticmethod
    def _executor_kind(spec: AgentSpec) -> str:
        kind = spec.kind if isinstance(spec.kind, AgentKind) else AgentKind.LLM
        return kind.value

    def _record_upstream_failure(self) -> None:
        if self._upstream_breaker is not None:
            self._upstream_breaker.record_failure(time.monotonic())

    @staticmethod
    def _find_spec(run: Run, envelope: TaskEnvelope) -> AgentSpec | None:
        for spec in run.agent_catalog:
            if spec.name == envelope.name:
                return spec
        return None

    def _inline_fallback(self, item: _WorkItem, output: Any,
                         output_str: str) -> Any:
        """Carrier for a completed result that could not spill (round-5).

        Under ``_MAX_INLINE_RESULT_CHARS`` the raw output is kept inline
        exactly as before. Above it, the raw output would be written
        verbatim into the persisted result ``payload`` column — an
        unbounded-DB-write vector once the round-4 8 MiB artifact cap made
        oversize results unspillable. The alternative — hard-fail the agent
        so the orchestrator can react — was rejected: it discards a
        completed, schema-valid result and re-runs the task, doubling its
        side effects, against the campaign's stated principle. Instead a
        bounded placeholder keeps the D31 XOR invariant (payload set,
        artifact_key None), records the true serialized size, and carries
        a short preview so the run converges on a present-but-truncated
        result rather than persisting arbitrary bytes.
        """
        if len(output_str) <= _MAX_INLINE_RESULT_CHARS:
            return output
        logger.warning(
            "[agentswarm][runtime] agent=%s result of %d chars exceeds inline cap %d and could not be spilled; "
            "persisting bounded truncated result placeholder",
            item.agent_id, len(output_str), _MAX_INLINE_RESULT_CHARS,
        )
        return {
            "truncated": True,
            "size": len(output_str),
            "preview": output_str[:_MAX_INLINE_RESULT_CHARS // 10],
        }

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
                try:
                    self._artifact_store.put(key, output_str)
                    artifact_key = key
                except Exception:
                    # Spilling is best-effort: never discard a completed
                    # (schema-valid) result — that would re-run the task and
                    # double its side effects. Keep the result inline; the
                    # result XOR contract (D31) still has exactly one carrier.
                    logger.exception(
                        "[agentswarm][runtime] agent=%s artifact put failed for key=%s; keeping result inline",
                        item.agent_id, key,
                    )
                    result_payload = self._inline_fallback(item, _raw.output, output_str)
            else:
                # No artifact store configured: keep the result inline so the
                # result XOR contract (D31) still has exactly one carrier.
                result_payload = self._inline_fallback(item, _raw.output, output_str)

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