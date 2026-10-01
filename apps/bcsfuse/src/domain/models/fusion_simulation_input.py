"""
Fusion Simulation Input

Worker Profile Retrieval & Fusion Simulation Baseline

Fusion Simulation 输入模型。
"""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field

from src.domain.models.retrieval_mode import RetrievalMode
from src.domain.models.worker_profile import WorkerProfile
from src.domain.models.worker_context_digest import WorkerContextDigest


class FusionSimulationInput(BaseModel):
    """
    Fusion Simulation 输入模型

    用于存储 fusion simulation 的输入信息。

    Attributes:
        question: 问题/任务描述
        mode: 检索模式
        profiles: 候选 profile 列表
        context_digests: 上下文摘要列表
        max_perspectives: 最大视角数
        options: 额外选项
    """

    # 基本信息
    question: str = Field(..., min_length=1, description="问题/任务描述")
    mode: RetrievalMode = Field(..., description="检索模式")

    # 候选数据
    profiles: list[WorkerProfile] = Field(
        default_factory=list,
        description="候选 profile 列表"
    )
    context_digests: list[WorkerContextDigest] = Field(
        default_factory=list,
        description="上下文摘要列表"
    )

    # 配置
    max_perspectives: int = Field(
        default=3,
        ge=1,
        le=10,
        description="最大视角数"
    )
    options: dict[str, Any] = Field(
        default_factory=dict,
        description="额外选项"
    )

    @property
    def profile_count(self) -> int:
        """
        获取 profile 数量

        Returns:
            profile 数量
        """
        return len(self.profiles)

    @classmethod
    def from_fusion_request(
        cls,
        fusion_request: Any,  # FusionRequest 类型，避免循环导入
        profiles: Optional[list[WorkerProfile]] = None,
        context_digests: Optional[list[WorkerContextDigest]] = None,
        max_perspectives: int = 3,
    ) -> "FusionSimulationInput":
        """
        从 FusionRequest 创建 FusionSimulationInput

        Args:
            fusion_request: FusionRequest 对象
            profiles: 可选的 profile 列表
            context_digests: 可选的上下文摘要列表
            max_perspectives: 最大视角数（默认 3）

        Returns:
            FusionSimulationInput 对象

        Note:
            FusionRequest 不包含 max_perspectives 字段，需单独传入。
            participants 字段可用于后续 profile 过滤。
        """
        mode = RetrievalMode.from_fusion_mode(fusion_request.fusion_mode)

        # 从 FusionRequest.options 提取相关选项
        options: dict[str, Any] = {}
        if hasattr(fusion_request, 'options') and fusion_request.options:
            options = {
                "timeout_ms": getattr(fusion_request.options, 'timeout_ms', 15000),
                "parallel": getattr(fusion_request.options, 'parallel', True),
                "participants": list(fusion_request.participants) if hasattr(fusion_request, 'participants') else [],
            }
            # 合并其他选项
            for key in ['detect_conflicts', 'extract_alignment_points',
                        'enable_risk_assessment', 'enable_expert_recommendations',
                        'enable_go_live_conditions']:
                if hasattr(fusion_request.options, key):
                    options[key] = getattr(fusion_request.options, key)

        return cls(
            question=fusion_request.question,
            mode=mode,
            profiles=profiles or [],
            context_digests=context_digests or [],
            max_perspectives=max_perspectives,
            options=options,
        )

    model_config = {
        "extra": "forbid",
    }


__all__ = ["FusionSimulationInput"]