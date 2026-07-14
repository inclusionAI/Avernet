"""Bot Run Queue Chunk 数据模型。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class BotRunQueueChunkRecord:
    """baas_bot_run_queue_chunk 表行记录。"""

    id: int
    run_id: str
    seq: int
    chunk_type: str
    content: str | None
    metadata: str | None
