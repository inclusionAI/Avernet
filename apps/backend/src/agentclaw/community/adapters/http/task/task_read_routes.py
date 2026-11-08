"""内部面读侧镜像路由:GET /dashboard 与 /list。

``adapters/http/task/router.py``(/api/v1/collaboration/tasks)经 ``include_router``
挂载;与 ``adapters/http/openapi_v1/task/router.py`` 公开面同一 ``TaskServiceProtocol``
委托、同一逻辑 —— 改其一须同步(本文件即"其一")。差异仅鉴权:内部面走
``get_current_user``,公开面经 gateway spanner 走 ``require_principal``。
独立小模块的原因:task/router.py 已在 Rule 9 体积帽沿(1000 行),读侧属
新的内聚关注点,按 relay/bbs/intake 子路由的既有先例拆出。
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from agentclaw.community.adapters.http.auth.dependencies import get_current_user
from agentclaw.community.adapters.http.auth.models import AuthenticatedUser
from agentclaw.community.adapters.http.openapi_v1.contracts import Envelope, Page
from agentclaw.community.adapters.http.openapi_v1.responses import (
    envelope,
    envelope_errors,
    page as page_envelope,
)
from agentclaw.community.adapters.http.task.schemas import (
    TaskExecutionGraphDTO,
    TaskInfoRecordDTO,
    graph_to_dto,
    task_info_record_to_dto,
)
from agentclaw.community.adapters.http.task.schemas_artifact import (
    group_artifacts_by_node,
)
from agentclaw.community.api.task.task_artifact_service import (
    TaskArtifactApiServiceProtocol,
)
from agentclaw.community.api.task.task_service import TaskServiceProtocol
from agentclaw.community.core.task.domain.models import Status
from agentclaw.community.di import Injected

router = APIRouter(tags=["task"])


@router.get("/dashboard", response_model=Envelope[TaskExecutionGraphDTO])
@envelope_errors
async def get_task_dashboard_internal(
    task_id: Annotated[str, Query(description="任务ID(创建时签发,bots 列表返回的 task_id)")],
    request: Request,
    user: AuthenticatedUser = Depends(get_current_user),
    node_id: Annotated[
        str | None, Query(description="子节点ID;指定则只返回该节点的子树投影,缺省返回整图")
    ] = None,
    include_action_log: Annotated[
        bool, Query(description="是否返回各节点动作级历史快照(诊断用,默认关)")
    ] = False,
    service: TaskServiceProtocol = Injected(TaskServiceProtocol),  # noqa: B008
    artifact_service: TaskArtifactApiServiceProtocol = Injected(TaskArtifactApiServiceProtocol),  # noqa: B008
) -> Envelope[TaskExecutionGraphDTO]:
    """任务执行详情可视化(内部副本;整图或按 node_id 子树投影)。

    include_action_log=true 时返回各节点动作级历史快照,默认关(诊断页开);
    节点 DTO 含产物清单(Descriptor 富化,spec 2026-10-20-task-artifact-domain §7)。
    任务/节点不存在 → TaskNotFoundError/NodeNotFoundError → 404。"""
    del user
    if include_action_log:
        graph = service.get_task_dashboard(task_id, node_id, include_action_log=True)
    else:
        graph = service.get_task_dashboard(task_id, node_id)
    # 产物清单纯读库(2026-10-27):不再先去 BCS 查 session_file 回填,直接收 DB 行。
    artifact_rows = artifact_service.list_artifacts(graph.task_id)
    return envelope(
        graph_to_dto(
            graph,
            include_action_log=include_action_log,
            artifacts_by_node=group_artifacts_by_node(artifact_rows),
        ),
        request,
    )


def _validate_task_status_filter(status: str | None) -> None:
    """校验 status query(逗号分隔的运行时态枚举),任一 token 非法 → 400。

    公开面 ``openapi_v1/task/router.py`` 同名校验的镜像(改其一须同步)。"""

    if status is None:
        return
    valid = {item.value for item in Status}
    for tok in (t.strip().upper() for t in status.split(",") if t.strip()):
        if tok not in valid:
            raise HTTPException(status_code=400, detail=f"invalid status filter: {status}")


@router.get(
    "/list",
    response_model=Envelope[list[TaskInfoRecordDTO] | Page[TaskInfoRecordDTO]],
)
@envelope_errors
async def list_tasks_internal(
    request: Request,
    user: AuthenticatedUser = Depends(get_current_user),
    user_id: str = Query(..., description="按归属 user_id 过滤任务记录"),
    status: Annotated[
        str | None,
        Query(description="可选 status 过滤:运行时态枚举,逗号分隔多值(如 PLANNING,RUNNING);非法值 → 400"),
    ] = None,
    page: Annotated[
        int | None, Query(ge=1, description="分页页码(1-based);不传则不分页,返回全量(列表契约不变)。")
    ] = None,
    page_size: Annotated[
        int | None,
        Query(ge=1, le=100, description="每页条数(1-100);不传则不分页,返回全量(列表契约不变)。"),
    ] = None,
    service: TaskServiceProtocol = Injected(TaskServiceProtocol),  # noqa: B008
) -> Envelope[list[TaskInfoRecordDTO] | Page[TaskInfoRecordDTO]]:
    """列持久化 task_info 记录(内部副本;按更新时间降序),按查询参数 user_id 过滤。

    user_id 是直接的筛选条件,不要求与认证主体一致,也不执行 owner 作用域校验。
    分页为可选入参(向后兼容):page/page_size 均不传时 data 为列表(历史契约);
    两者同时传入时 data 为 Page(total, items)(1-based,page_size 最大 100);
    仅传其一视为入参错误(400)。非法 status → 400。"""
    del user
    _validate_task_status_filter(status)
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
