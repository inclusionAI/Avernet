"""Task 内部 HTTP adapter routes —— 不经 gateway spanner(内部 API)。

任务模块内部前缀 ``/api/v1/collaboration/tasks``。本 router 承载:
- 公开面镜像(run-template/execute/dashboard/list 副本):供内部调用方(bot / 服务间)免 gateway spanner 直调;
  与 ``adapters/http/openapi_v1/task/`` 公开面同一 ``TaskServiceProtocol`` 委托,逻辑保持一致(改其一须同步)。
- 回投 / BBS 接力 / 任务发现阶段:前端不直面的内部写口/阶段接口。
前端公开面(经 gateway spanner)见 ``adapters/http/openapi_v1/task/``。本 router 只转协议,不持领域策略(Rule 22)。

端点(同一任务模块,不同阶段):
  POST /api/v1/collaboration/tasks/run-template     — 运行预置静态模板(delegate TaskServiceProtocol.run_template)
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
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request

from agentclaw.community.adapters.http.openapi_v1.contracts import Envelope, Page
from agentclaw.community.adapters.http.openapi_v1.responses import (
    envelope,
    envelope_errors,
    page as page_envelope,
)
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
    TemplateRunRequestDTO,
    TaskNodeCallbackRequest,
    TaskOpResultDTO,
    acceptance_result_from_dto,
    callback_from_dto,
    graph_to_dto,
    op_result_to_dto,
    TaskSettingRequestDTO,
    TaskSettingStateDTO,
    TaskClaimJoinFilterRequestDTO,
    TaskClaimJoinFilterStateDTO,
    TaskGrantRequestDTO,
    TaskGrantResultDTO,
    TaskRevokeRequestDTO,
    TaskRevokeResultDTO,
    task_info_record_to_dto,
    task_info_request_from_dto,
    task_spec_from_dto,
)
from agentclaw.community.adapters.http.task.translator import (
    is_bcn_event_payload,
    is_claw_mind_payload,
    is_common_task_payload,
    parse_manager_worker_bcn,
    translate,
    translate_bcn,
    translate_claw_mind,
    translate_common_task_callback
)
from agentclaw.community.core.task.task_runner.integration.callback_data_enricher import (
    CallbackDataEnricher,
)
from agentclaw.community.adapters.http.auth.dependencies import get_current_user
from agentclaw.community.adapters.http.auth.models import AuthenticatedUser
from agentclaw.community.core.task.task_dispatch.claim_join_gate import (
    CLAIM_JOIN_FILTER,
    SEARCH_SKILL,
    SINGLE_BOT_SKILL_REPORT,
    TaskClaimJoinGateProtocol,
    TaskSettingsServiceProtocol,
)
from agentclaw.community.api.task.task_grant_service import (
    TaskClaimGrantServiceProtocol,
)
from agentclaw.community.api.task.task_service import TaskServiceProtocol
from agentclaw.community.core.errors import InternalError
from agentclaw.community.utils.env_utils import get_current_env
from agentclaw.community.core.task.domain.errors import TaskStateError
from agentclaw.community.core.task.domain.models import Status
from agentclaw.community.core.task.task_discovery.discovery_service import (
    DiscoveryService,
)
from agentclaw.community.core.task.task_discovery.scheduler import (
    TaskDiscoveryScheduler,
)

from agentclaw.community.core.task.task_discovery.task_reader import (
    TaskReader,
    clear_discovered_tasks,
    upsert_discovered_tasks,
)
from agentclaw.community.core.task.task_runner.callback_correlation import (
    CallbackCorrelationRegistry,
)
from agentclaw.community.di import Injected
from agentclaw.community.plugin_api.database import DatabasePlugin
from agentclaw.community.log import get_logger

logger = get_logger()

router = APIRouter(prefix="/api/v1/collaboration/tasks", tags=["task"])


# ===== 公开面镜像(run-template/execute/dashboard/list;内部 /api/v1 副本,不经 spanner)=====
# 与 ``adapters/http/openapi_v1/task/router.py`` 公开面同一 ``TaskServiceProtocol`` 委托,
# 逻辑保持一致 —— 内部调用方(bot / 服务间)走此副本免 gateway spanner。改其一须同步。


@router.post("/run-template", response_model=Envelope[TaskOpResultDTO])
@envelope_errors
async def run_template_internal(
    body: TemplateRunRequestDTO,
    request: Request,
    user: AuthenticatedUser = Depends(get_current_user),
    service: TaskServiceProtocol = Injected(TaskServiceProtocol),  # noqa: B008
) -> Envelope[TaskOpResultDTO]:
    """运行预置静态模板(内部接口)。

    与动态任务 ``/execute`` 同属内部 Task API；调用方只提交模板标识和模板输入，
    模板节点、Bot 绑定及 DAG 由后端配置提供。触发者身份从 IAM cookie 解析:
    ``owner_user_id=user.staffId``(工号)→ 持久化 task_info,面板可按 owner 过滤;
    ``owner_account_id=user.operatorName``(账号)→ 存入 execution_config,
    供 engine notify 终端节点取 DingTalk account_id 发钉钉(免硬编码兜底)。
    """
    result = await service.run_template(
        body.template_id,
        body.input,
        owner_user_id=user.staffId,
        owner_bot_id="",
        auto_advance=body.auto_advance,
        owner_account_id=user.operatorName,
    )
    return envelope(op_result_to_dto(result), request)


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


@router.get(
    "/list",
    response_model=Envelope[list[TaskInfoRecordDTO] | Page[TaskInfoRecordDTO]],
)
@envelope_errors
async def list_tasks_internal(
    request: Request,
    status: str | None = None,
    user_id: str | None = Query(
        None,
        description="可选:按 owner_user_id 过滤;为空返回全量。与公开面 "
        "``/openapi/v1/.../list`` 的 owner 作用域语义对齐(内部镜像用查询参数身份,非签名 principal)",
    ),
    page: int | None = Query(
        None, ge=1, description="分页页码(1-based);不传则不分页,返回全量。"
    ),
    page_size: int | None = Query(
        None, ge=1, le=100, description="每页条数(1-100);不传则不分页,返回全量。"
    ),
    service: TaskServiceProtocol = Injected(TaskServiceProtocol),  # noqa: B008
) -> Envelope[list[TaskInfoRecordDTO] | Page[TaskInfoRecordDTO]]:
    """列持久化 ``task_info`` 记录(内部副本,按更新时间降序;可选状态/owner 过滤)。

    非法 ``status`` 过滤值 → 400(经 ``HTTPException`` → 中央 handler → ``ErrorEnvelope``)。
    ``user_id`` 为空时不按 owner 过滤(返回全量,供内部可信调用方);传入则按 ``owner_user_id``
    过滤,与公开面 ``/openapi/v1/.../list`` 的 owner 作用域一致。

    分页为可选入参(与公开面同步):page/page_size 均不传时 data 为列表(历史契约);
    两者同时传入时返回 Page(total, items);仅传其一 → 400。"""
    if status is not None and status not in {s.value for s in Status}:
        raise HTTPException(status_code=400, detail=f"invalid status filter: {status}")
    if (page is None) != (page_size is None):
        raise HTTPException(
            status_code=400,
            detail="page and page_size must be both provided or both omitted",
        )
    if page_size is None:
        items = service.list_tasks(status, owner_user_id=user_id)
        return envelope([task_info_record_to_dto(item) for item in items], request)
    items, total = service.list_tasks_page(
        status, owner_user_id=user_id, page=page or 1, page_size=page_size
    )
    return page_envelope(
        total,
        [task_info_record_to_dto(item) for item in items],
        request,
    )


# ===== 任务认领 Bot 授权(grant/revoke,无状态中继) =====
# 前端开「任务认领」时调:grant/revoke 透传浏览器 Cookie/Referer 到 secbaas admin(api-key 服务端持有,不落本地表)。
# 内部面(/api/v1, BUC 登录态,operator=staffId);对外另有公开面 /openapi/v1(.../grant, /revoke,经 gateway spanner)。


@router.post("/grant", response_model=Envelope[TaskGrantResultDTO])
@envelope_errors
async def grant_task_claim(
    body: TaskGrantRequestDTO,
    request: Request,
    user: AuthenticatedUser = Depends(get_current_user),
    service: TaskClaimGrantServiceProtocol = Injected(TaskClaimGrantServiceProtocol),  # noqa: B008
) -> Envelope[TaskGrantResultDTO]:
    """grant 公共 api-key 给某 Bot(透传人类 Cookie/Referer 到 secbaas;api-key 服务端持有,不落表)。

    ``bcs_bot_id``=real:entity(/mine bot.id)。secbaas 401/403(未登录/非 Bot owner/非管理员)
    → OpenApiAuthError → ``@envelope_errors`` 映射;4xx/5xx 可重试;幂等。"""
    result = await service.grant(
        bcs_bot_id=body.bcs_bot_id,
        cookie=request.headers.get("cookie", ""),
        referer=request.headers.get("referer", ""),
        operator=user.id,
    )
    return envelope(
        TaskGrantResultDTO(
            bcs_bot_id=result.bcs_bot_id,
            api_key_prefix=result.api_key_prefix,
            grant_status=result.grant_status,
            operator=result.operator,
        ),
        request,
    )


@router.post("/revoke", response_model=Envelope[TaskRevokeResultDTO])
@envelope_errors
async def revoke_task_claim(
    body: TaskRevokeRequestDTO,
    request: Request,
    user: AuthenticatedUser = Depends(get_current_user),
    service: TaskClaimGrantServiceProtocol = Injected(TaskClaimGrantServiceProtocol),  # noqa: B008
) -> Envelope[TaskRevokeResultDTO]:
    """撤销授权(透传人类 Cookie/Referer → secbaas revoke)。幂等(无记录/已 revoked 也返回 revoked)。"""
    result = await service.revoke(
        bcs_bot_id=body.bcs_bot_id,
        cookie=request.headers.get("cookie", ""),
        referer=request.headers.get("referer", ""),
        operator=user.id,
    )
    return envelope(
        TaskRevokeResultDTO(
            bcs_bot_id=result.bcs_bot_id,
            grant_status=result.grant_status,
        ),
        request,
    )


# ===== 通用任务开关(GET/POST /settings) =====


@router.get(
    "/settings",
    response_model=Envelope[list[TaskSettingStateDTO]],
)
@envelope_errors
async def get_task_settings(
    request: Request,
    user: AuthenticatedUser = Depends(get_current_user),
    service: TaskSettingsServiceProtocol = Injected(TaskSettingsServiceProtocol),  # noqa: B008
) -> Envelope[list[TaskSettingStateDTO]]:
    """读取全部已支持的任务开关状态。"""
    env = get_current_env()
    setting_types = (CLAIM_JOIN_FILTER, SEARCH_SKILL, SINGLE_BOT_SKILL_REPORT)
    states = [
        TaskSettingStateDTO(
            setting_type=setting_type,
            enabled=service.get_enabled(setting_type=setting_type, env=env),
            env=env,
        )
        for setting_type in setting_types
    ]
    logger.info(
        "[task][settings] GET all env=%s operator=%s states=%s",
        env,
        user.id,
        [(state.setting_type, state.enabled) for state in states],
    )
    return envelope(states, request)


@router.post(
    "/settings",
    response_model=Envelope[TaskSettingStateDTO],
)
@envelope_errors
async def set_task_setting(
    body: TaskSettingRequestDTO,
    request: Request,
    user: AuthenticatedUser = Depends(get_current_user),
    service: TaskSettingsServiceProtocol = Injected(TaskSettingsServiceProtocol),  # noqa: B008
) -> Envelope[TaskSettingStateDTO]:
    """根据请求体开启或关闭指定任务开关。"""
    env = get_current_env()
    enabled = service.set_enabled(
        setting_type=body.setting_type,
        enabled=body.enabled,
        env=env,
        operator=user.id,
    )
    logger.info(
        "[task][settings] POST setting_type=%s requested=%s effective=%s env=%s operator=%s",
        body.setting_type,
        body.enabled,
        enabled,
        env,
        user.id,
    )
    return envelope(
        TaskSettingStateDTO(setting_type=body.setting_type, enabled=enabled, env=env),
        request,
    )


# 旧路径保留为隐藏兼容入口，新的调用方应使用 /settings?setting_type=...。
@router.get(
    "/claim-join-filter",
    response_model=Envelope[TaskClaimJoinFilterStateDTO],
    include_in_schema=False,
)
@envelope_errors
async def get_task_claim_join_filter(
    request: Request,
    user: AuthenticatedUser = Depends(get_current_user),
    service: TaskClaimJoinGateProtocol = Injected(TaskClaimJoinGateProtocol),  # noqa: B008
) -> Envelope[TaskClaimJoinFilterStateDTO]:
    env = get_current_env()
    return envelope(
        TaskClaimJoinFilterStateDTO(enabled=service.get_enabled(env=env), env=env),
        request,
    )


@router.post(
    "/claim-join-filter",
    response_model=Envelope[TaskClaimJoinFilterStateDTO],
    include_in_schema=False,
)
@envelope_errors
async def set_task_claim_join_filter(
    body: TaskClaimJoinFilterRequestDTO,
    request: Request,
    user: AuthenticatedUser = Depends(get_current_user),
    service: TaskClaimJoinGateProtocol = Injected(TaskClaimJoinGateProtocol),  # noqa: B008
) -> Envelope[TaskClaimJoinFilterStateDTO]:
    env = get_current_env()
    enabled = service.set_enabled(enabled=body.enabled, env=env, operator=user.id)
    return envelope(
        TaskClaimJoinFilterStateDTO(enabled=enabled, env=env),
        request,
    )


# ===== 回投 / BBS 接力 =====


@router.post("/callback/report", response_model=Envelope[dict[str, Any]])
@envelope_errors
async def report_callback(
    request: Request,
    svc: TaskServiceProtocol = Injected(TaskServiceProtocol),  # noqa: B008
    auth: CallbackAuthenticator = Injected(CallbackAuthenticator),  # noqa: B008
    registry: CallbackCorrelationRegistry = Injected(CallbackCorrelationRegistry),  # noqa: B008
    enricher: CallbackDataEnricher = Injected(CallbackDataEnricher),  # noqa: B008
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
    logger.info(
        "[task_callback] entry method=%s path=%s body=%s",
        request.method,
        request.url.path,
        _preview,
    )
    return await _dispatch(request, "result", TaskCallbackRequest, svc, auth, registry, enricher)


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
    node = service.attach_bbs_node(
        body.task_id, body.parent_node_id, task_spec, body.bot_id
    )
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
    ar = (
        acceptance_result_from_dto(body.acceptance_result)
        if body.acceptance_result
        else None
    )
    await service.report_bbs_result(
        body.task_id,
        body.node_id,
        body.bot_id,
        acceptance_result=ar,
        output_patch=body.output_patch,
        exec_error=body.exec_error,
    )
    return envelope({"ok": True}, request)


# ===== 任务发现阶段(任务模块的一个阶段,非独立模块)=====


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
        user_id,
        agent_id,
        bot_id,
    )
    try:
        results = await service.discover(
            bot_id=bot_id,
            owner_id=owner_id,
            agent_id=agent_id,
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
    reader: TaskReader = Injected(TaskReader),  # noqa: B008
    service: DiscoveryService = Injected(DiscoveryService),  # noqa: B008
) -> Envelope[dict[str, Any]]:
    """查看任务发现状态。

    返回 db 里的 task 列表 + 关联 ``_discoveries`` 内存中的 session 信息。
    有 session_id 的 task 会标注 discover 已执行；没有的说明 discover 还没跑过。
    db 读失败 → ``InternalError`` → 500。
    """
    try:
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
            entry["session_url"] = (
                result.session.session_url if result.session else None
            )
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


@router.post("/discovery/tasks", response_model=Envelope[dict[str, Any]])
@envelope_errors
async def write_discovered_tasks(
    request: Request,
    tasks: list[dict[str, Any]] = Body(..., embed=True),
    db: DatabasePlugin = Injected(DatabasePlugin),  # noqa: B008
) -> Envelope[dict[str, Any]]:
    """写入已发现任务（upsert 语义）。

    按 ``task_id`` 自然键判断：已存在则更新，不存在则插入。
    跨 SQLite / OceanBase 兼容。供外部系统或 e2e 测试写入已发现任务数据。

    Body::

        {"tasks": [{"task_id": "...", "bot_id": "...", ...}, ...]}
    """
    try:
        count = upsert_discovered_tasks(db, tasks)
    except Exception as exc:
        raise InternalError("write discovered tasks failed") from exc
    return envelope({"written": count}, request)


@router.delete("/discovery/tasks", response_model=Envelope[dict[str, Any]])
@envelope_errors
async def clear_discovered_tasks_endpoint(
    request: Request,
    db: DatabasePlugin = Injected(DatabasePlugin),  # noqa: B008
) -> Envelope[dict[str, Any]]:
    """清空所有已发现任务数据。

    供测试清理或运维重置使用。
    """
    try:
        count = clear_discovered_tasks(db)
    except Exception as exc:
        raise InternalError("clear discovered tasks failed") from exc
    return envelope({"cleared": count}, request)


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
            "[task_discovery] scheduler-status failed: %s",
            exc,
            exc_info=True,
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
            "[task_discovery] scheduled-trigger failed: %s",
            exc,
            exc_info=True,
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
    logger.info(
        "[task_discovery] reschedule received: cron='%s' tz='%s'", cron, timezone
    )
    try:
        ok = scheduler.reschedule(cron, timezone=timezone)
    except Exception as exc:
        logger.error(
            "[task_discovery] reschedule failed: %s",
            exc,
            exc_info=True,
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
    logger.debug("[task_discovery] → set_dingtalk_config(body_keys=%s)", sorted(body.keys()))
    from agentclaw.community.plugins.community.notify_sender import (
        DingTalkCredentialHolder,
    )

    ak_id = (body.get("ak_id") or "").strip()
    ak_secret = (body.get("ak_secret") or "").strip()
    robot_code = (body.get("robot_code") or "").strip()
    card_template_id = (body.get("card_template_id") or "").strip()
    frontend_url = (body.get("frontend_url") or "").strip()

    if not all([ak_id, ak_secret, robot_code, card_template_id]):
        return {
            "success": False,
            "message": "钉钉字段必填: ak_id, ak_secret, robot_code, card_template_id",
        }

    DingTalkCredentialHolder.set(ak_id, ak_secret, robot_code, card_template_id)
    injected = ["dingtalk credentials"]

    if frontend_url:
        logger.info(
            "[task_discovery] frontend_url provided via API (no-op, use DI FrontendUrlProvider instead): %s",
            frontend_url,
        )

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
task_callback_router = APIRouter(
    prefix="/api/v1/collaboration/tasks/callback", tags=["task-callback"]
)

_TERMINAL = {Status.DONE, Status.FAILED, Status.HUNG}


def _find_node_status(svc: TaskServiceProtocol, loop_task_id: str) -> Status | None:
    task_id, node_id = loop_task_id.split("::", 1)
    graph = svc.get_task_dashboard(task_id)
    node = next((n for n in graph.tasks if n.node_id == node_id), None)
    return node.status if node is not None else None


def _session_id_of(raw_obj: Any) -> str:
    """从原始回调 body 提取 session_id(即落库 ``main_session_id`` 源),供入口/链路日志关联。

    BCN / manager_worker = ``scope.session_id``;ClawMind = ``ext_info.flow_runs.origin_session_id``;
    羽雀 schema / 兜底 DTO 形态不含,返 ``""``(其 session_id 经 translate 后落在 ``workflow_instance_id``)。"""
    if not isinstance(raw_obj, dict):
        return ""
    scope = raw_obj.get("scope")
    if isinstance(scope, dict) and scope.get("session_id"):
        return str(scope["session_id"])
    ext = raw_obj.get("ext_info")
    if isinstance(ext, dict):
        flow_runs = ext.get("flow_runs")
        if isinstance(flow_runs, dict) and flow_runs.get("origin_session_id"):
            return str(flow_runs["origin_session_id"])
    return ""


async def _dispatch(
    request: Request,
    disposition: str,
    schema_cls: type[TaskCallbackRequest],
    svc: TaskServiceProtocol,
    auth: CallbackAuthenticator,
    registry: CallbackCorrelationRegistry,
    enricher: CallbackDataEnricher,
) -> Envelope[dict[str, Any]]:
    raw = await request.body()
    # 回调 body 按调用者分流:ClawMind(HttpCallbackPayload 四字段)/ BCN(CloudEvent 信封)/ 羽雀(默认 schema)。
    try:
        _raw_obj = json.loads(raw)
    except Exception:
        _raw_obj = None
    _sid = _session_id_of(_raw_obj)  # session_id(主回投键)→ 入口/链路各日志关联
    # ClawMind / BCN 是事件/工作流级回投(run_id/workflow_id 不对应框架节点):只落 task_callback 审计,
    # 不推进编排核(start_run/report_result 会 NodeNotFoundError),直接 ack。
    if is_claw_mind_payload(_raw_obj):
        logger.info("[task_callback] claw_mind callback received session_id=%s", _sid)
        auth.verify(
            source="claw_mind",
            headers=request.headers,
            raw_body=raw,
            method=request.method,
            path=request.url.path,
        )
        _tc = translate_claw_mind(_raw_obj, disposition)
        enricher.enrich_claw_mind(_tc.data, _raw_obj)
        await svc.callback.ingest(_tc.data)
        return envelope({"ok": True}, request)
    if is_bcn_event_payload(_raw_obj):
        logger.info("[task_callback] bcn event received session_id=%s", _sid)
        auth.verify(
            source="bcn",
            headers=request.headers,
            raw_body=raw,
            method=request.method,
            path=request.url.path,
        )
        # manager_worker(任务协作群)事件:走 manager_worker 分流(parse+merge 进单 session 行 +
        # session.completed 收敛),不进 state_machine 的 translate_bcn/run_detail 路径。
        if parse_manager_worker_bcn(_raw_obj) is not None:
            logger.info("[task_callback] is_manager_worker_event, session_id=%s", _sid)
            await svc.apply_manager_worker_event(_raw_obj)
            return envelope({"ok": True}, request)

        logger.info("[task_callback] is_state_machine_event, session_id=%s", _sid)
        _tc = translate_bcn(_raw_obj)
        if _tc is None:
            return envelope({"ok": True}, request, message="bcn event not handled")
        # 回调数据处理(execution_graph 构建 + run 明细 → extend_props)统一交 CallbackDataEnricher,
        # base_url 取自注入的 BcsTokenProvider(替代原 os.environ BCS_API_BASE_URL/httpx 内联)。
        _run_id = (
            ((_raw_obj.get("scope") or {}).get("run_id"))
            if isinstance(_raw_obj, dict)
            else None
        )
        logger.info("[task_callback] bcn_event_run_id=%s session_id=%s", _run_id, _sid)
        # 1) 先落原始回调数据(translate 后 minimal:orig=CloudEvent + main_session_id + run_id;
        #    execution_graph/extend_props 暂空)——回调到达即留底,后续解析/查 BCS 失败也不丢原始记录。
        await svc.callback.ingest(_tc.data)
        # 2) 解析/转换:经 enricher 查 BCS run 明细 + 构建 execution_graph + 落 extend_props(改写 _tc.data)。
        _run_detail = (
            await enricher.enrich_bcn(_tc.data, _raw_obj, _run_id)
            if _run_id
            else None
        )
        # 3) 更新这条落库数据(补 execution_graph + extend_props;同 run_id/node_id upsert 覆盖)。
        await svc.callback.ingest(_tc.data)
        # 终态收敛:优先用 BCS run 明细(run_detail.run.status);fetch 失败/非 200 时,若事件本身是
        # state_machine.run.completed(BCS 已表明 run 成功完成),用事件体兜底收敛,不让 BCS 瞬时
        # 抖动丢掉终态翻转(任务节点停在 RUNNING)。按 session_id 查 task_node_run_info → 框架
        # (task_id, node_id) → svc.converge_by_session → on_report → 翻态(验收+传播+根收敛)。
        _run_status = (
            ((_run_detail.get("run") or {}).get("status")) if _run_detail else None
        )
        _converge_output = (
            ((_run_detail.get("run") or {}).get("output")) if _run_detail else None
        )
        if (
            _run_status is None
            and isinstance(_raw_obj, dict)
            and _raw_obj.get("event_type") == "state_machine.run.completed"
        ):
            _run_status = "completed"
            _converge_output = _tc.data.data.get("result", {}).get("data")
        if _run_status in ("completed", "failed", "aborted"):
            _session_id = (
                ((_raw_obj.get("scope") or {}).get("session_id"))
                if isinstance(_raw_obj, dict)
                else None
            )
            if _session_id:
                _success = _run_status == "completed"
                try:
                    await svc.converge_by_session(
                        _session_id, success=_success, output=_converge_output
                    )
                    logger.info(
                        "[task_callback_report] 终态收敛已触发 session_id=%s success=%s",
                        _session_id,
                        _success,
                    )
                except Exception as exc:  # noqa: BLE001 收敛失败不阻断(回调查询/落库已完成)
                    logger.warning(
                        "[task_callback_report] 终态收敛失败 session_id=%s: %s",
                        _session_id,
                        exc,
                    )
        logger.info("[task_callback] finish_process_callback session_id=%s run_id=%s", _sid, _run_id)
        return envelope({"ok": True}, request)

    tc = None
    if is_common_task_payload(_raw_obj):
        logger.info("[task_callback] common_task_callback, begin, task=%s", _raw_obj)

        tc = translate_common_task_callback(_raw_obj)
        await svc.callback.ingest(tc.data)
        logger.info("[task_callback] common_task_callback, finish")

    logger.info("[task_callback] report_callback_to_driver_engine, begin")
    if tc is not None:
        try:
            if tc.disposition == "start":
                logger.info("[task_callback] report_callback_to_driver_engine, type=start")
                await svc.callback.start_run(tc.data)
            else:
                logger.info("[task_callback] report_callback_to_driver_engine, type=result")
                await svc.callback.report_result(tc.data)
        except TaskStateError as e:
            logger.error("[task_callback] report_callback_to_driver_engine, meet exception = %s", e)
            raise (f"[task_callback] report_callback_to_driver_engine, meet exception = {str(e)}")
        return envelope({"ok": True}, request)

    logger.info("[task_callback] report_callback_to_driver_engine, finish")
    return envelope({"ok": True}, request)


@task_callback_router.post("/workflow_start", response_model=Envelope[dict[str, Any]])
@envelope_errors
async def workflow_start(
    request: Request,
    svc: TaskServiceProtocol = Injected(TaskServiceProtocol),  # noqa: B008
    auth: CallbackAuthenticator = Injected(CallbackAuthenticator),  # noqa: B008
    registry: CallbackCorrelationRegistry = Injected(CallbackCorrelationRegistry),  # noqa: B008
    enricher: CallbackDataEnricher = Injected(CallbackDataEnricher),  # noqa: B008
) -> Envelope[dict[str, Any]]:
    return await _dispatch(request, "start", TaskCallbackRequest, svc, auth, registry, enricher)


@task_callback_router.post("/workflow_result", response_model=Envelope[dict[str, Any]])
@envelope_errors
async def workflow_result(
    request: Request,
    svc: TaskServiceProtocol = Injected(TaskServiceProtocol),  # noqa: B008
    auth: CallbackAuthenticator = Injected(CallbackAuthenticator),  # noqa: B008
    registry: CallbackCorrelationRegistry = Injected(CallbackCorrelationRegistry),  # noqa: B008
    enricher: CallbackDataEnricher = Injected(CallbackDataEnricher),  # noqa: B008
) -> Envelope[dict[str, Any]]:
    return await _dispatch(request, "result", TaskCallbackRequest, svc, auth, registry, enricher)


@task_callback_router.post("/node_start", response_model=Envelope[dict[str, Any]])
@envelope_errors
async def node_start(
    request: Request,
    svc: TaskServiceProtocol = Injected(TaskServiceProtocol),  # noqa: B008
    auth: CallbackAuthenticator = Injected(CallbackAuthenticator),  # noqa: B008
    registry: CallbackCorrelationRegistry = Injected(CallbackCorrelationRegistry),  # noqa: B008
    enricher: CallbackDataEnricher = Injected(CallbackDataEnricher),  # noqa: B008
) -> Envelope[dict[str, Any]]:
    return await _dispatch(
        request, "start", TaskNodeCallbackRequest, svc, auth, registry, enricher
    )


@task_callback_router.post("/node_result", response_model=Envelope[dict[str, Any]])
@envelope_errors
async def node_result(
    request: Request,
    svc: TaskServiceProtocol = Injected(TaskServiceProtocol),  # noqa: B008
    auth: CallbackAuthenticator = Injected(CallbackAuthenticator),  # noqa: B008
    registry: CallbackCorrelationRegistry = Injected(CallbackCorrelationRegistry),  # noqa: B008
    enricher: CallbackDataEnricher = Injected(CallbackDataEnricher),  # noqa: B008
) -> Envelope[dict[str, Any]]:
    return await _dispatch(
        request, "result", TaskNodeCallbackRequest, svc, auth, registry, enricher
    )
