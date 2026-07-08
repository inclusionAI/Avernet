"""Bot 发布流程数据模型。

用于发布流程的请求和响应模型定义。
"""
from typing import Any, Dict, Optional
from pydantic import BaseModel, Field


class PublishFlowRequest(BaseModel):
    """发布流程推进请求."""
    publish_id: int = Field(..., description="发布单 ID")
    operator: str = Field(..., description="操作者 ID")
    device_count: int = Field(default=1, ge=1, description="设备数量")
    publish_stage: str = Field(default="verify", description="发布阶段: verify(验证) 或 online(上线)")


class BuildResult(BaseModel):
    """构建结果."""
    success: bool = Field(..., description="构建是否成功")
    bot_id: str = Field(..., description="Bot ID")
    entity_id: str = Field(..., description="实体 ID")
    entity_type: str = Field(..., description="实体类型")
    version: str = Field(..., description="版本号")
    migration_path: str = Field(..., description="构建产物目录")


class ReleaseResult(BaseModel):
    """发布结果."""
    success: bool = Field(..., description="发布是否成功")
    bot_uuid: Optional[str] = Field(default=None, description="BaaS 层返回的 Bot UUID")
    device_binding_id: Optional[int] = Field(default=None, description="设备绑定记录 ID")
    message: Optional[str] = Field(default=None, description="消息")


class PublishFlowResult(BaseModel):
    """发布流程结果."""
    publish_id: int = Field(..., description="发布单 ID")
    status: str = Field(..., description="当前状态")
    message: str = Field(..., description="状态消息")
    action: Optional[str] = Field(default=None, description="当前执行的操作类型，如 process/restart/rollback")
    bot_uuid: Optional[str] = Field(default=None, description="BaaS 层 Bot UUID")
    baas_publish_id: Optional[str] = Field(default=None, description="BaaS 层发布 ID")
    device_binding_id: Optional[int] = Field(default=None, description="设备绑定记录 ID")
    data: Optional[Dict[str, Any]] = Field(default=None, description="附加数据")
    target_publish_id: Optional[int] = Field(default=None, description="回滚目标发布单 ID（仅回滚操作）")
    approval: Optional[Dict[str, Any]] = Field(
        default=None,
        description="审批信息，包含 puid、action、operator_id、status、owner_id、approval_url、created_at、processed_at"
    )


class RollbackRequest(BaseModel):
    """回滚请求."""
    reason: Optional[str] = Field(default=None, description="回滚原因")


class RollbackResult(BaseModel):
    """回滚结果."""
    rolled_back_publish_id: int = Field(..., description="被回滚的版本 ID")
    rolled_back_status: str = Field(default="draft", description="被回滚版本的状态")
    target_publish_id: int = Field(..., description="回滚目标版本 ID")
    target_version: int = Field(..., description="目标版本号")
    target_status: str = Field(default="success", description="目标版本状态")


class CanRollbackResult(BaseModel):
    """检查回滚结果."""
    can_rollback: bool = Field(..., description="是否可以回滚")
    reason: Optional[str] = Field(default=None, description="不能回滚的原因")
    target_publish_id: Optional[int] = Field(default=None, description="目标版本 ID")
    target_version: Optional[int] = Field(default=None, description="目标版本号")
    rollback_restored_from: Optional[int] = Field(default=None, description="当前版本是否通过回滚恢复（值为回滚发起者ID）")
    # 版本链延伸检查
    next_publish_id: Optional[int] = Field(default=None, description="基于当前版本的新版本 ID（如有）")
    next_version: Optional[int] = Field(default=None, description="新版本号")
    next_status: Optional[str] = Field(default=None, description="新版本状态")
