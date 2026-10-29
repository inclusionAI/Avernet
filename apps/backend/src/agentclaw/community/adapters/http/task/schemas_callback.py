"""Task HTTP API —— task_loop inbound callback schemas.

从 ``schemas.py`` 拆出:task 级 / node 级 PUSH 回调载荷 DTO,避免 ``schemas.py``
超过 1000 行上限。纯 pydantic.BaseModel 序列化,不含业务逻辑(Rule 22)。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel

# ===== task_loop inbound callback schemas(PUSH 回调,对齐羽雀 TaskCallbackData/TaskNodeCallbackData)=====
# SSOT TaskCallbackData 保持精简(不扩);羽雀丰富字段在 translator 边缘折叠进 SSOT。
# 必填非可选(AGENTS.md):task_id/workflow_source/workflow_id/workflow_instance_id/status/is_success。
# None 仅契约态:goal/output/failed_info/ext_info/loop_task_id(回声字段,缺失走 registry)。


class TaskCallbackRequest(BaseModel):
    """task 级(workflow)回调载荷。"""

    task_id: str
    workflow_source: Literal["claw_mind", "bcn"]
    workflow_id: str
    workflow_instance_id: str
    goal: str | None = None
    status: str
    is_success: bool
    output: dict[str, Any] | None = None
    failed_info: str | None = None
    ext_info: dict[str, Any] | None = None
    loop_task_id: str | None = None  # 回声字段:派发期透传,引擎原样回带(可选)


class TaskNodeCallbackRequest(TaskCallbackRequest):
    """node 级回调载荷(node_id 即 Avernet 子节点 id,统一领域对象 1:1 映射)。"""

    node_id: str
