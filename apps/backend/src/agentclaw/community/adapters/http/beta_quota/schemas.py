"""Beta Quota API Pydantic schemas."""

from typing import TypeVar

from pydantic import BaseModel, Field


T = TypeVar("T")


class ApiResponse[T](BaseModel):
    success: bool = Field(..., description="Request success status")
    message: str = Field(..., description="Response message")
    error_code: int = Field(..., description="Error code (200 for success)")
    data: T | None = Field(None, description="Response data")


class AdjustQuotaRequest(BaseModel):
    delta: int = Field(..., description="名额增量：负=占用，正=增配")
