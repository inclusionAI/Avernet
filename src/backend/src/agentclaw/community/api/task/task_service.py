"""对外任务服务契约(任务中心 TaskService facade)。对齐 plan §3.7 + 任务中心文档 yugg6dorsxo8sgmp。

Re-export only. The Protocol is defined in its owning core module
(``core/task/task_service_protocol.py``) so the concrete service can
inherit it without a ``core -> api`` waiver; adapters keep importing
it from here.
"""

from __future__ import annotations

from agentclaw.community.core.task.task_service_protocol import (
    AcceptanceResult,
    NodeOpResult,
    TaskExecutionGraph,
    TaskInfoRecord,
    TaskInfoRequest,
    TaskNode,
    TaskOpResult,
    TaskServiceProtocol,
    TaskSpec,
)

__all__ = [
    "AcceptanceResult",
    "NodeOpResult",
    "TaskExecutionGraph",
    "TaskInfoRecord",
    "TaskInfoRequest",
    "TaskNode",
    "TaskOpResult",
    "TaskServiceProtocol",
    "TaskSpec",
]
