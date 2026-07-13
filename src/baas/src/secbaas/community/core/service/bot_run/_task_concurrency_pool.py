"""TaskConcurrencyPool - BotRunner 任务并发池

类比 AsyncChatClientPool 的 per-key + global cap 模型，
但管理的是并发任务槽位而非 WS 连接。

核心设计：
- 全局信号量（softmax）限制总并发任务数，默认 1000
- Per-key 信号量（per_key_max）限制同一 bot_id 的并发任务数，默认 2
- 排队上限（queue_max）限制全局排队等待数，默认 0（不限），防止协程堆积
- 超时（acquire_timeout）限制 acquire 等待时间，默认 600 秒（10 分钟），防止 session 变质
- 任务超时（task_timeout）限制单个任务的执行时长，默认 0（不限），
  超时后自动取消协程并释放槽位，让排队请求及时接管
- softmax=0 或 per_key_max=0 时对应维度不限流，零开销

BotRunner 的集成方式：
- 入口处不阻塞，在 background task 内调用 acquire() 排队等待
  - queue_max 限制排队数，超出直接 429
  - acquire_timeout 限制等待时间，超时直接 429

使用方式：
    # 方式一：slot.run()（推荐，自动超时 + 自动释放）
    slot = await pool.acquire(key=bot_id)
    await slot.run(some_async_task())  # 使用 pool 的 task_timeout
    await slot.run(some_async_task(), timeout=30)  # 覆盖超时为 30 秒
    await slot.run(some_async_task(), timeout=0)  # 不限超时

    # 方式二：手动 try/finally（不提供超时保护）
    slot = await pool.acquire(key=bot_id)
    try:
        await some_async_task()
    finally:
        slot.release()
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Coroutine
from typing import Any, TypeVar

from secbaas.community.api.bot_runtime import TooManyRequestsError
from secbaas.community.logger import get_logger

logger = get_logger("core-bot-run")

_T = TypeVar("_T")


class TaskConcurrencySlot:
    """任务并发槽位

    由 TaskConcurrencyPool.acquire() 返回。

    两种使用方式：
    1. slot.run(coro)：推荐。执行协程，超时自动取消，完成后自动释放。
    2. 手动 try/finally + slot.release()：不提供超时保护，需自行管理生命周期。

    多次调用 release() 是安全的（幂等）。
    """

    __slots__ = ("_pool", "_key", "_acquired_global", "_acquired_key", "_released")

    def __init__(
        self,
        pool: TaskConcurrencyPool,
        key: str,
        acquired_global: bool,
        acquired_key: bool,
    ) -> None:
        self._pool = pool
        self._key = key
        self._acquired_global = acquired_global
        self._acquired_key = acquired_key
        self._released = False

    def release(self) -> None:
        """释放占用的全局和 per-key 信号量槽位。

        多次调用是安全的（幂等）。
        """
        if self._released:
            return
        self._released = True
        self._pool._release_slot(
            key=self._key,
            acquired_global=self._acquired_global,
            acquired_key=self._acquired_key,
        )

    @property
    def key(self) -> str:
        return self._key

    async def run(
        self, coro: Coroutine[Any, Any, _T], timeout: int | None = None
    ) -> _T:
        """在 slot 内执行协程，超时自动取消并释放 slot。

        使用 asyncio.wait_for 限制任务执行时长。
        无论正常完成、异常、超时或取消，都会释放 slot。

        Args:
            coro: 要执行的协程
            timeout: 单次任务超时秒数，覆盖 pool 的 task_timeout。
                     传 None 时使用 pool 的 task_timeout（默认行为）；
                     传 0 表示不限超时；
                     传正数表示本次超时秒数。

        Returns:
            协程的返回值

        Raises:
            asyncio.TimeoutError: 任务执行超过超时限制
        """
        effective_timeout = timeout if timeout is not None else self._pool._task_timeout
        try:
            if effective_timeout > 0:
                return await asyncio.wait_for(coro, timeout=effective_timeout)
            else:
                return await coro
        finally:
            self.release()


class TaskConcurrencyPool:
    """BotRunner 任务并发池

    支持全局 + per-key 两种维度的并发限制，
    类似 AsyncChatClientPool 的 per-sandbox + global 模式。

    通过 acquire(key) 排队等待直到有槽位可用（用于后台任务内部）。

    Args:
        softmax: 全局最大并发任务数，默认 1000，0 表示不限
        per_key_max: 每个 key（bot_id）最大并发任务数，默认 2，0 表示不限
        queue_max: 全局最大排队等待数，默认 0（不限）。
                  当排队数已达 queue_max 时，新的 acquire() 直接抛
                  TooManyRequestsError，防止协程无限堆积。
        acquire_timeout: acquire() 等待槽位的超时秒数，默认 600.0（10 分钟）。
                  超时后抛 TooManyRequestsError，防止 session 在排队期间失效。
                  0 表示不限（向后兼容）。
        task_timeout: 单个任务的最大执行秒数，默认 0（不限）。
                  通过 slot.run(coro) 生效，用 asyncio.wait_for 包裹协程。
                  超时后抛 asyncio.TimeoutError 并自动释放 slot，
                  让排队中的请求及时接管槽位。
                  直接调用 release() 时不受此限制。
    """

    def __init__(
        self,
        softmax: int | None = 1000,
        per_key_max: int | None = 2,
        queue_max: int | None = 0,
        acquire_timeout: float | None = 600.0,
        task_timeout: float | None = 0,
    ) -> None:
        # 防御 None 和类型异常：dependency_injector 的 Configuration()
        # 在 config 中缺少对应 key 时解析为 None，覆盖 Python 默认参数值；
        # 在某些环境下也可能传入字符串形式的数值。
        # 此处统一处理：None → 默认值，非 None → 强制类型转换。
        if softmax is None:
            softmax = 1000
        else:
            softmax = int(softmax)
        if per_key_max is None:
            per_key_max = 2
        else:
            per_key_max = int(per_key_max)
        if queue_max is None:
            queue_max = 0
        else:
            queue_max = int(queue_max)
        if acquire_timeout is None:
            acquire_timeout = 600.0
        else:
            acquire_timeout = float(acquire_timeout)
        if task_timeout is None:
            task_timeout = 0
        else:
            task_timeout = float(task_timeout)

        self._softmax = softmax
        self._per_key_max = per_key_max
        self._queue_max = queue_max
        self._acquire_timeout = acquire_timeout
        self._task_timeout = task_timeout

        # 全局信号量
        self._global_sem: asyncio.Semaphore | None = (
            asyncio.Semaphore(softmax) if softmax > 0 else None
        )

        # Per-key 信号量池
        self._key_sems: dict[str, asyncio.Semaphore] = {}
        self._key_refcount: dict[str, int] = {}
        self._key_lock = asyncio.Lock()  # 保护 _key_sems 的创建

        # 自维护计数器（避免访问 Semaphore 内部属性）
        self._active_count = 0
        self._queued_count = 0

    @property
    def active_count(self) -> int:
        """当前全局活跃任务数"""
        return self._active_count

    @property
    def queue_depth(self) -> int:
        """当前排队等待的任务数"""
        return self._queued_count

    @property
    def softmax(self) -> int:
        """全局最大并发数，0 表示不限"""
        return self._softmax

    @property
    def per_key_max(self) -> int:
        """每个 key 最大并发数，0 表示不限"""
        return self._per_key_max

    @property
    def queue_max(self) -> int:
        """全局最大排队等待数，0 表示不限"""
        return self._queue_max

    @property
    def acquire_timeout(self) -> float:
        """acquire 超时秒数，0 表示不限"""
        return self._acquire_timeout

    @property
    def task_timeout(self) -> float:
        """任务执行超时秒数，0 表示不限"""
        return self._task_timeout

    def per_key_active_count(self, key: str) -> int:
        """指定 key 的当前活跃任务数"""
        return self._key_refcount.get(key, 0)

    def per_key_stats(self) -> dict[str, int]:
        """所有 key 的活跃任务数快照"""
        return dict(self._key_refcount)

    @property
    def is_noop(self) -> bool:
        """是否为无限制模式（softmax=0 且 per_key_max=0）"""
        return self._global_sem is None and self._per_key_max <= 0

    async def acquire(self, key: str) -> TaskConcurrencySlot:
        """获取并发槽位，排队等待直到有槽位可用。

        适用于后台任务内部（queue 语义），不阻塞 HTTP 响应。
        先快速返回 run_id/message_id，在 background task 内排队等待执行。

        受 queue_max 限制：当排队数已达上限时直接抛
        TooManyRequestsError，防止协程无限堆积。
        受 acquire_timeout 限制：等待超时后抛
        TooManyRequestsError，防止 session 在排队期间失效。

        Args:
            key: 分组键（通常为 bot_id）

        Returns:
            TaskConcurrencySlot，任务完成后需调用 release()

        Raises:
            TooManyRequestsError: 排队数超限（queue_max）或等待超时（acquire_timeout）
        """
        acquired_global = False
        acquired_key = False

        # 快速路径：两侧都不限流
        if self._global_sem is None and self._per_key_max <= 0:
            self._active_count += 1
            self._key_refcount[key] = self._key_refcount.get(key, 0) + 1
            return TaskConcurrencySlot(
                pool=self, key=key, acquired_global=False, acquired_key=True
            )

        # queue_max 检查：排队数已达上限时直接拒绝
        if self._queue_max > 0 and self._queued_count >= self._queue_max:
            logger.warning(
                "[TaskConcurrencyPool] Rejected (queue_max): "
                "key=%s, queued=%d, queue_max=%d",
                key,
                self._queued_count,
                self._queue_max,
            )
            raise TooManyRequestsError(
                bot_id=key, active=self._active_count, limit=self._queue_max
            )

        # 计算 deadline（acquire_timeout 跨两个信号量共享）
        deadline = (
            time.monotonic() + self._acquire_timeout
            if self._acquire_timeout > 0
            else None
        )

        # 1. 获取全局信号量（排队等待，带超时）
        if self._global_sem is not None:
            self._queued_count += 1
            try:
                if deadline is not None:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise TooManyRequestsError(
                            bot_id=key,
                            active=self._active_count,
                            limit=self._softmax,
                        )
                    try:
                        await asyncio.wait_for(
                            self._global_sem.acquire(),
                            timeout=remaining,
                        )
                    except TimeoutError:
                        logger.warning(
                            "[TaskConcurrencyPool] Timeout (global sem): "
                            "key=%s, active=%d, softmax=%d, timeout=%.1fs",
                            key,
                            self._active_count,
                            self._softmax,
                            self._acquire_timeout,
                        )
                        raise TooManyRequestsError(
                            bot_id=key,
                            active=self._active_count,
                            limit=self._softmax,
                        )
                else:
                    await self._global_sem.acquire()
            except TooManyRequestsError:
                # 超时或 queue_max 触发的 TooManyRequestsError 需要恢复计数
                self._queued_count -= 1
                raise
            except Exception:
                self._queued_count -= 1
                raise
            self._queued_count -= 1
            acquired_global = True

        # 2. 获取 per-key 信号量（排队等待，带剩余超时）
        if self._per_key_max > 0:
            key_sem = await self._get_or_create_key_sem(key)
            self._queued_count += 1
            try:
                if deadline is not None:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        # 全局信号量已获取但 per-key 超时，rollback 全局
                        if acquired_global and self._global_sem is not None:
                            self._global_sem.release()
                        logger.warning(
                            "[TaskConcurrencyPool] Timeout (per-key sem): "
                            "key=%s, no time remaining after global acquire",
                            key,
                        )
                        raise TooManyRequestsError(
                            bot_id=key,
                            active=self._key_refcount.get(key, 0),
                            limit=self._per_key_max,
                        )
                    try:
                        await asyncio.wait_for(
                            key_sem.acquire(),
                            timeout=remaining,
                        )
                    except TimeoutError:
                        # per-key 超时，rollback 全局信号量
                        if acquired_global and self._global_sem is not None:
                            self._global_sem.release()
                        logger.warning(
                            "[TaskConcurrencyPool] Timeout (per-key sem): "
                            "key=%s, active=%d, per_key_max=%d, "
                            "remaining=%.2fs",
                            key,
                            self._key_refcount.get(key, 0),
                            self._per_key_max,
                            remaining,
                        )
                        raise TooManyRequestsError(
                            bot_id=key,
                            active=self._key_refcount.get(key, 0),
                            limit=self._per_key_max,
                        )
                else:
                    await key_sem.acquire()
            except TooManyRequestsError:
                self._queued_count -= 1
                raise
            except Exception:
                # acquire 失败时 rollback 全局信号量
                if acquired_global and self._global_sem is not None:
                    self._global_sem.release()
                self._queued_count -= 1
                raise
            self._queued_count -= 1
            acquired_key = True

        # 3. 成功获取两侧槽位，更新计数
        self._active_count += 1
        self._key_refcount[key] = self._key_refcount.get(key, 0) + 1

        return TaskConcurrencySlot(
            pool=self,
            key=key,
            acquired_global=acquired_global,
            acquired_key=acquired_key,
        )

    def _release_slot(
        self, key: str, acquired_global: bool, acquired_key: bool
    ) -> None:
        """释放槽位（由 TaskConcurrencySlot.release() 调用）"""
        # 1. 释放 per-key 信号量
        if acquired_key and self._per_key_max > 0:
            key_sem = self._key_sems.get(key)
            if key_sem is not None:
                key_sem.release()

        # 2. 统一维护 refcount（不受 acquired_key 条件限制，
        #    确保 per_key_max=0 + global_sem 存在时也能正确递减）
        count = self._key_refcount.get(key, 0)
        if count > 0:
            count -= 1
            if count <= 0:
                self._key_refcount.pop(key, None)
                # 注意：不在 release 时清理 _key_sems。
                # key_sem.release() 会唤醒等待中的协程，但被唤醒的协程
                # 不会立即执行（asyncio 单线程），当前同步函数会先继续
                # 执行完毕。如果此时清理 _key_sems，后续 acquire 会
                # 创建新信号量，而被唤醒的协程仍持有旧信号量引用，导致
                # per-key 并发限制被突破。
            else:
                self._key_refcount[key] = count

        # 3. 释放全局信号量
        if acquired_global and self._global_sem is not None:
            self._global_sem.release()

        # 4. 更新全局计数
        if self._active_count > 0:
            self._active_count -= 1

        logger.debug(
            "[TaskConcurrencyPool] Released: key=%s, active=%d, queued=%d",
            key,
            self._active_count,
            self._queued_count,
        )

    # 惰性清理阈值：当 _key_sems 中的条目数超过活跃 key 数的
    # 此倍数时，在 _get_or_create_key_sem（带锁版本）中清理
    # refcount 已归零的闲置信号量。
    _KEY_SEMS_LAZY_CLEANUP_RATIO = 4

    async def _get_or_create_key_sem(self, key: str) -> asyncio.Semaphore:
        """获取或创建 per-key 信号量（带锁保护，用于 acquire）

        当 _key_sems 的条目数远超活跃 key 数时，惰性清理
        refcount 已归零的闲置信号量，防止长期运行后内存无限增长。
        清理在锁内执行，与 release 无竞争。
        """
        sem = self._key_sems.get(key)
        if sem is not None:
            return sem

        async with self._key_lock:
            # double-check
            sem = self._key_sems.get(key)
            if sem is not None:
                return sem

            # 惰性清理：移除 refcount 已归零的闲置信号量
            if (
                len(self._key_sems)
                > len(self._key_refcount) * self._KEY_SEMS_LAZY_CLEANUP_RATIO
            ):
                stale_keys = [k for k in self._key_sems if k not in self._key_refcount]
                for k in stale_keys:
                    del self._key_sems[k]
                if stale_keys:
                    logger.debug(
                        "[TaskConcurrencyPool] Lazy cleanup: "
                        "removed %d idle key semaphores, remaining=%d",
                        len(stale_keys),
                        len(self._key_sems),
                    )

            sem = asyncio.Semaphore(self._per_key_max)
            self._key_sems[key] = sem
            return sem

    async def close(self) -> None:
        """关闭池，清理资源

        当前实现中信号量为纯内存对象，无需显式关闭。
        此方法保留用于未来扩展（如取消等待者、记录指标）。
        """
        logger.info(
            "[TaskConcurrencyPool] Closing: active=%d, queued=%d, keys=%d",
            self._active_count,
            self._queued_count,
            len(self._key_refcount),
        )
        if self._active_count > 0:
            logger.warning(
                "[TaskConcurrencyPool] Closing with %d active tasks",
                self._active_count,
            )
