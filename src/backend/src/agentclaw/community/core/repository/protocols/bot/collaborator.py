"""Repository contracts owned by the ``bot`` domain.

Moved here by the ``core/repository`` consolidation. Every member is
``@abstractmethod``: an implementation that omits one fails at construction
naming the missing member, instead of raising ``AttributeError`` at the call
site. Domain imports are ``TYPE_CHECKING``-only — see the module docstring in
``core/repository/README.md`` for why that direction is load-bearing.
"""
from __future__ import annotations

from abc import abstractmethod
from typing import Any, Dict, List, Optional, Protocol, TYPE_CHECKING

if TYPE_CHECKING:
    from agentclaw.community.core.bot_collaborator.models import BotCollabLockRecord, BotCollabLogRecord, CollaboratorRecord


class CollaboratorRepositoryProtocol(Protocol):
    """Bot 协作者数据访问接口。

    所有方法使用字典或 Pydantic 模型作为输入输出，
    具体实现由 plugins_impl 提供。

    Implementations:
    - SQLite: plugins_impl.local.bot_collaborator_repository.SQLiteCollaboratorRepository
    - the corp store: plugins_impl.prod.bot_collaborator_repository.ZdasCollaboratorRepository
    """

    # ========================================================================
    # Insert Operations
    # ========================================================================

    @abstractmethod
    def insert(self, data: Dict[str, Any]) -> CollaboratorRecord:
        """创建协作者记录。

        Args:
            data: 包含协作者字段的字典
                - bot_pk: Bot主键（必填）
                - bot_id: Bot ID（必填）
                - owner_id: Bot拥有者工号（必填）
                - user_id: 协作者工号（必填）
                - user_name: 协作者花名（可选）
                - role: 角色（可选，默认 member）
                - operator_id: 操作者工号（必填）
                - env: 环境标识（可选，默认当前环境）

        Returns:
            创建的 CollaboratorRecord

        Raises:
            IntegrityError: 如果(bot_pk, user_id, env)已存在
        """
        ...

    # ========================================================================
    # Query Operations
    # ========================================================================

    @abstractmethod
    def get_by_id(self, collaborator_id: int) -> Optional[CollaboratorRecord]:
        """根据ID获取协作者记录。

        Args:
            collaborator_id: 记录ID

        Returns:
            CollaboratorRecord，不存在返回None
        """
        ...

    @abstractmethod
    def get_by_bot_and_user(
        self,
        bot_pk: int,
        user_id: str,
        env: str,
    ) -> Optional[CollaboratorRecord]:
        """根据Bot和用户查询协作者记录。

        Args:
            bot_pk: Bot主键
            user_id: 协作者工号
            env: 环境标识

        Returns:
            CollaboratorRecord，不存在返回None
        """
        ...

    @abstractmethod
    def list_by_bot(
        self,
        bot_id: str,
        owner_id: str,
        env: str,
        role: Optional[str] = None,
    ) -> List[CollaboratorRecord]:
        """获取Bot的协作者列表。

        Args:
            bot_id: Bot ID
            owner_id: Bot拥有者工号
            env: 环境标识
            role: 角色过滤（可选，admin/member）

        Returns:
            CollaboratorRecord 列表
        """
        ...

    @abstractmethod
    def list_by_user(
        self,
        user_id: str,
        env: str,
    ) -> List[CollaboratorRecord]:
        """获取用户参与的所有Bot协作者记录。

        Args:
            user_id: 协作者工号
            env: 环境标识

        Returns:
            CollaboratorRecord 列表
        """
        ...

    @abstractmethod
    def get_user_role(
        self,
        bot_pk: int,
        user_id: str,
        env: str,
    ) -> Optional[str]:
        """获取用户在Bot中的角色。

        Args:
            bot_pk: Bot主键
            user_id: 用户工号
            env: 环境标识

        Returns:
            角色字符串（'admin' 或 'member'），如果不是协作者返回None
        """
        ...

    # ========================================================================
    # Update Operations
    # ========================================================================

    @abstractmethod
    def update(
        self,
        collaborator_id: int,
        data: Dict[str, Any],
    ) -> Optional[CollaboratorRecord]:
        """更新协作者记录。

        Args:
            collaborator_id: 记录ID
            data: 要更新的字段字典
                - user_id: 协作者工号（可选）
                - user_name: 协作者花名（可选）
                - role: 角色（可选）
                - operator_id: 操作者工号（可选，记录是谁修改的）

        Returns:
            更新后的 CollaboratorRecord，不存在返回None
        """
        ...

    # ========================================================================
    # Delete Operations
    # ========================================================================

    @abstractmethod
    def delete(self, collaborator_id: int) -> bool:
        """删除协作者记录。

        Args:
            collaborator_id: 记录ID

        Returns:
            是否成功删除
        """
        ...

    @abstractmethod
    def delete_by_bot(self, bot_pk: int, env: str) -> int:
        """删除Bot的所有协作者记录。

        Args:
            bot_pk: Bot主键
            env: 环境标识

        Returns:
            删除的记录数
        """
        ...


class BotCollabLogRepositoryProtocol(Protocol):
    """协作操作日志数据访问接口。

    所有方法使用字典或 Pydantic 模型作为输入输出，
    具体实现由 plugins_impl 提供。

    Implementations:
    - plugins_impl.bot_collab_log_repository.BotCollabLogRepository
    """

    # ========================================================================
    # Insert Operations
    # ========================================================================

    @abstractmethod
    def insert(self, data: Dict[str, Any]) -> BotCollabLogRecord:
        """创建协作操作日志记录。

        Args:
            data: 包含日志字段的字典
                - bot_id: Bot ID（必填）
                - owner_id: Bot拥有者工号（必填）
                - detail: 操作详情（必填）
                - operator_id: 操作者工号（必填）
                - env: 环境标识（可选，默认当前环境）

        Returns:
            创建的 BotCollabLogRecord
        """
        ...

    # ========================================================================
    # Query Operations
    # ========================================================================

    @abstractmethod
    def list_by_bot(
        self,
        bot_id: str,
        owner_id: str,
        limit: int = 100,
    ) -> List[BotCollabLogRecord]:
        """按Bot查询协作操作日志列表。

        Args:
            bot_id: Bot ID
            owner_id: Bot拥有者工号
            limit: 返回数量限制（默认100）

        Returns:
            BotCollabLogRecord 列表，按创建时间倒序
        """
        ...

    @abstractmethod
    def list_by_operator(
        self,
        operator_id: str,
        limit: int = 100,
    ) -> List[BotCollabLogRecord]:
        """按操作者查询协作操作日志列表。

        Args:
            operator_id: 操作者工号
            limit: 返回数量限制（默认100）

        Returns:
            BotCollabLogRecord 列表，按创建时间倒序
        """
        ...


class BotCollabLockRepositoryProtocol(Protocol):
    """协作锁数据访问接口。

    所有方法使用字典或 Pydantic 模型作为输入输出，
    具体实现由 plugins_impl 提供。

    Implementations:
    - plugins_impl.bot_collab_lock_repository.BotCollabLockRepository
    """

    # ========================================================================
    # Insert Operations
    # ========================================================================

    @abstractmethod
    def acquire(self, lock_key: str, holder_user_id: str) -> BotCollabLockRecord:
        """获取协作锁。

        Args:
            lock_key: 锁键
            holder_user_id: 持锁者工号

        Returns:
            创建的 BotCollabLockRecord

        Raises:
            IntegrityError: 如果(lock_key, env)已存在
        """
        ...

    # ========================================================================
    # Query Operations
    # ========================================================================

    @abstractmethod
    def get_by_key(self, lock_key: str) -> Optional[BotCollabLockRecord]:
        """根据锁键查询锁记录。

        Args:
            lock_key: 锁键

        Returns:
            BotCollabLockRecord，不存在返回None
        """
        ...

    @abstractmethod
    def is_locked(self, lock_key: str) -> bool:
        """检查锁是否存在。

        Args:
            lock_key: 锁键

        Returns:
            是否被锁定
        """
        ...

    @abstractmethod
    def list_by_holder(
        self,
        holder_user_id: str,
    ) -> List[BotCollabLockRecord]:
        """获取用户持有的所有锁。

        Args:
            holder_user_id: 持锁者工号

        Returns:
            BotCollabLockRecord 列表
        """
        ...

    # ========================================================================
    # Delete Operations
    # ========================================================================

    @abstractmethod
    def release(self, lock_key: str) -> bool:
        """释放协作锁。

        Args:
            lock_key: 锁键

        Returns:
            是否成功释放
        """
        ...
