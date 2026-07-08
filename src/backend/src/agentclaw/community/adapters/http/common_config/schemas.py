"""Common Config API Pydantic schemas."""

from typing import Any, TypeVar

from pydantic import BaseModel, Field


T = TypeVar("T")


class ApiResponse[T](BaseModel):
    success: bool = Field(..., description="Request success status")
    message: str = Field(..., description="Response message")
    error_code: int = Field(..., description="Error code (200 for success)")
    data: T | None = Field(None, description="Response data")


class GetCommonConfigRequest(BaseModel):
    business_code: str = Field(..., description="业务 code")
    param_code: str = Field(..., description="配置项 code")
    only_enabled: bool = Field(False, description="是否只返回启用配置")


class GetCommonConfigValueRequest(BaseModel):
    business_code: str = Field(..., description="业务 code")
    param_code: str = Field(..., description="配置项 code")
    default: Any = Field(None, description="默认值")
    only_enabled: bool = Field(True, description="是否只读取启用配置")


class CreateCommonConfigRequest(BaseModel):
    business_code: str = Field(..., description="业务 code")
    business_name: str | None = Field(None, description="业务名称")
    param_code: str = Field(..., description="配置项 code")
    param_name: str = Field(..., description="配置项名称")
    param_value: Any = Field(None, description="配置项值")
    enable: str = Field("1", description="是否启用：1/0")
    ext_info: Any = Field(None, description="扩展属性")


class UpdateCommonConfigRequest(BaseModel):
    id: int = Field(..., description="配置 ID")
    business_name: str | None = Field(None, description="业务名称")
    param_name: str | None = Field(None, description="配置项名称")
    param_value: Any = Field(None, description="配置项值")
    enable: str | None = Field(None, description="是否启用：1/0")
    ext_info: Any = Field(None, description="扩展属性")


class DeleteCommonConfigRequest(BaseModel):
    id: int | None = Field(None, description="配置 ID")
    business_code: str | None = Field(None, description="业务 code")
    param_code: str | None = Field(None, description="配置项 code")


class ToggleCommonConfigRequest(BaseModel):
    id: int = Field(..., description="配置 ID")
