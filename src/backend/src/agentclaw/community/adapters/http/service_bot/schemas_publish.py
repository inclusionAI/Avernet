"""Bot 发布流程 API 数据模型。"""
from typing import Any, Dict, Literal, Optional
from pydantic import BaseModel, Field


class PublishFlowRequest(BaseModel):
    """发布流程推进请求."""
    publish_id: int = Field(..., description="发布单 ID")
    device_count: int = Field(default=1, ge=1, description="设备数量")


class PublishFlowResult(BaseModel):
    """发布流程结果."""
    publish_id: int = Field(..., description="发布单 ID")
    status: str = Field(..., description="当前状态")
    message: str = Field(..., description="状态消息")
    bot_uuid: Optional[str] = Field(default=None, description="BaaS 层 Bot UUID")
    baas_publish_id: Optional[str] = Field(default=None, description="BaaS 层发布 ID")
    device_binding_id: Optional[int] = Field(default=None, description="设备绑定记录 ID")
    data: Optional[Dict[str, Any]] = Field(default=None, description="附加数据")


class CreateFirstPublishRequest(BaseModel):
    """创建首个发布单请求."""
    bot_id: str = Field(..., description="Bot ID")
    name: str = Field(..., description="发布单名称")
    permission_owner: str = Field(default="owner", description="权限归属")
    description: Optional[str] = Field(default=None, description="描述")


class UpgradePublishRequest(BaseModel):
    """升级发布单请求."""
    publish_id: int = Field(..., description="原发布单 ID")


class UpdatePublishStatusRequest(BaseModel):
    """更新发布单状态请求."""
    target_status: str = Field(..., description="目标状态")
    source_status: str = Field(..., description="源状态，用于乐观锁校验")
    failed_status: str = Field(..., description="失败时的状态")


class UpgradeBotTypeRequest(BaseModel):
    """升级 Bot 类型请求（个人bot升级为服务bot）。"""

    bot_id: str = Field(..., description="Bot ID")


class UpgradeBotTypeForOthersRequest(UpgradeBotTypeRequest):
    """升级 Bot 类型请求（使用指定 owner_id）。"""

    owner_id: str = Field(..., description="Bot 拥有者 ID")


class UpdateBotTypeRequest(BaseModel):
    """更新 Bot 类型请求。"""

    bot_id: str = Field(..., description="Bot ID")
    bot_type: str = Field(..., description="Bot 类型: personal 或 service")


class UpdateBotTypeForOthersRequest(UpdateBotTypeRequest):
    """更新 Bot 类型请求（使用指定 owner_id）。"""

    owner_id: str = Field(..., description="Bot 拥有者 ID")


class CheckApprovalRequest(BaseModel):
    """检查审批状态请求."""

    action: Literal["online", "offline"] = Field(
        ..., description="审批类型: online=上线审批, offline=下线审批"
    )


class UpdateServiceBotConfigRequest(BaseModel):
    """更新服务 Bot 配置请求。"""

    bot_id: str = Field(..., description="Bot ID")
    owner_id: str = Field(..., description="Bot 拥有者 ID")
    config_update: Dict[str, Any] = Field(..., description="服务 Bot 配置更新内容")
