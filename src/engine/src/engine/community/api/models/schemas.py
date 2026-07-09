"""Models router HTTP schemas."""
from __future__ import annotations

from engine.community.api.response import ApiResponse


class ModelsListResponse(ApiResponse):
    """Models list response."""
    pass


__all__ = ["ModelsListResponse"]
