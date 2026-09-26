"""Unit tests for TaskConcurrencyPool.

Covers:
- No-op path (softmax=0, per_key_max=0)
- Queue strategy: blocking, release, fairness
- Per-key isolation: different keys independent
- Combined global + per-key
- Release semantics: idempotent, refcount cleanup
- Observability: active_count, queue_depth, per_key_active_count, per_key_stats
- queue_max: limit on number of queued waiters
- acquire_timeout: timeout on acquire waiting
- slot.run(): task execution with timeout + automatic release
"""

import asyncio

import pytest

from secbaas.community.api.bot_runtime import TooManyRequestsError
from secbaas.community.core.service.bot_run._task_concurrency_pool import (
    TaskConcurrencyPool,
)

# ==================== No-op path (disabled limits) ====================


class TestNoOpPath:
    """softmax=0 和 per_key_max=0 时不限流，acquire 立即返回。"""

    @pytest.mark.asyncio
    async def test_acquire_returns_slot_immediately(self):
        pool = TaskConcurrencyPool(softmax=0, per_key_max=0)
        slot = await pool.acquire(key="bot-1")
        assert slot is not None
        assert slot.key == "bot-1"
        assert pool.active_count == 1
        slot.release()
        assert pool.active_count == 0

    @pytest.mark.asyncio
    async def test_multiple_acquires_no_limit(self):
        pool = TaskConcurrencyPool(softmax=0, per_key_max=0)
        slots = []
        for i in range(100):
            slot = await pool.acquire(key="bot-1")
            slots.append(slot)
        assert pool.active_count == 100
        for slot in slots:
            slot.release()
        assert pool.active_count == 0

    @pytest.mark.asyncio
    async def test_per_key_count_with_no_limits(self):
        pool = TaskConcurrencyPool(softmax=0, per_key_max=0)
        slot_a = await pool.acquire(key="bot-a")
        slot_b = await pool.acquire(key="bot-b")
        assert pool.per_key_active_count("bot-a") == 1
        assert pool.per_key_active_count("bot-b") == 1
        slot_a.release()
        assert pool.per_key_active_count("bot-a") == 0
        slot_b.release()
        assert pool.per_key_active_count("bot-b") == 0


# ==================== Queue strategy - global ====================


class TestQueueGlobal:
    """Queue 策略下全局信号量测试。"""

    @pytest.mark.asyncio
    async def test_softmax_blocks_and_releases(self):
        pool = TaskConcurrencyPool(softmax=2, per_key_max=0)
        slot1 = await pool.acquire(key="bot-1")
        slot2 = await pool.acquire(key="bot-1")
        assert pool.active_count == 2

        # 第 3 个 acquire 应该阻塞
        acquired = asyncio.Event()
        slot3_holder = None

        async def try_acquire():
            nonlocal slot3_holder
            slot3_holder = await pool.acquire(key="bot-1")
            acquired.set()

        task = asyncio.create_task(try_acquire())
        # 给一点时间确保 task 已经开始等待
        await asyncio.sleep(0.05)
        assert not acquired.is_set()
        assert pool.queue_depth > 0

        # 释放一个槽位
        slot1.release()
        await asyncio.sleep(0.05)
        assert acquired.is_set()
        assert pool.active_count == 2
        task.cancel()
        if slot3_holder is not None:
            slot3_holder.release()
        slot2.release()

    @pytest.mark.asyncio
    async def test_release_idempotent(self):
        pool = TaskConcurrencyPool(softmax=1, per_key_max=0)
        slot = await pool.acquire(key="bot-1")
        assert pool.active_count == 1
        slot.release()
        assert pool.active_count == 0
        # 多次释放幂等
        slot.release()
        slot.release()
        assert pool.active_count == 0

        # 释放后可以再次 acquire
        slot2 = await pool.acquire(key="bot-1")
        assert pool.active_count == 1
        slot2.release()


# ==================== Per-key limiting ====================


class TestPerKeyLimit:
    """Per-key 信号量测试。"""

    @pytest.mark.asyncio
    async def test_per_key_queue_blocks(self):
        """Per-key 限制排队。"""
        pool = TaskConcurrencyPool(softmax=0, per_key_max=1)
        slot1 = await pool.acquire(key="bot-a")

        acquired = asyncio.Event()
        slot2_holder = None

        async def try_acquire():
            nonlocal slot2_holder
            slot2_holder = await pool.acquire(key="bot-a")
            acquired.set()

        task = asyncio.create_task(try_acquire())
        await asyncio.sleep(0.05)
        assert not acquired.is_set()

        slot1.release()
        await asyncio.sleep(0.05)
        assert acquired.is_set()
        assert pool.per_key_active_count("bot-a") == 1
        if slot2_holder is not None:
            slot2_holder.release()
        task.cancel()

    @pytest.mark.asyncio
    async def test_per_key_refcount_cleanup(self):
        """refcount 归零时清理 per-key semaphore。"""
        pool = TaskConcurrencyPool(softmax=0, per_key_max=2)
        slot1 = await pool.acquire(key="bot-x")
        slot2 = await pool.acquire(key="bot-x")
        assert pool.per_key_active_count("bot-x") == 2
        assert "bot-x" in pool.per_key_stats()

        slot1.release()
        assert pool.per_key_active_count("bot-x") == 1
        slot2.release()
        assert pool.per_key_active_count("bot-x") == 0
        # refcount 归零后清理
        assert "bot-x" not in pool.per_key_stats()


# ==================== Combined global + per-key ====================


class TestCombinedGlobalAndPerKey:
    """全局 + per-key 组合测试。"""

    @pytest.mark.asyncio
    async def test_queue_combined(self):
        """全局+per-key 都起作用。"""
        pool = TaskConcurrencyPool(softmax=10, per_key_max=1)
        slot_a = await pool.acquire(key="bot-a")
        # bot-a 的 per-key 限制为 1，第二个 bot-a 请求应排队
        acquired = asyncio.Event()
        slot_a2_holder = None

        async def try_acquire():
            nonlocal slot_a2_holder
            slot_a2_holder = await pool.acquire(key="bot-a")
            acquired.set()

        task = asyncio.create_task(try_acquire())
        await asyncio.sleep(0.05)
        assert not acquired.is_set()

        slot_a.release()
        await asyncio.sleep(0.05)
        assert acquired.is_set()
        if slot_a2_holder is not None:
            slot_a2_holder.release()
        task.cancel()


# ==================== queue_max ====================


class TestQueueMax:
    """queue_max 限制全局排队等待数。"""

    @pytest.mark.asyncio
    async def test_queue_max_rejects_when_full(self):
        """排队数达到 queue_max 时，新的 acquire 直接拒绝。"""
        pool = TaskConcurrencyPool(softmax=1, per_key_max=0, queue_max=2)
        # 占满并发槽位
        slot1 = await pool.acquire(key="bot-1")
        assert pool.active_count == 1

        # 启动 2 个排队者（达到 queue_max）
        waiters = []
        for _ in range(2):
            ev = asyncio.Event()
            holder = []

            async def wait_and_set(e=ev, h=holder):
                s = await pool.acquire(key="bot-1")
                h.append(s)
                e.set()

            waiters.append(asyncio.create_task(wait_and_set()))
        await asyncio.sleep(0.05)
        assert pool.queue_depth == 2

        # 第 3 个排队者应被 queue_max 拒绝
        with pytest.raises(TooManyRequestsError) as exc_info:
            await pool.acquire(key="bot-1")
        assert exc_info.value.limit == 2  # queue_max

        # 清理
        slot1.release()
        await asyncio.sleep(0.05)
        for w in waiters:
            w.cancel()
            try:
                await w
            except (asyncio.CancelledError, TooManyRequestsError):
                pass

    @pytest.mark.asyncio
    async def test_queue_max_zero_means_no_limit(self):
        """queue_max=0 时不限制排队数。"""
        pool = TaskConcurrencyPool(softmax=1, per_key_max=0, queue_max=0)
        slot1 = await pool.acquire(key="bot-1")

        # 启动很多排队者，不应被拒绝
        waiters = []
        for _ in range(20):
            holder = []

            async def wait_and_set(h=holder):
                s = await pool.acquire(key="bot-1")
                h.append(s)

            waiters.append(asyncio.create_task(wait_and_set()))
        await asyncio.sleep(0.05)

        # 没有拒绝，所有排队者都在等待
        assert pool.queue_depth == 20

        # 清理
        slot1.release()
        await asyncio.sleep(0.1)
        for w in waiters:
            w.cancel()
            try:
                await w
            except (asyncio.CancelledError, TooManyRequestsError):
                pass

    @pytest.mark.asyncio
    async def test_queue_max_property(self):
        pool = TaskConcurrencyPool(softmax=10, queue_max=100)
        assert pool.queue_max == 100
        pool2 = TaskConcurrencyPool(softmax=10, queue_max=0)
        assert pool2.queue_max == 0


# ==================== acquire_timeout ====================


class TestAcquireTimeout:
    """acquire_timeout 限制 acquire 等待时间。"""

    @pytest.mark.asyncio
    async def test_timeout_raises_too_many_requests(self):
        """超时后抛出 TooManyRequestsError。"""
        pool = TaskConcurrencyPool(softmax=1, per_key_max=0, acquire_timeout=0.1)
        slot1 = await pool.acquire(key="bot-1")
        # 第二个请求应超时
        with pytest.raises(TooManyRequestsError):
            await pool.acquire(key="bot-1")
        slot1.release()

    @pytest.mark.asyncio
    async def test_timeout_zero_means_no_limit(self):
        """acquire_timeout=0 时不限制等待时间。"""
        pool = TaskConcurrencyPool(softmax=1, per_key_max=0, acquire_timeout=0)
        slot1 = await pool.acquire(key="bot-1")

        # 应该可以等（不会超时），但只等 0.1 秒就释放
        acquired = asyncio.Event()
        slot2_holder = None

        async def wait_for_slot():
            nonlocal slot2_holder
            slot2_holder = await pool.acquire(key="bot-1")
            acquired.set()

        task = asyncio.create_task(wait_for_slot())
        await asyncio.sleep(0.05)
        assert not acquired.is_set()  # 还在等

        slot1.release()
        await asyncio.sleep(0.05)
        assert acquired.is_set()  # 获取到了
        if slot2_holder is not None:
            slot2_holder.release()
        task.cancel()

    @pytest.mark.asyncio
    async def test_timeout_per_key(self):
        """per-key 超时也触发 TooManyRequestsError。"""
        pool = TaskConcurrencyPool(softmax=10, per_key_max=1, acquire_timeout=0.1)
        slot1 = await pool.acquire(key="bot-1")
        # 同一 bot_id 的第二个请求应超时（per-key 限制为 1）
        with pytest.raises(TooManyRequestsError):
            await pool.acquire(key="bot-1")
        slot1.release()

    @pytest.mark.asyncio
    async def test_timeout_releases_global_on_per_key_timeout(self):
        """per-key 超时时，已获取的全局信号量被回滚。"""
        pool = TaskConcurrencyPool(softmax=2, per_key_max=1, acquire_timeout=0.1)
        slot1 = await pool.acquire(key="bot-1")
        # 同一 bot_id 的第二个请求：全局成功，per-key 超时
        with pytest.raises(TooManyRequestsError):
            await pool.acquire(key="bot-1")
        # 全局信号量应该被回滚，其他 bot 可以获取
        slot2 = await pool.acquire(key="bot-2")
        assert pool.active_count == 2
        slot1.release()
        slot2.release()

    @pytest.mark.asyncio
    async def test_acquire_timeout_property(self):
        pool = TaskConcurrencyPool(softmax=10, acquire_timeout=30.0)
        assert pool.acquire_timeout == 30.0
        pool2 = TaskConcurrencyPool(softmax=10, acquire_timeout=0)
        assert pool2.acquire_timeout == 0

    @pytest.mark.asyncio
    async def test_noop_path_not_affected_by_timeout(self):
        """softmax=0 和 per_key_max=0 时，timeout 不生效（快速路径直接返回）。"""
        pool = TaskConcurrencyPool(softmax=0, per_key_max=0, acquire_timeout=0.001)
        # 快速路径不走信号量等待，不受 timeout 影响
        slot = await pool.acquire(key="bot-1")
        assert slot is not None
        slot.release()


# ==================== queue_max + acquire_timeout combined ====================


class TestQueueMaxWithTimeout:
    """queue_max 和 acquire_timeout 组合测试。"""

    @pytest.mark.asyncio
    async def test_queue_max_checked_before_timeout(self):
        """queue_max 优先于 timeout 检查：排队满时立即拒绝，不等超时。"""
        pool = TaskConcurrencyPool(
            softmax=1,
            per_key_max=0,
            queue_max=1,
            acquire_timeout=10.0,  # 很长，不会超时
        )
        slot1 = await pool.acquire(key="bot-1")

        # 启动 1 个排队者（达到 queue_max）
        waiter = asyncio.create_task(pool.acquire(key="bot-1"))
        await asyncio.sleep(0.05)
        assert pool.queue_depth == 1

        # 第 2 个排队者应被 queue_max 立即拒绝（不等超时）
        with pytest.raises(TooManyRequestsError) as exc_info:
            await pool.acquire(key="bot-1")
        assert exc_info.value.limit == 1  # queue_max

        # 清理
        slot1.release()
        waiter.cancel()
        try:
            await waiter
        except (asyncio.CancelledError, TooManyRequestsError):
            pass


# ==================== Observability ====================


class TestObservability:
    """可观测性测试。"""

    @pytest.mark.asyncio
    async def test_active_count(self):
        pool = TaskConcurrencyPool(softmax=5, per_key_max=2)
        assert pool.active_count == 0
        s1 = await pool.acquire(key="a")
        assert pool.active_count == 1
        s2 = await pool.acquire(key="a")
        assert pool.active_count == 2
        s1.release()
        assert pool.active_count == 1
        s2.release()
        assert pool.active_count == 0

    @pytest.mark.asyncio
    async def test_per_key_stats(self):
        pool = TaskConcurrencyPool(softmax=0, per_key_max=2)
        s1 = await pool.acquire(key="x")
        s2 = await pool.acquire(key="y")
        stats = pool.per_key_stats()
        assert stats == {"x": 1, "y": 1}
        s1.release()
        stats = pool.per_key_stats()
        assert stats == {"y": 1}
        s2.release()
        stats = pool.per_key_stats()
        assert stats == {}

    @pytest.mark.asyncio
    async def test_queue_depth(self):
        pool = TaskConcurrencyPool(softmax=1, per_key_max=0)
        assert pool.queue_depth == 0
        s1 = await pool.acquire(key="a")

        # 启动一个排队的协程
        async def waiter():
            await pool.acquire(key="a")

        task = asyncio.create_task(waiter())
        await asyncio.sleep(0.05)
        assert pool.queue_depth >= 1

        s1.release()
        await asyncio.sleep(0.05)
        # queue_depth 在 acquire 成功后减 1
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    @pytest.mark.asyncio
    async def test_softmax_and_per_key_max_properties(self):
        pool = TaskConcurrencyPool(softmax=10, per_key_max=3)
        assert pool.softmax == 10
        assert pool.per_key_max == 3


# ==================== Concurrency stress ====================


class TestConcurrencyStress:
    """并发压力测试。"""

    @pytest.mark.asyncio
    async def test_high_concurrency_queue(self):
        """高并发 acquire/release 不泄漏。"""
        pool = TaskConcurrencyPool(softmax=5, per_key_max=2)
        n = 50

        async def work(bot_id: str):
            slot = await pool.acquire(key=bot_id)
            await asyncio.sleep(0.001)
            slot.release()

        tasks = [asyncio.create_task(work(f"bot-{i % 5}")) for i in range(n)]
        await asyncio.gather(*tasks)

        assert pool.active_count == 0
        assert pool.queue_depth == 0
        assert pool.per_key_stats() == {}

    @pytest.mark.asyncio
    async def test_release_on_unreleased_slots_after_exception(self):
        """模拟异常场景下 slot 释放不泄漏。"""
        pool = TaskConcurrencyPool(softmax=2, per_key_max=1)

        # 模拟 slot 被获取后未释放（异常场景）
        slot = await pool.acquire(key="bot-1")
        assert pool.active_count == 1

        # 手动释放（模拟 finally 调用）
        slot.release()
        assert pool.active_count == 0

        # 确认可以再次获取
        slot2 = await pool.acquire(key="bot-1")
        assert pool.active_count == 1
        slot2.release()


# ==================== close() ====================


class TestClose:
    @pytest.mark.asyncio
    async def test_close_with_active_tasks_logs_warning(self, caplog):
        """close() 时有活跃任务不抛异常，只记录日志。"""
        pool = TaskConcurrencyPool(softmax=1, per_key_max=0)
        slot = await pool.acquire(key="bot-1")
        await pool.close()  # 不应抛异常
        slot.release()

    @pytest.mark.asyncio
    async def test_close_idle(self):
        """close() 空池正常通过。"""
        pool = TaskConcurrencyPool(softmax=1, per_key_max=0)
        await pool.close()


# ==================== slot.run() ====================


class TestSlotRun:
    """slot.run() 方法测试：任务执行超时 + 自动释放。"""

    @pytest.mark.asyncio
    async def test_run_executes_and_releases(self):
        """run() 正常执行协程后自动释放 slot。"""
        pool = TaskConcurrencyPool(softmax=1, per_key_max=0, task_timeout=10.0)
        slot = await pool.acquire(key="bot-1")
        assert pool.active_count == 1

        result = await slot.run(asyncio.sleep(0.01, result="ok"))
        assert result == "ok"
        # run() 完成后自动释放
        assert pool.active_count == 0

    @pytest.mark.asyncio
    async def test_run_timeout_cancels_and_releases(self):
        """run() 超时后抛 TimeoutError 并自动释放 slot。"""
        pool = TaskConcurrencyPool(softmax=1, per_key_max=0, task_timeout=0.05)
        slot = await pool.acquire(key="bot-1")
        assert pool.active_count == 1

        with pytest.raises(asyncio.TimeoutError):
            await slot.run(asyncio.sleep(10))  # 模拟长时间任务

        # 超时取消后 slot 自动释放
        assert pool.active_count == 0

    @pytest.mark.asyncio
    async def test_run_exception_releases(self):
        """run() 中协程抛异常时仍自动释放 slot。"""
        pool = TaskConcurrencyPool(softmax=1, per_key_max=0, task_timeout=10.0)
        slot = await pool.acquire(key="bot-1")
        assert pool.active_count == 1

        with pytest.raises(ValueError, match="test error"):

            async def fail():
                raise ValueError("test error")

            await slot.run(fail())

        # 异常后 slot 自动释放
        assert pool.active_count == 0

    @pytest.mark.asyncio
    async def test_run_no_timeout_when_zero(self):
        """task_timeout=0 时不限执行时长，run() 正常完成。"""
        pool = TaskConcurrencyPool(softmax=1, per_key_max=0, task_timeout=0)
        slot = await pool.acquire(key="bot-1")

        # 0.2 秒任务，task_timeout=0 不会超时
        result = await slot.run(asyncio.sleep(0.2, result="done"))
        assert result == "done"
        assert pool.active_count == 0

    @pytest.mark.asyncio
    async def test_run_idempotent_release(self):
        """run() 内部释放后手动 release() 不报错（幂等）。"""
        pool = TaskConcurrencyPool(softmax=1, per_key_max=0, task_timeout=10.0)
        slot = await pool.acquire(key="bot-1")
        assert pool.active_count == 1

        await slot.run(asyncio.sleep(0.01))
        assert pool.active_count == 0

        # 手动再 release 一次，不应报错
        slot.release()
        assert pool.active_count == 0

    @pytest.mark.asyncio
    async def test_run_with_noop_pool(self):
        """softmax=0, per_key_max=0 时 run() 正常工作。"""
        pool = TaskConcurrencyPool(softmax=0, per_key_max=0, task_timeout=10.0)
        slot = await pool.acquire(key="bot-1")

        result = await slot.run(asyncio.sleep(0.01, result="noop"))
        assert result == "noop"

    @pytest.mark.asyncio
    async def test_run_timeout_frees_slot_for_waiter(self):
        """run() 超时释放 slot 后，排队等待者可以获取并执行。"""
        pool = TaskConcurrencyPool(
            softmax=1, per_key_max=1, task_timeout=0.1, acquire_timeout=5.0
        )

        # 占满 slot，启动一个长时间任务
        slot1 = await pool.acquire(key="bot-1")
        assert pool.active_count == 1

        # 排队等待者
        acquired = asyncio.Event()
        slot2_holder = []

        async def wait_and_run():
            s = await pool.acquire(key="bot-1")
            slot2_holder.append(s)
            await s.run(asyncio.sleep(0.01, result="second"))
            acquired.set()

        waiter = asyncio.create_task(wait_and_run())
        await asyncio.sleep(0.05)  # 等待排队者开始等待
        assert not acquired.is_set()

        # slot1 超时释放
        with pytest.raises(asyncio.TimeoutError):
            await slot1.run(asyncio.sleep(10))

        # 等待排队者拿到 slot 并执行
        await asyncio.sleep(0.2)
        assert acquired.is_set()
        assert pool.active_count == 0

        waiter.cancel()
        try:
            await waiter
        except (TimeoutError, asyncio.CancelledError):
            pass

    @pytest.mark.asyncio
    async def test_task_timeout_property(self):
        """task_timeout 属性可读。"""
        pool = TaskConcurrencyPool(softmax=10, task_timeout=42.0)
        assert pool.task_timeout == 42.0
        pool2 = TaskConcurrencyPool(softmax=10, task_timeout=0)
        assert pool2.task_timeout == 0
