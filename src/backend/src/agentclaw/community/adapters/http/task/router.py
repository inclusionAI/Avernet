"""Task HTTP adapter routes —— thin(Rule 22:只转协议,不持领域策略)。

POST /openapi/v1/task/execute            — 提交任务(delegate TaskServiceProtocol.execute)
GET  /openapi/v1/task/dashboard          — 查任务图(delegate TaskServiceProtocol.get_task_dashboard)
POST /openapi/v1/task/callback/report    — 执行实体回投(delegate TaskLoopCallbackProtocol.report_result)

对齐 api/task/{task_service,task_loop_callback}.py Protocol。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from agentclaw.community.adapters.http.task.schemas import (
    ApiResponse,
    BbsAttachDTO,
    BbsClaimDTO,
    BbsResultDTO,
    TaskCallbackDataDTO,
    TaskExecutionGraphDTO,
    TaskInfoDTO,
    TaskOpResultDTO,
    TaskSummaryDTO,
    acceptance_result_from_dto,
    callback_from_dto,
    graph_to_dto,
    op_result_to_dto,
    summary_to_dto,
    task_info_from_dto,
    task_spec_from_dto,
)
from agentclaw.community.api.task.task_loop_callback import TaskLoopCallbackProtocol
from agentclaw.community.api.task.task_service import TaskServiceProtocol
from agentclaw.community.di import Injected

router = APIRouter(prefix="/openapi/v1/task", tags=["task"])


@router.post("/execute", response_model=ApiResponse[TaskOpResultDTO])
async def execute_task(
    body: TaskInfoDTO,
    service: TaskServiceProtocol = Injected(TaskServiceProtocol),  # noqa: B008
) -> ApiResponse[TaskOpResultDTO]:
    """提交执行任务。initialize_graph(根 PENDING) → 编排核 on_execute 首帧推进。

    幂等:同 task_id 已建图(GraphAlreadyInitializedError)→ 409 Conflict(显式可读,非 500)。"""
    from fastapi import HTTPException
    task_info = task_info_from_dto(body)
    try:
        result = await service.execute(task_info)
    except GraphAlreadyInitializedError:
        raise HTTPException(status_code=409, detail=f"task_id={task_info.task_spec.metadata.task_id} 图已存在,请用 dashboard 查看"
                                  ) from None
    return ApiResponse(success=True, message="OK", error_code=200, data=op_result_to_dto(result))


@router.get("/dashboard", response_model=ApiResponse[TaskExecutionGraphDTO])
async def get_task_dashboard(
    task_id: str,
    node_id: str | None = None,
    include_action_log: bool = False,
    service: TaskServiceProtocol = Injected(TaskServiceProtocol),  # noqa: B008
) -> ApiResponse[TaskExecutionGraphDTO]:
    """任务执行详情可视化(整图或按 node_id 子树投影),只读。

    ``include_action_log=true`` 时返回各节点动作级历史快照(PLAN/DISPATCH/EXECUTE/VERIFY/RESET/
    TRANSITION 全量 payload),默认关(诊断页开)。任务不存在 → 404(显式可读,非 500)。"""
    from fastapi import HTTPException
    try:
        graph = service.get_task_dashboard(task_id, node_id)
    except (TaskNotFoundError, NodeNotFoundError):
        raise HTTPException(status_code=404, detail=f"task_id={task_id} 不存在") from None
    return ApiResponse(success=True, message="OK", error_code=200,
                       data=graph_to_dto(graph, include_action_log=include_action_log))


@router.get("/list", response_model=ApiResponse[list[TaskSummaryDTO]])
async def list_tasks(
    status: str | None = None,
    service: TaskServiceProtocol = Injected(TaskServiceProtocol),  # noqa: B008
) -> ApiResponse[list[TaskSummaryDTO]]:
    """列任务摘要(轻量投影),按 run_id 降序(最新在前)。可选 ``status`` 过滤图级状态。

    visualization/看板列表视图用;不返回完整图对象。非法 ``status`` 过滤值 → 400
    (显式可读,非 500:``Status(invalid)`` 会抛 ``ValueError``,router 层先校验截断)。"""
    from fastapi import HTTPException

    from agentclaw.community.core.task.domain.models import Status
    if status is not None and status not in {s.value for s in Status}:
        raise HTTPException(
            status_code=400, detail=f"invalid status filter: {status}",
        ) from None
    items = service.list_tasks(status)
    return ApiResponse(success=True, message="OK", error_code=200, data=[summary_to_dto(s) for s in items])


@router.post("/callback/report", response_model=ApiResponse[dict[str, Any]])
async def report_callback(
    body: TaskCallbackDataDTO,
    callback: TaskLoopCallbackProtocol = Injected(TaskLoopCallbackProtocol),  # noqa: B008
) -> ApiResponse[dict[str, Any]]:
    """执行实体(bot workflow / bcn 协作群)PUSH 回投 → 适配层 → 编排核 on_report → 翻态推进。"""
    data = callback_from_dto(body)
    await callback.report_result(data)
    return ApiResponse(success=True, message="OK", error_code=200, data={"ok": True})


@router.post("/bbs/claim", response_model=ApiResponse[dict[str, Any]])
async def bbs_claim(
    body: BbsClaimDTO,
    service: TaskServiceProtocol = Injected(TaskServiceProtocol),  # noqa: B008
) -> ApiResponse[dict[str, Any]]:
    """BBS 接力步②:任务根级 CAS 占有;恰一赢,输者/非 bbs 任务 → 409。

    幂等:同 bot 重 claim 返 200(视为已占有);非 bbs 任务或已被他人占有 → 409。
    """
    from fastapi import HTTPException

    from agentclaw.community.core.task.domain.errors import TaskStateError
    try:
        result = service.claim_bbs_task(body.task_id, body.bot_id)
    except TaskStateError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    return ApiResponse(success=True, message="OK", error_code=200,
                       data={"root_node_id": result.node_id, "task_id": body.task_id})


@router.post("/bbs/attach", response_model=ApiResponse[dict[str, Any]])
async def bbs_attach(
    body: BbsAttachDTO,
    service: TaskServiceProtocol = Injected(TaskServiceProtocol),  # noqa: B008
) -> ApiResponse[dict[str, Any]]:
    """BBS 接力步④:在 parent 下挂 run_mode=bbs scoped 子节点 + start(create+start 合一)。仅 claim 持有者可挂。

    owner 校验失败 / BBS 深度闸 / 分解树完整性违反 → 409(TaskStateError / GraphIntegrityError)。
    """
    from fastapi import HTTPException

    from agentclaw.community.core.task.domain.errors import (
        GraphIntegrityError, TaskStateError,
    )
    task_spec = task_spec_from_dto(body.task_spec)
    try:
        node = service.attach_bbs_node(body.task_id, body.parent_node_id, task_spec, body.bot_id)
    except (TaskStateError, GraphIntegrityError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    return ApiResponse(success=True, message="OK", error_code=200,
                       data={"node_id": node.node_id, "task_id": body.task_id})


@router.post("/bbs/result", response_model=ApiResponse[dict[str, Any]])
async def bbs_result(
    body: BbsResultDTO,
    service: TaskServiceProtocol = Injected(TaskServiceProtocol),  # noqa: B008
) -> ApiResponse[dict[str, Any]]:
    """BBS 接力步⑤:回投 scoped 节点终态 + 释放 claim;收口由框架经 owner 复核根 gap 自行收口(非 bot 声明)。

    ``acceptance_result``(PASS→DONE / FAIL+gaps→FAILED)/ ``output_patch``(checkpoint fold)/
    ``exec_error``(执行报错 fold)。``bot_id`` 须为当前 ``bbs_owner``,否则 ``TaskStateError`` → 409。
    """
    from fastapi import HTTPException

    from agentclaw.community.core.task.domain.errors import TaskStateError
    ar = acceptance_result_from_dto(body.acceptance_result) if body.acceptance_result else None
    try:
        await service.report_bbs_result(
            body.task_id, body.node_id, body.bot_id,
            acceptance_result=ar, output_patch=body.output_patch,
            exec_error=body.exec_error,
        )
    except TaskStateError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
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
    GraphAlreadyInitializedError, NodeNotFoundError, TaskNotFoundError, TaskStateError,
)
from agentclaw.community.core.task.domain.models import Status
from agentclaw.community.core.task.task_runner.callback_correlation import (
    CallbackCorrelationRegistry,
)

task_callback_router = APIRouter(prefix="/openapi/v1/task/callback", tags=["task-callback"])

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
