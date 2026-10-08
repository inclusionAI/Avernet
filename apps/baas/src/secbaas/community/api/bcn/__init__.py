"""BCN 下行协议领域包

定义 BCN -> Provider 下行协议 (chat.send / chat.inject / chat.history)
的服务接口、领域模型和异常类型。
"""

from ._exceptions import (
    BcnBotNotFoundError,
    BcnError,
    BcnIdempotencyConflictError,
    BcnInvalidRequestError,
    BcnProviderIdMismatchError,
    BcnRunTerminatedError,
    BcnSessionNotFoundError,
    BcnUnauthorizedError,
    BcnUnsupportedMethodError,
)
from ._models import (
    Attachment,
    BcnInteractionAnswer,
    BcnInteractionResolveInput,
    BcnInteractionResolveResult,
    BotRef,
    ChatAbortInput,
    ChatAbortResult,
    ChatEvent,
    ChatHistoryInput,
    ChatHistoryResult,
    ChatInjectInput,
    ChatInjectResult,
    ChatSendInput,
    ChatSendResult,
    ContentBlock,
    DownlinkMessage,
    EventMessage,
    EventResponse,
    EventRouting,
    EventSpeaker,
    EventUsage,
    FromRef,
    HistoryContentBlock,
    HistoryMessage,
    HistoryMeta,
    HistoryPluginMeta,
)
from ._protocols import BcnDownlinkService

__all__ = [
    # Protocol
    "BcnDownlinkService",
    # Models - 下行
    "Attachment",
    "BotRef",
    "BcnInteractionAnswer",
    "BcnInteractionResolveInput",
    "BcnInteractionResolveResult",
    "ChatAbortInput",
    "ChatAbortResult",
    "ChatHistoryInput",
    "ChatHistoryResult",
    "ChatInjectInput",
    "ChatInjectResult",
    "ChatSendInput",
    "ChatSendResult",
    "ContentBlock",
    "DownlinkMessage",
    "FromRef",
    "HistoryContentBlock",
    "HistoryMessage",
    "HistoryMeta",
    "HistoryPluginMeta",
    # Models - 上行
    "ChatEvent",
    "EventMessage",
    "EventResponse",
    "EventRouting",
    "EventSpeaker",
    "EventUsage",
    # Exceptions
    "BcnBotNotFoundError",
    "BcnError",
    "BcnIdempotencyConflictError",
    "BcnInvalidRequestError",
    "BcnProviderIdMismatchError",
    "BcnRunTerminatedError",
    "BcnSessionNotFoundError",
    "BcnUnauthorizedError",
    "BcnUnsupportedMethodError",
]
