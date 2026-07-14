"""
Dense Retriever

Phase E: Dense 向量检索（主召回）

职责：
- 基于 embedding 的语义检索
- 使用 FAISS 进行快速 ANN 搜索
- 作为主召回引擎
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Optional

from src.domain.models.retrieval_result import RetrievalProvenance, RetrievalResult
from src.infra.config.feature_flags import FeatureFlags
from src.infra.indexing.profile_embedding_store import ProfileEmbeddingStore

logger = logging.getLogger(__name__)


@dataclass
class DenseRetrievalConfig:
    """Dense 检索配置"""
    default_top_k: int = 10
    min_score: float = 0.5


class DenseRetriever:
    """
    Dense 向量检索器
    
    使用 embedding 进行语义检索，作为主召回引擎。
    
    特点：
    - 语义理解能力强
    - 支持近似最近邻搜索（ANN）
    - 需要预先构建索引
    
    降级策略：
    - 索引不可用 → 返回空，由上层决定是否 fallback
    - Embedding provider 不可用 → 返回空
    """
    
    def __init__(
        self,
        embedding_provider: Any,  # EmbeddingProvider
        profile_store: ProfileEmbeddingStore,
        config: Optional[DenseRetrievalConfig] = None,
    ):
        """
        初始化 Dense Retriever
        
        Args:
            embedding_provider: Embedding provider
            profile_store: Profile embedding store
            config: 检索配置
        """
        self._embedding_provider = embedding_provider
        self._profile_store = profile_store
        self._config = config or DenseRetrievalConfig()
    
    def retrieve(
        self,
        query: str,
        top_k: int = 10,
        filters: Optional[dict] = None,
        candidate_scope: Optional[set[str]] = None,
    ) -> list[RetrievalResult]:
        """
        执行 Dense 检索
        
        Args:
            query: 查询文本
            top_k: 返回数量
            filters: 过滤条件
            candidate_scope: 候选范围（用于 strict 模式）
        
        Returns:
            检索结果列表
        """
        # 检查 feature flag
        if not FeatureFlags.is_dense_retrieval_enabled():
            logger.debug("[DenseRetriever] Dense retrieval disabled by feature flag")
            return []
        
        # 检查索引可用性
        if not self._profile_store.is_index_available():
            logger.warning("[DenseRetriever] Profile embedding index not available")
            return []
        
        logger.info(
            "[DenseRetriever] Starting dense retrieval: query_len=%d, top_k=%d, scope_size=%s",
            len(query), top_k, len(candidate_scope) if candidate_scope else "all"
        )
        
        try:
            # 生成查询 embedding
            query_embedding = self._embedding_provider.embed(query)
            
            if not query_embedding:
                logger.error("[DenseRetriever] Failed to generate query embedding")
                return []
            
            # 执行向量搜索
            search_results = self._profile_store.search_similar(
                query_vector=query_embedding,
                top_k=top_k * 2,  # 多取一些，便于过滤
                filters=filters,
            )
            
            # 转换为 RetrievalResult
            results = []
            for profile_key, score, metadata in search_results:
                # strict 模式：检查是否在 candidate_scope 内
                if candidate_scope and profile_key not in candidate_scope:
                    logger.debug(
                        "[DenseRetriever] Skipping out-of-scope candidate: %s",
                        profile_key
                    )
                    continue
                
                # 创建 RetrievalResult
                provenance = RetrievalProvenance(
                    dense_score=score,
                    final_score=score,
                    metadata={"source": "dense", "model_version": getattr(self._embedding_provider, 'model_version', 'unknown')},
                )
                
                result = RetrievalResult(
                    profile_key=profile_key,
                    score=score,
                    provenance=provenance,
                    metadata=metadata,
                )
                
                results.append(result)
                
                # 达到 top_k 就停止
                if len(results) >= top_k:
                    break
            
            logger.info(
                "[DenseRetriever] Dense retrieval completed: results=%d",
                len(results)
            )
            
            return results
        
        except Exception as e:
            logger.error(
                "[DenseRetriever] Dense retrieval failed: %s",
                str(e)
            )
            return []
    
    def retrieve_by_vector(
        self,
        query_vector: list[float],
        top_k: int = 10,
        candidate_scope: Optional[set[str]] = None,
    ) -> list[RetrievalResult]:
        """
        通过向量直接检索
        
        Args:
            query_vector: 查询向量
            top_k: 返回数量
            candidate_scope: 候选范围
        
        Returns:
            检索结果列表
        """
        # 检查索引可用性
        if not self._profile_store.is_index_available():
            return []
        
        # 执行搜索
        search_results = self._profile_store.search_similar(
            query_vector=query_vector,
            top_k=top_k * 2,
        )
        
        # 转换为 RetrievalResult
        results = []
        for profile_key, score, metadata in search_results:
            # strict 模式检查
            if candidate_scope and profile_key not in candidate_scope:
                continue
            
            provenance = RetrievalProvenance(
                dense_score=score,
                final_score=score,
            )
            
            result = RetrievalResult(
                profile_key=profile_key,
                score=score,
                provenance=provenance,
                metadata=metadata,
            )
            
            results.append(result)
            
            if len(results) >= top_k:
                break
        
        return results
    
    def is_available(self) -> bool:
        """
        检查 Dense Retriever 是否可用
        
        Returns:
            是否可用
        """
        return (
            FeatureFlags.is_dense_retrieval_enabled()
            and self._profile_store.is_index_available()
        )


__all__ = [
    "DenseRetriever",
    "DenseRetrievalConfig",
]
