"""
FusionContext - 融合上下文

封装融合所需的所有上下文参数，简化函数签名。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.domain.models.worker_profile_content import WorkerProfileContent


@dataclass
class FusionContext:
    """
    融合上下文

    封装融合过程中需要传递的所有参数，包括：
    - 标识信息：fusion_id, group_id, driver_bot_id
    - 参与者信息：participant_ids, profiles
    - 请求信息：question, refresh
    - Profile 快照：profiles_dict（用于 fusion_id 计算和存储）

    使用场景：
    1. generate_fusion_id() - 计算 fusion_id
    2. ProfileMergeService.fuse_profiles() - 执行融合
    3. FusedProfileStorageService.save_fused_profile() - 存储结果
    """

    # === 核心标识 ===
    group_id: str
    """群组 ID"""

    driver_bot_id: str | None = None
    """发起融合的 bot ID"""

    # === 参与者信息 ===
    participant_ids: list[str] = field(default_factory=list)
    """参与者 ID 列表"""

    profiles: list["WorkerProfileContent"] = field(default_factory=list)
    """已收集的 WorkerProfileContent 列表"""

    profiles_dict: list[dict] = field(default_factory=list)
    """Profile 快照字典列表（用于 fusion_id 计算和存储）"""

    # === 请求信息 ===
    question: str | None = None
    """融合问题"""

    refresh: bool = False
    """是否强制刷新缓存"""

    # === 运行时生成 ===
    fusion_id: str = ""
    """融合唯一标识（由上层生成后设置）"""

    def __post_init__(self):
        """初始化后处理"""
        # 如果没有传入 participant_ids，从 profiles 提取
        if not self.participant_ids and self.profiles:
            self.participant_ids = [p.worker_id for p in self.profiles]

    @classmethod
    def from_request(
        cls,
        request: "FusionRequest",
        group_id: str,
        profiles: list["WorkerProfileContent"],
        profiles_dict: list[dict],
    ) -> "FusionContext":
        """
        从 FusionRequest 创建 FusionContext

        Args:
            request: 融合请求
            group_id: 群组 ID
            profiles: 已收集的 WorkerProfileContent 列表
            profiles_dict: Profile 快照字典列表

        Returns:
            FusionContext 实例
        """
        driver_bot_id = request.driver_bot_id
        if driver_bot_id is None and request.participants:
            driver_bot_id = request.participants[0]

        return cls(
            group_id=group_id,
            driver_bot_id=driver_bot_id,
            participant_ids=request.participants,
            profiles=profiles,
            profiles_dict=profiles_dict,
            question=request.question,
            refresh=request.options.refresh if request.options else False,
        )


__all__ = ["FusionContext"]