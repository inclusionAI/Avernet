"""Domain records and enums for market favorites."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel


class FavoriteTargetType(StrEnum):
    SKILL = "SKILL"
    MCP = "MCP"


class MarketFavoriteRecord(BaseModel):
    id: int
    space_id: int
    user_id: str
    target_type: FavoriteTargetType
    target_code: str
    created_by: str
    env: str
    gmt_created: datetime
    gmt_modified: datetime
