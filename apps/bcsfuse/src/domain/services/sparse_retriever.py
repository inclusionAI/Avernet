"""
Sparse Retriever

Phase E: Sparse 文本检索（辅助召回）

职责：
- BM25 / 关键词检索
- 作为 dense retrieval 不可用时的 fallback
- 不依赖 embedding
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any, Optional

from src.domain.models.retrieval_result import RetrievalProvenance, RetrievalResult
from src.infra.config.feature_flags import FeatureFlags

logger = logging.getLogger(__name__)


@dataclass
class SparseRetrievalConfig:
    """Sparse 检索配置"""
    bm25_k1: float = 1.5
    bm25_b: float = 0.75
    min_score: float = 0.1


class SparseRetriever:
    """
    Sparse 文本检索器
    
    使用 BM25 / 关键词匹配进行检索。
    作为 dense retrieval 的辅助或 fallback。
    
    特点：
    - 不依赖 embedding
    - 精确关键词匹配
    - 可解释性强
    """
    
    def __init__(
        self,
        config: Optional[SparseRetrievalConfig] = None,
    ):
        """
        初始化 Sparse Retriever
        
        Args:
            config: 检索配置
        """
        self._config = config or SparseRetrievalConfig()
        self._doc_cache: dict[str, list[str]] = {}
    
    def retrieve(
        self,
        query: str,
        candidates: list[Any],  # list of profiles or dicts
        top_k: int = 10,
        filters: Optional[dict] = None,
    ) -> list[RetrievalResult]:
        """
        执行 Sparse 检索
        
        Args:
            query: 查询文本
            candidates: 候选列表
            top_k: 返回数量
            filters: 过滤条件
        
        Returns:
            检索结果列表
        """
        if not candidates:
            return []
        
        # 检查 feature flag
        if not FeatureFlags.is_sparse_retrieval_enabled():
            logger.debug("[SparseRetriever] Sparse retrieval disabled by feature flag")
            return []
        
        logger.info(
            "[SparseRetriever] Starting sparse retrieval: query_len=%d, candidates=%d, top_k=%d",
            len(query), len(candidates), top_k
        )
        
        # 预处理查询
        query_terms = self._tokenize(query)
        
        # 计算每个候选的 BM25 分数
        scored_candidates = []
        
        for candidate in candidates:
            # 获取候选文本
            candidate_text = self._get_candidate_text(candidate)
            candidate_key = self._get_candidate_key(candidate)
            
            # Tokenize
            doc_terms = self._tokenize(candidate_text)
            
            # 计算 BM25 分数
            score = self._compute_bm25_score(query_terms, doc_terms)
            
            if score >= self._config.min_score:
                # 创建 RetrievalResult
                provenance = RetrievalProvenance(
                    sparse_score=score,
                    final_score=score,
                    metadata={"source": "sparse", "matched_terms": list(set(query_terms) & set(doc_terms))[:10]},
                )
                
                result = RetrievalResult(
                    profile_key=candidate_key,
                    score=score,
                    provenance=provenance,
                    metadata={"sparse_retrieval": True},
                )
                
                # 如果是 WorkerProfile，附加
                if hasattr(candidate, 'profile_key'):
                    result.profile = candidate
                
                scored_candidates.append((score, result))
        
        # 排序并返回 top_k
        scored_candidates.sort(key=lambda x: x[0], reverse=True)
        results = [r for _, r in scored_candidates[:top_k]]
        
        logger.info(
            "[SparseRetriever] Sparse retrieval completed: results=%d",
            len(results)
        )
        
        return results
    
    def _tokenize(self, text: str) -> list[str]:
        """
        分词
        
        Args:
            text: 文本
        
        Returns:
            词项列表
        """
        # 简单分词：小写 + 去标点
        text = text.lower()
        tokens = re.findall(r'\b\w+\b', text)
        
        # 过滤停用词（简化版）
        stopwords = {
            "a", "an", "the", "is", "are", "was", "were", "be", "been",
            "being", "have", "has", "had", "do", "does", "did", "will",
            "would", "could", "should", "may", "might", "must", "shall",
            "can", "to", "of", "in", "for", "on", "with", "at", "by",
            "from", "as", "into", "through", "during", "before", "after",
        }
        
        tokens = [t for t in tokens if t not in stopwords and len(t) > 1]
        
        return tokens
    
    def _get_candidate_text(self, candidate: Any) -> str:
        """获取候选文本"""
        if hasattr(candidate, 'searchable_text'):
            return candidate.searchable_text or ""
        elif isinstance(candidate, dict):
            return candidate.get('searchable_text', "")
        else:
            return str(candidate)
    
    def _get_candidate_key(self, candidate: Any) -> str:
        """获取候选 key"""
        if hasattr(candidate, 'profile_key'):
            return candidate.profile_key
        elif isinstance(candidate, dict):
            return candidate.get('profile_key', str(id(candidate)))
        else:
            return str(id(candidate))
    
    def _compute_bm25_score(
        self,
        query_terms: list[str],
        doc_terms: list[str],
    ) -> float:
        """
        计算 BM25 分数（简化版）
        
        BM25(Q, D) = Σ IDF(qi) * (f(qi, D) * (k1 + 1)) / (f(qi, D) + k1 * (1 - b + b * |D| / avgdl))
        
        Args:
            query_terms: 查询词项
            doc_terms: 文档词项
        
        Returns:
            BM25 分数
        """
        if not query_terms or not doc_terms:
            return 0.0
        
        # 统计词频
        doc_len = len(doc_terms)
        term_freq = {}
        for term in doc_terms:
            term_freq[term] = term_freq.get(term, 0) + 1
        
        # 计算每个查询词项的分数
        score = 0.0
        k1 = self._config.bm25_k1
        b = self._config.bm25_b
        
        # 假设平均文档长度为 100
        avgdl = 100.0
        
        for term in query_terms:
            if term in term_freq:
                f = term_freq[term]
                
                # IDF（简化：假设 N=1000, n=文档频率=10）
                idf = 2.3  # log(1000/10)
                
                # BM25 公式
                numerator = f * (k1 + 1)
                denominator = f + k1 * (1 - b + b * doc_len / avgdl)
                
                score += idf * numerator / denominator
        
        # 归一化到 [0, 1]
        normalized_score = min(1.0, score / 20.0)
        
        return normalized_score


__all__ = [
    "SparseRetriever",
    "SparseRetrievalConfig",
]
