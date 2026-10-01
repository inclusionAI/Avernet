"""
Hybrid Retrieval Service - 混合召回服务

统一召回入口，协调 Dense + Sparse + Structured 三层召回。

设计原则：
- 单一来源：所有降级链路必须通过本服务，禁止散落实现
- strict_participants：严格约束候选范围，不扩展召回范围
- Fallback Chain：Dense -> Sparse -> Structured
- 评分单一来源：所有评分通过 RetrievalScorer

Phase E 核心服务。

Fallback Chain（降级链路）：
    1. Dense (embedding 主召回)
       ↓ 不可用或无结果
    2. Sparse (BM25 文本召回)
       ↓ 不可用或无结果
    3. Structured (结构化过滤)
"""

from __future__ import annotations

import logging
import time
from typing import Any, Optional

from src.domain.models.hybrid_retrieval_result import (
    FallbackReason,
    HybridRetrievalContext,
    HybridRetrievalResult,
    RetrievalCandidate,
    RetrievalSource,
)
from src.domain.models.hybrid_score import HybridScore
from src.domain.models.worker_profile import WorkerProfile
from src.domain.services.dense_retriever import DenseRetriever
from src.domain.services.sparse_retriever import SparseRetriever
from src.domain.services.retrieval_scorer import RetrievalScorer
from src.infra.config.feature_flags import FeatureFlags

logger = logging.getLogger(__name__)


class HybridRetrievalService:
    """
    混合召回服务

    单一来源要求：
    - 所有 HybridRetrievalResult 的构造必须通过本服务
    - 降级链路在斜本服务中统一定义
    - 评分通过 RetrievalScorer

    Usage:
        service = HybridRetrievalService(
            dense_retriever=dense_retriever,
            sparse_retriever=sparse_retriever,
            retrieval_scorer=retrieval_scorer,
        )

        result = service.retrieve(context)
    """

    def __init__(
        self,
        dense_retriever: DenseRetriever,
        sparse_retriever: SparseRetriever,
        retrieval_scorer: RetrievalScorer,
        feature_flags: Optional[FeatureFlags] = None,
    ):
        """
        初始化混合召回服务

        Args:
            dense_retriever: Dense 召回器
            sparse_retriever: Sparse 召回器
            retrieval_scorer: 评分器（单一来源）
            feature_flags: Feature Flags
        """
        self._dense_retriever = dense_retriever
        self._sparse_retriever = sparse_retriever
        self._retrieval_scorer = retrieval_scorer
        self._feature_flags = feature_flags or FeatureFlags()

    def retrieve(self, context: HybridRetrievalContext) -> HybridRetrievalResult:
        """
        执行混合召回（单一来源入口）

        Fallback Chain（降级链路）：
            1. Dense (embedding 主召回)
               ↓ 不可用或无结果
            2. Sparse (BM25 文本召回)
               ↓ 不可用或无结果
            3. Structured (结构化过滤)

        Args:
            context: 召回上下文

        Returns:
            HybridRetrievalResult 召回结果
        """
        start_time = time.time()

        logger.info(
            f"[HybridRetrievalService] Starting retrieval, "
            f"question='{context.question[:50]}...', "
            f"strict={context.strict}, "
            f"profile_keys={context.profile_keys}"
        )

        # =====================================
        # S1: strict + profile_keys 约束
        # =====================================
        if context.strict and context.profile_keys:
            logger.info(
                f"[HybridRetrievalService] strict=true, "
                f"limiting candidates to {len(context.profile_keys)} profiles"
            )

        # 初始化结果
        result = HybridRetrievalResult(
            question=context.question,
            candidates=[],
            source=RetrievalSource.HYBRID,
            fallback_chain=[],
        )

        # =====================================
        # Phase 1: Dense Retrieval
        # =====================================
        if context.enable_dense and self._feature_flags.ENABLE_DENSE_RETRIEVAL:
            dense_candidates, query_embedding, dense_reason = self._retrieve_dense(context)

            if dense_candidates:
                logger.info(f"[HybridRetrievalService] Dense retrieved {len(dense_candidates)} candidates")
                result.candidates.extend(dense_candidates)
                result.query_embedding = query_embedding
                result.dense_candidates = len(dense_candidates)
                result.dense_latency_ms = (time.time() - start_time) * 1000

                # Dense 成功，直接跳到评分阶段
                if len(dense_candidates) >= context.top_k:
                    result.fallback_chain.append("dense")
                else:
                    # Dense 结果不足，尝试 Sparse 补充
                    result.fallback_chain.append("dense")
                    logger.info(
                        f"[HybridRetrievalService] Dense returned {len(dense_candidates)} < {context.top_k}, "
                        "trying Sparse to supplement"
                    )
            else:
                # Dense 失败或无结果
                logger.warning(
                    f"[HybridRetrievalService] Dense failed or empty: {dense_reason.value}"
                )
                result.fallback_occurred = True
                result.fallback_reason = dense_reason
                result.fallback_chain.append("dense_failed")
                result.query_embedding = query_embedding

        # =====================================
        # Phase 2: Sparse Retrieval
        # =====================================
        if context.enable_sparse and self._feature_flags.ENABLE_SPARSE_RETRIEVAL:
            # 判断是否需要 Sparse
            need_sparse = (
                len(result.candidates) < context.top_k
                or result.fallback_occurred
                or not self._feature_flags.ENABLE_DENSE_RETRIEVAL
            )

            if need_sparse:
                sparse_start = time.time()
                sparse_candidates = self._retrieve_sparse(context)

                if sparse_candidates:
                    logger.info(f"[HybridRetrievalService] Sparse retrieved {len(sparse_candidates)} candidates")
                    # 合并候选（去重）
                    existing_keys = {c.profile_key for c in result.candidates}
                    new_candidates = [
                        c for c in sparse_candidates if c.profile_key not in existing_keys
                    ]
                    result.candidates.extend(new_candidates)
                    result.sparse_candidates = len(new_candidates)
                    result.sparse_latency_ms = (time.time() - sparse_start) * 1000

                    if "dense" not in result.fallback_chain and "dense_failed" not in result.fallback_chain:
                        # Pure Sparse
                        result.fallback_chain.append("sparse")
                        result.fallback_occurred = True
                        result.fallback_reason = FallbackReason.FEATURE_FLAG_DISABLED
                    else:
                        # Sparse 补充 Dense
                        if "dense_failed" in result.fallback_chain:
                            result.fallback_chain.append("sparse")
                            result.fallback_reason = FallbackReason.NONE
                else:
                    logger.warning("[HybridRetrievalService] Sparse returned empty")
                    result.fallback_chain.append("sparse_failed")

        # =====================================
        # Phase 3: Structured Filtering (Fallback)
        # =====================================
        if context.enable_structured and len(result.candidates) == 0:
            # 如果 Dense 和 Sparse 都失败，尝试纯结构化过滤
            structured_start = time.time()
            structured_candidates = self._retrieve_structured(context)

            if structured_candidates:
                logger.info(
                    f"[HybridRetrievalService] Structured retrieved {len(structured_candidates)} candidates"
                )
                result.candidates.extend(structured_candidates)
                result.structured_latency_ms = (time.time() - structured_start) * 1000
                result.fallback_chain.append("structured")
                result.fallback_occurred = True
                result.fallback_reason = FallbackReason.EMPTY_DENSE_RESULT
                result.source = RetrievalSource.STRUCTURED
            else:
                logger.warning("[HybridRetrievalService] All retrieval methods failed or returned empty")

        # =====================================
        # S4: strict 后置验证
        # =====================================
        if context.strict and context.profile_keys:
            result.candidates = [
                c for c in result.candidates if c.profile_key in context.profile_keys
            ]
            if not result.candidates:
                logger.warning(
                    "[HybridRetrievalService] strict=true, no candidates after filtering, "
                    "returning empty result (no fallback)"
                )
                result.total_candidates = 0
                result.latency_ms = (time.time() - start_time) * 1000
                return result

        # =====================================
        # 评分阶段（单一来源：RetrievalScorer）
        # =====================================
        if result.candidates:
            result.candidates = self._score_candidates(result.candidates, context)

        # =====================================
        # 排序和截断
        # =====================================
        result.candidates.sort(key=lambda c: c.score, reverse=True)
        result.candidates = result.candidates[: context.top_k]
        result.total_candidates = len(result.candidates)

        # 计算总耗时
        result.latency_ms = (time.time() - start_time) * 1000

        # 记录启用的 flags
        result.flags_enabled = self._get_enabled_flags()

        logger.info(
            f"[HybridRetrievalService] Retrieval completed: "
            f"{result.total_candidates} candidates, "
            f"source={result.source.value}, "
            f"fallback={result.fallback_occurred}, "
            f"latency={result.latency_ms:.2f}ms"
        )

        return result

    def _retrieve_dense(
        self, context: HybridRetrievalContext
    ) -> tuple[list[RetrievalCandidate], Optional[list[float]], FallbackReason]:
        """执行 Dense 召回"""
        candidates, query_embedding, reason = self._dense_retriever.retrieve(
            question=context.question,
            top_k=context.top_k,
            profile_keys=context.profile_keys,
            strict=context.strict,
        )
        return candidates, query_embedding, reason

    def _retrieve_sparse(self, context: HybridRetrievalContext) -> list[RetrievalCandidate]:
        """执行 Sparse 召回"""
        return self._sparse_retriever.retrieve(
            question=context.question,
            top_k=context.top_k,
            profile_keys=context.profile_keys,
            strict=context.strict,
        )

    def _retrieve_structured(self, context: HybridRetrievalContext) -> list[RetrievalCandidate]:
        """
        执行结构化召回（降级方案）

        当 Dense 和 Sparse 都失败时使用。
        基于严格的条件匹配。
        """
        # 如果有 profile_keys，直接转换为候选
        if context.profile_keys:
            candidates = []
            for profile_key in context.profile_keys:
                # 从 sparse retriever 获取 profile 信息
                doc = self._sparse_retriever._documents.get(profile_key, {})
                candidate = RetrievalCandidate(
                    profile_key=profile_key,
                    score=0.5,  # 默认分数
                    source=RetrievalSource.STRUCTURED,
                    worker_id=doc.get("worker_id"),
                    profile_name=doc.get("profile_name"),
                    capabilities=doc.get("capabilities", []),
                    domains=doc.get("domains", []),
                    scenarios=doc.get("scenarios", []),
                    metadata={"structured_match": True},
                )
                candidates.append(candidate)
            return candidates

        # 否则返回空（不支持全库结构化召回，性能约束）
        logger.warning(
            "[HybridRetrievalService] Structured retrieval without profile_keys not supported"
        )
        return []

    def _score_candidates(
        self,
        candidates: list[RetrievalCandidate],
        context: HybridRetrievalContext,
    ) -> list[RetrievalCandidate]:
        """
        为候选打分（单一来源：RetrievalScorer）

        从 Sparse Retriever 获取完整 Profile 信息用于评分。
        """
        scored_candidates = []

        for candidate in candidates:
            # 从 sparse retriever 获取 profile 信息
            doc = self._sparse_retriever._documents.get(candidate.profile_key, {})

            # 构造 WorkerProfile（简化版）
            profile = WorkerProfile(
                name=doc.get("profile_name", ""),
                capabilities=[
                    {"name": cap} for cap in doc.get("capabilities", [])
                ],
                domains=doc.get("domains", []),
                scenarios=doc.get("scenarios", []),
            )

            # 提取分数组件
            dense_score = candidate.metadata.get("dense_score")
            sparse_raw_score = candidate.metadata.get("sparse_score")

            # 构造评分上下文
            score_context = {
                "matched_terms": candidate.matched_terms,
                "required_capabilities": context.required_capabilities,
                "required_domains": context.required_domains,
                "required_scenarios": context.required_scenarios,
                "model_name": candidate.metadata.get("model_name"),
                "index_hit": candidate.source == RetrievalSource.DENSE,
            }

            # 使用 RetrievalScorer 计算分数（单一来源）
            scored = self._retrieval_scorer.score_candidate(
                candidate=candidate,
                dense_score=dense_score,
                sparse_score=sparse_raw_score,
                profile=profile,
                context=score_context,
            )

            scored_candidates.append(scored)

        return scored_candidates

    def _get_enabled_flags(self) -> list[str]:
        """获取启用的 Feature Flags"""
        flags = []
        if self._feature_flags.ENABLE_DENSE_RETRIEVAL:
            flags.append("ENABLE_DENSE_RETRIEVAL")
        if self._feature_flags.ENABLE_SPARSE_RETRIEVAL:
            flags.append("ENABLE_SPARSE_RETRIEVAL")
        if self._feature_flags.ENABLE_HYBRID_RETRIEVAL:
            flags.append("ENABLE_HYBRID_RETRIEVAL")
        if self._feature_flags.ENABLE_RETRIEVAL_SCORE_BREAKDOWN:
            flags.append("ENABLE_RETRIEVAL_SCORE_BREAKDOWN")
        return flags


__all__ = ["HybridRetrievalService"]