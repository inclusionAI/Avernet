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

from agentclaw.community.core.task.task_runner.centralized_support import (
    Any,
    NodeAction,
    NodeOpResult,
    PlanResult,
    Status,
    TaskNodePatch,
    _EXEC_ERROR_MSG_MAX,
    _EXEC_ERROR_ORIGIN_TO_REASON,
    logger,
)

import hashlib
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
)
from agentclaw.community.di.task_trajectory_config import TrajectoryAnalysisConfig

if TYPE_CHECKING:
    from agentclaw.community.core.task.domain.models import Status
    from agentclaw.community.core.task.task_context.task_graph_service import (
        TaskGraphService,
    )
    from agentclaw.community.core.task.task_runner.client.bcs_bot_token_provider import (
        BcsBotTokenProvider,
    )
    from agentclaw.community.core.task.task_runner.client.ports import (
        BcsClientPort,
    )

logger = logging.getLogger("task.trajectory")

# RUNNING 节点会话明细探测的摘录预算(字符):单条消息截断上限 + 全任务所有
# 节点摘录总上限(超预算即停止消费且标 truncated,防 token 失控)。
_RUNNING_SESSION_MSG_MAX_CHARS = 600
_RUNNING_SESSION_TOTAL_MAX_CHARS = 4000


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


# ---------------------------------------------------------------------------
# Timeline fingerprint — the incremental idempotency key for ``do_analysis``
# ---------------------------------------------------------------------------


def _timeline_fingerprint(records: list[TrajectoryEventRecord]) -> str:
    """The sha256 fingerprint of a task's FULL trajectory-event set — the
    ``timeline_version`` stamped into :class:`TrajectoryAnalysis` at backfill.

    Ingredient per requirement: this task's ALL events concatenated in
    ``id + gmt_create`` ascending order, prefixed by the event count. The
    modified-timestamp (``gmt_modify``) is deliberately EXCLUDED:
    ``backfill_analysis`` rewrites EVERY event row's ``gmt_modify`` on every
    backfill (explicit UPDATE value + ORM ``onupdate``), so a gmt_modify-based
    fingerprint recorded in run N can never match the re-computation in run
    N+1 — idempotency would self-defeat and the bot would re-run on every
    call. ``gmt_create`` is written only at INSERT and never touched again,
    and the table is append-only, so "new event" ≡ "new id + new gmt_create"
    — exactly the change signal the version check wants.

    Pure function: no repo access, deterministic across input order (sorts by
    ``id`` itself). Empty list → the stable ``sha256("v1:0")`` so a zero-event
    task analysis stays idempotent.
    """
    parts = [f"v1:{len(records)}"]
    for rec in sorted(records, key=lambda r: r.id):
        gc = rec.gmt_create
        formatted = (
            gc.isoformat(sep=" ", timespec="microseconds")
            if hasattr(gc, "isoformat") else str(gc)
        )
        parts.append(f"|{rec.id}:{formatted}")
    return hashlib.sha256("".join(parts).encode("utf-8")).hexdigest()


def _persisted_timeline_version(analysis_json: str | None) -> str | None:
    """Defensively read the ``timeline_version`` stamped inside the persisted
    head-row analysis JSON. ``None`` on any failure — non-dict JSON, corrupt
    text, or a legacy analysis persisted before the key existed (``None`` is
    the "never matches" sentinel: legacy rows re-analyze exactly once, then
    carry the stamp)."""
    if not analysis_json:
        return None
    try:
        parsed = json.loads(analysis_json)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(parsed, dict):
        return None
    version = parsed.get("timeline_version")
    return version if isinstance(version, str) and version else None


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
    records: list[TrajectoryEventRecord] | None = None,
) -> Callable[[TrajectoryEvent], dict | None]:
    """Build the ``ext_info_lookup`` seam the analyzer consumes.

    Re-queries ``list_events_by_task(task_id)`` to get the records WITH ``ext_info``
    JSON (the assembler dropped ``ext_info`` from the domain ``TrajectoryEvent``),
    parses each record's ``ext_info`` defensively (broken JSON → ``None``, never
    raises — 决策 #14 emission-only waiver spirit; a single corrupt ``ext_info``
    must not break the on-demand analysis), and returns a closure
    ``lookup(event) -> dict | None`` keyed by the stable 4-tuple signature.

    ``records``: the SAME ``list_events_by_task`` result the caller already
    fetched (the ``do_analysis`` path reads it ONCE for the timeline
    fingerprint + this lookup). Supplied → the internal repo re-fetch is
    skipped; ``None`` → fetched here (unchanged standalone behavior).

    The outer ``schema_v`` envelope (``{"schema_v": 1, "_dispatch_rationale": ...,
    "elapsed_ms": ...}``) is preserved as-is — the analyzer reads the specific
    keys it needs (``_dispatch_rationale`` / ``elapsed_ms`` / ``sla_threshold_ms``)
    and ignores ``schema_v`` (P5a carry-note).
    """
    lookup: dict[tuple, dict | None] = {}
    try:
        if records is None:
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
        boost_reason: str | None = None,
        now_ms: int | None = None,
    ) -> None:
        """Fire-and-forget trajectory event write (never raises; decision #14)."""
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
        graph: "TaskGraphService | None" = None,
        bcs: "BcsClientPort | None" = None,
        bcs_bot_tokens: "BcsBotTokenProvider | None" = None,
    ) -> None:
        """``graph`` + ``bcs`` (the RUNNING-node session probe's collaborators)
        are optional lightweight-DI deps: either unbound/None → the probe is
        DISABLED and do_analysis passes ``running_sessions=None`` (zero
        behavior change vs. the pre-probe service). Full DI wires both via
        try/except-get fallbacks in ``TaskPersistenceModule``.

        ``bcs_bot_tokens`` resolves the RUNNING 节点持有者 bot 的 BCS session_token
        (``BcsBotTokenProvider``):BCS 会话历史读口有参与者级 ACL(401 "valid Human
        identity or Bot token is required")——服务 HMAC 签名不是会话参与者;探测带
        持有者 bot 的 Bearer 才能读到明细。未注入/解析不到 → 裸 HMAC 尝试,401 再
        按既有路径 WARNING 跳过(降级不阻断主分析)。"""
        self._assembler = assembler
        self._repo = repo
        self._analyzer = analyzer
        self._config = config
        self._graph = graph
        self._bcs = bcs
        self._bcs_bot_tokens = bcs_bot_tokens

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

        ``do_analysis=True``: INCREMENTALLY IDEMPOTENT — assembles; computes the
        timeline fingerprint (ALL the task's event rows, ``id+gmt_create`` 正序);
        if the head row carries a non-empty ``analysis`` whose persisted
        ``timeline_version`` equals the freshly computed fingerprint (the
        timeline is unchanged since the last analysis), returns it directly
        WITHOUT calling the bot (省一次 bot 调用 / 504). Mismatch — empty/None
        analysis, a legacy analysis without a stamp, or new events appended
        after the last backfill — re-runs the bot: builds ``ext_info_lookup``
        → calls the configured ``tc_bot`` analyzer → stamps the fresh
        ``timeline_version`` → serializes the ``TrajectoryAnalysis`` JSON →
        ``backfill_analysis`` (overwrite, 决策 #13 on the bot-run path) → returns
        the same-shape ``TaskTrajectory`` carrying the fresh analysis. To FORCE
        a refresh, append/change timeline events (or clear the head-row
        analysis), then re-call ``do_analysis=True``. Bot failure/timeout →
        ``TrajectoryAnalysisError`` re-raised (504, no backfill — 决策 #14); bot
        not configured (AND analysis stale/absent) →
        ``TrajectoryAnalysisNotConfiguredError`` (503).
        """
        if not do_analysis:
            trajectory = self._assembler.assemble(task_id)
            self._attach_node_outputs(trajectory)
            return trajectory
        return await self._do_analysis(task_id)

    # ------------------------------------------------------------------
    # do_analysis=True orchestration
    # ------------------------------------------------------------------

    async def _do_analysis(self, task_id: str) -> TaskTrajectory:
        """Assemble → (timeline 版本与持久分析戳一致则直接返回) → build ext_info_lookup
        → analyze → stamp timeline_version → backfill → return."""
        # 1. Assemble the timeline (read-side; no DB write beyond the head UPSERT
        #    which preserves existing analysis/gmt_modified).
        trajectory = self._assembler.assemble(task_id)
        # 1a. 读时富化:每个子任务(task_id+node_id)的最后一条事件挂上该节点的
        #     当前产出(task_execution_graph 查询接口)。旁路,失败降级不抛;两模式
        #     返回的 timeline 同形态(含快路径直接返回的持久化分析)。
        self._attach_node_outputs(trajectory)

        # 1b. Read ALL the task's event rows ONCE — feeding both the timeline
        #     fingerprint and the ext_info_lookup below. Read failure → WARNING
        #     + ``fingerprint=None`` (the never-matching sentinel: an
        #     unfingerprintable read must NOT be treated as a stale-match — the
        #     analysis re-runs rather than serving possibly-stale analysis).
        try:
            records = self._repo.list_events_by_task(task_id)
        except Exception as ex:  # noqa: BLE001  指纹读失败 → 视为不匹配(重跑,不误命中)
            logger.warning(
                "[task][trajectory] list_events_by_task 失败,指纹未计算 task=%s: %s: %s",
                task_id, type(ex).__name__, ex,
            )
            records = None
        fingerprint = _timeline_fingerprint(records) if records is not None else None

        # 2. Incremental idempotent fast path: head analysis present AND its
        #    persisted ``timeline_version`` equals the freshly computed
        #    fingerprint → the timeline has not changed since the last
        #    analysis; return the persisted analysis WITHOUT calling the bot
        #    (省一次 bot 调用 / 504). Mismatch → the timeline gained events
        #    after the last backfill → re-run the bot and re-stamp. A legacy
        #    analysis persisted before ``timeline_version`` existed carries no
        #    stamp → mismatch → re-analyzes exactly once, then carries the
        #    stamp. ``fingerprint=None`` (records read failed) never matches.
        if (
            trajectory.analysis
            and fingerprint is not None
            and _persisted_timeline_version(trajectory.analysis) == fingerprint
        ):
            logger.info(
                "[task][trajectory] timeline_version match, skip bot call "
                "(idempotent do_analysis) task=%s head_analysis_len=%d",
                task_id, len(trajectory.analysis),
            )
            return trajectory

        # 3. Build ext_info_lookup from the same records (the assembler DROPPED
        #    ext_info; the analyzer re-queries via this closure).
        ext_info_lookup = _build_ext_info_lookup(self._repo, task_id, records=records)

        # 4. Resolve the deployment-configured analysis bot_id (decision #10:
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

        # 5. RUNNING 节点会话明细探测(决策 #14 精神:观测旁路,永不抛)。有节点卡在
        #    RUNNING 时,经 task_execution_graph 拿 session_id → BCS 拉会话明细,
        #    组 brief 交给分析 bot(大模型)判"工具调用报错 / 执行完不上报结果"等
        #    timeline 看不到的问题。任一步失败 → 降级跳过该节点(WARNING),不影响
        #    主分析。
        running_sessions = await self._collect_running_session_briefs(task_id)

        # 6. Call the analyzer (tc_bot executor; synchronous with timeout per
        #    决策 #10). Raises TrajectoryAnalysisError on bot CALL failure/
        #    timeout or an EMPTY/unusable response — the caller (router) maps
        #    to 504; the service does NOT backfill on this path (decision #14).
        #    NB: a NON-EMPTY malformed-JSON response no longer 504s — the
        #    analyzer repairs bare inner quotes (方案A) or degrades to the raw
        #    text as analysis_output (方案B); only an empty response has
        #    nothing to salvage.
        analysis = await self._analyzer.analyze(
            trajectory,
            ext_info_lookup,
            analysis_type=AnalysisType.TC_BOT,
            analysis_executor=analysis_bot_id,
            running_sessions=running_sessions,
        )

        # 7. Stamp the timeline fingerprint the analysis was computed from —
        #    the NEXT ``do_analysis=true`` compares its re-computed fingerprint
        #    against this stamp (match → skip the bot). A ``records`` read
        #    failure stamps ``None`` → the next call re-runs (never a stale
        #    match). Stamped on the service side — the analyzer stays
        #    transport-only and knows nothing of the repo.
        analysis.timeline_version = fingerprint

        # 8. Serialize the flat TrajectoryAnalysis (no event list, no recursion —
        #    P0). ensure_ascii=False keeps Chinese error messages readable in
        #    the persisted JSON (matches the emitter's ext_info convention).
        #    The JSON now carries ``timeline_version`` automatically via ``asdict``.
        analysis_json = json.dumps(asdict(analysis), ensure_ascii=False)

        # 9. Backfill (overwrite both tables' analysis+gmt_modified — P1b
        #    transactional; decision #13 overwrite). Bot-failure path did NOT
        #    reach here (analyzer raised in step 6).
        self._repo.backfill_analysis(task_id, analysis_json)

        # 10. Return the TaskTrajectory carrying the fresh analysis. Mutate the
        #    in-memory object (avoids a second DB round-trip); gmt_modified
        #    reflects the backfill moment.
        trajectory.analysis = analysis_json
        trajectory.gmt_modified = int(time.time() * 1000)
        return trajectory

    # ------------------------------------------------------------------
    # 节点产出读时富化 — timeline 的"每子任务最后一条事件"携带执行产出
    # (决策 #14 精神:观测旁路,graph 未接线/图读失败 → 跳过,绝不影响轨迹本体)。
    # ------------------------------------------------------------------

    def _attach_node_outputs(self, trajectory: TaskTrajectory) -> None:
        """把每个子任务(task_id+node_id)的**当前产出**挂到它在 timeline 中的
        **最后一条**事件上。

        产出数据经 ``task_execution_graph`` 的查询接口获取
        (``query_task_dashboard`` → ``node.run_info.output``——执行回投写图的
        节点级 dict 产出)。倒序扫 timeline 找每个 node 的末位事件(正序时间线的
        "最新一条"),挂 ``ev.output``(浅拷贝与图现场解耦)。挂载语义:
        空 ``{}`` / 缺失的产出保持 ``None``(缺字段=无信号,DTO/HTML 不透出)。

        旁路保障:``graph`` 未接线(轻量 DI)→ 静默跳过;图读失败(任务已删 /
        relay 外部托管等)→ WARNING + 返回未富化;一律**不**影响 ``get_trajectory``
        本体返回。注意:富化不写库、不进指纹(``timeline_version`` 仍只敏感于
        事件行变化)。
        """
        if self._graph is None or not trajectory.timeline:
            return
        try:
            graph = self._graph.query_task_dashboard(trajectory.task_id)
        except Exception as ex:  # noqa: BLE001  图读失败 → 不富化,轨迹本体照常
            logger.warning(
                "[task][trajectory] 节点产出富化图读失败,跳过 task=%s: %s: %s",
                trajectory.task_id, type(ex).__name__, ex,
            )
            return
        outputs: dict[str, dict] = {}
        for node in getattr(graph, "tasks", []):
            out = getattr(node.run_info, "output", None)
            if isinstance(out, dict) and out:
                outputs[node.node_id] = out
        if not outputs:
            return
        last_seen: set[str] = set()
        for ev in reversed(trajectory.timeline):
            node_id = ev.node_id
            if node_id in last_seen:
                continue  # 该节点更早的事件:production 只挂最后一条
            last_seen.add(node_id)
            out = outputs.get(node_id)
            if out:
                ev.output = dict(out)

    # ------------------------------------------------------------------
    # RUNNING 节点会话明细探测 — timeline 之外的"执行侧现场"(决策 #14 精神:
    # 观测旁路,逐步降级,永不抛;探测失败绝不影响主分析)。
    # ------------------------------------------------------------------

    async def _collect_running_session_briefs(self, task_id: str) -> "list[dict] | None":
        """Probe the SESSION-side scene of nodes still RUNNING (task_id+node_id).

        For each node whose current status is RUNNING (queried from
        ``task_execution_graph`` — the authoritative live graph, not the
        trajectory's event-side view), read ``session_id`` from
        ``run_info.extend_props`` and pull the session's recent messages via
        ``BcsClientPort.get_session_messages``; the excerpt (role/content pairs,
        per-message + total budget truncated) goes to the analysis bot as the
        ``running_sessions`` section — the LLM's cue to look for what the
        timeline CANNOT show: tool-call errors, 执行已结束但不主动上报结果,
        长时间无新进展 etc.

        Guarantees: NEVER raises (every step degrades + WARNING); ``None`` when
        the probe is disabled (``graph``/``bcs`` unbound), the graph read
        fails, no node is RUNNING, or every node's brief degraded away. The
        ``Status`` import at module top (``centralized_support``) is runtime —
        ``Status.RUNNING`` is compared against node.status as-is.
        """
        logger.info("[task][trajectory], collect_trajectory_event_session_msgs, begin")

        if self._graph is None or self._bcs is None:
            logger.info("[task][trajectory], collect_trajectory_event_session_msgs, graph or bcs is none")
            return None  # 探测未接线(轻量 DI / 未部署 BCS)→ 无 RUNNING 会话段
        try:
            graph = self._graph.query_task_dashboard(task_id)
        except Exception as ex:  # noqa: BLE001  图读失败 → 探测整体降级
            logger.warning(
                "[task][trajectory] collect_trajectory_event_session_msgs, RUNNING 探测图读失败 task=%s: %s: %s",
                task_id, type(ex).__name__, ex,
            )
            return None
        running = [n for n in getattr(graph, "tasks", []) if n.status == Status.RUNNING]
        if not running:
            logger.info("[task][trajectory], collect_trajectory_event_session_msgs, running is none")
            return None
        # 卡得最久的排最前(总摘录预算耗尽时优先保留最"病"的节点)
        now_ms = int(time.time() * 1000)
        def _elapsed_ms(node) -> int:  # noqa: ANN001  domain node;defensive read
            st = getattr(node.run_info, "start_time", None)
            try:
                return now_ms - int(st) if st is not None else 0
            except (TypeError, ValueError):
                return 0
        running.sort(key=_elapsed_ms, reverse=True)

        limit = 50
        try:
            limit = int(
                getattr(self._config, "running_session_message_limit", 50) or 50
            )
        except (TypeError, ValueError):
            limit = 50

        briefs: list[dict] = []
        budget = _RUNNING_SESSION_TOTAL_MAX_CHARS
        for node in running:
            if budget <= 0:
                break  # 总预算耗尽:截断在节点粒度即止(不再拉新会话)
            sid = (node.run_info.extend_props or {}).get("session_id")
            if not sid or not isinstance(sid, str):
                logger.warning(
                    "[task][trajectory] collect_trajectory_event_session_msgs, RUNNING 节点无 session_id,跳过探测 task=%s node=%s",
                    task_id, node.node_id,
                )
                continue
            # 会话历史读口是参与者级 ACL(401 "valid Human identity or Bot token is
            # required"):服务 HMAC 签名不是会话参与者。带 RUNNING 节点持有者 bot 的
            # session_token 做 ``Authorization: Bearer``(同 create_group 的 caller
            # 身份手法),BCS 把 caller 解析成会话内成员 bot。持有者 id 取值序:
            # relay_holder_id(relay 棒)→ driver_bot_id(协作群 driver)→ assignee
            # (single_bot;协作群时是 group_id,解析自然 None→裸 HMAC 尝试)。
            holder_token = self._holder_bearer_token(node)
            try:
                logger.info("[task][trajectory], collect_trajectory_event_session_msgs, begin get bcs msgs holder_bearer=%s",
                            "yes" if holder_token else "hmac-only")
                msgs = await self._bcs.get_session_messages(
                    sid, limit=limit, caller_bot_token=holder_token,
                )
                logger.info("[task][trajectory], collect_trajectory_event_session_msgs, finish get bcs msgs")
            except Exception as ex:  # noqa: BLE001  单节点 BCS 失败 → 跳过,不拖垮其余
                logger.warning(
                    "[task][trajectory] collect_trajectory_event_session_msgs, 会话明细拉取失败,跳过 task=%s node=%s session=%s: %s: %s",
                    task_id, node.node_id, sid, type(ex).__name__, ex,
                )
                continue

            if not msgs:
                logger.info(
                    "[task][trajectory] collect_trajectory_event_session_msgs, 会话明细为空,跳过 task=%s node=%s session=%s",
                    task_id, node.node_id, sid,
                )
                continue
            excerpt: list[dict] = []
            truncated = False
            for m in msgs:
                if not isinstance(m, dict):
                    continue  # 非预期形态 → 跳过该条(防御)
                role = str(m.get("role") or "")
                content = str(m.get("content") or "")
                if len(content) > _RUNNING_SESSION_MSG_MAX_CHARS:
                    content = content[: _RUNNING_SESSION_MSG_MAX_CHARS - 1] + "…"
                if budget <= 0:
                    truncated = True
                    break
                if len(content) > budget:
                    content = content[: budget - 1] + "…"
                    truncated = True
                budget -= len(content)
                excerpt.append({"role": role, "content": content})
            if not excerpt:
                continue
            briefs.append({
                "node_id": node.node_id,
                "session_id": sid,
                "elapsed_ms": _elapsed_ms(node),
                "message_count": len(msgs),
                "truncated": truncated,
                "messages": excerpt,
            })
        if not briefs:
            return None
        logger.info(
            "[task][trajectory] collect_trajectory_event_session_msgs, RUNNING 会话探测完成 task=%s briefs=%d",
            task_id, len(briefs),
        )
        return briefs

    def _holder_bearer_token(self, node) -> "str | None":
        """解析 RUNNING 节点**持有者 bot** 的 BCS session_token(会话历史读取身份)。

        持有者 id 取值序(都是执行链路落在 extend_props/assignee 上的事实字段):
        ``relay_holder_id``(relay 当前棒持有 bot)→ ``driver_bot_id``(协作群
        driver)→ ``run_info.assignee``(single_bot 执行 bot;协作群时该值是
        group_id——交给 provider 解析,自然 None)。经 ``BcsBotTokenProvider``
        (corp 读 ``bcs_bots.session_token``,带缓存)取 token;未注入 provider /
        id 缺失 / 解析失败 → ``None``,调用方回退裸 HMAC(BCS 按 401 拒,走
        既有 WARNING 降级)。全程防御,永不抛。token 不落日志。
        """
        if self._bcs_bot_tokens is None:
            return None
        try:
            ep = node.run_info.extend_props or {}
            holder = (
                ep.get("relay_holder_id")
                or ep.get("driver_bot_id")
                or getattr(node.run_info, "assignee", None)
            )
            logger.info("[task][trajectory], collect_trajectory_event_session_msgs, hold is = %s", holder)
            if not holder or not isinstance(holder, str):
                return None
            return self._bcs_bot_tokens.get_token(holder) or None
        except Exception:  # noqa: BLE001  身份解析是观测旁路,任何失败回退匿名
            return None

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
        boost_reason: str | None = None,
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
            boost_reason=boost_reason,
            now_ms=now_ms,
        )

    def _emit_plan_trajectory(
        self,
        task_id: str,
        target_id: str | None,
        pr: PlanResult,
        attempt: int,
        failure_msg: str | None,
    ) -> None:
        """REQ-3 (P3-2) 每次尝试(retry attempt)发射一条 PLAN 轨迹事件。

        additive 旁路:与既有 post-loop ``_log_action(NodeAction.PLAN, ...)`` 在同一
        闸门位置、互不相干,经 ``emit_trajectory_event`` 独立 direct-INSERT 到
        ``task_trajectory_events``(不走 ``task_action_log``)。``action_input=pr.prompt_digest``
        (workflow 策略 → None);失败 mid-row(``gap_detail`` 以 ``plan_`` 开头)带
        ``error_type=PLAN_FAILURE`` + 截断 ``error_msg`` + ``ext_info={strategy_name,
        gap_detail, raw_response_digest}``;成功条带 ``ext_info={strategy_name, has_gap,
        gap_detail, children, raw_response_digest}``、``error_*=None``。

        全程防御:emitter(``emit_trajectory_event``)兜一层 try/except + WARNING
        (决策 #14);ext_info 装配在当前 PlanResult 形状下不会抛,无独立内层 guard
        (避免静默 swallow 掉未来的装配 bug —— 决策 #14 要求失败可见:WARNING)。
        ``repo is None`` 时 emitter 静默 no-op(测试/轻量 DI 取不到协议)。"""
        if target_id is None:
            # 无锚定节点(无根/缺图)——不发射,跳过;正常路径下不应发生(根已就绪)。
            return
        try:
            gd = pr.gap_detail or ""
            is_failure = gd.startswith("plan_")
            if is_failure:
                # call_fail  = bot 调用没拿到可用的 COMPLETED 响应(传输/未完成:
                #   plan_call_fail=planner 抛异常;plan_not_completed=run 非 COMPLETED)
                # parse_fail = COMPLETED 响应但形态无法用(空 content / 解析失败 /
                #   形态非预期:plan_parse_fail / plan_shape_unexpected / plan_empty_content)
                # 对齐 TrajectoryEvent.action_result 词表;analyzer 据此归因 plan_failure 子类。
                action_result = "parse_fail" if gd in (
                    "plan_parse_fail", "plan_shape_unexpected", "plan_empty_content",
                ) else "call_fail"
                raw_msg = failure_msg or gd
                error_msg = raw_msg if len(raw_msg) <= 500 else raw_msg[:497] + "..."
            else:
                action_result = "success"
                error_msg = None
            # ext_info 装配:dict 字面量在当前 PlanResult 形状下不会抛;不做静默 swallow
            # (决策 #14 要求失败可见:WARNING)。未来的装配 bug 由外层 try/except +
            # logger.warning 统一捕获可见(此处无独立 guard)。
            if is_failure:
                ext_info: dict[str, Any] = {
                    "strategy_name": pr.strategy_name,
                    "gap_detail": gd or None,
                    "raw_response_digest": pr.raw_response_digest,
                }
            else:
                ext_info = {
                    "strategy_name": pr.strategy_name,
                    "has_gap": pr.has_gap,
                    "gap_detail": gd or None,
                    "children": list(pr.planned_children or []),
                    "raw_response_digest": pr.raw_response_digest,
                }
            self._log_trajectory(
                task_id,
                target_id,
                "plan",  # TrajectoryActionType.PLAN.value(TYPE_CHECKING-only enum;emitter 接受 str)
                action_result=action_result,
                action_input=pr.prompt_digest,
                error_type="plan_failure" if is_failure else None,
                error_msg=error_msg,
                ext_info=ext_info,
                status_from=Status.PLANNING,
                status_to=Status.PLANNING,
                attempt=attempt,
            )
        except Exception as ex:  # noqa: BLE001  轨迹旁路:吞而不抛 + WARNING(决策 #14)
            logger.warning(
                "[task][trajectory][plan] task=%s attempt=%d 发射失败:%s",
                task_id, attempt, ex,
            )

    def _log_action(
        self,
        task_id: str,
        node_id: str,
        action: NodeAction,
        payload: dict,
        *,
        attempt: int | None = None,
        status_from: Status | None = None,
        status_to: Status | None = None,
    ) -> None:
        """追加节点动作历史快照(append-only;零侵入驱动逻辑)。

        供各逻辑动作(PLAN/DISPATCH/EXECUTE/VERIFY/RESET/TRANSITION)完成时调用,
        纯可观测旁路:不翻态、不读回驱动。``attempt`` 省略时取节点 harness_retries 快照;
        ``status_from``/``status_to`` 省略时由调用方按动作前/后态传(未翻态可不传)。
        """
        if attempt is None:
            node = next(
                (
                    n
                    for n in self._graph.query_task_dashboard(task_id).tasks
                    if n.node_id == node_id
                ),
                None,
            )
            attempt = (
                int(node.run_info.extend_props.get("harness_retries", 0)) if node else 0
            )
        try:
            self._graph.append_action_event(
                task_id,
                node_id,
                action,
                payload,
                attempt=attempt,
                status_from=status_from,
                status_to=status_to,
            )
        except Exception as ex:  # noqa: BLE001  历史快照写入失败不影响驱动
            logger.warning(
                "[task][action-log] task=%s node=%s action=%s 追加失败:%s",
                task_id,
                node_id,
                action.value,
                ex,
            )

    def _log_trajectory(
        self,
        task_id: str,
        node_id: str,
        action_type: "TrajectoryActionType",
        *,
        action_result: str,
        action_input: str | None = None,
        error_type: "ReasonCatalog | None" = None,
        error_msg: str | None = None,
        ext_info: dict[str, Any] | None = None,
        status_from: Status | None = None,
        status_to: Status | None = None,
        attempt: int = 0,
    ) -> None:
        """旁路发射一条任务轨迹事件(REQ-11 采集层)。零侵入驱动逻辑:

        - 与 ``_log_action`` 在同一闸门位置调用,但**完全独立**——经
          ``self._task_context_service.emit_trajectory_event(...)`` 中转到内部
          ``TaskTrajectoryService`` 直接 INSERT 到 ``task_trajectory_events``,
          **不**经 ``self._graph.append_action_event``
          (plan §"Spec clarifications" #2:The trajectory path is independent and
          direct-INSERT;mirrors only the swallow + no re-raise pattern)。
        - ``self._task_context_service`` 为 ``None`` 时(测试/轻量 DI 取不到协议)→
          跳过发射静默 no-op,正向驱动不受影响(内部 repo-None 情形由 emitter 再兜底 no-op)。
        - 全程 ``try/except Exception`` 吞异常(**不**抛出),失败记 WARNING 日志
          (已确认决策 #14;AGENTS.md "propagate persistence write failures" 对此
          fire-and-forget 观测旁路**明示豁免**)——见 ``task_trajectory/payloads.py``。

        线程/调用约定:同步调用,可在各 gate 的锁内/锁外任意位置直接调用。
        ``action_input`` **不截断**(原文落库);``error_msg`` 由调用方(各 gate)截断后传入。
        ``now_ms`` 由 emitter 取当前 wall-clock(emitter 内有兜底),故本方法不暴露该参数。
        """
        # ``task_context_service is None`` (lightweight DI: trajectory unbound) → no-op;
        # when present, the service relays to the internal TaskTrajectoryService whose
        # emitter no-ops + swallows on repo-None (decision #14). The guard keeps the
        # gate concise regardless.
        if self._task_context_service is not None:
            self._task_context_service.emit_trajectory_event(
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
            )

    @staticmethod
    def _transition_action_result(status_to: Status | None) -> str:
        """Map a TRANSITION's ``status_to`` to a trajectory ``action_result``
        (REQ-1's open-ended ``...`` enumeration). The status_to-derived
        lowercase name mirrors the ``_log_action`` payload's ``to`` field and
        is what the analyzer's ``_terminal_status`` keys on (via ``status_to``
        membership in the terminal set, not ``action_result``). Mapping:

            SUCCESS→"success"   HUNG→"hung"   FAILED→"failed"
            CANCELLED→"cancelled"   DONE→"done"   RUNNING→"running"
            PENDING→"pending"   PLANNING→"planning"

        ``None`` (defensive — no status_to on the event) → ``"transition"``.
        """
        if status_to is None:
            return "transition"
        return str(getattr(status_to, "value", status_to)).lower()

    @staticmethod
    def _read_exec_error_origin(patch: TaskNodePatch) -> str | None:
        """Read the surfaced ``_exec_error_origin`` from the patch's
        ``extend_props_patch`` defensively. Returns ``None`` on any failure
        (incl. a hostile dict that raises on ``.get``) — the gate's
        origin read is decision-#14 trajectory-assembly scope (a raise here
        degrades to ``error_type=None``, NOT a gate failure)."""
        try:
            ep = patch.extend_props_patch
            if isinstance(ep, dict):
                origin = ep.get("_exec_error_origin")
                if isinstance(origin, str):
                    return origin
        except Exception:  # noqa: BLE001  trajectory-assembly read; 决策 #14 scope
            return None
        return None

    @staticmethod
    def _read_interface_error_code(patch: TaskNodePatch) -> str | None:
        """Read an optional ``interface_error_code`` surfaced on the patch's
        ``extend_props_patch`` (the bot may set it via ``result._ext_info``).
        Defensive — ``None`` on any failure / absent key."""
        try:
            ep = patch.extend_props_patch
            if isinstance(ep, dict):
                code = ep.get("interface_error_code")
                if code is not None:
                    return str(code)
        except Exception:  # noqa: BLE001  trajectory-assembly read; 决策 #14 scope
            return None
        return None

    def _read_exec_request_input_and_attempt(
        self, task_id: str, node_id: str
    ) -> tuple[str | None, int]:
        """Defensively read, in ONE graph query, the downstream request原文
        (``node.run_info.extend_props["_exec_request_input"]``) and the harness
        retries snapshot (``harness_retries``) for the EXECUTE/VERIFY trajectory
        event (REQ-1/REQ-5). ``action_input`` is the request原文 — **not
        truncated** — read from the node the executor would have surfaced it on
        (production surfacing is a separate executor-wire concern; the gate
        consumes the key defensively, ``None`` when absent). ``attempt`` mirrors
        ``_log_action``'s default ``harness_retries`` snapshot. Returns
        ``(None, 0)`` on any failure — decision-#14 trajectory-assembly scope
        (NOT the gate's main driving logic)."""
        try:
            graph = self._graph.query_task_dashboard(task_id)
            node = next((n for n in graph.tasks if n.node_id == node_id), None)
            if node is None:
                return None, 0
            ri = node.run_info.extend_props
            req = ri.get("_exec_request_input")
            req = req if isinstance(req, str) else None
            attempts = int(ri.get("harness_retries", 0) or 0)
            return req, attempts
        except Exception:  # noqa: BLE001  trajectory-assembly read; 决策 #14 scope
            return None, 0

    def _emit_execute_trajectory(
        self,
        patch: TaskNodePatch,
        result: NodeOpResult,
        *,
        action_type: str,            # "execute" | "verify"
        action_result: str,          # "failed" | "success" | "accept_pass" | "accept_fail"
        is_exec_error: bool = False,  # True only on the exec_error (EXECUTE err) path
    ) -> None:
        """REQ-5: fire one EXECUTE/VERIFY trajectory event **additively** to
        ``_log_action(NodeAction.EXECUTE/VERIFY, ...)`` (alongside, NOT replacing).

        ``error_type`` 按 ``patch.extend_props_patch["_exec_error_origin"]``
        经 ``_EXEC_ERROR_ORIGIN_TO_REASON`` 映射(``bot_interface→
        underlying_interface_error`` 等);未映射/无 origin → ``None``。
        ``error_msg`` = ``patch.exec_error`` 截断 ≤500(仅 ``is_exec_error`` 路径;
        EXECUTE(ok) / VERIFY 无 error_msg)。
        ``action_input`` = 下发请求原文(从节点 ``run_info.extend_props
        ["_exec_request_input"]`` 防御性读取;缺失 ``None``、**不截断**)。
        ``ext_info={"interface_error_code": <code>}`` 若 patch 透出该 code,否则 ``None``。
        ``status_from``/``status_to``/``attempt`` 对齐 ``_log_action``。

        决策 #14:全程 ``try/except`` 吞而不抛 + WARNING(轨迹旁路 fire-and-forget,
        不阻塞闸门主逻辑——本方法只在装配/发射轨迹,不在 swallows 内含任何
        驱动逻辑)。emitter(``emit_trajectory_event``)另兜一层 try/except + WARNING。
        ``repo is None`` 时 emitter 静默 no-op。
        """
        try:
            origin = self._read_exec_error_origin(patch)
            error_type = _EXEC_ERROR_ORIGIN_TO_REASON.get(origin) if origin else None
            if is_exec_error and patch.exec_error:
                raw = patch.exec_error
                error_msg = (
                    raw if len(raw) <= _EXEC_ERROR_MSG_MAX
                    else raw[: _EXEC_ERROR_MSG_MAX - 3] + "..."
                )
            else:
                error_msg = None
            action_input, attempt = self._read_exec_request_input_and_attempt(
                patch.task_id, patch.node_id
            )
            code = self._read_interface_error_code(patch)
            ext_info = {"interface_error_code": code} if code is not None else None
            self._log_trajectory(
                patch.task_id,
                patch.node_id,
                action_type,  # TrajectoryActionType.EXECUTE/VERIFY.value (emitter accepts str)
                action_result=action_result,
                action_input=action_input,
                error_type=error_type,
                error_msg=error_msg,
                ext_info=ext_info,
                status_from=result.prev_status,
                status_to=result.new_status,
                attempt=attempt,
            )
        except Exception as ex:  # noqa: BLE001  轨迹旁路:吞而不抛 + WARNING(决策 #14)
            logger.warning(
                "[task][trajectory][%s] task=%s node=%s 发射失败:%s",
                action_type, patch.task_id, patch.node_id, ex,
            )

    def _static_runtime(self, task_id: str):
        from agentclaw.community.core.task.task_plan.static_plan import StaticPlanDefinition
        from agentclaw.community.core.task.task_plan.static_plan import StaticPlanRuntime
        cfg = self._graph._execution_config(task_id)
        # 判据:cfg 含 ``static_plan_id`` 或 ``static_plan_yaml`` 任一即视为预置模板 plan(不依赖 task_type 字符串);
        # task_type 仍可显式 STATIC_PLAN 兼容旧调用方,但默认 dynamic caller 经 execute 内容路由命中后,
        # 也会在此处回填 static_plan_id/static_plan_yaml 进入预置 plan runtime。
        template_id = cfg.get("static_plan_id")
        yaml_text = cfg.get("static_plan_yaml")
        if not template_id and not yaml_text and cfg.get("task_type") != "static_plan":
            return None
        if not yaml_text and template_id:
            # 显式只传 task_type/static_plan_id 未带 yaml → 从仓库 plans 懒加载。
            # plans 仓库固定位于 core/task/task_plan/plans — 本方法被 CentralizedExecutionAdapter
            # borrow 执行(bbs 重构自 task_center/task_service 同源迁入),锚定 static_plan 模块自身
            # 定位;沿用 parents[1] 会随宿主文件层级漂移(task_context/task_trajectory 下不再指向
            # core/task,模板懒加载静默失效 → runtime=None)。
            from pathlib import Path
            from agentclaw.community.core.task.task_plan import static_plan as _static_plan_module
            plans_dir = Path(_static_plan_module.__file__).resolve().parent / "plans"
            plans_path = plans_dir / f"{template_id}.yaml"
            if not plans_path.exists():
                return None
            yaml_text = plans_path.read_text(encoding="utf-8")
        try:
            definition = StaticPlanDefinition.from_yaml(
                str(yaml_text) if yaml_text else "",
                bindings=self._bot_bindings.bot_id_by_role if self._bot_bindings else None,
            )
            runtime = StaticPlanRuntime(definition, dict(cfg.get("template_input") or {}))
        except Exception:
            logger.exception(
                "[task][static-plan] runtime init failed task=%s template=%s",
                task_id,
                template_id,
            )
            raise
        logger.debug(
            "[task][static-plan] runtime loaded task=%s template=%s",
            task_id,
            template_id or definition.template_id,
        )
        return runtime
