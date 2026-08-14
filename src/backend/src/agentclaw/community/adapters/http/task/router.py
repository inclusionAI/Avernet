"""Task HTTP adapter routes —— thin(Rule 22:只转协议,不持领域策略)。

POST /api/task/execute            — 提交任务(delegate TaskServiceProtocol.execute)
GET  /api/task/dashboard          — 查任务图(delegate TaskServiceProtocol.get_task_dashboard)
POST /api/task/callback/report    — 执行实体回投(delegate TaskLoopCallbackProtocol.report_result)

对齐 api/task/{task_service,task_loop_callback}.py Protocol。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from agentclaw.community.adapters.http.task.schemas import (
    ApiResponse,
    TaskCallbackDataDTO,
    TaskExecutionGraphDTO,
    TaskInfoDTO,
    TaskOpResultDTO,
    callback_from_dto,
    graph_to_dto,
    op_result_to_dto,
    task_info_from_dto,
)
from agentclaw.community.api.task.task_loop_callback import TaskLoopCallbackProtocol
from agentclaw.community.api.task.task_service import TaskServiceProtocol
from agentclaw.community.di import Injected

router = APIRouter(prefix="/api/task", tags=["task"])


@router.post("/execute", response_model=ApiResponse[TaskOpResultDTO])
async def execute_task(
    body: TaskInfoDTO,
    service: TaskServiceProtocol = Injected(TaskServiceProtocol),  # noqa: B008
) -> ApiResponse[TaskOpResultDTO]:
    """提交执行任务。initialize_graph(根 PENDING) → 编排核 on_execute 首帧推进。"""
    task_info = task_info_from_dto(body)
    result = await service.execute(task_info)
    return ApiResponse(success=True, message="OK", error_code=200, data=op_result_to_dto(result))


@router.get("/dashboard", response_model=ApiResponse[TaskExecutionGraphDTO])
async def get_task_dashboard(
    task_id: str,
    node_id: str | None = None,
    service: TaskServiceProtocol = Injected(TaskServiceProtocol),  # noqa: B008
) -> ApiResponse[TaskExecutionGraphDTO]:
    """任务执行详情可视化(整图或按 node_id 子树投影),只读。"""
    graph = service.get_task_dashboard(task_id, node_id)
    return ApiResponse(success=True, message="OK", error_code=200, data=graph_to_dto(graph))


@router.post("/callback/report", response_model=ApiResponse[dict[str, Any]])
async def report_callback(
    body: TaskCallbackDataDTO,
    callback: TaskLoopCallbackProtocol = Injected(TaskLoopCallbackProtocol),  # noqa: B008
) -> ApiResponse[dict[str, Any]]:
    """执行实体(bot workflow / bcn 协作群)PUSH 回投 → 适配层 → 编排核 on_report → 翻态推进。"""
    data = callback_from_dto(body)
    await callback.report_result(data)
    return ApiResponse(success=True, message="OK", error_code=200, data={"ok": True})
