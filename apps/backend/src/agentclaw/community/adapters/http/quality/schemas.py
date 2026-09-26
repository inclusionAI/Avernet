"""Quality API Schemas.

Pydantic Request/Response models for quality task endpoints.
"""
from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field


class ApiResponse(BaseModel):
    """Unified API response format."""

    success: bool
    message: str = "OK"
    error_code: int = 200
    data: Optional[Any] = None


class ListQualityTasksRequest(BaseModel):
    """List quality tasks request."""

    task_type: str = Field(default="eval", description="任务类型，默认 eval")
    biz_type: str = Field(
        default="service_bot_single", description="业务类型，默认 service_bot_single"
    )
    bot_id: Optional[str] = Field(None, description="Bot ID")
    owner_id: Optional[str] = Field(None, description="Owner ID")
    page: int = Field(default=1, ge=1, description="页码，从 1 开始")
    page_size: int = Field(default=20, ge=1, le=100, description="每页数量，最大 100")


class QualityTaskResponse(BaseModel):
    """Quality task response."""

    id: int
    uuid: Optional[str] = None
    task_type: str
    biz_type: str
    status: str
    bot_id: Optional[str] = None
    owner_id: Optional[str] = None
    ext: dict[str, Any] = Field(default_factory=dict, description="扩展字段，JSON")
    operator_id: Optional[str] = None
    env: Optional[str] = None
    gmt_create: Optional[datetime] = None
    gmt_modified: Optional[datetime] = None


class ListQualityTasksResponse(BaseModel):
    """List quality tasks response."""

    items: list[QualityTaskResponse]
    total: int
    page: int
    page_size: int


class CreateQualityTaskRequest(BaseModel):
    """Create quality task request."""

    task_type: str = Field(..., description="任务类型")
    biz_type: str = Field(..., description="业务类型")
    bot_id: Optional[str] = Field(None, description="Bot ID")
    owner_id: Optional[str] = Field(None, description="Owner ID")
    ext: Optional[dict[str, Any]] = Field(None, description="扩展字段")


class CreateQualityTaskResponse(BaseModel):
    """Create quality task response."""

    success: bool
    data: QualityTaskResponse
    message: str


class ProcessTaskResponse(BaseModel):
    """Process task response."""

    success: bool
    data: QualityTaskResponse
    message: str
