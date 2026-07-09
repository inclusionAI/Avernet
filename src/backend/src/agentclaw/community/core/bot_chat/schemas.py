from typing import Any, Generic, TypeVar

from pydantic import BaseModel, Field


# --- Response Data Models ---


class SessionMetadata(BaseModel):
    """Metadata from Langfuse trace, including identity attributes."""

    attributes: dict[str, Any] = Field(default_factory=dict, description="Trace metadata attributes (e.g. identity.bot_id, identity.owner_id, session_id)")


class ConversationSession(BaseModel):
    """A single conversation session (mapped from a Langfuse trace)."""

    id: str = Field(..., description="Trace ID")
    biz_task_id: str | None = Field(default=None, description="Business task ID")
    biz_scene: str | None = Field(default=None, description="Business scene")
    session_id: str | None = Field(default=None, description="Canonical session ID")
    session_key: str | None = Field(default=None, description="Engine session key")
    name: str = Field(default="", description="Session / trace name")
    input: str | None = Field(default=None, description="Last user message extracted from trace input")
    status: str = Field(default="SUCCESS", description="SUCCESS or FAILED")
    timestamp: str = Field(..., description="ISO 8601 timestamp")
    user_id: str | None = Field(default=None, description="Langfuse userId (owner_id)")
    metadata: SessionMetadata | None = Field(default=None, description="Trace metadata with attributes")
    total_cost: float = Field(default=0.0, description="Total cost in USD")
    latency_ms: float = Field(default=0.0, description="Total latency in milliseconds")
    total_tokens: int = Field(default=0, description="Total tokens used")


class ConversationObservation(BaseModel):
    """A single observation within a trace (span or generation)."""

    id: str
    biz_task_id: str | None = Field(default=None)
    biz_scene: str | None = Field(default=None)
    name: str = Field(default="")
    type: str = Field(..., description="GENERATION or SPAN")
    latency_ms: float = Field(default=0.0)
    total_cost: float = Field(default=0.0)
    total_tokens: int = Field(default=0)
    input: Any = Field(default=None)
    output: Any = Field(default=None)
    model_name: str | None = Field(default=None, description="Model name from metadata.attributes['gen_ai.request.model']")
    parent_observation_id: str | None = Field(default=None)
    children: list["ConversationObservation"] = Field(default_factory=list)


class ConversationDetail(BaseModel):
    """Full conversation session detail including observation tree."""

    id: str
    biz_task_id: str | None = Field(default=None)
    biz_scene: str | None = Field(default=None)
    session_id: str | None = Field(default=None)
    session_key: str | None = Field(default=None)
    name: str = Field(default="")
    input: Any = Field(default=None)
    output: Any = Field(default=None)
    status: str = Field(default="SUCCESS")
    timestamp: str = Field(..., description="ISO 8601 timestamp")
    user_id: str | None = Field(default=None)
    metadata: SessionMetadata | None = Field(default=None)
    total_cost: float = Field(default=0.0)
    latency_ms: float = Field(default=0.0)
    total_tokens: int = Field(default=0)
    observations: list[ConversationObservation] = Field(default_factory=list, description="Root-level observations with nested children")


class SessionListResponse(BaseModel):
    """Paginated list of conversation sessions."""

    sessions: list[ConversationSession] = Field(default_factory=list)
    total: int = Field(default=0, description="Total number of matching sessions")
    page: int = Field(default=1)
    limit: int = Field(default=20)
    has_more: bool = Field(default=False)


class HealthCheckData(BaseModel):
    """Health check response data."""

    status: str = Field(..., description="healthy or unhealthy")
    langfuse_url: str | None = Field(default=None)
    error: str | None = Field(default=None)


# --- Generic API Response ---


T = TypeVar("T")


class ApiResponse(BaseModel, Generic[T]):
    """Unified API response envelope."""

    success: bool
    message: str
    error_code: int = 200
    data: T | None = None
