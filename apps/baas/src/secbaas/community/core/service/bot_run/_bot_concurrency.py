"""Bot 全局并发上限配置管理。

包含：
- ``BotConcurrencyManager``：缓存 bot_qpm_config，定期全量刷新，热更新生效。

语义：某 bot 的**集群并发准入目标**，**不是**每秒/每分钟的请求速率。表名/字段沿用历史命名 ``qpm``（``bot_qpm_config.qpm``），仅为兼容，
不要按 "queries per minute" 理解。

Worker 在短查询返回后比较 RUNNING 在途数，再调用原子出队（见 ``_worker.py``）。
计数与出队不是原子配额：并发 Worker 可能同时通过，不能承诺严格的集群上限。
"""

from __future__ import annotations

import threading
import time

from secbaas.community.core.repository.bot_qpm import BotQpmRepository
from secbaas.community.logger import get_logger

logger = get_logger("core-bot-run")

#: teclaw 队列工作项在无 exact QPM 行时的全局并发默认值。
#: 该值只影响已经由既有配置/BCN 链路进入队列的 teclaw，不改变路由选择。
TECLAW_DEFAULT_CONCURRENCY = 5000


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

    def get_concurrency_num(
        self, bot_id: str, *, engine_type: str | None = None
    ) -> int | None:
        """返回某 bot 的全局并发限制（未配置时按引擎回退）。

        解析顺序：
        1. exact ``bot_id`` 行存在则立即返回，包括 ``0``（暂停语义）；
        2. exact 未配置且 engine_type 为 teclaw，返回 teclaw 默认 5000，
           不读取 ``*`` 通配，避免系统全局通配覆盖 teclaw 默认值；
        3. 非 teclaw/unknown 继续沿用既有 ``*`` 通配回退。

        ``0`` 是合法的并发上限（用于暂停该 bot），必须与"未配置"区分，
        因此不能用 ``or`` 兜底到通配 ``*``。
        """
        self._maybe_refresh()
        num = self._configs.get(bot_id)
        if num is not None:
            return num
        if engine_type == "teclaw":
            return TECLAW_DEFAULT_CONCURRENCY
        return self._configs.get("*")

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
