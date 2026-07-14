"""Common domain types (ApiResponse, DomainError, RequestId, etc.)."""

__all__ = [
    "ApiResponse",
    "BaseRequest",
    "DomainError",
    "ListResponse",
    "OperationContext",
    "RequestContext",
    "RequestId",
    "SuccessResponse",
    "T",
    "WithRequestId",
    "validate_request_id",
]

import re
from dataclasses import dataclass
from typing import Annotated, TypeVar

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field

T = TypeVar("T")


def validate_request_id(v: str) -> str:
    """Validate request_id format: 32-64 chars, alphanumeric/hyphens/underscores."""
    if len(v) < 32:
        raise ValueError("request_id must be at least 32 characters")
    if len(v) > 64:
        raise ValueError("request_id must be at most 64 characters")
    if not re.match(r"^[a-zA-Z0-9_-]+$", v):
        raise ValueError(
            "request_id must contain only alphanumeric characters, hyphens, and underscores"
        )
    return v


RequestId = Annotated[
    str,
    Field(
        ...,
        min_length=32,
        max_length=64,
        description="Request ID for correlation (e.g. 487ec32cf90b424195f6786651ac1ba5)",
    ),
    BeforeValidator(validate_request_id),
]


class WithRequestId(BaseModel):
    """Mixin for models that require a request_id for correlation."""

    request_id: RequestId


BaseRequest = WithRequestId


class ApiResponse[T](BaseModel):
    """统一 API 响应"""

    code: int = Field(default=0, description="响应码，0 表示成功")
    message: str = Field(default="success", description="响应消息")
    data: T | None = Field(default=None, description="响应数据")

    model_config = ConfigDict(from_attributes=True)


class ListResponse[T](BaseModel):
    """通用列表响应"""

    items: list[T] = Field(default_factory=list, description="数据项列表")
    total: int = Field(default=0, description="总记录数")
    page: int = Field(default=1, ge=1, description="当前页码")
    page_size: int = Field(default=20, ge=1, le=100, description="每页数量")

    model_config = ConfigDict(from_attributes=True)


class SuccessResponse(BaseModel):
    """通用成功响应"""

    success: bool = Field(default=True, description="操作是否成功")
    message: str = Field(
        default="Operation completed successfully", description="操作结果消息"
    )

    model_config = ConfigDict(from_attributes=True)


class DomainError(Exception):
    error_code: str = "DOMAIN_ERROR"
    http_status: int = 500

    def __init__(self, message: str = ""):
        self.message = message
        super().__init__(message)


@dataclass
class RequestContext:
    """Request context containing user information."""

    user_id: str
    nick_name: str | None = None  # 花名


class OperationContext(BaseModel):
    """Operation context"""

    # staff_id
    operator: str = Field(..., min_length=1, max_length=128, description="Operator")

    env: str = Field(..., min_length=1, max_length=128, description="Environment")
