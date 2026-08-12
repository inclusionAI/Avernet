"""对外任务服务契约(任务中心 TaskService facade)。对齐 plan §3.7 + 任务中心文档 yugg6dorsxo8sgmp。"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from agentclaw.community.core.task.domain.models import (
    TaskExecutionGraph,
    TaskInfo,
    TaskOpResult,
)


@runtime_checkable
class TaskServiceProtocol(Protocol):
    """系统唯一对外入口(2 API)。facade 内部由 ExecutionEngine 编排核协调
    TaskGraphService/TaskPlanner/TaskDispatcher/TaskRunner。"""

    async def execute(self, task_info: TaskInfo) -> TaskOpResult:
        """提交执行任务:initialize_graph(根 PENDING)→ 编排核 on_execute
        首帧推进(plan→add_task_nodes→dispatch→start_run)。返回 TaskOpResult。"""
        ...

    def get_task_dashboard(
        self, task_id: str, node_id: str | None = None
    ) -> TaskExecutionGraph:
        """任务执行详情可视化(整图或按 node_id 子树投影),只读。"""
        ...
