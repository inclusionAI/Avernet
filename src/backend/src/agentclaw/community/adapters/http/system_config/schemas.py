"""System Config API Pydantic schemas — 对外 API 契约.

All Pydantic models for the System Config API are defined here.
This is the single source of truth for frontend <-> backend contracts.

Rules:
- Only Pydantic BaseModel + Field
- NO business logic
- Do NOT import core/plugin_api/plugins
"""

from typing import Any, Generic, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


# =============================================================================
# Base Response
# =============================================================================


class ApiResponse(BaseModel, Generic[T]):
    """Unified API response format."""

    success: bool = Field(..., description="Request success status")
    message: str = Field(..., description="Response message")
    error_code: int = Field(..., description="Error code (200 for success)")
    data: T | None = Field(None, description="Response data")


# =============================================================================
# Category Request Models
# =============================================================================


class CreateCategoryRequest(BaseModel):
    """创建分类目录请求"""

    category: str = Field(..., description="分类标识")
    category_name: str = Field(..., description="分类名称")
    description: str | None = Field(default=None, description="描述")


class DeleteCategoryRequest(BaseModel):
    """删除分类目录请求"""

    category_id: int = Field(..., description="分类ID")


# =============================================================================
# Config Item Request Models
# =============================================================================


class GetConfigRequest(BaseModel):
    """获取配置请求"""

    category: str = Field(..., description="分类标识：device/management/upgrade/security/system")
    config_key: str = Field(..., description="配置键")


class SetConfigRequest(BaseModel):
    """设置配置请求"""

    category: str = Field(..., description="分类标识")
    config_key: str = Field(..., description="配置键")
    config_value: Any = Field(..., description="配置值")
    description: str | None = Field(default=None, description="配置描述")


class DeleteConfigRequest(BaseModel):
    """删除配置请求"""

    category: str = Field(..., description="分类标识")
    config_key: str = Field(..., description="配置键")


# =============================================================================
# Device Config Request Models
# =============================================================================


class AddAllocationListRequest(BaseModel):
    """添加员工到分配名单请求"""

    staff_ids: list[str] = Field(..., description="员工工号列表")
    provider: str = Field(..., description="Provider: arca 或 baas")


class RemoveAllocationListRequest(BaseModel):
    """从分配名单移除员工请求"""

    staff_ids: list[str] = Field(..., description="员工工号列表")
    provider: str = Field(..., description="Provider: arca 或 baas")


class SetDefaultProviderRequest(BaseModel):
    """设置默认 Provider 请求"""

    provider: str = Field(..., description="默认 Provider: arca 或 baas")


class SetTemplateTypeProviderMapRequest(BaseModel):
    """设置 template_type → provider 映射请求"""

    mapping: dict[str, str] = Field(
        ..., description="template_type → provider 映射, e.g. {'personalCoding': 'baas'}"
    )


class SetPersonalBotBaasDisableRequest(BaseModel):
    """设置 personal bot BaaS 紧急回退开关"""

    disabled: bool = Field(..., description="true 时所有 personal bot 强制走 arca")
