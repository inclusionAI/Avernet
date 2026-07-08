"""
测试连接限流器
"""
import asyncio

import pytest

from engine.community.shared.connection_limiter import (
    ConnectionLimiter,
    get_connection_limiter,
    reset_connection_limiter,
)


class TestConnectionLimiter:
    """测试 ConnectionLimiter 类"""

    def test_initial_count_is_zero(self) -> None:
        """初始计数为 0"""
        limiter = ConnectionLimiter()
        assert limiter.count == 0

    @pytest.mark.asyncio
    async def test_acquire_increments_count(self) -> None:
        """获取槽位时计数增加"""
        limiter = ConnectionLimiter()
        assert await limiter.try_acquire(10) is True
        assert limiter.count == 1

    @pytest.mark.asyncio
    async def test_release_decrements_count(self) -> None:
        """释放槽位时计数减少"""
        limiter = ConnectionLimiter()
        await limiter.try_acquire(10)
        assert limiter.count == 1
        await limiter.release()
        assert limiter.count == 0

    @pytest.mark.asyncio
    async def test_release_does_not_go_negative(self) -> None:
        """释放不会让计数变为负数"""
        limiter = ConnectionLimiter()
        await limiter.release()  # 在计数为 0 时释放
        assert limiter.count == 0

    @pytest.mark.asyncio
    async def test_unlimited_when_max_is_zero(self) -> None:
        """max_connections 为 0 时不限制"""
        limiter = ConnectionLimiter()
        for _ in range(100):
            assert await limiter.try_acquire(0) is True
        assert limiter.count == 100

    @pytest.mark.asyncio
    async def test_unlimited_when_max_is_negative(self) -> None:
        """max_connections 为负数时不限制"""
        limiter = ConnectionLimiter()
        for _ in range(50):
            assert await limiter.try_acquire(-1) is True
        assert limiter.count == 50

    @pytest.mark.asyncio
    async def test_reject_when_limit_reached(self) -> None:
        """达到限制时拒绝新连接"""
        limiter = ConnectionLimiter()
        max_conn = 3

        assert await limiter.try_acquire(max_conn) is True
        assert await limiter.try_acquire(max_conn) is True
        assert await limiter.try_acquire(max_conn) is True
        assert limiter.count == 3

        # 第 4 个应该被拒绝
        assert await limiter.try_acquire(max_conn) is False
        assert limiter.count == 3  # 计数不变

    @pytest.mark.asyncio
    async def test_release_allows_new_connection(self) -> None:
        """释放后可以接受新连接"""
        limiter = ConnectionLimiter()
        max_conn = 2

        await limiter.try_acquire(max_conn)
        await limiter.try_acquire(max_conn)
        assert await limiter.try_acquire(max_conn) is False  # 达到限制

        await limiter.release()  # 释放一个
        assert await limiter.try_acquire(max_conn) is True  # 可以再次获取

    @pytest.mark.asyncio
    async def test_concurrent_acquire_is_thread_safe(self) -> None:
        """并发获取是线程安全的"""
        limiter = ConnectionLimiter()
        max_conn = 10

        async def try_connect() -> bool:
            return await limiter.try_acquire(max_conn)

        # 并发 20 个请求
        results = await asyncio.gather(*[try_connect() for _ in range(20)])

        # 只有 10 个成功
        success_count = sum(1 for r in results if r)
        assert success_count == 10
        assert limiter.count == 10


class TestGlobalLimiter:
    """测试全局限流器函数"""

    def test_get_connection_limiter_returns_same_instance(self) -> None:
        """获取全局实例返回同一个对象"""
        reset_connection_limiter()
        limiter1 = get_connection_limiter()
        limiter2 = get_connection_limiter()
        assert limiter1 is limiter2

    def test_reset_connection_limiter(self) -> None:
        """重置后返回新实例"""
        limiter1 = get_connection_limiter()
        reset_connection_limiter()
        limiter2 = get_connection_limiter()
        assert limiter1 is not limiter2
