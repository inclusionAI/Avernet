"""
FusedProfileRepository - 融合结果存储接口

定义融合结果的数据存储接口，支持：
- 融合结果的增删改查
- 对话轮次的追加和查询
- 执行状态的更新

根据 fusion-storage-design.md 规范实现。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional

from src.domain.models.profile_fusion import FusedProfileRecord
from src.domain.models.profile_fusion import ConversationTurn


class FusedProfileRepository(ABC):
    """
    融合结果存储接口

    定义融合结果的数据存储契约，支持：
    - 融合结果的保存和查询
    - 对话轮次的追加和统计更新
    - 执行状态的更新

    实现类：
    - InMemoryFusedProfileStore: 内存存储（开发测试用）
    - ZdasFusedProfileStore: ZDAS 数据库存储（生产环境）
    """

    @abstractmethod
    def save(self, record: FusedProfileRecord) -> str:
        """
        保存融合结果

        Args:
            record: 融合结果记录

        Returns:
            fusion_id: 融合唯一标识
        """
        pass

    @abstractmethod
    def find_by_key(self, fusion_id: str) -> Optional[FusedProfileRecord]:
        """
        根据 fusion_id 查询融合结果

        Args:
            fusion_id: 融合唯一标识

        Returns:
            FusedProfileRecord 或 None
        """
        pass

    @abstractmethod
    def find_by_participant(
        self,
        participant_id: str,
        limit: int = 20,
        fusion_mode: Optional[str] = None,
    ) -> list[FusedProfileRecord]:
        """
        查询某个专家参与的融合

        Args:
            participant_id: 参与者 ID
            limit: 返回数量限制
            fusion_mode: 融合模式过滤（可选）

        Returns:
            FusedProfileRecord 列表
        """
        pass

    @abstractmethod
    def find_by_group(
        self,
        group_id: str,
        limit: int = 20,
        fusion_mode: Optional[str] = None,
    ) -> list[FusedProfileRecord]:
        """
        查询某个群组的融合记录

        Args:
            group_id: 群组 ID
            limit: 返回数量限制
            fusion_mode: 融合模式过滤（可选）

        Returns:
            FusedProfileRecord 列表
        """
        pass

    @abstractmethod
    def append_turn(
        self,
        fusion_id: str,
        turn: ConversationTurn,
    ) -> None:
        """
        追加对话轮次，更新统计

        Args:
            fusion_id: 融合唯一标识
            turn: 对话轮次
        """
        pass

    @abstractmethod
    def get_conversation(
        self,
        fusion_id: str,
        offset: int = 0,
        limit: int = 100,
    ) -> Optional[dict]:
        """
        获取对话（支持分页）

        Args:
            fusion_id: 融合唯一标识
            offset: 偏移量
            limit: 返回数量限制

        Returns:
            包含对话列表和统计信息的字典
        """
        pass

    @abstractmethod
    def update_status(
        self,
        fusion_id: str,
        status: str,
        fuse_message: Optional[str] = None,
    ) -> None:
        """
        更新执行状态

        Args:
            fusion_id: 融合唯一标识
            status: 执行状态
            fuse_message: 执行消息（可选）
        """
        pass

    @abstractmethod
    def exists(self, fusion_id: str) -> bool:
        """
        检查记录是否存在

        Args:
            fusion_id: 融合唯一标识

        Returns:
            是否存在
        """
        pass

    @abstractmethod
    def update(self, record: FusedProfileRecord) -> str:
        """
        更新已存在的融合结果

        Args:
            record: 融合结果记录

        Returns:
            fusion_id: 融合唯一标识

        Raises:
            FusionNotFoundException: 记录不存在
        """
        pass


__all__ = [
    "FusedProfileRepository",
]