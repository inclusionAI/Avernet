"""产物 API 路由:POST /artifacts(手动登记一条产物)+ GET /artifacts(按 task_id 列产物)。

``adapters/http/task/router.py``(/api/v1/collaboration/tasks)经 ``include_router``
挂载。写出/读入的领域语义全部在核心 ``TaskArtifactApiService``;结构性校验收口在核心
``parse_artifact_report_item``(Rule 22:HTTP 层只转协议)。请求体与 relay 上报 item
同构(扁平自描述,spec 2026-10-20-task-artifact-domain §4),归属字段与图交叉校验。

独立小模块的原因:task/router.py 已在 Rule 9 体积帽沿(1000 行),产物关注点按
relay/bbs/intake/read 子路由的既有先例拆出。
"""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Query, Request

from agentclaw.community.adapters.http.auth.dependencies import get_current_user
from agentclaw.community.adapters.http.auth.models import AuthenticatedUser
from agentclaw.community.adapters.http.openapi_v1.contracts import Envelope
from agentclaw.community.adapters.http.openapi_v1.responses import (
    envelope,
    envelope_errors,
)
from agentclaw.community.adapters.http.task.schemas_artifact import (
    TaskArtifactAddRequestDTO,
    TaskArtifactDescriptorDTO,
    artifact_record_to_descriptor,
)
from agentclaw.community.api.task.task_artifact_service import (
    TaskArtifactApiServiceProtocol,
)
from agentclaw.community.core.task.domain.errors import TaskArtifactContentError
from agentclaw.community.core.task.task_context.task_artifact.report_item import (
    parse_artifact_report_item,
)
from agentclaw.community.di import Injected

router = APIRouter(tags=["task"])


@router.post(
    "/artifacts",
    response_model=Envelope[TaskArtifactDescriptorDTO],
)
@envelope_errors
async def add_task_artifact(
    request: Request,
    payload: TaskArtifactAddRequestDTO,
    user: AuthenticatedUser = Depends(get_current_user),
    service: TaskArtifactApiServiceProtocol = Injected(TaskArtifactApiServiceProtocol),  # noqa: B008
) -> Envelope[TaskArtifactDescriptorDTO]:
    """手动登记一条任务产物(append-only;幂等命中返回既有行)。

    请求体与 relay 回调上报 item 同构:``artifact_kind`` + 恰好一个 content 字段
    (四个互斥;**JSON 对象或 JSON 字符串均可**,对象由本层归一为字符串);task_id/
    node_id/attempt 与图交叉校验(attempt 失配即拒,session_id 图绑定优先、上报
    兜底)。结构/内容校验失败 → 422;任务或节点不存在 → 404。成功返回读侧
    Descriptor(安全字段,spec §7)。"""
    del user
    raw = payload.model_dump()
    _normalize_content_fields(raw)
    try:
        item = parse_artifact_report_item(
            raw,
            report_task_id=payload.task_id,
            report_node_id=payload.node_id,
        )
    except ValueError as exc:
        # 解析层收口的全部结构性失配(互斥/JSON/路径逃逸/非 http(s) 等)统一进
        # 既有 422 域错误信封——产物记录宁可没有,不可有假。
        raise TaskArtifactContentError(str(exc)) from exc
    record = service.add_artifact(item)
    return envelope(artifact_record_to_descriptor(record), request)


_CONTENT_FIELDS = (
    "text_content",
    "file_content",
    "url_link_content",
    "code_content",
)


def _normalize_content_fields(raw: dict) -> None:
    """content 字段对象形态 → 单行 JSON 字符串(就地归一;仅协议翻译,Rule 22)。

    核心解析(含 kind-tag 禁入/互斥/路径逃逸校验)只认字符串;独立 API 放开
    对象形态是调用方易用性取向,归一后语义与 relay 上报逐字节同构。
    """

    for field in _CONTENT_FIELDS:
        value = raw.get(field)
        if isinstance(value, dict):
            raw[field] = json.dumps(value, ensure_ascii=False)


@router.get(
    "/artifacts",
    response_model=Envelope[list[TaskArtifactDescriptorDTO]],
)
@envelope_errors
async def list_task_artifacts(
    request: Request,
    user: AuthenticatedUser = Depends(get_current_user),
    task_id: str = Query(..., description="任务 ID(创建时签发;检索维度,必填)"),
    node_id: str | None = Query(
        None, description="产出节点 ID;指定则只列该节点产物"
    ),
    limit: int = Query(200, ge=1, le=500, description="返回条数上限(1-500,默认 200)"),
    service: TaskArtifactApiServiceProtocol = Injected(TaskArtifactApiServiceProtocol),  # noqa: B008
) -> Envelope[list[TaskArtifactDescriptorDTO]]:
    """按 task_id 查询产物清单(attempt 倒序;node_id 可选过滤)。

    返回读侧 Descriptor 列表(kind-tagged 引用元数据;不含任何临时下载
    凭证,前端基于既有读口自行拼链接)。任务无产物 → 空列表(非 404)。"""
    del user
    records = service.list_artifacts(task_id, node_id=node_id, limit=limit)
    return envelope(
        [artifact_record_to_descriptor(record) for record in records], request
    )
