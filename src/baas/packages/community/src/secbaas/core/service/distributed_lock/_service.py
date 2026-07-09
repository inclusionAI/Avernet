"""
分布式锁服务层

实现功能：
1. 自动续期 - 通过后台线程定期更新过期时间
2. 可重入锁 - 同一线程/持有者可以多次获取锁，维护重入计数
3. 基于唯一索引 + SELECT/INSERT 实现分布式锁
4. 防死锁 - 通过 expire_time 防止进程宕机导致的死锁

使用示例：
    lock_service = DistributedLockService(repository)

    # 方式1：使用上下文管理器（推荐）
    with lock_service.try_lock("my_lock", lock_holder="worker_1", expire_seconds=30) as lock:
        if lock.acquired:
            # 执行业务逻辑
            pass

    # 方式2：手动管理锁
    lock = lock_service.acquire_lock("my_lock", lock_holder="worker_1", expire_seconds=30)
    if lock.acquired:
        try:
            # 执行业务逻辑
            pass
        finally:
            lock_service.release_lock("my_lock", lock_holder="worker_1")
"""

import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from secbaas.core.repository.distributed_lock import (
    DistributedLockRepository,
)
from secbaas.logger import get_logger

logger = get_logger("core-service")


def _is_unique_constraint_violation(exc: Exception) -> bool:
    """判断异常是否为唯一索引冲突（并发加锁时的正常竞争，非系统错误）。"""
    from sqlalchemy.exc import IntegrityError

    if isinstance(exc, IntegrityError):
        msg = str(exc.orig).lower() if exc.orig else str(exc).lower()
        return any(
            kw in msg
            for kw in ("duplicate", "unique", "constraint")
        )
    return False


@dataclass
class LockContext:
    """锁上下文，用于跟踪锁状态"""

    lock_name: str
    lock_holder: str
    expire_time: datetime
    reentrant_count: int = 1
    renew_thread: threading.Thread | None = None
    stop_renew: threading.Event = field(default_factory=threading.Event)
    acquired: bool = False


class DistributedLockService:
    """分布式锁服务

    特性：
    1. 自动续期：获取锁后启动后台线程定期续期
    2. 可重入锁：同一线程可以多次获取同一锁
    3. 防死锁：通过 expire_time 防止锁永久持有
    4. 持有者验证：只有锁持有者才能释放锁
    """

    def __init__(
        self,
        repository: DistributedLockRepository,
        default_expire_seconds: int = 30,
        renew_interval_seconds: int = 10,
    ):
        """Initialize DistributedLockService.

        Args:
            repository: 分布式锁仓库实例
            default_expire_seconds: 默认锁过期时间（秒）
            renew_interval_seconds: 自动续期间隔（秒）
        """
        self._repository = repository
        self._default_expire_seconds = default_expire_seconds
        self._renew_interval_seconds = renew_interval_seconds
        self._lock_contexts: dict[str, LockContext] = {}
        self._local_lock = threading.Lock()

    def _generate_holder_id(self) -> str:
        """生成唯一的锁持有者标识。

        Returns:
            格式: {hostname}_{pid}_{thread_id}_{uuid}
        """
        import os
        import socket

        hostname = socket.gethostname()
        pid = os.getpid()
        thread_id = threading.current_thread().ident
        short_uuid = uuid.uuid4().hex[:8]
        return f"{hostname}_{pid}_{thread_id}_{short_uuid}"

    def _start_renew_thread(self, context: LockContext) -> None:
        """启动自动续期线程。

        Args:
            context: 锁上下文
        """
        # 如果续期间隔为0或负数，禁用自动续期
        if self._renew_interval_seconds <= 0:
            logger.info(
                f"[start_renew_thread] Auto-renew disabled for lock '{context.lock_name}'"
            )
            return

        def renew_worker() -> None:
            while not context.stop_renew.is_set():
                # 等待续期间隔
                context.stop_renew.wait(timeout=self._renew_interval_seconds)
                if context.stop_renew.is_set():
                    break

                try:
                    # 计算新的过期时间
                    new_expire_time = datetime.now() + timedelta(
                        seconds=self._default_expire_seconds
                    )

                    # 更新数据库中的过期时间
                    updated = self._repository.update_expire_time(
                        lock_name=context.lock_name,
                        expire_time=new_expire_time,
                    )

                    if updated > 0:
                        context.expire_time = new_expire_time
                        logger.info(
                            f"[renew_thread] Lock '{context.lock_name}' renewed until {new_expire_time}"
                        )
                    else:
                        logger.warning(
                            f"[renew_thread] Failed to renew lock '{context.lock_name}'"
                        )
                        break
                except Exception as e:
                    logger.error(
                        f"[renew_thread] Error renewing lock '{context.lock_name}': {e}"
                    )
                    break

        context.renew_thread = threading.Thread(
            target=renew_worker,
            name=f"LockRenew-{context.lock_name}",
            daemon=True,
        )
        context.renew_thread.start()
        logger.info(
            f"[start_renew_thread] Started renew thread for lock '{context.lock_name}'"
        )

    def _stop_renew_thread(self, context: LockContext) -> None:
        """停止自动续期线程。

        Args:
            context: 锁上下文
        """
        if context.renew_thread and context.renew_thread.is_alive():
            context.stop_renew.set()
            context.renew_thread.join(timeout=2.0)
            logger.info(
                f"[stop_renew_thread] Stopped renew thread for lock '{context.lock_name}'"
            )

    def acquire_lock(
        self,
        lock_name: str,
        lock_holder: str | None = None,
        expire_seconds: int | None = None,
        block: bool = False,
        block_timeout: float | None = None,
    ) -> LockContext:
        """获取分布式锁。

        支持可重入：同一线程多次获取同一锁会增加重入计数。
        自动续期：获取锁后会启动后台线程定期续期。

        Args:
            lock_name: 锁名称
            lock_holder: 锁持有者标识，为 None 时自动生成
            expire_seconds: 锁过期时间（秒），None 使用默认值
            block: 是否阻塞等待
            block_timeout: 阻塞等待超时时间（秒），None 表示无限等待

        Returns:
            LockContext 对象，acquired 属性表示是否获取成功
        """
        if expire_seconds is None:
            expire_seconds = self._default_expire_seconds

        if lock_holder is None:
            lock_holder = self._generate_holder_id()

        # 检查是否已持有锁（可重入）
        with self._local_lock:
            if lock_name in self._lock_contexts:
                context = self._lock_contexts[lock_name]
                if context.lock_holder == lock_holder:
                    # 可重入：增加计数
                    context.reentrant_count += 1
                    logger.info(
                        f"[acquire_lock] Reentrant lock '{lock_name}' acquired, count={context.reentrant_count}"
                    )
                    return context

        # 尝试获取锁
        start_time = time.time()
        while True:
            acquired = self._try_acquire_lock_internal(
                lock_name, lock_holder, expire_seconds
            )
            if acquired:
                break

            if not block:
                logger.info(
                    f"[acquire_lock] Failed to acquire lock '{lock_name}', non-blocking mode"
                )
                return LockContext(
                    lock_name=lock_name,
                    lock_holder=lock_holder,
                    expire_time=datetime.now(),
                    acquired=False,
                )

            # 检查阻塞超时
            if (
                block_timeout is not None
                and (time.time() - start_time) >= block_timeout
            ):
                logger.info(f"[acquire_lock] Timeout waiting for lock '{lock_name}'")
                return LockContext(
                    lock_name=lock_name,
                    lock_holder=lock_holder,
                    expire_time=datetime.now(),
                    acquired=False,
                )

            # 短暂休眠后重试
            time.sleep(0.1)

        # 创建锁上下文
        expire_time = datetime.now() + timedelta(seconds=expire_seconds)
        context = LockContext(
            lock_name=lock_name,
            lock_holder=lock_holder,
            expire_time=expire_time,
            reentrant_count=1,
            acquired=True,
        )

        # 缓存锁上下文
        with self._local_lock:
            self._lock_contexts[lock_name] = context

        # 启动自动续期线程
        self._start_renew_thread(context)

        logger.info(
            f"[acquire_lock] Lock '{lock_name}' acquired by '{lock_holder}', expires at {expire_time}"
        )
        return context

    def _try_acquire_lock_internal(
        self,
        lock_name: str,
        lock_holder: str,
        expire_seconds: int,
    ) -> bool:
        """内部方法：尝试获取锁。

        基于 SELECT + INSERT/DELETE 的简单流程，依赖唯一索引保证并发安全：
        1. SELECT 查询锁记录
        2. 记录不存在 → INSERT，成功则加锁，唯一索引冲突则被人抢了
        3. 记录已过期 → DELETE → INSERT，同上
        4. 同 holder → UPDATE expire_time 续期
        5. 他人持有未过期 → 失败

        Args:
            lock_name: 锁名称
            lock_holder: 锁持有者
            expire_seconds: 过期时间（秒）

        Returns:
            是否获取成功
        """
        try:
            now = datetime.now()
            expire_time = now + timedelta(seconds=expire_seconds)

            record = self._repository.get_by_lock_name(lock_name)

            if record is None:
                # 锁不存在，尝试插入
                inserted = self._repository.insert_lock(
                    lock_name=lock_name,
                    lock_holder=lock_holder,
                    expire_time=expire_time,
                )
                return inserted > 0

            if record.expire_time and record.expire_time < now:
                # 锁已过期，删除后重新插入
                self._repository.delete_lock(lock_name)
                inserted = self._repository.insert_lock(
                    lock_name=lock_name,
                    lock_holder=lock_holder,
                    expire_time=expire_time,
                )
                return inserted > 0

            if record.lock_holder == lock_holder:
                # 同一持有者，续期
                self._repository.update_expire_time(
                    lock_name=lock_name,
                    expire_time=expire_time,
                )
                return True

            # 锁被他人持有且未过期
            return False

        except Exception as e:
            if _is_unique_constraint_violation(e):
                logger.info(
                    "[_try_acquire_lock_internal] Lock '%s' concurrently acquired by another holder",
                    lock_name,
                )
            else:
                logger.error(
                    f"[_try_acquire_lock_internal] Error acquiring lock '{lock_name}': {e}"
                )
            return False

    def release_lock(self, lock_name: str, lock_holder: str | None = None) -> bool:
        """释放分布式锁。

        支持可重入：只有重入计数减到 0 时才真正释放锁。

        Args:
            lock_name: 锁名称
            lock_holder: 锁持有者，为 None 时使用当前上下文

        Returns:
            是否成功释放
        """
        with self._local_lock:
            if lock_name not in self._lock_contexts:
                logger.warning(
                    f"[release_lock] Lock '{lock_name}' not found in context"
                )
                return False

            context = self._lock_contexts[lock_name]

            # 验证持有者
            if lock_holder is None:
                lock_holder = context.lock_holder
            elif lock_holder != context.lock_holder:
                logger.warning(
                    f"[release_lock] Lock '{lock_name}' holder mismatch: expected '{context.lock_holder}', got '{lock_holder}'"
                )
                return False

            # 可重入：减少计数
            context.reentrant_count -= 1
            logger.info(
                f"[release_lock] Lock '{lock_name}' reentrant count decreased to {context.reentrant_count}"
            )

            if context.reentrant_count > 0:
                # 还有重入，不真正释放
                return True

            # 真正释放锁
            return self._release_lock_internal(lock_name, lock_holder)

    def _release_lock_internal(self, lock_name: str, lock_holder: str) -> bool:
        """内部方法：真正释放锁。

        注意：此方法应该在已持有 _local_lock 的情况下调用。

        Args:
            lock_name: 锁名称
            lock_holder: 锁持有者

        Returns:
            是否成功释放
        """
        try:
            # 注意：调用者已经持有 _local_lock，这里不需要再获取
            if lock_name in self._lock_contexts:
                context = self._lock_contexts[lock_name]
                # 停止续期线程
                self._stop_renew_thread(context)
                # 移除上下文
                del self._lock_contexts[lock_name]

            # 删除数据库中的锁记录
            deleted = self._repository.delete_lock(lock_name)
            if deleted:
                logger.info(
                    f"[_release_lock_internal] Lock '{lock_name}' released by '{lock_holder}'"
                )
            else:
                logger.warning(
                    f"[_release_lock_internal] Lock '{lock_name}' not found in database"
                )

            return deleted

        except Exception as e:
            logger.error(
                f"[_release_lock_internal] Error releasing lock '{lock_name}': {e}"
            )
            return False

    def is_lock_held(self, lock_name: str, lock_holder: str | None = None) -> bool:
        """检查锁是否被持有。

        Args:
            lock_name: 锁名称
            lock_holder: 锁持有者，为 None 时检查任意持有者

        Returns:
            是否被持有
        """
        # 先检查本地上下文
        with self._local_lock:
            if lock_name in self._lock_contexts:
                context = self._lock_contexts[lock_name]
                if lock_holder is None or context.lock_holder == lock_holder:
                    return True

        # 查询数据库
        record = self._repository.get_by_lock_name(lock_name)
        if record is None:
            return False

        # 检查是否过期
        if record.expire_time and record.expire_time < datetime.now():
            return False

        if lock_holder is not None:
            return record.lock_holder == lock_holder

        return True

    def renew_lock(
        self, lock_name: str, lock_holder: str, additional_seconds: int | None = None
    ) -> bool:
        """手动续期锁。

        Args:
            lock_name: 锁名称
            lock_holder: 锁持有者
            additional_seconds: 增加的秒数，None 使用默认值

        Returns:
            是否续期成功
        """
        if additional_seconds is None:
            additional_seconds = self._default_expire_seconds

        # 验证持有者
        with self._local_lock:
            if lock_name not in self._lock_contexts:
                logger.warning(f"[renew_lock] Lock '{lock_name}' not found in context")
                return False

            context = self._lock_contexts[lock_name]
            if context.lock_holder != lock_holder:
                logger.warning(f"[renew_lock] Lock '{lock_name}' holder mismatch")
                return False

        try:
            new_expire_time = datetime.now() + timedelta(seconds=additional_seconds)
            updated = self._repository.update_expire_time(
                lock_name=lock_name,
                expire_time=new_expire_time,
            )

            if updated > 0:
                context.expire_time = new_expire_time
                logger.info(
                    f"[renew_lock] Lock '{lock_name}' renewed until {new_expire_time}"
                )
                return True
            else:
                logger.warning(f"[renew_lock] Failed to renew lock '{lock_name}'")
                return False

        except Exception as e:
            logger.error(f"[renew_lock] Error renewing lock '{lock_name}': {e}")
            return False

    @contextmanager
    def try_lock(
        self,
        lock_name: str,
        lock_holder: str | None = None,
        expire_seconds: int | None = None,
        block: bool = False,
        block_timeout: float | None = None,
    ) -> Any:
        """上下文管理器方式获取锁。

        使用示例：
            with lock_service.try_lock("my_lock", expire_seconds=30) as lock:
                if lock.acquired:
                    # 执行业务逻辑
                    pass
                else:
                    # 获取锁失败
                    pass

        Args:
            lock_name: 锁名称
            lock_holder: 锁持有者
            expire_seconds: 过期时间（秒）
            block: 是否阻塞等待
            block_timeout: 阻塞等待超时时间（秒）

        Yields:
            LockContext 对象
        """
        context = self.acquire_lock(
            lock_name=lock_name,
            lock_holder=lock_holder,
            expire_seconds=expire_seconds,
            block=block,
            block_timeout=block_timeout,
        )

        try:
            yield context
        finally:
            if context.acquired:
                self.release_lock(lock_name, context.lock_holder)

    def get_lock_info(self, lock_name: str) -> dict[str, Any] | None:
        """获取锁信息。

        Args:
            lock_name: 锁名称

        Returns:
            锁信息字典，锁不存在则返回 None
        """
        # 先检查本地上下文
        with self._local_lock:
            if lock_name in self._lock_contexts:
                context = self._lock_contexts[lock_name]
                return {
                    "lock_name": context.lock_name,
                    "lock_holder": context.lock_holder,
                    "expire_time": (
                        context.expire_time.isoformat() if context.expire_time else None
                    ),
                    "reentrant_count": context.reentrant_count,
                    "acquired": context.acquired,
                    "source": "local_context",
                }

        # 查询数据库
        record = self._repository.get_by_lock_name(lock_name)
        if record is None:
            return None

        return {
            "lock_name": record.lock_name,
            "lock_holder": record.lock_holder,
            "expire_time": (
                record.expire_time.isoformat() if record.expire_time else None
            ),
            "gmt_create": record.gmt_create.isoformat() if record.gmt_create else None,
            "gmt_modified": (
                record.gmt_modified.isoformat() if record.gmt_modified else None
            ),
            "source": "database",
        }

    def force_unlock(self, lock_name: str) -> bool:
        """强制释放锁（管理员操作）。

        Args:
            lock_name: 锁名称

        Returns:
            是否成功释放
        """
        # 停止续期线程
        with self._local_lock:
            if lock_name in self._lock_contexts:
                context = self._lock_contexts[lock_name]
                self._stop_renew_thread(context)
                del self._lock_contexts[lock_name]

        # 删除数据库记录
        deleted = self._repository.delete_lock(lock_name)
        logger.info(f"[force_unlock] Lock '{lock_name}' force unlocked")
        return deleted
