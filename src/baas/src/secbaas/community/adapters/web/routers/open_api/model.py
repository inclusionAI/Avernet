"""Open API request/response model definitions"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from secbaas.community.api import ApiResponse


class RunRequest(BaseModel):
    """Single-turn conversation request model"""

    message: str = Field(..., description="User message content", min_length=1)
    metadata: dict[str, Any] | None = Field(
        default=None,
        description=(
            "Optional metadata information."
            "Recognized keys: "
            "biz_task_id (str, optional): Caller-assigned business task identifier. Defaults to run_id when absent. "
            "biz_scene (str, optional): Business scene/category tag. Defaults to 'default' when absent."
        ),
    )


class ExtraInfo(BaseModel):
    """Result extra information"""

    usage: dict[str, Any] | None = Field(
        default=None, description="Token usage information"
    )


class RunResultData(BaseModel):
    """Conversation result data"""

    content: str | None = Field(default=None, description="Bot reply content")
    extra: ExtraInfo | None = Field(
        default=None, description="Result extra information"
    )


class RunResultResponseData(BaseModel):
    """Run result response data"""

    run_id: str = Field(..., description="Run ID")
    bot_id: str = Field(..., description="Bot ID")
    session_id: str = Field(..., description="Session ID")
    status: str = Field(..., description="Run status: pending/running/completed/failed")
    created_at: datetime = Field(..., description="Creation time")
    completed_at: datetime | None = Field(default=None, description="Completion time")
    result: RunResultData | None = Field(
        default=None, description="Conversation result"
    )
    error: str | None = Field(default=None, description="Error information")


class RunResponseData(BaseModel):
    """Single-turn conversation response data"""

    run_id: str = Field(..., description="Run ID")


class RunResponse(ApiResponse[RunResponseData]):
    """Single-turn conversation standard response"""

    data: RunResponseData | None = Field(default=None, description="Response data")


class RunResultResponse(ApiResponse[RunResultResponseData]):
    """Run result query response"""

    data: RunResultResponseData | None = Field(
        default=None, description="Run result data"
    )


class MessageRequest(BaseModel):
    """Message delivery request model"""

    message: str = Field(..., description="User message content", min_length=1)
    bot_id: str = Field(..., description="Bot unique identifier", min_length=1)
    callback_url: str | None = Field(default=None, description="Callback URL")
    message_id: str | None = Field(default=None, description="Message ID")
    metadata: dict[str, Any] | None = Field(
        default=None,
        description=(
            "Optional metadata information."
            "Recognized keys: "
            "biz_task_id (str, optional): Caller-assigned business task identifier. Defaults to run_id when absent. "
            "biz_scene (str, optional): Business scene/category tag. Defaults to 'default' when absent."
        ),
    )


class StreamMessageRequest(BaseModel):
    """Streaming message delivery request model"""

    message: str = Field(..., description="User message content", min_length=1)
    bot_id: str = Field(..., description="Bot unique identifier", min_length=1)
    metadata: dict[str, Any] | None = Field(
        default=None,
        description=(
            "Optional metadata information."
            "Recognized keys: "
            "biz_task_id (str, optional): Caller-assigned business task identifier. Defaults to run_id when absent. "
            "biz_scene (str, optional): Business scene/category tag. Defaults to 'default' when absent."
        ),
    )


class MessageResponseData(BaseModel):
    """Message delivery response data"""

    message_id: str = Field(
        ..., description="Message ID, used for tracking delivery status"
    )
    session_id: str | None = Field(default=None, description="Session ID")


class MessageResponse(ApiResponse[MessageResponseData]):
    """Message delivery standard response"""

    data: MessageResponseData | None = Field(default=None, description="Response data")


class MessageResultData(BaseModel):
    """Message result data"""

    content: str | None = Field(default=None, description="Bot reply content")
    extra: ExtraInfo | None = Field(
        default=None, description="Result extra information"
    )


class MessageResultResponseData(BaseModel):
    """Message result response data"""

    message_id: str = Field(..., description="Message ID")
    bot_id: str = Field(..., description="Bot ID")
    session_id: str = Field(..., description="Session ID")
    status: str = Field(
        ..., description="Message status: pending/running/completed/failed"
    )
    created_at: datetime = Field(..., description="Creation time")
    completed_at: datetime | None = Field(default=None, description="Completion time")
    result: MessageResultData | None = Field(
        default=None, description="Message processing result"
    )
    error: str | None = Field(default=None, description="Error information")


class MessageResultResponse(ApiResponse[MessageResultResponseData]):
    """Message result query response"""

    data: MessageResultResponseData | None = Field(
        default=None, description="Message result data"
    )


class SessionQueryResponseData(BaseModel):
    """Session query response data"""

    session_id: str = Field(..., description="Session ID")
    bot_id: str = Field(..., description="Bot ID")
    status: str = Field(..., description="Session status")
    created_at: datetime | None = Field(default=None, description="Creation time")
    updated_at: datetime | None = Field(default=None, description="Update time")


class SessionQueryResponse(ApiResponse[SessionQueryResponseData]):
    """Session query standard response"""

    data: SessionQueryResponseData | None = Field(
        default=None, description="Session query data"
    )


class MessageItem(BaseModel):
    """Message item"""

    id: str | None = Field(default=None, description="Message ID")
    session_id: str | None = Field(default=None, description="Session ID")
    role: str = Field(..., description="Message role: user / assistant / tool_result")
    content: str | None = Field(default=None, description="Message text content")
    meta: dict[str, Any] | None = Field(default=None, description="Message metadata")
    created_at: str | None = Field(
        default=None, description="Message creation time (ISO 8601)"
    )
    history_meta: dict[str, Any] | None = Field(
        default=None, description="Historical message metadata"
    )


class SessionMessagesResponseData(BaseModel):
    """Session message list response data"""

    session_id: str = Field(..., description="Session ID")
    messages: list[MessageItem] = Field(
        default_factory=list, description="Message list"
    )
    total: int = Field(default=0, description="Total messages")
    has_more: bool = Field(default=False, description="Whether there are more messages")


class SessionMessagesResponse(ApiResponse[SessionMessagesResponseData]):
    """Session message list standard response"""

    data: SessionMessagesResponseData | None = Field(
        default=None, description="Session message data"
    )


class SessionListItem(BaseModel):
    """Session list item.

    Field set is aligned with the api-level ``SessionInfo`` returned by
    ``BotRunner.list_sessions``. ``title`` / ``user_id`` / ``agent_id`` /
    ``model`` / ``message_count`` are optional: the upstream
    ``AsyncSessionClient.list_sessions`` carries them, but they are not
    surfaced through the api-level ``SessionInfo`` mapping today, so they are
    surfaced as ``None`` rather than fabricated.
    """

    session_id: str = Field(..., description="Session ID")
    bot_id: str | None = Field(default=None, description="Bot ID")
    status: str | None = Field(default=None, description="Session status")
    title: str | None = Field(default=None, description="Session title")
    user_id: str | None = Field(default=None, description="User ID")
    agent_id: str | None = Field(default=None, description="Agent ID")
    model: str | None = Field(default=None, description="Model name")
    message_count: int | None = Field(
        default=None, description="Number of messages in the session"
    )
    created_at: datetime | None = Field(default=None, description="Creation time")
    updated_at: datetime | None = Field(default=None, description="Update time")


class SessionListResponseData(BaseModel):
    """Session list response data.

    ``total`` and ``has_more`` are derived from the returned ``items`` list
    because ``AsyncSessionClient.list_sessions`` does not return a total count.
    ``has_more = len(items) == limit`` is a conservative hint, not a precise
    indicator — see spec §3.1.
    """

    items: list[SessionListItem] = Field(
        default_factory=list, description="Session list"
    )
    total: int = Field(default=0, description="Number of sessions in this response")
    has_more: bool = Field(
        default=False,
        description="Conservative hint: True when the returned page is full",
    )


class SessionListResponse(ApiResponse[SessionListResponseData]):
    """Session list standard response"""

    data: SessionListResponseData | None = Field(
        default=None, description="Session list data"
    )
