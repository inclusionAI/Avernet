"""SSE 流式转换插件接口定义

定义 StreamConverter 插件协议，将单个 StreamChunk 转换为
SSE 协议的 SseEvent。调用方自行控制迭代节奏。

插件化设计使得不同 Bot 引擎（BaasBotService / ClawBotService 等）
的输出可以经过不同的转换逻辑适配到统一的 SSE 协议。
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from ._models import SseEvent, StreamChunk


@runtime_checkable
class StreamConverter(Protocol):
    """SSE 流式转换插件协议

    将单个 StreamChunk 转换为 SseEvent。调用方负责迭代
    StreamChunk 流并逐个调用 convert。

    实现方负责:
    1. 维护 seq 计数（每个 run 内从 0 递增）
    2. 将 chunk.type 映射为 SSE event 类型
    3. 构造符合 SSE 协议的 data JSON
    """

    @staticmethod
    def name() -> str:
        """返回 converter 在工厂中的注册名称"""
        ...

    def convert(self, chunk: StreamChunk, *, run_id: str) -> SseEvent | None:
        """将单个 StreamChunk 转换为 SseEvent

        Args:
            chunk: 底层 Bot 产出的单个流式 chunk
            run_id: 当前 run 的 ID，用于 SSE 事件中的 run_id 字段

        Returns:
            SseEvent | None: 转换后的 SSE 事件；None 表示该 chunk 被丢弃
        """
        ...


@runtime_checkable
class SseConverterFactory(Protocol):
    def create(self, name: str | None = None) -> StreamConverter: ...
