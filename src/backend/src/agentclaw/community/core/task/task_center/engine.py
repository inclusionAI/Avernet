"""ExecutionEngine 内部编排核(事件驱动 + 状态条件触发)。对齐 plan §3.0。

非独立模块,TaskService 内部实现细节,对外不暴露。零参构造,自建 planner/dispatcher/runner
(内置策略池 + stub 投递);corp 子类经 ocb 仓覆写工厂方法注入真实策略/投递后端。
验收 100% 走 on_report 回投(engine 不主动验,无 OwnerBotVerifyPort);BBS 投递归 runner BBS 模态
(无 BbsMarketPort,升 BBS 只翻图态 bbs_mode)。零 case 知识:engine 不含任何节点名字面量。
"""
from __future__ import annotations

from agentclaw.community.core.task.domain.models import NodeOpResult, TaskNodePatch


class ExecutionEngine:
    """事件驱动 + 状态条件触发协调 plan/graph/dispatch/execution。

    零参构造:``__init__(graph)`` 自建 TaskPlanner/TaskDispatcher/TaskRunner(内置 PlanningStrategy/
    DispatchStrategy 策略池 + DeliveryPort stub 投递);corp 子类覆写 ``_build_*`` 工厂方法注入真实
    策略/投递后端(ocb 仓)。on_* 入参统一收口 TaskNodePatch。按事件 + 状态条件(a/b/c + plan 三条件)
    分段协调(无 single drive fixpoint 泵)。同 task_id 串行,跨 task 并行。loop_round 仅升 BBS 时 ++。
    验收 100% 走 on_report 回投(无 OwnerBotVerifyPort);BBS 投递归 runner BBS 模态(无 BbsMarketPort)。
    协程化(任务执行耗时):on_* 全 `async def`;`plan`/`dispatch`(corp LLM/catalog IO)锁内 `await`,
    投递/拉群 IO 锁外 `await`(详 Avernet spec/README)。`threading.RLock` 适用一次性事件循环/跨线程回调模型;
    corp 单持久 loop 并发同 task 需切 `asyncio.Lock`(ocb 仓定)。
    """

    def __init__(self, graph) -> None:
        """graph: TaskGraphService。零参自建 planner/dispatcher/runner(内置策略池+stub 投递);
        corp 子类覆写 ``_build_*`` 工厂方法注入真实策略/投递后端。构造接线实现待后续 PR。"""
        self._graph = graph
        # planner/dispatcher/runner 自建(首批壳,接线待后续 PR)
        self._planner = None       # type: ignore[assignment]
        self._dispatcher = None    # type: ignore[assignment]
        self._runner = None        # type: ignore[assignment]

    async def on_execute(self, task_id: str) -> None:
        """execute 事件:initialize_graph(根 PENDING)→ 触发首帧推进:
        条件 a(根 PENDING)→ plan → add_task_nodes(第一层,根进 PLANNING)
        → dispatch(返填执行者 list[TaskNode])→ update_task_node_info(RUNNING)→ start_run。"""
        raise NotImplementedError

    async def on_report(self, patch: TaskNodePatch) -> NodeOpResult:
        """回投事件:patch 内含 (task_id,node_id)+ 唯一翻态依据 acceptance_result + output_patch。
        update_task_node_info 翻态(+fold output):
        PASS→DONE:查结构父 P;P=PLANNING 且全部结构子(本批兄弟)DONE ∧ 无 RUNNING(决策C)
          → 委托 plan→decompose(P):产新子→add_task_nodes→dispatch→落库→start_run;
          decompose 返 [](gap 已闭)→ 非根 P→DONE(传播)/ 根保持 PLANNING 等 owner bot 终验回投。
        FAIL+gaps→FAILED:深度闸门(<MAX 放行)→ 条件 b → plan → add_task_nodes(补救子)→ dispatch → start_run。
        验收 100% 走回投(engine 不主动验,无 OwnerBotVerifyPort)。返回 NodeOpResult 供适配层 ack。"""
        raise NotImplementedError

    async def on_miss(self, patch: TaskNodePatch) -> None:
        """dispatcher MISS → 节点仍 PENDING(miss_events 已填):
        <MAX → plan→add_task_nodes(拆细)→ 消费 miss_events → dispatch;
        ≥MAX → 自动升 BBS:remove_subtree(删 xx_node 及其下整个子树;前提:所有子都 MISS、
          没走 RUNNING)+ loop_round++ + 标 BBS(标 bbs_mode=True;BBS 投递归 runner BBS 模态,
          无 BbsMarketPort)。BBS bot 认领后自算 gap+规划子任务 → add_task_nodes(run_mode="bbs")
          → 上报经 on_report。loop_round 达 BBS_MAX_DEPTH 仍执行不下去 → STUCK → HUNG(stuck)。"""
        raise NotImplementedError

    async def on_harness(self, patch: TaskNodePatch) -> None:
        """Harness 旁路:RUNNING 超时/崩溃 → 复位回 PENDING(update_task_node_info)→ 正常 dispatch 重投。
        不抢正向驱动;不直接写 HUNG(STUCK 走 on_miss 升 BBS 链路上限判)。"""
        raise NotImplementedError
