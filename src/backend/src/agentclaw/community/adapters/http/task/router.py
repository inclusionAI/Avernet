"""Task 内部 HTTP adapter routes —— 不经 gateway spanner(内部 API)。

任务模块内部前缀 ``/api/v1/collaboration/tasks``。本 router 承载:
- 公开面镜像(execute/dashboard/list 副本):供内部调用方(bot / 服务间)免 gateway spanner 直调;
  与 ``adapters/http/openapi_v1/task/`` 公开面同一 ``TaskServiceProtocol`` 委托,逻辑保持一致(改其一须同步)。
- 回投 / BBS 接力 / 任务发现阶段:前端不直面的内部写口/阶段接口。
前端公开面(经 gateway spanner)见 ``adapters/http/openapi_v1/task/``。本 router 只转协议,不持领域策略(Rule 22)。

端点(同一任务模块,不同阶段):
  POST /api/v1/collaboration/tasks/execute          — 提交任务(公开面镜像;delegate TaskServiceProtocol.execute)
  GET  /api/v1/collaboration/tasks/dashboard         — 查任务图(公开面镜像;delegate get_task_dashboard)
  GET  /api/v1/collaboration/tasks/list              — 列持久化任务记录(公开面镜像;delegate list_tasks)
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

import json
import os

import httpx
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body, HTTPException, Query, Request

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
    TaskInfoRecordDTO,
    TaskInfoRequestDTO,
    TaskNodeCallbackRequest,
    TaskOpResultDTO,
    acceptance_result_from_dto,
    callback_from_dto,
    graph_to_dto,
    op_result_to_dto,
    task_info_record_to_dto,
    task_info_request_from_dto,
    task_spec_from_dto,
)
from agentclaw.community.adapters.http.task.translator import (
    is_bcn_event_payload, is_claw_mind_payload, parse_manager_worker_bcn,
    translate, translate_bcn, translate_claw_mind,
)
from agentclaw.community.api.task.task_service import TaskServiceProtocol
from agentclaw.community.core.errors import InternalError
from agentclaw.community.core.task.domain.errors import TaskStateError
from agentclaw.community.core.task.domain.models import Status
from agentclaw.community.core.task.task_discovery.discovery_service import (
    DiscoveryService,
)
from agentclaw.community.core.task.task_discovery.scheduler import (
    TaskDiscoveryScheduler,
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
    body: TaskInfoRequestDTO,
    request: Request,
    service: TaskServiceProtocol = Injected(TaskServiceProtocol),  # noqa: B008
) -> Envelope[TaskOpResultDTO]:
    """提交执行任务(内部副本)。task_id 服务端生成;持久化 task_info(PENDING)→ initialize_graph → on_execute。

    幂等:同 task_id 已建图(GraphAlreadyInitializedError)→ ``@envelope_errors`` 映射 409。"""
    task_request = task_info_request_from_dto(body)
    result = await service.execute(task_request)
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
    if include_action_log:
        graph = service.get_task_dashboard(task_id, node_id, include_action_log=True)
    else:
        graph = service.get_task_dashboard(task_id, node_id)
    return envelope(graph_to_dto(graph, include_action_log=include_action_log), request)


@router.get("/list", response_model=Envelope[list[TaskInfoRecordDTO]])
@envelope_errors
async def list_tasks_internal(
    request: Request,
    status: str | None = None,
    user_id: str | None = Query(
        None,
        description="可选:按 owner_user_id 过滤;为空返回全量。与公开面 "
        "``/openapi/v1/.../list`` 的 owner 作用域语义对齐(内部镜像用查询参数身份,非签名 principal)",
    ),
    service: TaskServiceProtocol = Injected(TaskServiceProtocol),  # noqa: B008
) -> Envelope[list[TaskInfoRecordDTO]]:
    """列持久化 ``task_info`` 记录(内部副本,按更新时间降序;可选状态/owner 过滤)。

    非法 ``status`` 过滤值 → 400(经 ``HTTPException`` → 中央 handler → ``ErrorEnvelope``)。
    ``user_id`` 为空时不按 owner 过滤(返回全量,供内部可信调用方);传入则按 ``owner_user_id``
    过滤,与公开面 ``/openapi/v1/.../list`` 的 owner 作用域一致。"""
    if status is not None and status not in {s.value for s in Status}:
        raise HTTPException(status_code=400, detail=f"invalid status filter: {status}")
    items = service.list_tasks(status, owner_user_id=user_id)
    return envelope([task_info_record_to_dto(item) for item in items], request)


# ===== 回投 / BBS 接力 =====


@router.post("/callback/report", response_model=Envelope[dict[str, Any]])
@envelope_errors
async def report_callback(
    request: Request,
    svc: TaskServiceProtocol = Injected(TaskServiceProtocol),  # noqa: B008
    auth: CallbackAuthenticator = Injected(CallbackAuthenticator),  # noqa: B008
    registry: CallbackCorrelationRegistry = Injected(CallbackCorrelationRegistry),  # noqa: B008
) -> Envelope[dict[str, Any]]:
    """统一回投入口:仅接 ``request``,交 ``_dispatch`` 按 body 形态区分 ClawMind/BCN/羽雀 → 转换 → 入库/推进。

    ClawMind(HttpCallbackPayload)/BCN(CloudEvent)→ 转换 + ``ingest`` 只落 ``task_callback`` 审计;
    羽雀(TaskCallbackRequest,框架节点级)→ ``translate`` + ``report_result`` 落库并推进编排核。
    disposition 固定 ``result``(回投即终态/进度落库);羽雀节点级 start 仍由 task_callback_router 的
    workflow_start/node_start 端点各自走(disposition=start)。领域异常上抛 → ``@envelope_errors`` 映射。"""
    # 入口日志:打出回调原始 body(CloudEvent / HttpCallbackPayload / 羽雀 schema 都能见),便于排查。
    # Starlette request.body() 首次读后缓存,_dispatch 再读仍得同一份,不冲突。
    _body = await request.body()
    _preview = _body[:4000].decode("utf-8", "replace")
    if len(_body) > 4000:
        _preview += f"...(truncated, total {len(_body)} bytes)"
    logger.info("[report_callback] entry method=%s path=%s body=%s",
                request.method, request.url.path, _preview)
    return await _dispatch(request, "result", TaskCallbackRequest, svc, auth, registry)


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
                    "project_name": r.task.title,
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
async def get_discovery_status(
    request: Request,
    service: DiscoveryService = Injected(DiscoveryService),  # noqa: B008
) -> Envelope[dict[str, Any]]:
    """查看任务发现状态。

    返回 db 里的 task 列表 + 关联 ``_discoveries`` 内存中的 session 信息。
    有 session_id 的 task 会标注 discover 已执行；没有的说明 discover 还没跑过。
    db 读失败 → ``InternalError`` → 500。
    """
    db_path = _resolve_db_path()
    try:
        reader = SqliteTaskReader(db_path)
        tasks = reader.read_discovered_tasks()
    except Exception as exc:
        raise InternalError("status read failed") from exc

    task_list = []
    for t in tasks:
        entry: dict[str, Any] = {
            "task_id": t.task_id,
            "bot_id": t.bot_id,
            "owner_id": t.owner_id,
            "dt": t.dt,
            "project_name": t.title,
            "status": t.status,
            "priority": t.priority,
        }
        # 关联 _discoveries 内存中的 discover 结果
        result = service.get_discovery_result(t.task_id)
        if result is not None:
            entry["discovered"] = True
            entry["session_id"] = result.session.session_id if result.session else None
            entry["session_url"] = result.session.session_url if result.session else None
            entry["notification_sent"] = result.notification_sent
            entry["error"] = result.error
        else:
            entry["discovered"] = False
            entry["session_id"] = None
            entry["session_url"] = None
            entry["notification_sent"] = False
            entry["error"] = None
        task_list.append(entry)

    return envelope(
        {
            "total": len(tasks),
            "discovered": sum(1 for e in task_list if e["discovered"]),
            "tasks": task_list,
        },
        request,
    )


# ===== task-discovery 调度端点（discovery 阶段；外部 cron / 运维触发）=====
# 扁平 JSON 响应（success + 业务字段顶层直返），供外部 scheduler / 运维直接调用，
# 契约：scheduler-status 顶层 running/jobs；scheduled-trigger 顶层 total_discovered/results。
@router.get("/discovery/scheduler-status")
async def get_scheduler_status(
    scheduler: TaskDiscoveryScheduler = Injected(TaskDiscoveryScheduler),  # noqa: B008
) -> dict[str, Any]:
    """查看 APScheduler 调度状态 — running / jobs / cron / timezone / auto_start。

    透传 ``TaskDiscoveryScheduler.get_status()``，顶层追加 ``success``。
    scheduler 未启动时 running=False、jobs=[]（不报错，便于运维探活）。
    """
    try:
        status = scheduler.get_status()
    except Exception as exc:
        logger.error(
            "[task_discovery] scheduler-status failed: %s", exc, exc_info=True,
        )
        return {"success": False, "message": str(exc), "running": False, "jobs": []}
    return {"success": True, **status}


@router.post("/discovery/scheduled-trigger")
async def run_scheduled_trigger(
    service: DiscoveryService = Injected(DiscoveryService),  # noqa: B008
) -> dict[str, Any]:
    """外部 scheduler 主动触发 — 调用 ``discover_all_bots()`` 全量发现。

    遍历所有 bot 执行发现流程，按任务聚合结果。顶层 always 200，
    单任务失败体现在 ``results[].success/error``（session 创建按任务捕获，非顶层）。
    """
    logger.info("[task_discovery] scheduled-trigger received")
    try:
        results = await service.discover_all_bots()
    except Exception as exc:
        logger.error(
            "[task_discovery] scheduled-trigger failed: %s", exc, exc_info=True,
        )
        return {
            "success": False,
            "message": str(exc),
            "total_discovered": 0,
            "results": [],
        }

    payload = [
        {
            "bot_id": r.task.bot_id,
            "task_id": r.task.task_id,
            "success": r.success,
            "session_id": r.session.session_id if r.session else None,
            "session_url": r.session.session_url if r.session else None,
            "notification_sent": r.notification_sent,
            "error": r.error,
        }
        for r in results
    ]
    logger.info(
        "[task_discovery] scheduled-trigger done: total=%d ok=%d",
        len(payload),
        sum(1 for r in payload if r["success"]),
    )
    return {
        "success": True,
        "total_discovered": len(payload),
        "results": payload,
    }


@router.post("/discovery/reschedule")
async def reschedule_cron(
    cron: str = Query(..., description="新的 5 字段 cron 表达式, e.g. '30 14 * * *'"),
    timezone: str | None = Query(None, description="时区, 默认沿用当前时区"),
    scheduler: TaskDiscoveryScheduler = Injected(TaskDiscoveryScheduler),  # noqa: B008
) -> dict[str, Any]:
    """运行时修改 cron 触发时间 — 无需重启 backend。

    使用 APScheduler ``reschedule_job()`` 原地替换 job 的 trigger，
    新 cron 立即生效，旧的下一次执行计划被丢弃。

    扁平 JSON 响应（与 scheduler-status / scheduled-trigger 一致）。
    """
    logger.info("[task_discovery] reschedule received: cron='%s' tz='%s'", cron, timezone)
    try:
        ok = scheduler.reschedule(cron, timezone=timezone)
    except Exception as exc:
        logger.error(
            "[task_discovery] reschedule failed: %s", exc, exc_info=True,
        )
        return {"success": False, "message": str(exc)}
    if not ok:
        return {"success": False, "message": "scheduler not running"}

    status = scheduler.get_status()
    jobs = status.get("jobs") or []
    next_run = jobs[0].get("next_run_time") if jobs else None
    return {
        "success": True,
        "cron": cron,
        "timezone": status.get("timezone"),
        "next_run_time": next_run,
    }


@router.post("/discovery/dingtalk-config")
async def set_dingtalk_config(
    body: dict = Body(...),
) -> dict[str, Any]:
    """运行时注入钉钉凭证 + 前端 URL — 无需重启 backend。

    测试/e2e 可通过本端点注入 AK/Robot/Template 和可选的 frontend_url，
    随后的 cron fire 即用这些凭证投递卡片，card_data 内的 session_url 也用注入的 frontend_url。
    凭证仅存于进程内存，重启后失效。
    """
    from agentclaw.community.plugins.community.notify_sender import (
        DingTalkCredentialHolder,
    )

    ak_id = (body.get("ak_id") or "").strip()
    ak_secret = (body.get("ak_secret") or "").strip()
    robot_code = (body.get("robot_code") or "").strip()
    card_template_id = (body.get("card_template_id") or "").strip()
    frontend_url = (body.get("frontend_url") or "").strip()

    if not all([ak_id, ak_secret, robot_code, card_template_id]):
        return {"success": False, "message": "钉钉字段必填: ak_id, ak_secret, robot_code, card_template_id"}

    DingTalkCredentialHolder.set(ak_id, ak_secret, robot_code, card_template_id)
    injected = ["dingtalk credentials"]

    if frontend_url:
        from agentclaw.community.core.task.task_discovery.session_initiator import (
            FrontendUrlHolder,
        )
        FrontendUrlHolder.set(frontend_url)
        injected.append(f"frontend_url={frontend_url}")

    logger.info(
        "[task_discovery] injected via API: %s (robot=%s, template=%s)",
        ", ".join(injected),
        robot_code,
        card_template_id,
    )
    return {"success": True, "message": "; ".join(injected) + " injected"}


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


def _build_task_status_graph(run_detail: dict | None, graph_detail: dict | None) -> dict | None:
    """结合 BCS DAG(graph nodes+edges)与实际执行结果(run nodes)→ 任务状态图谱。

    - run_detail: ``GET /state-machine-runs/{run_id}`` 响应(run 级 status/output + nodes 执行记录)。
    - graph_detail: ``GET /state-machine-runs/{run_id}/graph`` 响应(definition + nodes DAG + edges 流转)。
    合并后每个 node 同时含 DAG 定义(display_name/kind/assignee/final_output)+ 执行结果(status/attempt/outcome/artifact_text)。
    """
    if not run_detail and not graph_detail:
        return None
    # 执行结果按 node_id 索引(来自 run_detail.nodes)
    _exec_by_node: dict[str, dict] = {}
    if run_detail:
        for _n in run_detail.get("nodes") or []:
            _nid = _n.get("node_id")
            if _nid:
                _exec_by_node[_nid] = _n
    # DAG nodes + edges(来自 graph_detail)
    _dag_nodes = (graph_detail or {}).get("nodes") or []
    _dag_edges = (graph_detail or {}).get("edges") or []
    # 合并:DAG node 定义 + 该 node 的执行结果
    _merged_nodes = []
    for _dn in _dag_nodes:
        _nid = _dn.get("node_id")
        _exec = _exec_by_node.get(_nid, {})
        _merged_nodes.append({
            "node_id": _nid,
            "display_name": _dn.get("display_name"),
            "kind": _dn.get("kind"),
            "assignee": _dn.get("assignee"),
            "final_output": _dn.get("final_output"),
            "execution": {
                "status": _exec.get("status") or _dn.get("status"),
                "attempt": _exec.get("attempt") or _dn.get("attempt"),
                "outcome": _exec.get("outcome"),
                "artifact_text": _exec.get("artifact_text"),
                "assignee_bot_id": _exec.get("assignee_bot_id"),
                "error": _exec.get("error"),
            },
        })
    # fallback:graph 无 nodes 但 run_detail 有 → 直接用 run_detail 的 nodes
    if not _merged_nodes and run_detail:
        _merged_nodes = [
            {"node_id": _n.get("node_id"), "execution": _n}
            for _n in run_detail.get("nodes") or []
        ]
    return {
        "run_status": ((run_detail or {}).get("run") or {}).get("status"),
        "run_output": ((run_detail or {}).get("run") or {}).get("output"),
        "definition": (graph_detail or {}).get("definition"),
        "nodes": _merged_nodes,
        "edges": _dag_edges,
    }


async def _dispatch(
    request: Request, disposition: str, schema_cls: type[TaskCallbackRequest],
    svc: TaskServiceProtocol, auth: CallbackAuthenticator, registry: CallbackCorrelationRegistry,
) -> Envelope[dict[str, Any]]:
    raw = await request.body()
    # 回调 body 按调用者分流:ClawMind(HttpCallbackPayload 四字段)/ BCN(CloudEvent 信封)/ 羽雀(默认 schema)。
    try:
        _raw_obj = json.loads(raw)
    except Exception:
        _raw_obj = None
    # ClawMind / BCN 是事件/工作流级回投(run_id/workflow_id 不对应框架节点):只落 task_callback 审计,
    # 不推进编排核(start_run/report_result 会 NodeNotFoundError),直接 ack。
    if is_claw_mind_payload(_raw_obj):
        auth.verify(source="claw_mind", headers=request.headers, raw_body=raw,
                    method=request.method, path=request.url.path)
        await svc.callback.ingest(translate_claw_mind(_raw_obj, disposition).data)
        return envelope({"ok": True}, request)
    if is_bcn_event_payload(_raw_obj):
        auth.verify(source="bcn", headers=request.headers, raw_body=raw,
                    method=request.method, path=request.url.path)
        # manager_worker(任务协作群)事件:走 manager_worker 分流(parse+merge 进单 session 行 +
        # session.completed 收敛),不进 state_machine 的 translate_bcn/run_detail 路径。
        if parse_manager_worker_bcn(_raw_obj) is not None:
            await svc.apply_manager_worker_event(_raw_obj)
            return envelope({"ok": True}, request)
        _tc = translate_bcn(_raw_obj)
        if _tc is None:
            return envelope({"ok": True}, request, message="bcn event not handled")
        # 从 CloudEvent 取 scope.run_id → 经 BCS GET /state-machine-runs/{run_id} 查 run 明细
        # → 把明细覆盖 _raw_callback_body,落 task_callback.orig_callback_data(而非原始 CloudEvent)。
        _run_detail: dict | None = None
        _run_id = ((_raw_obj.get("scope") or {}).get("run_id")) if isinstance(_raw_obj, dict) else None
        if _run_id:
            try:
                _bcs_base = os.environ.get("BCS_API_BASE_URL", "http://127.0.0.1:21000").rstrip("/")
                async with httpx.AsyncClient(timeout=10.0) as _cli:
                    _run_resp = await _cli.get(f"{_bcs_base}/state-machine-runs/{_run_id}")
                    _graph_resp = await _cli.get(f"{_bcs_base}/state-machine-runs/{_run_id}/graph")
                _run_detail = _run_resp.json() if _run_resp.status_code == 200 else None
                _graph_detail = _graph_resp.json() if _graph_resp.status_code == 200 else None
                if _run_detail:
                    _tc.data.data["_raw_callback_body"] = _run_detail
                    logger.info("[task_callback_report] BCN run 明细已取回 run_id=%s → orig_callback_data", _run_id)
                else:
                    logger.warning("[task_callback_report] BCS run 明细非 200 run_id=%s status=%s",
                                   _run_id, _run_resp.status_code)
                # 结合 DAG(graph nodes+edges)与执行结果(run nodes)→ 任务状态图谱 → execution_graph
                _task_graph = _build_task_status_graph(_run_detail, _graph_detail)
                if _task_graph:
                    _tc.data.data["execution_graph"] = _task_graph
                    logger.info("[task_callback_report] 任务状态图谱已构建 run_id=%s nodes=%d edges=%d → execution_graph",
                                _run_id, len(_task_graph.get("nodes") or []), len(_task_graph.get("edges") or []))
            except Exception as exc:  # noqa: BLE001 查 BCS 明细/DAG 失败不阻断落库(fallback 存原始 CloudEvent)
                logger.warning("[task_callback_report] 查 BCS run 明细/DAG 失败 run_id=%s: %s", _run_id, exc)
        await svc.callback.ingest(_tc.data)
        # 终态收敛:优先用 BCS run 明细(run_detail.run.status);fetch 失败/非 200 时,若事件本身是
        # state_machine.run.completed(BCS 已表明 run 成功完成),用事件体兜底收敛,不让 BCS 瞬时
        # 抖动丢掉终态翻转(任务节点停在 RUNNING)。按 session_id 查 task_node_run_info → 框架
        # (task_id, node_id) → svc.converge_by_session → on_report → 翻态(验收+传播+根收敛)。
        _run_status = ((_run_detail.get("run") or {}).get("status")) if _run_detail else None
        _converge_output = ((_run_detail.get("run") or {}).get("output")) if _run_detail else None
        if _run_status is None and _tc.data.data.get("status") == "state_machine.run.completed":
            _run_status = "completed"
            _converge_output = _tc.data.data.get("result", {}).get("data")
        if _run_status in ("completed", "failed", "aborted"):
            _session_id = ((_raw_obj.get("scope") or {}).get("session_id")) if isinstance(_raw_obj, dict) else None
            if _session_id:
                _success = _run_status == "completed"
                try:
                    await svc.converge_by_session(_session_id, success=_success, output=_converge_output)
                    logger.info("[task_callback_report] 终态收敛已触发 session_id=%s success=%s", _session_id, _success)
                except Exception as exc:  # noqa: BLE001 收敛失败不阻断(回调查询/落库已完成)
                    logger.warning("[task_callback_report] 终态收敛失败 session_id=%s: %s", _session_id, exc)
        return envelope({"ok": True}, request)
    # 羽雀/框架节点级回投:先按 schema_cls(TaskCallbackRequest 富 schema)校验 → translate → report_result/start_run;
    # 不符合则兜底 TaskCallbackDataDTO(loop_task_id+result,report_callback 旧契约)→ callback_from_dto → report_result。
    try:
        req = schema_cls.model_validate_json(raw)
    except Exception:
        req = None
    if req is not None:
        # source 来自已解析 body;HMAC 用原始字节。CallbackAuthError/CallbackCorrelationError 上抛 → @envelope_errors
        auth.verify(source=req.workflow_source, headers=request.headers, raw_body=raw,
                    method=request.method, path=request.url.path)
        tc = translate(req, disposition, registry)
        try:
            if tc.disposition == "start":
                await svc.callback.start_run(tc.data)
            else:
                await svc.callback.report_result(tc.data)
        except TaskStateError:
            # 幂等:result 重投到已终态节点 → 200 ack;否则 TaskStateError 上抛 → @envelope_errors 409
            if tc.disposition == "result":
                _payload = tc.data.data
                _loop_task_id = _payload.get("loop_task_id") if isinstance(_payload, dict) else ""
                cur = _find_node_status(svc, _loop_task_id)
                if cur in _TERMINAL:
                    return envelope({"ok": True}, request, message="idempotent")
            raise
        return envelope({"ok": True}, request)
    # 兜底:TaskCallbackDataDTO(loop_task_id+result)→ callback_from_dto → report_result(落库 + 推进编排核)。
    try:
        dto = TaskCallbackDataDTO.model_validate(_raw_obj if isinstance(_raw_obj, dict) else {})
    except Exception:
        raise HTTPException(status_code=422, detail="invalid callback body")
    # TaskCallbackDataDTO 无 workflow_source;按 workflow_type 取 source(单测/内部可信 Noop/singlebox 直通;
    # 生产侧该 source 应已登记密钥,否则 HMAC 校验会拒)。
    auth.verify(source=(dto.workflow_type or "single_bot"), headers=request.headers, raw_body=raw,
                method=request.method, path=request.url.path)
    await svc.callback.report_result(callback_from_dto(dto))
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
