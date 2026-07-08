"""默认 StreamConverter 实现

由 SseConverterFactory 通过 DI 注入，按名称实例化。

转换逻辑:
  - chunk.type == "delta"  → SSE event: delta  (增量文本)
  - chunk.type == "final"  → SSE event: final  (最终完整文本 + usage)
  - chunk.type == "error"  → SSE event: error  (错误信息)
  - chunk.type == "usage"  → 缓存 usage，合并到下一个非 usage 事件
"""

from __future__ import annotations

import json
from typing import Any

from secbaas.api.sse import SseEvent, StreamChunk


class DefaultStreamConverter:
    """默认 BCN SSE 流式转换器

    直接透传 StreamChunk 的 text 内容为 BCN SSE 事件，
    维护 seq 计数。每次 convert 调用转换一个 chunk。
    """

    def __init__(self) -> None:
        self._seq: int = 0
        self._pending_usage: dict[str, Any] | None = None

    @staticmethod
    def name():
        return "default"

    def convert(self, chunk: StreamChunk, *, run_id: str) -> SseEvent:
        """将单个 StreamChunk 转换为 SseEvent

        Args:
            chunk: 底层 Bot 产出的单个流式 chunk
            run_id: 当前 run 的 ID

        Returns:
            SseEvent: 转换后的 SSE 事件
        """
        if chunk.type == "usage" and chunk.usage:
            self._pending_usage = chunk.usage
            # usage 不产生独立 SSE 事件，返回空 delta 占位
            self._seq += 1
            return SseEvent(
                event="delta",
                data=json.dumps(
                    {"run_id": run_id, "seq": self._seq, "content": ""},
                    ensure_ascii=False,
                ),
            )

        self._seq += 1

        if chunk.type == "delta":
            return SseEvent(
                event="delta",
                data=json.dumps(
                    {"run_id": run_id, "seq": self._seq, "content": chunk.content},
                    ensure_ascii=False,
                ),
            )

        if chunk.type == "final":
            payload: dict[str, Any] = {
                "run_id": run_id,
                "seq": self._seq,
                "content": chunk.content,
            }
            usage = chunk.usage or self._pending_usage
            if usage:
                payload["usage"] = usage
            return SseEvent(
                event="final",
                data=json.dumps(payload, ensure_ascii=False),
            )

        if chunk.type == "error":
            error_info: dict[str, Any] = {
                "code": "BOT_EXECUTION_ERROR",
                "message": chunk.content or "Unknown error",
            }
            if chunk.metadata and "error_code" in chunk.metadata:
                error_info["code"] = chunk.metadata["error_code"]
            return SseEvent(
                event="error",
                data=json.dumps(
                    {"run_id": run_id, "seq": self._seq, "error": error_info},
                    ensure_ascii=False,
                ),
            )

        # 未知 chunk 类型，作为 delta 透传
        return SseEvent(
            event="delta",
            data=json.dumps(
                {"run_id": run_id, "seq": self._seq, "content": chunk.content},
                ensure_ascii=False,
            ),
        )
