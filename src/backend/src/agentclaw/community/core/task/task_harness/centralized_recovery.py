"""Centralized recovery operations bound to TaskHarness."""

from __future__ import annotations

from agentclaw.community.core.task.task_harness.harness import (
    effective_pending_timeout_ms,
    effective_sla_threshold_ms,
)

from agentclaw.community.core.task.task_runner.centralized_support import (
    NodeAction,
    ReasonCatalog,
    Status,
    TaskGraphPatch,
    TaskNode,
    TaskNodePatch,
    _is_stale_dispatching,
    _now_ms,
    _read_dispatch_side_data,
    asyncio,
    effective_run_mode,
    logger,
    replace,
    threading,
)


async def redrive(self, task_id: str) -> None:
    """Recovery resume entrypoint: re-dispatch pending leaf nodes of a
    hydrated non-terminal task after an instance restart / rolling deploy.

    Mirrors the dispatch tail of ``on_execute`` but starts from the
    already-hydrated graph (``query_task_dashboard`` hydrates from the shared
    store on cache miss): collect未派发 PENDING 叶 → dispatch → start_run.
    Only non-terminal runtime statuses are recoverable (the worker filters),
    and terminal graphs freeze immediately. Idempotent: ``_prepare_into``
    skips nodes already ``dispatching`` and the status machine guards repeats.
    """
    if self._is_external_managed_task(task_id):
        logger.info(
            "[task][redrive] task=%s external-managed, skip Avernet redrive", task_id
        )
        return
    if self._is_graph_terminal(task_id):
        logger.info("[task][redrive] task=%s 图已终态,冻结重投", task_id)
        return
    side: list[tuple] = []
    with self._lock_for(task_id):
        graph = self._graph.query_task_dashboard(task_id)
        logger.info(
            "[task][redrive] task=%s status=%s resume dispatch",
            task_id,
            graph.status.value,
        )
        # redrive unstick:清崩溃在途遗留的陈旧 dispatching=True(超阈值的飞行态),否则
        # _prepare_into/harness 永久跳过 → 节点卡死 PENDING 无法重派。新鲜在途(dispatching_at
        # < 阈值)不动——redrive 可在本实例正在驱动时被周期 recovery 触发,盲清会与在途 start_run 双派发。
        for _rn in graph.tasks:
            if _rn.status == Status.PENDING and _is_stale_dispatching(_rn):
                logger.info(
                    "[task][redrive] task=%s node=%s 清陈旧飞行态 dispatching(崩溃遗留)→可重派",
                    task_id,
                    _rn.node_id,
                )
                self._report_node_patch(
                    TaskNodePatch(
                        task_id=task_id,
                        node_id=_rn.node_id,
                        extend_props_patch={
                            "dispatching": None,
                            "dispatching_at": None,
                        },
                    )
                )
        await self._prepare_into(task_id, side)
    await self._drain(task_id, side)


def _reset_action_result(self, exec_error: str) -> str:
    """Map the ``_on_harness_collect`` ``exec_error`` trigger to a RESET
    ``action_result`` category (REQ-4).

    ``external_harness`` is the sentinel ``on_harness`` emits when a harness
    poll patch has no ``exec_error``; in prod the ONLY such patch is the
    RUNNING SLA-timeout reset (pinned by
    ``test_only_sla_timeout_harness_patch_omits_exec_error``), so the mapping
    ``external_harness → sla_timeout`` depends on that invariant — if a
    future harness patch omits ``exec_error`` for a non-SLA reason, revisit
    this mapping. Any other non-canonical string (e.g. a poller ``exec_error``
    arriving via ``on_report``) is treated as an execution-failure retry
    (``exec_failed_retry``).
    """
    if exec_error == "pending_dispatch_stuck":
        return "pending_dispatch_stuck"
    if exec_error == "bbs_lease_expired":
        return "bbs_lease_expired"
    if exec_error in ("exec_failed_retry", "sla_timeout"):
        return exec_error
    if exec_error == "external_harness":
        return "sla_timeout"
    return "exec_failed_retry"


def _reset_sla_threshold_ms(
    self, task_id: str, node: "TaskNode | None", action_result: str
) -> int | None:
    """Effective SLA threshold (ms) for a RESET trajectory event (REQ-4).

    Timeout-class triggers only: ``sla_timeout`` → RUNNING SLA (600 / 900 s
    by ``run_mode``, overridable via ``execution_config["SLA_TIMEOUT"]``);
    ``pending_dispatch_stuck`` → pending timeout (180 s, overridable via
    ``execution_config["PENDING_TIMEOUT"]``). Non-timeout triggers
    (``exec_failed_retry`` / ``bbs_lease_expired`` / ``harness_max``) →
    ``None`` (short-circuited before the ``_execution_config`` graph read,
    so non-timeout fires pay no threshold read). Defensive: any read
    failure → ``None`` (never breaks the gate). Mirrors
    ``harness._sla_timeout`` / ``_pending_timeout`` for the default-
    constructed harness; keep in sync if those change.
    """
    if action_result not in ("sla_timeout", "pending_dispatch_stuck"):
        return None
    try:
        cfg = self._graph._execution_config(task_id)
        if action_result == "pending_dispatch_stuck":
            return effective_pending_timeout_ms(cfg)
        return effective_sla_threshold_ms(cfg, effective_run_mode(node))
    except Exception:  # noqa: BLE001 defensive read — never break the gate
        return None


def _reset_elapsed_ms(self, node: "TaskNode | None") -> int | None:
    """``elapsed_ms = now_ms - run_info.start_time`` (int-ms epoch, REQ-4).

    ``None`` when the node never started (e.g. a fresh undispatched PENDING
    node whose dwell baseline lives in the harness's internal
    ``_pending_seen_at`` rather than on ``run_info.start_time``);
    defensive → ``None``.
    """
    try:
        st = node.run_info.start_time if node is not None else None
        if st is None:
            return None
        return _now_ms() - int(st)
    except Exception:  # noqa: BLE001 defensive
        return None


def _emit_reset_trajectory(
    self,
    task_id: str,
    node_id: str,
    *,
    action_result: str,
    node: "TaskNode | None",
    attempts_seen: int,
    status_from: Status | None = None,
    status_to: Status | None = None,
) -> None:
    """Fire one ``TrajectoryActionType.RESET`` trajectory event (REQ-4).

    Additive to ``_log_action(NodeAction.RESET, ...)``; never raises (the
    emitter swallows + logs WARNING, 决策 #14). ``ext_info.trigger`` echoes
    ``action_result`` (the normalized category) so the row is self-
    describing; ``ext_info.{elapsed_ms, sla_threshold_ms, attempts_seen}``
    carry the failure_reason-derivable material (elapsed/threshold for
    ``sla_timeout`` / ``pending_dispatch_stuck``).
    """
    threshold = self._reset_sla_threshold_ms(task_id, node, action_result)
    elapsed = self._reset_elapsed_ms(node)
    ext_info = {
        "trigger": action_result,
        "elapsed_ms": elapsed,
        "sla_threshold_ms": threshold,
        "attempts_seen": attempts_seen,
    }
    self._log_trajectory(
        task_id,
        node_id,
        "reset",  # TrajectoryActionType.RESET.value(TYPE_CHECKING-only enum; emitter accepts str)
        action_result=action_result,
        action_input=None,
        ext_info=ext_info,
        status_from=status_from,
        status_to=status_to,
        attempt=attempts_seen,
    )


async def _on_harness_collect(
    self, task_id: str, node_id: str, exec_error: str, side: list[tuple]
) -> None:
    """harness 重试仅处理执行失败(exec_error:网络抖动、超时、崩溃、poll 耗尽)。
    验收未通过不进入此路径,由图服务记录为 DONE 并保留验收结论。
    """
    graph = self._graph.query_task_dashboard(task_id)
    node = next((n for n in graph.tasks if n.node_id == node_id), None)
    if node is None:
        return
    retries = int(node.run_info.extend_props.get("harness_retries", 0))
    max_harness = self._max_harness(task_id)
    if retries >= max_harness:
        self._report_node_patch(
            TaskNodePatch(
                task_id=task_id,
                node_id=node_id,
                extend_props_patch={"last_exec_error": exec_error},
            )
        )
        logger.warning(
            "[task][on_harness] task=%s node=%s 达 MAX_HARNESS(%d)→HUNG(retries=%d)",
            task_id,
            node_id,
            max_harness,
            retries,
        )
        # ``node`` 是图内活引用,``_hung_and_escalate`` 会把 ``node.status``
        # 改写为 HUNG —— 翻态前快照 status_from(与既有 site 的
        # ``_prev = node.status`` 同模式)。
        _prev_status = node.status
        self._hung_and_escalate(task_id, node_id, "exec_stuck")
        # 轨迹旁路:RESET(harness_max — 重试耗尽→HUNG,无复位)。与既有
        # ``_log_action`` RESET 路径并列的独立直插轨迹事件(action_result
        # 标注"重试上限已达"供 analyzer 末事件派生"重试耗尽"根因);决策 #14
        # 吞+WARNING 在 emitter 层。status_to=None:此处未复位,HUNG 由
        # ``_hung_and_escalate`` 内的 TRANSITION 动作记录。
        self._emit_reset_trajectory(
            task_id,
            node_id,
            action_result="harness_max",
            node=node,
            attempts_seen=retries,
            status_from=_prev_status,
            status_to=None,
        )
        return
    retries += 1
    logger.info(
        "[task][on_harness] task=%s node=%s reason=%s retries=%d/%d",
        task_id,
        node_id,
        exec_error,
        retries,
        max_harness,
    )
    self._report_node_patch(
        TaskNodePatch(
            task_id=task_id,
            node_id=node_id,
            extend_props_patch={
                "harness_retries": retries,
                "last_exec_error": exec_error,
            },
        )
    )
    # 复位到 PENDING 重新派发执行:FAILED/RUNNING→PENDING;PENDING 派发卡住(搜推无响应/派发失败)清
    # dispatch_error 让 prepare 重新派发(harness owns 重试计数+HUNG 上限,正常 cycle 跳过 dispatch_error 节点)
    if node.status in {Status.FAILED, Status.RUNNING}:
        _prev = node.status
        self._report_node_patch(
            TaskNodePatch(task_id=task_id, node_id=node_id, status=Status.PENDING)
        )
        # 动作历史:RESET(harness 重新派发执行重试)
        self._log_action(
            task_id,
            node_id,
            NodeAction.RESET,
            {
                "reason": exec_error or "failed_retry",
                "prev_status": _prev.value,
                "harness_retries_after": retries,
            },
            attempt=retries,
            status_from=_prev,
            status_to=Status.PENDING,
        )
        # 轨迹旁路:RESET —— 与既有 ``_log_action(NodeAction.RESET, ...)``
        # 同闸门位置、独立直插 ``task_trajectory_events``(additive,非替换)。
        # action_result 按 trigger 映射(REQ-4):``external_harness``→sla_timeout、
        # ``exec_failed_retry``/``bbs_lease_expired``/poller exec_error→对应类;
        # ext_info 携带 {trigger, elapsed_ms, sla_threshold_ms, attempts_seen}
        # 供 analyzer 末事件派生"在 N ms 触发,阈值 M ms"。
        self._emit_reset_trajectory(
            task_id,
            node_id,
            action_result=self._reset_action_result(exec_error),
            node=node,
            attempts_seen=retries,
            status_from=_prev,
            status_to=Status.PENDING,
        )
    elif node.status == Status.PENDING and node.run_info.extend_props.get(
        "dispatch_error"
    ):
        self._report_node_patch(
            TaskNodePatch(
                task_id=task_id,
                node_id=node_id,
                extend_props_patch={"dispatch_error": None},
            )
        )
        # 轨迹旁路:RESET(pending_dispatch_stuck — PENDING 派发卡住清
        # dispatch_error 重搜推;此 elif 路径无 ``_log_action(RESET)``,故
        # 轨迹事件为唯一 RESET 记录)。sla_threshold_ms 取 pending_timeout
        # (180s,*1000 ms,execution_config 可覆盖);elapsed_ms 为停留时长
        # (run_info.start_time 缺省时为 None — 未派发 PENDING 的 dwell 基线在
        # harness 内部 _pending_seen_at,本闸门不可达)。
        self._emit_reset_trajectory(
            task_id,
            node_id,
            action_result="pending_dispatch_stuck",
            node=node,
            attempts_seen=retries,
            status_from=Status.PENDING,
            status_to=Status.PENDING,
        )
    # static plan:harness 重派走 static prepare(只派发 readiness.ready 的绑定 bot),
    # 不进搜推/claim_join,避免依赖未满足的节点(strategy_approval/implementation)被提前搜推
    # 派给 catalog 命中的错误 bot(如 default:35983)。
    _static_runtime = self._static_runtime(task_id)
    if _static_runtime is not None:
        await self._prepare_static(task_id, _static_runtime, side)
    else:
        await self._prepare_into(task_id, side)


async def on_miss(self, patch: TaskNodePatch) -> None:
    """dispatcher MISS(搜推未匹配执行者)→深度闸门:
    depth>=MAX → HUNG 升 BBS(拆不动,无 bot);depth<MAX → mark_planning + plan(target=miss 叶)拆细:
    有子→add(父置 PLANNING)+dispatch;空+has_gap=F→gap 闭不推进(罕见);空+has_gap=T→HUNG 升 BBS。
    MISS 不进 harness(无 bot 无可重试执行体)。"""
    if self._is_external_managed_task(patch.task_id):
        logger.info(
            "[task][on_miss] task=%s external-managed, skip dynamic planning",
            patch.task_id,
        )
        return
    if self._is_graph_terminal(patch.task_id):
        logger.info("[task][on_miss] task=%s 图已终态,冻结 MISS 推进", patch.task_id)
        return
    side: list[tuple] = []
    with self._lock_for(patch.task_id):
        depth = self._graph._node_depth(patch.task_id, patch.node_id)
        cfg = self._graph._execution_config(patch.task_id)
        max_depth = cfg["MAX_DEPTH"]
        # 动作历史:DISPATCH(MISS 搜推未命中执行者)
        _miss_reason = ""
        _ep = patch.extend_props_patch or {}
        if isinstance(_ep.get("miss_events"), list) and _ep["miss_events"]:
            _miss_reason = str(_ep["miss_events"][0])
        self._log_action(
            patch.task_id,
            patch.node_id,
            NodeAction.DISPATCH,
            {
                "outcome": "MISS",
                "miss_reason": _miss_reason,
                "depth": depth,
                "max_depth": max_depth,
            },
            status_from=Status.PENDING,
            status_to=Status.PENDING,
        )
        # 轨迹旁路:DISPATCH(MISS) —— 与 ``_log_action`` 同闸门位置、独立直插
        # ``task_trajectory_events``。action_input=None(MISS 无 dispatch target);
        # ext_info 携带 ``_dispatch_rationale`` (经 dispatcher 写入节点的 carrier,
        # 防御读取:缺/异常 → None),strategy_name/decision_mode/candidates/join_dropped
        # 全在 ext_info(REQ-1 action_input≠候选,候选入 ext_info;决策 #14 吞+WARNING)。
        # ``patch`` 是 TaskNodePatch(非 node),需查图:经 ``_read_dispatch_side_data``
        # **一次**查询同时取 ext_info 与 attempt(HIT_SINGLE/HIT_MULTI 从 in-scope
        # ``cur``/``node`` inline,无额外查询 — 非对称:只有 MISS 走此 helper)。
        _miss_ext_info, _miss_attempt = _read_dispatch_side_data(
            self._graph, patch.task_id, patch.node_id
        )
        self._log_trajectory(
            patch.task_id,
            patch.node_id,
            "dispatch",  # TrajectoryActionType.DISPATCH.value (TYPE_CHECKING-only enum)
            action_result="miss",
            action_input=None,
            ext_info=_miss_ext_info,
            status_from=Status.PENDING,
            status_to=Status.PENDING,
            attempt=_miss_attempt,
        )
        if depth >= max_depth:
            logger.info(
                "[task][on_miss] task=%s node=%s depth=%d/%d 拆不动→HUNG",
                patch.task_id,
                patch.node_id,
                depth,
                max_depth,
            )
            self._hung_and_escalate(
                patch.task_id, patch.node_id, "miss_depth_exhausted"
            )
            await self._drain(patch.task_id, side)
            return
        self._mark_planning(patch.task_id, patch.node_id)
        graph = self._graph.query_task_dashboard(patch.task_id)
        pr = await self._plan_with_retry(
            patch.task_id, graph, target_node_id=patch.node_id
        )
        logger.info(
            "[task][on_miss] task=%s node=%s depth=%d/%d plan 产 %d 子 has_gap=%s",
            patch.task_id,
            patch.node_id,
            depth,
            max_depth,
            len(pr.children),
            pr.has_gap,
        )
        if pr.children:
            self._report_add_nodes(pr.children, patch.node_id)
            await self._prepare_into(patch.task_id, side)
        elif not pr.has_gap:
            pass
        else:
            self._hung_and_escalate(patch.task_id, patch.node_id, "miss_no_decompose")
    await self._drain(patch.task_id, side)


async def on_harness(self, patch: TaskNodePatch) -> None:
    """Harness 旁路入口:exec_error 语义(超时/崩溃/FAILED 巡检)→ 复用 _on_harness_collect 重新派发重试/上限 HUNG。"""
    if self._is_external_managed_task(patch.task_id):
        logger.info(
            "[task][on_harness] task=%s external-managed, skip Avernet retry",
            patch.task_id,
        )
        return
    if self._is_graph_terminal(patch.task_id):
        logger.info(
            "[task][on_harness] task=%s 图已终态,冻结 harness 推进", patch.task_id
        )
        return
    if self._static_runtime(patch.task_id) is not None:
        # 固定 plan 任务:真实上报兜底由 _static_auto_report(默认 80s mock PASS→SUCCESS)承担,
        # V2 relay 节点下发为纯交接正文(不含 {success,data,gaps} poller 协议),bot 常回自然语言
        # → poller 误判 exec_error;若仍走 harness 重投×MAX_HARNESS→HUNG,会在 80s fallback 之前就把
        # 节点 HUNG,80s 兜底因 status!=RUNNING 而跳过,致单节点挂死、整流程不往下走。
        # 此处对固定 plan 跳过 harness 重投/HUNG,把恢复交给 static fallback:真实回投先到则自跳过,
        # 否则 80s 由 mock 推进 DONE,流程不卡(真实派发已完成,不重复派发)。
        logger.info(
            "[task][on_harness] task=%s node=%s 固定 plan,由 static fallback 兜底,跳过 harness 重投/HUNG",
            patch.task_id,
            patch.node_id,
        )
        return
    side: list[tuple] = []
    with self._lock_for(patch.task_id):
        await self._on_harness_collect(
            patch.task_id,
            patch.node_id,
            patch.exec_error or "external_harness",
            side,
        )
    await self._drain(patch.task_id, side)


def _sync_graph_status_to_root(self, task_id: str) -> None:
    """终态镜像:graph.status := root.status(仅终态 DONE/HUNG/FAILED/CANCELLED)。

    不变量:graph.status 是 root.status 的同步镜像——root 是什么终态 graph 就什么终态,
    graph 不脱离 root 独立翻终态。root 非终态(PENDING/PLANNING/RUNNING)时不动 graph 的
    RUNNING 进行态(建图/中间态不镜像)。BBS 可恢复态(root HUNG 但等接力)由 _maybe_propagate_hung
    自管,不走本方法。"""
    root = self._root(task_id)
    if root is None or root.status not in {
        Status.DONE,
        Status.SUCCESS,
        Status.HUNG,
        Status.FAILED,
        Status.CANCELLED,
    }:
        return
    g = self._graph.query_task_dashboard(task_id)
    if g.status == root.status:
        return
    self._report_graph_patch(task_id, TaskGraphPatch(status=root.status))


def _bump_loop_round(self, task_id: str) -> None:
    """图级 loop_round 升 BBS 计次(先判后+1):当前 loop_round>=MAX_LOOP → root HUNG(loop_exhausted)
    + graph 终态镜像 HUNG(硬停);否则 loop_round+1。MAX_LOOP=N 允许 N 次升 BBS 接力,第 N+1 次撞顶。

    终态镜像:不再 graph 独立写 HUNG——先置 root HUNG(loop_exhausted),再 _sync_graph_status_to_root
    镜像 graph(保证 graph.status≡root.status)。"""
    graph = self._graph.query_task_dashboard(task_id)
    max_loop = self._graph._execution_config(task_id)["MAX_LOOP"]
    if graph.loop_round >= max_loop:
        root = self._root(task_id)
        if root is not None and root.status != Status.HUNG:
            self._report_node_patch(
                TaskNodePatch(
                    task_id=task_id,
                    node_id=root.node_id,
                    status=Status.HUNG,
                    extend_props_patch={"hung_reason": "loop_exhausted"},
                )
            )
        # graph 终态镜像 root(HUNG)+ 保留图级 loop_exhausted 诊断标记
        self._sync_graph_status_to_root(task_id)
        self._report_graph_patch(
            task_id,
            TaskGraphPatch(extend_props_patch={"hung_reason": "loop_exhausted"}),
        )
        logger.warning(
            "[task][loop_round] task=%s 达 MAX_LOOP(%d)→root/graph HUNG(loop_exhausted)",
            task_id,
            max_loop,
        )
        return
    self._report_graph_patch(task_id, TaskGraphPatch(loop_round_increment=1))


def _escalate_hung(self, task_id: str, node_id: str, hung_reason: str) -> None:
    """传播节点 HUNG 的影响,但不在节点级消耗根 BBS 轮次。

    ``_maybe_propagate_hung`` 负责判断阻塞是否已扩散到根;只有根节点确认进入
    BBS 的分支才设置 ``bbs_mode``、递增 ``loop_round`` 并经 ``start_run`` 调度。
    **不置节点态**——调用方须保证节点已 HUNG。乙' a+R1:验收 FAIL 节点已由
    on_report 折叠直驱 HUNG,故 _on_fail_collect 直接调用本方法;其余 HUNG
    (miss/harness/plan_round/gap_no_progress)经 _hung_and_escalate 写 HUNG 后复用本方法。
    """
    # 节点级 HUNG 只做影响传播;根级 BBS 计数在 _maybe_propagate_hung 收口。
    self._maybe_propagate_hung(task_id, node_id, hung_reason)


def _hung_and_escalate(self, task_id: str, node_id: str, hung_reason: str) -> None:
    """节点置 HUNG + 升级传播(锁内同步)。乙' a+R1:验收 FAIL 不再经此(_on_fail_collect 节点已折叠
    HUNG,直接 _escalate_hung);其余 HUNG 仍经此一次写 HUNG。"""
    _prev = next(
        (
            n.status
            for n in self._graph.query_task_dashboard(task_id).tasks
            if n.node_id == node_id
        ),
        None,
    )
    self._report_node_patch(
        TaskNodePatch(
            task_id=task_id,
            node_id=node_id,
            status=Status.HUNG,
            extend_props_patch={"hung_reason": hung_reason},
        )
    )
    # 动作历史:TRANSITION(节点 HUNG)
    self._log_action(
        task_id,
        node_id,
        NodeAction.TRANSITION,
        {"reason": hung_reason, "to": "HUNG"},
        status_from=_prev,
        status_to=Status.HUNG,
    )
    # 轨迹旁路:TRANSITION(节点 → HUNG)—— additive 独立直插。HUNG 是终态错误
    # (需人介入),error_type=HUNG + error_msg=hung_reason 供 analyzer 末事件
    # bullet 4 派生 "hung: {reason}";action_input=null(REQ-1);ext_info 携带
    # reason。决策 #14:仅发射被吞(emitter 内 try/except+WARNING),上面的
    # status=HUNG 翻转 + 后续 _escalate_hung 均不在 swallow 内。
    self._log_trajectory(
        task_id,
        node_id,
        "transition",  # TrajectoryActionType.TRANSITION.value
        action_result=self._transition_action_result(Status.HUNG),
        action_input=None,
        error_type=ReasonCatalog.HUNG,
        error_msg=hung_reason,
        ext_info={"reason": hung_reason},
        status_from=_prev,
        status_to=Status.HUNG,
        attempt=0,
    )
    logger.info(
        "[task][hung] task=%s node=%s reason=%s → 向上评估根级 BBS",
        task_id,
        node_id,
        hung_reason,
    )
    self._escalate_hung(task_id, node_id, hung_reason)


def _on_bg_done(self, bg: object) -> None:
    """后台任务完成:脱离跟踪集 + 异常/取消可见(不抛,不阻塞 on_*)。"""
    self._bg_tasks.discard(bg)
    cancelled = getattr(bg, "cancelled", lambda: False)()
    if cancelled:
        logger.warning(
            "[task][engine] background task cancelled task=%s",
            getattr(bg, "_bbs_task_id", ""),
        )
        return
    exc = getattr(bg, "exception", lambda: None)()
    if exc is not None:
        logger.error("[task][engine] background task 异常: %s", exc, exc_info=exc)
        return
    result = getattr(bg, "result", lambda: None)()
    if isinstance(result, list) and any(item is not True for item in result):
        logger.error(
            "[task][engine] background start_run 投递失败 task=%s results=%s",
            getattr(bg, "_bbs_task_id", ""),
            result,
        )


def _ensure_bbs_loop(self) -> asyncio.AbstractEventLoop:
    """Return an engine-owned loop that outlives Harness' temporary loop.

    Harness invokes ``on_harness`` through ``asyncio.run``. BBS is a
    minutes-long workflow, so scheduling it on that loop makes it a child of
    a short-lived request and silently cancels it when Harness returns.
    """
    with self._bbs_loop_guard:
        if self._bbs_loop is not None and self._bbs_loop.is_running():
            return self._bbs_loop
        self._bbs_loop_ready.clear()

        def _run() -> None:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            with self._bbs_loop_guard:
                self._bbs_loop = loop
                self._bbs_loop_thread = threading.current_thread()
                self._bbs_loop_ready.set()
            loop.run_forever()

        self._bbs_loop_thread = threading.Thread(
            target=_run,
            daemon=True,
            name="task-bbs-loop",
        )
        self._bbs_loop_thread.start()

    if not self._bbs_loop_ready.wait(timeout=5.0):
        raise RuntimeError("BBS background event loop failed to start")
    loop = self._bbs_loop
    if loop is None or not loop.is_running():
        raise RuntimeError("BBS background event loop is not running")
    return loop


def _on_auto_report_done(self, t: "asyncio.Task") -> None:
    """静态自驱 on_report 后台任务完成:脱离跟踪集 + 异常可见。"""
    self._bg_tasks.discard(t)
    if t.cancelled():
        return
    exc = t.exception()
    if exc is not None:
        logger.error(
            "[task][static-plan] auto-report bg task 异常: %s", exc, exc_info=exc
        )


def _reset_root_plan_round(self, task_id: str) -> None:
    """升 BBS 收口时将根节点 plan_round 重置为 loop_round:on_pass 计数为"先判后+1"
    (plan_round 现值<MAX 产子并 +1,达 MAX 撞顶),故从 plan_round=loop_round 起产子 = MAX-loop_round
    (plan_round loop..MAX-1 产,MAX 撞),逐轮递减;MAX_LOOP 仍作总轮次兜底。避免 plan_round 残留撞顶值
    → BBS 接力 on_pass 回根立即再撞 plan_round_exhausted、重新规划白做。"""
    root = self._root(task_id)
    if root is None:
        return
    loop_round = self._graph.query_task_dashboard(task_id).loop_round
    self._report_node_patch(
        TaskNodePatch(
            task_id=task_id,
            node_id=root.node_id,
            extend_props_patch={"plan_round": loop_round},
        )
    )


def _schedule_bbs_notify(self, task_id: str, execution_graph) -> None:
    """可恢复拦截点(spec §5):fire-and-forget ``runner.start_run([bbs_node])``。

    命中根 BBS 可恢复态(bbs_mode + 未 claim)时调用——主动 bid→select→claim→
    dispatch 给 claim-enabled bot。上层只使用统一 start_run seam，不感知 BBS 专用方法。
    不持锁、不阻塞 ``on_*``/``_maybe_propagate_hung`` 汇报路径:``asyncio.create_task``
    调度后台协程,异常经 ``_on_bg_done`` 记 log。端口不全(无 runner/bot/bcs,如单测 stub)→ 静默跳过。"""
    logger.info("[task][bbs_mode], begin schedule bbs notify, task_id=%s", task_id)
    if not self._runner:
        logger.info("[task][bbs_mode], _runner is none, skip, task_id=%s", task_id)
        return
    root = next(
        (node for node in execution_graph.tasks if node.node_id == task_id),
        None,
    )
    if root is None:
        logger.error(
            "[task][bbs_mode] root missing, skip start_run task_id=%s", task_id
        )
        return
    # BBS 是本次执行请求的目标模态，不改写图中根节点原有 run_mode/assignee。
    # 具体 BBS 语义由执行 adapter 隐藏，Runner 只接收普通 TaskNode。
    bbs_node = replace(
        root,
        run_info=replace(
            root.run_info,
            run_mode="bbs",
            output=dict(root.run_info.output),
            extend_props=dict(root.run_info.extend_props),
            action_log=list(root.run_info.action_log),
        ),
    )
    loop = self._ensure_bbs_loop()
    coroutine = self._runner.start_run([bbs_node])
    try:
        bg = asyncio.run_coroutine_threadsafe(coroutine, loop)
    except Exception:
        coroutine.close()
        raise
    # concurrent.futures.Future is intentionally tracked here. It belongs
    # to the durable BBS loop, not the caller's Harness asyncio loop.
    bg._bbs_task_id = task_id  # type: ignore[attr-defined]
    self._bg_tasks.add(bg)
    bg.add_done_callback(self._on_bg_done)
    logger.info(
        "[task][engine] task=%s 升BBS可恢复态→提交 durable BBS loop thread=%s",
        task_id,
        self._bbs_loop_thread.name if self._bbs_loop_thread else "unknown",
    )


def _enter_root_bbs(self, task_id: str, execution_graph) -> bool:
    """Enter one root-level BBS round and schedule the relay if budget remains.

    ``loop_round`` counts root-to-BBS escalations only. A node-level HUNG caller
    reaches this helper only after ``_maybe_propagate_hung`` has confirmed that
    the root is blocked.
    """
    if execution_graph.status == Status.HUNG:
        return False
    root = self._root(task_id)
    if root is None or root.run_info.extend_props.get("bbs_owner"):
        return False
    self._report_graph_patch(
        task_id, TaskGraphPatch(extend_props_patch={"bbs_mode": True})
    )
    self._bump_loop_round(task_id)
    current = self._graph.query_task_dashboard(task_id)
    if current.status == Status.HUNG:
        return False
    self._reset_root_plan_round(task_id)
    self._schedule_bbs_notify(task_id, current)
    return True


def _maybe_propagate_hung(
    self, task_id: str, node_id: str, hung_reason: str = ""
) -> None:
    """自 node 往上:若父的子全终态且含 HUNG → 父 HUNG(不计额外 loop_round,纯冒泡)→ 继续上行。
    到根 → 图终态收口(HUNG)。若图已 HUNG(loop_exhausted 等已收口)→ 不覆盖 hung_reason。

    **BBS 可恢复态(spec §10.5,调度优化)**:只要阻塞已经传播到根节点(任一 ``hung_reason``:
    ``miss_depth_exhausted``/``root_gap_no_decompose``/``gap_no_progress``/
    ``plan_round_exhausted``/``exec_stuck``/``child_hung`` 等),且根未被 claim,即进入根级 BBS
    可恢复态(经 ``start_run`` 派发 BBS);``loop_exhausted`` 由 ``_bump_loop_round`` 置根/图 HUNG,
    被上方 ``g.status==HUNG`` 短路拦截、不再调度,保留反失控兜底。在途 BBS(``bbs_owner``
    非空)亦跳过,不重复派发。``loop_round`` 只在根确认进入 BBS 时递增。
    """
    # 任意 HUNG 都必须沿依赖链冒泡到根。根 HUNG 后再由统一入口进入 BBS，
    # 不允许 miss_depth_exhausted 之类的特殊分支把根留在 PLANNING/EXECUTING。
    cur = node_id
    while True:
        parent = self._graph.get_parent_task(task_id, cur)
        if parent is None:
            # cur 是根 → 图级收口(根 HUNG → 图 HUNG);不覆盖已设的图级 hung_reason。
            # 根 HUNG 且图未进入硬终态时,由根级入口统一计数并调度 BBS。
            root = self._root(task_id)
            if root is not None and root.node_id == cur and root.status == Status.HUNG:
                g = self._graph.query_task_dashboard(task_id)
                if g.status == Status.HUNG:
                    return
                if not g.extend_props.get("bbs_owner"):
                    # 根 HUNG 才进入 BBS;此处统一递增根级 loop_round。
                    logger.info(
                        "[task][hung-propagate] task=%s 根 HUNG(reason=%s)→升 BBS 可恢复态",
                        task_id,
                        hung_reason,
                    )
                    self._enter_root_bbs(task_id, g)
                    return
                # 无 bbs_mode 或已被 BBS claim → 硬 HUNG 收口(不再调度,等在途 BBS 回投)
                # 终态镜像:root 已 HUNG → graph 经单一同步点镜像 HUNG;保留 root_stuck 诊断
                self._sync_graph_status_to_root(task_id)
                self._report_graph_patch(
                    task_id,
                    TaskGraphPatch(extend_props_patch={"hung_reason": "root_stuck"}),
                )
            return
        siblings = self._graph.get_child_tasks(task_id, parent.node_id)
        if any(
            st.status in {Status.RUNNING, Status.PLANNING, Status.PENDING}
            for st in siblings
        ):
            return  # 还有活子,等
        if any(st.status == Status.HUNG for st in siblings):
            self._report_node_patch(
                TaskNodePatch(
                    task_id=task_id,
                    node_id=parent.node_id,
                    status=Status.HUNG,
                    extend_props_patch={"hung_reason": "child_hung"},
                )
            )
            logger.info(
                "[task][hung-propagate] task=%s 父=%s 因子含 HUNG→HUNG",
                task_id,
                parent.node_id,
            )
            cur = parent.node_id
            continue
        return


def _reconcile_root_hung_if_blocked(self, task_id: str) -> None:
    """Close a missed root-HUNG transition after a late sibling completion.

    HUNG propagation can observe an active sibling and return. A later
    completion may arrive through a status-only or fold-only callback path
    that does not call ``_on_pass_collect``. Re-scan the root boundary after
    every callback so an all-terminal root with any HUNG child cannot remain
    in PLANNING/RUNNING forever.
    """
    root = self._root(task_id)
    if root is None or root.status in {
        Status.HUNG,
        Status.DONE,
        Status.SUCCESS,
        Status.FAILED,
        Status.CANCELLED,
    }:
        return
    siblings = self._graph.get_child_tasks(task_id, root.node_id)
    if not siblings or any(
        node.status in {Status.PENDING, Status.PLANNING, Status.RUNNING}
        for node in siblings
    ):
        return
    hung = next((node for node in siblings if node.status == Status.HUNG), None)
    if hung is None:
        return
    reason = str(hung.run_info.extend_props.get("hung_reason") or "child_hung")
    logger.warning(
        "[task][hung-reconcile] task=%s root=%s all children terminal with HUNG child=%s "
        "-> force root propagation reason=%s",
        task_id,
        root.node_id,
        hung.node_id,
        reason,
    )
    self._maybe_propagate_hung(task_id, hung.node_id, reason)
