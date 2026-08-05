"""_SessionState - Per-sessionKey 状态数据类。

从 _async_chat_client 中抽取，避免与 _session_key_matcher 产生循环导入。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any


@dataclass
class _SessionState:
    """Per-sessionKey 状态，用于多路复用场景下隔离不同会话的消息。"""

    content: str = ""
    state: str = ""
    agent_payloads: list[dict[str, Any]] = field(default_factory=list)
    last_stream_is_assistant: bool = False
    chat_complete: asyncio.Event = field(default_factory=asyncio.Event)
    agent_complete: asyncio.Event = field(default_factory=asyncio.Event)
    # 请求注册时的 trace context，用于回调中恢复 trace 关联。
    # 类型由 TracerPlugin 实现决定（OTel Context / OpenTracing span 等）。
    trace_context: Any = None
    # 流式模式专用：_on_chat/_on_agent push StreamChunk 到此 queue，
    # send_message_stream 从中消费。仅流式请求时创建，非流式为 None。
    stream_queue: asyncio.Queue[Any] | None = None
