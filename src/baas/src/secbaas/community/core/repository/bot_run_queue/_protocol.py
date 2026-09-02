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

    def touch_heartbeat(self, run_id: str, worker_id: str) -> None:
        """刷新当前 Worker 持有的 RUNNING 工作项心跳（供宕机恢复判活）。"""
        ...

    def release_to_pending(self, run_id: str, worker_id: str) -> int:
        """把当前 Worker 持有的 RUNNING 工作项放回 PENDING，返回受影响行数。"""
        ...

    def mark_done(self, run_id: str, worker_id: str) -> int:
        """当前 Worker 执行写入终态后标记 DONE（RUNNING→DONE），返回受影响行数。"""
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

    def find_running_by_session(self, session_id: str) -> list[BotRunQueueRecord]:
        """按 session_id 查询所有未终结（PENDING/RUNNING）的工作项。

        供 chat.abort 按 session 定位待取消的 run。``session_id`` 在首条消息
        入队时可能为 NULL（``update_session_id`` 在首条后才合并），该 session 下
        返回空，调用方按 best-effort 处理。
        """
        ...

    def find_terminal_by_session(self, session_id: str) -> list[BotRunQueueRecord]:
        """按 session_id 查询所有已终结（DONE）的队列工作项。

        用于 chat.abort 区分"session 无任何 run 记录"与"run 已终态"：前者返回
        200 ``{aborted: false}``，后者返回 410 ``run_terminated``。DONE 行在 TTL
        清理前可见。
        """
        ...

    def find_running_by_bot_session(
        self, session_id: str, bot_id: str
    ) -> list[BotRunQueueRecord]:
        """按 (bot_id, session_id) 维度查询所有 RUNNING 的队列工作项。

        供群聊 ``chat.abort`` 精确定位目标 bot 的 RUNNING run，避免误杀同 session
        下其它 bot 的 run。``session_id`` 或 ``bot_id`` 为空时返回空列表。PENDING
        工作项不命中，由超时扫描路径兜底。
        """
        ...

    def find_terminal_by_bot_session(
        self, session_id: str, bot_id: str
    ) -> list[BotRunQueueRecord]:
        """按 (bot_id, session_id) 维度查询所有已终结（DONE）的队列工作项。

        用于群聊 ``chat.abort`` 维度收窄后的"已终结"判定（410 vs 200），仅参考
        目标 bot 的终态记录，不被同 session 其它 bot 的终态记录影响。
        """
        ...
