"""TaskService facade(2 API):系统唯一对外入口,内部持 ExecutionEngine 编排核。对齐 plan §3.7。

零参 facade:用户只传 TaskInfo。内部 ``_build_engine`` 工厂方法自建 ExecutionEngine(零参,自建
planner/dispatcher/runner 内置策略池 + stub 投递);corp 子类覆写工厂方法注入真实策略/投递后端(ocb 仓)。
回投经 ``callback``(TaskLoopCallback)适配层 → 编排核 on_report(非 facade 直暴露)。
Avernet 发 stub/singlebox;corp adapter 红线在 ocb 仓。engine 对调用方不可见(无 engine property)。
"""
from __future__ import annotations

import asyncio

from agentclaw.community.api.task.task_service import TaskServiceProtocol
from agentclaw.community.core.task.domain.models import (
    TaskExecutionGraph,
    TaskInfo,
    TaskOpResult,
)
from agentclaw.community.core.task.task_center.engine import ExecutionEngine
from agentclaw.community.core.task.task_runner.callback_adapter import (
    CallbackAdapter,
    TaskLoopCallback,
)


class TaskService(TaskServiceProtocol):
    """对外 facade(2 API);内部持 ExecutionEngine 编排核 + TaskGraphService + Harness(可选)+ TaskLoopCallback。

    验收 100% 走回调回投;engine 不主动验,无 verify/bbs port。engine 对调用方不可见(无 property)。
    """

    def __init__(self, graph, harness=None) -> None:
        """graph: TaskGraphService;harness: TaskHarness | None(旁路复位,可选)。
        corp 接真实策略/投递:子类覆写 ``_build_engine`` 返回注入真实策略池/投递后端的 CorpEngine。"""
        self._graph = graph
        self._harness = harness
        self._engine = self._build_engine()
        # 回投适配层:执行实体 PUSH → 适配 → 编排核 on_report
        self._callback = TaskLoopCallback(CallbackAdapter(), self._engine)
        # harness 复位重投入口回填(编排核已建,harness 才能拿到 on_harness)
        if self._harness is not None:
            self._harness.set_on_harness(self._engine.on_harness)

    def _build_engine(self) -> ExecutionEngine:
        """(corp 覆写 seam)构造编排核。默认 ExecutionEngine(graph)自建 stub 策略池/投递;
        corp 子类返回 CorpEngine(覆写 _build_planner/_build_dispatcher/_build_runner 注入真实实现)。"""
        return ExecutionEngine(self._graph)

    @property
    def callback(self) -> TaskLoopCallback:
        """供执行实体(bot workflow / bcn 协作群)PUSH 回投的入口(适配层 → 编排核 on_report)。"""
        return self._callback

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
