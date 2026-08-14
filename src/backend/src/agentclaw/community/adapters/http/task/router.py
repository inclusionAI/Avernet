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


# ===== task_loop inbound PUSH callback router(单 bot workflow / bcn 协作群)=====
# 边缘:解析 raw body → Pydantic schema → auth.verify(source from body) → translate
# → disposition 分发 start_run/report_result。TaskError(TaskStateError/NotFound)router 层显式映射;
# CallbackAuthError/CallbackCorrelationError(DomainError 子类)router 层显式映射 401/400,亦经中央 handler 兜底。
# 幂等:result 重投到已终态节点→200 ack;start stale→409。无节点名字面量(零 case)。
from fastapi import Depends, HTTPException, Request

from agentclaw.community.adapters.http.task.auth import CallbackAuthenticator
from agentclaw.community.adapters.http.task.schemas import (
    CallbackResponse, TaskCallbackRequest, TaskNodeCallbackRequest,
)
from agentclaw.community.adapters.http.task.translator import translate
from agentclaw.community.core.errors import (
    CallbackAuthError, CallbackCorrelationError,
)
from agentclaw.community.core.task.domain.errors import (
    NodeNotFoundError, TaskNotFoundError, TaskStateError,
)
from agentclaw.community.core.task.domain.models import Status
from agentclaw.community.core.task.task_runner.callback_correlation import (
    CallbackCorrelationRegistry,
)

task_callback_router = APIRouter(prefix="/task_loop/callback", tags=["task-callback"])

_TERMINAL = {Status.DONE, Status.FAILED, Status.HUNG}


def _find_node_status(svc: TaskServiceProtocol, loop_task_id: str) -> Status | None:
    task_id, node_id = loop_task_id.split("::", 1)
    graph = svc.get_task_dashboard(task_id)
    node = next((n for n in graph.tasks if n.node_id == node_id), None)
    return node.status if node is not None else None


async def _dispatch(
    request: Request, disposition: str, schema_cls: type[TaskCallbackRequest],
    svc: TaskServiceProtocol, auth: CallbackAuthenticator, registry: CallbackCorrelationRegistry,
) -> CallbackResponse:
    raw = await request.body()
    try:
        req = schema_cls.model_validate_json(raw)
    except Exception:
        raise HTTPException(status_code=422, detail="invalid callback body")
    # source 来自已解析 body;HMAC 用原始字节
    try:
        auth.verify(source=req.workflow_source, headers=request.headers, raw_body=raw,
                    method=request.method, path=request.url.path)
        tc = translate(req, disposition, registry)
    except CallbackAuthError:
        raise HTTPException(status_code=401, detail="callback auth failed")
    except CallbackCorrelationError as e:
        raise HTTPException(status_code=400, detail=e.detail)
    try:
        if disposition == "start":
            await svc.callback.start_run(tc.data)
        else:
            await svc.callback.report_result(tc.data)
    except (TaskNotFoundError, NodeNotFoundError):
        raise HTTPException(status_code=404, detail="task/node not found")
    except TaskStateError:
        if disposition == "result":
            cur = _find_node_status(svc, tc.data.loop_task_id)
            if cur in _TERMINAL:
                return CallbackResponse(success=True, code=200, message="idempotent")
        raise HTTPException(status_code=409, detail="illegal state transition")
    return CallbackResponse(success=True)


@task_callback_router.post("/workflow_start", response_model=CallbackResponse)
async def workflow_start(
    request: Request,
    svc: TaskServiceProtocol = Injected(TaskServiceProtocol),  # noqa: B008
    auth: CallbackAuthenticator = Injected(CallbackAuthenticator),  # noqa: B008
    registry: CallbackCorrelationRegistry = Injected(CallbackCorrelationRegistry),  # noqa: B008
) -> CallbackResponse:
    return await _dispatch(request, "start", TaskCallbackRequest, svc, auth, registry)


@task_callback_router.post("/workflow_result", response_model=CallbackResponse)
async def workflow_result(
    request: Request,
    svc: TaskServiceProtocol = Injected(TaskServiceProtocol),  # noqa: B008
    auth: CallbackAuthenticator = Injected(CallbackAuthenticator),  # noqa: B008
    registry: CallbackCorrelationRegistry = Injected(CallbackCorrelationRegistry),  # noqa: B008
) -> CallbackResponse:
    return await _dispatch(request, "result", TaskCallbackRequest, svc, auth, registry)


@task_callback_router.post("/node_start", response_model=CallbackResponse)
async def node_start(
    request: Request,
    svc: TaskServiceProtocol = Injected(TaskServiceProtocol),  # noqa: B008
    auth: CallbackAuthenticator = Injected(CallbackAuthenticator),  # noqa: B008
    registry: CallbackCorrelationRegistry = Injected(CallbackCorrelationRegistry),  # noqa: B008
) -> CallbackResponse:
    return await _dispatch(request, "start", TaskNodeCallbackRequest, svc, auth, registry)


@task_callback_router.post("/node_result", response_model=CallbackResponse)
async def node_result(
    request: Request,
    svc: TaskServiceProtocol = Injected(TaskServiceProtocol),  # noqa: B008
    auth: CallbackAuthenticator = Injected(CallbackAuthenticator),  # noqa: B008
    registry: CallbackCorrelationRegistry = Injected(CallbackCorrelationRegistry),  # noqa: B008
) -> CallbackResponse:
    return await _dispatch(request, "result", TaskNodeCallbackRequest, svc, auth, registry)
