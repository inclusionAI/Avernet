"""Bot router HTTP schemas."""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


class BotConfigRequest(BaseModel):
    role: Optional[str] = None        # "OWNER" 或 "CALLER"
    visibility: Optional[str] = None  # "PRIVATE" 或 "PUBLIC"


__all__ = ["BotConfigRequest"]
