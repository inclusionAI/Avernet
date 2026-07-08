"""Engine management router HTTP schemas."""
from __future__ import annotations

from pydantic import BaseModel


class EngineSwitchRequest(BaseModel):
    engine: str
    force: bool = False


class EngineRestartRequest(BaseModel):
    force: bool = False


__all__ = ["EngineSwitchRequest", "EngineRestartRequest"]
