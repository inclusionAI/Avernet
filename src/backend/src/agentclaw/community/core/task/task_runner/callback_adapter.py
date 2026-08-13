"""回投适配层:TaskCallbackData → TaskNodePatch → ExecutionEngine.on_report。

对齐 plan §3.5.2。
"""
from __future__ import annotations

from agentclaw.community.core.task.domain.models import TaskCallbackData, TaskNodePatch


class CallbackAdapter:
    """把执行实体回投的 TaskCallbackData 组装成 TaskNodePatch,交编排核 on_report。

    loop_task_id→(task_id,node_id)映射;result.success/data→acceptance_result/output;
    fail_detail→extend_props_patch。
    """

    def __init__(self, engine):
        """engine: ExecutionEngine(编排核 on_report 入口)。"""
        self._engine = engine

    def adapt(self, data: TaskCallbackData) -> TaskNodePatch:
        """把 TaskCallbackData 适配为 TaskNodePatch(首批壳,映射逻辑待实现)。"""
        raise NotImplementedError
