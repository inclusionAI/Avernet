"""TaskService facade(2 API),内部持 ExecutionEngine 编排核。对齐 plan §3.7。"""
from __future__ import annotations

from agentclaw.community.core.task.domain.models import (
    TaskExecutionGraph,
    TaskInfo,
    TaskOpResult,
)


class TaskService:
    """对外 facade(2 API);内部持 ExecutionEngine 编排核 + TaskGraphService +
    TaskPlanner + TaskDispatcher + TaskRunner + TaskHarness(可选)。"""

    def __init__(self, graph, planner, dispatcher, runner, harness=None):
        """graph: TaskGraphService;planner: TaskPlanner;dispatcher: TaskDispatcher;
        runner: TaskRunner;harness: TaskHarness | None。ExecutionEngine 内部构造,
        持上述依赖做事件驱动编排(首批壳,接线待 DI)。"""
        self._graph = graph
        self._planner = planner
        self._dispatcher = dispatcher
        self._runner = runner
        self._harness = harness
        self._engine = None  # ExecutionEngine(首批壳,接线待 DI)

    async def execute(self, task_info: TaskInfo) -> TaskOpResult:
        """graph.initialize_graph(task_info)(根 PENDING)→ 编排核 on_execute(task_id)
        → 首帧推进(条件 a:根 PENDING → plan → add_task_nodes → dispatch → start_run)。"""
        raise NotImplementedError

    def get_task_dashboard(
        self, task_id: str, node_id: str | None = None
    ) -> TaskExecutionGraph:
        """graph.query_task_dashboard(task_id, node_id);只读。"""
        raise NotImplementedError
