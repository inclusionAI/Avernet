"""Bot 全局并发上限配置管理。

包含：
- ``BotConcurrencyManager``：缓存 bot_qpm_config，定期全量刷新，热更新生效。

语义：某 bot 在**全集群同时最多执行的请求数**（全局并发上限），**不是**每秒/每分钟
的请求速率。表名/字段沿用历史命名 ``qpm``（``bot_qpm_config.qpm``），仅为兼容，
不要按 "queries per minute" 理解。

真正的限流在队列层按 RUNNING 在途数强制：``claim_pending_by_bot(max_running=...)``，
Worker 把该 bot 的并发上限作为 ``max_running`` 传入即可（见 ``_worker.py``）。
"""

from __future__ import annotations

import threading
import time

from secbaas.community.core.repository.bot_qpm import BotQpmRepository
from secbaas.community.logger import get_logger

logger = get_logger("core-bot-run")


class BotConcurrencyManager:
    """Bot QPM 配置管理器：缓存 + 定期全量刷新。

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
        """返回某 bot 的全局并发限制（未配置时返回 None）。

        ``0`` 是合法的并发上限（用于暂停该 bot），必须与"未配置"区分，
        因此不能用 ``or`` 兜底到通配 ``*``。
        """
        self._maybe_refresh()
        num = self._configs.get(bot_id)
        if num is None:
            num = self._configs.get("*")
        return num

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
            logger.info(
                "[BotConcurrencyManager] refreshed %s qpm configs", len(self._configs)
            )
        except Exception as e:
            # 刷新失败不应打断限流：保留旧缓存，下个周期重试。
            self._last_refresh = time.monotonic()
            logger.error(
                "[BotConcurrencyManager] refresh failed, keep stale cache: %s", e
            )
