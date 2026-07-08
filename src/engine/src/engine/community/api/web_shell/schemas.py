"""Web shell router HTTP schemas."""
from __future__ import annotations

from pydantic import BaseModel


class ResizeMessage(BaseModel):
    """终端尺寸调整消息。

    历史上定义，实际上 `ws_terminal` 手动解析 JSON 帧（因为它走 WebSocket,
    不是 HTTP body）— 类保留作为消息格式的类型文档。
    """
    type: str = "resize"
    cols: int = 80
    rows: int = 24


__all__ = ["ResizeMessage"]
