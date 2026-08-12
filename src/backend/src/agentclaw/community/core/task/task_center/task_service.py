"""TaskService facade(2 API):系统唯一对外入口,内部持 ExecutionEngine 编排核。对齐 plan §3.7。

组合根:注入 graph/planner/dispatcher/runner(+ 可选 harness/verify_port/bbs_market),内部构造
ExecutionEngine(verifier_port/bbs_market 缺省用 in-process no-op seam);回投经 ``callback``(TaskLoopCallback)
适配层 → 编排核 on_report(非 facade 直暴露)。Avernet 发 stub/singlebox;corp adapter 红线(ocb 仓)。
"""
from __future__ import annotations

import asyncio

from agentclaw.community.api.task.task_service import TaskServiceProtocol
from agentclaw.community.core.task.domain.models import (
    TaskExecutionGraph,
    TaskInfo,
    TaskOpResult,
)
from agentclaw.community.core.task.task_center.engine import (
    BbsMarketPort,
    ExecutionEngine,
    OwnerBotVerifyPort,
)
from agentclaw.community.core.task.task_runner.callback_adapter import (
    CallbackAdapter,
    TaskLoopCallback,
)


class _NoopVerifyPort:
    """Avernet 默认终验 seam(不驱动):测试/生产注入真实 OwnerBotVerifyPort。"""

    def request_verify(self, task_id: str, node_id: str) -> None:  # noqa: D401
        """no-op:owner bot 终验由 corp adapter 注入;缺省不发起。"""
        return None


class _NoopBbsMarket:
    """Avernet 默认 BBS 广场 seam(不挂单):测试/生产注入真实 BbsMarketPort。"""

    def publish_task(self, task_id: str) -> None:  # noqa: D401
        """no-op:任务广场由 corp adapter 注入;缺省不挂单。"""
        return None


class TaskService(TaskServiceProtocol):
    """对外 facade(2 API);内部持 ExecutionEngine 编排核 + TaskGraphService + Planner + Dispatcher +
    Runner + Harness(可选)+ TaskLoopCallback(回投适配)。

    引擎不识别"终验节点":终验=根节点验收,由 owner bot skill 回投经 ``callback``→``on_report``。
    """

    def __init__(
        self,
        graph,
        planner,
        dispatcher,
        runner,
        harness=None,
        *,
        verify_port: OwnerBotVerifyPort | None = None,
        bbs_market: BbsMarketPort | None = None,
    ) -> None:
        """graph: TaskGraphService;planner: TaskPlanner;dispatcher: TaskDispatcher;runner: TaskRunner;
        harness: TaskHarness | None;verify_port/bbs_market: 编排核 seam(缺省 no-op,Avernet 默认)。"""
        self._graph = graph
        self._planner = planner
        self._dispatcher = dispatcher
        self._runner = runner
        self._harness = harness
        self._verify_port = verify_port or _NoopVerifyPort()
        self._bbs_market = bbs_market or _NoopBbsMarket()
        # 组合根:构造编排核(注入全部 seam)
        self._engine = ExecutionEngine(
            graph=self._graph,
            planner=self._planner,
            dispatcher=self._dispatcher,
            runner=self._runner,
            verify_port=self._verify_port,
            bbs_market=self._bbs_market,
        )
        # 回投适配层:执行实体 PUSH → 适配 → 编排核 on_report
        self._callback = TaskLoopCallback(CallbackAdapter(), self._engine)
        # harness 复位重投入口回填(编排核已建,harness 才能拿到 on_harness)
        if self._harness is not None:
            self._harness.set_on_harness(self._engine.on_harness)

    @property
    def callback(self) -> TaskLoopCallback:
        """供执行实体(bot workflow / bcn 协作群)PUSH 回投的入口(适配层 → 编排核 on_report)。"""
        return self._callback

    @property
    def engine(self) -> ExecutionEngine:
        """编排核引用(测试/编排观测用;生产不应跨 facade 直接驱动)。"""
        return self._engine

    async def execute(self, task_info: TaskInfo) -> TaskOpResult:
        """提交执行任务:initialize_graph(根 PENDING)→ 编排核 on_execute(首帧推进:
        条件 a 根 PENDING → plan → add_task_nodes → dispatch → start_run)。返回 TaskOpResult(含 run_id)。"""
        graph = self._graph.initialize_graph(task_info)
        task_id = task_info.task_spec.metadata.task_id
        if self._harness is not None:
            self._harness.register(task_id)
        self._engine.on_execute(task_id)
        return TaskOpResult(task_id=task_id, success=True, run_id=graph.run_id)

    def get_task_dashboard(
        self, task_id: str, node_id: str | None = None
    ) -> TaskExecutionGraph:
        """任务执行详情可视化(整图或按 node_id 子树投影),只读。"""
        return self._graph.query_task_dashboard(task_id, node_id)


def run_execute(facade: TaskService, task_info: TaskInfo) -> TaskOpResult:
    """同步执行 ``execute``(无事件循环依赖的调用方/单测用)。"""
    return asyncio.new_event_loop().run_until_complete(facade.execute(task_info))
