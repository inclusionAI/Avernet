"""BCN 下行协议请求/响应模型定义

参考: BCN Bot 下行连接接入方案 (内部文档)
"""

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from secbaas.community.api.bcn import (
    BotRef as DomainBotRef,
)
from secbaas.community.api.bcn import (
    ContentBlock as DomainContentBlock,
)
from secbaas.community.api.bcn import (
    DownlinkMessage as DomainDownlinkMessage,
)
from secbaas.community.api.bcn import (
    FromRef as DomainFromRef,
)
from secbaas.community.api.bcn import (
    HistoryContentBlock as DomainHistoryContentBlock,
)
from secbaas.community.api.bcn import (
    HistoryMessage as DomainHistoryMessage,
)
from secbaas.community.api.bcn import (
    HistoryMeta as DomainHistoryMeta,
)
from secbaas.community.api.bcn import (
    HistoryPluginMeta as DomainHistoryPluginMeta,
)

# ─────────────────────────── 共享数据结构 ───────────────────────────


class BotRef(BaseModel):
    """Bot 标识信息"""

    provider_id: str = Field(..., description="Provider ID")
    provider_bot_ref: str | None = Field(
        default=None, description="Provider 内部 Bot 标识"
    )
    tags: list[str] | None = Field(default=None, description="Bot 标签列表")

    def to_domain(self) -> DomainBotRef:
        return DomainBotRef(
            provider_id=self.provider_id,
            provider_bot_ref=self.provider_bot_ref or "",
            tags=self.tags,
        )


class FromRef(BaseModel):
    """消息发送方"""

    kind: str = Field(..., description="发送方类型, 如 human / bot")
    id: str | None = Field(default=None, description="发送方 ID")
    name: str | None = Field(default=None, description="发送方名称")

    def to_domain(self) -> DomainFromRef:
        return DomainFromRef(
            kind=self.kind,
            id=self.id,
            name=self.name,
        )


class ContentBlock(BaseModel):
    """消息内容块 (DownlinkMessage 中使用)"""

    type: Literal["text", "toolCall"] = Field(..., description="内容块类型")
    text: str | None = Field(default=None, description="文本内容 (type=text 时)")
    # toolCall 字段
    name: str | None = Field(default=None, description="工具名称 (type=toolCall 时)")
    id: str | None = Field(default=None, description="工具调用 ID (type=toolCall 时)")
    arguments: str | None = Field(
        default=None, description="工具调用参数 JSON (type=toolCall 时)"
    )

    def to_domain(self) -> DomainContentBlock:
        return DomainContentBlock(
            type=self.type,
            text=self.text,
            name=self.name,
            id=self.id,
            arguments=self.arguments,
        )


class DownlinkMessage(BaseModel):
    """下行消息结构 (chat.send.message / chat.inject.message)"""

    role: str = Field(..., description="消息角色, 常见取值为 user / assistant")
    content: list[ContentBlock] | str = Field(..., description="消息内容块列表或纯文本")
    timestamp: int | None = Field(default=None, description="消息时间戳 (毫秒)")

    def to_domain(self) -> DomainDownlinkMessage:
        if isinstance(self.content, str):
            content: list[DomainContentBlock] | str = self.content
        else:
            content = [b.to_domain() for b in self.content]
        return DomainDownlinkMessage(
            role=self.role,
            content=content,
            timestamp=self.timestamp,
        )


# ─────────────────────────── Request ───────────────────────────


class ChatSendRequest(BaseModel):
    """chat.send 请求体

    BCN 请求某个 Bot 对当前会话轮次进行响应。
    """

    type: Literal["req"] = Field(default="req", description="固定为 req")
    id: str = Field(
        ..., description="本次 Bot 响应的生命周期 ID，即 run ID，也是幂等键"
    )
    session_id: str = Field(..., description="会话标识，Provider 应按它维护上下文")
    bcn_group_id: str = Field(..., description="BCN 侧 group ID")
    method: Literal["chat.send"] = Field(
        default="chat.send", description="固定为 chat.send"
    )
    to_bot: BotRef = Field(..., description="目标 Bot 信息")
    from_: FromRef = Field(..., alias="from", description="消息发送方")
    message: DownlinkMessage = Field(..., description="本轮输入消息")
    timeout_ms: int = Field(
        default=60000, description="BCN 等待 final 回调的最长时间 (毫秒)"
    )
    extensions: dict[str, Any] | None = Field(default=None, description="扩展信息")

    model_config = {"populate_by_name": True}


class ChatInjectRequest(BaseModel):
    """chat.inject 请求体

    BCN 向 Bot 注入一条消息，Provider 需要把它纳入 session state，
    但不触发推理，也不需要回调 /v1/bot/events。
    """

    type: Literal["req"] = Field(default="req", description="固定为 req")
    id: str = Field(..., description="本次注入事件 ID，也是 chat.inject 幂等键")
    session_id: str = Field(..., description="会话标识")
    bcn_group_id: str = Field(..., description="BCN 侧 group ID")
    method: Literal["chat.inject"] = Field(
        default="chat.inject", description="固定为 chat.inject"
    )
    to_bot: BotRef = Field(..., description="目标 Bot 信息")
    from_: FromRef = Field(..., alias="from", description="消息发送方")
    message: DownlinkMessage = Field(..., description="注入消息")
    timeout_ms: int = Field(default=60000, description="超时时间 (毫秒)")

    model_config = {"populate_by_name": True}


class ChatHistoryRequest(BaseModel):
    """chat.history 请求体

    BCN 向 Provider 查询指定 Bot 在某个会话中的聊天历史。
    Provider 必须在 HTTP 响应中同步返回消息列表。
    """

    type: Literal["req"] = Field(default="req", description="固定为 req")
    id: str = Field(..., description="本次查询请求 ID，用于幂等去重和日志追踪")
    method: Literal["chat.history"] = Field(
        default="chat.history", description="固定为 chat.history"
    )
    to_bot: BotRef = Field(..., description="目标 Bot 信息")
    session_id: str = Field(..., description="会话标识")
    bcn_group_id: str = Field(..., description="BCN 侧 group ID")
    limit: int = Field(default=50, ge=1, le=1000, description="最多返回的消息条数")
    before: int | None = Field(
        default=None,
        description="游标: 返回时间戳严格小于 before 的消息 (毫秒时间戳)",
    )
    after: int | None = Field(
        default=None,
        description="游标: 返回时间戳严格大于 after 的消息 (毫秒时间戳)",
    )
    timeout_ms: int = Field(default=30000, description="BCN 等待响应的最长时间 (毫秒)")

    @model_validator(mode="after")
    def _check_before_after_exclusive(self) -> "ChatHistoryRequest":
        """before 和 after 互斥，同时传入时应返回 400"""
        if self.before is not None and self.after is not None:
            raise ValueError("before 和 after 互斥，不能同时传入")
        return self


# ─────────────────────────── Response ───────────────────────────


class ChatSendSuccessResponse(BaseModel):
    """chat.send 成功响应"""

    ok: bool = Field(default=True)


class ChatInjectSuccessResponse(BaseModel):
    """chat.inject 成功响应"""

    ok: bool = Field(default=True)


class HistoryPluginMeta(BaseModel):
    """assistant 消息中的 plugin 元数据"""

    name: str | None = Field(default=None, description="插件名称")
    call_id: str | None = Field(default=None, description="调用 ID")

    @classmethod
    def from_domain(cls, src: DomainHistoryPluginMeta) -> "HistoryPluginMeta":
        return cls(name=src.name, call_id=src.call_id)


class HistoryMeta(BaseModel):
    """引擎元数据，BCN 透传到前端"""

    assistant_aggregation: bool | None = Field(
        default=None, alias="assistantAggregation"
    )
    plugin: HistoryPluginMeta | None = Field(default=None)

    model_config = {"populate_by_name": True}

    @classmethod
    def from_domain(cls, src: DomainHistoryMeta) -> "HistoryMeta":
        plugin = HistoryPluginMeta.from_domain(src.plugin) if src.plugin else None
        return cls(assistant_aggregation=src.assistant_aggregation, plugin=plugin)


class HistoryContentBlock(BaseModel):
    """chat.history 响应 messages 中的内容块"""

    type: Literal["text", "toolCall"] = Field(..., description="内容块类型")
    text: str | None = Field(default=None, description="文本内容 (type=text 时)")
    name: str | None = Field(default=None, description="工具名称 (type=toolCall 时)")
    id: str | None = Field(default=None, description="工具调用 ID (type=toolCall 时)")
    arguments: str | None = Field(
        default=None, description="工具调用参数 JSON (type=toolCall 时)"
    )

    @classmethod
    def from_domain(cls, src: DomainHistoryContentBlock) -> "HistoryContentBlock":
        return cls(
            type=src.type,  # type: ignore[arg-type]
            text=src.text,
            name=src.name,
            id=src.id,
            arguments=src.arguments,
        )


class HistoryMessage(BaseModel):
    """chat.history 响应中的消息条目"""

    id: str | None = Field(default=None, description="消息唯一标识")
    role: str = Field(
        ..., description="消息角色: user / assistant / tool_result (或 toolResult)"
    )
    content: str | list[HistoryContentBlock] = Field(
        ..., description="消息内容，纯文本为字符串，包含工具调用时为数组"
    )
    timestamp: int = Field(..., description="消息时间戳 (毫秒)")
    # assistant 相关
    stop_reason: str | None = Field(
        default=None,
        alias="stopReason",
        description="停止原因: complete(正常结束) / toolUse(触发工具调用)",
    )
    history_meta: HistoryMeta | None = Field(
        default=None, alias="historyMeta", description="引擎元数据"
    )
    # tool_result 相关
    tool_name: str | None = Field(
        default=None, alias="toolName", description="工具名称 (role=tool_result)"
    )
    tool_call_id: str | None = Field(
        default=None, alias="toolCallId", description="工具调用 ID (role=tool_result)"
    )
    is_error: bool | None = Field(
        default=None, alias="isError", description="工具执行是否出错 (role=tool_result)"
    )
    # 日志用
    error_message: str | None = Field(
        default=None, alias="errorMessage", description="错误信息，仅用于日志追踪"
    )
    model: str | None = Field(default=None, description="模型标识，仅用于日志追踪")

    model_config = {"populate_by_name": True}

    @classmethod
    def from_domain(cls, msg: DomainHistoryMessage) -> "HistoryMessage":
        """领域模型 HistoryMessage -> Pydantic HistoryMessage 响应模型

        Pydantic 模型通过 ``populate_by_name=True`` 支持用 Python 字段名
        （snake_case）构造，对外序列化时仍然走 alias（camelCase）。
        """
        content: str | list[HistoryContentBlock]
        if isinstance(msg.content, str):
            content = msg.content
        else:
            content = [HistoryContentBlock.from_domain(b) for b in msg.content]

        history_meta = (
            HistoryMeta.from_domain(msg.history_meta) if msg.history_meta else None
        )

        return cls(
            id=msg.id,
            role=msg.role,
            content=content,
            timestamp=msg.timestamp,
            stop_reason=msg.stop_reason,
            history_meta=history_meta,
            tool_name=msg.tool_name,
            tool_call_id=msg.tool_call_id,
            is_error=msg.is_error,
            error_message=msg.error_message,
            model=msg.model,
        )


class ChatHistorySuccessResponse(BaseModel):
    """chat.history 成功响应"""

    ok: bool = Field(default=True)
    session_id: str = Field(..., description="回传请求中的 session_id")
    messages: list[HistoryMessage] = Field(
        default_factory=list,
        description="消息数组，按时间戳倒序排列（最新在前）",
    )
    has_more: bool = Field(default=False, description="是否还有更早或更晚的消息可翻页")
    next_before: int | None = Field(
        default=None,
        description="下一页游标: 当前结果中最早消息的 timestamp，用于向前翻页",
    )
    next_after: int | None = Field(
        default=None,
        description="下一页游标: 当前结果中最晚消息的 timestamp，用于向后翻页",
    )


# ─────────────────────────── 错误响应 ───────────────────────────


class BcnErrorDetail(BaseModel):
    """BCN 错误详情"""

    code: str = Field(..., description="错误码")
    message: str = Field(..., description="错误描述")
    retryable: bool = Field(default=False, description="是否可重试")
    retry_after_ms: int | None = Field(
        default=None, description="建议重试等待时间 (毫秒)"
    )


class BcnErrorResponse(BaseModel):
    """BCN 统一下行错误响应"""

    ok: bool = Field(default=False)
    error: BcnErrorDetail = Field(..., description="错误详情")
