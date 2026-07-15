"""System config management Pydantic models.

Extracted from api/domain/system_config_manage.py as part of
api/domain → api/{domain_name}/ refactoring.
"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class SystemConfigCreate(BaseModel):
    """Create system config request."""

    conf_key: str = Field(
        ...,
        max_length=256,
        description="配置key，以点为分隔符的1段或多段字符串key",
    )
    conf_value: str | None = Field(
        None,
        description="配置值，纯文本存储",
    )
    name: str = Field(
        ...,
        max_length=256,
        description="配置项显示名",
    )
    description: str | None = Field(
        None,
        max_length=1024,
        description="配置项说明",
    )
    operator: str | None = Field(
        default=None,
        max_length=64,
        description="操作者",
    )


class SystemConfigUpdate(BaseModel):
    """Update system config request."""

    conf_value: str | None = Field(
        None,
        description="配置值，纯文本存储",
    )
    name: str | None = Field(
        None,
        max_length=256,
        description="配置项显示名",
    )
    description: str | None = Field(
        None,
        max_length=1024,
        description="配置项说明",
    )
    operator: str | None = Field(
        default=None,
        max_length=64,
        description="操作人",
    )


class SystemConfigResponse(BaseModel):
    """System config response."""

    id: int
    conf_key: str
    conf_value: str | None
    env: str
    name: str
    description: str | None
    creator: str
    modifier: str
    gmt_create: datetime
    gmt_modified: datetime

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


class SystemConfigListResponse(BaseModel):
    """System config list response."""

    items: list[SystemConfigResponse]
    total: int
    page: int
    page_size: int
