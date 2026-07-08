"""OSS → NAS 迁移运维操作接口。

HTTP 层：仅包含 Pydantic schemas 和 FastAPI router 端点。
业务编排逻辑见：core/devices/services/oss_to_nas_switch_service.py
数据访问层见：core/devices/repository/oss_to_nas_record_repo.py
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from agentclaw.community.adapters.http.auth.models import AuthenticatedUser
from agentclaw.community.adapters.http.auth.dependencies import require_operator
from agentclaw.community.api.oss_to_nas_switch_service import OssToNasSwitchServiceProtocol
from agentclaw.community.core.devices.repository.protocol import OssToNasRecordRepository
from agentclaw.community.di import Injected
from agentclaw.community.log import get_logger

logger = get_logger()


# =============================================================================
# Pydantic Schemas
# =============================================================================

class ApiResponse(BaseModel):
    success: bool
    message: str = ""
    error_code: int = 200
    data: Any = None


class InsertRecordRequest(BaseModel):
    staff_no: str = Field(..., description="用户工号")
    bot_id: str = Field(..., description="Bot ID")
    env: str = Field(..., description="环境：pre / prod")
    batch_no: str = Field(..., description="批次号")
    sub_batch_no: str = Field(..., description="子批次号")
    bot_info: dict = Field(default_factory=dict, description="Bot 相关信息，如 storage_dir_name")
    storage_status: str = Field("oss", description="初始存储状态，默认 oss")


class BatchSwitchRequest(BaseModel):
    env: str = Field(..., description="环境：pre / prod")
    batch_no: str = Field(..., description="批次号")
    sub_batch_no: str = Field(..., description="子批次号")
    concurrency: int = Field(..., ge=1, description="并发数")
    force: bool = Field(False, description="强制执行，不检查 storage_status")


class SingleSwitchRequest(BaseModel):
    env: str = Field(..., description="环境")
    staff_no: str = Field(..., description="用户工号")
    bot_id: str = Field(..., description="Bot ID")


class BatchRollbackRequest(BaseModel):
    env: str = Field(..., description="环境")
    batch_no: str = Field(..., description="批次号")
    sub_batch_no: str = Field(..., description="子批次号")


class SingleRollbackRequest(BaseModel):
    env: str = Field(..., description="环境")
    staff_no: str = Field(..., description="用户工号")
    bot_id: str = Field(..., description="Bot ID")


class GetRecordRequest(BaseModel):
    staff_no: str = Field(..., description="用户工号")
    bot_id: str = Field(..., description="Bot ID")
    env: str | None = Field(None, description="环境：pre / prod，不传则自动获取当前环境")


class UpdateRecordRequest(BaseModel):
    staff_no: str = Field(..., description="用户工号")
    bot_id: str = Field(..., description="Bot ID")
    env: str | None = Field(None, description="环境：pre / prod")
    batch_no: str | None = Field(None, description="批次号")
    sub_batch_no: str | None = Field(None, description="子批次号")
    bot_info: dict | None = Field(None, description="Bot 相关信息")
    storage_status: str | None = Field(None, description="存储状态")


class DeleteRecordRequest(BaseModel):
    staff_no: str = Field(..., description="用户工号")
    bot_id: str = Field(..., description="Bot ID")
    env: str | None = Field(None, description="环境：pre / prod，不传则自动获取当前环境")


# =============================================================================
# Router
# =============================================================================

router = APIRouter(prefix="/api/ops/oss-to-nas", tags=["oss-to-nas-migration"])


@router.post("/get-record", response_model=ApiResponse)
async def get_record(
    req: GetRecordRequest,
    record_repo: OssToNasRecordRepository = Injected(OssToNasRecordRepository),
) -> ApiResponse:
    """按 staff_no + bot_id 查询单条迁移记录。"""
    try:
        record = record_repo.get_record(req.staff_no, req.bot_id, env=req.env)
        if not record:
            return ApiResponse(success=False, message=f"记录不存在: {req.staff_no}/{req.bot_id}", error_code=404)
        return ApiResponse(success=True, message="查询成功", data=record)
    except Exception as e:
        logger.error(f"[oss_to_nas] get-record failed: {e}")
        return ApiResponse(success=False, message=str(e), error_code=500)


@router.post("/insert-record", response_model=ApiResponse)
async def insert_record(
    req: InsertRecordRequest,
    record_repo: OssToNasRecordRepository = Injected(OssToNasRecordRepository),
) -> ApiResponse:
    """插入一条迁移记录到 ac_oss_to_nas_record 表。"""
    try:
        record = record_repo.insert_record(
            staff_no=req.staff_no,
            bot_id=req.bot_id,
            env=req.env,
            batch_no=req.batch_no,
            sub_batch_no=req.sub_batch_no,
            bot_info=req.bot_info or None,
            storage_status=req.storage_status,
        )
        return ApiResponse(success=True, message="记录插入成功", data=record)
    except Exception as e:
        logger.error(f"[oss_to_nas] insert-record failed: {e}")
        return ApiResponse(success=False, message=str(e), error_code=500)


@router.post("/batch-switch", response_model=ApiResponse)
async def batch_switch(
    req: BatchSwitchRequest,
    switch_service: OssToNasSwitchServiceProtocol = Injected(OssToNasSwitchServiceProtocol),
    record_repo: OssToNasRecordRepository = Injected(OssToNasRecordRepository),
) -> ApiResponse:
    """批量切换：按批次号将 bot 从 OSS 迁移到 NAS。"""
    try:
        status_filter = None if req.force else "oss"
        records = record_repo.query_records_by_batch(req.env, req.batch_no, req.sub_batch_no, status_filter)

        if not records:
            return ApiResponse(success=True, message="没有需要切换的记录", data={"total": 0})

        result = await switch_service.batch_switch_with_concurrency(records, req.concurrency, env=req.env)
        return ApiResponse(success=True, message="批量切换完成", data=result)

    except Exception as e:
        logger.error(f"[oss_to_nas] batch-switch failed: {e}")
        return ApiResponse(success=False, message=str(e), error_code=500)


@router.post("/single-switch", response_model=ApiResponse)
async def single_switch(
    req: SingleSwitchRequest,
    _user: AuthenticatedUser = Depends(require_operator),
    switch_service: OssToNasSwitchServiceProtocol = Injected(OssToNasSwitchServiceProtocol),
    record_repo: OssToNasRecordRepository = Injected(OssToNasRecordRepository),
) -> ApiResponse:
    """单个切换：强制执行（不检查 storage_status）。需操作员权限。"""
    try:
        record = record_repo.get_record(req.staff_no, req.bot_id, env=req.env)
        if not record:
            return ApiResponse(success=False, message=f"记录不存在: {req.staff_no}/{req.bot_id}", error_code=404)

        result = await switch_service.switch_one(req.staff_no, req.bot_id, env=req.env)
        return ApiResponse(success=result["status"] == "success", message="单个切换完成", data=result)

    except Exception as e:
        logger.error(f"[oss_to_nas] single-switch failed: {e}")
        return ApiResponse(success=False, message=str(e), error_code=500)


@router.post("/batch-rollback", response_model=ApiResponse)
async def batch_rollback(
    req: BatchRollbackRequest,
    record_repo: OssToNasRecordRepository = Injected(OssToNasRecordRepository),
) -> ApiResponse:
    """批量回滚：将批次记录的 storage_status 更新为 oss。"""
    try:
        count = record_repo.batch_update_status(req.env, req.batch_no, req.sub_batch_no, "oss")
        return ApiResponse(
            success=True,
            message=f"批量回滚完成，影响 {count} 条记录",
            data={"affected": count},
        )
    except Exception as e:
        logger.error(f"[oss_to_nas] batch-rollback failed: {e}")
        return ApiResponse(success=False, message=str(e), error_code=500)


@router.post("/single-rollback", response_model=ApiResponse)
async def single_rollback(
    req: SingleRollbackRequest,
    switch_service: OssToNasSwitchServiceProtocol = Injected(OssToNasSwitchServiceProtocol),
    record_repo: OssToNasRecordRepository = Injected(OssToNasRecordRepository),
) -> ApiResponse:
    """单个回滚：停止 bot，将 NAS 数据同步回 OSS，重启 bot，更新状态为 oss。"""
    try:
        record = record_repo.get_record(req.staff_no, req.bot_id, env=req.env)
        if not record:
            return ApiResponse(success=False, message=f"记录不存在: {req.staff_no}/{req.bot_id}", error_code=404)

        result = await switch_service.rollback_one(req.staff_no, req.bot_id, env=req.env)
        return ApiResponse(
            success=result["status"] == "success",
            message=f"回滚完成: {req.staff_no}/{req.bot_id}" if result["status"] == "success" else f"回滚失败: {result.get('error', '')}",
            data=result,
        )
    except Exception as e:
        logger.error(f"[oss_to_nas] single-rollback failed: {e}")
        return ApiResponse(success=False, message=str(e), error_code=500)


@router.post("/update-record", response_model=ApiResponse)
async def update_record(
    req: UpdateRecordRequest,
    record_repo: OssToNasRecordRepository = Injected(OssToNasRecordRepository),
) -> ApiResponse:
    """更新 ac_oss_to_nas_record 表中指定记录的字段。"""
    try:
        updates = req.model_dump(exclude={"staff_no", "bot_id"}, exclude_none=True)
        if not updates:
            return ApiResponse(success=False, message="没有提供需要更新的字段", error_code=400)
        record = record_repo.update_record(req.staff_no, req.bot_id, updates)
        return ApiResponse(success=True, message="记录更新成功", data=record)
    except Exception as e:
        logger.error(f"[oss_to_nas] update-record failed: {e}")
        return ApiResponse(success=False, message=str(e), error_code=500)


@router.post("/delete-record", response_model=ApiResponse)
async def delete_record(
    req: DeleteRecordRequest,
    record_repo: OssToNasRecordRepository = Injected(OssToNasRecordRepository),
) -> ApiResponse:
    """删除 ac_oss_to_nas_record 表中指定的迁移记录。"""
    try:
        deleted = record_repo.delete_record(req.staff_no, req.bot_id, env=req.env)
        if not deleted:
            return ApiResponse(success=False, message=f"记录不存在: {req.staff_no}/{req.bot_id}", error_code=404)
        return ApiResponse(
            success=True,
            message=f"记录删除成功: {req.staff_no}/{req.bot_id}",
            data={"staff_no": req.staff_no, "bot_id": req.bot_id},
        )
    except Exception as e:
        logger.error(f"[oss_to_nas] delete-record failed: {e}")
        return ApiResponse(success=False, message=str(e), error_code=500)
