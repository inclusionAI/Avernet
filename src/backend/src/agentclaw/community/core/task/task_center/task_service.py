"""TaskService facade(2 API):系统唯一对外入口,内部持 ExecutionEngine 编排核。对齐 plan §3.7。

零参 facade:用户只传 TaskInfo。内部 ``_build_engine`` 工厂方法自建 ExecutionEngine(零参,自建
planner/dispatcher/runner 内置策略池 + stub 投递);corp 子类覆写工厂方法注入真实策略/投递后端(ocb 仓)。
回投经 ``callback``(TaskLoopCallback)适配层 → 编排核 on_report(非 facade 直暴露)。
engine 对调用方不可见(无 engine property)。
"""
from __future__ import annotations

from agentclaw.community.core.task.domain.models import (
    TaskExecutionGraph,
    TaskInfo,
    TaskOpResult,
)


class TaskService:
    """对外 facade(2 API);内部持 ExecutionEngine 编排核 + TaskGraphService + Harness(可选)+ TaskLoopCallback。

    零参构造:``__init__(graph, harness=None)``;``_build_engine`` 自建 ExecutionEngine(零参自建
    planner/dispatcher/runner 内置策略池+stub 投递);corp 子类覆写 ``_build_engine`` 返回 CorpEngine
    (覆写 ``_build_*`` 注入真实策略/投递)。验收 100% 走回投(无 verify/bbs port)。engine 对调用方不可见。
    """

    def __init__(self, graph, harness=None) -> None:
        """graph: TaskGraphService;harness: TaskHarness | None(旁路复位,可选)。
        零参 facade:内部 `_build_engine` 工厂方法自建 ExecutionEngine(首批壳,接线待后续 PR)。"""
        self._graph = graph
        self._harness = harness
        self._engine = None  # ExecutionEngine(首批壳,接线待后续 PR)

    async def execute(self, task_info: TaskInfo) -> TaskOpResult:
        """graph.initialize_graph(task_info)(根 PENDING)→ 编排核 on_execute(task_id)
        → 首帧推进(条件 a:根 PENDING → plan → add_task_nodes → dispatch → start_run)。"""
        raise NotImplementedError

    def get_task_dashboard(
        self, task_id: str, node_id: str | None = None
    ) -> TaskExecutionGraph:
        """graph.query_task_dashboard(task_id, node_id);只读。"""
        raise NotImplementedError
