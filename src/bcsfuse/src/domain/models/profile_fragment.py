"""
Profile Fragment Domain Model

定义 Profile 语义片段模型，用于多向量索引。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
import hashlib


@dataclass
class ProfileFragment:
    """
    Profile 语义片段

    表示 WorkerProfile 分解后的语义独立片段，每个片段生成独立的 embedding。

    Attributes:
        fragment_type: 片段类型 (soul, skills, agent, tools, full, 等)
        content: 用于嵌入的文本内容
        weight: 检索权重（用于聚合时加权）
        index: 同类型内的索引序号
        subtype: 子类型（如具体 skill 名称，可选）
        description: 描述信息（可选）
        metadata: 额外元数据
        content_hash: 内容哈希（用于检测变化）
    """

    fragment_type: str
    content: str
    weight: float = 1.0
    index: int = 0
    subtype: str | None = None
    description: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    _content_hash: str | None = field(default=None, repr=False)

    def __post_init__(self):
        # 确保 content 不为 None
        if self.content is None:
            self.content = ""

    @property
    def is_empty(self) -> bool:
        """判断内容是否为空"""
        return not self.content or not self.content.strip()

    @property
    def content_preview(self, max_length: int = 200) -> str:
        """获取内容预览"""
        if not self.content:
            return ""
        return self.content[:max_length] + ("..." if len(self.content) > max_length else "")

    def get_full_content(self) -> str:
        """获取完整内容（不做截断）"""
        return self.content if self.content else ""

    def to_embedding_text(self) -> str:
        """
        生成用于 embedding 的文本

        返回原始内容，不添加任何类型前缀。
        """
        return self.content.strip()

    @property
    def content_hash(self) -> str:
        """
        计算内容哈希（用于快速检测变化）

        基于 content 和 metadata 中的关键字段计算。
        """
        if self._content_hash is None:
            # 混合 content 和关键 metadata 计算 hash
            hash_input = f"{self.fragment_type}:{self.index}:{self.content.strip()}"
            self._content_hash = hashlib.sha256(hash_input.encode()).hexdigest()[:16]
        return self._content_hash

    def compute_fragment_id(self, profile_key: str) -> str:
        """
        计算 fragment 的完整 ID

        Args:
            profile_key: Profile 标识 (如 "worker_id:profile_id")

        Returns:
            Fragment ID (如 "worker_id:profile_id:soul" 或 "worker_id:profile_id:soul:1")
        """
        if self.index > 0:
            return f"{profile_key}:{self.fragment_type}:{self.index}"
        return f"{profile_key}:{self.fragment_type}"


@dataclass
class FragmentMatch:
    """
    Fragment 匹配结果

    表示检索时某个 Fragment 的匹配信息。

    Attributes:
        fragment_type: 片段类型
        fragment_id: 完整的 fragment ID
        score: 原始相似度分数
        weighted_score: 加权后的分数
        content_preview: 内容预览
        content: 完整内容（不做截断）
        index: 在同类片段中的索引
    """

    fragment_type: str
    fragment_id: str
    score: float
    weighted_score: float = 0.0
    content_preview: str = ""
    content: str = ""  # 完整内容，用于reranker
    index: int = 0

    def __post_init__(self):
        if self.weighted_score == 0.0 and self.score > 0:
            self.weighted_score = self.score


@dataclass
class FragmentAggregatedResult:
    """
    Fragment 聚合结果

    表示按 profile_key 聚合后的结果。

    Attributes:
        profile_key: Profile 标识
        final_score: 聚合后的最终分数
        fragments: 匹配的 fragments 列表
        best_fragment_score: 最高分的 fragment 分数
        weighted_sum: 加权和
        total_weight: 总权重
        metadata: 完整的 profile 元数据
    """

    profile_key: str
    final_score: float = 0.0
    fragments: list[FragmentMatch] = field(default_factory=list)
    best_fragment_score: float = 0.0
    weighted_sum: float = 0.0
    total_weight: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)


class AggregationStrategy:
    """
    聚合策略枚举
    """

    BEST_MATCH = "best_match"           # 取最高分的 fragment
    WEIGHTED_AVG = "weighted_avg"       # 加权平均
    WEIGHTED_BEST = "weighted_best"   # 加权后取最好
    TOP3_MEAN = "top3_mean"             # Top3 fragment 平均
    EXP_BOOST = "exp_boost"             # 指数加权
    WEIGHTED_SUM = "weighted_sum"       # 加权分数求和（默认）


__all__ = [
    "ProfileFragment",
    "FragmentMatch",
    "FragmentAggregatedResult",
    "AggregationStrategy",
]
