"""
API Key 数据模型定义
"""

from dataclasses import dataclass
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from secbaas.api import ListResponse

from ._enums import APIKeyStatus

# ==================== Core Record ====================


@dataclass(slots=True)
class APIKeyRecord:
    """API Key 核心记录"""

    id: int
    gmt_create: datetime
    gmt_modified: datetime
    api_key_hash: str
    api_key_prefix: str
    key_name: str | None
    app_id: str
    app_type: str | None
    description: str | None
    rate_limit_rpm: int | None
    rate_limit_rpd: int | None
    status: str
    owner: str
    tenant: str | None
    env: str
    creator: str
    modifier: str | None
    policy: str | None


# ==================== Request Models ====================


class APIKeyCreate(BaseModel):
    """创建 API Key 请求"""

    app_id: str = Field(..., min_length=1, max_length=128, description="应用ID")
    app_type: str | None = Field(None, max_length=64, description="应用类型")
    key_name: str | None = Field(None, max_length=128, description="密钥名称")
    description: str | None = Field(None, description="描述")
    rate_limit_rpm: int | None = Field(None, description="每分钟请求数限制")
    rate_limit_rpd: int | None = Field(None, description="每天请求数限制")
    owner: str | None = Field(None, max_length=64, description="Owner")
    tenant: str | None = Field(None, max_length=64, description="租户标识")
    policy: str | None = Field(None, description="权限策略，JSON 格式")


class BotAPIKeyCreate(BaseModel):
    """创建 Bot API Key 请求（app_type 固定为 bot，无 policy 字段）

    owner 由服务端从 OperationContext 自动填充，无需客户端指定。
    app_id 格式必须为 real_bot_id:entity_id。
    """

    app_id: str = Field(
        ...,
        min_length=1,
        max_length=128,
        description="应用ID，格式为 real_bot_id:entity_id",
    )
    key_name: str | None = Field(None, max_length=128, description="密钥名称")
    description: str | None = Field(None, description="描述")
    rate_limit_rpm: int | None = Field(None, description="每分钟请求数限制")
    rate_limit_rpd: int | None = Field(None, description="每天请求数限制")
    tenant: str | None = Field("team_claw", max_length=64, description="租户标识")


class AppAPIKeyCreate(BaseModel):
    """创建 App API Key 请求（app_type 固定为 app）

    owner 由服务端从 OperationContext 自动填充，无需客户端指定。
    policy 默认不允许访问任何 bot (allowed_bots=[])，后续通过 allowed-bots API 授权。
    """

    app_id: str = Field(..., min_length=1, max_length=128, description="应用ID")
    key_name: str | None = Field(None, max_length=128, description="密钥名称")
    description: str | None = Field(None, description="描述")
    rate_limit_rpm: int | None = Field(None, description="每分钟请求数限制")
    rate_limit_rpd: int | None = Field(None, description="每天请求数限制")
    tenant: str | None = Field("team_claw", max_length=64, description="租户标识")


class APIKeyUpdate(BaseModel):
    """更新 API Key 请求"""

    key_name: str | None = Field(None, max_length=128, description="密钥名称")
    description: str | None = Field(None, description="描述")
    app_id: str | None = Field(None, max_length=128, description="应用ID")
    app_type: str | None = Field(None, max_length=64, description="应用类型")
    rate_limit_rpm: int | None = Field(None, description="每分钟请求数限制")
    rate_limit_rpd: int | None = Field(None, description="每天请求数限制")
    owner: str | None = Field(None, max_length=64, description="Owner")
    tenant: str | None = Field(None, max_length=64, description="租户标识")
    policy: str | None = Field(None, description="权限策略，JSON 格式")


# ==================== Response Models ====================


class APIKeyResponse(BaseModel):
    """API Key 响应"""

    id: int
    app_id: str
    app_type: str | None
    key_name: str | None
    api_key_prefix: str
    description: str | None
    rate_limit_rpm: int | None
    rate_limit_rpd: int | None
    status: str
    owner: str
    tenant: str | None
    env: str
    creator: str
    modifier: str | None
    policy: str | None
    gmt_create: datetime
    gmt_modified: datetime

    model_config = ConfigDict(from_attributes=True)


class APIKeyCreateResponse(APIKeyResponse):
    """创建 API Key 响应（包含明文密钥）"""

    api_key: str = Field(..., description="明文 API Key，仅创建时返回一次")


class APIKeyListResponse(ListResponse[APIKeyResponse]):
    """API Key 列表响应"""

    items: list[APIKeyResponse] = Field(
        default_factory=list, description="API Key 列表"
    )


class APIKeyQuery(BaseModel):
    """API Key 查询参数"""

    app_id: str | None = Field(None, description="应用ID")
    app_type: str | None = Field(None, description="应用类型")
    status: APIKeyStatus | None = Field(None, description="状态")
    creator: str | None = Field(None, description="创建人")
    owner: str | None = Field(None, description="Owner")
    tenant: str | None = Field(None, description="租户标识")
