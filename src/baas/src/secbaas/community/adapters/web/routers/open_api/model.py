"""Open API 请求/响应模型定义"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from secbaas.community.api import ApiResponse


class RunRequest(BaseModel):
    """单次对话请求模型"""

    message: str = Field(..., description="用户消息内容", min_length=1)
    metadata: dict[str, Any] | None = Field(
        default=None,
        description=(
            "可选的元数据信息。"
            "Recognized keys: "
            "biz_task_id (str, optional): Caller-assigned business task identifier. Defaults to run_id when absent. "
            "biz_scene (str, optional): Business scene/category tag. Defaults to 'default' when absent."
        ),
    )


class ExtraInfo(BaseModel):
    """结果额外信息"""

    usage: dict[str, Any] | None = Field(default=None, description="Token 使用信息")


class RunResultData(BaseModel):
    """对话结果数据"""

    content: str | None = Field(default=None, description="Bot 回复内容")
    extra: ExtraInfo | None = Field(default=None, description="结果额外信息")


class RunResultResponseData(BaseModel):
    """运行结果响应数据"""

    run_id: str = Field(..., description="运行 ID")
    bot_id: str = Field(..., description="Bot ID")
    session_id: str = Field(..., description="会话 ID")
    status: str = Field(..., description="运行状态: pending/running/completed/failed")
    created_at: datetime = Field(..., description="创建时间")
    completed_at: datetime | None = Field(default=None, description="完成时间")
    result: RunResultData | None = Field(default=None, description="对话结果")
    error: str | None = Field(default=None, description="错误信息")


class RunResponseData(BaseModel):
    """单次对话响应数据"""

    run_id: str = Field(..., description="运行 ID")


class RunResponse(ApiResponse[RunResponseData]):
    """单次对话标准响应"""

    data: RunResponseData | None = Field(default=None, description="响应数据")


class RunResultResponse(ApiResponse[RunResultResponseData]):
    """运行结果查询响应"""

    data: RunResultResponseData | None = Field(default=None, description="运行结果数据")


class MessageRequest(BaseModel):
    """消息投递请求模型"""

    message: str = Field(..., description="用户消息内容", min_length=1)
    bot_id: str = Field(..., description="Bot 唯一标识", min_length=1)
    callback_url: str | None = Field(default=None, description="Callback URL")
    message_id: str | None = Field(default=None, description="Message ID")
    metadata: dict[str, Any] | None = Field(
        default=None,
        description=(
            "可选的元数据信息。"
            "Recognized keys: "
            "biz_task_id (str, optional): Caller-assigned business task identifier. Defaults to run_id when absent. "
            "biz_scene (str, optional): Business scene/category tag. Defaults to 'default' when absent."
        ),
    )


class StreamMessageRequest(BaseModel):
    """消息投递请求模型"""

    message: str = Field(..., description="用户消息内容", min_length=1)
    bot_id: str = Field(..., description="Bot 唯一标识", min_length=1)
    metadata: dict[str, Any] | None = Field(
        default=None,
        description=(
            "可选的元数据信息。"
            "Recognized keys: "
            "biz_task_id (str, optional): Caller-assigned business task identifier. Defaults to run_id when absent. "
            "biz_scene (str, optional): Business scene/category tag. Defaults to 'default' when absent."
        ),
    )


class MessageResponseData(BaseModel):
    """消息投递响应数据"""

    message_id: str = Field(..., description="消息 ID，用于追踪投递状态")
    session_id: str | None = Field(default=None, description="会话 ID")


class MessageResponse(ApiResponse[MessageResponseData]):
    """消息投递标准响应"""

    data: MessageResponseData | None = Field(default=None, description="响应数据")


class MessageResultData(BaseModel):
    """消息结果数据"""

    content: str | None = Field(default=None, description="Bot 回复内容")
    extra: ExtraInfo | None = Field(default=None, description="结果额外信息")


class MessageResultResponseData(BaseModel):
    """消息结果响应数据"""

    message_id: str = Field(..., description="消息 ID")
    bot_id: str = Field(..., description="Bot ID")
    session_id: str = Field(..., description="会话 ID")
    status: str = Field(..., description="消息状态: pending/running/completed/failed")
    created_at: datetime = Field(..., description="创建时间")
    completed_at: datetime | None = Field(default=None, description="完成时间")
    result: MessageResultData | None = Field(default=None, description="消息处理结果")
    error: str | None = Field(default=None, description="错误信息")


class MessageResultResponse(ApiResponse[MessageResultResponseData]):
    """消息结果查询响应"""

    data: MessageResultResponseData | None = Field(
        default=None, description="消息结果数据"
    )


class SessionQueryResponseData(BaseModel):
    """会话查询响应数据"""

    session_id: str = Field(..., description="Session ID")
    bot_id: str = Field(..., description="Bot ID")
    status: str = Field(..., description="Session status")
    created_at: datetime | None = Field(default=None, description="创建时间")
    updated_at: datetime | None = Field(default=None, description="更新时间")


class SessionQueryResponse(ApiResponse[SessionQueryResponseData]):
    """会话查询标准响应"""

    data: SessionQueryResponseData | None = Field(
        default=None, description="会话查询数据"
    )


class MessageItem(BaseModel):
    """消息条目"""

    id: str | None = Field(default=None, description="Message ID")
    session_id: str | None = Field(default=None, description="Session ID")
    role: str = Field(..., description="Message role: user / assistant / tool_result")
    content: str | None = Field(default=None, description="Message text content")
    meta: dict[str, Any] | None = Field(default=None, description="消息元数据")
    created_at: str | None = Field(default=None, description="消息创建时间 (ISO 8601)")
    history_meta: dict[str, Any] | None = Field(
        default=None, description="历史消息元数据"
    )


class SessionMessagesResponseData(BaseModel):
    """会话消息列表响应数据"""

    session_id: str = Field(..., description="Session ID")
    messages: list[MessageItem] = Field(default_factory=list, description="消息列表")
    total: int = Field(default=0, description="消息总数")
    has_more: bool = Field(default=False, description="是否还有更多消息")


class SessionMessagesResponse(ApiResponse[SessionMessagesResponseData]):
    """会话消息列表标准响应"""

    data: SessionMessagesResponseData | None = Field(
        default=None, description="会话消息数据"
    )
