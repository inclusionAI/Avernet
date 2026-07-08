from typing import Protocol, runtime_checkable

from ._record import BotRunQueueRecord


@runtime_checkable
class BotRunQueueRepository(Protocol):
    """Bot Run 队列工作项仓库协议。

    职责：Worker 发现 / 乐观认领 / 心跳 / 串行放回 / 终态标记 / 宕机恢复 / 背压计数 /
    meta 更新。结果正文不在本表，仍写 ``baas_bot_run``（见 BotRunRepository）。
    """

    def insert_queue(
        self,
        *,
        run_id: str,
        bot_id: str,
        session_id: str | None = None,
        meta: dict | None = None,
    ) -> str:
        """入队一个工作项（status=PENDING），与 baas_bot_run 同一 run_id。"""
        ...

    def get_by_run_id(self, run_id: str) -> BotRunQueueRecord | None:
        """按 run_id 取工作项。"""
        ...

    def discover_active_bots(self, limit: int = 50) -> list[str]:
        """发现有 PENDING 工作项的 bot_id 列表（确定性顺序，避免活锁）。"""
        ...

    def claim_pending_by_bot(
        self,
        bot_id: str,
        worker_id: str,
        *,
        candidates: int = 5,
    ) -> BotRunQueueRecord | None:
        """无锁乐观认领指定 bot 的一个 PENDING 工作项（PENDING→RUNNING）。"""
        ...

    def touch_heartbeat(self, run_id: str) -> None:
        """刷新执行中工作项的心跳时间戳（供宕机恢复判活）。"""
        ...

    def release_to_pending(self, run_id: str) -> int:
        """把已置 RUNNING 但未能开跑的工作项放回 PENDING，返回受影响行数。"""
        ...

    def mark_done(self, run_id: str) -> int:
        """工作项执行写入终态后标记 DONE（RUNNING→DONE），返回受影响行数。"""
        ...

    def force_done(self, run_id: str) -> int:
        """无论 PENDING/RUNNING 直接标记 DONE（超时终结用），返回受影响行数。"""
        ...

    def reset_stale_running(self, stale_seconds: int) -> int:
        """心跳过期的 RUNNING 工作项重置为 PENDING，返回重置行数。"""
        ...

    def count_pending_by_bot(self, bot_id: str) -> int:
        """统计某 bot 的 PENDING 队列深度（供入口背压判断）。"""
        ...

    def update_meta(self, run_id: str, updates: dict) -> bool:
        """合并更新队列工作项的 meta JSON 字段，返回是否成功。"""
        ...

    def scan_timeout(self, limit: int = 200) -> list[BotRunQueueRecord]:
        """扫描 PENDING/RUNNING 中已超时的工作项（meta.timeout 已过期）。"""
        ...
