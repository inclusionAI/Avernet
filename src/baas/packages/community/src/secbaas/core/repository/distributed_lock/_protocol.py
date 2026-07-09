from datetime import datetime
from typing import Protocol, runtime_checkable

from ._record import LockRecord


@runtime_checkable
class DistributedLockRepository(Protocol):
    """Protocol for distributed lock repository."""

    def get_by_lock_name(self, lock_name: str) -> LockRecord | None:
        """获取锁记录（只读，不加行锁）"""
        ...

    def get_by_lock_name_for_update(self, lock_name: str) -> LockRecord | None:
        """使用 FOR UPDATE 获取锁记录"""
        ...

    def insert_lock(
        self,
        *,
        lock_name: str,
        lock_holder: str,
        expire_time: datetime,
    ) -> int:
        """插入新的锁记录，唯一索引冲突时返回 0"""
        ...

    def update_lock_holder(
        self,
        *,
        lock_name: str,
        lock_holder: str,
        expire_time: datetime,
    ) -> int:
        """更新锁持有者和过期时间"""
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

    def delete_expired_locks(self, current_time: datetime) -> int:
        """删除已过期的锁记录（供异步定时清理使用）"""
        ...
