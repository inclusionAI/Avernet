"""
连接限流器

提供全局连接数限制功能，支持多个 WebSocket 端点共享同一个计数器。
"""

import asyncio
import logging
from typing import Optional

log = logging.getLogger("connection-limiter")


class ConnectionLimiter:
    """全局连接限流器"""

    def __init__(self):
        self._count = 0
        self._lock = asyncio.Lock()

    async def try_acquire(self, max_connections: int) -> bool:
        """
        尝试获取连接槽位

        Args:
            max_connections: 最大连接数，0 或负数表示不限制

        Returns:
            是否成功获取槽位
        """
        if max_connections <= 0:
            async with self._lock:
                self._count += 1
            return True

        async with self._lock:
            if self._count >= max_connections:
                return False
            self._count += 1
            return True

    async def release(self) -> None:
        """释放连接槽位"""
        async with self._lock:
            if self._count > 0:
                self._count -= 1

    @property
    def count(self) -> int:
        """当前连接数"""
        return self._count


# 全局单例
_limiter: Optional[ConnectionLimiter] = None


def get_connection_limiter() -> ConnectionLimiter:
    """获取全局连接限流器实例"""
    global _limiter
    if _limiter is None:
        _limiter = ConnectionLimiter()
    return _limiter


def reset_connection_limiter() -> None:
    """重置全局连接限流器（用于测试）"""
    global _limiter
    _limiter = None
