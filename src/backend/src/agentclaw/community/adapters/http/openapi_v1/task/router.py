"""Task 公开 HTTP adapter routes —— 前端公开面(仅 execute/dashboard/list;Rule 22:只转协议,不持领域策略)。

POST /openapi/v1/collaboration/tasks/execute   — 提交任务(delegate TaskServiceProtocol.execute)
GET  /openapi/v1/collaboration/tasks/dashboard  — 查任务图(delegate TaskServiceProtocol.get_task_dashboard)
GET  /openapi/v1/collaboration/tasks/list       — 列任务摘要(delegate TaskServiceProtocol.list_tasks)

前端公开面经 gateway spanner 鉴权(``/openapi/v1/collaboration/**`` → user+app required)。内部接口
(回投 / bbs 接力 / 任务发现阶段)见 ``adapters/http/task/``(前缀 ``/api/v1/collaboration/tasks``,不经 spanner)。
统一 ``/openapi/v1`` 返回协议:成功经 ``envelope()`` → ``Envelope{code,message,data,request_id}``;
领域异常(GraphAlreadyInitialized/TaskNotFound/TaskState/GraphIntegrity…)直接上抛,
由 ``@envelope_errors`` + ``ENVELOPE_ERRORS`` 映射为统一 ``ErrorEnvelope``——router 不手写
``HTTPException`` 处理领域错误。仅对纯输入校验(非法 status 过滤)用 ``HTTPException`` 走中央 handler,
同样产出 ``ErrorEnvelope``。对齐 api/task/task_service.py Protocol。
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from agentclaw.community.adapters.http.openapi_v1.contracts import Envelope
from agentclaw.community.adapters.http.openapi_v1.responses import envelope, envelope_errors
from agentclaw.community.adapters.http.task.schemas import (
    TaskExecutionGraphDTO,
    TaskInfoDTO,
    TaskOpResultDTO,
    TaskSummaryDTO,
    graph_to_dto,
    op_result_to_dto,
    summary_to_dto,
    task_info_from_dto,
)
from agentclaw.community.api.task.task_service import TaskServiceProtocol
from agentclaw.community.core.task.domain.models import Status
from agentclaw.community.di import Injected

router = APIRouter(prefix="/openapi/v1/collaboration/tasks", tags=["task"])


@router.post("/execute", response_model=Envelope[TaskOpResultDTO])
@envelope_errors
async def execute_task(
    body: TaskInfoDTO,
    request: Request,
    service: TaskServiceProtocol = Injected(TaskServiceProtocol),  # noqa: B008
) -> Envelope[TaskOpResultDTO]:
    """提交执行任务。initialize_graph(根 PENDING) → 编排核 on_execute 首帧推进。

    幂等:同 task_id 已建图(GraphAlreadyInitializedError)→ ``@envelope_errors`` 映射 409。"""
    task_info = task_info_from_dto(body)
    result = await service.execute(task_info)
    return envelope(op_result_to_dto(result), request)


@router.get("/dashboard", response_model=Envelope[TaskExecutionGraphDTO])
@envelope_errors
async def get_task_dashboard(
    task_id: str,
    request: Request,
    node_id: str | None = None,
    include_action_log: bool = False,
    service: TaskServiceProtocol = Injected(TaskServiceProtocol),  # noqa: B008
) -> Envelope[TaskExecutionGraphDTO]:
    """任务执行详情可视化(整图或按 node_id 子树投影),只读。

    ``include_action_log=true`` 时返回各节点动作级历史快照(PLAN/DISPATCH/EXECUTE/VERIFY/RESET/
    TRANSITION 全量 payload),默认关(诊断页开)。任务/节点不存在 → TaskNotFoundError/NodeNotFoundError
    → ``@envelope_errors`` 映射 404。"""
    graph = service.get_task_dashboard(task_id, node_id)
    return envelope(graph_to_dto(graph, include_action_log=include_action_log), request)


@router.get("/list", response_model=Envelope[list[TaskSummaryDTO]])
@envelope_errors
async def list_tasks(
    request: Request,
    status: str | None = None,
    service: TaskServiceProtocol = Injected(TaskServiceProtocol),  # noqa: B008
) -> Envelope[list[TaskSummaryDTO]]:
    """列任务摘要(轻量投影),按 run_id 降序(最新在前)。可选 ``status`` 过滤图级状态。

    visualization/看板列表视图用;不返回完整图对象。非法 ``status`` 过滤值 → 400
    (``Status(invalid)`` 会抛 ``ValueError``,router 层先校验;经 ``HTTPException`` → 中央 handler
    → ``ErrorEnvelope``,非 500)。"""
    if status is not None and status not in {s.value for s in Status}:
        raise HTTPException(status_code=400, detail=f"invalid status filter: {status}")
    items = service.list_tasks(status)
    return envelope([summary_to_dto(s) for s in items], request)
