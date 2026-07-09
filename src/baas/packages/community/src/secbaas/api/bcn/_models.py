"""BCN 下行协议领域模型

定义 BCN 下行协议 (chat.send / chat.inject / chat.history) 使用的领域模型。
与 adapters/web 层的 Pydantic 模型不同，这里使用 dataclass 用于轻量内部传递。
"""

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True, frozen=True)
class BotRef:
    """Bot 标识信息"""

    provider_id: str
    provider_bot_ref: str
    tags: list[str] | None = None


@dataclass(slots=True, frozen=True)
class FromRef:
    """消息发送方"""

    kind: str
    id: str | None = None
    name: str | None = None


@dataclass(slots=True, frozen=True)
class ContentBlock:
    """消息内容块"""

    type: str  # "text" | "toolCall"
    text: str | None = None
    name: str | None = None
    id: str | None = None
    arguments: str | None = None


@dataclass(slots=True, frozen=True)
class DownlinkMessage:
    """下行消息结构 (chat.send / chat.inject 的 message)"""

    role: str
    content: list[ContentBlock] | str
    timestamp: int | None = None


# ─────────────────────────── chat.send ───────────────────────────


@dataclass(slots=True, frozen=True)
class ChatSendInput:
    """chat.send 请求的领域输入"""

    run_id: str  # 即 body.id
    session_id: str
    bcn_group_id: str
    to_bot: BotRef
    from_ref: FromRef
    message: DownlinkMessage
    extensions: dict[str, Any] | None
    timeout_ms: int = 60000


@dataclass(slots=True)
class ChatSendResult:
    """chat.send 处理结果"""

    ok: bool = True
    already_in_progress: bool = False


# ─────────────────────────── chat.inject ───────────────────────────


@dataclass(slots=True, frozen=True)
class ChatInjectInput:
    """chat.inject 请求的领域输入"""

    id: str
    session_id: str
    bcn_group_id: str
    to_bot: BotRef
    from_ref: FromRef
    message: DownlinkMessage
    timeout_ms: int = 60000


@dataclass(slots=True)
class ChatInjectResult:
    """chat.inject 处理结果"""

    ok: bool = True
    already_in_progress: bool = False


# ─────────────────────────── chat.history ───────────────────────────


@dataclass(slots=True, frozen=True)
class ChatHistoryInput:
    """chat.history 请求的领域输入"""

    id: str
    session_id: str
    bcn_group_id: str
    to_bot: BotRef
    limit: int = 50
    before: int | None = None
    after: int | None = None
    timeout_ms: int = 30000


@dataclass(slots=True, frozen=True)
class HistoryPluginMeta:
    """assistant 消息中的 plugin 元数据"""

    name: str | None = None
    call_id: str | None = None


@dataclass(slots=True, frozen=True)
class HistoryMeta:
    """引擎元数据"""

    assistant_aggregation: bool | None = None
    plugin: HistoryPluginMeta | None = None


@dataclass(slots=True, frozen=True)
class HistoryContentBlock:
    """chat.history 响应消息中的内容块"""

    type: str  # "text" | "toolCall"
    text: str | None = None
    name: str | None = None
    id: str | None = None
    arguments: str | None = None


@dataclass(slots=True, frozen=True)
class HistoryMessage:
    """chat.history 响应中的单条消息"""

    role: str
    content: str | list[HistoryContentBlock]
    timestamp: int
    id: str | None = None
    stop_reason: str | None = None
    history_meta: HistoryMeta | None = None
    tool_name: str | None = None
    tool_call_id: str | None = None
    is_error: bool | None = None
    error_message: str | None = None
    model: str | None = None


@dataclass(slots=True)
class ChatHistoryResult:
    """chat.history 处理结果"""

    ok: bool = True
    session_id: str = ""
    messages: list[HistoryMessage] = field(default_factory=list)
    has_more: bool = False
    next_before: int | None = None
    next_after: int | None = None


# ─────────────────────────── 上行协议模型 ───────────────────────────


@dataclass(slots=True, frozen=True)
class EventSpeaker:
    """上行事件消息的发送方"""

    kind: str
    id: str | None = None
    name: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """序列化为 BCN 上行协议 JSON 字段，自动省略 None 值"""
        d: dict[str, Any] = {"kind": self.kind}
        if self.id is not None:
            d["id"] = self.id
        if self.name is not None:
            d["name"] = self.name
        return d


@dataclass(slots=True, frozen=True)
class EventMessage:
    """上行事件消息体"""

    text: str
    speaker: EventSpeaker | None = None

    def to_dict(self) -> dict[str, Any]:
        """序列化为 BCN 上行协议 JSON 字段，自动省略 None 值"""
        d: dict[str, Any] = {"text": self.text}
        if self.speaker is not None:
            d["speaker"] = self.speaker.to_dict()
        return d


@dataclass(slots=True, frozen=True)
class EventUsage:
    """上行事件 token 用量"""

    prompt_tokens: int
    completion_tokens: int
    model: str | None = None
    latency_ms: int | None = None

    def to_dict(self) -> dict[str, Any]:
        """序列化为 BCN 上行协议 JSON 字段，自动省略 None 值"""
        d: dict[str, Any] = {
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
        }
        if self.model is not None:
            d["model"] = self.model
        if self.latency_ms is not None:
            d["latency_ms"] = self.latency_ms
        return d


@dataclass(slots=True, frozen=True)
class EventRouting:
    """上行事件路由信息"""

    next_provider_id: str | None = None
    next_bot_ref: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """序列化为 BCN 上行协议 JSON 字段，自动省略 None 值

        当所有路由字段均为 None 时返回空 dict，
        调用方据此判断是否写入 payload。
        """
        d: dict[str, Any] = {}
        if self.next_provider_id is not None:
            d["next_provider_id"] = self.next_provider_id
        if self.next_bot_ref is not None:
            d["next_bot_ref"] = self.next_bot_ref
        return d


@dataclass(slots=True, frozen=True)
class ChatEvent:
    """BCN 上行事件模型

    对应 BCN 协议 §7.2 POST /v1/bot/events 请求体。
    """

    run_id: str
    seq: int = 0
    state: str = "completed"
    message: EventMessage | None = None
    usage: EventUsage | None = None
    routing: EventRouting | None = None

    def to_dict(self) -> dict[str, Any]:
        """序列化为 BCN 上行协议 JSON 请求体

        自动省略值为 None 的可选字段，参考 §7.2 规范。
        """
        payload: dict[str, Any] = {
            "run_id": self.run_id,
            "seq": self.seq,
            "state": self.state,
        }
        if self.message is not None:
            payload["message"] = self.message.to_dict()
        if self.usage is not None:
            payload["usage"] = self.usage.to_dict()
        if self.routing is not None:
            routing_dict = self.routing.to_dict()
            if routing_dict:  # 只在非空时写入
                payload["routing"] = routing_dict
        return payload


@dataclass(slots=True)
class EventResponse:
    """BCN 上行事件响应"""

    ok: bool = False
    deduplicated: bool = False
