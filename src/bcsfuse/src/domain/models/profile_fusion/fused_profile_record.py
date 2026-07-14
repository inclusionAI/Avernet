"""
FusedProfileRecord - 融合结果记录

存储融合操作的结果记录，对应数据库表 bcsfuse_fusion_session。
根据 fusion-storage-design.md 规范实现。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

from src.domain.enums.fuse_enums import FusionMode, FusionStatus
from src.utils.env_utils import get_fusion_env


@dataclass
class FusedProfileRecord:
    """
    融合结果记录

    存储融合操作的完整结果，包括：
    - 标识信息：fusion_id, fusion_mode, group_id, driver_bot_id
    - 请求信息：question, participant_ids, participant_profile_snapshot
    - 融合结果：fuse_detail
    - 对话存储：conversation_recent, conversation_stats
    - 执行信息：status, fuse_message
    - 审计信息：created_by, gmt_create, gmt_modify

    对应数据库表：bcsfuse_fusion_session
    """

    # === 标识信息 ===
    fusion_id: str
    """融合唯一标识（G9模式为MD5哈希，其他模式为UUID）"""

    fusion_mode: str
    """融合模式（参见 FusionMode 枚举）"""

    group_id: Optional[str] = None
    """关联群组"""

    driver_bot_id: Optional[str] = None
    """发起融合的 bot ID"""

    # === 请求信息 ===
    question: Optional[str] = None
    """融合问题"""

    participant_ids: str = ""
    """逗号分隔的参与者ID列表"""

    participant_profile_snapshot: Optional[list[dict]] = None
    """参与融合时的 Profile 快照 JSON 数组"""

    # === 融合结果 ===
    fuse_detail: Optional[dict[str, Any]] = None
    """融合详情 JSON（结构根据 fusion_mode 不同而不同）"""

    # === 对话存储 ===
    conversation_recent: Optional[list[dict]] = None
    """最近对话记录 JSON 数组（最多100轮）"""

    conversation_stats: Optional[dict[str, Any]] = None
    """对话统计 JSON（包含 turns, avg_response_ms）"""

    # === 执行信息 ===
    status: str = FusionStatus.SUCCESS.value
    """执行状态（参见 FusionStatus 枚举）"""

    fuse_message: Optional[str] = None
    """执行消息"""

    # === 审计信息 ===
    created_by: Optional[str] = None
    """触发融合的用户/系统"""

    gmt_create: Optional[datetime] = None
    """创建时间"""

    gmt_modify: Optional[datetime] = None
    """修改时间"""

    env: str = field(default_factory=get_fusion_env)
    """环境标识（pre/prod），用于单一表多环境数据隔离"""

    def __post_init__(self):
        """初始化后处理"""
        if self.gmt_create is None:
            self.gmt_create = datetime.now()
        if self.gmt_modify is None:
            self.gmt_modify = datetime.now()
        if self.conversation_recent is None:
            self.conversation_recent = []
        if self.conversation_stats is None:
            self.conversation_stats = {"turns": 0, "avg_response_ms": 0, "avg_question_token": 0, "avg_response_token": 0}

    def get_participant_ids_list(self) -> list[str]:
        """获取参与者ID列表"""
        from src.utils.fuse_util import parse_participant_ids
        return parse_participant_ids(self.participant_ids)

    def to_dict(self) -> dict[str, Any]:
        """转换为字典（用于 JSON 序列化）"""
        return {
            "fusion_id": self.fusion_id,
            "fusion_mode": self.fusion_mode,
            "group_id": self.group_id,
            "driver_bot_id": self.driver_bot_id,
            "question": self.question,
            "participant_ids": self.participant_ids,
            "participant_profile_snapshot": self.participant_profile_snapshot,
            "fuse_detail": self.fuse_detail,
            "conversation_recent": self.conversation_recent,
            "conversation_stats": self.conversation_stats,
            "status": self.status,
            "fuse_message": self.fuse_message,
            "created_by": self.created_by,
            "gmt_create": self.gmt_create.isoformat() if self.gmt_create else None,
            "gmt_modify": self.gmt_modify.isoformat() if self.gmt_modify else None,
            "env": self.env,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "FusedProfileRecord":
        """从字典创建实例"""
        # 处理时间字段
        gmt_create = data.get("gmt_create")
        if isinstance(gmt_create, str):
            gmt_create = datetime.fromisoformat(gmt_create)
        gmt_modify = data.get("gmt_modify")
        if isinstance(gmt_modify, str):
            gmt_modify = datetime.fromisoformat(gmt_modify)

        return cls(
            fusion_id=data["fusion_id"],
            fusion_mode=data["fusion_mode"],
            group_id=data.get("group_id"),
            driver_bot_id=data.get("driver_bot_id"),
            question=data.get("question"),
            participant_ids=data.get("participant_ids", ""),
            participant_profile_snapshot=data.get("participant_profile_snapshot"),
            fuse_detail=data.get("fuse_detail"),
            conversation_recent=data.get("conversation_recent", []),
            conversation_stats=data.get("conversation_stats", {"turns": 0, "avg_response_ms": 0, "avg_question_token": 0, "avg_response_token": 0}),
            status=data.get("status", FusionStatus.SUCCESS.value),
            fuse_message=data.get("fuse_message"),
            created_by=data.get("created_by"),
            gmt_create=gmt_create,
            gmt_modify=gmt_modify,
            env=data.get("env", get_fusion_env()),
        )


__all__ = ["FusedProfileRecord"]