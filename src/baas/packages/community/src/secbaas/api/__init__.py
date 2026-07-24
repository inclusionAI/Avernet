"""Common domain types for the packages tree (secbaas.* import convention)."""

from typing import TypeVar

from pydantic import BaseModel, ConfigDict, Field

T = TypeVar("T")


class ApiResponse[T](BaseModel):
    """Unified API response wrapper."""

    code: int = Field(default=0, description="Response code, 0 indicates success")
    message: str = Field(default="success", description="Response message")
    data: T | None = Field(default=None, description="Response data")

    model_config = ConfigDict(from_attributes=True)


class DomainError(Exception):
    """Base domain error for the SecBaaS platform.

    All domain-specific exception classes inherit from this.
    """

    error_code: str = "DOMAIN_ERROR"
    http_status: int = 500

    def __init__(self, message: str = ""):
        self.message = message
        super().__init__(message)
