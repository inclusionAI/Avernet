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
  REQ-8 amendment (2026-10-10): pure read NOW ALSO performs READ-ONLY live
  external session-detail fetches per session-bearing node (BCS channel for
  BCS session keys; BaaS Open API channel for engine-side
  ``agent:main:session:*`` keys — ``_live_session_msgs_for_read``), attaching
  the fresh excerpt ONLY to the returned in-memory trajectory. It still
  performs NO DB write of any kind and NO tc_bot call; per-node fetch failure
  degrades with WARNING to the persisted ``ext_info.session_msgs`` snapshot.

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

Scope boundary: no FastAPI / transport (the router is P5b's other half). The
centralized path keeps its independent trajectory-event stream. Relay reads its
transactional ``task_action_log`` as an authoritative projection fallback so
report/search/recovery/delivery facts cannot disappear from the timeline. The
analyzer is the ONLY bot caller. ``TaskService`` remains a separate service.

Authoritative: ``specs/2026-09-16-task-trajectory-collection-and-analysis/spec.md``
REQ-8 (the ``/trajectory`` endpoint + ``do_analysis`` two-mode), REQ-10 (merged
— no separate ``/analysis``), 决策 #10/#11/#13/#14, plan.md Phase 5.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
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
from agentclaw.community.core.task.domain.models import effective_run_mode
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
from agentclaw.community.core.task.task_context.task_trajectory.trajectory_lifecycle import (
    TaskTrajectoryLifecycle,
)
from agentclaw.community.core.task.task_context.task_trajectory.trajectory_session_support import (
    TrajectorySessionSupport,
)
from agentclaw.community.core.task.task_context.task_trajectory.relay_action_projection import (
    merge_relay_action_log,
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
        OpenApiBotPort,
    )

logger = logging.getLogger("task.trajectory")

# 会话明细探测:取最近 N 条 msg(条数上限),内容**不截断**(去掉了单条/总量
# 字符预算,按需求保留会话原文;N 经 TrajectoryAnalysisConfig.
# running_session_message_limit 配置,默认 50)。
_RUNNING_SESSION_DEFAULT_MSG_LIMIT = 50

# 引擎侧会话 key 判别(读通道分派依据):匹配 → 引擎会话(BaaS Open API 读,
# ``agent:main:session:{uuid}:user:{pid}`` openclaw / ``agent:{bot}:session:...``
# base 引擎 / ``user:{u}:session:{r}:agent:{b}`` claude_code / 裸
# ``session:{uuid}:user:{pid}`` 归一形);不匹配 → BCS 会话键(``bcs-cli:*`` A2A、
# ``{group_id}:initial`` 协作群——BCS GET /sessions/{sid}/messages 读口自管)。
# 形态镜像 openapi_v1 sessions converter 的权威正则(converter_creation.py:25-28;
# 复制不 import,避免 core→adapters 反向依赖)+ claude_code 变体
# (core/bot_management/engines/aicoding/strategy.py:726 同源)。锚定全匹配。
_ENGINE_SESSION_KEY_RE = re.compile(
    r"^(?:(?:agent:[^:]+:)?session:[^:]+:user:[^:]+"
    r"|user:[^:]+:session:[^:]+:agent:[^:]+)$",
    re.IGNORECASE,
)


def _is_engine_session_id(session_id: str) -> bool:
    """``session_id`` 是否为引擎侧会话 key(→ BaaS Open API 读通道)。

    False = BCS 会话键或无法识别的形态 → 走既有 BCS 通道(读失败按既有
    WARNING 降级,不误伤 BCS 新格式)。"""
    return bool(session_id) and _ENGINE_SESSION_KEY_RE.match(session_id) is not None


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


def _relay_timeline_fingerprint(base: str, trajectory: TaskTrajectory) -> str:
    """Extend the persisted-event fingerprint with Relay action-log projection."""
    parts = [base]
    for event in trajectory.timeline:
        parts.append(
            "|".join(
                (
                    event.node_id,
                    event.action_type.value,
                    str(event.attempt),
                    str(event.gmt_create),
                    event.status_from.value if event.status_from else "",
                    event.status_to.value if event.status_to else "",
                    event.action_result,
                )
            )
        )
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()


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
        force_analysis: bool = False,
        session_detail: bool = False,
    ) -> TaskTrajectory:
        """Read the trajectory for ``task_id``; optionally trigger bot analysis
        (``force_analysis=True`` 强制重跑,跳过 timeline 版本号幂等快路径;
        ``session_detail=True`` 纯读时实拉会话明细,默认 false 不携带)。"""
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


class TaskTrajectoryService(
    TrajectorySessionSupport,
    TaskTrajectoryLifecycle,
    TaskTrajectoryServiceProtocol,
):
    """Service facade combining the assembler + analyzer + repo + config into
    the ``get_trajectory(task_id, *, do_analysis)`` entrypoint (REQ-8, P5b).

    SEPARATE from ``TaskService`` (spec invariant — the trajectory service is an
    independent旁路 consumer. Centralized collection remains independent;
    Relay action logs are merged read-only as its durable lifecycle source).

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
        openapi_bot: "OpenApiBotPort | None" = None,
    ) -> None:
        """``graph`` + ``bcs`` + ``openapi_bot`` (the session probe's collaborators)
        are optional lightweight-DI deps: either unbound/None → the corresponding
        read CHANNEL degrades per-node (WARNING), never raising. Full DI wires both
        via try/except-get fallbacks in ``TaskPersistenceModule``.

        ``bcs_bot_tokens`` resolves the RUNNING 节点持有者 bot 的 BCS session_token
        (``BcsBotTokenProvider``):BCS 会话历史读口有参与者级 ACL(401 "valid Human
        identity or Bot token is required")——服务 HMAC 签名不是会话参与者;探测带
        持有者 bot 的 Bearer 才能读到明细。未注入/解析不到 → 裸 HMAC 尝试,401 再
        按既有路径 WARNING 跳过(降级不阻断主分析)。

        ``openapi_bot`` is the ENGINE-session read channel (``BaaS Open API
        ``GET /openapi/v1/sessions/{sid}/messages``)—same ``OpenApiBotPort`` the
        tc_bot analyzer consumes; 鉴权面=派发面(同 api_key 同 validate_policy gate,
        派发成功的 bot 读回必过)。未注入 → 引擎会话节点逐个 WARNING 跳过。"""
        self._assembler = assembler
        self._repo = repo
        self._analyzer = analyzer
        self._config = config
        self._graph = graph
        self._bcs = bcs
        self._bcs_bot_tokens = bcs_bot_tokens
        self._openapi_bot = openapi_bot

    async def get_trajectory(
        self,
        task_id: str,
        *,
        do_analysis: bool = False,
        force_analysis: bool = False,
        session_detail: bool = False,
    ) -> TaskTrajectory:
        """Read the trajectory for ``task_id``; optionally trigger bot analysis.

        ``do_analysis=False`` (default, PURE READ): assembler reads the events
        table and returns ``TaskTrajectory``; ``analysis`` = persisted or
        ``None``; no DB write, no bot call. 纯读默认**不携带会话明细**
        (``session_detail=False``:零外部 GET,响应更轻);``session_detail=True``
        时才对每个带 session_id 的子任务**实拉会话明细**(只读外部服务;
        BCS 键走 BCS 读口,引擎键 ``agent:main:session:*`` 走 BaaS Open API——
        结果只挂返回的内存轨迹对象,不落库);单节点拉取失败 WARNING 降级,
        回退末位事件 ext_info 快照。

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
        # force_analysis=True 隐含执行分析(即便 do_analysis 未传);并对 _do_analysis
        # 说"跳过快路径"——忽略 timeline 版本号比对,强制重跑 bot + 重新回填盖戳。
        if force_analysis:
            do_analysis = True
        if not do_analysis:
            trajectory = self._assembler.assemble(task_id)
            self._merge_relay_actions(trajectory)
            self._attach_node_outputs(trajectory)
            # session_detail=True 才实拉会话明细(REQ-8 不变式保持:纯读绝不写库——
            # merge_last_event_ext_info / backfill_analysis 只在分析路径可达;
            # 纯读实拉结果仅挂返回的内存轨迹对象)。默认 false 零外部 GET,
            # 返回不携带 session_msgs;单节点拉取失败已在 helper 内 WARNING 降级,
            # 节点缺席 fresh map → _attach_session_msgs 自然回退该子任务末位事件
            # 的 ext_info 缓存快照(零额外回退代码)。
            if session_detail:
                records = self._safe_list_records(task_id)
                fresh = await self._live_session_msgs_for_read(task_id)
                self._attach_session_msgs(trajectory, records, fresh)
            # session_detail 与 do_analysis 完全独立:分析路径内部的会话物化/
            # 研判/返回态富化不受该参数影响(见 _do_analysis)。
            return trajectory
        return await self._do_analysis(task_id, force_analysis=force_analysis)

    # ------------------------------------------------------------------
    # do_analysis=True orchestration
    # ------------------------------------------------------------------

    async def _do_analysis(
        self, task_id: str, *, force_analysis: bool = False,
    ) -> TaskTrajectory:
        """Assemble → (非强制且 timeline 版本与持久分析戳一致则直接返回) → build
        ext_info_lookup → analyze → stamp timeline_version → backfill → return。

        ``force_analysis=True``:完全跳过版本号幂等快路径——即使指纹与持久戳一致
        也重新调 bot + 回填盖戳(供 bot 换代/强刷结论场景;语义与清空 head analysis
        等价,但不依赖写库复位)。"""
        _forced = bool(force_analysis)
        # 1. Assemble the timeline (read-side; no DB write beyond the head UPSERT
        #    which preserves existing analysis/gmt_modified).
        trajectory = self._assembler.assemble(task_id)
        relay_actions_merged = self._merge_relay_actions(trajectory)
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
        if fingerprint is not None and relay_actions_merged:
            fingerprint = _relay_timeline_fingerprint(fingerprint, trajectory)

        # 2. Incremental idempotent fast path: head analysis present AND its
        #    persisted ``timeline_version`` equals the freshly computed
        #    fingerprint → the timeline has not changed since the last
        #    analysis; return the persisted analysis WITHOUT calling the bot
        #    (省一次 bot 调用 / 504). Mismatch → the timeline gained events
        #    after the last backfill → re-run the bot and re-stamp. A legacy
        #    analysis persisted before ``timeline_version`` existed carries no
        #    stamp → mismatch → re-analyzes exactly once, then carries the
        #    stamp. ``fingerprint=None`` (records read failed) never matches.
        self._attach_session_msgs(trajectory, records)  # 快路径返回同样带会话消息(展示用)
        if (
            not _forced
            and trajectory.analysis
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
        running_sessions, fresh_session_msgs = await self._collect_session_briefs(
            task_id, records, force_analysis=force_analysis,
        )

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

        # 9b. 返回态富化:本回合实际拉取的 session_msgs 覆盖快照后挂末位事件,
        #     保证 do_analysis=true 返回的 timeline 也能在 HTML 展示每个子任务的
        #     最新会话消息。
        if fresh_session_msgs:
            self._attach_session_msgs(trajectory, records, fresh_session_msgs)

        # 10. Return the TaskTrajectory carrying the fresh analysis. Mutate the
        #    in-memory object (avoids a second DB round-trip); gmt_modified
        #    reflects the backfill moment.
        trajectory.analysis = analysis_json
        trajectory.gmt_modified = int(time.time() * 1000)
        return trajectory

    # ------------------------------------------------------------------
    # 节点当前态读时富化 — timeline 的"每子任务最后一条事件"携带执行产出 + 真实模态
    # (决策 #14 精神:观测旁路,graph 未接线/图读失败 → 跳过,绝不影响轨迹本体)。
    # ------------------------------------------------------------------

    def _merge_relay_actions(self, trajectory: TaskTrajectory) -> bool:
        """Project transactionally persisted Relay action logs on read."""
        if self._graph is None:
            return False
        is_relay_task = getattr(self._graph, "is_relay_task", None)
        if is_relay_task is None:
            return False
        try:
            if not is_relay_task(trajectory.task_id):
                return False
            graph = self._graph.query_task_dashboard(trajectory.task_id)
            self._graph.load_action_logs(graph)
            return bool(merge_relay_action_log(trajectory, graph))
        except Exception as ex:  # noqa: BLE001 - observability must not break reads
            logger.warning(
                "[task][trajectory] Relay action log projection failed task=%s: %s: %s",
                trajectory.task_id,
                type(ex).__name__,
                ex,
            )
            return False

    def _attach_node_outputs(self, trajectory: TaskTrajectory) -> None:
        """把每个子任务(task_id+node_id)的**当前产出**与**真实执行模态**挂到它在
        timeline 中的**最后一条**事件上。

        产出数据经 ``task_execution_graph`` 的查询接口获取
        (``query_task_dashboard`` → ``node.run_info.output``——执行回投写图的
        节点级 dict 产出)。真实模态取 ``effective_run_mode(node)``
        (``run_info.extend_props.actual_run_mode`` 优先,缺失回退 ``run_mode``),
        同源同挂法。倒序扫 timeline 找每个 node 的末位事件(正序时间线的
        "最新一条"),挂 ``ev.output``(浅拷贝与图现场解耦)与
        ``ev.actual_run_mode``。挂载语义:空 ``{}`` / 缺失的产出、无模态均保持
        ``None``(缺字段=无信号,DTO/HTML 不透出)。

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
        run_modes: dict[str, str] = {}
        root_spec = None
        for node in getattr(graph, "tasks", []):
            out = getattr(node.run_info, "output", None)
            if isinstance(out, dict) and out:
                outputs[node.node_id] = out
            # 真实模态:actual_run_mode 优先(执行时交通覆盖),缺失回退 run_mode;
            # 两者皆缺 → 不挂(缺字段=无信号)。
            mode = effective_run_mode(node)
            if mode:
                run_modes[node.node_id] = mode
            if node.node_id == trajectory.task_id:
                root_spec = getattr(node, "task_spec", None)
        if outputs or run_modes:
            last_seen: set[str] = set()
            for ev in reversed(trajectory.timeline):
                node_id = ev.node_id
                if node_id in last_seen:
                    continue  # 该节点的当前态(output/modality)只挂最后一条
                last_seen.add(node_id)
                out = outputs.get(node_id)
                if out:
                    ev.output = dict(out)
                mode = run_modes.get(node_id)
                if mode:
                    ev.actual_run_mode = mode
        self._attach_submit_goal(trajectory, root_spec)

    def _attach_submit_goal(self, trajectory: TaskTrajectory, root_spec) -> None:
        """把 timeline **第一条 submit 事件**的 ``action_input`` 富化为 TaskSpec 的
        goal 提示文本(展示层语义;DB 行仍存 task_spec_digest 指纹,不落库)。

        需求:首条 submit 的 action_input 原是 SHA-256 task_spec_digest,页面上无排查
        价值——读时改为呈现根节点 TaskSpec 的目标(objective + 验收标准)。仅当首条
        事件 action_type=="submit" 且根 spec 存在时替换;其余情况保持原值。观测旁路:
        任何失败静默保持原样(决策 #14 精神)。
        """
        try:
            if not trajectory.timeline or root_spec is None:
                return
            first = trajectory.timeline[0]
            if str(getattr(first.action_type, "value", first.action_type)) != "submit":
                return
            goal = getattr(root_spec, "goal", None)
            if goal is None:
                return
            objective = str(getattr(goal, "objective", "") or "")
            acceptances = [
                str(getattr(a, "description", "") or a)
                for a in (getattr(goal, "acceptances", None) or [])
            ]
            lines = []
            if objective:
                lines.append(f"目标: {objective}")
            if acceptances:
                lines.append("验收: " + " / ".join(a for a in acceptances if a))
            text = "\n".join(lines).strip()
            if text:
                first.action_input = text
        except Exception:  # noqa: BLE001  展示富化失败 → 保留原 digest
            return
