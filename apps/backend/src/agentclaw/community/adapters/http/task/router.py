"""Internal task HTTP adapter; domain work is delegated to TaskServiceProtocol."""

from __future__ import annotations

import json
from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, Query, Request
from fastapi.responses import HTMLResponse

from agentclaw.community.adapters.http.openapi_v1.contracts import Envelope
from agentclaw.community.adapters.http.openapi_v1.responses import envelope, envelope_errors
from agentclaw.community.adapters.http.task.auth import CallbackAuthenticator
from agentclaw.community.adapters.http.task.relay_guard import require_relay_attempt
from agentclaw.community.adapters.http.task.schemas_relay_report import (
    TaskReportCommandDTO, report_result_to_dto,
)
from agentclaw.community.adapters.http.task.trajectory_html import render_trajectory_html
from agentclaw.community.adapters.http.task.schemas import (
    TaskCallbackRequest, TaskExecutionGraphDTO,
    TaskGrantRequestDTO, TaskGrantResultDTO, TaskInfoRequestDTO,
    TaskNodeUpdateDTO, TaskOpResultDTO,
    TaskRevokeRequestDTO, TaskRevokeResultDTO, TaskSettingRequestDTO,
    TaskSettingStateDTO, TaskTrajectoryDTO, acceptance_result_from_dto,
    graph_to_dto, op_result_to_dto, task_info_request_from_dto,
    trajectory_to_dto,
)
from agentclaw.community.adapters.http.task.relay_routes import router as relay_api_router
from agentclaw.community.adapters.http.task.bbs_routes import router as bbs_api_router
from agentclaw.community.adapters.http.task.intake_routes import router as intake_api_router
from agentclaw.community.adapters.http.task.callback_routes import (
    _dispatch,
    task_callback_router as task_callback_router,
)
from agentclaw.community.core.task.task_runner.client.callback_data_enricher import CallbackDataEnricher
from agentclaw.community.adapters.http.auth.dependencies import get_current_user
from agentclaw.community.adapters.http.auth.models import AuthenticatedUser
from agentclaw.community.core.task.task_dispatch.claim_join_gate import (
    CLAIM_JOIN_FILTER, HARNESS_POLLER, RELAY_EXECUTION, SEARCH_SKILL, SKILL_REPORT,
    TaskSettingsServiceProtocol,
)
from agentclaw.community.api.task.task_grant_service import TaskClaimGrantServiceProtocol
from agentclaw.community.api.task.task_service import TaskServiceProtocol
from agentclaw.community.api.task.task_context_service import TaskContextServiceProtocol
from agentclaw.community.core.errors import InternalError
from agentclaw.community.utils.env_utils import get_current_env
from agentclaw.community.core.task.domain.errors import TaskStateError
from agentclaw.community.core.task.domain.models import TaskOpResult
from agentclaw.community.core.task.domain.identity import compose_bot_identity
from agentclaw.community.core.task.task_runner.client.bcs_bot_token_provider import (
    BcsBotTokenProvider,
)
from agentclaw.community.core.task.task_runner.client.ports import (
    BcsClientPort,
    OpenApiBotPort,
)
from agentclaw.community.core.task.task_runner.client.prompt_formatter import (
    compose_benchmark_a2a_session_id,
    format_benchmark_prompt,
)
from agentclaw.community.core.task.task_discovery.discovery_service import DiscoveryService
from agentclaw.community.core.task.task_discovery.scheduler import TaskDiscoveryScheduler

from agentclaw.community.core.task.task_discovery.task_reader import (
    TaskReader, clear_discovered_tasks, upsert_discovered_tasks,
)
from agentclaw.community.core.task.task_runner.callback_correlation import CallbackCorrelationRegistry
from agentclaw.community.di import Injected
from agentclaw.community.plugin_api.database import DatabasePlugin
from agentclaw.community.log import get_logger

logger = get_logger()


router = APIRouter(prefix="/api/v1/collaboration/tasks", tags=["task"])
router.include_router(relay_api_router)
router.include_router(bbs_api_router)
router.include_router(intake_api_router)

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


@router.post("/benchmark_execute", response_model=Envelope[TaskOpResultDTO])
@envelope_errors
async def benchmark_execute_task_internal(
    body: TaskInfoRequestDTO,
    request: Request,
    bot_port: OpenApiBotPort = Injected(OpenApiBotPort),  # noqa: B008
    bcs_client: BcsClientPort = Injected(BcsClientPort),  # noqa: B008
    bot_tokens: BcsBotTokenProvider = Injected(BcsBotTokenProvider),  # noqa: B008
) -> Envelope[TaskOpResultDTO]:
    """Benchmark 执行(内部副本,不经 spanner)。构造 prompt 直接发给 bot,不创建 task。

    task_id 由 bot 在 session 中自行生成;本端点只负责把 task_spec 构造为 prompt 并通过
    OpenApiBotPort.send_message 发给 owner_bot。返回 benchmark_run_id / benchmark_session_id。

    A2A 分支(execution_config.target_bot_id 与 target_user_id 同时非空):不建 task、不改走
    openapi messages 通道,由 owner_bot 经 BCS A2A ``chat-async`` 直接发给目标 bot
    (``POST /bots/{target_bot_id}:{target_user_id}/chat-async``);发送方身份取
    ``BcsBotTokenProvider.get_token(owner_bot_id)``(bcs_bots.session_token)作 Bearer;
    sessionId 由服务端生成(``compose_benchmark_a2a_session_id``,格式
    ``bcs-cli:task-<时间戳>-<随机6位>``)。"""
    # 构造 prompt 并直接发给 bot(不调 service.execute,不创建 task)
    task_spec_dict = body.task_spec.model_dump()
    prompt = format_benchmark_prompt(task_spec_dict)
    bot_identity = compose_bot_identity(body.owner_bot_id, body.owner_user_id)

    a2a_target_bot = str(body.execution_config.target_bot_id or "").strip() or None
    a2a_target_user = str(body.execution_config.target_user_id or "").strip() or None

    extend_props: dict[str, Any] = {}
    if a2a_target_bot and a2a_target_user:
        # A2A 分支:owner_bot → target_bot 的 BCS chat-async;不走 ensure_grant/openapi 通道。
        session_id = compose_benchmark_a2a_session_id()
        extend_props["benchmark_channel"] = "a2a"
        extend_props["benchmark_session_id"] = session_id
        if bcs_client is not None:
            try:
                caller_token = (
                    bot_tokens.get_token(bot_identity) if bot_tokens is not None else None
                )
                sent = await bcs_client.send_message_a2a(
                    target_bot_id=a2a_target_bot,
                    target_user_id=a2a_target_user,
                    message=prompt,
                    session_id=session_id,
                    caller_bearer=caller_token,
                )
                extend_props["benchmark_run_id"] = sent.run_id
                extend_props["benchmark_send"] = "ok"
            except Exception as exc:  # noqa: BLE001
                extend_props["benchmark_send"] = f"error: {exc}"
        else:
            extend_props["benchmark_send"] = "skip"
    elif bot_port is not None:
        try:
            await bot_port.ensure_grant(bot_identity)
            sent = await bot_port.send_message(
                bot_id=bot_identity,
                message=prompt,
                metadata={"benchmark": True},
            )
            extend_props["benchmark_run_id"] = sent.run_id
            extend_props["benchmark_session_id"] = sent.session_id
            extend_props["benchmark_send"] = "ok"
        except Exception as exc:  # noqa: BLE001
            extend_props["benchmark_send"] = f"error: {exc}"
    else:
        extend_props["benchmark_send"] = "skip"

    result = TaskOpResult(
        task_id="",
        success=True,
        run_id=0,
        extend_props=extend_props,
    )
    return envelope(op_result_to_dto(result), request)


@router.get("/trajectory", response_model=Envelope[TaskTrajectoryDTO])
@envelope_errors
async def get_task_trajectory_internal(
    task_id: Annotated[str, Query(description="任务ID(创建时签发, bots 列表返回的 task_id)")],
    request: Request,
    do_analysis: Annotated[
        bool, Query(description="是否触发 bot 总体分析(默认关闭)")
    ] = False,
    force_analysis: Annotated[
        bool,
        Query(description="强制重跑分析,默认 false:忽略 timeline 版本号幂等判断,"
                          "即使版本未变也重新调 bot 并回填(do_analysis 的前门旁路)"),
    ] = False,
    session_detail: Annotated[
        bool,
        Query(description="是否查询会话明细并挂到各子任务末位事件(默认 false:返回不携带"
                          " session_msgs,零外部读);仅读路径生效,do_analysis=true 时不起作用"),
    ] = False,
    display: Annotated[
        str | None,
        Query(description="展示形式:html=返回人类可读的 HTML 轨迹页;省略或其它值=默认 JSON envelope"),
    ] = None,
    service: TaskContextServiceProtocol = Injected(TaskContextServiceProtocol),  # noqa: B008
) -> Envelope[TaskTrajectoryDTO]:
    """读取任务轨迹(内部副本;与公开面 ``adapters/http/openapi_v1/task/router.py`` 同一委托,逻辑一致,改其一须同步)。

    do_analysis=false(默认,纯读):返回 TaskTrajectory,analysis 取已落库值或 None,不写库不调 bot;
    session_detail=false(默认)纯读不携带 session_msgs;session_detail=true 才实拉会话明细挂末位事件。
    do_analysis=true:调 DI 注入 bot 做总体分析→ 覆盖回填 analysis+gmt_modified→ 返回同形态 TaskTrajectory
    (该路径不受 session_detail 影响,会话明细照其内部逻辑物化/研判)。
    bot 未配置→503;bot 失败/超时→504 且不回填(决策 #10/#14)。原 /analysis 端点已并入此入口(决策 #10)。

    display=html:返回自包含 HTML 轨迹页(FastAPI 对 ``HTMLResponse`` 返回值跳过 ``response_model``
    序列化,与默认 ``Envelope[TaskTrajectoryDTO]`` JSON 形态并存;错误仍走 ``envelope_errors`` 的 JSON)。"""
    trajectory = await service.get_trajectory(
        task_id, do_analysis=do_analysis, force_analysis=force_analysis,
        session_detail=session_detail,
    )
    dto = trajectory_to_dto(trajectory)
    if display == "html":
        return HTMLResponse(render_trajectory_html(dto, do_analysis=do_analysis))
    return envelope(dto, request)


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
    try:
        raw_event = json.loads(_body)
    except (TypeError, ValueError):
        raw_event = None
    if isinstance(raw_event, dict) and "relay_token" in raw_event:
        _preview = "<relay report payload redacted>"
    else:
        _preview = _body[:4000].decode("utf-8", "replace")
        if len(_body) > 4000:
            _preview += f"...(truncated, total {len(_body)} bytes)"
    logger.info(
        "[task_callback] entry method=%s path=%s body=%s",
        request.method,
        request.url.path,
        _preview,
    )
    if isinstance(raw_event, dict) and "relay_token" in raw_event:
        command = TaskReportCommandDTO.model_validate(raw_event).to_domain()
        graph = svc.get_task_dashboard(command.task_id)
        if not graph.is_relay:
            raise TaskStateError("relay report requires a relay task")
        node = next((item for item in graph.tasks if item.node_id == command.node_id), None)
        if node is None:
            raise TaskStateError("relay report node does not exist")
        require_relay_attempt(node, command.relay_token)
        result = await svc.report(command)
        return envelope(report_result_to_dto(result).model_dump(), request)
    return await _dispatch(request, "result", TaskCallbackRequest, svc, auth, registry, enricher)


@router.post("/nodes/update", response_model=Envelope[dict[str, Any]])
@envelope_errors
async def update_task_node(
    body: TaskNodeUpdateDTO,
    request: Request,
    service: TaskServiceProtocol = Injected(TaskServiceProtocol),  # noqa: B008
) -> Envelope[dict[str, Any]]:
    """内部节点写口:直接更新节点 run_info(经 ``update_task_node_info`` → ``CentralizedExecutionAdapter.on_report`` 落库并触发翻态/验收/收敛传播)。

    透传 ``TaskNodePatch`` 三选一终态翻转(互斥):``acceptance_result`` 验收 / ``exec_error`` 执行报错(→ on_harness 重投)/
    ``status`` 框架直驱;三空仅 fold 非状态字段。供内部/测试直驱节点状态,不经 BBS claim 校验(区别于 ``bbs/result``)。领域异常→ ``@envelope_errors`` 映射。"""
    graph = service.get_task_dashboard(body.task_id)
    if graph.is_relay:
        raise TaskStateError("new relay nodes can only be updated by the report protocol")
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


# Keep this dynamic route after every static task route so names such as
# ``benchmark_execute`` and ``discovery`` can never be consumed as task IDs.
@router.get("/{task_id}", response_model=Envelope[TaskExecutionGraphDTO])
@envelope_errors
async def get_task_internal(
    task_id: str,
    request: Request,
    user: AuthenticatedUser = Depends(get_current_user),
    service: TaskServiceProtocol = Injected(TaskServiceProtocol),  # noqa: B008
) -> Envelope[TaskExecutionGraphDTO]:
    """Return one task dashboard for authenticated Goal clients."""
    del user
    graph = service.get_task_dashboard(task_id)
    return envelope(graph_to_dto(graph), request)
