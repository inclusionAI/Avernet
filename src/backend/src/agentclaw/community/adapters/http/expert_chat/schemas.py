"""
Pydantic Request/Response models for Expert Chat APIs.

Single source of truth for all expert chat HTTP contracts.
"""
from typing import Optional, List, Dict, Any, Generic, TypeVar
from pydantic import BaseModel, Field


T = TypeVar('T')

# ============ Request Models ============


class AddChatBotRequest(BaseModel):
    """添加专家Bot到对话列表请求"""
    bot_id: str = Field(..., description="Bot ID")
    owner_id: str = Field(..., description="Bot 所有者ID")


class GrtChatRequest(BaseModel):
    """GRT Chat 请求"""
    bot_id: str = Field(..., description="Bot ID")
    query: str = Field(..., description="提问内容")
    owner_id: str = Field(..., description="Bot 所有者ID")
    user_id: str = Field(..., description="用户ID")
    session_id: str = Field(..., description="session:uuid 格式")


# ============ Response Models ============

class AddChatBotResponse(BaseModel):
    """添加专家Bot响应"""
    id: int
    user_id: str
    bot_id: str
    owner_id: str
    status: str


class ExpertBotInfo(BaseModel):
    """专家Bot信息"""
    bot_id: str
    owner_id: str
    bot_name: str
    owner_name: str
    status: str
    binding_available: bool = False
    binding_id: Optional[int] = None
    ext: Optional[Dict[str, Any]] = None


class ConnectionInfo(BaseModel):
    """连接信息"""
    type: str = "websocket"
    target: Optional[str] = None
    token: Optional[str] = None
    engine_type: str = "openclaw"
    url: Optional[str] = None
    headers: Optional[Dict[str, str]] = None
    use_proxy: bool = False
    sandbox_id: Optional[str] = None


class ChatSessionResponse(BaseModel):
    """Chat Session 响应"""
    session_key: str = Field(..., description="session:uuid 格式")
    is_new: bool = Field(..., description="是否新创建")
    connection: ConnectionInfo


class ExpertBotListResponse(BaseModel):
    """专家Bot列表响应"""
    total: int
    items: List[ExpertBotInfo]


class ApiResponse(BaseModel, Generic[T]):
    """统一的 API 响应格式"""
    success: bool
    message: str
    error_code: int = 200
    data: T | None = None
