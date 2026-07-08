from __future__ import annotations

from typing import Protocol, runtime_checkable

from ._record import BotRunQueueChunkRecord


@runtime_checkable
class BotRunQueueChunkRepository(Protocol):
    """baas_bot_run_queue_chunk 仓库协议。

    仅 Queue 模式流式路径使用，存储 Worker 产出的流式 chunk，
    供 HTTP handler 侧轮询消费。
    """

    def insert_chunk(
        self,
        *,
        run_id: str,
        seq: int,
        chunk_type: str,
        content: str | None = None,
        metadata: str | None = None,
    ) -> None:
        """插入一条 chunk 记录。(run_id, seq) 联合唯一，重复写 INSERT IGNORE。"""
        ...

    def get_chunks_after(
        self, run_id: str, last_seq: int, *, limit: int = 50
    ) -> list[BotRunQueueChunkRecord]:
        """获取 run_id 下 seq > last_seq 的 chunk 列表，按 seq 升序。"""
        ...

    def get_max_seq(self, run_id: str) -> int | None:
        """获取 run_id 下最大 seq，无 chunk 返回 None。"""
        ...

    def delete_chunks_by_run(self, run_id: str) -> None:
        """删除 run_id 下所有 chunk 记录。流结束后由轮询方调用清理。"""
        ...
