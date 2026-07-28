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


class InFlightOperation(BaseModel):
    """正在执行中的 BaaS 操作（重启/升级/扩缩容等）快照。

    来源是 ``ac_publish_operation`` 台账：意图在调用 BaaS **之前**落库，只有到达
    终态（completed/failed/abandoned）才离开 ``pending``/``id_recorded``。因此一条
    非终态记录就是"这张发布单当前是否有操作在跑"的持久答案。

    前端需要它来在**页面加载时**恢复禁用态：重启期间容器被销毁重建，此时
    重启/升级/下线都不该可点，但前端自己的"进行中"标记只存在于页面内存里，
    刷新即丢失，导致按钮重新可点、请求打到一个正在重建的 Bot 上。
    """
    kind: str = Field(..., description="PublishOperationKind 值，如 restart/upgrade")
    stage: str = Field(..., description="发布阶段: verify / online / eval / 空")
    state: str = Field(..., description="PublishOperationState 值: pending / id_recorded")
    started_at: str = Field(..., description="操作开始时间(ISO8601)，即台账行创建时间")
    baas_publish_id: Optional[int] = Field(default=None, description="BaaS 工作流 ID；到达 id_recorded 后才有")


class PublishFlowResult(BaseModel):
    """发布流程结果."""
    publish_id: int = Field(..., description="发布单 ID")
    status: str = Field(..., description="当前状态")
    message: str = Field(..., description="状态消息")
    action: Optional[str] = Field(default=None, description="当前执行的操作类型，如 process/restart/rollback")
    in_flight: Optional[InFlightOperation] = Field(
        default=None,
        description="当前正在执行的 BaaS 操作；None 表示没有操作在跑（此时操作按钮可用）"
    )
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
