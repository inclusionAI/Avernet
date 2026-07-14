"""
Worker API Routes

Worker 相关的 HTTP API 端点。

Stage 1 Phase 3：基于 SQLite 持久化的 Worker 管理 API。

端点：
- POST /v1/workers - 创建/注册 Worker（API 主路径）
- POST /v1/workers/{worker_id}/sync - 原子同步（创建/更新 + 上线 + Profile）
- GET /v1/workers - 列出 Worker（支持过滤）
- GET /v1/workers/{worker_id} - 获取单个 Worker
- PUT /v1/workers/{worker_id}/online - 设为在线
- PUT /v1/workers/{worker_id}/offline - 设为离线
- PUT /v1/workers/{worker_id}/availability - 设置可见性状态 (private/protected/public)
- DELETE /v1/workers/{worker_id} - 删除 Worker
- PATCH /v1/workers/{worker_id} - 更新 Worker（保留）
"""

import asyncio
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field, ValidationError

from src.application.services.worker_import_service import WorkerImportService
from src.application.services.worker_runtime_state_service import WorkerRuntimeStateService
from src.application.services.worker_profile_content_service import WorkerProfileContentService
from src.domain.models.worker_lifecycle_state import WorkerLifecycleState
from src.interfaces.api.schemas.worker_config_schemas import *  # noqa: F403
from src.domain.models.worker_runtime_state import WorkerRuntimeState
from src.domain.models.worker_source_info import WorkerSourceType
from src.domain.exceptions import WorkerNotFoundException, DuplicateWorkerException
from src.interfaces.api.error_handling import (
    exception_to_response,
    get_status_code_for_exception,
)
from src.utils import get_table_name
from src.interfaces.api.dependencies.worker_dependencies import (
    get_worker_import_service,
    get_worker_runtime_state_service,
    get_registry_store,
    get_worker_profile_content_service,
    get_audit_log_store,
)


router = APIRouter()


# ============================================================================
# Helper Functions
# ============================================================================

def _get_enum_value(val) -> str:
    """安全获取枚举值（Pydantic use_enum_values 后字段可能是字符串）"""
    if hasattr(val, 'value'):
        return val.value
    return str(val)


# ============================================================================
# Request/Response Models
# ============================================================================

class CreateWorkerRequest(BaseModel):
    """创建 Worker 请求"""
    id: str = Field(..., description="Worker ID", min_length=1)
    type: str = Field(default="bot", description="Worker 类型 (human/bot)")
    name: str = Field(..., description="显示名称", min_length=1)
    handle: Optional[str] = Field(None, description="Handle (如 @bot-name)")
    description: Optional[str] = Field(None, description="描述")
    responsibilities: list[str] = Field(default_factory=lambda: ["general"], description="职责列表")
    domains: list[str] = Field(default_factory=list, description="领域列表")
    capabilities: list[dict[str, Any]] = Field(default_factory=list, description="能力列表")
    skills: list[dict[str, Any]] = Field(default_factory=list, description="技能列表")
    resources: list[dict[str, Any]] = Field(default_factory=list, description="资源列表")
    availability: str = Field(default="private", description="可用性 (private/protected/public)")
    trust_level: str = Field(default="guarded", description="信任级别")
    external_id: Optional[str] = Field(None, description="外部 ID")
    profile_key: Optional[str] = Field(None, description="Profile Key")
    created_by: Optional[str] = Field(None, description="创建者")


class CreateWorkerResponse(BaseModel):
    """创建 Worker 响应"""
    success: bool
    id: str
    type: str
    name: str
    handle: Optional[str]
    description: Optional[str]
    lifecycle_state: str
    runtime_state: str
    source_type: str
    version: int
    responsibilities: list[str]
    domains: list[str]


class WorkerResponse(BaseModel):
    """Worker 响应"""
    success: bool
    id: str
    type: str
    name: str
    handle: Optional[str]
    description: Optional[str]
    lifecycle_state: str
    runtime_state: str
    source_type: str
    version: int
    responsibilities: list[str]
    domains: list[str]
    availability: str
    trust_level: str
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class WorkerListResponse(BaseModel):
    """Worker 列表响应"""
    success: bool
    items: list[WorkerResponse]
    total: int


class BatchQueryWorkerItem(BaseModel):
    """批量查询 Worker 单项"""
    name: str = Field(..., description="显示名称")
    runtime_state: str = Field(..., description="运行态 (online/offline)")
    availability: str = Field(..., description="可见性 (private/protected/public)")
    profile_tags: dict[str, str] = Field(
        default_factory=dict, description="Profile 标签，如 trust_level 等"
    )


class BatchQueryWorkersResponse(BaseModel):
    """批量查询 Worker 响应"""
    success: bool
    data: dict[str, BatchQueryWorkerItem] = Field(
        default_factory=dict, description="Worker 数据，key 为 worker_id"
    )
    not_found_ids: list[str] = Field(
        default_factory=list, description="未找到的 Worker ID 列表"
    )


class RuntimeStateResponse(BaseModel):
    """运行态响应"""
    success: bool
    worker_id: str
    runtime_state: str
    lifecycle_state: str
    version: int


# ============================================================================
# API Endpoints
# ============================================================================

@router.post(
    "/workers",
    status_code=status.HTTP_201_CREATED,
    response_model=CreateWorkerResponse,
)
async def create_worker(
    request: CreateWorkerRequest,
    service: WorkerImportService = Depends(get_worker_import_service),
):
    """
    创建或注册一个 Worker

    这是 API 注册主路径：
    - 创建 Worker 记录
    - 初始化 runtime_state 为 offline
    - 写入审计日志
    - 触发索引同步
    - 持久化到 SQLite
    """
    try:
        # 构建 worker_data
        worker_data = {
            "id": request.id,
            "type": request.type,
            "identity": {
                "name": request.name,
                "handle": request.handle or f"@{request.id}",
                "description": request.description,
            },
            "responsibilities": request.responsibilities,
            "domains": request.domains,
            "capabilities": request.capabilities if request.capabilities else [
                {"name": "general", "level": "intermediate"}
            ],
            "skills": request.skills,
            "resources": request.resources,
            "state": {
                "availability": request.availability,
                "trust_level": request.trust_level,
            },
            "external_id": request.external_id,
            "active_profile_key": request.profile_key,
        }

        # 调用服务创建
        created = service.import_from_api(
            worker_data=worker_data,
            actor=request.created_by,
        )

        return CreateWorkerResponse(
            success=True,
            id=created.id,
            type=_get_enum_value(created.type),
            name=created.identity.name,
            handle=created.identity.handle,
            description=created.identity.description,
            lifecycle_state=_get_enum_value(created.lifecycle_state),
            runtime_state=_get_enum_value(created.state.runtime_state),
            source_type=_get_enum_value(created.source_type),
            version=created.version,
            responsibilities=created.responsibilities,
            domains=created.domains,
        )

    except DuplicateWorkerException as e:
        return exception_to_response(
            exc=e,
            status_code=status.HTTP_409_CONFLICT,
            context={"worker_id": request.id},
        )
    except ValidationError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=e.errors()
        )


@router.get("/workers/{worker_id}", response_model=WorkerResponse)
async def get_worker(
    worker_id: str,
    store = Depends(get_registry_store),
):
    """获取单个 Worker"""
    worker = store.get_by_id(worker_id)
    if worker is None:
        exc = WorkerNotFoundException(worker_id)
        return exception_to_response(
            exc=exc,
            status_code=status.HTTP_404_NOT_FOUND,
            context={"worker_id": worker_id},
        )

    return WorkerResponse(
        success=True,
        id=worker.id,
        type=_get_enum_value(worker.type),
        name=worker.identity.name,
        handle=worker.identity.handle,
        description=worker.identity.description,
        lifecycle_state=_get_enum_value(worker.lifecycle_state),
        runtime_state=_get_enum_value(worker.state.runtime_state),
        source_type=_get_enum_value(worker.source_type),
        version=worker.version,
        responsibilities=worker.responsibilities,
        domains=worker.domains,
        availability=_get_enum_value(worker.state.availability),
        trust_level=_get_enum_value(worker.state.trust_level),
        created_at=worker.created_at.isoformat() if worker.created_at else None,
        updated_at=worker.updated_at.isoformat() if worker.updated_at else None,
    )


class BatchQueryWorkersRequest(BaseModel):
    """批量查询 Worker 请求"""
    worker_ids: list[str] = Field(
        ..., description="Worker ID 列表", min_length=1, max_length=100
    )


@router.post("/workers/batch", response_model=BatchQueryWorkersResponse)
async def batch_query_workers(
    request: BatchQueryWorkersRequest,
    store = Depends(get_registry_store),
):
    """根据 worker_id 列表批量查询 Worker"""
    data: dict[str, BatchQueryWorkerItem] = {}
    not_found_ids: list[str] = []

    for worker_id in request.worker_ids:
        worker = store.get_by_id(worker_id)
        if worker is None:
            not_found_ids.append(worker_id)
            continue

        data[worker.id] = BatchQueryWorkerItem(
            name=worker.identity.name,
            runtime_state=_get_enum_value(worker.state.runtime_state),
            availability=_get_enum_value(worker.state.availability),
            profile_tags={"trust_level": _get_enum_value(worker.state.trust_level)},
        )

    return BatchQueryWorkersResponse(
        success=True,
        data=data,
        not_found_ids=not_found_ids,
    )


@router.get("/workers", response_model=WorkerListResponse)
async def list_workers(
    lifecycle_state: Optional[str] = Query(None, description="生命周期状态 (active/inactive/disabled)"),
    runtime_state: Optional[str] = Query(None, description="运行态 (online/offline)"),
    source_type: Optional[str] = Query(None, description="来源类型 (api/file/import)"),
    domain: Optional[str] = Query(None, description="领域过滤"),
    type: Optional[str] = Query(None, description="Worker 类型 (human/bot)"),
    capability: Optional[list[str]] = Query(None, description="能力过滤（可多选）"),
    skill: Optional[list[str]] = Query(None, description="技能过滤（可多选）"),
    resource: Optional[list[str]] = Query(None, description="资源 ID 过滤（可多选）"),
    limit: int = Query(100, ge=1, le=1000, description="分页限制"),
    offset: int = Query(0, ge=0, description="分页偏移"),
    store = Depends(get_registry_store),
):
    """列出 Worker"""
    # 构建过滤条件
    lifecycle_states = None
    if lifecycle_state:
        try:
            lifecycle_states = [WorkerLifecycleState(lifecycle_state)]
        except ValueError:
            pass  # 忽略无效的状态值

    source_types = None
    if source_type:
        try:
            source_types = [WorkerSourceType(source_type)]
        except ValueError:
            pass  # 忽略无效的类型值

    domains = [domain] if domain else None

    # 查询
    workers = store.list(
        lifecycle_states=lifecycle_states,
        source_types=source_types,
        domains=domains,
        limit=limit,
        offset=offset,
    )

    # 运行态过滤（内存过滤）
    if runtime_state:
        try:
            target_state = WorkerRuntimeState(runtime_state)
            workers = [w for w in workers if _get_enum_value(w.state.runtime_state) == runtime_state]
        except ValueError:
            pass  # 忽略无效的状态值

    # 类型过滤（内存过滤）
    if type:
        workers = [w for w in workers if _get_enum_value(w.type) == type]

    # 能力过滤（内存过滤，OR 语义）
    if capability:
        def has_any_capability(w, cap_names):
            w_caps = []
            for c in (w.capabilities or []):
                if hasattr(c, 'name'):
                    w_caps.append(c.name.lower())
                elif isinstance(c, dict):
                    w_caps.append(c.get("name", "").lower())
            return any(cn.lower() in w_caps for cn in cap_names)
        workers = [w for w in workers if has_any_capability(w, capability)]

    # 技能过滤（内存过滤，OR 语义）
    if skill:
        def has_any_skill(w, skill_names):
            w_skills = []
            for s in (w.skills or []):
                if hasattr(s, 'name'):
                    w_skills.append(s.name.lower())
                elif isinstance(s, dict):
                    w_skills.append(s.get("name", "").lower())
            return any(sn.lower() in w_skills for sn in skill_names)
        workers = [w for w in workers if has_any_skill(w, skill)]

    # 资源过滤（内存过滤，OR 语义）
    if resource:
        def has_any_resource(w, resource_ids):
            w_resources = []
            for r in (w.resources or []):
                if hasattr(r, 'id'):
                    w_resources.append(r.id.lower())
                elif isinstance(r, dict):
                    w_resources.append(r.get("id", "").lower())
            return any(rid.lower() in w_resources for rid in resource_ids)
        workers = [w for w in workers if has_any_resource(w, resource)]

    # 转换响应
    items = [
        WorkerResponse(
            success=True,
            id=w.id,
            type=_get_enum_value(w.type),
            name=w.identity.name,
            handle=w.identity.handle,
            description=w.identity.description,
            lifecycle_state=_get_enum_value(w.lifecycle_state),
            runtime_state=_get_enum_value(w.state.runtime_state),
            source_type=_get_enum_value(w.source_type),
            version=w.version,
            responsibilities=w.responsibilities,
            domains=w.domains,
            availability=_get_enum_value(w.state.availability),
            trust_level=_get_enum_value(w.state.trust_level),
            created_at=w.created_at.isoformat() if w.created_at else None,
            updated_at=w.updated_at.isoformat() if w.updated_at else None,
        )
        for w in workers
    ]

    return WorkerListResponse(success=True, items=items, total=len(items))


class UpdateTrustLevelRequest(BaseModel):
    """修改 Worker 信任级别请求"""
    trust_level: str = Field(..., description="信任级别 (unverified/verifying/sandbox_only/guarded/trusted)")


class UpdateTrustLevelResponse(BaseModel):
    """修改 Worker 信任级别响应"""
    success: bool
    worker_id: str
    trust_level: str


@router.put("/workers/{worker_id}/trust-level", response_model=UpdateTrustLevelResponse)
async def update_worker_trust_level(
    worker_id: str,
    request: UpdateTrustLevelRequest,
    store = Depends(get_registry_store),
):
    """修改 Worker 的信任级别"""
    from src.domain.models.worker import TrustLevel

    valid_values = [t.value for t in TrustLevel]
    if request.trust_level not in valid_values:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "INVALID_TRUST_LEVEL",
                "message": f"Invalid trust_level '{request.trust_level}'. Must be one of: {valid_values}"
            }
        )

    existing = store.get_by_id(worker_id)
    if existing is None:
        exc = WorkerNotFoundException(worker_id)
        return exception_to_response(
            exc=exc,
            status_code=status.HTTP_404_NOT_FOUND,
            context={"worker_id": worker_id},
        )

    updated = store.update_trust_level(worker_id, TrustLevel(request.trust_level))

    return UpdateTrustLevelResponse(
        success=True,
        worker_id=worker_id,
        trust_level=_get_enum_value(updated.state.trust_level),
    )


@router.put("/workers/{worker_id}/online", response_model=RuntimeStateResponse)
async def set_worker_online(
    worker_id: str,
    updated_by: Optional[str] = Query(None, description="更新者"),
    service: WorkerRuntimeStateService = Depends(get_worker_runtime_state_service),
):
    """设置 Worker 为在线"""
    try:
        updated = service.set_online(worker_id, updated_by=updated_by)

        return RuntimeStateResponse(
            success=True,
            worker_id=updated.id,
            runtime_state=_get_enum_value(updated.state.runtime_state),
            lifecycle_state=_get_enum_value(updated.lifecycle_state),
            version=updated.version,
        )

    except WorkerNotFoundException as e:
        return exception_to_response(
            exc=e,
            status_code=status.HTTP_404_NOT_FOUND,
            context={"worker_id": worker_id},
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "INVALID_STATE_TRANSITION", "message": str(e)}
        )


@router.put("/workers/{worker_id}/offline", response_model=RuntimeStateResponse)
async def set_worker_offline(
    worker_id: str,
    updated_by: Optional[str] = Query(None, description="更新者"),
    service: WorkerRuntimeStateService = Depends(get_worker_runtime_state_service),
):
    """设置 Worker 为离线"""
    try:
        updated = service.set_offline(worker_id, updated_by=updated_by)

        return RuntimeStateResponse(
            success=True,
            worker_id=updated.id,
            runtime_state=_get_enum_value(updated.state.runtime_state),
            lifecycle_state=_get_enum_value(updated.lifecycle_state),
            version=updated.version,
        )

    except WorkerNotFoundException as e:
        return exception_to_response(
            exc=e,
            status_code=status.HTTP_404_NOT_FOUND,
            context={"worker_id": worker_id},
        )


class SetAvailabilityRequest(BaseModel):
    """设置可见性请求"""
    availability: str = Field(..., description="可见性状态 (private/protected/public)")


class AvailabilityResponse(BaseModel):
    """可见性响应"""
    success: bool
    worker_id: str
    availability: str
    version: int


@router.put("/workers/{worker_id}/availability", response_model=AvailabilityResponse)
async def set_worker_availability(
    worker_id: str,
    request: SetAvailabilityRequest,
    updated_by: Optional[str] = Query(None, description="更新者"),
    service: WorkerRuntimeStateService = Depends(get_worker_runtime_state_service),
):
    """
    设置 Worker 的可见性状态。

    Args:
        worker_id: Worker ID
        request: 可见性状态请求
        updated_by: 更新者

    Returns:
        更新后的 Worker 信息
    """
    from src.domain.models.worker import Availability

    # 验证 availability 值
    valid_values = [a.value for a in Availability]
    if request.availability not in valid_values:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "INVALID_AVAILABILITY",
                "message": f"Invalid availability '{request.availability}'. Must be one of: {valid_values}"
            }
        )

    availability = Availability(request.availability)

    try:
        updated = service.set_availability(worker_id, availability, updated_by=updated_by)

        return AvailabilityResponse(
            success=True,
            worker_id=updated.id,
            availability=_get_enum_value(updated.state.availability),
            version=updated.version,
        )

    except WorkerNotFoundException as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": e.code, "message": e.message}
        )


class SetAvailabilityRequest(BaseModel):
    """设置可见性请求"""
    availability: str = Field(..., description="可见性状态 (private/protected/public)")


class AvailabilityResponse(BaseModel):
    """可见性响应"""
    success: bool
    worker_id: str
    availability: str
    version: int


@router.put("/workers/{worker_id}/availability", response_model=AvailabilityResponse)
async def set_worker_availability(
    worker_id: str,
    request: SetAvailabilityRequest,
    updated_by: Optional[str] = Query(None, description="更新者"),
    service: WorkerRuntimeStateService = Depends(get_worker_runtime_state_service),
):
    """
    设置 Worker 的可见性状态。

    Args:
        worker_id: Worker ID
        request: 可见性状态请求
        updated_by: 更新者

    Returns:
        更新后的 Worker 信息
    """
    from src.domain.models.worker import Availability

    # 验证 availability 值
    valid_values = [a.value for a in Availability]
    if request.availability not in valid_values:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "INVALID_AVAILABILITY",
                "message": f"Invalid availability '{request.availability}'. Must be one of: {valid_values}"
            }
        )

    availability = Availability(request.availability)

    try:
        updated = service.set_availability(worker_id, availability, updated_by=updated_by)

        return AvailabilityResponse(
            success=True,
            worker_id=updated.id,
            availability=_get_enum_value(updated.state.availability),
            version=updated.version,
        )

    except WorkerNotFoundException as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": e.code, "message": e.message}
        )


class SetAvailabilityRequest(BaseModel):
    """设置可见性请求"""
    availability: str = Field(..., description="可见性状态 (private/protected/public)")


class AvailabilityResponse(BaseModel):
    """可见性响应"""
    success: bool
    worker_id: str
    availability: str
    version: int


@router.put("/workers/{worker_id}/availability", response_model=AvailabilityResponse)
async def set_worker_availability(
    worker_id: str,
    request: SetAvailabilityRequest,
    updated_by: Optional[str] = Query(None, description="更新者"),
    service: WorkerRuntimeStateService = Depends(get_worker_runtime_state_service),
):
    """
    设置 Worker 的可见性状态。

    Args:
        worker_id: Worker ID
        request: 可见性状态请求
        updated_by: 更新者

    Returns:
        更新后的 Worker 信息
    """
    from src.domain.models.worker import Availability

    # 验证 availability 值
    valid_values = [a.value for a in Availability]
    if request.availability not in valid_values:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "INVALID_AVAILABILITY",
                "message": f"Invalid availability '{request.availability}'. Must be one of: {valid_values}"
            }
        )

    availability = Availability(request.availability)

    try:
        updated = service.set_availability(worker_id, availability, updated_by=updated_by)

        return AvailabilityResponse(
            success=True,
            worker_id=updated.id,
            availability=_get_enum_value(updated.state.availability),
            version=updated.version,
        )

    except WorkerNotFoundException as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": e.code, "message": e.message}
        )


class SetAvailabilityRequest(BaseModel):
    """设置可见性请求"""
    availability: str = Field(..., description="可见性状态 (private/protected/public)")


class AvailabilityResponse(BaseModel):
    """可见性响应"""
    success: bool
    worker_id: str
    availability: str
    version: int


@router.put("/workers/{worker_id}/availability", response_model=AvailabilityResponse)
async def set_worker_availability(
    worker_id: str,
    request: SetAvailabilityRequest,
    updated_by: Optional[str] = Query(None, description="更新者"),
    service: WorkerRuntimeStateService = Depends(get_worker_runtime_state_service),
):
    """
    设置 Worker 的可见性状态。

    Args:
        worker_id: Worker ID
        request: 可见性状态请求
        updated_by: 更新者

    Returns:
        更新后的 Worker 信息
    """
    from src.domain.models.worker import Availability

    # 验证 availability 值
    valid_values = [a.value for a in Availability]
    if request.availability not in valid_values:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "INVALID_AVAILABILITY",
                "message": f"Invalid availability '{request.availability}'. Must be one of: {valid_values}"
            }
        )

    availability = Availability(request.availability)

    try:
        updated = service.set_availability(worker_id, availability, updated_by=updated_by)

        return AvailabilityResponse(
            success=True,
            worker_id=updated.id,
            availability=_get_enum_value(updated.state.availability),
            version=updated.version,
        )

    except WorkerNotFoundException as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": e.code, "message": e.message}
        )


# ============================================================================
# Sync Endpoint (Atomic: create/update + online + upsert profile)
# ============================================================================

class SummaryData(BaseModel):
    """摘要数据"""
    capability: Optional[str] = Field(None, description="能力描述")
    role: Optional[str] = Field(None, description="角色定位")


class SyncProfileData(BaseModel):
    """Profile data within a sync request"""
    profile_id: str = Field(default="default", description="Profile ID，默认为 default", min_length=1)
    display_name: Optional[str] = Field(None, description="显示名称")
    soul_md: Optional[str] = Field(None, description="SOUL.md 内容")
    contents: dict[str, Any] = Field(default_factory=dict, description="扩展内容 Map，支持任意类型")
    skill_sets: list[dict[str, Any]] = Field(default_factory=list, description="技能集")
    metadata: dict[str, Any] = Field(default_factory=dict, description="扩展元数据")
    summary: Optional[SummaryData] = Field(None, description="摘要信息，包含 capability 和 role")
    activate: bool = Field(default=True, description="是否激活 profile")


class SyncWorkerRequest(BaseModel):
    """
    Atomic sync request: create/update worker + set online + upsert profile.

    Sent by BCS during bot onboard. Idempotent — safe to call repeatedly.
    """
    type: str = Field(default="bot", description="Worker 类型")
    name: str = Field(..., description="显示名称", min_length=1)
    description: Optional[str] = Field(None, description="描述")
    responsibilities: list[str] = Field(default_factory=lambda: ["general"])
    domains: list[str] = Field(default_factory=list)
    runtime_state: Optional[str] = Field(None, description="运行态 (online/offline)，不传则不修改")
    capabilities: list[dict[str, Any]] = Field(default_factory=list)
    skills: list[dict[str, Any]] = Field(default_factory=list)
    availability: str = Field(default="private", description="可见性 (private/protected/public)")
    trust_level: str = Field(default="guarded")
    profile_key: Optional[str] = Field(None, description="Profile Key")
    profile: SyncProfileData = Field(..., description="Profile 数据")
    sync_llm: bool = Field(False, description="是否同步调用 LLM 分析，True 为同步等待结果，False 为异步后台执行")


class SyncWorkerResponse(BaseModel):
    """Sync 响应"""
    success: bool
    worker_id: str
    created: bool = Field(description="True if worker was newly created, False if updated")
    runtime_state: Optional[str] = Field(None, description="运行态，None 表示未修改")
    profile_id: str
    profile_activated: bool


# ============================================================================
# Background Task: LLM Profile Analysis with Concurrency Control
# ============================================================================

# 并发控制：线程池限制并发数
_LLM_ANALYSIS_EXECUTOR: Optional[ThreadPoolExecutor] = None
_LLM_ANALYSIS_MAX_WORKERS = 4  # 最大工作线程数
_LLM_ANALYSIS_RUNNING_COUNT = 0  # 当前运行任务数
_LLM_ANALYSIS_COUNT_LOCK = threading.Lock()  # 线程安全计数器锁


def _get_llm_analysis_executor() -> ThreadPoolExecutor:
    """获取 LLM 分析线程池（懒加载）"""
    global _LLM_ANALYSIS_EXECUTOR
    if _LLM_ANALYSIS_EXECUTOR is None:
        _LLM_ANALYSIS_EXECUTOR = ThreadPoolExecutor(
            max_workers=_LLM_ANALYSIS_MAX_WORKERS,
            thread_name_prefix="llm-analysis-"
        )
    return _LLM_ANALYSIS_EXECUTOR


def _increment_llm_analysis_count() -> int:
    """增加运行计数并返回当前值"""
    global _LLM_ANALYSIS_RUNNING_COUNT
    with _LLM_ANALYSIS_COUNT_LOCK:
        _LLM_ANALYSIS_RUNNING_COUNT += 1
        return _LLM_ANALYSIS_RUNNING_COUNT


def _decrement_llm_analysis_count() -> int:
    """减少运行计数并返回当前值"""
    global _LLM_ANALYSIS_RUNNING_COUNT
    with _LLM_ANALYSIS_COUNT_LOCK:
        _LLM_ANALYSIS_RUNNING_COUNT -= 1
        return _LLM_ANALYSIS_RUNNING_COUNT


async def _analyze_and_persist_async(
    worker_id: str,
    profile_id: str,
    content_serialized: dict,
    max_retries: int = 2,
) -> None:
    """
    异步后台任务：LLM 分析 + 写回数据库 + 触发向量重建（并发控制）

    使用线程池限制：
    - 最多 4 个线程同时执行
    - 超过限制的任务在线程池队列中等待

    Args:
        worker_id: Worker ID
        profile_id: Profile ID
        content_serialized: 序列化的 Profile 内容（dict）
        max_retries: 最大重试次数
    """
    logger = logging.getLogger(__name__)

    executor = _get_llm_analysis_executor()
    loop = asyncio.get_event_loop()

    # 增加运行计数
    current_running = _increment_llm_analysis_count()
    logger.info(
        f"[BG-LLM][{worker_id}] Task started "
        f"(running: {current_running}/{_LLM_ANALYSIS_MAX_WORKERS})"
    )

    try:
        # 在线程池中执行同步任务
        await loop.run_in_executor(
            executor,
            _analyze_and_persist_sync,
            worker_id,
            profile_id,
            content_serialized,
            max_retries,
        )
    finally:
        # 减少运行计数
        current_running = _decrement_llm_analysis_count()
        logger.info(
            f"[BG-LLM][{worker_id}] Task finished "
            f"(running: {current_running}/{_LLM_ANALYSIS_MAX_WORKERS})"
        )


def _analyze_and_persist_sync(
    worker_id: str,
    profile_id: str,
    content_serialized: dict,
    max_retries: int = 2,
) -> None:
    """
    同步执行 LLM 分析 + 写回数据库 + 触发向量重建

    注意：此函数由 _analyze_and_persist_async 通过 asyncio.to_thread 调用。

    Args:
        worker_id: Worker ID
        profile_id: Profile ID
        content_serialized: 序列化的 Profile 内容（dict）
        max_retries: 最大重试次数
    """
    import time
    import logging
    logger = logging.getLogger(__name__)

    for attempt in range(max_retries + 1):
        try:
            from src.interfaces.api.profile_routes import (
                _get_profile_service,
                _get_profile_analyzer,
                _trigger_index_sync,
            )
            from src.domain.models.worker_profile_content import WorkerProfileContent

            # 重建 ProfileContent 对象
            content = WorkerProfileContent.model_validate(content_serialized)

            # Step 1: LLM 分析
            analyzer = _get_profile_analyzer()
            if not analyzer:
                logger.warning(f"[BG-LLM][{worker_id}] Analyzer not available, skip analysis")
                return

            logger.info(f"[BG-LLM][{worker_id}] Starting analysis (attempt {attempt + 1}/{max_retries + 1})")

            analysis = analyzer.analyze(content)

            if not analysis.llm_success:
                logger.warning(f"[BG-LLM][{worker_id}] Analysis failed: {analysis.error_message}")
                if attempt < max_retries:
                    time.sleep(2 ** attempt)  # 指数退避
                    continue
                return

            # Step 2: 写回数据库
            logger.info(f"[BG-LLM][{worker_id}] Writing analysis results to DB, tags={analysis.capability_tags}")

            # 更新 contents 字段
            content.contents["profile"] = analysis.semantic_profile
            content.contents["capabilities"] = analysis.capability_tags
            content.contents["short_profile"] = analysis.short_profile  # 新增：精简画像

            # 重新获取 service（后台任务需要新实例）
            profile_service = _get_profile_service()

            # 重新保存 Profile
            updated_content = profile_service.register_or_update_profile(
                worker_id=worker_id,
                profile_id=profile_id,
                display_name=content.display_name,
                soul_md=content.soul_md,
                contents=content.contents,
                skill_sets=[s.model_dump() for s in content.skill_sets],
                metadata=content.metadata,
                activate=True,
            )

            logger.info(
                f"[BG-LLM][{worker_id}] Analysis persisted successfully, "
                f"tags={analysis.capability_tags}, profile_len={len(analysis.semantic_profile or '')}"
            )

            # Step 3: 触发向量重建
            _trigger_index_sync(worker_id)
            logger.info(f"[BG-LLM][{worker_id}] Vector rebuild triggered")

            return  # 成功完成

        except Exception as e:
            logger.error(
                f"[BG-LLM][{worker_id}] Background task failed (attempt {attempt + 1}): {e}",
                exc_info=True
            )
            if attempt < max_retries:
                time.sleep(2 ** attempt)
            else:
                logger.error(f"[BG-LLM][{worker_id}] All retries exhausted, giving up")


@router.post(
    "/workers/{worker_id}/sync",
    response_model=SyncWorkerResponse,
    status_code=status.HTTP_200_OK,
)
async def sync_worker(
    worker_id: str,
    request: SyncWorkerRequest,
    import_service: WorkerImportService = Depends(get_worker_import_service),
    runtime_service: WorkerRuntimeStateService = Depends(get_worker_runtime_state_service),
    store = Depends(get_registry_store),
):
    """
    Atomic sync: create/update worker + set online + upsert profile.

    Idempotent — safe to call repeatedly with the same data.

    Args:
        worker_id: Worker ID
        request: 同步请求体
        sync_llm: 是否同步调用 LLM 分析
            - True: 同步等待 LLM 分析完成后返回
            - False: 异步后台执行 LLM 分析，立即返回

    Error Handling:
    - Worker creation failure: 立即返回错误（无副作用）
    - Set online failure: 执行补偿（删除刚创建的 Worker）
    - Profile upsert failure: 非致命错误，Worker 保持 online
    """
    import logging
    logger = logging.getLogger(__name__)

    created = False
    worker_created = False  # 标记是否已创建（用于补偿）

    try:
        # Step 1: Create or update worker
        existing = store.get_by_id(worker_id)

        if existing is None:
            # Create new worker
            worker_data = {
                "id": worker_id,
                "type": request.type,
                "identity": {
                    "name": request.name,
                    "handle": f"@{worker_id}",
                    "description": request.description,
                },
                "responsibilities": request.responsibilities,
                "domains": request.domains,
                "capabilities": request.capabilities if request.capabilities else [
                    {"name": "general", "level": "intermediate"}
                ],
                "skills": request.skills,
                "resources": [],
                "state": {
                    "availability": request.availability,
                    "trust_level": request.trust_level,
                },
                # 如果没传 profile_key，默认使用 worker_id:profile_id
                "active_profile_key": request.profile_key or f"{worker_id}:{request.profile.profile_id}",
            }

            try:
                import_service.import_from_api(worker_data=worker_data, actor="bcs-sync")
                created = True
                worker_created = True  # 标记已创建，用于失败时补偿
                logger.info(f"[SYNC] Worker created: {worker_id}")
            except DuplicateWorkerException:
                # Race condition: another request created it between check and create
                logger.info(f"[SYNC] Worker already exists (race): {worker_id}")
                created = False
        else:
            worker_dict = existing.model_dump()
            worker_dict["identity"]["name"] = request.name
            if request.description is not None:
                worker_dict["identity"]["description"] = request.description
            worker_dict["responsibilities"] = request.responsibilities
            worker_dict["domains"] = request.domains
            # 更新 availability
            worker_dict["state"]["availability"] = request.availability
            # 如果没传 profile_key，默认使用 worker_id:profile_id
            worker_dict["active_profile_key"] = request.profile_key or f"{worker_id}:{request.profile.profile_id}"

            from src.domain.models.worker import Worker
            updated_worker = Worker.model_validate(worker_dict)
            store.update(updated_worker)
            logger.info(f"[SYNC] Worker updated: {worker_id}")

        # Step 2: Set runtime state
        # runtime_state 为 None 时不修改，否则按传入值设置
        # LLM 分析条件：availability != "private" 或 runtime_state == "online"
        effective_runtime_state: Optional[str] = None
        should_run_llm_analysis = False

        # 决定是否执行 LLM 分析
        # 条件：availability 不为 private，或者 runtime_state 为 online
        if request.availability != "private":
            should_run_llm_analysis = True
            logger.info(f"[SYNC] Worker {worker_id} availability={request.availability} (not private), enable LLM analysis")
        elif request.runtime_state == "online":
            should_run_llm_analysis = True
            logger.info(f"[SYNC] Worker {worker_id} runtime_state=online, enable LLM analysis")
        else:
            logger.info(f"[SYNC] Worker {worker_id} availability=private and runtime_state={request.runtime_state}, skip LLM analysis")

        # 设置 runtime_state（仅当传入了值时才设置）
        if request.runtime_state is not None:
            effective_runtime_state = request.runtime_state
            try:
                if effective_runtime_state == "online":
                    runtime_service.set_online(worker_id, updated_by="bcs-sync")
                else:
                    runtime_service.set_offline(worker_id, updated_by="bcs-init")

                logger.info(f"[SYNC] Worker runtime state set: {worker_id} -> {effective_runtime_state}")
            except ValueError as e:
                # Already online or invalid state — log and continue
                logger.info(f"[SYNC] set_runtime_state skipped for {worker_id}: {e}")
            except Exception as e:
                # 设置 runtime state 失败：执行补偿清理
                logger.error(f"[SYNC] set_runtime_state failed for {worker_id}: {e}")

                if worker_created:
                    try:
                        # 补偿：删除刚创建的 Worker，避免僵尸记录
                        store.delete(worker_id)
                        logger.warning(f"[SYNC] Compensation: deleted worker {worker_id} due to set_runtime_state failure")
                    except Exception as cleanup_error:
                        logger.error(f"[SYNC] Compensation failed for {worker_id}: {cleanup_error}")
                        # 补偿失败记录，需要人工介入

                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail={"code": "SET_RUNTIME_STATE_FAILED", "message": f"Failed to set worker runtime state: {str(e)}"}
                )
        else:
            logger.info(f"[SYNC] Worker {worker_id} runtime_state not provided, skip runtime state update")

        # Step 3: Upsert profile
        # 新方案：向量归属 Profile，ProfileService 自动检测内容变化并重建向量
        profile_data = request.profile
        profile_activated = False

        try:
            from src.interfaces.api.profile_routes import (
                _get_profile_service,
                _sync_profile_binding,
                _sync_worker_active_profile,
                _trigger_index_sync,
            )

            profile_service = _get_profile_service()

            # 合并 summary 到 contents 中
            merged_contents = dict(profile_data.contents)  # 复制原有 contents
            if profile_data.summary is not None:
                merged_contents["ecb_summary"] = profile_data.summary.model_dump(exclude_none=True)

            content = profile_service.register_or_update_profile(
                worker_id=worker_id,
                profile_id=profile_data.profile_id,
                display_name=profile_data.display_name,
                description=request.description,
                soul_md=profile_data.soul_md,
                contents=merged_contents,
                skill_sets=profile_data.skill_sets,
                metadata=profile_data.metadata,
                activate=profile_data.activate,
            )

            # ↑ 新方案：register_or_update_profile 内部检测内容变化，
            #   变化时自动重建向量，无需外部干预

            # 根据条件决定是否执行 LLM 分析
            if should_run_llm_analysis:
                content_serialized = content.model_dump()

                if request.sync_llm:
                    # 同步调用：直接等待 LLM 分析完成
                    logger.info(f"[SYNC] Profile registered, starting synchronous LLM analysis")
                    try:
                        await _analyze_and_persist_async(
                            worker_id=worker_id,
                            profile_id=profile_data.profile_id,
                            content_serialized=content_serialized,
                        )
                        logger.info(f"[SYNC] Synchronous LLM analysis completed")
                    except Exception as e:
                        logger.warning(f"[SYNC] Synchronous LLM analysis failed: {e}")
                else:
                    # 异步调用：后台执行
                    asyncio.create_task(
                        _analyze_and_persist_async(
                            worker_id=worker_id,
                            profile_id=profile_data.profile_id,
                            content_serialized=content_serialized,
                        )
                    )
                    logger.info(f"[SYNC] Profile registered, LLM analysis scheduled in background")
            else:
                logger.info(f"[SYNC] Profile registered, LLM analysis skipped (availability=private, runtime_state=offline)")

            if profile_data.activate:
                _sync_profile_binding(worker_id, profile_data.profile_id)
                _sync_worker_active_profile(worker_id, profile_data.profile_id)
                profile_activated = True

            # 🔧 关键：同步 worker state 到向量 payload
            # profile 内容无变化时，register_or_update_profile 不会触发向量更新
            # 但 worker state（availability/runtime_state）变化需要同步到向量 payload
            if request.runtime_state is not None:
                try:
                    from src.interfaces.api.dependencies.worker_dependencies import get_worker_profile_content_service
                    profile_service = get_worker_profile_content_service()
                    profile_service.sync_worker_state_to_vectors(worker_id)
                    logger.info(f"[SYNC] Worker state synced to vectors: {worker_id}")
                except Exception as e:
                    logger.warning(f"[SYNC] Failed to sync worker state to vectors: {e}")

            # 重置服务缓存（后台任务完成向量重建后会再次触发）
            _trigger_index_sync(worker_id)
            logger.info(f"[SYNC] Profile upserted: {worker_id}/{profile_data.profile_id}")

        except Exception as e:
            logger.warning(f"[SYNC] Profile upsert failed for {worker_id}: {e}")
            # Profile failure is non-fatal — worker is still created and online

        # Step 4: Capability verify integration (DRM 配置开关)
        from src.application.utils.drm_config_helper import is_capability_verify_enabled
        _cap_verify_on = is_capability_verify_enabled()
        if _cap_verify_on is None:
            from src.infra.config.feature_flags import FeatureFlags
            _cap_verify_on = FeatureFlags.is_capability_verify_enabled()
        if _cap_verify_on:
            try:
                from src.domain.models.worker import TrustLevel
                from src.domain.events import get_event_bus, WorkerProfileCreatedEvent
                from src.interfaces.api.dependencies.fusion_dependencies import get_capability_verify_service

                verify_service = get_capability_verify_service()
                if verify_service is not None and not verify_service._running:
                    await verify_service.start()
                    logger.info("[SYNC] CapabilityVerifyService lazy-started")

                store.update_trust_level(worker_id, TrustLevel.UNVERIFIED)
                logger.info("[SYNC] Worker set to unverified: %s", worker_id)

                event_bus = get_event_bus()
                event_bus.publish(WorkerProfileCreatedEvent(worker_id=worker_id))
                logger.info("[SYNC] WorkerProfileCreatedEvent published: %s", worker_id)
            except Exception as e:
                logger.warning("[SYNC] Capability verify setup failed for %s: %s", worker_id, e)

        return SyncWorkerResponse(
            success=True,
            worker_id=worker_id,
            created=created,
            runtime_state=effective_runtime_state,
            profile_id=profile_data.profile_id,
            profile_activated=profile_activated,
        )

    except HTTPException:
        # 已处理的异常，直接抛出
        raise
    except Exception as e:
        logger.error(f"[SYNC] Sync failed for {worker_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"code": "SYNC_FAILED", "message": f"Worker sync failed: {str(e)}"}
        )


@router.patch("/workers/{worker_id}", response_model=WorkerResponse)
async def update_worker(
    worker_id: str,
    update_data: dict[str, Any],
    store = Depends(get_registry_store),
):
    """局部更新 Worker"""
    # 获取现有 Worker
    existing = store.get_by_id(worker_id)
    if existing is None:
        exc = WorkerNotFoundException(worker_id)
        return exception_to_response(
            exc=exc,
            status_code=status.HTTP_404_NOT_FOUND,
            context={"worker_id": worker_id},
        )

    # 合并更新数据
    existing_dict = existing.model_dump(exclude_none=True)

    # 处理嵌套更新（如 identity）
    for key, value in update_data.items():
        if key == "identity" and isinstance(value, dict):
            existing_identity = existing_dict.get("identity", {})
            existing_identity.update(value)
            existing_dict["identity"] = existing_identity
        elif key not in ["id", "version", "created_at", "source_type"]:
            # 保护关键字段不被修改
            existing_dict[key] = value

    try:
        # 重新校验并创建更新后的 Worker
        from src.domain.models.worker import Worker
        updated_worker = Worker.model_validate(existing_dict)

        # 检测 state 字段是否变化（is_public/availability 影响向量 payload）
        state_changed = (
            "state" in update_data or
            existing.state.is_public != updated_worker.state.is_public
        )

        # 保存
        updated = store.update(updated_worker)

        # 如果 state 变化，触发向量重建以更新 payload 中的 availability
        if state_changed:
            try:
                from src.interfaces.api.dependencies.fusion_dependencies import _build_vector_index_for_worker
                import asyncio
                # 异步触发重建，不阻塞 API 响应
                asyncio.create_task(_rebuild_vector_async(worker_id))
            except Exception as e:
                logger.warning(f"[WorkerRoutes] Failed to trigger vector rebuild for {worker_id}: {e}")

        return WorkerResponse(
            success=True,
            id=updated.id,
            type=_get_enum_value(updated.type),
            name=updated.identity.name,
            handle=updated.identity.handle,
            description=updated.identity.description,
            lifecycle_state=_get_enum_value(updated.lifecycle_state),
            runtime_state=_get_enum_value(updated.state.runtime_state),
            source_type=_get_enum_value(updated.source_type),
            version=updated.version,
            responsibilities=updated.responsibilities,
            domains=updated.domains,
            availability=_get_enum_value(updated.state.availability),
            trust_level=_get_enum_value(updated.state.trust_level),
            created_at=updated.created_at.isoformat() if updated.created_at else None,
            updated_at=updated.updated_at.isoformat() if updated.updated_at else None,
        )

    except ValueError as e:
        if "Version conflict" in str(e):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"code": "VERSION_CONFLICT", "message": str(e)}
            )
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "VALIDATION_ERROR", "message": str(e)}
        )
    except ValidationError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=e.errors()
        )


class DeleteWorkerResponse(BaseModel):
    """删除 Worker 响应"""
    success: bool
    worker_id: str
    message: str


@router.delete(
    "/workers/{worker_id}",
    response_model=DeleteWorkerResponse,
    status_code=status.HTTP_200_OK,
)
async def delete_worker(
    worker_id: str,
    store = Depends(get_registry_store),
    profile_service: WorkerProfileContentService = Depends(get_worker_profile_content_service),
):
    """
    删除 Worker

    删除操作会级联删除：
    - Worker 运行状态记录
    - Profile 绑定记录
    - Profile 内容数据（含向量索引）
    - 缓存失效

    审计日志会保留用于历史追溯。
    """
    import logging
    logger = logging.getLogger(__name__)

    # 检查 Worker 是否存在
    existing = store.get_by_id(worker_id)
    if existing is None:
        exc = WorkerNotFoundException(worker_id)
        return exception_to_response(
            exc=exc,
            status_code=status.HTTP_404_NOT_FOUND,
            context={"worker_id": worker_id},
        )

    try:
        # Step 1: 删除该 Worker 的所有 Profiles（会自动删除向量）
        try:
            profiles_result = profile_service.list_profiles(worker_id)
            for profile in profiles_result.items:
                logger.info(f"[DELETE-WORKER] Deleting profile: {worker_id}:{profile.profile_id}")
                profile_service.delete_profile(worker_id, profile.profile_id)
        except Exception as e:
            logger.warning(f"[DELETE-WORKER] Failed to delete profiles for {worker_id}: {e}")
            # Profile 删除失败不阻断主流程

        # Step 2: 调用 store 删除（会自动处理级联删除和缓存失效）
        store.delete(worker_id)

        logger.info(f"[DELETE-WORKER] Worker deleted successfully: {worker_id}")
        return DeleteWorkerResponse(
            success=True,
            worker_id=worker_id,
            message=f"Worker {worker_id} deleted successfully"
        )

    except Exception as e:
        logger.error(f"[DELETE-WORKER] Failed to delete worker {worker_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"code": "DELETE_FAILED", "message": f"Failed to delete worker: {str(e)}"}
        )


# =============================================================================
# 管理员 API - 批量清理
# =============================================================================

class CleanupResponse(BaseModel):
    """清理响应"""
    success: bool
    message: str
    deleted_counts: dict = Field(default_factory=dict)


@router.delete(
    "/admin/cleanup-all",
    status_code=status.HTTP_200_OK,
    response_model=CleanupResponse,
    summary="清理所有 BCS Fuse 数据（管理员）",
    description="""
    危险操作！清理以下数据：
    - bcsfuse_workers
    - bcsfuse_worker_profile_contents
    - bcsfuse_worker_runtime_states
    - bcsfuse_worker_profile_bindings
    - bcsfuse_worker_audit_logs
    - Qdrant 向量库（qdrant_vector_store）

    使用方式：
    curl -X DELETE "http://localhost:8765/v1/admin/cleanup-all?confirm=true" \\
         -H "Content-Type: application/json" \\
         -b "spanner=xxx; authorization=xxx; IAM_TOKEN=xxx"
    """
)
async def cleanup_all_data(
    confirm: bool = Query(False, description="必须设置为 true 才能执行清理"),
    store = Depends(get_registry_store),
):
    """
    清理所有 BCS Fuse 相关数据 - SQLite only for open-core

    这是一个危险操作，需要 confirm=true 才会执行。
    """
    if not confirm:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "CONFIRMATION_REQUIRED",
                "message": "必须设置 confirm=true 才能执行清理操作"
            }
        )

    import logging
    logger = logging.getLogger(__name__)

    try:
        # Open-core: SQLite only
        import sqlite3
        from src.infra.config.worker_registry_settings import WorkerRegistrySettings

        registry_settings = WorkerRegistrySettings()
        db_path = registry_settings.get_effective_db_path()

        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        deleted_counts = {}

        tables = [
            get_table_name("bcsfuse_vector_embeddings"),
            get_table_name("bcsfuse_worker_profile_contents"),
            get_table_name("bcsfuse_worker_runtime_states"),
            get_table_name("bcsfuse_worker_profile_bindings"),
            get_table_name("bcsfuse_worker_audit_logs"),
            get_table_name("bcsfuse_workers"),
        ]

        for table_name in tables:
            try:
                # Validate table name is alphanumeric + underscore only
                if not all(c.isalnum() or c == '_' for c in table_name):
                    deleted_counts[table_name] = 0
                    continue
                cursor.execute(f"SELECT COUNT(*) FROM {table_name}")
                count_before = cursor.fetchone()[0]
                cursor.execute(f"DELETE FROM {table_name} WHERE 1=1")
                deleted_counts[table_name] = count_before
            except sqlite3.OperationalError:
                deleted_counts[table_name] = 0  # 表不存在

        conn.commit()
        conn.close()

        logger.info("[Open-Core] SQLite cleanup completed: %s", deleted_counts)

        # 🔧 清理 Qdrant 向量库（真正删除向量数据）
        try:
            from src.interfaces.api.dependencies.worker_dependencies import (
                _get_profile_embedding_store,
                reset_stores,
            )

            # 获取 ProfileEmbeddingStore 并清理向量数据
            profile_embedding_store = _get_profile_embedding_store()
            if profile_embedding_store and hasattr(profile_embedding_store, 'vector_store'):
                vector_count = profile_embedding_store.vector_store.size()
                profile_embedding_store.vector_store.clear()
                deleted_counts["qdrant_vector_store"] = vector_count
                logger.info("[Open-Core] Qdrant vector store cleared: %d vectors deleted", vector_count)

            # 重置所有存储单例
            reset_stores()
            logger.info("[Open-Core] All store singletons reset successfully")
        except Exception as e:
            logger.warning("[Open-Core] Failed to cleanup vector store: %s", e)
            deleted_counts["qdrant_vector_store"] = f"error: {e}"

        total_deleted = sum(v for v in deleted_counts.values() if isinstance(v, int))

        return CleanupResponse(
            success=True,
            message=f"清理完成，共删除 {total_deleted} 条记录",
            deleted_counts=deleted_counts
        )

    except Exception as e:
        logger.error("[ADMIN] Cleanup failed: %s", e, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"code": "CLEANUP_FAILED", "message": f"清理失败: {str(e)}"}
        )


# ============================================================================
# 向量库定时任务管理 API
# ============================================================================

class VectorStoreMaintenanceResponse(BaseModel):
    """向量库维护响应"""
    success: bool
    message: str
    rebuilt_count: int = 0
    purged_count: int = 0
    duration_seconds: float = 0.0


class VectorStoreSchedulerStatusResponse(BaseModel):
    """向量库定时任务状态响应"""
    is_running: bool
    jobs: list[dict] = []


@router.post(
    "/admin/vector-store/maintenance",
    status_code=status.HTTP_200_OK,
    response_model=VectorStoreMaintenanceResponse,
    summary="向量库维护（手动触发）",
    description="""
    手动触发向量库维护任务：
    1. 全量重建向量索引（从 ZDAS 重新加载）
    2. 物理删除所有软删除的 embeddings

    使用方式：
    curl -X POST "http://localhost:8765/v1/admin/vector-store/maintenance" \
         -H "Content-Type: application/json" \
         -b "spanner=xxx; authorization=xxx; IAM_TOKEN=xxx"
    """
)
async def vector_store_maintenance():
    """
    手动触发向量库维护任务
    """
    import logging
    logger = logging.getLogger(__name__)

    try:
        from src.interfaces.api.dependencies.worker_dependencies import _get_profile_embedding_store

        # 获取 ProfileEmbeddingStore
        profile_embedding_store = _get_profile_embedding_store()
        if profile_embedding_store is None:
            return VectorStoreMaintenanceResponse(
                success=False,
                message="ProfileEmbeddingStore 未初始化",
                rebuilt_count=0,
                purged_count=0,
                duration_seconds=0.0
            )

        # 直接通过 ProfileEmbeddingStore 执行全量同步
        result = profile_embedding_store.force_full_sync()

        if result.get("success"):
            return VectorStoreMaintenanceResponse(
                success=True,
                message=f"维护完成：重建 {result.get('rebuilt_count', 0)} 个向量，清理 {result.get('purged_count', 0)} 个软删除向量",
                rebuilt_count=result.get("rebuilt_count", 0),
                purged_count=result.get("purged_count", 0),
                duration_seconds=result.get("total_duration", 0.0)
            )
        else:
            return VectorStoreMaintenanceResponse(
                success=False,
                message=f"维护失败：{result.get('error', '未知错误')}",
                rebuilt_count=0,
                purged_count=0,
                duration_seconds=0.0
            )

    except Exception as e:
        logger.error(f"[ADMIN] Vector store maintenance failed: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"code": "MAINTENANCE_FAILED", "message": f"维护失败: {str(e)}"}
        )


@router.get(
    "/admin/vector-store/scheduler-status",
    status_code=status.HTTP_200_OK,
    response_model=VectorStoreSchedulerStatusResponse,
    summary="获取向量库定时任务状态",
    description="""
    获取向量库定时任务调度器的状态。

    使用方式：
    curl -X GET "http://localhost:8765/v1/admin/vector-store/scheduler-status" \
         -H "Content-Type: application/json" \
         -b "spanner=xxx; authorization=xxx; IAM_TOKEN=xxx"
    """
)
async def vector_store_scheduler_status():
    """
    获取向量库定时任务状态
    """
    try:
        from src.interfaces.api.dependencies.worker_dependencies import _get_profile_embedding_store

        profile_embedding_store = _get_profile_embedding_store()
        if profile_embedding_store is None:
            return VectorStoreSchedulerStatusResponse(
                is_running=False,
                jobs=[]
            )

        # 获取同步状态
        sync_status = profile_embedding_store.get_sync_status()

        return VectorStoreSchedulerStatusResponse(
            is_running=sync_status.get("full_sync", {}).get("scheduler_running", False),
            jobs=[{
                "id": "full_sync",
                "name": "Qdrant Full Sync",
                "interval_minutes": sync_status.get("full_sync", {}).get("interval_minutes"),
            }]
        )

    except Exception as e:
        return VectorStoreSchedulerStatusResponse(
            is_running=False,
            jobs=[]
        )


@router.post(
    "/admin/vector-store/start-scheduler",
    status_code=status.HTTP_200_OK,
    response_model=VectorStoreSchedulerStatusResponse,
    summary="启动向量库定时任务",
    description="""
    启动向量库定时任务调度器（【测试模式】每 5 分钟自动维护）。

    使用方式：
    curl -X POST "http://localhost:8765/v1/admin/vector-store/start-scheduler" \
         -H "Content-Type: application/json" \
         -b "spanner=xxx; authorization=xxx; IAM_TOKEN=xxx"
    """
)
async def vector_store_start_scheduler():
    """
    启动向量库定时任务调度器
    """
    import logging
    logger = logging.getLogger(__name__)

    try:
        from src.interfaces.api.dependencies.worker_dependencies import _get_profile_embedding_store

        profile_embedding_store = _get_profile_embedding_store()
        if profile_embedding_store is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={"code": "NOT_INITIALIZED", "message": "ProfileEmbeddingStore 未初始化"}
            )

        # 直接通过 ProfileEmbeddingStore 启动调度器
        success = profile_embedding_store.start_full_sync_scheduler()

        if success:
            sync_status = profile_embedding_store.get_sync_status()
            return VectorStoreSchedulerStatusResponse(
                is_running=sync_status.get("full_sync", {}).get("scheduler_running", False),
                jobs=[{
                    "id": "full_sync",
                    "name": "Qdrant Full Sync",
                    "interval_minutes": sync_status.get("full_sync", {}).get("interval_minutes"),
                }]
            )
        else:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={"code": "START_FAILED", "message": "定时任务启动失败（可能需要安装 APScheduler）"}
            )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[ADMIN] Failed to start scheduler: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"code": "START_FAILED", "message": f"启动失败: {str(e)}"}
        )


# ============================================================================
# Worker Config Endpoints
# ============================================================================


@router.get(
    "/workers/{worker_id}/config",
    response_model=WorkerConfigResponse,
)
async def get_worker_config(
    worker_id: str,
    store=Depends(get_registry_store),
):
    """获取 Worker 的 fusion_enable 配置"""
    worker = store.get_by_id(worker_id)
    if worker is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "WORKER_NOT_FOUND", "message": f"Worker {worker_id} not found"},
        )
    return WorkerConfigResponse(
        success=True,
        worker_id=worker.id,
        fusion_enable=worker.config.fusion_enable,
        version=worker.version,
    )


@router.put(
    "/workers/{worker_id}/config",
    response_model=WorkerConfigResponse,
)
async def set_worker_config(
    worker_id: str,
    request: SetWorkerConfigRequest,
    updated_by: Optional[str] = Query(None, description="更新者"),
    store=Depends(get_registry_store),
    audit_log_store=Depends(get_audit_log_store),
):
    """修改 Worker 的 fusion_enable 配置"""
    from src.domain.models.worker_config import WorkerConfig
    from src.domain.models.worker_audit_log import WorkerAuditLog, WorkerAuditAction
    from src.domain.models.worker_source_info import WorkerSourceType

    worker = store.get_by_id(worker_id)
    if worker is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "WORKER_NOT_FOUND", "message": f"Worker {worker_id} not found"},
        )

    old_fusion_enable = worker.config.fusion_enable
    worker.config = WorkerConfig(fusion_enable=request.fusion_enable)

    try:
        updated = store.update(worker)
    except ValueError as e:
        if "Version conflict" in str(e):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"code": "VERSION_CONFLICT", "message": str(e)},
            )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "INVALID_CONFIG", "message": str(e)},
        )

    # 审计日志
    audit_log_store.append_log(WorkerAuditLog(
        worker_id=worker_id,
        action=WorkerAuditAction.CONFIG_CHANGED,
        old_value=str(old_fusion_enable),
        new_value=str(request.fusion_enable),
        source_type=WorkerSourceType.API,
        performed_by=updated_by,
    ))

    return WorkerConfigResponse(
        success=True,
        worker_id=updated.id,
        fusion_enable=updated.config.fusion_enable,
        version=updated.version,
    )


@router.post(
    "/workers/config/batch",
    response_model=BatchQueryConfigResponse,
)
async def batch_query_worker_configs(
    request: BatchQueryConfigRequest,
    store=Depends(get_registry_store),
):
    """根据 Worker ID 列表批量查询 fusion_enable 配置"""
    configs, not_found_ids = store.batch_get_configs(request.worker_ids)

    data: dict[str, WorkerConfigItem] = {
        wid: WorkerConfigItem(fusion_enable=cfg.fusion_enable)
        for wid, cfg in configs.items()
    }

    return BatchQueryConfigResponse(
        success=True,
        data=data,
        not_found_ids=not_found_ids,
    )


__all__ = ["router"]