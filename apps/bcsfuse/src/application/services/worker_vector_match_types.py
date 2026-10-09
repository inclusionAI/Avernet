"""Value objects shared by legacy and fragment matching implementations."""
from __future__ import annotations
from dataclasses import dataclass, field
from src.domain.models.metadata_record import MetadataRecord
from src.domain.models.profile_fragment import FragmentMatch

@dataclass
class MatchResult:
    """匹配结果（V2 扩展）。

    Attributes:
        profile_key: Profile 唯一标识
        metadata: 完整的元数据记录
        score: 最终得分（向量相似度 + rerank 加分）
        aggregated_score: 【Fragment模式】聚合前的原始分数
        reasons: 得分原因说明列表
        fragment_matches: 【新增】匹配的 fragments 列表（用于解释）
        is_reranked: 【新增】是否经过 Reranker 精排
    """
    profile_key: str
    metadata: MetadataRecord
    score: float
    aggregated_score: float | None = None
    reasons: list[str] = field(default_factory=list)
    fragment_matches: list[FragmentMatch] = field(default_factory=list)
    is_reranked: bool = False


@dataclass
class FragmentProfileCandidate:
    """Fragment 模式下的候选 Profile"""
    profile_key: str
    aggregated_score: float
    fragments: list[FragmentMatch]
    metadata: dict = field(default_factory=dict)
