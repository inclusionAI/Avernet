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

* ``force_analysis=True``(**请求大模型的唯一情况**;``do_analysis`` 为兼容
  参数,单独出现即纯读,不校验 bot 配置):
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
  NB: 原"do_analysis 增量幂等快路径"(时间线指纹比对命中免跑 bot)随
  2026-10-15 触发口径改变永久不可达已移除;``timeline_version`` 仅作溯源
  信息盖入 analysis JSON。

Bot failure/timeout → analyzer raises ``TrajectoryAnalysisError``; the service
MUST NOT backfill and re-raises (decision #14 waiver is EMISSION-only; the
on-demand analysis-trigger failure MUST be visible — 504 + no backfill). The
router maps via ``@envelope_errors`` / ``ENVELOPE_ERRORS``.

Scope boundary: no FastAPI / transport (the router is P5b's other half). The
centralized path keeps its independent trajectory-event stream. **Relay
(2026-10-15 验收语义)**:轨迹的 relay 事实 = 上报时逐条落表的 ``trace.steps``
行(``TaskServiceRelayMixin.report`` 写时发射),不再读时投影 action_log——
apply 记录的高层动作属于 dashboard 侧事实,不入轨迹时间线。The
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


def _display_run_mode(node) -> "str | None":
    """轨迹展示用的真实模态(2026-10-16 规则):

    ``run_mode=bbs`` **短路直判 bbs**——BBS 节点的 ``extend_props.actual_run_mode``
    可能残留单 bot 交通过渡值,展示口径一律以图的 bbs 为准;其余模态沿用
    ``effective_run_mode``(``actual_run_mode`` 优先,缺失回退 ``run_mode``)。

    NB:刻意**不改**全局 ``effective_run_mode``——它还驱动 harness 的 BBS 租约
    语义,轨迹展示规则单独收口在此。"""
    runtime = getattr(node, "run_info", None)
    if runtime is not None and str(runtime.run_mode or "").strip() == "bbs":
        return "bbs"
    return effective_run_mode(node)

# 会话明细探测:取最近 N 条 msg(条数上限),内容**不截断**(去掉了单条/总量
# 字符预算,按需求保留会话原文;N 经 TrajectoryAnalysisConfig.
# running_session_message_limit 配置,默认 200)。
_RUNNING_SESSION_DEFAULT_MSG_LIMIT = 200

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


# 主事件判别(2026-10-15 验收语义):relay 轨迹的主序列 = Bot 上报的 trace step,
# 其 action_type 直接采用 step 码——"S 后带数字再带其它"(如 S1_CONTEXT、
# S4A_EXECUTION、S5A_REFRESH_GAPS);组装序即 id 正序(仓储 ORDER BY gmt_create,id)。
_MAIN_EVENT_RE = re.compile(r"^S\d+")

# 子任务结果展示虚拟事件(2026-10-16):读时追加在每个子任务 timeline 末尾,
# 承载 output / session_msgs / actual_run_mode——不落库、无 id,顶层展示位
# (不参与 system_logs 嵌套的子事件归并)。
_RESULT_DISPLAY_ACTION = "task_result_display"
_RESULT_DISPLAY_RESULT = "display"


def _is_main_event(action_type: object) -> bool:
    """顶层主事件(S �码 ``^S\d+``,或 ``task_result_display`` 虚拟展示事件)。"""
    value = str(getattr(action_type, "value", action_type) or "")
    if value == _RESULT_DISPLAY_ACTION:
        return True
    return bool(value) and _MAIN_EVENT_RE.match(value) is not None


def _nest_system_logs(trajectory: TaskTrajectory) -> None:
    """读时把 timeline 整理为"主事件序列 + 嵌套系统事件"两态(不落库)。

    规则(2026-10-15 验收):
    * 主事件 = action_type 匹配 ``^S\\d+`` 的 trace step 行,留在顶层时间线;
    * 各子任务(task_id+node_id)的**其它 action_type 事件**(system 侧:submit /
      reset / dispatch 投递 / transition 等)挂到**距离最近的前一个主事件**的
      ``system_logs`` 列表——子事件 id 必须小于下一主事件 id(组装序即 id 序,
      逐个 append 天然按 id 正序);
    * 子事件的 id 早于该子任务首个主事件的(如任务提交 submit)无前置主,
      **保持平级**;所在子任务没有任何主事件的,该子任务的事件整体保持平级
      (中心化任务的常规形态不受影响——无 S 码即无变换,零重排)。
    """
    if not trajectory.timeline:
        return
    if not any(_is_main_event(e.action_type) for e in trajectory.timeline):
        return  # 无主事件(中心化常规/relay 未上报):零变换
    flat: list[TrajectoryEvent] = []
    last_main_by_node: dict[str, TrajectoryEvent] = {}
    for event in trajectory.timeline:
        if _is_main_event(event.action_type):
            flat.append(event)
            last_main_by_node[event.node_id] = event
            continue
        host = last_main_by_node.get(event.node_id)
        if host is None:
            flat.append(event)  # 无前置主:保持平级,不丢数据
            continue
        if host.system_logs is None:
            host.system_logs = []
        host.system_logs.append(event)
    trajectory.timeline = flat


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
        """Read the trajectory for ``task_id``; bot analysis ONLY on force.

        2026-10-15 语义收紧:**请求大模型(分析 bot)的唯一情况 =
        ``force_analysis=true``**;``do_analysis`` 降级为兼容参数(不再有触发
        效力,也不校验 bot 配置)——``do_analysis=true`` 单独出现即纯读,返回
        已持久化的 ``analysis``。

        纯读(``force_analysis=false``,含 ``do_analysis=true`` 单独出现):
        assembler 读事件表返回 ``TaskTrajectory``;``analysis`` = 持久值或
        ``None``;不写库不调 bot;``session_detail=true`` 时对每个带 session_id
        的子任务实拉会话明细(BCS 键走 BCS 读口,引擎键走 BaaS Open API,只挂
        内存返回,不落库;单节点失败 WARNING 回退末位事件 ext_info 快照)。

        ``force_analysis=true``:组装 → RUNNING 会话物化研判 → 配置的
        ``tc_bot`` 分析 → ``timeline_version`` 盖戳 → ``backfill_analysis``
        (overwrite, 决策 #13)→ 返回携带新 analysis 的同形态轨迹。
        Bot 失败/超时 → ``TrajectoryAnalysisError``(504, 不回填, 决策 #14);
        bot 未配置 → ``TrajectoryAnalysisNotConfiguredError``(503)。
        """
        if not force_analysis:
            trajectory = self._assembler.assemble(task_id)
            outputs, run_modes = self._attach_node_outputs(trajectory)
            # session_detail=true 才实拉会话明细(REQ-8 不变式保持:纯读绝不
            # 写库;结果只挂返回的内存轨迹对象)。默认 false 零外部 GET,
            # 单节点拉取失败 WARNING 降级回退末位事件 ext_info 缓存快照。
            session_msgs = {}
            if session_detail:
                records = self._safe_list_records(task_id)
                fresh = await self._live_session_msgs_for_read(task_id)
                session_msgs = self._collect_session_msgs_by_node(records, fresh)
            # 子任务结果展示虚拟事件(2026-10-16):output/会话明细/真实模态
            # 挂在每个子任务 timeline 最后的 task_result_display 上。
            self._append_result_display_events(trajectory, outputs, run_modes, session_msgs)
            # 最后一步读时嵌套:S 码主序列 + 子事件挂 system_logs(不落库)。
            _nest_system_logs(trajectory)
            return trajectory
        # 大模型唯一触发口:force_analysis(原 do_analysis 增量幂等快路径随
        # 触发口径改变而不可达,已在 _do_analysis 内一并移除)。
        return await self._do_analysis(task_id)

    # ------------------------------------------------------------------
    # do_analysis=True orchestration
    # ------------------------------------------------------------------

    async def _do_analysis(self, task_id: str) -> TaskTrajectory:
        """force_analysis=true 的分析编排:assemble → 会话物化/研判 → 调 bot →
        盖 timeline_version 溯源戳 → backfill → 返回。

        2026-10-15 语义:本方法**仅由 force_analysis=true 触达**,每跑即全量
        重分析——原"do_analysis 增量幂等快路径"(时间线指纹比对命中即免跑 bot)
        随触发口径改变永久不可达,已移除;``timeline_version`` 仍作为**溯源
        信息**盖进 analysis JSON(记录本次分析对应的时间线形态,非幂等键)。
        """

        # 读全部事件行:ext_info_lookup 的数据源(ext_info 不入领域对象,REQ-1)。
        # 读取失败 → WARNING + ext_info 降级为空查找表(分析照常执行)。
        try:
            records = self._repo.list_events_by_task(task_id)
        except Exception as ex:  # noqa: BLE001
            logger.warning(
                "[task][trajectory] list_events_by_task 失败,ext_info 降级 "
                "task=%s: %s: %s",
                task_id, type(ex).__name__, ex,
            )
            records = None
        ext_info_lookup = _build_ext_info_lookup(self._repo, task_id, records=records)
        fingerprint = _timeline_fingerprint(records) if records is not None else None

        # 组装时间线(头行 UPSERT 保留既有 analysis/gmt_modified)。
        trajectory = self._assembler.assemble(task_id)
        outputs, run_modes = self._attach_node_outputs(trajectory)

        # RUNNING 节点会话明细探测(决策 #14 精神:观测旁路,永不抛)——
        # 有节点卡在 RUNNING 时实拉会话明细组 brief,交给分析 bot 判读
        # "工具调用报错 / 执行完不上报结果"等 timeline 看不到的问题。
        running_sessions, fresh_session_msgs = await self._collect_session_briefs(
            task_id, records, force_analysis=True,
        )

        # 解析部署配置的分析 bot_id(决策 #10:非 per-request)。None → 503。
        analysis_bot_id = self._resolve_analysis_bot_id()
        if analysis_bot_id is None:
            raise TrajectoryAnalysisNotConfiguredError(
                "trajectory analysis bot is not configured "
                "(task_trajectory.analysis_bot_id — or analysis_bot_id_pre for the "
                "pre env — missing in user_config); "
                "force_analysis=true requires a deployment-configured bot_id"
            )

        # 调分析 bot(tc_bot;失败/超时/空响应 → TrajectoryAnalysisError=504,
        # 不回填,决策 #14)。
        analysis = await self._analyzer.analyze(
            trajectory,
            ext_info_lookup,
            analysis_type=AnalysisType.TC_BOT,
            analysis_executor=analysis_bot_id,
            running_sessions=running_sessions,
        )

        # 盖 timeline_version 溯源戳(溯源信息;非幂等键)。
        analysis.timeline_version = fingerprint

        # 序列化扁平 TrajectoryAnalysis(P0;中文可读)。
        analysis_json = json.dumps(asdict(analysis), ensure_ascii=False)

        # 回填(overwrite 两表 analysis+gmt_modified — P1b 事务;决策 #13)。
        self._repo.backfill_analysis(task_id, analysis_json)

        # 返回态富化:会话明细归集(本回合实拉覆盖快照)→ 虚拟展示事件 + 嵌套。
        session_msgs = self._collect_session_msgs_by_node(records, fresh_session_msgs)
        self._append_result_display_events(trajectory, outputs, run_modes, session_msgs)
        trajectory.analysis = analysis_json
        trajectory.gmt_modified = int(time.time() * 1000)
        _nest_system_logs(trajectory)
        return trajectory

    # ------------------------------------------------------------------
    # 节点当前态读时富化 — timeline 的"每子任务最后一条事件"携带执行产出 + 真实模态
    # (决策 #14 精神:观测旁路,graph 未接线/图读失败 → 跳过,绝不影响轨迹本体)。
    # ------------------------------------------------------------------

    def _attach_node_outputs(
        self, trajectory: TaskTrajectory
    ) -> "tuple[dict[str, dict], dict[str, str]]":
        """计算各子任务当前产出与真实模态(2026-10-16 起**不再逐事件挂载**:
        结果由 ``_append_result_display_events`` 追加的 ``task_result_display``
        虚拟事件承载,挂各子任务 timeline 的最后)。

        产出经 ``query_task_dashboard`` → ``node.run_info.output``;真实模态取
        ``_display_run_mode``(run_mode=bbs 短路,其余 ``effective_run_mode``)。
        缺素材的节点不出现在返回 map(缺字段=无信号)。旁路:graph 未接线 → 空
        maps;图读失败 → WARNING + 空 maps;不落库、不进指纹。
        """
        if self._graph is None or not trajectory.timeline:
            return {}, {}
        try:
            graph = self._graph.query_task_dashboard(trajectory.task_id)
        except Exception as ex:  # noqa: BLE001  图读失败 → 不富化,轨迹本体照常
            logger.warning(
                "[task][trajectory] 节点产出富化图读失败,跳过 task=%s: %s: %s",
                trajectory.task_id, type(ex).__name__, ex,
            )
            return {}, {}
        outputs: dict[str, dict] = {}
        run_modes: dict[str, str] = {}
        root_spec = None
        for node in getattr(graph, "tasks", []):
            out = getattr(node.run_info, "output", None)
            if isinstance(out, dict) and out:
                outputs[node.node_id] = out
            mode = _display_run_mode(node)
            if mode:
                run_modes[node.node_id] = mode
            if node.node_id == trajectory.task_id:
                root_spec = getattr(node, "task_spec", None)
        self._attach_submit_goal(trajectory, root_spec)
        return outputs, run_modes

    def _append_result_display_events(
        self,
        trajectory: TaskTrajectory,
        outputs: "dict[str, dict]",
        run_modes: "dict[str, str]",
        session_msgs_by_node: "dict[str, list] | None" = None,
    ) -> None:
        """在每个子任务 timeline 的**最后**追加虚拟事件 ``task_result_display``:
        承载该子任务的 output / session_msgs / actual_run_mode(读时富化,不落库、
        无表行 id;gmt 取该子任务真实末位事件 +1ms 保持排序)。

        仅当该子任务有任何展示素材(产出/模态/会话明细任一)时才追加——空节点
        保持干净(缺字段=无信号)。插入发生在 ``_nest_system_logs`` 之前,虚拟事件
        按**顶层主事件**对待(``_is_main_event``),不被嵌套收编。
        """
        if not trajectory.timeline:
            return
        msgs_by_node = session_msgs_by_node or {}
        last_idx_by_node: dict[str, int] = {}
        for idx, ev in enumerate(trajectory.timeline):
            last_idx_by_node[ev.node_id] = idx
        inserts: list[tuple[int, TrajectoryEvent]] = []
        for node_id, last_idx in last_idx_by_node.items():
            out = outputs.get(node_id)
            mode = run_modes.get(node_id)
            msgs = msgs_by_node.get(node_id)
            if not out and not mode and not msgs:
                continue
            last = trajectory.timeline[last_idx]
            gmt = int(last.gmt_create) + 1
            inserts.append((last_idx + 1, TrajectoryEvent(
                task_id=trajectory.task_id,
                node_id=node_id,
                action_type=_RESULT_DISPLAY_ACTION,
                action_result=_RESULT_DISPLAY_RESULT,
                attempt=0,
                gmt_create=gmt,
                gmt_modified=gmt,
                output=dict(out) if isinstance(out, dict) else out,
                session_msgs=list(msgs) if msgs else None,
                actual_run_mode=mode,
            )))
        for pos, ev in sorted(inserts, key=lambda pair: pair[0], reverse=True):
            trajectory.timeline.insert(pos, ev)

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
