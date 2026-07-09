"""Shared HTTP response envelope used across all routers."""
from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel


class ApiResponse(BaseModel):
    """Canonical envelope returned by engine-adapter HTTP endpoints.

    `warning` is populated when the active engine services the capability with
    a documented limitation (see `EngineCapabilities.limited`). Callers should
    surface it so the user knows the response may be partial.
    """

    success: bool
    data: Optional[Any] = None
    message: Optional[str] = None
    warning: Optional[str] = None
    total: Optional[int] = None


__all__ = ["ApiResponse"]
