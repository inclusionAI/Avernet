"""Bot 协作者 API schemas (Pydantic models).

All request/response models for bot_collaborator API.
"""
from datetime import datetime
from typing import Any, List, Optional

from pydantic import BaseModel, Field


class ApiResponse(BaseModel):
    """Unified API response format."""
    success: bool
    message: str = "OK"
    error_code: int = 200
    data: Optional[Any] = None


# ============================================================================
# 协作者相关
# ============================================================================
class AddCollaboratorRequest(BaseModel):
    """添加协作者请求。"""
    bot_id: str = Field(..., description="Bot ID")
    owner_id: str = Field(..., description="Bot 拥有者工号")
    user_id: str = Field(..., description="协作者工号")
    user_name: Optional[str] = Field(default=None, description="协作者花名")
    role: str = Field(default="admin", description="角色：admin/member")


class AddCollaboratorResponse(BaseModel):
    """添加协作者响应。"""
    id: int = Field(..., description="协作者记录 ID")
    bot_pk: int = Field(..., description="Bot 主键")
    bot_id: str = Field(..., description="Bot ID")
    owner_id: str = Field(..., description="Bot 拥有者工号")
    user_id: str = Field(..., description="协作者工号")
    user_name: Optional[str] = Field(default=None, description="协作者花名")
    role: str = Field(..., description="角色")
    operator_id: str = Field(..., description="操作者工号")
    gmt_create: datetime = Field(..., description="创建时间")


class CollaboratorInfo(BaseModel):
    """协作者信息。"""
    id: int = Field(..., description="协作者记录 ID")
    bot_pk: int = Field(..., description="Bot 主键")
    bot_id: str = Field(..., description="Bot ID")
    owner_id: str = Field(..., description="Bot 拥有者工号")
    user_id: str = Field(..., description="协作者工号")
    user_name: Optional[str] = Field(default=None, description="协作者花名")
    role: str = Field(..., description="角色")
    operator_id: str = Field(..., description="操作者工号")
    gmt_create: datetime = Field(..., description="创建时间")
    gmt_modified: datetime = Field(..., description="修改时间")


class ListCollaboratorsRequest(BaseModel):
    """获取协作者列表请求。"""
    bot_id: str = Field(..., description="Bot ID")
    owner_id: str = Field(..., description="Bot 拥有者工号")
    role: Optional[str] = Field(default=None, description="角色过滤（admin/member）")


class ListCollaboratorsResponse(BaseModel):
    """获取协作者列表响应。"""
    collaborators: List[CollaboratorInfo] = Field(default_factory=list, description="协作者列表")


class UpdateCollaboratorRequest(BaseModel):
    """更新协作者请求。"""
    id: int = Field(..., description="协作者记录 ID")
    user_id: Optional[str] = Field(default=None, description="协作者工号")
    user_name: Optional[str] = Field(default=None, description="协作者花名")
    role: Optional[str] = Field(default=None, description="角色：admin/member")


class UpdateCollaboratorResponse(BaseModel):
    """更新协作者响应。"""
    id: int = Field(..., description="协作者记录 ID")
    bot_pk: int = Field(..., description="Bot 主键")
    bot_id: str = Field(..., description="Bot ID")
    owner_id: str = Field(..., description="Bot 拥有者工号")
    user_id: str = Field(..., description="协作者工号")
    user_name: Optional[str] = Field(default=None, description="协作者花名")
    role: str = Field(..., description="角色")
    operator_id: str = Field(..., description="操作者工号")
    gmt_create: datetime = Field(..., description="创建时间")
    gmt_modified: datetime = Field(..., description="修改时间")


class RemoveCollaboratorRequest(BaseModel):
    """移除协作者请求。"""
    id: int = Field(..., description="协作者记录 ID")


class RemoveCollaboratorResponse(BaseModel):
    """移除协作者响应。"""
    deleted: bool = Field(..., description="是否成功删除")


class CheckPermissionRequest(BaseModel):
    """检查权限请求。"""
    bot_id: str = Field(..., description="Bot ID")
    owner_id: str = Field(..., description="Bot 拥有者工号")
    user_id: Optional[str] = Field(default=None, description="要检查的用户工号，不传则使用当前登录用户")
    required_level: str = Field(default="ADMIN", description="需要的权限级别：NONE/MEMBER/ADMIN/OWNER")


class CheckPermissionResponse(BaseModel):
    """检查权限响应。"""
    has_permission: bool = Field(..., description="是否有权限")
    level: str = Field(..., description="权限级别名称")
    level_value: int = Field(..., description="权限级别数值")


# ============================================================================
# 协作锁相关
# ============================================================================
class AcquireLockRequest(BaseModel):
    """获取协作锁请求。"""
    bot_id: str = Field(..., description="Bot ID")
    owner_id: str = Field(..., description="Bot 拥有者工号")


class LockInfo(BaseModel):
    """锁信息。"""
    id: int = Field(..., description="锁记录 ID")
    lock_key: str = Field(..., description="锁键")
    holder_user_id: str = Field(..., description="持锁者工号")
    holder_name: Optional[str] = Field(default=None, description="持锁者花名")
    gmt_create: datetime = Field(..., description="获取锁时间")


class AcquireLockResponse(BaseModel):
    """获取协作锁响应。"""
    acquired: bool = Field(..., description="是否成功获取锁")
    lock: Optional[LockInfo] = Field(default=None, description="锁信息，获取成功时返回")


class ReleaseLockRequest(BaseModel):
    """释放协作锁请求。"""
    bot_id: str = Field(..., description="Bot ID")
    owner_id: str = Field(..., description="Bot 拥有者工号")
    force: bool = Field(default=False, description="是否强制释放（忽略持有者检查）")


class ReleaseLockResponse(BaseModel):
    """释放协作锁响应。"""
    released: bool = Field(..., description="是否成功释放锁")


class GetLockInfoRequest(BaseModel):
    """获取锁信息请求。"""
    bot_id: str = Field(..., description="Bot ID")
    owner_id: str = Field(..., description="Bot 拥有者工号")


class GetLockInfoResponse(BaseModel):
    """获取锁信息响应。"""
    locked: bool = Field(..., description="是否被锁定")
    lock: Optional[LockInfo] = Field(default=None, description="锁信息，被锁定时返回")
    has_collaborators: bool = Field(default=False, description="Bot 是否有协作者")
    is_owner: bool = Field(default=False, description="锁持有者是否是 Bot owner")
    need_lock: bool = Field(default=True, description="是否需要获取锁才能编辑")


class StealLockRequest(BaseModel):
    """抢锁请求。"""
    bot_id: str = Field(..., description="Bot ID")
    owner_id: str = Field(..., description="Bot 拥有者工号")


class StealLockResponse(BaseModel):
    """抢锁响应。"""
    stolen: bool = Field(..., description="是否抢锁成功")
    lock: Optional[LockInfo] = Field(default=None, description="锁信息")
    previous_holder: Optional[str] = Field(default=None, description="原锁持有者工号")


# ============================================================================
# 会话级锁（coding 应用）— key: session:{bot_id}:{owner_id}:{session_id}
# ============================================================================
class SessionLockRequest(BaseModel):
    """会话锁请求（acquire / steal）。"""
    bot_id: str = Field(..., description="Bot ID（应用 ID）")
    owner_id: str = Field(..., description="应用 owner 工号")
    session_id: str = Field(..., description="会话 ID")


class SessionReleaseLockRequest(BaseModel):
    """释放会话锁请求。"""
    bot_id: str = Field(..., description="Bot ID（应用 ID）")
    owner_id: str = Field(..., description="应用 owner 工号")
    session_id: str = Field(..., description="会话 ID")
    force: bool = Field(default=False, description="是否强制释放")


class SessionLockResponse(BaseModel):
    """会话锁 acquire/steal 响应。"""
    acquired: bool = Field(..., description="是否持有锁（acquire 成功或重入；steal 恒为 true）")
    lock: Optional[LockInfo] = Field(default=None, description="锁信息")


class SessionLockInfoResponse(BaseModel):
    """会话锁状态响应。"""
    locked: bool = Field(default=False, description="是否被锁定")
    lock: Optional[LockInfo] = Field(default=None, description="锁信息，被锁定时返回")
    is_mine: bool = Field(default=False, description="当前用户是否持有该锁")
