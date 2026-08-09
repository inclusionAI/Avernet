"""协作锁服务。

提供 Bot 协作场景下的锁管理功能，防止并发冲突操作。
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from sqlalchemy.exc import IntegrityError

from agentclaw.community.core.bot_collaborator.models import BotCollabLockRecord, LockInfoResult, SessionLockInfoResult
from agentclaw.community.core.repository.protocols.bot import BotCollabLockRepositoryProtocol
from agentclaw.community.log import get_logger

if TYPE_CHECKING:
    from agentclaw.community.core.bot_collaborator.protocols import (
        BotServiceProtocol,
        CollaboratorServiceProtocol,
    )

logger = get_logger()


# ============================================================================
# 异常定义
# ============================================================================
class CollaboratorLockServiceError(Exception):
    """协作锁服务基础异常。"""
    pass


class LockNotHeldError(CollaboratorLockServiceError):
    """锁未被持有异常（尝试释放不存在的锁）。"""

    def __init__(self, bot_id: str, owner_id: str):
        self.bot_id = bot_id
        self.owner_id = owner_id
        super().__init__(f"Bot 未被锁定: bot_id={bot_id}, owner_id={owner_id}")


class LockReleaseDeniedError(CollaboratorLockServiceError):
    """无权释放锁异常（非持有者尝试释放）。"""

    def __init__(self, bot_id: str, owner_id: str, holder_user_id: str, requester_user_id: str):
        self.bot_id = bot_id
        self.owner_id = owner_id
        self.holder_user_id = holder_user_id
        self.requester_user_id = requester_user_id
        super().__init__(
            f"无权释放锁: bot_id={bot_id}, owner_id={owner_id}, holder={holder_user_id}, requester={requester_user_id}"
        )


# ============================================================================
# 服务实现
# ============================================================================
class CollaboratorLockService:
    """协作锁管理服务。

    提供 Bot 协作场景下的锁管理功能：
    - 获取锁（acquire）：防止并发冲突，支持重入
    - 释放锁（release）：释放资源
    - 获取锁信息（get_lock_info）：查看锁的详细信息，包含持有者名字

    锁键格式：{bot_id}:{owner_id}
    """

    def __init__(
        self,
        lock_repo: BotCollabLockRepositoryProtocol,
        collab_service: CollaboratorServiceProtocol,
        bot_service: BotServiceProtocol,
    ) -> None:
        """初始化服务。

        Args:
            lock_repo: 协作锁数据访问接口
            collab_service: 协作者服务，用于查询协作者名字
            bot_service: Bot 服务，用于查询 Bot owner 名字
        """
        self._lock_repo = lock_repo
        self._collab_service = collab_service
        self._bot_service = bot_service

    @staticmethod
    def _make_lock_key(bot_id: str, owner_id: str) -> str:
        """生成锁键。

        Args:
            bot_id: Bot ID
            owner_id: Bot 拥有者工号

        Returns:
            锁键字符串
        """
        return f"{bot_id}:{owner_id}"

    # ========================================================================
    # 获取锁
    # ========================================================================

    def acquire_lock(
        self,
        bot_id: str,
        owner_id: str,
        user_id: str,
    ) -> Optional[BotCollabLockRecord]:
        """获取协作锁（支持重入）。

        尝试获取指定 Bot 的锁：
        1. 先查询锁是否存在
        2. 如果锁存在且持有者是当前用户，返回锁对象（重入）
        3. 如果锁存在但持有者是其他用户，返回 None
        4. 如果锁不存在，尝试获取锁，成功返回锁对象，失败返回 None

        Args:
            bot_id: Bot ID
            owner_id: Bot 拥有者工号
            user_id: 尝试获取锁的用户工号

        Returns:
            BotCollabLockRecord 如果成功获取锁或已持有锁，否则返回 None
        """
        lock_key = self._make_lock_key(bot_id, owner_id)

        # 1. 先查询锁是否存在
        existing = self._lock_repo.get_by_key(lock_key)
        if existing:
            # 2. 如果持有者是当前用户，返回锁对象（重入）
            if existing.holder_user_id == user_id:
                logger.debug(
                    "[acquire_lock] Lock already held by requester (reentrant): bot_id=%s, owner_id=%s, user=%s",
                    bot_id, owner_id, user_id
                )
                return existing
            # 3. 持有者是其他用户，返回 None
            logger.debug(
                "[acquire_lock] Lock held by another user: bot_id=%s, owner_id=%s, holder=%s, requester=%s",
                bot_id, owner_id, existing.holder_user_id, user_id
            )
            return None

        # 4. 锁不存在，尝试获取锁
        try:
            lock = self._lock_repo.acquire(lock_key, user_id)
            logger.info(
                "[acquire_lock] Acquired: bot_id=%s, owner_id=%s, user=%s",
                bot_id, owner_id, user_id
            )
            return lock
        except IntegrityError:
            # 并发冲突，其他线程先获取了锁
            logger.warning(
                "[acquire_lock] Concurrent acquire failed: bot_id=%s, owner_id=%s, user=%s",
                bot_id, owner_id, user_id
            )
            return None
        except Exception as e:
            # 其他异常
            logger.error(
                "[acquire_lock] Unexpected error: bot_id=%s, owner_id=%s, user=%s, error=%s",
                bot_id, owner_id, user_id, str(e)
            )
            return None

    # ========================================================================
    # 释放锁
    # ========================================================================

    def release_lock(
        self,
        bot_id: str,
        owner_id: str,
        user_id: str,
        force: bool = False,
    ) -> bool:
        """释放协作锁。

        释放指定 Bot 的锁。默认情况下，只有锁的持有者才能释放锁。

        Args:
            bot_id: Bot ID
            owner_id: Bot 拥有者工号
            user_id: 请求释放锁的用户工号
            force: 是否强制释放（忽略持有者检查）

        Returns:
            是否成功释放

        Raises:
            LockNotHeldError: 锁未被持有
            LockReleaseDeniedError: 无权释放锁（非持有者尝试释放）
        """
        lock_key = self._make_lock_key(bot_id, owner_id)

        # 检查锁状态
        lock = self._lock_repo.get_by_key(lock_key)
        if not lock:
            logger.warning(
                "[release_lock] Lock not held: bot_id=%s, owner_id=%s, user=%s",
                bot_id, owner_id, user_id
            )
            raise LockNotHeldError(bot_id, owner_id)

        # 检查权限（除非强制释放）
        if not force and lock.holder_user_id != user_id:
            logger.warning(
                "[release_lock] Permission denied: bot_id=%s, owner_id=%s, holder=%s, requester=%s",
                bot_id, owner_id, lock.holder_user_id, user_id
            )
            raise LockReleaseDeniedError(bot_id, owner_id, lock.holder_user_id, user_id)

        # 释放锁
        success = self._lock_repo.release(lock_key)
        if success:
            logger.info(
                "[release_lock] Released: bot_id=%s, owner_id=%s, user=%s, forced=%s",
                bot_id, owner_id, user_id, force
            )

        return success

    # ========================================================================
    # 抢锁
    # ========================================================================

    def steal_lock(
        self,
        bot_id: str,
        owner_id: str,
        user_id: str,
    ) -> BotCollabLockRecord:
        """抢夺协作锁。

        强制获取锁，无论当前是否被其他用户持有。
        注意：权限校验由拦截器完成，此方法假设调用者已有权限。

        Args:
            bot_id: Bot ID
            owner_id: Bot 拥有者工号
            user_id: 尝试抢锁的用户工号

        Returns:
            BotCollabLockRecord 抢锁成功后的锁记录
        """
        lock_key = self._make_lock_key(bot_id, owner_id)

        # 释放旧锁（如果存在）
        existing = self._lock_repo.get_by_key(lock_key)
        previous_holder = existing.holder_user_id if existing else None
        if existing:
            self._lock_repo.release(lock_key)
            logger.info(
                "[steal_lock] Released previous lock: bot_id=%s, owner_id=%s, previous_holder=%s, new_holder=%s",
                bot_id, owner_id, previous_holder, user_id
            )

        # 获取新锁
        lock = self._lock_repo.acquire(lock_key, user_id)
        logger.info(
            "[steal_lock] Stolen lock: bot_id=%s, owner_id=%s, user=%s, previous_holder=%s",
            bot_id, owner_id, user_id, previous_holder
        )
        return lock

    def get_lock_info(
        self,
        bot_id: str,
        owner_id: str,
        user_id: str,
    ) -> LockInfoResult:
        """获取 Bot 锁的详细信息，包含持有者名字和是否有协作者。

        如果 Bot 没有协作者，直接返回 has_collaborators=False，不查询锁信息。

        Args:
            bot_id: Bot ID
            owner_id: Bot 拥有者工号
            user_id: 当前请求用户工号（用于权限检查）

        Returns:
            LockInfoResult，包含锁记录、持有者名字、是否有协作者和持有者是否是 owner；
            如果未锁定则 lock 为 None
        """
        # 查询协作者列表（用于判断是否有协作者和获取持有者名字）
        collaborators = self._list_collaborators_safe(bot_id, owner_id, user_id)
        has_collaborators = len(collaborators) > 0

        # 没有协作者直接返回，不查询锁
        if not has_collaborators:
            return LockInfoResult(lock=None, holder_name=None, has_collaborators=False, is_owner=False)

        # 有协作者，查询锁信息
        lock_key = self._make_lock_key(bot_id, owner_id)
        lock = self._lock_repo.get_by_key(lock_key)

        if not lock:
            return LockInfoResult(lock=None, holder_name=None, has_collaborators=True, is_owner=False)

        # 判断持有者是否是 owner
        is_owner = lock.holder_user_id == owner_id

        # 查询锁持有者的名字（复用协作者列表）
        holder_name = self._get_holder_name_from_collaborators(
            owner_id=owner_id,
            holder_user_id=lock.holder_user_id,
            collaborators=collaborators,
            bot_id=bot_id,
        )

        return LockInfoResult(lock=lock, holder_name=holder_name, has_collaborators=True, is_owner=is_owner)

    # ========================================================================
    # 会话级锁（coding 应用）—— 复用 ac_bot_collab_lock，key 为 3 段
    #   session:{bot_id}:{owner_id}:{session_id}
    # 与 Service Bot 的 2 段 bot 级锁（{bot_id}:{owner_id}）同表共存、命名空间隔离。
    # 注意：不要改动上面的 2 段 _make_lock_key / acquire_lock 等（Service Bot 在用）。
    # ========================================================================

    @staticmethod
    def _make_session_lock_key(bot_id: str, owner_id: str, session_id: str) -> str:
        """生成会话锁键：session:{bot_id}:{owner_id}:{session_id}。"""
        return f"session:{bot_id}:{owner_id}:{session_id}"

    def acquire_session_lock(
        self,
        bot_id: str,
        owner_id: str,
        session_id: str,
        user_id: str,
    ) -> Optional[BotCollabLockRecord]:
        """获取会话锁（支持重入）。

        1. 锁已存在且持有者为当前用户 -> 返回（重入）
        2. 锁已存在但他人持有 -> 返回 None
        3. 锁不存在 -> 获取并返回；并发冲突返回 None
        """
        lock_key = self._make_session_lock_key(bot_id, owner_id, session_id)
        existing = self._lock_repo.get_by_key(lock_key)
        if existing:
            if existing.holder_user_id == user_id:
                return existing
            logger.debug(
                "[acquire_session_lock] Held by another: session_id=%s, holder=%s, requester=%s",
                session_id, existing.holder_user_id, user_id,
            )
            return None
        try:
            lock = self._lock_repo.acquire(lock_key, user_id)
            logger.info(
                "[acquire_session_lock] Acquired: bot_id=%s, session_id=%s, user=%s",
                bot_id, session_id, user_id,
            )
            return lock
        except IntegrityError:
            logger.warning(
                "[acquire_session_lock] Concurrent acquire failed: session_id=%s, user=%s",
                session_id, user_id,
            )
            return None
        except Exception as e:
            logger.error(
                "[acquire_session_lock] Unexpected error: session_id=%s, user=%s, error=%s",
                session_id, user_id, str(e),
            )
            return None

    def release_session_lock(
        self,
        bot_id: str,
        owner_id: str,
        session_id: str,
        user_id: str,
        force: bool = False,
    ) -> bool:
        """释放会话锁。仅持有者可释放（force=True 除外）。锁不存在视为已释放，返回 True。"""
        lock_key = self._make_session_lock_key(bot_id, owner_id, session_id)
        lock = self._lock_repo.get_by_key(lock_key)
        if not lock:
            return True
        if not force and lock.holder_user_id != user_id:
            raise LockReleaseDeniedError(bot_id, owner_id, lock.holder_user_id, user_id)
        success = self._lock_repo.release(lock_key)
        if success:
            logger.info(
                "[release_session_lock] Released: bot_id=%s, session_id=%s, user=%s, forced=%s",
                bot_id, session_id, user_id, force,
            )
        return success

    def steal_session_lock(
        self,
        bot_id: str,
        owner_id: str,
        session_id: str,
        user_id: str,
    ) -> BotCollabLockRecord:
        """抢夺会话锁。强制释放旧锁后获取新锁。"""
        lock_key = self._make_session_lock_key(bot_id, owner_id, session_id)
        existing = self._lock_repo.get_by_key(lock_key)
        previous_holder = existing.holder_user_id if existing else None
        if existing:
            self._lock_repo.release(lock_key)
        lock = self._lock_repo.acquire(lock_key, user_id)
        logger.info(
            "[steal_session_lock] Stolen: bot_id=%s, session_id=%s, user=%s, previous_holder=%s",
            bot_id, session_id, user_id, previous_holder,
        )
        return lock

    def get_session_lock_info(
        self,
        bot_id: str,
        owner_id: str,
        session_id: str,
        user_id: str,
    ) -> SessionLockInfoResult:
        """获取会话锁状态。直接按 lock_key 查锁，不依赖"是否有协作者"短路。"""

        lock_key = self._make_session_lock_key(bot_id, owner_id, session_id)
        lock = self._lock_repo.get_by_key(lock_key)
        if not lock:
            return SessionLockInfoResult(locked=False, lock=None, holder_name=None, is_mine=False)

        is_mine = lock.holder_user_id == user_id
        # 复用协作者列表解析持有者花名（成员也在 ac_bot_collaborator 中）
        collaborators = self._list_collaborators_safe(bot_id, owner_id, user_id)
        holder_name = self._get_holder_name_from_collaborators(
            owner_id=owner_id,
            holder_user_id=lock.holder_user_id,
            collaborators=collaborators,
            bot_id=bot_id,
        )
        return SessionLockInfoResult(
            locked=True, lock=lock, holder_name=holder_name, is_mine=is_mine,
        )

    def _list_collaborators_safe(
        self,
        bot_id: str,
        owner_id: str,
        user_id: str,
    ):
        """安全地查询协作者列表。

        Args:
            bot_id: Bot ID
            owner_id: Bot 拥有者工号
            user_id: 当前请求用户工号（用于权限检查）

        Returns:
            协作者列表，查询失败返回空列表
        """
        try:
            return self._collab_service.list_collaborators(
                bot_id=bot_id,
                owner_id=owner_id,
                user_id=user_id,
            )
        except Exception as e:
            logger.warning("[_list_collaborators_safe] Failed to list collaborators: %s", e)
            return []

    def _get_holder_name_from_collaborators(
        self,
        owner_id: str,
        holder_user_id: str,
        collaborators,
        bot_id: str,
    ) -> Optional[str]:
        """从协作者列表中获取持有者名字。

        Args:
            owner_id: Bot 拥有者工号
            holder_user_id: 锁持有者工号
            collaborators: 协作者列表（已查询）
            bot_id: Bot ID（用于查询 owner_name）

        Returns:
            持有者名字，找不到则返回 None
        """
        # 如果锁持有者是 owner，从 bot 信息获取 owner_name
        if holder_user_id == owner_id:
            try:
                bot = self._bot_service.get_bot(bot_id, owner_id)
                return bot.get("owner_name") if bot else None
            except Exception as e:
                logger.warning("[_get_holder_name_from_collaborators] Failed to get bot owner_name: %s", e)
                return None

        # 从协作者列表中查找
        for collab in collaborators:
            if collab.user_id == holder_user_id:
                return collab.user_name
        return None
