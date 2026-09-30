"""
Bot management Pydantic models.

Request/response schemas for the bot management API.
Defined here in core/ so api/ and core/ code can import without
reaching into services/openclawserver/.
"""
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from agentclaw.community.core.workspace.constants import DEFAULT_ENGINE_TYPE, SUPPORTED_ENGINE_TYPES


class BotCreateRequest(BaseModel):
    """Request model for creating a bot."""
    bot_name: Optional[str] = Field(default=None, description="Bot name")
    bot_desc: Optional[str] = Field(default=None, description="Bot description")
    entity_id: Optional[str] = Field(default=None, description="Entity ID (defaults to staff_{user_id})")
    entity_type: Optional[str] = Field(default=None, description="Entity type: staff, proj, team (defaults to staff)")
    share_policy: Optional[Dict[str, Any]] = Field(default=None, description="Sharing configuration (JSON object)")
    engine_types: Optional[List[str]] = Field(default=None, description=f"Engine types (default: {SUPPORTED_ENGINE_TYPES})")
    engine_type: Optional[str] = Field(default=None, description=f"Active engine type (default: {DEFAULT_ENGINE_TYPE})")
    public: Optional[str] = Field(default="0", description="Whether the bot is public: 0=private, 1=public")
    ext: Optional[Dict[str, Any]] = Field(default=None, description="Extension field (JSON object)")
    bot_type: Optional[str] = Field(default="personal", description="Bot type: personal or service")


class BotUpdateRequest(BaseModel):
    """Request model for updating a bot."""
    bot_name: Optional[str] = Field(default=None, description="Bot name")
    bot_desc: Optional[str] = Field(default=None, description="Bot description")
    share_policy: Optional[Dict[str, Any]] = Field(default=None, description="Sharing configuration (JSON object)")
    public: Optional[str] = Field(default=None, description="Whether the bot is public: 0=private, 1=public")
    ext: Optional[Dict[str, Any]] = Field(default=None, description="Extension field (JSON object)")


class BotResponse(BaseModel):
    """Response model for bot operations."""
    success: bool
    data: Optional[Dict[str, Any]] = None
    message: str = "OK"


class BotListResponse(BaseModel):
    """Response model for bot list operations."""
    success: bool
    data: list = []
    total: int = 0
    message: str = "OK"


__all__ = ["BotCreateRequest", "BotUpdateRequest", "BotResponse", "BotListResponse"]
