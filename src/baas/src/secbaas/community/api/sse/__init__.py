"""SSE 流式模型与转换插件接口

定义 StreamChunk、SseEvent 模型及 StreamConverter 插件协议。
"""

from ._heartbeat import (
    HEARTBEAT_SSE,
    convert_chunks_to_sse,
    with_sse_heartbeat,
)
from ._models import SseEvent, StreamChunk
from ._protocol import SseConverterFactory, StreamConverter

__all__ = [
    "HEARTBEAT_SSE",
    "SseEvent",
    "StreamChunk",
    "StreamConverter",
    "SseConverterFactory",
    "convert_chunks_to_sse",
    "with_sse_heartbeat",
]
