"""P2 — ExecutionEngine,TaskCenter 内部编排核 (方案 C, v4).

设计: ``design-review.md`` §2.0、tasks.md P2。本类**非独立模块、无对外 API**,
由 ``TaskCenter`` facade (``execute`` / ``report_task_execution``) 委托单步 ``drive``。
统一驱动 ``TaskExecutionGraph`` 的反应式 loop:
    传播 DONE -> 深度闸门 (FAIL) -> 步进 plan -> dispatch -> MISS 处理 -> 不动点 -> 终验。

v4 变更 (相对 v3):
- **删 SPAWNING**: "委托中" = 有分解子 (结构派生),传播规则 = 有子 ∧ 子全 DONE ∧ 非DONE → DONE。
- **删 reroute 内存态 + cascade 自动调用**: model B 下 FAIL/MISS 针对该节点产子挂它下,
  下游在 deps 满足前未入图,无需复位下游。``cascade_rollback`` 仅人工 ``rollback_to_node``。
- **FAIL/MISS 同构**: 均走 ``plan(graph)`` 读图自发现 (FAIL 读 gaps,MISS 读 miss_events),
  产子挂该节点下。
- **步进式 plan**: 每步产当前 gap 对应的下一层,不铺满。
- **深度闸门是引擎决策**: FAIL/MISS 拆解前引擎查 depth,≥MAX → HUNG。
- **MISS 信号 = miss_events** (append+consume): _handle_miss 写 miss_event → plan 产子 →
  add_task_graph → 消费 miss_event,全在 drive 同 pass 内。
- **drive 串行化**: 同 task_id 可重入锁,跨 task 并行。
- **terminal PASS**: 全可执行 DONE + 终验 verdict=PASS。

依赖反转: 持 ``TaskGraphStore`` + ``PlannerPort`` + ``DispatcherPort`` (必填),
``BbsExecutorPort`` 可选 (人工确认升 BBS 时用;M1 可缺省)。
"""
from __future__ import annotations

import threading
from agentclaw.community.core.task_goal_driven.graph_store import (
    TaskGraphStore,
)
from agentclaw.community.core.task_goal_driven.models import (
    AcceptanceResult,
    AcceptanceVerdict,
    DispatchKind,
    NodeOpResult,
    NodeQueryCriteria,
    NodeRuntimePatch,
    RunMode,
    Status,
    TaskExecutionGraph,
    TaskNode,
)
from agentclaw.community.core.task_goal_driven.protocols import (
    BbsExecutorPort,
    DispatcherPort,
    PlannerPort,
)

_DEFAULT_MAX_DEPTH = 3


class ExecutionEngine:
    """统一驱动 TaskExecutionGraph 的反应式 loop。"""

    def __init__(
        self,
        store: TaskGraphStore,
        planner: PlannerPort,
        dispatcher: DispatcherPort,
        bbs_executor: BbsExecutorPort | None = None,
    ) -> None:
        self._store = store
        self._planner = planner
        self._dispatcher = dispatcher
        self._bbs = bbs_executor
        # v4: 同 task_id 可重入锁 (drive 串行化),跨 task 并行
        self._locks: dict[str, threading.RLock] = {}
        self._locks_guard = threading.Lock()

    def _lock_for(self, task_id: str) -> threading.RLock:
        """获取 (或创建) task_id 专属可重入锁."""
        with self._locks_guard:
            if task_id not in self._locks:
                self._locks[task_id] = threading.RLock()
            return self._locks[task_id]

    # ------------------------------------------------------------------
    # facade 委托入口
    # ------------------------------------------------------------------

    def drive(self, task_id: str) -> None:
        """反应式泵 (幂等,串行化;由 execute / report 触发).

        一次 ``drive`` 推进到不动点:
        传播 DONE → 深度闸门 → 步进 plan → dispatch → MISS 处理 → 重复 → 终验。
        """
        with self._lock_for(task_id):
            graph = self._store.get_graph(task_id)
            if graph.status in {Status.DONE, Status.FAILED}:
                return

            # fixpoint 泵
            while True:
                self._propagate_done(task_id)
                self._gate_failed_depth(task_id)

                # 步进 plan: 读图产新节点 (FAIL 补救 / 前向下一层)
                graph = self._store.get_graph(task_id)
                new_nodes = self._planner.plan(graph)
                if new_nodes:
                    parent_id = self._infer_parent(task_id, graph, new_nodes)
                    self._store.add_task_graph(task_id, parent_id, new_nodes)
                    # 补救 (非根 parent) → loop_round++
                    if parent_id != self._root_of(graph).node_id:
                        self._store.bump_loop_round(task_id)

                # 就绪扫描 + 派发
                ready = self._store.query_nodes(
                    task_id, NodeQueryCriteria(status=Status.PENDING, dependencies_satisfied=True)
                )
                # v4: 排除有 miss_events 的节点 (正在被 _handle_miss 处理中)
                ready = [n for n in ready if not n.run_info.extend_props.get("miss_events")]

                miss_handled = False
                if ready:
                    outcomes = self._dispatcher.dispatch(ready)
                    for oc in outcomes:
                        if oc.kind == DispatchKind.MISS:
                            node = next((n for n in ready if n.node_id == oc.node_id), None)
                            if node is not None:
                                self._handle_miss(task_id, node)
                                miss_handled = True

                # 无进展 → 退出泵
                if not new_nodes and not ready and not miss_handled:
                    break

            # 终验判定
            self._maybe_finalize(task_id)

    def report(
        self,
        task_id: str,
        node_id: str,
        output_patch: dict | None = None,
        acceptance_result: AcceptanceResult | None = None,
    ) -> NodeOpResult:
        """执行实体/验收器回投 -> 落 output/acceptance;PASS->DONE;FAIL+gaps->补救;无gaps->HUNG."""
        verdict = acceptance_result.verdict if acceptance_result else AcceptanceVerdict.PASS
        if verdict == AcceptanceVerdict.PASS:
            patch = NodeRuntimePatch(
                status=Status.DONE,
                output_patch=output_patch,
                acceptance_result=acceptance_result,
            )
        else:
            # FAIL: 有 gaps → FAILED (可补救); 无 gaps → HUNG (超时/崩溃,不补救)
            has_gaps = acceptance_result is not None and bool(acceptance_result.gaps)
            patch = NodeRuntimePatch(
                status=Status.FAILED if has_gaps else Status.HUNG,
                output_patch=output_patch,
                acceptance_result=acceptance_result,
            )
        result = self._store.patch_node_runtime_info(task_id, node_id, patch)
        self.drive(task_id)
        return result

    def report_stuck(
        self, task_id: str, node_id: str, output_patch: dict | None = None
    ) -> NodeOpResult:
        '''ExecutorResult.STUCK 回报 -> HUNG (人工确认后 escalate_to_bbs 升 BBS 接力).

        STUCK = 连续 N 轮无进展 / 不可恢复 / 子 SLA 时限 (阈值由 P5 实现期定)。
        HUNG 后 drive 不会重复派发 (ready 仅扫 PENDING 无分解子);图终结闸门也因 HUNG 不满足,
        直到 BBS 接力 PASS 解除。
        '''
        return self._store.patch_node_runtime_info(
            task_id, node_id, NodeRuntimePatch(status=Status.HUNG, output_patch=output_patch)
        )

    def escalate_to_bbs(self, task_id: str, node_id: str) -> str:
        """人工确认后升 BBS:算投影 -> run_bbs -> 置 RUNNING (突破 HUNG)."""
        if self._bbs is None:
            raise RuntimeError("no BbsExecutorPort configured; cannot escalate to BBS")
        node = self._store.get_node(task_id, node_id)
        projection = self._store.compute_output_projection(task_id, node_id)
        runtime_task_id = self._bbs.run_bbs(node, projection)
        self._store.patch_node_runtime_info(
            task_id,
            node_id,
            NodeRuntimePatch(
                status=Status.RUNNING,
                run_mode=RunMode.BBS,
                extend_props_patch={"runtime_task_id": runtime_task_id, "bbs_escalated": True},
            ),
        )
        return runtime_task_id

    # ------------------------------------------------------------------
    # MISS 深度闸门 + 补救 (与 FAIL 同构)
    # ------------------------------------------------------------------

    def _handle_miss(self, task_id: str, node: TaskNode) -> None:
        """MISS → 深度闸门 → plan 产子挂该节点下 → 消费 miss_event (同 pass 内完成)."""
        depth = self._store.node_depth(task_id, node.node_id)
        max_depth = self._max_depth(task_id)
        if depth >= max_depth:
            self._store.patch_node_runtime_info(
                task_id, node.node_id, NodeRuntimePatch(status=Status.HUNG)
            )
            return
        # 写 miss_event (append-only)
        current = self._store.get_node(task_id, node.node_id)
        existing = current.run_info.extend_props.get("miss_events", [])
        self._store.patch_node_runtime_info(
            task_id, node.node_id,
            NodeRuntimePatch(extend_props_patch={"miss_events": [*existing, "no bot cover"]}),
        )
        # plan 读 miss_event 产子
        sub = self._planner.plan(self._store.get_graph(task_id))
        if not sub:
            # 无法拆解又无法搜推 → HUNG (消费 miss_event 避免死循环)
            self._store.patch_node_runtime_info(
                task_id, node.node_id,
                NodeRuntimePatch(status=Status.HUNG, extend_props_patch={"miss_events": []}),
            )
            return
        self._store.add_task_graph(task_id, node.node_id, sub)
        self._store.bump_loop_round(task_id)
        # 消费 miss_event (add_task_graph 后节点有分解子,不再被 ready 扫描)
        self._store.patch_node_runtime_info(
            task_id, node.node_id,
            NodeRuntimePatch(extend_props_patch={"miss_events": []}),
        )

    # ------------------------------------------------------------------
    # FAIL 深度闸门
    # ------------------------------------------------------------------

    def _gate_failed_depth(self, task_id: str) -> None:
        """深度闸门: FAILED+gaps 节点 depth ≥ MAX → HUNG (引擎决策,不进 planner)."""
        graph = self._store.get_graph(task_id)
        max_depth = self._max_depth(task_id)
        for n in graph.tasks:
            if (n.status == Status.FAILED
                    and n.run_info.acceptance_result
                    and n.run_info.acceptance_result.gaps):
                if self._store.node_depth(task_id, n.node_id) >= max_depth:
                    self._store.patch_node_runtime_info(
                        task_id, n.node_id, NodeRuntimePatch(status=Status.HUNG)
                    )

    # ------------------------------------------------------------------
    # 传播 DONE & 终验
    # ------------------------------------------------------------------

    def _propagate_done(self, task_id: str) -> None:
        """有分解子 ∧ 子全 DONE ∧ 本节点非 DONE ∧ 非根 → DONE (覆盖 FAIL 补救、MISS 拆解).

        v4: **跳过根** (无 depends_on)。根的 DONE 由 _maybe_finalize 显式设定,
        不是传播 — 因为步进式规划下根会持续增子,传播会过早 DONE。
        """
        changed = True
        while changed:
            changed = False
            graph = self._store.get_graph(task_id)
            for n in graph.tasks:
                if n.status == Status.DONE:
                    continue
                if not n.depends_on:  # 根: 跳过 (由 finalize 设定)
                    continue
                kids = self._store.decomposition_children(task_id, n.node_id)
                if kids and all(k.status == Status.DONE for k in kids):
                    self._store.patch_node_runtime_info(
                        task_id, n.node_id, NodeRuntimePatch(status=Status.DONE)
                    )
                    changed = True

    def _maybe_finalize(self, task_id: str) -> None:
        """terminal PASS = 全非根节点 DONE ∧ 终验 verdict=PASS;否则不静默空转.

        v4: 根不靠传播 (步进式下持续增子);由 finalize 检查全非根 DONE 后显式设 root DONE。
        """
        graph = self._store.get_graph(task_id)
        if graph.status in {Status.DONE, Status.FAILED}:
            return
        exec_nodes = [n for n in graph.tasks if n.depends_on]
        if not exec_nodes:
            return  # 只有根,无可执行节点
        if not all(n.status == Status.DONE for n in exec_nodes):
            return  # 非根节点未全 DONE
        # 显式终验 verdict 校验 (防脏写)
        verify_node = self._find_terminal_verify(graph)
        if verify_node is not None:
            ar = verify_node.run_info.acceptance_result
            if ar is not None and ar.verdict != AcceptanceVerdict.PASS:
                return  # 终验非 PASS → 不终结
        # 全条件满足 → root DONE + graph DONE
        root = self._root_of(graph)
        self._store.patch_node_runtime_info(task_id, root.node_id, NodeRuntimePatch(status=Status.DONE))
        self._store.patch_graph_status(task_id, Status.DONE)

    def _find_terminal_verify(self, graph: TaskExecutionGraph) -> TaskNode | None:
        """找到终验节点 (最深的可执行叶;参考实现: 最后入图的 scope=task 叶)."""
        exec_nodes = [n for n in graph.tasks if n.depends_on]
        if not exec_nodes:
            return None
        # 启发式: 最后入图的非根节点 (步进式下终验节点最后入图;节点名由 decomposer 产出)
        last = exec_nodes[-1]
        return last

    # ------------------------------------------------------------------
    # 内部工具
    # ------------------------------------------------------------------

    def _infer_parent(self, task_id: str, graph: TaskExecutionGraph,
                       new_nodes: list[TaskNode]) -> str:
        """推断 plan 产出的结构父 (add_task_graph 的 parent_node_id).

        - FAIL 补救: 产出节点的 parent 指向 FAILED 节点 → 用它。
        - 前向步进: 产出节点的 parent 指向 DONE 叶 (数据依赖) → 结构父是根。
        """
        by_id = {n.node_id: n for n in graph.tasks}
        produced_parents: set[str] = set()
        for n in new_nodes:
            produced_parents.update(n.depends_on)
        # FAIL 补救: 某个 parent 是 FAILED → 用它
        for pid in produced_parents:
            if pid in by_id and by_id[pid].status == Status.FAILED:
                return pid
        # 前向步进 → 结构父是根
        return self._root_of(graph).node_id

    def _root_of(self, graph: TaskExecutionGraph) -> TaskNode:
        for n in graph.tasks:
            if not n.depends_on:
                return n
        return graph.tasks[0]

    def _max_depth(self, task_id: str) -> int:
        cfg = self._store.execution_config(task_id)
        val = cfg.get("MAX_DEPTH", _DEFAULT_MAX_DEPTH)
        try:
            return int(val)
        except (TypeError, ValueError):
            return _DEFAULT_MAX_DEPTH
