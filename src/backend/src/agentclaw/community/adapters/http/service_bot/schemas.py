"""Service Bot schemas (Pydantic models).

All request/response models for service_bot API.
"""
from typing import Any, Optional
from pydantic import BaseModel, Field

from agentclaw.community.core.service_bot.types import PublishStage


class ApiResponse(BaseModel):
    """Unified API response format."""
    success: bool
    message: str = "OK"
    error_code: int = 200
    data: Optional[Any] = None


class BotBuildRequest(BaseModel):
    """Bot build request model - 用于 Bot 构建迁移参数."""
    bot_id: str = Field(..., description="Bot ID")
    version: str = Field(default="v1", description="迁移版本号")
    device_count: int = Field(default=1, ge=1, description="设备数量")
    publish_stage: PublishStage = Field(
        default=PublishStage.VERIFY,
        description="发布推进阶段：verify(验证阶段) 或 release(发布上线阶段)"
    )


class ReadOnlyRuleItem(BaseModel):
    """只读规则条目。"""
    path: str = Field(..., description="只读路径")
    rule_type: str = Field(..., description="路径类型: file/directory/glob")


class ReadOnlyTreeItem(BaseModel):
    """目录树中单个文件/目录条目。"""
    name: str = Field(..., description="文件或目录名")
    path: str = Field(..., description="相对路径（相对于 base_path）")
    is_dir: bool = Field(False, description="是否为目录")
    is_readonly_default: bool = Field(False, description="是否匹配默认只读规则")
    is_readonly_custom: bool = Field(False, description="是否匹配用户自定义只读规则")


class ReadOnlyTreeResponse(BaseModel):
    """目录树查询响应。"""
    base_path: str = Field(..., description="查询的根路径")
    items: list[ReadOnlyTreeItem] = Field(default_factory=list, description="目录树条目列表")
    default_rules: list[ReadOnlyRuleItem] = Field(default_factory=list, description="默认只读规则列表")
    custom_rules: list[ReadOnlyRuleItem] = Field(default_factory=list, description="用户自定义只读规则列表")
