"""Internal task HTTP adapter: execute, callbacks, settings and discovery.

Domain work is delegated to ``TaskServiceProtocol``; this module only adapts transport DTOs and maps errors to the shared response envelope."""

from __future__ import annotations

import json
import time
from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request

from agentclaw.community.adapters.http.openapi_v1.contracts import Envelope
from agentclaw.community.adapters.http.openapi_v1.responses import (
    envelope,
    envelope_errors,
)
from agentclaw.community.adapters.http.task.auth import CallbackAuthenticator
from agentclaw.community.adapters.http.task.schemas import (
    TaskCallbackDataDTO,
    TaskCallbackRequest,
    TaskInfoRequestDTO,
    TaskNodeUpdateDTO,
    RelayTaskEventDTO,
    TaskNodeCallbackRequest,
    TaskOpResultDTO,
    acceptance_result_from_dto,
    callback_from_dto,
    op_result_to_dto,
    TaskSettingRequestDTO,
    TaskSettingStateDTO,
    TaskGrantRequestDTO,
    TaskGrantResultDTO,
    TaskRevokeRequestDTO,
    TaskRevokeResultDTO,
    TaskTrajectoryDTO,
    task_info_request_from_dto,
    task_spec_from_dto,
    trajectory_to_dto,
)
from agentclaw.community.adapters.http.task.relay_routes import router as relay_api_router
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
from agentclaw.community.core.task.task_runner.client.callback_data_enricher import (
    CallbackDataEnricher,
)
from agentclaw.community.adapters.http.auth.dependencies import get_current_user
from agentclaw.community.adapters.http.auth.models import AuthenticatedUser
from agentclaw.community.core.task.task_dispatch.claim_join_gate import (
    CLAIM_JOIN_FILTER,
    HARNESS_POLLER,
    SEARCH_SKILL,
    SKILL_REPORT,
    RELAY_EXECUTION,
    TaskSettingsServiceProtocol,
)
from agentclaw.community.api.task.task_grant_service import (
    TaskClaimGrantServiceProtocol,
)
from agentclaw.community.api.task.task_service import TaskServiceProtocol
from agentclaw.community.api.task.task_trajectory_service import (
    TaskTrajectoryServiceProtocol,
)
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
router.include_router(relay_api_router)

# ===== 公开面镜像(execute;内部 /api/v1 副本,不经 spanner)=====
# 与 ``adapters/http/openapi_v1/task/router.py`` 公开面同一 ``TaskServiceProtocol`` 委托,逻辑一致 —— 内部调用方走此副本免 gateway spanner。改其一须同步。


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


@router.get("/trajectory", response_model=Envelope[TaskTrajectoryDTO])
@envelope_errors
async def get_task_trajectory_internal(
    task_id: Annotated[str, Query(description="任务ID(创建时签发, bots 列表返回的 task_id)")],
    request: Request,
    do_analysis: Annotated[
        bool, Query(description="是否触发 bot 总体分析(默认关闭)")
    ] = False,
    service: TaskTrajectoryServiceProtocol = Injected(TaskTrajectoryServiceProtocol),  # noqa: B008
) -> Envelope[TaskTrajectoryDTO]:
    """读取任务轨迹(内部副本;与公开面 ``adapters/http/openapi_v1/task/router.py`` 同一委托,逻辑一致,改其一须同步)。

    do_analysis=false(默认,纯读):返回 TaskTrajectory,analysis 取已落库值或 None,不写库不调 bot;
    do_analysis=true:调 DI 注入 bot 做总体分析→ 覆盖回填 analysis+gmt_modified→ 返回同形态 TaskTrajectory。
    bot 未配置→503;bot 失败/超时→504 且不回填(决策 #10/#14)。原 /analysis 端点已并入此入口(决策 #10)。"""
    trajectory = await service.get_trajectory(task_id, do_analysis=do_analysis)
    return envelope(trajectory_to_dto(trajectory), request)


# ===== 任务认领 Bot 授权(grant/revoke,无状态中继) =====
# grant/revoke 透传人类 Cookie/Referer 到 secbaas admin(api-key 服务端持有,不落表):内部面(/api/v1,BUC,operator=staffId);公开面 /openapi/v1(.../grant,/revoke,经 gateway spanner)。


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
    setting_types = (CLAIM_JOIN_FILTER, HARNESS_POLLER, SEARCH_SKILL, SKILL_REPORT, RELAY_EXECUTION)
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

    ClawMind/BCN→ 转换+``ingest`` 只落 ``task_callback`` 审计;羽雀(框架节点级)→ ``translate``+``report_result`` 落库推进。
    disposition 固定 ``result``;羽雀节点级 start 由 task_callback_router 的 workflow_start/node_start 端点走(start);领域异常→ ``@envelope_errors`` 映射。"""
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


@router.post("/nodes/update", response_model=Envelope[dict[str, Any]])
@envelope_errors
async def update_task_node(
    body: TaskNodeUpdateDTO,
    request: Request,
    service: TaskServiceProtocol = Injected(TaskServiceProtocol),  # noqa: B008
) -> Envelope[dict[str, Any]]:
    """内部节点写口:直接更新节点 run_info(经 ``update_task_node_info`` → ``ExecutionEngine.on_report`` 落库并触发翻态/验收/收敛传播)。

    透传 ``TaskNodePatch`` 三选一终态翻转(互斥):``acceptance_result`` 验收 / ``exec_error`` 执行报错(→ on_harness 重投)/
    ``status`` 框架直驱;三空仅 fold 非状态字段。供内部/测试直驱节点状态,不经 BBS claim 校验(区别于 ``bbs/result``)。领域异常→ ``@envelope_errors`` 映射。"""
    ar = (
        acceptance_result_from_dto(body.acceptance_result)
        if body.acceptance_result
        else None
    )
    result = await service.update_task_node_info(
        body.task_id,
        body.node_id,
        status=body.status,
        run_mode=body.run_mode,
        assignee=body.assignee,
        output_patch=body.output_patch,
        acceptance_result=ar,
        exec_error=body.exec_error,
        progress_reason=body.progress_reason,
        failure_reason=body.failure_reason,
        extend_props_patch=body.extend_props_patch,
    )
    return envelope(
        {
            "task_id": result.task_id,
            "node_id": result.node_id,
            "success": result.success,
            "prev_status": result.prev_status.value if result.prev_status else None,
            "new_status": result.new_status.value if result.new_status else None,
            "error": result.error,
        },
        request,
    )


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

    session-creation 错误按任务捕获(非顶层),discover 总返 200 ``Envelope``(失色任务在 tasks[].success/error 体现);仅顶层 discover 失败 → ``InternalError`` → 500。"""
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
                    # 2026-09-16: 通知明细拆分 — notification_sent 是
                    # card_sent or work_order_sent 的聚合;工单通道(本地 DB)
                    # 几乎必成功, 单看聚合会掩盖外发卡片失败。
                    "notification_sent": r.notification_sent,
                    "card_sent": r.card_sent,
                    "work_order_sent": r.work_order_sent,
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
    """查看任务发现状态:返回 db task 列表 + 关联 ``_discoveries`` 内存的 session 信息。

    有 session_id 的 task = discover 已跑过;db 读失败 → ``InternalError`` → 500。"""
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
            # 2026-09-16: 通知明细拆分(见 discover 端点同名注释)。
            entry["notification_sent"] = result.notification_sent
            entry["card_sent"] = result.card_sent
            entry["work_order_sent"] = result.work_order_sent
            entry["error"] = result.error
        else:
            entry["discovered"] = False
            entry["session_id"] = None
            entry["session_url"] = None
            entry["notification_sent"] = False
            entry["card_sent"] = False
            entry["work_order_sent"] = False
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
    """写入已发现任务(upsert 语义:按 ``task_id`` 自然键,跨 SQLite/OceanBase 兼容)。供外部系统/e2e 测试写入。

    Body: ``{"tasks": [{"task_id": "...", "bot_id": "...", ...}, ...]}`` 。"""
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
    """清空所有已发现任务数据(供测试清理或运维重置)。"""
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
    """查看 APScheduler 调度状态(running/jobs/cron/timezone/auto_start):透传 ``TaskDiscoveryScheduler.get_status()`` 顶层追加 ``success``。

    scheduler 未启动时 running=False、jobs=[](不报错,便于运维探活)。"""
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
    """运行时修改 cron 触发时间(无需重启 backend):用 APScheduler ``reschedule_job()`` 原地替换 trigger,新 cron 立即生效,旧的执行计划被丢弃。

    扁平 JSON 响应(与 scheduler-status/scheduled-trigger 一致)。"""
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
    """运行时注入钉钉凭证+前端 URL(无需重启):测试/e2e 注入 AK/Robot/Template 和可选 frontend_url,
    后续 cron fire 即用这些凭证投递卡片(card_data.session_url 也用注入的 frontend_url)。凭证仅存进程内存,重启失效。"""
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
        from agentclaw.community.core.task.task_discovery.frontend_url import (
            FrontendUrlHolder,
        )

        FrontendUrlHolder.set(frontend_url)

    logger.info(
        "[task_discovery] injected via API: %s (robot=%s, template=%s)",
        ", ".join(injected),
        robot_code,
        card_template_id,
    )
    return {"success": True, "message": "; ".join(injected) + " injected"}


# ===== task_loop inbound PUSH callback router(单 bot workflow / bcn 协作群)=====
# 边缘:解析 raw body → Pydantic schema → auth.verify(source from body) → translate → disposition 分发 start_run/report_result。
# 领域异常→ ``@envelope_errors`` 映射(CallbackAuthError→401/CallbackCorrelationError→400/NotFound→404/TaskState→409);
# raw-body 非 JSON→ HTTPException(422);幂等:result 重投到已终态节点→200 ack(start stale→409)。无节点名字面量(零 case)。
task_callback_router = APIRouter(
    prefix="/api/v1/collaboration/tasks/callback", tags=["task-callback"]
)

_TERMINAL = {Status.DONE, Status.SUCCESS, Status.FAILED, Status.HUNG}


def _find_node_status(svc: TaskServiceProtocol, loop_task_id: str) -> Status | None:
    task_id, node_id = loop_task_id.split("::", 1)
    graph = svc.get_task_dashboard(task_id)
    node = next((n for n in graph.tasks if n.node_id == node_id), None)
    return node.status if node is not None else None


def _session_id_of(raw_obj: Any) -> str:
    """从原始回调 body 提取 session_id(落库 ``main_session_id`` 源),供入口/链路日志关联。
    BCN/manager_worker=``scope.session_id``;ClawMind=``ext_info.flow_runs.origin_session_id``;羽雀/兜底 DTO 无此形态 → 返 ``""``。"""
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
    """回调数据处理总入口(计时包装):实际分流/落库委派 ``_dispatch_impl``;全程计时到毫秒,
    ``finally`` 打 ``elapsed_ms``(覆盖正常 + 异常路径,便于定位慢回投)。"""
    _t0 = time.perf_counter()
    try:
        return await _dispatch_impl(
            request, disposition, schema_cls, svc, auth, registry, enricher,
        )
    finally:
        logger.info(
            "[task_callback] _dispatch 总耗时 elapsed_ms=%.0f disposition=%s",
            (time.perf_counter() - _t0) * 1000, disposition,
        )


async def _dispatch_impl(
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
    if _raw_obj is None and raw:
        # raw-body 非 JSON → HTTPException(422)(对齐 _dispatch docstring:仅非 JSON 走 422;
        # 合法 JSON 但不匹配任一分流 → 200 ack,不推进)。各端点共享本分流。
        raise HTTPException(status_code=422, detail="callback raw body is not valid json")
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
        # 解析(translate+构图)+落库任一步出错(如内嵌 JSON 非法)→ 打 error 日志,兜底落错误记录
        # (exec_error=错误信息、extend_props=原始 body;经 ingest_parse_error→upsert_error 仅改这两列,
        # 其它已有字段不动),再 ack 返回——不跳过落库,也不全量覆盖污染已有 task_callback。
        try:
            _tc = translate_claw_mind(_raw_obj, disposition)
            enricher.enrich_claw_mind(_tc.data, _raw_obj)
            await svc.callback.ingest(_tc.data)
        except Exception as exc:  # noqa: BLE001 解析失败不阻断回投应答;兜底落错误记录而非全量覆盖
            logger.error(
                "[task_callback] claw_mind 回调解析失败,兜底落错误记录 session_id=%s: %s",
                _sid, exc, exc_info=True,
            )
            await svc.callback.ingest_parse_error(_raw_obj, str(exc))
        return envelope({"ok": True}, request)
    if is_bcn_event_payload(_raw_obj):
        logger.info("[task_callback] bcn_event_callback session_id=%s", _sid)
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
            #await svc.apply_manager_worker_event(_raw_obj)
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

    # Framework task callbacks support the rich callback schema, the common
    # task-loop payload, and the legacy loop_task_id/result DTO. Unknown JSON
    # must be rejected instead of being acknowledged as success.
    if not isinstance(_raw_obj, dict):
        raise HTTPException(status_code=422, detail="callback body must be a JSON object")

    if _raw_obj.get("event_type") in {
        "EXECUTION_RESULT", "PLAN_RESULT", "SEARCH_RESULT",
    }:
        try:
            event = RelayTaskEventDTO.model_validate(_raw_obj)
        except Exception as exc:
            raise HTTPException(status_code=422, detail="invalid relay task event") from exc
        auth.verify(
            source="task_loop",
            headers=request.headers,
            raw_body=raw,
            method=request.method,
            path=request.url.path,
        )
        result = await svc.report_task_event(
            task_id=event.task_id,
            node_id=event.node_id,
            event_type=event.event_type,
            event_id=event.event_id,
            holder_id=event.holder_id,
            relay_turn=event.relay_turn,
            progress_reason=event.progress_reason,
            failure_reason=event.failure_reason,
            payload=event.payload,
        )
        return envelope(result, request)

    if is_common_task_payload(_raw_obj):
        logger.info("[task_callback] common_task_loop_callback session_id=%s, raw_obj=%s", _sid, _raw_obj)
        tc = translate_common_task_callback(_raw_obj)
        try:
            await svc.callback.report_result(tc.data)
        except TaskStateError:
            loop_task_id = tc.data.data.get("loop_task_id", "")
            try:
                if _find_node_status(svc, loop_task_id) in _TERMINAL:
                    return envelope({"ok": True}, request, message="idempotent")
            except (AttributeError, KeyError, ValueError):
                pass
            raise
        return envelope({"ok": True}, request)

    # Rich framework callback. Validation errors are intentionally allowed to
    # fall through to the legacy DTO so the old report contract remains valid.
    try:
        req = schema_cls.model_validate(_raw_obj)
    except Exception:
        req = None
    if req is not None:
        auth.verify(
            source=req.workflow_source,
            headers=request.headers,
            raw_body=raw,
            method=request.method,
            path=request.url.path,
        )
        tc = translate(req, disposition, registry)
        try:
            if tc.disposition == "start":
                await svc.callback.start_run(tc.data)
            else:
                await svc.callback.report_result(tc.data)
        except TaskStateError:
            # A result replay against an already terminal node is idempotent.
            if tc.disposition == "result":
                loop_task_id = tc.data.data.get("loop_task_id", "")
                try:
                    if _find_node_status(svc, loop_task_id) in _TERMINAL:
                        return envelope({"ok": True}, request, message="idempotent")
                except (AttributeError, KeyError, ValueError):
                    pass
            raise
        return envelope({"ok": True}, request)

    # Legacy callback/report contract: {loop_task_id, workflow_type, result}.
    try:
        dto = TaskCallbackDataDTO.model_validate(_raw_obj)
    except Exception as exc:
        # A syntactically valid JSON object that carries no callback-shaped
        # fields is an unrelated probe/notification. Acknowledge it without
        # invoking authentication or advancing the task graph. Objects that
        # claim to be callback payloads remain validation errors, preserving
        # the endpoint contract for malformed callback requests.
        callback_markers = {
            "loop_task_id",
            "workflow_type",
            "result",
            "task_id",
            "workflow_source",
            "workflow_id",
            "workflow_instance_id",
            "node_id",
            "status",
            "is_success",
            "output",
            "failed_info",
            "acceptance_result",
        }
        if _raw_obj and not any(key in _raw_obj for key in callback_markers):
            return envelope({"ok": True}, request, message="ignored")
        raise HTTPException(status_code=422, detail="invalid callback body") from exc
    auth.verify(
        source=(dto.workflow_type or "single_bot"),
        headers=request.headers,
        raw_body=raw,
        method=request.method,
        path=request.url.path,
    )
    try:
        await svc.callback.report_result(callback_from_dto(dto))
    except TaskStateError:
        try:
            if _find_node_status(svc, dto.loop_task_id) in _TERMINAL:
                return envelope({"ok": True}, request, message="idempotent")
        except (AttributeError, KeyError, ValueError):
            pass
        raise
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
