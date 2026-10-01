from datetime import datetime
from typing import Protocol, runtime_checkable

from ._record import LockRecord


@runtime_checkable
class DistributedLockRepository(Protocol):
    """Protocol for distributed lock repository."""

    def get_by_lock_name(self, lock_name: str) -> LockRecord | None:
        """获取锁记录（只读，不加行锁）"""
        ...

    def update_expire_time(
        self,
        *,
        lock_name: str,
        expire_time: datetime,
    ) -> int:
        """更新锁过期时间（用于续期）"""
        ...

    def delete_lock(self, lock_name: str) -> bool:
        """删除锁记录"""
        ...

    def try_acquire_lock(
        self,
        *,
        lock_name: str,
        lock_holder: str,
        expire_time: datetime,
    ) -> bool:
        """以单条原子 upsert 尝试获取锁。

        使用基于 ``uk_lock_name`` 的单条 upsert：无行则 INSERT 初始化，
        已有行仅在可获取（同 holder / 无 expire_time / 已过期）时覆盖更新，
        否则 no-op 不偷锁；随后一次确认读 SELECT 判定调用方是否持锁。

        返回 True 表示加锁成功；False 表示锁被他人持有且未过期。
        OceanBase/MySQL 锁等待超时（1205）视为正常竞争，返回 False。
        """
        ...

    def delete_expired_locks_by_prefix(self, prefix: str, *, now: datetime) -> int:
        """批量删除 ``lock_name LIKE prefix%`` 且 ``expire_time <= now`` 的孤儿锁行。

        参数化 ``LIKE`` 绑定，避免字符串拼接。用于 ``BotRunRecoveryTask``
        对过期 ``botrun:session:`` 锁的对账清理（不依赖锁 TTL 自愈）。

        Args:
            prefix: 锁名前缀，例如 ``botrun:session:``。
            now: 应用 wall-clock 时间；``expire_time <= now`` 视为已过期。

        Returns:
            被删除的行数。
        """
        ...
