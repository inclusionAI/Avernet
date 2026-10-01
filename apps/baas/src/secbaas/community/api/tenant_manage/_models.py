"""Tenant management Pydantic models.

Extracted from api/domain/tenant_manage.py as part of
api/domain → api/{domain_name}/ refactoring.
"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class TenantConfig(BaseModel):
    """Typed model for baas_tenant.extra_config JSON column.

    Contains tenant-level configuration only.
    PaaS-specific credentials moved to baas_device_template.config.

    Uses extra="allow" to tolerate unknown keys from legacy data.
    """

    model_config = ConfigDict(extra="allow")

    default_template_uuid: str | None = Field(
        None, description="默认设备模板 UUID，用于自动模板选择"
    )


class TenantCreate(BaseModel):
    """创建租户请求"""

    name: str = Field(..., min_length=1, max_length=256, description="租户名称")
    description: str | None = Field(None, max_length=1024, description="描述")
    extra_config: TenantConfig | None = Field(None, description="扩展配置")
    operator: str | None = Field(
        default=None, min_length=1, max_length=128, description="操作人 ID"
    )


class TenantUpdate(BaseModel):
    """更新租户请求"""

    description: str | None = Field(None, max_length=1024, description="描述")
    extra_config: TenantConfig | None = Field(None, description="扩展配置")
    operator: str | None = Field(
        default=None, min_length=1, max_length=128, description="操作人 ID"
    )


class TenantResponse(BaseModel):
    """租户响应"""

    name: str
    description: str | None
    env: str
    extra_config: TenantConfig | None
    creator: str
    modifier: str
    gmt_create: datetime
    gmt_modified: datetime

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


class TenantListResponse(BaseModel):
    """租户列表响应"""

    items: list[TenantResponse]
    total: int
    page: int
    page_size: int
