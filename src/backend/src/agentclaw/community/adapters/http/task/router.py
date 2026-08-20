"""Task 内部 HTTP adapter routes —— 不经 gateway spanner(内部 API)。

任务模块内部前缀 ``/api/v1/collaboration/tasks``。本 router 承载:
- 公开面镜像(execute/dashboard/list 副本):供内部调用方(bot / 服务间)免 gateway spanner 直调;
  与 ``adapters/http/openapi_v1/task/`` 公开面同一 ``TaskServiceProtocol`` 委托,逻辑保持一致(改其一须同步)。
- 回投 / BBS 接力 / 任务发现阶段:前端不直面的内部写口/阶段接口。
前端公开面(经 gateway spanner)见 ``adapters/http/openapi_v1/task/``。本 router 只转协议,不持领域策略(Rule 22)。

端点(同一任务模块,不同阶段):
  POST /api/v1/collaboration/tasks/execute          — 提交任务(公开面镜像;delegate TaskServiceProtocol.execute)
  GET  /api/v1/collaboration/tasks/dashboard         — 查任务图(公开面镜像;delegate get_task_dashboard)
  GET  /api/v1/collaboration/tasks/list              — 列任务摘要(公开面镜像;delegate list_tasks)
  POST /api/v1/collaboration/tasks/callback/report  — 执行实体回投(delegate TaskLoopCallbackProtocol.report_result)
  POST /api/v1/collaboration/tasks/bbs/claim        — BBS 接力步②:CAS 占根(恰一赢,输者 409)
  POST /api/v1/collaboration/tasks/bbs/attach        — BBS 接力步④:挂 run_mode=bbs scoped 子节点 + start
  POST /api/v1/collaboration/tasks/bbs/result        — BBS 接力步⑤:回投终态 + 释放 claim
  POST /api/v1/collaboration/tasks/discovery/discover — 任务发现阶段:读取任务 → per-bot engine 建 session → 投递通知
  GET  /api/v1/collaboration/tasks/discovery/status   — 任务发现状态(读 SQLite db)

task_loop inbound PUSH callback(前缀 ``/api/v1/collaboration/tasks/callback``):
  POST .../callback/workflow_start | workflow_result | node_start | node_result

成功经 ``envelope()`` → ``Envelope{code,message,data,request_id}``;领域异常
(GraphAlreadyInitialized/TaskNotFound/TaskState/GraphIntegrity/CallbackAuth/Correlation…)
直接上抛,由 ``@envelope_errors`` + ``ENVELOPE_ERRORS`` 映射为 ``ErrorEnvelope``——不经中央 handler。
仅对纯输入校验(callback 原文非 JSON / discover 顶层失败)用 ``HTTPException``/``InternalError`` 上抛,
落到中央 handler 时内部路径下为 ``{"detail": ...}`` 形。对齐 api/task/{task_service,task_loop_callback}.py Protocol。
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

from agentclaw.community.adapters.http.openapi_v1.contracts import Envelope
from agentclaw.community.adapters.http.openapi_v1.responses import envelope, envelope_errors
from agentclaw.community.adapters.http.task.auth import CallbackAuthenticator
from agentclaw.community.adapters.http.task.schemas import (
    BbsAttachDTO,
    BbsClaimDTO,
    BbsResultDTO,
    TaskCallbackDataDTO,
    TaskCallbackRequest,
    TaskExecutionGraphDTO,
    TaskInfoDTO,
    TaskNodeCallbackRequest,
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
from agentclaw.community.adapters.http.task.translator import translate
from agentclaw.community.api.task.task_loop_callback import TaskLoopCallbackProtocol
from agentclaw.community.api.task.task_service import TaskServiceProtocol
from agentclaw.community.core.errors import InternalError
from agentclaw.community.core.task.domain.errors import TaskStateError
from agentclaw.community.core.task.domain.models import Status
from agentclaw.community.core.task.task_discovery.discovery_service import (
    DiscoveryService,
)
from agentclaw.community.core.task.task_discovery.task_reader import (
    SqliteTaskReader,
)
from agentclaw.community.core.task.task_runner.callback_correlation import (
    CallbackCorrelationRegistry,
)
from agentclaw.community.di import Injected
from agentclaw.community.log import get_logger

logger = get_logger()

router = APIRouter(prefix="/api/v1/collaboration/tasks", tags=["task"])


# ===== 公开面镜像(execute/dashboard/list;内部 /api/v1 副本,不经 spanner)=====
# 与 ``adapters/http/openapi_v1/task/router.py`` 公开面同一 ``TaskServiceProtocol`` 委托,
# 逻辑保持一致 —— 内部调用方(bot / 服务间)走此副本免 gateway spanner。改其一须同步。


@router.post("/execute", response_model=Envelope[TaskOpResultDTO])
@envelope_errors
async def execute_task_internal(
    body: TaskInfoDTO,
    request: Request,
    service: TaskServiceProtocol = Injected(TaskServiceProtocol),  # noqa: B008
) -> Envelope[TaskOpResultDTO]:
    """提交执行任务(内部副本)。initialize_graph(根 PENDING)→ 编排核 on_execute 首帧推进。

    幂等:同 task_id 已建图(GraphAlreadyInitializedError)→ ``@envelope_errors`` 映射 409。"""
    task_info = task_info_from_dto(body)
    result = await service.execute(task_info)
    return envelope(op_result_to_dto(result), request)


@router.get("/dashboard", response_model=Envelope[TaskExecutionGraphDTO])
@envelope_errors
async def get_task_dashboard_internal(
    task_id: str,
    request: Request,
    node_id: str | None = None,
    include_action_log: bool = False,
    service: TaskServiceProtocol = Injected(TaskServiceProtocol),  # noqa: B008
) -> Envelope[TaskExecutionGraphDTO]:
    """任务执行详情可视化(内部副本,只读;整图或按 node_id 子树投影)。

    任务/节点不存在 → TaskNotFoundError/NodeNotFoundError → ``@envelope_errors`` 映射 404。"""
    graph = service.get_task_dashboard(task_id, node_id)
    return envelope(graph_to_dto(graph, include_action_log=include_action_log), request)


@router.get("/list", response_model=Envelope[list[TaskSummaryDTO]])
@envelope_errors
async def list_tasks_internal(
    request: Request,
    status: str | None = None,
    service: TaskServiceProtocol = Injected(TaskServiceProtocol),  # noqa: B008
) -> Envelope[list[TaskSummaryDTO]]:
    """列任务摘要(内部副本,按 run_id 降序;可选 ``status`` 过滤图级状态)。

    非法 ``status`` 过滤值 → 400(经 ``HTTPException`` → 中央 handler → ``ErrorEnvelope``)。"""
    if status is not None and status not in {s.value for s in Status}:
        raise HTTPException(status_code=400, detail=f"invalid status filter: {status}")
    items = service.list_tasks(status)
    return envelope([summary_to_dto(s) for s in items], request)


# ===== 回投 / BBS 接力 =====


@router.post("/callback/report", response_model=Envelope[dict[str, Any]])
@envelope_errors
async def report_callback(
    body: TaskCallbackDataDTO,
    request: Request,
    callback: TaskLoopCallbackProtocol = Injected(TaskLoopCallbackProtocol),  # noqa: B008
) -> Envelope[dict[str, Any]]:
    """执行实体(bot workflow / bcn 协作群)PUSH 回投 → 适配层 → 编排核 on_report → 翻态推进。

    领域异常(TaskStateError/TaskNotFoundError…)上抛 → ``@envelope_errors`` 映射。"""
    data = callback_from_dto(body)
    await callback.report_result(data)
    return envelope({"ok": True}, request)


@router.post("/bbs/claim", response_model=Envelope[dict[str, Any]])
@envelope_errors
async def bbs_claim(
    body: BbsClaimDTO,
    request: Request,
    service: TaskServiceProtocol = Injected(TaskServiceProtocol),  # noqa: B008
) -> Envelope[dict[str, Any]]:
    """BBS 接力步②:任务根级 CAS 占有;恰一赢,输者/非 bbs 任务 → 409。

    幂等:同 bot 重 claim 返 200(视为已占有);非 bbs 任务或已被他人占有 → TaskStateError
    → ``@envelope_errors`` 映射 409。
    """
    result = service.claim_bbs_task(body.task_id, body.bot_id)
    return envelope({"root_node_id": result.node_id, "task_id": body.task_id}, request)


@router.post("/bbs/attach", response_model=Envelope[dict[str, Any]])
@envelope_errors
async def bbs_attach(
    body: BbsAttachDTO,
    request: Request,
    service: TaskServiceProtocol = Injected(TaskServiceProtocol),  # noqa: B008
) -> Envelope[dict[str, Any]]:
    """BBS 接力步④:在 parent 下挂 run_mode=bbs scoped 子节点 + start(create+start 合一)。仅 claim 持有者可挂。

    owner 校验失败 / BBS 深度闸 / 分解树完整性违反 → TaskStateError / GraphIntegrityError
    → ``@envelope_errors`` 映射 409。
    """
    task_spec = task_spec_from_dto(body.task_spec)
    node = service.attach_bbs_node(body.task_id, body.parent_node_id, task_spec, body.bot_id)
    return envelope({"node_id": node.node_id, "task_id": body.task_id}, request)


@router.post("/bbs/result", response_model=Envelope[dict[str, Any]])
@envelope_errors
async def bbs_result(
    body: BbsResultDTO,
    request: Request,
    service: TaskServiceProtocol = Injected(TaskServiceProtocol),  # noqa: B008
) -> Envelope[dict[str, Any]]:
    """BBS 接力步⑤:回投 scoped 节点终态 + 释放 claim;收口由框架经 owner 复核根 gap 自行收口(非 bot 声明)。

    ``acceptance_result``(PASS→DONE / FAIL+gaps→FAILED)/ ``output_patch``(checkpoint fold)/
    ``exec_error``(执行报错 fold)。``bot_id`` 须为当前 ``bbs_owner``,否则 ``TaskStateError``
    → ``@envelope_errors`` 映射 409。
    """
    ar = acceptance_result_from_dto(body.acceptance_result) if body.acceptance_result else None
    await service.report_bbs_result(
        body.task_id, body.node_id, body.bot_id,
        acceptance_result=ar, output_patch=body.output_patch,
        exec_error=body.exec_error,
    )
    return envelope({"ok": True}, request)


# ===== 任务发现阶段(任务模块的一个阶段,非独立模块)=====

#: 默认 db 文件路径(9 级上溯到仓库根 → scripts/.dependencies/data/discovered_tasks.db)
_PROJECT_ROOT = Path(__file__).resolve()
for _ in range(9):
    _PROJECT_ROOT = _PROJECT_ROOT.parent
_DEFAULT_DB = str(_PROJECT_ROOT / "scripts" / ".dependencies" / "data" / "discovered_tasks.db")


def _resolve_db_path() -> str:
    """从环境变量或默认路径解析 db 文件路径。"""
    return os.environ.get("TASK_DISCOVERY_DATA_FILE", _DEFAULT_DB)


@router.post("/discovery/discover", response_model=Envelope[dict[str, Any]])
@envelope_errors
async def discover_tasks(
    request: Request,
    user_id: str = Query("default", description="用户 ID"),
    agent_id: str = Query("bot_001", description="Bot/Agent ID"),
    bot_id: str = Query(..., description="Bot ID"),
    owner_id: str = Query(..., description="Bot 所有者 ID"),
    service: DiscoveryService = Injected(DiscoveryService),  # noqa: B008
) -> Envelope[dict[str, Any]]:
    """任务发现阶段:读取任务 → 在 per-bot engine 创建 session → 投递通知。

    session-creation 错误按任务捕获(非顶层),故 discover 端到端跑完总返 200
    ``Envelope``(失色任务在 ``tasks[].success/error`` 体现)。仅顶层 discover
    失败 → ``InternalError`` → 500。
    """
    logger.info(
        "[task_discovery] discover triggered: user_id=%s, agent_id=%s, bot_id=%s",
        user_id, agent_id, bot_id,
    )
    try:
        results = await service.discover(
            bot_id=bot_id, owner_id=owner_id, agent_id=agent_id,
        )
    except Exception as exc:
        logger.error("[task_discovery] discover failed: %s", exc, exc_info=True)
        raise InternalError("discovery failed") from exc
    return envelope(
        {
            "discovered": len(results),
            "tasks": [
                {
                    "task_id": r.task.task_id,
                    "project_name": r.task.project_name,
                    "success": r.success,
                    "session_id": r.session.session_id if r.session else None,
                    "notification_sent": r.notification_sent,
                    "error": r.error,
                }
                for r in results
            ],
        },
        request,
    )


@router.get("/discovery/status", response_model=Envelope[dict[str, Any]])
@envelope_errors
async def get_discovery_status(request: Request) -> Envelope[dict[str, Any]]:
    """查看任务发现状态(从 SQLite db 读取)。db 读失败 → ``InternalError`` → 500。"""
    db_path = _resolve_db_path()
    try:
        reader = SqliteTaskReader(db_path)
        tasks = reader.read_discovered_tasks()
    except Exception as exc:
        raise InternalError("status read failed") from exc
    return envelope(
        {
            "total": len(tasks),
            "tasks": [
                {
                    "task_id": t.task_id,
                    "project_name": t.project_name,
                    "status": t.status,
                    "priority": t.priority,
                }
                for t in tasks
            ],
        },
        request,
    )


# ===== task_loop inbound PUSH callback router(单 bot workflow / bcn 协作群)=====
# 边缘:解析 raw body → Pydantic schema → auth.verify(source from body) → translate
# → disposition 分发 start_run/report_result。领域异常统一上抛 ``@envelope_errors`` 映射:
# CallbackAuthError→401 / CallbackCorrelationError→400 / TaskNotFoundError·NodeNotFoundError→404 /
# TaskStateError→409。仅 raw-body 非 JSON 用 ``HTTPException(422)`` 走中央 handler(内部 ``{"detail": ...}``)。
# 幂等:result 重投到已终态节点→200 ack(start stale→409)。无节点名字面量(零 case)。
task_callback_router = APIRouter(prefix="/api/v1/collaboration/tasks/callback", tags=["task-callback"])

_TERMINAL = {Status.DONE, Status.FAILED, Status.HUNG}


def _find_node_status(svc: TaskServiceProtocol, loop_task_id: str) -> Status | None:
    task_id, node_id = loop_task_id.split("::", 1)
    graph = svc.get_task_dashboard(task_id)
    node = next((n for n in graph.tasks if n.node_id == node_id), None)
    return node.status if node is not None else None


async def _dispatch(
    request: Request, disposition: str, schema_cls: type[TaskCallbackRequest],
    svc: TaskServiceProtocol, auth: CallbackAuthenticator, registry: CallbackCorrelationRegistry,
) -> Envelope[dict[str, Any]]:
    raw = await request.body()
    try:
        req = schema_cls.model_validate_json(raw)
    except Exception:
        raise HTTPException(status_code=422, detail="invalid callback body")
    # source 来自已解析 body;HMAC 用原始字节。CallbackAuthError/CallbackCorrelationError 上抛 → @envelope_errors
    auth.verify(source=req.workflow_source, headers=request.headers, raw_body=raw,
                method=request.method, path=request.url.path)
    tc = translate(req, disposition, registry)
    try:
        if disposition == "start":
            await svc.callback.start_run(tc.data)
        else:
            await svc.callback.report_result(tc.data)
    except TaskStateError:
        # 幂等:result 重投到已终态节点 → 200 ack;否则 TaskStateError 上抛 → @envelope_errors 409
        if disposition == "result":
            cur = _find_node_status(svc, tc.data.loop_task_id)
            if cur in _TERMINAL:
                return envelope({"ok": True}, request, message="idempotent")
        raise
    return envelope({"ok": True}, request)


@task_callback_router.post("/workflow_start", response_model=Envelope[dict[str, Any]])
@envelope_errors
async def workflow_start(
    request: Request,
    svc: TaskServiceProtocol = Injected(TaskServiceProtocol),  # noqa: B008
    auth: CallbackAuthenticator = Injected(CallbackAuthenticator),  # noqa: B008
    registry: CallbackCorrelationRegistry = Injected(CallbackCorrelationRegistry),  # noqa: B008
) -> Envelope[dict[str, Any]]:
    return await _dispatch(request, "start", TaskCallbackRequest, svc, auth, registry)


@task_callback_router.post("/workflow_result", response_model=Envelope[dict[str, Any]])
@envelope_errors
async def workflow_result(
    request: Request,
    svc: TaskServiceProtocol = Injected(TaskServiceProtocol),  # noqa: B008
    auth: CallbackAuthenticator = Injected(CallbackAuthenticator),  # noqa: B008
    registry: CallbackCorrelationRegistry = Injected(CallbackCorrelationRegistry),  # noqa: B008
) -> Envelope[dict[str, Any]]:
    return await _dispatch(request, "result", TaskCallbackRequest, svc, auth, registry)


@task_callback_router.post("/node_start", response_model=Envelope[dict[str, Any]])
@envelope_errors
async def node_start(
    request: Request,
    svc: TaskServiceProtocol = Injected(TaskServiceProtocol),  # noqa: B008
    auth: CallbackAuthenticator = Injected(CallbackAuthenticator),  # noqa: B008
    registry: CallbackCorrelationRegistry = Injected(CallbackCorrelationRegistry),  # noqa: B008
) -> Envelope[dict[str, Any]]:
    return await _dispatch(request, "start", TaskNodeCallbackRequest, svc, auth, registry)


@task_callback_router.post("/node_result", response_model=Envelope[dict[str, Any]])
@envelope_errors
async def node_result(
    request: Request,
    svc: TaskServiceProtocol = Injected(TaskServiceProtocol),  # noqa: B008
    auth: CallbackAuthenticator = Injected(CallbackAuthenticator),  # noqa: B008
    registry: CallbackCorrelationRegistry = Injected(CallbackCorrelationRegistry),  # noqa: B008
) -> Envelope[dict[str, Any]]:
    return await _dispatch(request, "result", TaskNodeCallbackRequest, svc, auth, registry)
