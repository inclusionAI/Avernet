"""
FusionRequest

G1: Fusion Entry Layer / G2: Conflict Alignment Layer / G5: Expert Diagnosis Layer

融合请求的领域模型定义。

注意：为了保持向后兼容：
- `mode` 字段保留（G1 使用，固定为 "agent"）
- `fusion_mode` 字段新增（支持 G1/G2/G5 模式区分）

timeout_ms 限制（已根据真实 LLM 调用时间调整）：
- G1/G2 模式：最大 600000ms（10分钟）
- G5 expert_diagnosis 模式：最大 600000ms（10分钟）
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field, model_validator


# 超时限制常量（已根据真实 LLM 调用时间调整）
DEFAULT_TIMEOUT_MS = 120000  # 默认 120 秒
MIN_TIMEOUT_MS = 1000
MAX_TIMEOUT_MS_NORMAL = 600000  # G1/G2 模式最大 10 分钟
MAX_TIMEOUT_MS_EXPERT_DIAGNOSIS = 600000  # G5 模式最大 10 分钟


class FuseOptions(BaseModel):
    """
    融合选项

    控制融合行为的配置选项。
    """

    model_config = {"extra": "forbid"}

    timeout_ms: int = Field(
        default=DEFAULT_TIMEOUT_MS,
        ge=MIN_TIMEOUT_MS,
        le=MAX_TIMEOUT_MS_EXPERT_DIAGNOSIS,  # 放宽到最大值，实际校验在 FusionRequest 中
        description="融合操作超时时间（毫秒）。G1/G2/G5 最大 600000ms（10分钟）",
    )
    parallel: bool = Field(
        default=True,
        description="是否并行收集 participant 视角",
    )
    include_recommendation: bool = Field(
        default=True,
        description="是否生成 recommendation",
    )
    include_transcript: bool = Field(
        default=False,
        description="是否包含完整 transcript",
    )
    strict_participants: bool = Field(
        default=True,
        description="participant 解析失败时是否硬失败",
    )
    fail_fast: bool = Field(
        default=False,
        description="是否在第一个失败时立即返回",
    )
    # G2 特有选项
    detect_conflicts: bool = Field(
        default=False,
        description="是否启用冲突检测（G2）",
    )
    extract_alignment_points: bool = Field(
        default=False,
        description="是否提取对齐点（G2）",
    )
    # G5 特有选项
    enable_risk_assessment: bool = Field(
        default=True,
        description="是否启用风险评估（G5）",
    )
    enable_expert_recommendations: bool = Field(
        default=True,
        description="是否生成专家建议列表（G5）",
    )
    enable_go_live_conditions: bool = Field(
        default=True,
        description="是否生成上线条件（G5）",
    )
    # 缓存控制
    refresh: bool = Field(
        default=False,
        description="是否强制刷新，跳过缓存直接重新计算",
    )


class FuseMetadata(BaseModel):
    """
    融合元数据

    请求的追踪和来源信息。
    """

    model_config = {"extra": "forbid"}

    request_id: Optional[str] = Field(
        default=None,
        max_length=128,
        description="请求 ID",
    )
    source: Optional[str] = Field(
        default=None,
        max_length=64,
        description="请求来源",
    )
    operator: Optional[str] = Field(
        default=None,
        max_length=128,
        description="操作者",
    )
    trace_id: Optional[str] = Field(
        default=None,
        max_length=128,
        description="追踪 ID",
    )


class FusionRequest(BaseModel):
    """
    融合请求

    在指定 group 上对一个问题发起多参与者视角融合。

    字段说明：
    - mode: 保留字段，固定为 "agent"，用于 G1 向后兼容
    - fusion_mode: 新增字段，支持 "agent"（G1）、"conflict_alignment"（G2）或 "expert_diagnosis"（G5）
    - timeout_ms: G1/G2/G5 最大 600000ms（10分钟）
    """

    model_config = {"extra": "forbid"}

    question: str = Field(
        min_length=1,
        max_length=2000,
        description="需要多方协作评估的问题",
    )
    participants: list[str] = Field(
        min_length=1,
        max_length=20,
        description="参与者标识符列表",
    )
    driver_bot_id: Optional[str] = Field(
        default=None,
        min_length=1,
        max_length=128,
        description="显式指定的 driver bot ID",
    )
    mode: Literal["agent"] = Field(
        default="agent",
        description="融合模式（保留，向后兼容）",
    )
    fusion_mode: Literal["agent", "conflict_alignment", "expert_diagnosis", "bot_profile_fuse"] = Field(
        default="agent",
        description="融合模式：agent（G1）、conflict_alignment（G2）、expert_diagnosis（G5）或 bot_profile_fuse（G9）",
    )
    options: FuseOptions = Field(
        default_factory=FuseOptions,
        description="融合选项",
    )
    metadata: Optional[FuseMetadata] = Field(
        default=None,
        description="请求元数据",
    )

    @model_validator(mode="after")
    def validate_timeout_for_mode(self) -> "FusionRequest":
        """
        根据 fusion_mode 动态校验 timeout_ms

        - G1/G2/G5 模式：最大 600000ms（10 分钟）
        """
        timeout_ms = self.options.timeout_ms

        if self.fusion_mode == "expert_diagnosis":
            # G5 模式允许最大 10 分钟
            max_timeout = MAX_TIMEOUT_MS_EXPERT_DIAGNOSIS
            mode_name = "G5 expert_diagnosis"
        else:
            # G1/G2 模式最大 10 分钟
            max_timeout = MAX_TIMEOUT_MS_NORMAL
            mode_name = f"G1/G2 ({self.fusion_mode})"

        if timeout_ms > max_timeout:
            raise ValueError(
                f"timeout_ms={timeout_ms} exceeds maximum allowed for {mode_name} mode "
                f"(max={max_timeout}ms). "
                f"G1/G2 modes allow up to {MAX_TIMEOUT_MS_NORMAL}ms, "
                f"G5 expert_diagnosis allows up to {MAX_TIMEOUT_MS_EXPERT_DIAGNOSIS}ms."
            )

        return self


__all__ = [
    "FusionRequest",
    "FuseOptions",
    "FuseMetadata",
]