"""TaskRunner 任务执行模块:三模态投递 + 回投。对齐 plan.md §3.5 + tasks.md T4b。

Avernet 阶段:form_coop_group stub(不真实 BCS)、start_run stub 投递(记日志,不真实 bot workflow/群/BBS)。
三类投递后端经 ``set_delivery`` 注入(corp ocb 仓:真实 workflow engine/BCS/BBS 广场);缺省 stub fallback。
"""

from __future__ import annotations

from agentclaw.community.core.task.task_runner.centralized_support import (
    AcceptanceResult,
    AcceptanceVerdict,
    NodeAction,
    ReasonCatalog,
    TaskNodePatch,
    _STATIC_MOCK_SUMMARY,
    _UHT_MOCK,
    _dispatch_fail_action_result,
)


import asyncio
import logging
import os
import random
import uuid
from typing import Any, Protocol

from agentclaw.community.core.task.domain.models import (
    Status,
    TaskNode,
    task_spec_instruction,
    TaskNodeQueryCriteria,
)
from agentclaw.community.core.task.task_dispatch.strategies import GroupFormation

logger = logging.getLogger(__name__)


class DeliveryPort(Protocol):
    """执行投递后端 seam(单 bot workflow / bcn 协作群 / BBS 广场)。corp 注入真实实现。"""

    async def deliver(self, node: TaskNode) -> bool:
        """投递任务节点给执行主体,返回是否投递成功(完成结果经 TaskLoopCallback PUSH 回投)。"""
        ...


class TaskRunner:
    """将已派发 TaskNode 发送给单 bot/协作群/BBS 执行,并回收状态/详情/结果。

    调用方:编排核(经 TaskService facade 驱动)。一个 start_run(批量)入口三模态自适应。
    三类投递后端经 ``set_delivery(mode, port)`` 注入(corp);缺省 stub 记投递日志返回 True。
    投递并发:``start_run`` 内部 ``asyncio.gather`` + ``_DELIVER_CONCURRENCY`` Semaphore 限流
    (对齐 backend lifecycle 模式,多节点网络投递并发防雪崩)。
    """

    # 投递并发上限(多节点投递 gather 限流;对齐 backend lifecycle Semaphore 模式)。
    _DELIVER_CONCURRENCY = 8

    def __init__(self, graph, execution_backend=None) -> None:
        """graph: TaskGraphService(派生查询 + 投递映射用);execution_backend: TaskExecutor | None
        (注入则真实派发 single_bot/coop_group/bbs;缺省 stub fallback 记日志)。"""
        self._graph = graph
        self._execution_backend = execution_backend
        self._deliveries: dict[str, DeliveryPort] = {}
        self._groups: dict[
            str, GroupFormation
        ] = {}  # group_id -> GroupFormation(form_coop_group stub 记录)
        self._run_log: list[dict[str, Any]] = []  # 投递日志(stub fallback,不真实发起)

    def set_delivery(self, mode: str, port: DeliveryPort) -> None:
        """(非公开)注入执行投递后端。mode∈{"single_bot","coop_group","bbs"};corp ocb 仓注入真实实现。"""
        self._deliveries[mode] = port

    async def start_run(self, toDoTaskList: list[TaskNode]) -> list[bool]:
        """图谱上有 TaskNode 完成派发后立即触发执行。入参批量(刚被 dispatcher/adaptor patch 完
        run_mode/assignee 的节点);返回每个任务派发是否成功 list[bool]。

        内部按 run_mode(str)自适应分发:有注入 delivery → ``await`` delivery.deliver(投递耗时 IO),
        多节点经 ``asyncio.gather`` + ``_DELIVER_CONCURRENCY`` Semaphore 并发限流(对齐 backend lifecycle
        模式,防投递雪崩);否则 stub 记日志返 True。
        协程化:真实投递(单 bot workflow/BCS 协作群/BBS 广场)是网络 IO,并发 await 不阻塞编排核。"""
        if self._execution_backend is not None:
            # 真实后端一次接收完整批次，由其统一 semaphore 控制三种模态的并发。
            return list(await self._execution_backend.dispatch(toDoTaskList))

        sem = asyncio.Semaphore(self._DELIVER_CONCURRENCY)

        async def _deliver_one(node: TaskNode) -> bool:
            mode = node.run_info.run_mode
            if mode not in ("single_bot", "coop_group", "bbs"):
                return False
            async with sem:
                port = self._deliveries.get(mode)
                if port is not None:
                    return bool(await port.deliver(node))
                logger.warning(
                    "[task][task_runner] start_run 退桩(无 execution_backend 且无 %s delivery 注入)→ node=%s 记日志返 True,不真实发起",
                    mode,
                    node.node_id,
                )
                self._run_log.append(
                    {
                        "task_id": node.task_id,
                        "node_id": node.node_id,
                        "run_mode": mode,
                        "assignee": node.run_info.assignee,
                        "loop_task_id": f"{node.task_id}::{node.node_id}",
                    }
                )
                return True

        return list(await asyncio.gather(*[_deliver_one(n) for n in toDoTaskList]))

    async def form_coop_group(self, gf: GroupFormation) -> str:
        """(内部)HIT_MULTI_BOTS 动态拉协作群,复用 BCS 建群 → group_id。
        协程化:BCS 建群是网络 IO,``await`` 不阻塞编排核(由 engine 锁外 await 调用)。
        注入 execution_backend 时委托其真实建群;否则 Avernet stub:生成 group_id 并记录 GroupFormation。
        prod BCS wiring(group_strategy=collab_mode;state_machine 注入 workflow yaml)在 ocb 仓。"""
        logger.info("[task][task_runner] form_coop_group begin, group_formation=%s", gf)

        if self._execution_backend is not None:
            return await self._execution_backend.form_coop_group(gf)
        gid = f"grp_{uuid.uuid4().hex[:8]}"
        self._groups[gid] = gf
        logger.warning(
            "[task][task_runner] form_coop_group 退桩(无 execution_backend)→ 造假 group_id=%s;"
            "无真群/无 poller,任务将卡 RUNNING 不收敛。排查: grep [task][engine] execution_backend 不装配",
            gid,
        )
        return gid

    async def resume_relay_turn(self, node: TaskNode, relay_turn: str) -> bool:
        """Resume the current Relay holder at PLAN_RESULT without re-executing work."""
        backend = self._execution_backend
        resume = getattr(backend, "resume_relay_turn", None)
        if not callable(resume):
            logger.warning(
                "[task][task_runner] relay resume unavailable node=%s execution_backend=%s",
                node.node_id,
                type(backend).__name__ if backend is not None else "None",
            )
            return False
        return bool(await resume(node, relay_turn))

    async def get_group_session(self, group_id: str) -> str | None:
        """Fetch the initial session_id for a coop group; create one if absent."""
        if self._execution_backend is not None:
            return await self._execution_backend.get_group_session(group_id)
        logger.debug(
            "[task][task_runner] get_group_session 退桩→ None(group_id=%s 无 execution_backend)",
            group_id,
        )
        return None

    def _build_context(self, task_id: str, node_id: str) -> dict[str, Any]:
        """上下文组装(Runner 内聚;内部自动判定,无 NODE/SUBTREE/TASK scope 入参)。

        有结构子(``get_child_tasks`` 非空)→**验收模式**:聚合【结构子(子树)DONE 的 run_info.output
        + 本节点 ``task_spec.goal/acceptances``】→ 组装验证 prompt(经 source_channel 派 owner/master bot)。
        无结构子→**执行模式**:取结构父 ``P = get_parent_task``;聚合【``P.task_spec/goal`` + P 已 DONE 结构子
        (本节点兄弟)``run_info.output`` + 本节点 ``task_spec``】→ 组装执行 prompt 注入执行主体。
        数据流一律经结构父 P 中转,无跨兄弟直接数据边。"""
        node = self._get_node(task_id, node_id)
        relay_blackboard = self._relay_blackboard(task_id)
        children = self._graph.get_child_tasks(task_id, node_id)
        if children:
            return {
                "mode": "verify",
                "child_outputs": {
                    c.node_id: c.run_info.output
                    for c in children
                    if c.status == Status.SUCCESS
                },
                "goal": node.task_spec.goal if node else None,
                "acceptances": node.task_spec.goal.acceptances if node else None,
                "node_instruction": (
                    node.run_info.extend_props.get("execution_prompt")
                    or task_spec_instruction(node.task_spec)
                )
                if node
                else None,
                "relay_blackboard": relay_blackboard,
            }
        parent = self._graph.get_parent_task(task_id, node_id)
        if parent is None:
            return {
                "mode": "execute",
                "parent_node_id": None,
                "parent_spec": None,
                "sibling_outputs": {},
                "node_spec": node.task_spec if node else None,
                "relay_blackboard": relay_blackboard,
            }
        siblings = self._graph.get_child_tasks(task_id, parent.node_id)
        sibling_outputs = {
            s.node_id: s.run_info.output
            for s in siblings
            if s.status == Status.SUCCESS and s.node_id != node_id
        }
        return {
            "mode": "execute",
            "parent_node_id": parent.node_id,
            "parent_spec": parent.task_spec,
            "sibling_outputs": sibling_outputs,
            "node_spec": node.task_spec if node else None,
            "relay_blackboard": relay_blackboard,
        }

    def _relay_blackboard(self, task_id: str) -> dict[str, Any] | None:
        graph = self._graph.query_task_dashboard(task_id)
        config = graph.extend_props.get("execution_config", {}) or {}
        if config.get("orchestration_mode") != "relay":
            return None
        root = next((node for node in graph.tasks if node.node_id == task_id), None)
        return {
            "root_goal": root.task_spec.goal.to_dict() if root else {},
            "loop_round": graph.loop_round,
            "nodes": [
                {
                    "node_id": node.node_id,
                    "status": node.status.value,
                    "goal": node.task_spec.goal.to_dict(),
                    "output": node.run_info.output,
                }
                for node in graph.tasks
            ],
        }

    def _get_node(self, task_id: str, node_id: str) -> TaskNode | None:
        """从图回读单节点(经公开 ``query_task_nodes``;Runner 不持有图对象引用篡改)。"""
        hits = self._graph.query_task_nodes(
            task_id, TaskNodeQueryCriteria(node_ids=[node_id])
        )
        return hits[0] if hits else None

    async def _drain(self, task_id: str, side: list[tuple]) -> None:
        """锁外统一执行 side effects。投递/拉群 IO 锁外 await;翻态(side effect)收口锁内。
        v4 状态机:run 经 start_run 投递,成功后才翻 RUNNING+清 dispatching(对齐"调执行方法后置 RUNNING");
        失败→清执行者+清 dispatching+标 dispatch_error 留 PENDING 交 harness 重试搜推。group 经 form_coop_group
        拉群后翻 RUNNING+清 dispatching。miss 递归推进与 run 投递不互相阻塞。"""
        run_nodes: list[TaskNode] = []
        miss_tasks: list[TaskNodePatch] = []
        dispatch_fail_patches: list[TaskNodePatch] = []
        auto_nodes: list[TaskNode] = []
        for kind, *payload in side:
            if kind == "run":
                run_nodes.extend(payload[0])
            elif kind == "group":
                node, gf = payload
                logger.info(
                    "[task][drain] task=%s node=%s 拉群开始 collab=%s bot_ids=%s members=%s "
                    "dynamic_task_node_protocol=%s",
                    task_id,
                    node.node_id,
                    gf.collab_mode,
                    list(getattr(gf, "bot_ids", []) or []),
                    list(getattr(gf, "members_info", []) or []),
                    gf.extend_props.get("dynamic_task_node_protocol"),
                )
                # 协作群叶子:注入 loop_task_id 供 form_coop_group 写入群 context,
                # 供 driver/owner bot 验收后 push 回投 /callback/report 定位执行节点
                # (acceptance 段4;single_bot 走 poll,不经此拉群路径)。
                gf.extend_props.setdefault(
                    "loop_task_id", f"{node.task_id}::{node.node_id}"
                )
                if gf.extend_props.get("dynamic_task_node_protocol"):
                    # 只作用于动态规划的 manager_worker 群。静态计划、YAML 和
                    # BBS 链路不带此标记，维持各自既有的群上下文和执行语义。
                    group_context = self.build(node.task_id, node.node_id)
                    gf.extend_props.setdefault("task_id", node.task_id)
                    gf.extend_props.setdefault(
                        "task_objective", node.task_spec.goal.objective
                    )
                    gf.extend_props.setdefault(
                        "task_instruction", task_spec_instruction(node.task_spec)
                    )
                    gf.extend_props.setdefault(
                        "acceptances",
                        [
                            {"id": item.id, "description": item.description}
                            for item in node.task_spec.goal.acceptances
                        ],
                    )
                    gf.extend_props.setdefault(
                        "upstream_outputs", group_context.get("sibling_outputs") or {}
                    )
                try:
                    gid = await self._runner.form_coop_group(gf)
                    logger.info(
                        "[task][drain] task=%s node=%s 拉群成功 group_id=%s collab=%s",
                        task_id,
                        node.node_id,
                        gid,
                        gf.collab_mode,
                    )
                except Exception as ex:  # noqa: BLE001  拉群异常→清 dispatching 留 PENDING 交 harness
                    logger.exception(
                        "[task][drain] task=%s node=%s 拉群失败 exc_type=%s exc=%s collab=%s bot_ids=%s",
                        task_id,
                        node.node_id,
                        type(ex).__name__,
                        ex,
                        gf.collab_mode,
                        list(getattr(gf, "bot_ids", []) or []),
                    )
                    with self._lock_for(task_id):
                        self._report_node_patch(
                            TaskNodePatch(
                                task_id=task_id,
                                node_id=node.node_id,
                                run_mode="",
                                assignee="",
                                extend_props_patch={
                                    "dispatching": None,
                                    "dispatch_error": "form_group_failed",
                                },
                            )
                        )
                    # 轨迹旁路:DISPATCH(form_group_failed) —— 拉群失败,节点清执行者留 PENDING 交
                    # harness 重试。error_type=DISPATCH_STUCK(analyzer failure_reason 不派生自此 — 仅
                    # RESET pending_dispatch_stuck bullet 触及);error_msg 含异常类型便于排查。
                    self._log_trajectory(
                        task_id,
                        node.node_id,
                        "dispatch",
                        action_result="form_group_failed",
                        action_input=None,
                        error_type=ReasonCatalog.DISPATCH_STUCK,
                        error_msg=f"form_group_failed: {type(ex).__name__}",
                        status_from=Status.PENDING,
                        status_to=Status.PENDING,
                        attempt=int(
                            node.run_info.extend_props.get("harness_retries", 0) or 0
                        ),
                    )
                    continue
                node.run_info.assignee = gid
                with self._lock_for(task_id):
                    self._report_node_patch(
                        TaskNodePatch(
                            task_id=task_id,
                            node_id=node.node_id,
                            status=Status.RUNNING,
                            run_mode=node.run_info.run_mode,
                            assignee=gid,
                            extend_props_patch={"dispatching": None},
                        )
                    )
                # 动作历史:DISPATCH(HIT_MULTI 协作群)
                self._log_action(
                    task_id,
                    node.node_id,
                    NodeAction.DISPATCH,
                    {
                        "outcome": "HIT_MULTI",
                        "run_mode": "coop_group",
                        "assignee": gid,
                        "collab_mode": getattr(gf, "collab_mode", None),
                        "bot_ids": list(getattr(gf, "bot_ids", []) or []),
                    },
                    status_from=Status.PENDING,
                    status_to=Status.RUNNING,
                )
                # 轨迹旁路:DISPATCH(HIT_MULTI) —— action_input=group_id(assignee);
                # ext_info 携带 ``_dispatch_rationale`` (经 dispatcher 写入节点 extend_props)。
                # ``node`` 是 in-scope in-memory 节点 —— inline 读 carrier(零额外查询,
                # 与 MISS 闸门走 _read_dispatch_side_data 的路径对称:那里 patch 是 TaskNodePatch
                # 必须查图;这里节点已在手上,无需查)。
                _hit_multi_rat = node.run_info.extend_props.get("_dispatch_rationale")
                _hit_multi_fail = node.run_info.extend_props.get("_dispatch_failure")
                _hit_multi_ext: dict[str, Any] = {}
                if isinstance(_hit_multi_rat, dict):
                    _hit_multi_ext["_dispatch_rationale"] = _hit_multi_rat
                if isinstance(_hit_multi_fail, dict):
                    _hit_multi_ext["_dispatch_failure"] = _hit_multi_fail
                self._log_trajectory(
                    task_id,
                    node.node_id,
                    "dispatch",
                    action_result="hit_multi",
                    action_input=gid,
                    ext_info=_hit_multi_ext or None,
                    status_from=Status.PENDING,
                    status_to=Status.RUNNING,
                    attempt=int(
                        node.run_info.extend_props.get("harness_retries", 0) or 0
                    ),
                )
                run_nodes.append(node)
            elif kind == "auto":
                auto_nodes.extend(payload[0])
            elif kind == "bbs_handoff":
                node, bot_id, items = payload
                t = asyncio.create_task(
                    self._bbs_handoff_claim(task_id, node.node_id, bot_id, items)
                )
                # 强引用保活(同 auto-report),避免 sleep 期间被 GC 回收
                self._bg_tasks.add(t)
                t.add_done_callback(self._on_bbs_handoff_done)
                logger.info(
                    "[task][static-plan] bbs_handoff claim scheduled task=%s node=%s rnd_bot=%s in %.1fs",
                    task_id,
                    node.node_id,
                    bot_id,
                    self._bbs_handoff_delay(task_id),
                )
            elif kind == "miss":
                miss_tasks.append(payload[0])
            elif kind == "dispatch_fail":
                dispatch_fail_patches.append(payload[0])
            elif kind == "finish":
                logger.info("[task][drain] task=%s finish(根 gap 闭→图 DONE)", task_id)
                self._maybe_finish_graph(payload[0])
        # ① run:start_run 投递,成功后翻 RUNNING+清 dispatching;失败清执行者+清 dispatching+标 dispatch_error 留 PENDING
        if run_nodes:
            logger.info(
                "[task][drain] task=%s start_run %d 节点:%s",
                task_id,
                len(run_nodes),
                [n.node_id for n in run_nodes],
            )
            try:
                results = await self._runner.start_run(run_nodes)
            except Exception as ex:  # noqa: BLE001  start_run 异常→全部当失败,清 dispatching 留 PENDING 交 harness
                logger.warning(
                    "[task][drain] task=%s start_run 异常:%s→全部留 PENDING 待 harness",
                    task_id,
                    ex,
                )
                results = [False] * len(run_nodes)
            with self._lock_for(task_id):
                cur_map = {
                    x.node_id: x
                    for x in self._graph.query_task_nodes(
                        task_id,
                        TaskNodeQueryCriteria(node_ids=[n.node_id for n in run_nodes]),
                    )
                }
                for node, ok in zip(run_nodes, results):
                    if not ok:
                        logger.warning(
                            "[task][drain] task=%s node=%s start_run 失败→清执行者留 PENDING 待 harness",
                            task_id,
                            node.node_id,
                        )
                        # 清 run_mode/assignee(置空串)+清 dispatching 使其重新可搜推;标 dispatch_error
                        self._report_node_patch(
                            TaskNodePatch(
                                task_id=task_id,
                                node_id=node.node_id,
                                run_mode="",
                                assignee="",
                                extend_props_patch={
                                    "dispatching": None,
                                    "dispatch_error": "start_run_failed",
                                },
                            )
                        )
                        # 轨迹旁路:DISPATCH(start_run_failed) —— 投递失败,节点清执行者留 PENDING
                        # 交 harness 重试。error_type=DISPATCH_STUCK;start_run 整批 except →
                        # results=[False]*n 不暴露单节点异常,故 error_msg 仅标类别。
                        self._log_trajectory(
                            task_id,
                            node.node_id,
                            "dispatch",
                            action_result="start_run_failed",
                            action_input=None,
                            error_type=ReasonCatalog.DISPATCH_STUCK,
                            error_msg="start_run_failed",
                            status_from=Status.PENDING,
                            status_to=Status.PENDING,
                            attempt=int(
                                node.run_info.extend_props.get("harness_retries", 0)
                                or 0
                            ),
                        )
                        continue
                    cur = cur_map.get(node.node_id)
                    if cur is not None and cur.status == Status.PENDING:
                        self._report_node_patch(
                            TaskNodePatch(
                                task_id=task_id,
                                node_id=node.node_id,
                                status=Status.RUNNING,
                                extend_props_patch={"dispatching": None},
                            )
                        )
                        # 动作历史:DISPATCH(HIT_SINGLE 单 bot 派发执行)
                        self._log_action(
                            task_id,
                            node.node_id,
                            NodeAction.DISPATCH,
                            {
                                "outcome": "HIT_SINGLE",
                                "run_mode": cur.run_info.run_mode,
                                "assignee": cur.run_info.assignee,
                            },
                            status_from=Status.PENDING,
                            status_to=Status.RUNNING,
                        )
                        # 轨迹旁路:DISPATCH(HIT_SINGLE) —— action_input=cur.run_info.assignee
                        # (dispatch target);ext_info 携带 ``_dispatch_rationale``。
                        # ``cur`` 是 in-scope in-memory 节点 —— inline 读 carrier(零额外查询,
                        # 与 MISS 闸门走 _read_dispatch_side_data 的路径对称:那里 patch 是
                        # TaskNodePatch 必须查图;这里节点已在手上,无需查)。
                        _hit_single_rat = cur.run_info.extend_props.get(
                            "_dispatch_rationale"
                        )
                        _hit_single_fail = cur.run_info.extend_props.get(
                            "_dispatch_failure"
                        )
                        _hit_single_ext: dict[str, Any] = {}
                        if isinstance(_hit_single_rat, dict):
                            _hit_single_ext["_dispatch_rationale"] = _hit_single_rat
                        if isinstance(_hit_single_fail, dict):
                            _hit_single_ext["_dispatch_failure"] = _hit_single_fail
                        self._log_trajectory(
                            task_id,
                            node.node_id,
                            "dispatch",
                            action_result="hit_single",
                            action_input=cur.run_info.assignee,
                            ext_info=_hit_single_ext or None,
                            status_from=Status.PENDING,
                            status_to=Status.RUNNING,
                            attempt=int(
                                cur.run_info.extend_props.get("harness_retries", 0) or 0
                            ),
                        )
        # ④ 固定流程兜底上报:每个真实派发节点都调度一条延迟 mock 兜底(auto=True 短延迟演示,
        #    默认真实模式 fallback 超时 80s);真实回投先到则 _static_auto_report 内自跳过。
        if auto_nodes:
            logger.info(
                "[task][drain] task=%s fallback-mock scheduled %d nodes: %s",
                task_id,
                len(auto_nodes),
                [n.node_id for n in auto_nodes],
            )
            for n in auto_nodes:
                t = asyncio.create_task(self._static_auto_report(task_id, n.node_id))
                # 保活:存强引用,避免 sleep 期间被 GC 回收导致 on_report 永不触发(asyncio 官方坑)
                self._bg_tasks.add(t)
                t.add_done_callback(self._on_auto_report_done)
                logger.info(
                    "[task][static-plan] report-fallback scheduled task=%s node=%s task_obj=%s",
                    task_id,
                    n.node_id,
                    id(t),
                )
        # ② dispatch_fail:落 dispatch_error(留 PENDING,harness 按超时重试搜推)
        for patch in dispatch_fail_patches:
            self._report_node_patch(patch)
            # 轨迹旁路:DISPATCH(failed) —— 派发未产出执行者(搜推异常 / 无结果),节点留 PENDING 交
            # harness。error_type=DISPATCH_STUCK;error_msg 优先取 ``_dispatch_failure`` carrier(含异常
            # 消息,dispatcher 顶层 except 写入并经 _handle_node 透传),回退到短 dispatch_error 状态串。
            _derr = (patch.extend_props_patch or {}).get(
                "dispatch_error"
            ) or "no_result"
            _df = (patch.extend_props_patch or {}).get("_dispatch_failure")
            _emsg = (_df.get("error_msg") if isinstance(_df, dict) else None) or str(
                _derr
            )
            self._log_trajectory(
                patch.task_id,
                patch.node_id,
                "dispatch",
                action_result=_dispatch_fail_action_result(_derr),
                action_input=None,
                error_type=ReasonCatalog.DISPATCH_STUCK,
                error_msg=_emsg[:500] if _emsg else None,
                status_from=Status.PENDING,
                status_to=Status.PENDING,
                attempt=0,
            )
        # ③ miss 推进(递归 collect+drain)
        for m in miss_tasks:
            await self.on_miss(m)

    def _static_auto_report_on(self, task_id: str) -> bool:
        """演示自驱开关:开启后静态 plan 节点不做真实派发/拉群,转为后台自回投 mock 结果,

        复用同一 on_report 通路推进图态,便于上报/skill 未就绪时也能跑通全链路。

        优先级:按任务 execution_config.static_auto_report(bool) → 服务端 env OCB_TASK_STATIC_AUTO_REPORT。"""

        # 与 _static_runtime 同判据:预置模板 plan 任务才需要 static_auto_report 开关。

        cfg = self._graph._execution_config(task_id)

        if not (
            cfg.get("static_plan_id")
            or cfg.get("static_plan_yaml")
            or cfg.get("task_type") == "static_plan"
        ):
            return False

        flag = cfg.get("static_auto_report")

        if isinstance(flag, bool):
            return flag

        return os.environ.get("OCB_TASK_STATIC_AUTO_REPORT", "").lower() in {
            "1",
            "true",
            "yes",
            "on",
        }

    async def _static_auto_report(self, task_id: str, node_id: str) -> None:
        """固定流程兜底上报:节点真实派发后,若在随机延迟内(单 bot 20-40s,协作群 40-80s;取消固定 80s 超时)无真实回投,

        则以 mock 信息回投 PASS→SUCCESS 推进图态,避免单节点不上报致整流程卡死;

        auto=True 演示模式改走短("demo")延迟。



        mock 只替代"上报信息",不替代派发——拉群/发消息仍走真实路径(_drain group/run)。

        延迟到期后仅在节点处 RUNNING 态(真实派发成功)时才回投 PASS→SUCCESS;

        若派发失败留 PENDING / 已被真实上报翻 DONE,则跳过(暴露真实失败,不掩盖,不重复翻态)。"""

        runtime = self._static_runtime(task_id)

        if runtime is None:
            return

        auto = self._static_auto_report_on(task_id)

        delay = (
            self._static_auto_report_delay(task_id)
            if auto
            else self._static_mock_fallback_delay(task_id, node_id)
        )

        logger.info(
            "[task][static-plan] %s scheduled task=%s node=%s in %.2fs",
            "auto-report" if auto else "fallback-report",
            task_id,
            node_id,
            delay,
        )

        await asyncio.sleep(delay)

        # 仅真实派发成功(RUNNING)才 mock 上报;派发失败/已真实上报则跳过

        graph = self._graph.query_task_dashboard(task_id)

        node = next((n for n in graph.tasks if n.node_id == node_id), None)

        if node is None or node.status != Status.RUNNING:
            logger.info(
                "[task][static-plan] auto-report skip task=%s node=%s status=%s (非 RUNNING,留给真实派发/上报)",
                task_id,
                node_id,
                node.status.value if node is not None else None,
            )

            return

        definition = runtime.by_id.get(node_id)

        # 兜底产出摘要:用各节点真实产出(剧本)代替 [auto] 占位,使下游 ## 上游产出正文 可读、流程不因

        # 无意义占位文本读不通。真实上报先到则本兜底自跳过,不被使用。

        mock_result: Any = {
            "summary": _STATIC_MOCK_SUMMARY.get(node_id, f"[auto] node={node_id}"),
            "random": f"{random.randrange(10**6):06d}",
        }

        if definition is not None and any(
            isinstance(v, str) and v.startswith("$.result.approved")
            for v in definition.output.values()
        ):
            mock_result["approved"] = True

        # 造不可实现任务列表(仅当节点 output 含 $.result.unhandled_tasks,如 risk_assessment 群):

        # 大促剧本兜底=舆情监控方案缺失(内部无舆情监控 bot,转 BBS 安全架构师)。

        if definition is not None and any(
            isinstance(v, str) and v.startswith("$.result.unhandled_tasks")
            for v in definition.output.values()
        ):
            mock_result["unhandled_tasks"] = [dict(t) for t in _UHT_MOCK]

        logger.info(
            "[task][static-plan] auto-report fire task=%s node=%s mock=%s -> on_report",
            task_id,
            node_id,
            mock_result,
        )

        await self.on_report(
            TaskNodePatch(
                task_id=task_id,
                node_id=node_id,
                acceptance_result=AcceptanceResult(
                    verdict=AcceptanceVerdict.DONE,
                    done_items=["static_auto"],
                ),
                output_patch={"result": mock_result},
                extend_props_patch={"dispatching": None},
            )
        )

    async def _static_bbs_handoff_auto_report(
        self, task_id: str, node_id: str, rnd_bot_id: str, items: Any
    ) -> None:
        """固定流程 bbs_handoff 兜底上报:与节点级 _static_auto_report 同语义——真实 poller 先到则自跳过(节点非 RUNNING);

        否则随机延迟(单 bot/node 同单 bot 20-40s)后 mock PASS→SUCCESS,避免旁路节点长挂致整流程不终态。

        auto 演示模式用短 demo 延迟。"""

        auto = self._static_auto_report_on(task_id)

        delay = (
            self._static_auto_report_delay(task_id)
            if auto
            else self._static_mock_fallback_delay(task_id, node_id)
        )

        logger.info(
            "[task][static-plan] %s scheduled task=%s node=%s in %.2fs",
            "bbs-auto-report" if auto else "bbs-fallback-report",
            task_id,
            node_id,
            delay,
        )

        await asyncio.sleep(delay)

        g2 = self._graph.query_task_dashboard(task_id)

        n2 = next((x for x in g2.tasks if x.node_id == node_id), None)

        if n2 is None or n2.status != Status.RUNNING:
            logger.info(
                "[task][static-plan] bbs_handoff report-skip task=%s node=%s status=%s (真实闭环/未RUNNING)",
                task_id,
                node_id,
                n2.status.value if n2 is not None else None,
            )

            return

        await self.on_report(
            TaskNodePatch(
                task_id=task_id,
                node_id=node_id,
                acceptance_result=AcceptanceResult(
                    verdict=AcceptanceVerdict.DONE,
                    done_items=["bbs_handoff"],
                ),
                output_patch={
                    "result": {
                        "summary": _STATIC_MOCK_SUMMARY.get(
                            node_id, f"[bbs-handoff] node={node_id}"
                        ),
                        "handed_to": rnd_bot_id,
                        "items": items,
                        "random": f"{random.randrange(10**6):06d}",
                    }
                },
                extend_props_patch={"dispatching": None},
            )
        )
