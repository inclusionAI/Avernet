"""
Channel API Schemas
Pydantic Request/Response models for channel endpoints.
"""
from typing import Optional
from pydantic import BaseModel, Field


class ChannelConfig(BaseModel):
    """渠道配置详情"""
    # 前端传递的字段
    client_id: str
    client_secret: Optional[str] = Field(None, description="秘钥")
    card_template_id: Optional[str] = Field(None, description="卡片模板ID")
    card_template_key: Optional[str] = Field(None, description="卡片模板Key")
    enable_streaming_cards: bool = Field(False, description="是否启用流式卡片")
    # 后端默认字段
    dm_policy: str = Field(default="open", description="私信策略: open=开放, disabled=关闭")
    allowlist: list[str] = Field(default_factory=lambda: ["*"], description="允许列表: [*]=允许所有, 或指定staff_id列表")
    reply_to_message: bool = Field(default=True, description="是否回复原消息")
    robot_code: str = Field(default="", description="机器人编码")
    aix_enable: bool = Field(default=True, description="是否启用AIX")
    aix_preview_url: str = Field(
        default="",
        description="AIX预览URL（空时由路由层用 AixConfig.preview_url 补全，社区构建默认空）",
    )
    include_sender_name: bool = Field(default=True, description="是否包含发送者名称")


class CreateChannelRequest(BaseModel):
    """创建渠道配置请求"""
    type: str = Field(..., description="渠道类型: dingding")
    description: Optional[str] = Field(None, description="描述")
    identity_id: str = Field(..., description="用户ID")
    bind_bot_id: str = Field(..., description="绑定bot ID")
    config: ChannelConfig = Field(..., description="配置详情")
    stage: Optional[str] = Field(None, description="配置阶段: draft(草稿)/verify(验证中)/online(已上线)，可选，默认空")


class CreateChannelResponse(BaseModel):
    """创建渠道配置响应"""
    success: bool
    data: dict
    message: str


class ChannelListRequest(BaseModel):
    """查询渠道列表请求"""
    type: str = Field(..., description="渠道类型: dingding")
    identity_id: str = Field(..., description="用户ID")
    bind_bot_id: str = Field(..., description="绑定bot ID")


class ChannelResponse(BaseModel):
    """渠道配置响应"""
    id: int
    type: str
    description: Optional[str]
    identity_id: str
    bind_bot_id: str
    config: dict
    status: str
    gmt_create: Optional[str]
    gmt_modified: Optional[str]
    stage: Optional[str] = None


class ChannelListResponse(BaseModel):
    """查询渠道列表响应"""
    success: bool
    data: list[ChannelResponse]
    message: str


class UpdateStatusResponse(BaseModel):
    """更新状态响应"""
    success: bool
    message: str


class UpdateChannelRequest(BaseModel):
    """更新渠道配置请求"""
    type: str = Field(..., description="渠道类型: dingding")
    description: Optional[str] = Field(None, description="描述")
    identity_id: str = Field(..., description="用户ID")
    bind_bot_id: str = Field(..., description="绑定bot ID")
    config: ChannelConfig = Field(..., description="配置详情")
    status: str = Field("0", description="状态: 0=未生效, 1=生效")
    stage: Optional[str] = Field(None, description="配置阶段: draft(草稿)/verify(验证中)/online(已上线)，可选，默认空")


class OpenClawConfigsResponse(BaseModel):
    """多环境 OpenClaw 配置响应"""
    verify: str = Field(..., description="openclaw_verify.json 内容")
    online: str = Field(..., description="openclaw_online.json 内容")
    eval: str = Field(..., description="openclaw_eval.json 内容，钉钉渠道已禁用")
    success: bool = Field(True, description="请求是否成功")
    message: str = Field("生成成功", description="响应消息")
