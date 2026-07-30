"""Skills router — dispatches every endpoint through ``EngineManager.skills``.

Engine-specific behaviour (filesystem symlinks under
``$SKILLS_LINK_BASE_DIR`` for OpenClaw; relay-driven per-skill ops for
AiCoding) lives in the engine's ``skills`` plugin. The router only
marshals HTTP↔Plugin types and applies capability guards.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException

from engine.community.api.caps import check_capability
from engine.community.api.response import ApiResponse
from engine.community.api.skills.schemas import (
    BindPathRequest,
    CenterEnsureRequestSchema,
    CleanSymlinkRequest,
    PoolLayoutActivateApiResponse,
    PoolLayoutActivateRequest,
    PoolLayoutActivateResponse,
    PoolLayoutRollbackRequest,
    PoolQuarantineCleanupRequest,
    PoolMappingVerifyRequest,
    RuntimeLayoutProbeApiResponse,
    RuntimeLayoutProbeRequest,
    RuntimeLayoutProbeResponse,
    SyncSymlinkRequest,
)
from engine.community.core.engine.capability import Capability
from engine.community.core.engine.exceptions import CapabilityNotSupportedError
from engine.community.core.skills.models import (
    CenterEnsureItem,
    CenterEnsureRequest,
    CleanSymlinksRequest,
    PoolLayoutActivateRequest as PoolLayoutActivateCommand,
    PoolLayoutRollbackRequest as PoolLayoutRollbackCommand,
    PoolLayoutProbeRequest as PoolLayoutProbeCommand,
    PoolMappingSourceLayout,
    PoolQuarantineCleanupRequest as PoolQuarantineCleanupCommand,
    SymlinkItem,
    SyncBindPathsRequest,
    SyncSymlinksRequest,
)

router = APIRouter(prefix="/api/skills", tags=["skills"])
log = logging.getLogger("api-skills")


def _skills_plugin():
    from engine.community.manager import EngineManager
    return EngineManager.get_instance().skills


@router.post("/layout/probe", response_model=RuntimeLayoutProbeApiResponse)
async def probe_runtime_skills_layout(
    body: RuntimeLayoutProbeRequest,
) -> RuntimeLayoutProbeApiResponse:
    """通过 Skills Service API 核验当前运行时事实。"""
    plugin = _skills_plugin()
    try:
        result = await plugin.probe_pool_layout(
            PoolLayoutProbeCommand(
                engine=body.engine,
                layout_contract_version=body.layout_contract_version,
            )
        )
    except CapabilityNotSupportedError as error:
        raise HTTPException(status_code=501, detail=str(error)) from error
    return RuntimeLayoutProbeApiResponse(
        success=True,
        data=RuntimeLayoutProbeResponse.model_validate(result.to_data()),
        message="运行时 Skills Pool 布局探测完成",
    )


@router.post(
    "/layout/activate",
    response_model=PoolLayoutActivateApiResponse,
)
async def activate_runtime_skills_layout(
    body: PoolLayoutActivateRequest,
) -> PoolLayoutActivateApiResponse:
    """在当前容器的持久化文件系统上提交对应引擎的 Pool 数据面。"""

    plugin = _skills_plugin()
    try:
        result = await plugin.activate_pool_layout(
            PoolLayoutActivateCommand(
                migration_generation=body.migration_generation,
                preparation_id=body.preparation_id,
                registered_local_names=body.registered_local_names,
                mappings=[
                    SymlinkItem(source=item.source, target=item.target)
                    for item in body.mappings
                ],
            )
        )
    except CapabilityNotSupportedError as error:
        raise HTTPException(status_code=501, detail=str(error)) from error
    return PoolLayoutActivateApiResponse(
        success=result.committed,
        data=PoolLayoutActivateResponse.model_validate(result.to_data()),
        message=(
            "Skills Pool 数据面已提交"
            if result.committed
            else "Skills Pool 数据面未提交"
        ),
    )


@router.post(
    "/layout/rollback",
    response_model=PoolLayoutActivateApiResponse,
)
async def rollback_runtime_skills_layout(
    body: PoolLayoutRollbackRequest,
) -> PoolLayoutActivateApiResponse:
    """从当前权威 Pool 内容重建 Legacy，并原子切换 local bridge。"""

    plugin = _skills_plugin()
    try:
        result = await plugin.rollback_pool_layout(
            PoolLayoutRollbackCommand(
                rollback_generation=body.rollback_generation,
                registered_local_names=body.registered_local_names,
            )
        )
    except CapabilityNotSupportedError as error:
        raise HTTPException(status_code=501, detail=str(error)) from error
    return PoolLayoutActivateApiResponse(
        success=result.committed,
        data=PoolLayoutActivateResponse.model_validate(result.to_data()),
        message=(
            "Skills Pool 已显式回滚至 Legacy"
            if result.committed
            else "Skills Pool 显式回滚未提交"
        ),
    )


@router.post("/layout/quarantine/cleanup", response_model=ApiResponse)
async def cleanup_runtime_skills_quarantine(
    body: PoolQuarantineCleanupRequest,
) -> ApiResponse:
    """幂等清理固定 Pool 根下的一个 migration generation。"""
    plugin = _skills_plugin()
    try:
        result = await plugin.cleanup_pool_quarantine(
            PoolQuarantineCleanupCommand(
                migration_generation=body.migration_generation,
            )
        )
    except CapabilityNotSupportedError as error:
        raise HTTPException(status_code=501, detail=str(error)) from error
    return ApiResponse(
        success=result.status in {"CLEANED", "ALREADY_ABSENT"},
        data=result.to_data(),
        message="Migration Quarantine 清理完成",
    )


@router.post("/layout/mappings/verify", response_model=ApiResponse)
async def verify_runtime_skill_mappings(
    body: PoolMappingVerifyRequest,
) -> ApiResponse:
    """验证当前受管入口与 Pool source 一致。"""

    plugin = _skills_plugin()
    layout_kwargs = (
        {"source_layout": PoolMappingSourceLayout.LEGACY}
        if body.source_layout == PoolMappingSourceLayout.LEGACY.value
        else {}
    )
    try:
        result = await plugin.verify_pool_mappings(
            [
                SymlinkItem(source=item.source, target=item.target)
                for item in body.mappings
            ],
            **layout_kwargs,
        )
    except CapabilityNotSupportedError as error:
        raise HTTPException(status_code=501, detail=str(error)) from error
    return ApiResponse(
        success=result.valid,
        data=result.to_data(),
        message="Skill mapping 验证完成",
    )


@router.post("/layout/mappings/publish", response_model=ApiResponse)
async def publish_runtime_skill_mappings(
    body: PoolMappingVerifyRequest,
) -> ApiResponse:
    """全量发布 Pool 受管 mapping，保留结构桥和外部入口。"""

    plugin = _skills_plugin()
    layout_kwargs = (
        {"source_layout": PoolMappingSourceLayout.LEGACY}
        if body.source_layout == PoolMappingSourceLayout.LEGACY.value
        else {}
    )
    try:
        result = await plugin.publish_pool_mappings(
            [
                SymlinkItem(source=item.source, target=item.target)
                for item in body.mappings
            ],
            **layout_kwargs,
        )
    except CapabilityNotSupportedError as error:
        raise HTTPException(status_code=501, detail=str(error)) from error
    return ApiResponse(
        success=result.published,
        data=result.to_data(),
        message="Skill mapping 发布完成",
    )


@router.post("/symlink", response_model=ApiResponse)
async def sync_symlinks(body: SyncSymlinkRequest) -> ApiResponse:
    warning = check_capability(Capability.SKILLS_SYNC_SYMLINKS)
    items = [SymlinkItem(source=i.source, target=i.target) for i in (body.symlinks or [])]
    try:
        plugin = _skills_plugin()
        result = await plugin.sync_symlinks(SyncSymlinksRequest(symlinks=items))
    except CapabilityNotSupportedError as e:
        raise HTTPException(status_code=501, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except RuntimeError as e:  # SkillsConflictError subclass + base 目录无效
        raise HTTPException(status_code=409, detail=str(e)) from e

    return ApiResponse(
        success=True,
        data={
            "base_dir": result.base_dir,
            "total": result.total,
            "created": result.created,
            "updated": result.updated,
            "kept": result.kept,
            "removed": result.removed,
        },
        message="同步成功",
        warning=warning,
    )


@router.post("/symlink/bindpath", response_model=ApiResponse)
async def sync_bindpath_symlinks(body: BindPathRequest) -> ApiResponse:
    warning = check_capability(Capability.SKILLS_SYNC_BINDPATHS)
    items = [SymlinkItem(source=i.source, target=i.target) for i in body.symlinks]
    try:
        plugin = _skills_plugin()
        result = await plugin.sync_bindpaths(
            SyncBindPathsRequest(
                symlinks=items,
                clean_target_dir=body.clean_target_dir,
            )
        )
    except CapabilityNotSupportedError as e:
        raise HTTPException(status_code=501, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e

    return ApiResponse(
        success=True,
        data={
            "total": result.total,
            "created": result.created,
            "updated": result.updated,
            "kept": result.kept,
            "removed": result.removed,
        },
        message="同步成功",
        warning=warning,
    )


@router.post("/symlink/clean", response_model=ApiResponse)
async def clean_symlinks(body: CleanSymlinkRequest) -> ApiResponse:
    warning = check_capability(Capability.SKILLS_CLEAN_SYMLINKS)
    try:
        plugin = _skills_plugin()
        result = await plugin.clean_symlinks(
            CleanSymlinksRequest(directories=list(body.directories or []))
        )
    except CapabilityNotSupportedError as e:
        raise HTTPException(status_code=501, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    return ApiResponse(
        success=True,
        data={
            "directories_scanned": result.directories_scanned,
            "removed": result.removed,
        },
        message="清理成功",
        warning=warning,
    )


@router.post("/center/ensure", response_model=ApiResponse)
async def ensure_center_skills(body: CenterEnsureRequestSchema) -> ApiResponse:
    warning = check_capability(Capability.SKILLS_CENTER_ENSURE)
    items = [CenterEnsureItem(skill_uuid=i.skill_uuid, version=i.version) for i in body.items]
    try:
        plugin = _skills_plugin()
        result = await plugin.ensure_center_skills(CenterEnsureRequest(items=items))
    except CapabilityNotSupportedError as e:
        raise HTTPException(status_code=501, detail=str(e)) from e

    return ApiResponse(
        success=True,
        data={
            "ok": [{"skill_uuid": x.skill_uuid, "version": x.version} for x in result.ok],
            "failed": [
                {"skill_uuid": x.skill_uuid, "version": x.version, "reason": x.reason}
                for x in result.failed
            ],
        },
        message="ensure 成功",
        warning=warning,
    )
