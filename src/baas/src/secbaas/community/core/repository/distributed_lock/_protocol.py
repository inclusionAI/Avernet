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
        """在单一事务中尝试获取锁（SELECT → DELETE expired → INSERT）。

        返回 True 表示加锁成功；False 表示锁被他人持有且未过期。
        唯一索引冲突视为并发竞争，返回 False 而非抛异常。
        """
        ...
