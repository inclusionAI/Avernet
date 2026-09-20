"""``TaskTrajectoryService`` — the service facade + ``do_analysis`` orchestration
(REQ-8 trigger, P5b).

The CONSUMPTION layer that wires the read (P4 assembler) + analysis (P5a
analyzer) + persistence (P1b repo) into the single对外入口
``get_trajectory(task_id, *, do_analysis=False) -> TaskTrajectory``. Its internal
contract ``TaskTrajectoryServiceProtocol`` (declared in this module) is inherited
by this class and consumed only by the facade ``TaskContextService``
(``core/task/task_context/task_context_service.py``); external callers reach it via
``api/task/task_context_service.py``'s ``TaskContextServiceProtocol``.

Two modes (spec REQ-8; decision #10 — the separate ``/analysis`` endpoint is
NOT built; ``do_analysis`` on ``/trajectory`` is the single trigger):

* ``do_analysis=False`` (default, PURE READ) — ``assembler.assemble(task_id)``
  → ``TaskTrajectory``; return it (``analysis`` = persisted or ``None``, no DB
  write, no bot). No ``task_action_log`` touch (trajectory is an 独立旁路).

* ``do_analysis=True``:
  1. ``assemble(task_id)`` → ``TaskTrajectory`` (timeline).
  2. Build ``ext_info_lookup: Callable[[TrajectoryEvent], dict | None]`` from
     the repo's ``list_events_by_task`` records. The assembler DROPS ``ext_info``
     from the domain ``TrajectoryEvent`` (REQ-1) — the analyzer re-queries via
     this closure. Keyed by a STABLE 4-tuple signature derivable from
     ``TrajectoryEvent`` (see ``_EVENT_SIGNATURE`` below).
  3. ``analysis_bot_id = config.analysis_bot_id`` (deployment config; decision
     #10 — NOT per-request; ``None`` → raise
     ``TrajectoryAnalysisNotConfiguredError`` → endpoint maps to 503).
  4. ``await analyzer.analyze(trajectory, ext_info_lookup,
     analysis_type=AnalysisType.TC_BOT, analysis_executor=analysis_bot_id)`` →
     ``TrajectoryAnalysis``.
  5. Serialize: ``json.dumps(asdict(analysis), ensure_ascii=False)`` (no event
     list, no recursion — P0 flattening).
  6. ``repo.backfill_analysis(task_id, analysis_json)`` (overwrite both tables'
     ``analysis``+``gmt_modified`` — P1b transactional; decision #13 overwrite).
  7. Return the ``TaskTrajectory`` with the fresh ``analysis`` set (mutate
     ``.analysis`` + ``.gmt_modified`` on the returned object — avoids a second
     DB round-trip; ``.analysis`` == the new JSON).

Bot failure/timeout → analyzer raises ``TrajectoryAnalysisError``; the service
MUST NOT backfill and re-raises (decision #14 waiver is EMISSION-only; the
on-demand analysis-trigger failure MUST be visible — 504 + no backfill). The
router maps via ``@envelope_errors`` / ``ENVELOPE_ERRORS``.

Scope boundary: no FastAPI / transport (the router is P5b's other half); no
``NodeAction`` / ``append_action_event`` / ``task_action_log`` (spec invariant);
the analyzer is the ONLY bot caller. ``TaskService`` is NOT touched — this is a
SEPARATE service (spec invariant).

Authoritative: ``specs/2026-09-16-task-trajectory-collection-and-analysis/spec.md``
REQ-8 (the ``/trajectory`` endpoint + ``do_analysis`` two-mode), REQ-10 (merged
— no separate ``/analysis``), 决策 #10/#11/#13/#14, plan.md Phase 5.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict
from typing import TYPE_CHECKING, Any, Callable, Protocol, runtime_checkable

from injector import inject

from agentclaw.community.core.repository.protocols.task import (
    TaskTrajectoryRepositoryProtocol,
)
from agentclaw.community.core.task.domain.errors import (
    TrajectoryAnalysisNotConfiguredError,
)
from agentclaw.community.core.task.repository.types import TrajectoryEventRecord
from agentclaw.community.core.task.task_context.task_trajectory.analyzer import (
    TaskTrajectoryAnalyzer,
)
from agentclaw.community.core.task.task_context.task_trajectory.assembler import (
    TaskTrajectoryAssembler,
    _datetime_to_int_ms,
)
from agentclaw.community.core.task.task_context.task_trajectory.models import (
    AnalysisType,
    ReasonCatalog,
    TaskTrajectory,
    TrajectoryActionType,
    TrajectoryEvent,
)
from agentclaw.community.core.task.task_context.task_trajectory.payloads import (
    emit_trajectory_event as _emit_trajectory_event,
    emit_submit_trajectory as _emit_submit_trajectory,
)
from agentclaw.community.di.task_trajectory_config import TrajectoryAnalysisConfig

if TYPE_CHECKING:
    # ``Status`` / ``TaskInfo`` are used only in emit_* annotations (the methods
    # pass values through to the payloads emitters which read fields via attribute
    # access at runtime); guarded to keep the runtime import surface minimal.
    from agentclaw.community.core.task.domain.models import Status, TaskInfo

logger = logging.getLogger("task.trajectory")


# ---------------------------------------------------------------------------
# ext_info_lookup — re-query the ``ext_info`` JSON the assembler dropped (REQ-1)
# ---------------------------------------------------------------------------


def _event_signature(rec: TrajectoryEventRecord) -> tuple:
    """The STABLE key tying a ``TrajectoryEventRecord`` (with ``ext_info``) to
    the domain ``TrajectoryEvent`` (which DROPPED ``ext_info`` — REQ-1).

    ``(gmt_create_ms, node_id, action_type, attempt)`` — the 4 fields that
    uniquely identify an event in practice:

    * ``gmt_create_ms`` — int-ms epoch (the record's ``gmt_create`` datetime
      converted via the assembler's Asia/Shanghai storage convention; matches
      the domain event's ``gmt_create`` int by construction). Timestamp-second
      means same-second ties are accepted by spec (the table has no unique
      constraint; ``id`` is the deterministic tiebreaker for ORDER, not here).
    * ``node_id`` — the node the event is about.
    * ``action_type`` — lowercase string (``submit|plan|dispatch|...``).
    * ``attempt`` — harness retry sequence snapshot.

    Collision semantics: two events same-node / same-action / same-attempt /
    same-second are astronomically rare (each gate fires once per node+attempt).
    On collision the lookup keeps the LAST record by ``id`` (``list_events_by_task``
    returns ``ORDER BY gmt_create ASC, id ASC``; later rows overwrite earlier in
    the dict build). The analyzer only reads ``ext_info`` for specific events
    (last DISPATCH → boost_reason; RESET events → elapsed/threshold), so a
    collision on those is both rare and last-write-wins-acceptable (decision #13).
    """
    return (
        _datetime_to_int_ms(rec.gmt_create),
        rec.node_id,
        rec.action_type,
        rec.attempt,
    )


def _event_key(event: TrajectoryEvent) -> tuple:
    """The domain-event side of ``_event_signature`` (mirrors the record key).

    ``event.action_type`` is a ``TrajectoryActionType`` enum whose ``.value`` is
    the lowercase string the record stored; ``event.gmt_create`` is the int-ms
    the assembler produced from the record's ``gmt_create`` via the same
    ``_datetime_to_int_ms``. The two keys therefore match exactly.
    """
    action_type = (
        event.action_type.value
        if hasattr(event.action_type, "value")
        else str(event.action_type)
    )
    return (event.gmt_create, event.node_id, action_type, event.attempt)


def _build_ext_info_lookup(
    repo: TaskTrajectoryRepositoryProtocol,
    task_id: str,
) -> Callable[[TrajectoryEvent], dict | None]:
    """Build the ``ext_info_lookup`` seam the analyzer consumes.

    Re-queries ``list_events_by_task(task_id)`` to get the records WITH ``ext_info``
    JSON (the assembler dropped ``ext_info`` from the domain ``TrajectoryEvent``),
    parses each record's ``ext_info`` defensively (broken JSON → ``None``, never
    raises — 决策 #14 emission-only waiver spirit; a single corrupt ``ext_info``
    must not break the on-demand analysis), and returns a closure
    ``lookup(event) -> dict | None`` keyed by the stable 4-tuple signature.

    The outer ``schema_v`` envelope (``{"schema_v": 1, "_dispatch_rationale": ...,
    "elapsed_ms": ...}``) is preserved as-is — the analyzer reads the specific
    keys it needs (``_dispatch_rationale`` / ``elapsed_ms`` / ``sla_threshold_ms``)
    and ignores ``schema_v`` (P5a carry-note).
    """
    lookup: dict[tuple, dict | None] = {}
    try:
        records = repo.list_events_by_task(task_id)
    except Exception as ex:  # noqa: BLE001  read failure → empty lookup (degrade)
        logger.warning(
            "[task][trajectory] ext_info_lookup 构建失败 task=%s: %s: %s",
            task_id, type(ex).__name__, ex,
        )
        return lambda _event: None

    for rec in records:
        if not rec.ext_info:
            continue
        try:
            parsed = json.loads(rec.ext_info)
        except (json.JSONDecodeError, TypeError):
            # Single corrupt ext_info → skip (None for this key); never raise.
            logger.warning(
                "[task][trajectory] 跳过损坏 ext_info task=%s id=%s node=%s",
                task_id, getattr(rec, "id", None), rec.node_id,
            )
            continue
        if isinstance(parsed, dict):
            lookup[_event_signature(rec)] = parsed

    def ext_info_lookup(event: TrajectoryEvent) -> dict | None:
        return lookup.get(_event_key(event))

    return ext_info_lookup


# ---------------------------------------------------------------------------
# Internal contract — trajectory sub-module's Service API (consumed only by
# core/task/task_context/task_context_service.py; NOT re-exported from api/).
# Repo pattern (api/README "Where a Protocol is defined"): Protocol in its owning
# core module, impl inherits it.
# ---------------------------------------------------------------------------


@runtime_checkable
class TaskTrajectoryServiceProtocol(Protocol):
    """Internal contract for the trajectory sub-module's read + emit operations.

    Declared in this module (the owning core module) and inherited by
    ``TaskTrajectoryService`` so the facade ``TaskContextService`` depends on this
    contract, not the concrete service. NOT re-exported from ``api/`` — external
    callers go through ``TaskContextServiceProtocol`` (api/task/
    task_context_service.py) which relays.
    """

    async def get_trajectory(
        self,
        task_id: str,
        *,
        do_analysis: bool = False,
    ) -> TaskTrajectory:
        """Read the trajectory for ``task_id``; optionally trigger bot analysis."""
        ...

    def emit_trajectory_event(
        self,
        task_id: str,
        node_id: str,
        action_type: "TrajectoryActionType | str",
        *,
        action_result: str,
        action_input: str | None = None,
        error_type: "ReasonCatalog | str | None" = None,
        error_msg: str | None = None,
        ext_info: "dict[str, Any] | None" = None,
        status_from: "Status | str | None" = None,
        status_to: "Status | str | None" = None,
        attempt: int = 0,
        now_ms: int | None = None,
    ) -> None:
        """Fire-and-forget trajectory event write (never raises; decision #14)."""
        ...

    def emit_submit_trajectory(
        self,
        task_id: str,
        task_info: "TaskInfo",
        *,
        submitted_at_ms: int,
        node_id: str | None = None,
    ) -> None:
        """Fire-and-forget SUBMIT trajectory event write (never raises; decision #14)."""
        ...


# ---------------------------------------------------------------------------
# Service — the facade + do_analysis orchestration
# ---------------------------------------------------------------------------


class TaskTrajectoryService(TaskTrajectoryServiceProtocol):
    """Service facade combining the assembler + analyzer + repo + config into
    the ``get_trajectory(task_id, *, do_analysis)`` entrypoint (REQ-8, P5b).

    SEPARATE from ``TaskService`` (spec invariant — the trajectory service is an
    independent旁路 consumer; it does NOT touch ``TaskService`` /
    ``task_action_log`` / ``NodeAction`` / ``append_action_event``).

    Constructor injection (``@inject``) lets unit tests pass fakes directly and
    DI wire the real deps. The config is optional: a ``None`` config (lightweight
    DI injector that did not bind ``TrajectoryAnalysisConfig``) is treated as
    ``analysis_bot_id=None`` → ``do_analysis=true`` raises
    ``TrajectoryAnalysisNotConfiguredError`` (503); ``do_analysis=false`` (the
    default read path) never touches the config.
    """

    @inject
    def __init__(
        self,
        assembler: TaskTrajectoryAssembler,
        repo: TaskTrajectoryRepositoryProtocol,
        analyzer: TaskTrajectoryAnalyzer,
        config: "TrajectoryAnalysisConfig | None" = None,
    ) -> None:
        self._assembler = assembler
        self._repo = repo
        self._analyzer = analyzer
        self._config = config

    async def get_trajectory(
        self,
        task_id: str,
        *,
        do_analysis: bool = False,
    ) -> TaskTrajectory:
        """Read the trajectory for ``task_id``; optionally trigger bot analysis.

        ``do_analysis=False`` (default, PURE READ): assembler reads the events
        table and returns ``TaskTrajectory``; ``analysis`` = persisted or
        ``None``; no DB write, no bot call.

        ``do_analysis=True``: assembles → builds ``ext_info_lookup`` → calls the
        configured ``tc_bot`` analyzer → serializes the resulting
        ``TrajectoryAnalysis`` JSON → ``backfill_analysis`` (overwrite, 决策 #13)
        → returns the same-shape ``TaskTrajectory`` carrying the fresh analysis.
        Bot failure/timeout → ``TrajectoryAnalysisError`` re-raised (504, no
        backfill — 决策 #14); bot not configured →
        ``TrajectoryAnalysisNotConfiguredError`` (503).
        """
        if not do_analysis:
            return self._assembler.assemble(task_id)
        return await self._do_analysis(task_id)

    # ------------------------------------------------------------------
    # do_analysis=True orchestration
    # ------------------------------------------------------------------

    async def _do_analysis(self, task_id: str) -> TaskTrajectory:
        """Assemble → build ext_info_lookup → analyze → backfill → return."""
        # 1. Assemble the timeline (read-side; no DB write beyond the head UPSERT
        #    which preserves existing analysis/gmt_modified).
        trajectory = self._assembler.assemble(task_id)

        # 2. Build ext_info_lookup from the repo's records (the assembler DROPPED
        #    ext_info; the analyzer re-queries via this closure).
        ext_info_lookup = _build_ext_info_lookup(self._repo, task_id)

        # 3. Resolve the deployment-configured analysis bot_id (decision #10:
        #    NOT per-request). None → 503 (service capability not ready, fix
        #    config — distinct from 504 bot failure/timeout).
        analysis_bot_id = self._resolve_analysis_bot_id()
        logger.info(f"[task][task_trajectory] analysis_bot_id={analysis_bot_id}")

        if analysis_bot_id is None:
            raise TrajectoryAnalysisNotConfiguredError(
                "trajectory analysis bot is not configured "
                "(task_trajectory.analysis_bot_id — or analysis_bot_id_pre for the "
                "pre env — missing in user_config); "
                "do_analysis=true requires a deployment-configured bot_id"
            )

        # 4. Call the analyzer (tc_bot executor; synchronous with timeout per
        #    决策 #10). Raises TrajectoryAnalysisError on bot timeout/failure/
        #    unparseable response — the caller (router) maps to 504; the service
        #    does NOT backfill on this path (decision #14).
        analysis = await self._analyzer.analyze(
            trajectory,
            ext_info_lookup,
            analysis_type=AnalysisType.TC_BOT,
            analysis_executor=analysis_bot_id,
        )

        # 5. Serialize the flat TrajectoryAnalysis (no event list, no recursion —
        #    P0). ensure_ascii=False keeps Chinese error messages readable in
        #    the persisted JSON (matches the emitter's ext_info convention).
        analysis_json = json.dumps(asdict(analysis), ensure_ascii=False)

        # 6. Backfill (overwrite both tables' analysis+gmt_modified — P1b
        #    transactional; decision #13 overwrite). Bot-failure path did NOT
        #    reach here (analyzer raised in step 4).
        self._repo.backfill_analysis(task_id, analysis_json)

        # 7. Return the TaskTrajectory carrying the fresh analysis. Mutate the
        #    in-memory object (avoids a second DB round-trip); gmt_modified
        #    reflects the backfill moment.
        trajectory.analysis = analysis_json
        trajectory.gmt_modified = int(time.time() * 1000)
        return trajectory

    # ------------------------------------------------------------------
    # Config resolution — optional DI; None when unbound (lightweight injectors)
    # ------------------------------------------------------------------

    def _resolve_analysis_bot_id(self) -> "str | None":
        """Read the deployment-configured, env-aware ``analysis_bot_id`` (decision #10).

        Mirrors ``openapi_bot.base_url`` env selection (``_env_select(prod,
        pre)``): pre env → ``analysis_bot_id_pre``, else → ``analysis_bot_id``.
        The pair is deployment-configured; ``get_current_env()`` normalizes
        prepub→pre / gray→prod (lazy import to avoid a module-load cycle, same
        idiom as ``_env_select``). A ``None`` config (lightweight DI that did not
        bind ``TrajectoryAnalysisConfig``) — or the env-relevant variant being
        unset — is treated as not-configured: the ``do_analysis=true`` call
        raises ``TrajectoryAnalysisNotConfiguredError`` (503). The default read
        path (``do_analysis=false``) never calls this.
        """
        if self._config is None:
            return None
        from agentclaw.community.utils.env_utils import get_current_env
        if get_current_env() == "pre":
            return self._config.analysis_bot_id_pre
        return self._config.analysis_bot_id

    # ------------------------------------------------------------------
    # Emit (write) — fire-and-forget event emission (decision #14). The
    # facade ``TaskContextService`` relays here; external callers never touch
    # the raw repo. Wraps payloads.emit_* with self._repo; the free funcs keep
    # the no-op-if-None + swallow+warn semantics unchanged.
    # ------------------------------------------------------------------

    def emit_trajectory_event(
        self,
        task_id: str,
        node_id: str,
        action_type: "TrajectoryActionType | str",
        *,
        action_result: str,
        action_input: str | None = None,
        error_type: "ReasonCatalog | str | None" = None,
        error_msg: str | None = None,
        ext_info: "dict[str, Any] | None" = None,
        status_from: "Status | str | None" = None,
        status_to: "Status | str | None" = None,
        attempt: int = 0,
        now_ms: int | None = None,
    ) -> None:
        """Emit one trajectory event via the repo. Fire-and-forget (decision #14):
        never raises; ``self._repo is None`` (lightweight DI) → no-op."""
        _emit_trajectory_event(
            self._repo,
            task_id,
            node_id,
            action_type,
            action_result=action_result,
            action_input=action_input,
            error_type=error_type,
            error_msg=error_msg,
            ext_info=ext_info,
            status_from=status_from,
            status_to=status_to,
            attempt=attempt,
            now_ms=now_ms,
        )

    def emit_submit_trajectory(
        self,
        task_id: str,
        task_info: "TaskInfo",
        *,
        submitted_at_ms: int,
        node_id: str | None = None,
    ) -> None:
        """Emit the SUBMIT trajectory event (REQ-6). Fire-and-forget (decision #14):
        never raises; ``self._repo is None`` (lightweight DI) → no-op."""
        _emit_submit_trajectory(
            self._repo,
            task_id,
            task_info,
            submitted_at_ms=submitted_at_ms,
            node_id=node_id,
        )
