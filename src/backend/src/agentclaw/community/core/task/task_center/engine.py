"""ExecutionEngine 内部编排核(事件驱动 + 状态条件触发)。对齐 plan.md §3.0。

非独立模块,TaskService 内部实现细节,对外不暴露。零参构造,自建 planner/dispatcher/runner
(内置策略池 + stub 投递);corp 子类覆写 ``_build_*`` 工厂方法注入真实策略/投递后端(ocb 仓)。
验收 100% 走 on_report 回投(engine 不主动验,无 OwnerBotVerifyPort);BBS 投递归 runner BBS 模态
(无 BbsMarketPort,升 BBS 只翻图态 bbs_mode)。零 case 知识:engine 不含任何节点名字面量。
"""
from __future__ import annotations

import threading

from agentclaw.community.core.task.domain.models import (
    AcceptanceVerdict,
    NodeOpResult,
    Status,
    TaskGraphPatch,
    TaskNode,
    TaskNodePatch,
    TaskNodeQueryCriteria,
)


class ExecutionEngine:
    """事件驱动 + 状态条件触发协调 plan/graph/dispatch/execution。

    零参构造:``_build_planner/dispatcher/runner`` 工厂方法自建(TaskPlanner/TaskDispatcher/TaskRunner
    内置策略池 + stub 投递)。corp 子类覆写工厂方法注入真实策略池/投递后端(ocb 仓)。
    on_* 入参统一收口 TaskNodePatch。按事件 + 状态条件(a/b/c + plan 三条件)分段协调
    (无 single drive fixpoint 泵)。同 task_id 串行(per-task RLock),跨 task 并行。
    loop_round 仅升 BBS 时 ++(外层 BBS 上升轮次,正常补救不 ++)。
    """

    def __init__(self, graph) -> None:
        self._graph = graph
        self._planner = self._build_planner()
        self._dispatcher = self._build_dispatcher()
        self._runner = self._build_runner()
        self._locks: dict[str, threading.RLock] = {}
        self._locks_guard = threading.RLock()

    # ===== protected 工厂方法(corp 覆写 seam;Avernet 默认内置 stub)=====
    def _build_planner(self):
        from agentclaw.community.core.task.task_plan.planner import TaskPlanner
        return TaskPlanner(self._graph)

    def _build_dispatcher(self):
        from agentclaw.community.core.task.task_dispatch.dispatcher import TaskDispatcher
        return TaskDispatcher(self._graph)

    def _build_runner(self):
        from agentclaw.community.core.task.task_runner.runner import TaskRunner
        return TaskRunner(self._graph)

    def _lock_for(self, task_id: str) -> threading.RLock:
        with self._locks_guard:
            lk = self._locks.get(task_id)
            if lk is None:
                lk = threading.RLock()
                self._locks[task_id] = lk
            return lk

    def _root(self, task_id: str) -> TaskNode | None:
        graph = self._graph.query_task_dashboard(task_id)
        for n in graph.tasks:
            if self._graph.get_parent_task(task_id, n.node_id) is None:
                return n
        return None

    # ===== on_execute =====
    def on_execute(self, task_id: str) -> None:
        """execute 事件:initialize_graph 后,条件 a(根 PENDING)→ plan→add→dispatch→start_run。"""
        with self._lock_for(task_id):
            root = self._root(task_id)
            if root is None or root.status != Status.PENDING:
                return  # 非条件 a
            graph = self._graph.query_task_dashboard(task_id)
            nodes = self._planner.plan(graph)
            if not nodes:
                return  # 无可规划
            self._graph.add_task_nodes(nodes, root.node_id)
            self._dispatch_and_run(task_id)

    # ===== on_report =====
    def on_report(self, patch: TaskNodePatch) -> NodeOpResult:
        """回投事件:patch 内含 (task_id,node_id)+acceptance_result+output_patch。
        update_task_node_info 翻态(+fold)→ PASS 传播 / FAIL+gaps 补救。验收 100% 来自回投,engine 不主动验。"""
        with self._lock_for(patch.task_id):
            result = self._graph.update_task_node_info(patch)
            if patch.acceptance_result is None:
                return result  # 仅 fold,无翻态
            verdict = patch.acceptance_result.verdict
            if verdict == AcceptanceVerdict.PASS:
                self._on_pass(patch.task_id, patch.node_id)
            else:  # FAIL
                self._on_fail(patch.task_id, patch.node_id)
            return result

    def _on_pass(self, task_id: str, node_id: str) -> None:
        """PASS→DONE 后:查结构父 P;P=PLANNING 且本批兄弟全 DONE ∧ 无 RUNNING(决策C)
        → 委托 plan→decompose(P):产新子→add→dispatch;gap 闭 → 非根传播 DONE 上行 / 根保持 PLANNING 等回投。
        根 PASS(来自 owner bot 回投)→ 图完成(_maybe_finish_graph)。"""
        parent = self._graph.get_parent_task(task_id, node_id)
        if parent is None:
            # 根 PASS(来自 owner bot 终验回投)→ 图完成
            self._maybe_finish_graph(task_id)
            return
        if parent.status != Status.PLANNING:
            return  # 父非委托态,不推进
        siblings = self._graph.get_child_tasks(task_id, parent.node_id)
        if not all(s.status == Status.DONE for s in siblings):
            return  # 兄弟未齐,等待
        if any(s.status == Status.RUNNING for s in siblings):
            return  # 仍有 RUNNING,等待
        # 本批兄弟全 DONE → 委托 plan
        graph = self._graph.query_task_dashboard(task_id)
        new_children = self._planner.plan(graph)
        if new_children:
            self._graph.add_task_nodes(new_children, parent.node_id)
            self._dispatch_and_run(task_id)
        else:
            # decompose 返 [](gap 闭)→ 非根传播 DONE / 根保持 PLANNING 等回投
            root = self._root(task_id)
            if parent.node_id == (root.node_id if root else None):
                return  # 根:保持 PLANNING 等 owner bot 终验回投(engine 不主动验)
            self._graph.update_task_node_info(
                TaskNodePatch(task_id=task_id, node_id=parent.node_id, status=Status.DONE)
            )
            self._on_pass(task_id, parent.node_id)  # 上行传播

    def _on_fail(self, task_id: str, node_id: str) -> None:
        """FAIL+gaps→FAILED 后:深度闸门 <MAX → plan→add(补救子挂该节点下,该节点进 PLANNING)→dispatch;
        ≥MAX → 自动升 BBS(remove_subtree+loop_round+++标 BBS)。"""
        depth = self._graph._node_depth(task_id, node_id)
        cfg = self._graph._execution_config(task_id)
        max_depth = cfg["MAX_DEPTH"]
        if depth < max_depth:
            graph = self._graph.query_task_dashboard(task_id)
            new_children = self._planner.plan(graph)
            if new_children:
                self._graph.add_task_nodes(new_children, node_id)
                self._dispatch_and_run(task_id)
        else:
            self._escalate_bbs(task_id, node_id)

    # ===== on_miss =====
    def on_miss(self, patch: TaskNodePatch) -> None:
        """dispatcher MISS → 节点仍 PENDING(miss_events 已填):
        <MAX → plan→add(拆细)→ 消费 miss_events → dispatch;≥MAX → 自动升 BBS。"""
        with self._lock_for(patch.task_id):
            depth = self._graph._node_depth(patch.task_id, patch.node_id)
            cfg = self._graph._execution_config(patch.task_id)
            max_depth = cfg["MAX_DEPTH"]
            if depth < max_depth:
                graph = self._graph.query_task_dashboard(patch.task_id)
                new_children = self._planner.plan(graph)
                if new_children:
                    self._graph.add_task_nodes(new_children, patch.node_id)
                    self._dispatch_and_run(patch.task_id)
            else:
                self._escalate_bbs(patch.task_id, patch.node_id)

    # ===== on_harness =====
    def on_harness(self, patch: TaskNodePatch) -> None:
        """Harness 旁路:RUNNING 超时/崩溃 → 复位回 PENDING(update_task_node_info)→ 正常 dispatch 重投。
        不抢正向驱动;不直接写 HUNG(STUCK 走 on_miss/on_fail 升 BBS 链路上限判)。"""
        with self._lock_for(patch.task_id):
            self._graph.update_task_node_info(patch)
            self._dispatch_and_run(patch.task_id)

    # ===== 升 BBS =====
    def _escalate_bbs(self, task_id: str, node_id: str) -> None:
        """自动升 BBS(无人工挡板):remove_subtree(删 xx_node+子树)+ 经图级写口 loop_round++ + 标 BBS。
        loop_round ≥ BBS_MAX_DEPTH → STUCK → graph HUNG(人介入)。图级写收口 update_task_graph_info(SSOT)。
        BBS 实际投递归 runner BBS 模态(下次 dispatch→start_run;无独立 BbsMarketPort)。"""
        self._graph.remove_subtree(task_id, node_id)
        graph = self._graph.update_task_graph_info(
            task_id, TaskGraphPatch(loop_round_increment=1)
        )
        cfg = self._graph._execution_config(task_id)
        bbs_max = cfg["BBS_MAX_DEPTH"]
        if graph.loop_round >= bbs_max:
            # STUCK → graph HUNG(人介入);node 已删,标 graph 级(经图级写口)
            self._graph.update_task_graph_info(
                task_id,
                TaskGraphPatch(
                    status=Status.HUNG,
                    extend_props_patch={"hung_reason": "stuck"},
                ),
            )
        else:
            self._graph.update_task_graph_info(
                task_id,
                TaskGraphPatch(extend_props_patch={"bbs_mode": True}),
            )

    # ===== 派发+执行(通用)=====
    def _dispatch_and_run(self, task_id: str) -> None:
        """查 PENDING 可派发节点 → dispatcher.dispatch 返填执行者 → HIT_MULTI_BOTS 拉群(runner.form_coop_group)
        填 assignee → 落库 RUNNING + start_run;MISS(miss_events)→ on_miss。"""
        pending = self._graph.query_task_nodes(
            task_id, TaskNodeQueryCriteria(status=Status.PENDING)
        )
        if not pending:
            return
        dispatched = self._dispatcher.dispatch(pending)
        to_run: list[TaskNode] = []
        for node in dispatched:
            miss = node.run_info.extend_props.get("miss_events")
            gf = node.run_info.extend_props.pop("pending_group_formation", None)
            if gf is not None:
                # HIT_MULTI_BOTS:runner 拉 group 填 assignee
                gid = self._runner.form_coop_group(gf)
                node.run_info.assignee = gid
            if node.run_info.run_mode and node.run_info.assignee:
                # 有执行者 → 落库 RUNNING
                self._graph.update_task_node_info(
                    TaskNodePatch(
                        task_id=task_id,
                        node_id=node.node_id,
                        status=Status.RUNNING,
                        run_mode=node.run_info.run_mode,
                        assignee=node.run_info.assignee,
                    )
                )
                to_run.append(node)
            elif miss:
                # MISS → on_miss
                self.on_miss(
                    TaskNodePatch(
                        task_id=task_id,
                        node_id=node.node_id,
                        extend_props_patch={"miss_events": miss},
                    )
                )
        if to_run:
            self._runner.start_run(to_run)

    def _maybe_finish_graph(self, task_id: str) -> None:
        """根 PASS(终验回投通过)→ 全图 DONE。图级写收口 update_task_graph_info(SSOT 唯一网关)。"""
        self._graph.update_task_graph_info(
            task_id,
            TaskGraphPatch(
                status=Status.DONE,
                output_patch={"result": "all_done"},
            ),
        )
