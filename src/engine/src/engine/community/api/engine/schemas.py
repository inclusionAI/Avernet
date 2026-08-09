"""Engine management router HTTP schemas."""
from __future__ import annotations

from pydantic import BaseModel


class EngineRestartRequest(BaseModel):
    force: bool = False


__all__ = ["EngineRestartRequest"]
