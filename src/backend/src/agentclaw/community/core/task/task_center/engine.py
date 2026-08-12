"""ExecutionEngine 内部编排核(事件驱动 + 状态条件触发)。对齐 plan §3.0。

非独立模块,TaskService 内部实现细节,对外不暴露。
"""
from __future__ import annotations

from agentclaw.community.core.task.domain.models import NodeOpResult, TaskNodePatch


class ExecutionEngine:
    """事件驱动 + 状态条件触发协调 plan/graph/dispatch/execution。

    持 TaskGraphService/TaskPlanner/TaskDispatcher/TaskRunner;BBS 模态统一归
    TaskRunner 一条 run_mode="bbs" 分支。on_* 入参统一收口 TaskNodePatch。
    按事件 + 状态条件(a/b/c + plan 三条件)分段协调(无 single drive fixpoint 泵)。
    """

    def __init__(self, graph, planner, dispatcher, runner):
        self._graph = graph
        self._planner = planner
        self._dispatcher = dispatcher
        self._runner = runner

    def on_execute(self, task_id: str) -> None:
        """execute 事件:initialize_graph(根 PENDING)→ 触发首帧推进:
        条件 a(根 PENDING)→ plan → add_task_nodes(第一层,根进 PLANNING)
        → dispatch(返填执行者 list[TaskNode])→ update_task_node_info(RUNNING)→ start_run。"""
        raise NotImplementedError

    def on_report(self, patch: TaskNodePatch) -> NodeOpResult:
        """回投事件:patch 内含 (task_id,node_id)+ 唯一翻态依据 acceptance_result + output_patch。
        update_task_node_info 翻态(+fold output):
        PASS→DONE:查结构父 P;P=PLANNING 且全部结构子(本批兄弟)DONE ∧ 无 RUNNING(决策C)
          → 委托 plan→decompose(P):产新子→add_task_nodes→dispatch→落库→start_run;
          decompose 返 [](gap 已闭)→ 非根 P→DONE(传播)/ 根→终验。
        FAIL+gaps→FAILED:深度闸门(<MAX 放行)→ 条件 b → plan → add_task_nodes(补救子)→ dispatch → start_run。
        返回 NodeOpResult(prev/new_status)供适配层 ack。"""
        raise NotImplementedError

    def on_miss(self, patch: TaskNodePatch) -> None:
        """dispatcher MISS → 节点仍 PENDING(miss_events 已填):
        <MAX → plan→add_task_nodes(拆细)→ 消费 miss_events → dispatch;
        ≥MAX → 自动升 BBS:remove_subtree(删 xx_node 及其下整个子树;前提:所有子都 MISS、
          没走 RUNNING)+ loop_round++ + 标 BBS(挂任务广场)。BBS bot 认领后自算 gap+规划子任务
          → add_task_nodes(run_mode="bbs")→ 上报经 on_report。loop_round 达 BBS_MAX_DEPTH
          仍执行不下去 → STUCK → HUNG(stuck)。"""
        raise NotImplementedError

    def on_harness(self, patch: TaskNodePatch) -> None:
        """Harness 旁路:RUNNING 超时/崩溃 → 复位回 PENDING(update_task_node_info)→ 正常 dispatch 重投。
        不抢正向驱动;不直接写 HUNG(STUCK 走 on_miss 升 BBS 链路上限判)。"""
        raise NotImplementedError
