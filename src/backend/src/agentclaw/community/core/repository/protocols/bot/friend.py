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
    from agentclaw.community.core.bot_public.repository.models import BotFriendQueryKey


class BotFriendRepositoryProtocol(Protocol):
    """Bot好友关系数据访问接口。

    所有方法使用字典作为输入输出，具体实现由 plugins 提供。
    """

    # ========================================================================
    # Insert Operations
    # ========================================================================

    @abstractmethod
    def insert(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        创建好友申请记录。

        Args:
            data: 包含好友申请字段的字典
                - requester_entity_id: 申请方实体ID
                - requester_name: 申请人姓名（可选）
                - target_entity_id: 目标方实体ID
                - target_name: 目标方姓名（可选）
                - target_bot_id: 目标Bot ID
                - status: 状态（默认PENDING）
                - ext: 扩展字段（可选）

        Returns:
            创建的记录字典
        """
        ...

    # ========================================================================
    # Query Operations
    # ========================================================================

    @abstractmethod
    def get_by_id(self, record_id: int) -> Optional[Dict[str, Any]]:
        """
        根据ID获取好友申请记录。

        Args:
            record_id: 记录ID

        Returns:
            记录字典，不存在返回None
        """
        ...

    @abstractmethod
    def get_by_entity_ids_batch(
        self,
        keys: List[BotFriendQueryKey]
    ) -> List[Dict[str, Any]]:
        """
        批量查询好友关系记录。

        Args:
            keys: BotFriendQueryKey 对象列表

        Returns:
            匹配的记录列表
        """
        ...

    @abstractmethod
    def get_by_entity_ids(
        self,
        requester_entity_id: str,
        target_entity_id: str,
        target_bot_id: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """
        根据申请方和目标方实体ID获取记录。

        Args:
            requester_entity_id: 申请方实体ID
            target_entity_id: 目标方实体ID
            target_bot_id: 目标Bot ID（可选）

        Returns:
            记录字典，不存在返回None
        """
        ...

    @abstractmethod
    def list_by_requester(
        self,
        requester_entity_id: str,
        status: Optional[str] = None,
        target_name: Optional[str] = None,
        target_owner_name: Optional[str] = None,
        page: int = 1,
        page_size: int = 20
    ) -> tuple[int, List[Dict[str, Any]]]:
        """
        查询申请方发起的好友申请列表。

        Args:
            requester_entity_id: 申请方实体ID
            status: 状态过滤（可选）
            target_name: 目标方姓名过滤（可选，模糊匹配）
            target_owner_name: 目标Bot拥有者花名过滤（可选，模糊匹配）
            page: 页码（从1开始）
            page_size: 每页数量

        Returns:
            (总数, 记录列表)
        """
        ...

    @abstractmethod
    def list_by_target(
        self,
        target_entity_id: str,
        status: Optional[str] = None,
        page: int = 1,
        page_size: int = 20
    ) -> tuple[int, List[Dict[str, Any]]]:
        """
        查询目标方收到的好友申请列表。

        Args:
            target_entity_id: 目标方实体ID
            status: 状态过滤（可选）
            page: 页码（从1开始）
            page_size: 每页数量

        Returns:
            (总数, 记录列表)
        """
        ...

    @abstractmethod
    def list_by_target_bot_id(
        self,
        target_bot_id: str,
        status: Optional[str] = None,
        page: int = 1,
        page_size: int = 20
    ) -> tuple[int, List[Dict[str, Any]]]:
        """
        根据目标Bot ID查询好友申请列表。

        Args:
            target_bot_id: 目标Bot ID
            status: 状态过滤（可选）
            page: 页码（从1开始）
            page_size: 每页数量

        Returns:
            (总数, 记录列表)
        """
        ...

    @abstractmethod
    def list_approved_friends_for_bot(
        self,
        bot_id: str,
        owner_id: str,
        page_size: int = 1000,
    ) -> list[dict[str, Any]]:
        """查询指定 Bot 下人工审批通过的好友（用于授权关系重建，内部自动分页）。

        Args:
            bot_id: Bot ID
            owner_id: Bot owner ID（用于过滤同名 Bot）
            page_size: 每页数量（默认 1000）

        Returns:
            审批通过的好友记录列表（仅包含 ext.approvals 最后一条为 MANUAL/APPROVED 的记录）
        """
        ...

    # ========================================================================
    # Update Operations
    # ========================================================================

    @abstractmethod
    def accept(
        self,
        record_id: int,
        approval_uuid: Optional[str] = None,
        ext: Optional[Dict[str, Any]] = None
    ) -> Optional[Dict[str, Any]]:
        """
        接受好友申请。

        Args:
            record_id: 记录ID
            approval_uuid: 审批单UUID（可选）
            ext: 扩展字段（可选）

        Returns:
            更新后的记录
        """
        ...

    @abstractmethod
    def reject(
        self,
        record_id: int,
        reject_reason: Optional[str] = None,
        approval_uuid: Optional[str] = None,
        ext: Optional[Dict[str, Any]] = None
    ) -> Optional[Dict[str, Any]]:
        """
        拒绝好友申请。

        Args:
            record_id: 记录ID
            reject_reason: 拒绝原因
            approval_uuid: 审批单UUID（可选）
            ext: 扩展字段（可选）

        Returns:
            更新后的记录
        """
        ...

    @abstractmethod
    def cancel(
        self,
        record_id: int,
        approval_uuid: Optional[str] = None,
        ext: Optional[Dict[str, Any]] = None
    ) -> Optional[Dict[str, Any]]:
        """
        取消好友申请。

        Args:
            record_id: 记录ID
            approval_uuid: 审批单UUID（可选）
            ext: 扩展字段（可选）

        Returns:
            更新后的记录
        """
        ...

    @abstractmethod
    def soft_delete(self, record_id: int) -> bool:
        """
        软删除记录（通过更新状态为CANCELLED）。

        Args:
            record_id: 记录ID

        Returns:
            是否成功
        """
        ...

    # ========================================================================
    # Approval Operations
    # ========================================================================

    @abstractmethod
    def create_approval(
        self,
        record_id: int,
        approval_data: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """
        创建审批工单并更新到 BotFriend 的 ext 字段。

        Args:
            record_id: BotFriend 记录ID
            approval_data: 审批单数据

        Returns:
            更新后的 BotFriend 记录
        """
        ...

    @abstractmethod
    def get_approval_by_uuid(
        self,
        record_id: int,
        approval_uuid: str
    ) -> Optional[Dict[str, Any]]:
        """
        根据UUID获取审批单。

        Args:
            record_id: BotFriend 记录ID
            approval_uuid: 审批单UUID

        Returns:
            审批单字典，不存在返回None
        """
        ...

    @abstractmethod
    def update_approval_status(
        self,
        record_id: int,
        approval_uuid: str,
        new_status: str,
        reject_reason: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """
        更新审批单状态。

        Args:
            record_id: BotFriend 记录ID
            approval_uuid: 审批单UUID
            new_status: 新状态
            reject_reason: 拒绝原因（拒绝时必填）

        Returns:
            更新后的 BotFriend 记录
        """
        ...

    @abstractmethod
    def get_latest_approval(self, record_id: int) -> Optional[Dict[str, Any]]:
        """
        获取最新的审批单。

        Args:
            record_id: BotFriend 记录ID

        Returns:
            最新的审批单字典，不存在返回None
        """
        ...

    @abstractmethod
    def list_approvals(self, record_id: int) -> List[Dict[str, Any]]:
        """
        获取所有审批单列表。

        Args:
            record_id: BotFriend 记录ID

        Returns:
            审批单列表
        """
        ...

    # ========================================================================
    # Internal Update Methods (for plugins use)
    # ========================================================================

    @abstractmethod
    def _update_status_and_ext(
        self,
        record_id: int,
        status: str,
        ext: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """
        同时更新状态和 ext 字段。

        Args:
            record_id: 记录ID
            status: 新状态
            ext: 新的 ext 字典

        Returns:
            更新后的记录
        """
        ...
