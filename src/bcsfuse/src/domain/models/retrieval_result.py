"""
RetrievalResult - Hybrid Retrieval 结果模型

Phase E: Hybrid retrieval 统一返回格式

设计原则：
- 纯数据模型，不包含业务逻辑
- 不包含 strict 约束校验（在 service 层处理）
- 以 profile_key 为主，不强绑定 WorkerProfile
- 字段尽量 optional，避免破坏旧调用方
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class RetrievalProvenance:
    """
    Retrieval 来源追溯
    
    记录分数来源和过滤路径
    
    Attributes:
        dense_score: Dense retrieval 分数
        sparse_score: Sparse retrieval 分数
        final_score: 最终分数
        dense_contribution: Dense 贡献值
        sparse_contribution: Sparse 贡献值
        passed_filters: 通过的过滤器列表
        failed_filters: 未通过的过滤器列表
        fallback_path: 降级路径
        metadata: 额外元数据
    """
    dense_score: Optional[float] = None
    sparse_score: Optional[float] = None
    final_score: float = 0.0
    dense_contribution: float = 0.0
    sparse_contribution: float = 0.0
    passed_filters: list[str] = field(default_factory=list)
    failed_filters: list[str] = field(default_factory=list)
    fallback_path: Optional[str] = None
    metadata: dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> dict[str, Any]:
        """转换为字典格式"""
        result = {
            "final_score": self.final_score,
        }
        
        if self.dense_score is not None:
            result["dense_score"] = self.dense_score
            
        if self.sparse_score is not None:
            result["sparse_score"] = self.sparse_score
            
        if self.dense_contribution != 0.0:
            result["dense_contribution"] = self.dense_contribution
            
        if self.sparse_contribution != 0.0:
            result["sparse_contribution"] = self.sparse_contribution
            
        if self.passed_filters:
            result["passed_filters"] = self.passed_filters
            
        if self.failed_filters:
            result["failed_filters"] = self.failed_filters
            
        if self.fallback_path:
            result["fallback_path"] = self.fallback_path
            
        if self.metadata:
            result["metadata"] = self.metadata
        
        return result


@dataclass
class RetrievalResult:
    """
    Hybrid Retrieval 单个结果
    
    设计原则：
    - 以 profile_key 为主标识
    - 不强绑定 WorkerProfile（避免循环依赖）
    - 包含来源追溯信息
    
    Attributes:
        profile_key: Profile 唯一标识
        score: 最终分数
        provenance: 来源追溯
        evidences: 相关证据列表（可选）
        metadata: 额外元数据
        profile: WorkerProfile 对象（可选，用于兼容）
    """
    profile_key: str
    score: float
    provenance: RetrievalProvenance = field(default_factory=RetrievalProvenance)
    evidences: Optional[list[Any]] = None
    metadata: dict[str, Any] = field(default_factory=dict)
    profile: Optional[Any] = None  # 用于向后兼容
    
    def validate(self) -> bool:
        """
        轻量校验
        
        Returns:
            是否有效
        """
        if not self.profile_key:
            return False
        
        if self.score < 0:
            return False
        
        return True
    
    def to_dict(self) -> dict[str, Any]:
        """转换为字典格式"""
        result = {
            "profile_key": self.profile_key,
            "score": self.score,
            "provenance": self.provenance.to_dict(),
        }
        
        if self.evidences:
            result["evidences"] = self.evidences
        
        if self.metadata:
            result["metadata"] = self.metadata
        
        return result


@dataclass
class RetrievalExplanation:
    """
    Retrieval 解释项

    记录检索匹配的详细说明，用于透明化检索过程。

    Attributes:
        candidate_type: 候选类型 (worker/knowledge/skill/resource)
        candidate_id: 候选 ID
        matched_fields: 匹配的字段列表
        match_reason: 匹配原因
        score: 匹配分数
    """
    candidate_type: str
    candidate_id: str
    matched_fields: list[str] = field(default_factory=list)
    match_reason: str = ""
    score: float = 0.0


__all__ = [
    "RetrievalProvenance",
    "RetrievalResult",
    "RetrievalExplanation",
]
