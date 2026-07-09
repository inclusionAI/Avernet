"""
Bot management schemas (Pydantic models).

All request/response models for bot management API.
"""
from typing import Any, Optional, Dict
from pydantic import BaseModel


class ApiResponse(BaseModel):
    """Unified API response format."""
    success: bool
    message: str = "OK"
    error_code: int = 200
    data: Optional[Any] = None


class BotListData(BaseModel):
    """Bot list data."""
    total: int
    items: list


class BotCreateRequest(BaseModel):
    """Bot creation request model."""
    bot_name: Optional[str] = None
    bot_desc: Optional[str] = None
    entity_id: Optional[str] = None
    entity_type: Optional[str] = None
    share_policy: Optional[Any] = None
    engine_type: Optional[str] = None
    avatar_url: Optional[str] = None
    bot_type: Optional[str] = None  # "personal" or "service", defaults to "personal"
    template_type: Optional[str] = None  # Template type (e.g., "applicationCoding")
    template_config: Optional[Dict[str, Any]] = None  # Template configuration


class BotUpdateRequest(BaseModel):
    """Bot update request model."""
    bot_name: Optional[str] = None
    bot_desc: Optional[str] = None
    share_policy: Optional[Any] = None
    avatar_url: Optional[str] = None
    ext: Optional[Dict[str, Any]] = None  # Extension field (optional), e.g. read_only_rules
    template_config: Optional[Dict[str, Any]] = None  # Template configuration (optional)


class AdminUpdateBotRequest(BaseModel):
    """Admin update bot request model — operator only, can modify any bot."""
    owner_id: str  # Required: target bot's owner ID
    bot_name: Optional[str] = None
    bot_desc: Optional[str] = None
    template_config: Optional[Dict[str, Any]] = None  # Sandbox config: image/command/envs/resource_spec


class BotPublicRequest(BaseModel):
    """Bot public/unpublic request model."""
    public: str
    permission_owner: str = "caller"


class CreateBotForOthersRequest(BaseModel):
    """Request model for creating bot for others."""
    target_user_id: str
    target_nick_name: str


class ReleaseBotForOthersRequest(BaseModel):
    """Request model for releasing bot for others."""
    target_user_id: str
    target_bot_id: str


class RestartBotForOthersRequest(BaseModel):
    """Request model for restarting bot for others."""
    target_user_id: str
    target_bot_id: str
