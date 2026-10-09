"""SSE 流式数据模型"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(slots=True, frozen=True)
class StreamChunk:
    """Bot 执行产生的原始流式 chunk

    由 BotRunner / BotService 在流式执行过程中产出，
    交由 StreamConverter 插件转换为 SSE 事件。

    这是与底层引擎无关的中间表示，所有类型的 Bot
    （BaasBotService / ClawBotService 等）统一产出此模型。
    """

    type: str  # "delta" | "final" | "error" | "usage" | "heartbeat"
    content: str = ""
    usage: dict[str, Any] | None = None
    metadata: dict[str, Any] | None = None
    engine_type: str | None = None


@dataclass(slots=True, frozen=True)
class SseEvent:
    """SSE 协议事件

    由 StreamConverter 转换产出的 SSE 事件，
    包含 event 类型、data JSON 字符串以及可选的 retry 指令。
    Router 层直接将此模型序列化为 SSE 文本帧。
    """

    event: str  # "delta" | "final" | "error"
    data: str  # JSON 字符串
    id: str | None = None
    retry: int | None = None

    def to_sse(self) -> str:
        """序列化为 SSE 文本帧（以 \\n\\n 结尾）

        当 event 以 ":" 开头时输出 SSE 注释帧（如心跳），不设 event/data 行。
        """
        if self.event.startswith(":"):
            return f"{self.event}\n\n"
        lines: list[str] = []
        if self.id is not None:
            lines.append(f"id: {self.id}")
        if self.retry is not None:
            lines.append(f"retry: {self.retry}")
        lines.append(f"event: {self.event}")
        lines.append(f"data: {self.data}")
        return "\n".join(lines) + "\n\n"
