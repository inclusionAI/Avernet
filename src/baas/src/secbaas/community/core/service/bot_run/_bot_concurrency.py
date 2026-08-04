"""QPM 限流原语（阶段一）。

包含三部分：
- ``ConcurrencyLimiter``：进程内并发限制器，同时最多 capacity 个请求执行。
- ``BotQpmManager``：缓存 bot_qpm_config，定期全量刷新，热更新生效。
- ``MachineCountProvider``：在线 Worker 机器数来源，用于"均分 QPM"策略。

真正把三者组合成 per-bot 限制器的逻辑在 Worker（见 ``_worker.py``）：
``capacity = max(1, qpm // machine_count)``。
"""

from __future__ import annotations

import threading
import time

from secbaas.community.core.repository.bot_qpm import BotQpmRepository
from secbaas.community.logger import get_logger

logger = get_logger("core-bot-run")


class ConcurrencyLimiter:
    """并发限制器：同时最多 ``capacity`` 个请求在执行中。

    ``try_acquire`` 占用一个槽位，``release`` 归还。如果已有 capacity
    个请求在跑，``try_acquire`` 返回 False，调用方需等待重试。

    支持 ``ref_count`` 引用计数：调用方 acquire 时 +1，release 时 -1。
    当 ``ref_count == 0`` 且超过空闲 TTL 时可安全淘汰。

    ``min_interval_seconds`` 用于亚单位并发场景：当 QPM 小于机器数时，
    per_machine 并发不足 1，通过限制两次 acquire 的最小间隔来降低速率，
    使全局 TPM 可精确控制到 1。
    """

    def __init__(self, capacity: int, min_interval_seconds: float = 0.0) -> None:
        self.capacity = capacity
        self._semaphore = threading.Semaphore(capacity)
        self._ref_count = 0
        self.last_used = time.monotonic()
        self._min_interval = min_interval_seconds
        self._last_acquire_ts: float = float("-inf")

    @property
    def ref_count(self) -> int:
        return self._ref_count

    def has_slot(self) -> bool:
        """预检：是否有空闲槽位（不消费）。"""
        if self._semaphore._value <= 0:
            return False
        if self._min_interval > 0:
            elapsed = time.monotonic() - self._last_acquire_ts
            if elapsed < self._min_interval:
                return False
        return True

    def try_acquire(self) -> bool:
        """尝试占用一个槽位，成功返回 True。"""
        if not self.has_slot():
            return False
        ok = self._semaphore.acquire(blocking=False)
        if ok:
            self._ref_count += 1
            self.last_used = time.monotonic()
            self._last_acquire_ts = time.monotonic()
        return ok

    def release(self) -> None:
        """请求完成后归还槽位。"""
        self._semaphore.release()
        self._ref_count -= 1


class FixedMachineCountProvider:
    """固定机器数（最简实现）。后续可替换为 DRM 下发 / DB 心跳统计。"""

    def __init__(self, count: int = 1) -> None:
        self._count = max(1, count)

    def get_machine_count(self) -> int:
        return self._count


class BotConcurrencyManager:
    """Bot PM 配置管理器：缓存 + 定期全量刷新。

    线程安全：Worker 主循环（可能在独立线程/事件循环）会并发读，刷新时整体替换
    dict 引用，读侧无需加锁即可拿到一致快照。
    """

    def __init__(
        self,
        repository: BotQpmRepository,
        refresh_interval_seconds: float = 30.0,
    ) -> None:
        self._repo = repository
        self._refresh_interval = refresh_interval_seconds
        self._configs: dict[str, int] = {}
        self._last_refresh = 0.0
        self._refresh_lock = threading.Lock()

    def get_concurrency_num(self, bot_id: str) -> int | None:
        """返回某 bot 的全局 并发限制（未配置时返回 None）。"""
        self._maybe_refresh()
        return self._configs.get(bot_id) or self._configs.get("*")

    def _maybe_refresh(self) -> None:
        now = time.monotonic()
        if now - self._last_refresh <= self._refresh_interval:
            return
        # 只让一个线程真正刷新；其他线程直接用旧快照，不阻塞。
        if not self._refresh_lock.acquire(blocking=False):
            return
        try:
            self.refresh()
        finally:
            self._refresh_lock.release()

    def refresh(self) -> None:
        """从仓库全量加载 QPM 配置并替换缓存。失败时保留旧缓存。"""
        try:
            rows = self._repo.list_all()
            self._configs = {r.bot_id: r.qpm for r in rows}
            self._last_refresh = time.monotonic()
            logger.info("[BotQpmManager] refreshed %s qpm configs", len(self._configs))
        except Exception as e:
            # 刷新失败不应打断限流：保留旧缓存，下个周期重试。
            self._last_refresh = time.monotonic()
            logger.error("[BotQpmManager] refresh failed, keep stale cache: %s", e)
