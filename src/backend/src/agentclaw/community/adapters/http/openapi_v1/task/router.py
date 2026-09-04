"""Task 公开 HTTP adapter routes —— 前端公开面(execute/dashboard/list;Rule 22:只转协议,不持领域策略)。

POST /openapi/v1/collaboration/tasks/execute   — 提交任务(delegate TaskServiceProtocol.execute)
GET  /openapi/v1/collaboration/tasks/dashboard  — 查任务图(delegate TaskServiceProtocol.get_task_dashboard)
GET  /openapi/v1/collaboration/tasks/list       — 列持久化任务记录(delegate TaskServiceProtocol.list_tasks)
GET  /openapi/v1/collaboration/tasks/bbs/list   — 列 BBS 接力任务(分页;delegate TaskServiceProtocol.list_bbs_tasks)

前端公开面经 gateway spanner 鉴权(``/openapi/v1/collaboration/**`` → user+app required)。其余内部接口
(回投 / bbs 接力步 claim·attach·result / 任务发现阶段)见 ``adapters/http/task/``(前缀 ``/api/v1/collaboration/tasks``,不经 spanner)。
统一 ``/openapi/v1`` 返回协议:成功经 ``envelope()`` → ``Envelope{code,message,data,request_id}``;
领域异常(GraphAlreadyInitialized/TaskNotFound/TaskState/GraphIntegrity…)直接上抛,
由 ``@envelope_errors`` + ``ENVELOPE_ERRORS`` 映射为统一 ``ErrorEnvelope``——router 不手写
``HTTPException`` 处理领域错误。仅对纯输入校验(非法 status 过滤)用 ``HTTPException`` 走中央 handler,
同样产出 ``ErrorEnvelope``。对齐 api/task/task_service.py Protocol。
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from agentclaw.community.adapters.http.openapi_v1.contracts import Envelope, Page
from agentclaw.community.adapters.http.openapi_v1.dependencies import (
    Principal,
    require_principal,
)
from agentclaw.community.adapters.http.openapi_v1.responses import (
    envelope,
    envelope_errors,
    page as page_envelope,
)
from agentclaw.community.adapters.http.task.schemas import (
    BbsTaskItemDTO,
    TaskExecutionGraphDTO,
    TaskGrantRequestDTO,
    TaskGrantResultDTO,
    TaskInfoRecordDTO,
    TaskInfoRequestDTO,
    TaskOpResultDTO,
    TaskRevokeRequestDTO,
    TaskRevokeResultDTO,
    bbs_task_overview_to_dto,
    graph_to_dto,
    op_result_to_dto,
    task_info_record_to_dto,
    task_info_request_from_dto,
)
from agentclaw.community.api.task.task_grant_service import (
    TaskClaimGrantServiceProtocol,
)
from agentclaw.community.api.task.task_service import TaskServiceProtocol
from agentclaw.community.core.task.domain.models import Status
from agentclaw.community.di import Injected
from agentclaw.community.adapters.http.openapi_v1.authorization import PublicAPIRoute

def _validate_status_filter(status: str | None) -> None:
    """校验 status query(逗号分隔的运行时态枚举),任一 token 非法 → 400。"""
    if status is None:
        return
    valid = {s.value for s in Status}
    for tok in (t.strip().upper() for t in status.split(",") if t.strip()):
        if tok not in valid:
            raise HTTPException(status_code=400, detail=f"invalid status filter: {status}")


def _validate_single_status(status: str | None) -> str | None:
    """校验单值 status(运行时态枚举):空白 → None(不过滤);非空则 strip+upper 后须 ∈ Status 枚举值,
    否则 400(逗号多值整体不在枚举内 → 400,强制单值契约)。返回归一化大写字符串供等值比对。"""
    if status is None or not status.strip():
        return None
    v = status.strip().upper()
    if v not in {s.value for s in Status}:
        raise HTTPException(status_code=400, detail=f"invalid status: {status}")
    return v


router = APIRouter(prefix="/openapi/v1/collaboration/tasks", tags=["task"], route_class=PublicAPIRoute)

# Handler-level principal dependency: ``test_public_routes_require_principal`` walks each
# route's dependant tree, and the ``@envelope_errors`` wrapper keeps handler-level
# `Depends(...)` params reachable while the router-level ``_PUBLIC_AUTH`` stays on the
# mount. Operations with no bot to gate (execute/dashboard) declare this so the route
# is visibly gated, then ``del principal`` since the identity is not used.
PrincipalDep = Annotated[Principal, Depends(require_principal)]


@router.post("/execute", response_model=Envelope[TaskOpResultDTO])
@envelope_errors
async def execute_task(
    body: TaskInfoRequestDTO,
    request: Request,
    principal: PrincipalDep,
    service: TaskServiceProtocol = Injected(TaskServiceProtocol),  # noqa: B008
) -> Envelope[TaskOpResultDTO]:
    """提交执行任务。task_id 服务端生成;持久化 task_info(PENDING)→ initialize_graph → on_execute 首帧。

    幂等:同 task_id 已建图(GraphAlreadyInitializedError)→ @envelope_errors 映射 409。"""
    del principal  # 鉴权经 PrincipalDep(require_principal);identity 不在此处使用。
    task_request = task_info_request_from_dto(body)
    result = await service.execute(task_request)
    return envelope(op_result_to_dto(result), request)


@router.get("/dashboard", response_model=Envelope[TaskExecutionGraphDTO])
@envelope_errors
async def get_task_dashboard(
    task_id: Annotated[str, Query(description="任务ID(创建时签发,bots 列表返回的 task_id)")],
    request: Request,
    principal: PrincipalDep,
    node_id: Annotated[
        str | None, Query(description="子节点ID;指定则只返回该节点的子树投影,缺省返回整图")
    ] = None,
    include_action_log: Annotated[
        bool, Query(description="是否返回各节点动作级历史快照(诊断用,默认关)")
    ] = False,
    service: TaskServiceProtocol = Injected(TaskServiceProtocol),  # noqa: B008
) -> Envelope[TaskExecutionGraphDTO]:
    """任务执行详情可视化(整图或按 node_id 子树投影),只读。

    include_action_log=true 时返回各节点动作级历史快照(PLAN/DISPATCH/EXECUTE/VERIFY/RESET/
    TRANSITION 全量 payload),默认关(诊断页开)。任务/节点不存在 → TaskNotFoundError/NodeNotFoundError
    → @envelope_errors 映射 404。"""
    del principal  # 鉴权经 PrincipalDep(require_principal);identity 不在此处使用。
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
async def list_tasks(
    request: Request,
    principal: PrincipalDep,
    user_id: str = Query(..., description="按归属 user_id 过滤任务记录"),
    status: Annotated[
        str | None,
        Query(description="可选 status 过滤:运行时态枚举,逗号分隔多值(如 PLANNING,RUNNING);非法值 → 400"),
    ] = None,
    page: Annotated[
        int | None,
        Query(ge=1, description="分页页码(1-based);不传则不分页,返回全量(列表契约不变)。"),
    ] = None,
    page_size: Annotated[
        int | None,
        Query(
            ge=1,
            le=100,
            description="每页条数(1-100);不传则不分页,返回全量(列表契约不变)。",
        ),
    ] = None,
    service: TaskServiceProtocol = Injected(TaskServiceProtocol),  # noqa: B008
) -> Envelope[list[TaskInfoRecordDTO] | Page[TaskInfoRecordDTO]]:
    """列持久化 task_info 记录,按更新时间降序,并按查询参数 user_id 过滤。

    user_id 是直接的筛选条件,不要求与认证主体一致,也不执行 owner 作用域校验。
    返回完整任务记录字段。非法 status 返回 400,而不是服务器内部错误。

    分页为可选入参(向后兼容):page/page_size 均不传时 data 为列表,
    等同历史契约(供接力 skill 全量枚举等场景);两者同时传入时 data 为
    Page(total, items)(1-based,page_size 最大 100)。仅传其一视为入参错误(400)。"""
    del principal  # Authentication remains mandatory; user_id is only a query filter.
    _validate_status_filter(status)
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


@router.get("/bbs/list", response_model=Envelope[Page[BbsTaskItemDTO]])
@envelope_errors
async def list_bbs_tasks(
    request: Request,
    principal: PrincipalDep,
    page: int = Query(default=1, ge=1, description="页码,1-based,默认 1"),
    page_size: int = Query(
        default=20, ge=1, le=100, description="每页数量,默认 20,最大 100"
    ),
    search_word: str | None = Query(
        default=None,
        description="可选模糊匹配:对 task_spec/extend_props 文本大小写不敏感 LIKE;空则不过滤",
    ),
    status: str | None = Query(
        default=None,
        description="可选单值状态过滤(PENDING/PLANNING/RUNNING/DONE/FAILED/HUNG/CANCELLED);逗号多值/非法 → 400",
    ),
    service: TaskServiceProtocol = Injected(TaskServiceProtocol),  # noqa: B008
) -> Envelope[Page[BbsTaskItemDTO]]:
    """列 BBS 接力任务(run_mode='bbs')的一页(公开面镜像;delegate TaskServiceProtocol.list_bbs_tasks)。

    鉴权经 PrincipalDep(require_principal);按 run_mode='bbs' 列全部任务,不按 user 归属过滤(与内部
    同路径契约一致)。分页 1-based:page 默认 1、page_size 默认 20(最大 100);可选 status 单值等值
    过滤、search_word 模糊匹配。返回 Envelope{data: Page{total, items}};非法 status 返回 400。"""
    del principal  # 鉴权经 PrincipalDep;bbs/list 按 run_mode='bbs' 列全部,不按 user 过滤
    normalized_status = _validate_single_status(status)
    normalized_word = (search_word or "").strip() or None
    records, total = service.list_bbs_tasks(
        page=page,
        page_size=page_size,
        search_word=normalized_word,
        status=normalized_status,
    )
    return page_envelope(
        total,
        [bbs_task_overview_to_dto(r) for r in records],
        request,
    )


@router.post("/grant", response_model=Envelope[TaskGrantResultDTO])
@envelope_errors
async def grant_task_claim(
    body: TaskGrantRequestDTO,
    request: Request,
    principal: PrincipalDep,
    service: TaskClaimGrantServiceProtocol = Injected(TaskClaimGrantServiceProtocol),  # noqa: B008
) -> Envelope[TaskGrantResultDTO]:
    """grant 公共 api-key 给某 Bot(前端 public openapi → task 无状态中继 → secbaas admin)。

    透传浏览器 Cookie/Referer;api-key 由服务端持有,不暴露前端。bcs_bot_id=real:entity(/mine bot.id)。
    secbaas 401/403(未登录/非 Bot owner/非管理员)→ envelope_errors 映射;4xx/5xx 可重试;幂等。"""
    result = await service.grant(
        bcs_bot_id=body.bcs_bot_id,
        cookie=request.headers.get("cookie", ""),
        referer=request.headers.get("referer", ""),
        operator=principal.user_id,
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
    principal: PrincipalDep,
    service: TaskClaimGrantServiceProtocol = Injected(TaskClaimGrantServiceProtocol),  # noqa: B008
) -> Envelope[TaskRevokeResultDTO]:
    """撤销授权(透传 Cookie/Referer → secbaas revoke)。幂等(无记录/已 revoked 也返回 revoked)。"""
    result = await service.revoke(
        bcs_bot_id=body.bcs_bot_id,
        cookie=request.headers.get("cookie", ""),
        referer=request.headers.get("referer", ""),
        operator=principal.user_id,
    )
    return envelope(
        TaskRevokeResultDTO(
            bcs_bot_id=result.bcs_bot_id,
            grant_status=result.grant_status,
        ),
        request,
    )
