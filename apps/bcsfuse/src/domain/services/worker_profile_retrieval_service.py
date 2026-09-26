"""
Worker Profile Retrieval Service

Worker Profile Retrieval & Fusion Simulation Baseline

Mode-aware 检索服务，根据不同模式应用不同的评分策略。

Phase C: G1 Semantic Rerank V2
- 当 ENABLE_G1_PROFILE_RERANK=true 且 mode=AGENT 时，使用 ProfileSemanticRanker
- 支持 score_breakdown 输出（仅用于日志/调试，不改变 RetrievalResult 结构）
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Optional

from src.domain.models.retrieval_mode import RetrievalMode
from src.domain.models.scoring_signal import ScoringSignal, SignalType
from src.domain.models.worker_profile import WorkerProfile
from src.domain.services.worker_profile_source import WorkerProfileSource
from src.domain.services.profile_key_canonicalizer import ProfileKeyCanonicalizer
from src.infra.config.feature_flags import FeatureFlags

if TYPE_CHECKING:
    from src.domain.services.adapters.worker_profile_filter_adapter import WorkerProfileFilterAdapter
    from src.domain.services.adapters.worker_profile_binding_store_adapter import WorkerProfileBindingStoreAdapter
    from src.domain.services.profile_semantic_ranker import ProfileSemanticRanker, RerankContext

logger = logging.getLogger(__name__)


@dataclass
class RetrievalResult:
    """
    单个检索结果

    Attributes:
        profile: Worker Profile
        total_score: 总分
        signals: 评分信号列表
        rank: 排名
    """
    profile: WorkerProfile
    total_score: float
    signals: list[ScoringSignal] = field(default_factory=list)
    rank: int = 0


@dataclass
class RetrievalResponse:
    """
    检索响应

    Attributes:
        results: 检索结果列表
        question: 原始问题
        mode: 检索模式
        total_count: 总结果数
    """
    results: list[RetrievalResult]
    question: str
    mode: RetrievalMode
    total_count: int = 0

    def __post_init__(self):
        if self.total_count == 0:
            self.total_count = len(self.results)


class ModeAwareScorer:
    """
    Mode-aware 评分器

    根据检索模式应用不同的评分权重策略。
    """

    # 各模式的权重配置
    MODE_WEIGHTS = {
        RetrievalMode.AGENT: {
            "skill_name_match": 0.35,
            "skill_desc_match": 0.15,
            "context_match": 0.25,
            "searchable_match": 0.15,
            "profile_type_bonus": 0.10,
        },
        RetrievalMode.CONFLICT_ALIGNMENT: {
            "skill_name_match": 0.25,
            "skill_desc_match": 0.15,
            "context_match": 0.30,
            "searchable_match": 0.10,
            "profile_type_bonus": 0.10,
            "mode_bonus": 0.10,  # G2 额外考虑视角多样性
        },
        RetrievalMode.EXPERT_DIAGNOSIS: {
            "skill_name_match": 0.20,
            "skill_desc_match": 0.10,
            "context_match": 0.20,
            "searchable_match": 0.10,
            "coverage_score": 0.25,  # G5 优先领域覆盖
            "domain_coverage": 0.15,  # G5 领域多样性
        },
        RetrievalMode.GENERAL: {
            "skill_name_match": 0.30,
            "skill_desc_match": 0.20,
            "context_match": 0.20,
            "searchable_match": 0.20,
            "profile_type_bonus": 0.10,
        },
    }

    def __init__(self, mode: RetrievalMode):
        self.mode = mode
        self.weights = self.MODE_WEIGHTS.get(mode, self.MODE_WEIGHTS[RetrievalMode.GENERAL])

    def get_weight(self, signal_type: str) -> float:
        """获取信号类型的权重"""
        return self.weights.get(signal_type, 0.1)


class WorkerProfileRetrievalService:
    """
    Worker Profile 检索服务

    提供基于模式的检索功能，支持：
    - G1 (AGENT): 关注直接相关性
    - G2 (CONFLICT_ALIGNMENT): 考虑视角多样性
    - G5 (EXPERT_DIAGNOSIS): 优先领域覆盖/多样性

    Stage 1 Phase 4: 支持 Registry 状态过滤
    - 可选注入 WorkerProfileFilterAdapter
    - 只返回 active + online 的 profile
    """

    def __init__(
        self,
        source: WorkerProfileSource,
        profile_filter: Optional["WorkerProfileFilterAdapter"] = None,
        binding_store: Optional["WorkerProfileBindingStoreAdapter"] = None,
        strict_participants: bool = False,
    ):
        """
        初始化服务

        Args:
            source: Worker Profile 来源
            profile_filter: Worker Profile 过滤器（可选）
                - None: 不过滤（后向兼容）
                - WorkerProfileFilterAdapter: 根据 Registry 状态过滤
            binding_store: Worker Profile 绑定存储（可选，用于 profile_key 规范化）
            strict_participants: 是否启用严格参与者模式
                - False（默认）: participant 过滤失败时允许 fallback
                - True: participant 过滤失败时禁止 fallback，返回空结果
        """
        self._source = source
        self._profile_filter = profile_filter
        self._binding_store = binding_store
        self._strict_participants = strict_participants
        self._canonicalizer = ProfileKeyCanonicalizer(binding_store)
        # Phase C: V2 Ranker (延迟初始化)
        self._v2_ranker: Optional["ProfileSemanticRanker"] = None

    def _get_v2_ranker(self) -> "ProfileSemanticRanker":
        """
        获取 V2 Ranker（延迟初始化）

        Returns:
            ProfileSemanticRanker: V2 排序器实例
        """
        if self._v2_ranker is None:
            from src.domain.services.profile_semantic_ranker import ProfileSemanticRanker
            self._v2_ranker = ProfileSemanticRanker()
        return self._v2_ranker

    def retrieve(
        self,
        question: str,
        mode: RetrievalMode,
        top_k: Optional[int] = None,
        profile_keys: Optional[list[str]] = None,
        skill_filter: Optional[list[str]] = None,
        min_score: float = 0.0,
        strict_participants: Optional[bool] = None,
    ) -> RetrievalResponse:
        """
        检索 profiles

        Args:
            question: 问题/任务描述
            mode: 检索模式
            top_k: 返回数量限制
            profile_keys: 指定 profile keys 过滤
            skill_filter: 技能过滤（只返回包含指定技能的 profiles）
            min_score: 最低分数阈值
            strict_participants: 是否启用严格参与者模式（覆盖实例配置）
                - None: 使用实例配置
                - False: participant 过滤失败时允许 fallback
                - True: participant 过滤失败时禁止 fallback

        Returns:
            RetrievalResponse: 检索响应

        Raises:
            ValueError: strict_participants=True 且 profile_keys 过滤为空时
        """
        # 确定是否使用严格模式
        use_strict = strict_participants if strict_participants is not None else self._strict_participants

        logger.info("[RETRIEVAL] ========== retrieve 开始 ==========")
        logger.info("[RETRIEVAL] PID: %d", os.getpid())
        logger.info("[RETRIEVAL] question 长度: %d, 预览: %s", len(question), question[:80] if len(question) > 80 else question)
        logger.info("[RETRIEVAL] mode: %s", mode)
        logger.info("[RETRIEVAL] top_k: %s", top_k)
        logger.info("[RETRIEVAL] profile_keys (raw): %s", profile_keys)
        logger.info("[RETRIEVAL] skill_filter: %s", skill_filter)
        logger.info("[RETRIEVAL] min_score: %s", min_score)
        logger.info("[RETRIEVAL] strict_participants: %s (请求) / %s (实例)", strict_participants, self._strict_participants)
        logger.info("[RETRIEVAL] use_strict: %s", use_strict)

        # 获取所有 profiles
        logger.info("[RETRIEVAL] Step 1: 扫描 profile source...")
        logger.info("[RETRIEVAL]   _source 类型: %s", type(self._source).__name__)
        logger.info("[RETRIEVAL]   _source id: %d", id(self._source))

        scan_start = datetime.now()
        scan_result = self._source.scan()
        scan_elapsed = (datetime.now() - scan_start).total_seconds()
        logger.info("[RETRIEVAL]   scan() 完成，耗时: %.3fs", scan_elapsed)

        profiles = scan_result.profiles
        logger.info("[RETRIEVAL]   scan() 返回 profiles 数量: %d", len(profiles) if profiles else 0)

        if not profiles:
            logger.warning(
                "⚠️ [ProfileRetrieval] No profiles found after scan, "
                "source_type=%s, source_id=%d, scan_elapsed=%.3fs, "
                "scan_warnings=%s",
                type(self._source).__name__,
                id(self._source),
                scan_elapsed,
                scan_result.scan_warnings
            )
            return RetrievalResponse(
                results=[],
                question=question,
                mode=mode,
            )

        # Stage 1 Phase 4: 应用 Registry 状态过滤
        # 只返回 active + online 的 profile
        logger.info("[RETRIEVAL] Step 2: 应用 Registry 状态过滤...")
        logger.info("[RETRIEVAL]   _profile_filter: %s", "已注入" if self._profile_filter else "None")

        if self._profile_filter is not None:
            before_filter = len(profiles)
            profiles = self._profile_filter.filter_profiles(profiles)
            after_filter = len(profiles)
            logger.info(
                "[RETRIEVAL]   过滤前: %d, 过滤后: %d, 过滤掉: %d",
                before_filter, after_filter, before_filter - after_filter
            )

            if after_filter == 0:
                logger.warning(
                    "⚠️ [ProfileRetrieval] All profiles filtered out by registry filter, "
                    "before_filter=%d, after_filter=%d, filter_type=%s",
                    before_filter,
                    after_filter,
                    type(self._profile_filter).__name__
                )
            else:
                logger.info(
                    "✅ [ProfileRetrieval] Registry filter applied, "
                    "profiles_before=%d, profiles_after=%d, filtered_count=%d",
                    before_filter,
                    after_filter,
                    before_filter - after_filter
                )
        else:
            logger.info("[RETRIEVAL]   跳过过滤（无 filter）")

        # 应用 profile_keys 过滤
        if profile_keys:
            logger.info("[RETRIEVAL] Step 3: 应用 profile_keys 过滤...")
            before_count = len(profiles)

            # 获取可用的 profile_keys
            available_keys = set(p.profile_key for p in profiles)
            logger.info("[RETRIEVAL]   可用 profile_keys 数量: %d", len(available_keys))

            # 规范化 profile_keys
            logger.info("[RETRIEVAL]   开始规范化 profile_keys...")
            canonicalized_map = self._canonicalizer.canonicalize(profile_keys, available_keys)
            canonicalized_keys = set(canonicalized_map.values())
            logger.info("[RETRIEVAL]   规范化完成: %d -> %d 个唯一 key", len(profile_keys), len(canonicalized_keys))

            # 记录规范化详情
            for raw_key, canonical_key in canonicalized_map.items():
                if raw_key != canonical_key:
                    logger.info("[RETRIEVAL]     规范化: %s -> %s", raw_key, canonical_key)

            # 使用规范化后的 keys 过滤
            profiles = [
                p for p in profiles
                if p.profile_key in canonicalized_keys
            ]
            logger.info("[RETRIEVAL]   过滤前: %d, 过滤后: %d", before_count, len(profiles))

            # 严格模式处理：如果过滤后为空且有显式 profile_keys
            if len(profiles) == 0 and use_strict:
                logger.error(
                    "❌ [ProfileRetrieval] Participant matching failed in strict mode, "
                    "raw_profile_keys_count=%d, canonicalized_keys_count=%d, "
                    "available_keys_count=%d, raw_keys=%s, canonicalized=%s, "
                    "available_sample=%s",
                    len(profile_keys),
                    len(canonicalized_keys),
                    len(available_keys),
                    profile_keys[:5],
                    list(canonicalized_keys)[:5],
                    list(available_keys)[:5]
                )
                logger.error(
                    "❌ [ProfileRetrieval] Strict mode - no matching participants found, "
                    "returning empty results, strict_mode=True"
                )

                # 在严格模式下，返回空结果而不是 fallback
                return RetrievalResponse(
                    results=[],
                    question=question,
                    mode=mode,
                )
            elif len(profiles) == 0 and not use_strict:
                # 兼容模式：记录警告，但允许继续（后续可能 fallback）
                logger.warning(
                    "⚠️ [ProfileRetrieval] Participant matching failed, falling back to all profiles, "
                    "raw_profile_keys_count=%d, canonicalized_keys_count=%d, "
                    "available_keys_count=%d, strict_mode=False",
                    len(profile_keys),
                    len(canonicalized_keys),
                    len(available_keys)
                )

        # 应用 skill_filter 过滤
        if skill_filter:
            logger.info("[RETRIEVAL] Step 4: 应用 skill_filter 过滤...")
            before_count = len(profiles)
            filter_set = set(s.lower() for s in skill_filter)
            profiles = [
                p for p in profiles
                if any(s.name.lower() in filter_set for s in p.active_skills)
            ]
            logger.info(
                "[RETRIEVAL]   过滤前: %d, 过滤后: %d, skill_filter=%s",
                before_count, len(profiles), skill_filter
            )

            if len(profiles) == 0:
                logger.warning(
                    "⚠️ [ProfileRetrieval] No profiles match skill filter, "
                    "skill_filter=%s, profiles_before_filter=%d, profiles_after_filter=%d",
                    skill_filter,
                    before_count,
                    len(profiles)
                )

        if not profiles:
            logger.warning(
                "⚠️ [ProfileRetrieval] No profiles available after all filters, "
                "mode=%s, has_profile_keys=%s, has_skill_filter=%s, "
                "has_registry_filter=%s",
                mode,
                profile_keys is not None,
                skill_filter is not None,
                self._profile_filter is not None
            )
            return RetrievalResponse(
                results=[],
                question=question,
                mode=mode,
            )

        # 计算 mode-aware 评分
        logger.info("[RETRIEVAL] Step 5: 计算 mode-aware 评分...")

        # Phase E: Hybrid Retrieval（优先级最高）
        use_hybrid_retrieval = (
            FeatureFlags.is_enabled("ENABLE_HYBRID_RETRIEVAL")
            and mode == RetrievalMode.AGENT
        )

        # Phase C: V2 评分路径（仅 G1/AGENT 模式）
        use_v2_scorer = (
            FeatureFlags.is_g1_profile_rerank_enabled()
            and mode == RetrievalMode.AGENT
        )

        if use_hybrid_retrieval:
            logger.info("[RETRIEVAL]   使用 Hybrid Retrieval（Phase E）")
            results = self._retrieve_with_hybrid(
                profiles=profiles,
                question=question,
                min_score=min_score,
                top_k=top_k,
                use_strict=use_strict,
                profile_keys=profile_keys,
            )
            logger.info(
                "✅ [ProfileRetrieval] Hybrid retrieval completed, "
                "results_count=%d, profiles_input=%d",
                len(results),
                len(profiles)
            )
        elif use_v2_scorer:
            logger.info("[RETRIEVAL]   使用 V2 ProfileSemanticRanker（G1 V2 评分）")
            results = self._calculate_v2_scores(
                profiles=profiles,
                question=question,
                min_score=min_score,
                top_k=top_k,
                use_strict=use_strict,
                profile_keys=profile_keys,
            )
            logger.info(
                "✅ [ProfileRetrieval] V2 scoring completed, "
                "results_count=%d, profiles_input=%d",
                len(results),
                len(profiles)
            )
        else:
            # Legacy 评分路径
            logger.info("[RETRIEVAL]   使用 Legacy ModeAwareScorer")
            scorer = ModeAwareScorer(mode)
            results: list[RetrievalResult] = []

            for profile in profiles:
                signals = self._calculate_signals(profile, question, scorer, mode)
                total_score = sum(s.weighted_score or 0 for s in signals)

                if total_score >= min_score:
                    results.append(RetrievalResult(
                        profile=profile,
                        total_score=total_score,
                        signals=signals,
                    ))
            logger.info(
                "✅ [ProfileRetrieval] Legacy scoring completed, "
                "results_count=%d, profiles_input=%d, min_score=%.3f",
                len(results),
                len(profiles),
                min_score
            )

        logger.info("[RETRIEVAL]   评分后有效结果数: %d", len(results))

        # G5 特殊处理：领域多样性排序
        if mode == RetrievalMode.EXPERT_DIAGNOSIS:
            logger.info("[RETRIEVAL] Step 6: 应用 G5 领域多样性排序...")
            results = self._apply_diversity_ranking(results)
            logger.info(
                "✅ [ProfileRetrieval] G5 diversity ranking applied, "
                "results_count=%d, mode=%s",
                len(results),
                mode
            )

        # 按分数降序排序
        results.sort(key=lambda r: r.total_score, reverse=True)

        # 设置排名
        for i, result in enumerate(results):
            result.rank = i + 1

        # 应用 top_k
        if top_k is not None:
            results = results[:top_k]
            logger.info("[RETRIEVAL]   应用 top_k=%d 后结果数: %d", top_k, len(results))

        if len(results) > 0:
            logger.info(
                "✅ [ProfileRetrieval] Retrieval completed successfully, "
                "results_count=%d, mode=%s, question_preview=%s",
                len(results),
                mode,
                question[:50] if len(question) > 50 else question
            )
        else:
            logger.warning(
                "⚠️ [ProfileRetrieval] Retrieval completed with no results, "
                "mode=%s, min_score=%.3f, profiles_input=%d",
                mode,
                min_score,
                len(profiles)
            )

        logger.info("[RETRIEVAL] ========== retrieve 完成，返回 %d 个结果 ==========", len(results))
        for i, r in enumerate(results[:5]):
            logger.info("[RETRIEVAL]   result[%d]: profile_key=%s, score=%.3f",
                       i, r.profile.profile_key, r.total_score)

        return RetrievalResponse(
            results=results,
            question=question,
            mode=mode,
        )

    def _retrieve_with_hybrid(
        self,
        profiles: list[WorkerProfile],
        question: str,
        min_score: float,
        top_k: Optional[int],
        use_strict: bool,
        profile_keys: Optional[list[str]],
    ) -> list[RetrievalResult]:
        """
        Phase E: Hybrid Retrieval 方法

        使用 Dense + Sparse + Structured 混合检索。

        Args:
            profiles: 待检索的 profiles（已过滤）
            question: 问题文本
            min_score: 最低分数阈值
            top_k: 目标数量
            use_strict: 是否使用严格模式
            profile_keys: 显式指定的 profile_keys

        Returns:
            list[RetrievalResult]: 检索结果
        """
        try:
            # 1. 初始化 Hybrid Retrieval 组件
            from src.domain.services.hybrid_retrieval_service import HybridRetrievalService
            from src.domain.services.dense_retriever import DenseRetriever
            from src.domain.services.sparse_retriever import SparseRetriever
            from src.domain.services.retrieval_scorer import RetrievalScorer
            from src.domain.models.hybrid_retrieval_result import HybridRetrievalContext
            from src.infra.embedding.providers.real_provider import RealEmbeddingProvider
            from src.infra.indexing.profile_embedding_store import ProfileEmbeddingStore
            from src.infra.config.feature_flags import FeatureFlags as FF

            # 获取 embedding provider
            embedding_provider = RealEmbeddingProvider()

            # 获取 profile embedding store（根据环境自动选择 local/zdas）
            from src.infra.config.data_paths import resolve_data_path
            profile_store = ProfileEmbeddingStore(
                dimension=4096,
                index_type="local",  # 会在依赖注入时根据环境切换
                db_path=resolve_data_path("data/vector_store.db"),
            )

            # 初始化 retrievers
            dense_retriever = DenseRetriever(
                embedding_provider=embedding_provider,
                profile_store=profile_store,
            )

            sparse_retriever = SparseRetriever()
            retrieval_scorer = RetrievalScorer()

            # 初始化 Hybrid Retrieval Service
            hybrid_service = HybridRetrievalService(
                dense_retriever=dense_retriever,
                sparse_retriever=sparse_retriever,
                retrieval_scorer=retrieval_scorer,
            )

            # 2. 构造检索上下文
            context = HybridRetrievalContext(
                question=question,
                profile_keys=profile_keys,
                strict=use_strict,
                top_k=top_k or 10,
                min_score=min_score,
                enable_dense=FF.is_enabled("ENABLE_DENSE_RETRIEVAL"),
                enable_sparse=FF.is_enabled("ENABLE_SPARSE_RETRIEVAL"),
            )

            # 3. 执行 Hybrid Retrieval
            hybrid_result = hybrid_service.retrieve(context)

            # 4. 转换为 RetrievalResult 格式
            results: list[RetrievalResult] = []
            for candidate in hybrid_result.candidates:
                # 从 profiles 中找到对应的 WorkerProfile
                profile = next(
                    (p for p in profiles if p.profile_key == candidate.profile_key),
                    None
                )

                if profile and candidate.score >= min_score:
                    # 将 Hybrid 分数包装为 signal
                    signal = ScoringSignal(
                        signal_type=SignalType.SEARCHABLE_MATCH,
                        raw_score=candidate.score,
                        weight=1.0,
                        details={
                            "scorer_version": "hybrid",
                            "source": candidate.source.value if hasattr(candidate.source, 'value') else str(candidate.source),
                            "hybrid_score": candidate.score,
                        },
                    )
                    results.append(RetrievalResult(
                        profile=profile,
                        total_score=candidate.score,
                        signals=[signal],
                    ))

            if len(results) > 0:
                logger.info(
                    "✅ [ProfileRetrieval] Hybrid retrieval completed successfully, "
                    "results_count=%d, source=%s, fallback=%s, profiles_input=%d",
                    len(results),
                    hybrid_result.source.value if hasattr(hybrid_result.source, 'value') else str(hybrid_result.source),
                    hybrid_result.fallback_occurred,
                    len(profiles)
                )
            else:
                logger.warning(
                    "⚠️ [ProfileRetrieval] Hybrid retrieval returned no results, "
                    "source=%s, fallback=%s, profiles_input=%d, min_score=%.3f",
                    hybrid_result.source.value if hasattr(hybrid_result.source, 'value') else str(hybrid_result.source),
                    hybrid_result.fallback_occurred,
                    len(profiles),
                    min_score
                )

            return results

        except Exception as e:
            logger.error(
                "❌ [ProfileRetrieval] Hybrid retrieval failed, falling back to legacy scorer, "
                "error_type=%s, error_message=%s, profiles_input=%d",
                type(e).__name__,
                str(e),
                len(profiles),
                exc_info=True
            )
            # Fallback 到 Legacy 评分
            scorer = ModeAwareScorer(RetrievalMode.AGENT)
            results = []
            for profile in profiles:
                signals = self._calculate_signals(profile, question, scorer, RetrievalMode.AGENT)
                total_score = sum(s.weighted_score or 0 for s in signals)
                if total_score >= min_score:
                    results.append(RetrievalResult(
                        profile=profile,
                        total_score=total_score,
                        signals=signals,
                    ))
            return results

    def _calculate_v2_scores(
        self,
        profiles: list[WorkerProfile],
        question: str,
        min_score: float,
        top_k: Optional[int],
        use_strict: bool,
        profile_keys: Optional[list[str]],
    ) -> list[RetrievalResult]:
        """
        Phase C: V2 评分方法

        使用 ProfileSemanticRanker 进行评分和排序。

        Args:
            profiles: 待评分的 profiles
            question: 问题文本
            min_score: 最低分数阈值
            top_k: 目标数量
            use_strict: 是否使用严格模式
            profile_keys: 显式指定的 profile_keys

        Returns:
            list[RetrievalResult]: 评分结果（转换为 legacy 格式）
        """
        from src.domain.services.profile_semantic_ranker import RerankContext

        # 构建 RerankContext
        context = RerankContext(
            question=question,
            mode="agent",
            strict_participants=use_strict,
            profile_keys=profile_keys,
        )

        # 获取 V2 Ranker
        ranker = self._get_v2_ranker()

        # 执行 V2 评分和排序
        scored_profiles = ranker.rank(
            profiles=profiles,
            context=context,
            top_k=top_k,
        )

        # 转换为 RetrievalResult（保持 legacy 格式）
        results: list[RetrievalResult] = []
        for profile, score in scored_profiles:
            if score.final_score >= min_score:
                # 将 V2 分数包装为单个 signal（简化表示）
                signal = ScoringSignal(
                    signal_type=SignalType.SEARCHABLE_MATCH,  # 使用现有类型
                    raw_score=score.final_score,
                    weight=1.0,
                    details={
                        "scorer_version": "v2",
                        "base_score": score.base_score,
                        "diversity_adjusted": score.diversity_adjusted,
                    },
                )
                results.append(RetrievalResult(
                    profile=profile,
                    total_score=score.final_score,
                    signals=[signal],
                ))

        # 设置排名
        for i, result in enumerate(results):
            result.rank = i + 1

        if len(results) > 0:
            logger.info(
                "✅ [ProfileRetrieval] V2 scoring completed successfully, "
                "profiles_input=%d, results_count=%d, min_score=%.3f",
                len(profiles),
                len(results),
                min_score
            )
        else:
            logger.warning(
                "⚠️ [ProfileRetrieval] V2 scoring returned no results, "
                "profiles_input=%d, min_score=%.3f, question_preview=%s",
                len(profiles),
                min_score,
                question[:50] if len(question) > 50 else question
            )

        return results

    def _calculate_signals(
        self,
        profile: WorkerProfile,
        question: str,
        scorer: ModeAwareScorer,
        mode: RetrievalMode,
    ) -> list[ScoringSignal]:
        """
        计算评分信号

        Args:
            profile: Worker Profile
            question: 问题描述
            scorer: 评分器
            mode: 检索模式

        Returns:
            评分信号列表
        """
        signals: list[ScoringSignal] = []
        question_lower = question.lower()
        question_keywords = self._extract_keywords(question)

        # 1. 技能名称匹配信号
        for skill in profile.active_skills:
            skill_name_lower = skill.name.lower()
            raw_score = 0.0

            # 完整匹配
            if skill_name_lower in question_lower or question_lower in skill_name_lower:
                raw_score = 1.0
            # 关键词匹配
            elif any(kw in skill_name_lower for kw in question_keywords):
                raw_score = 0.7

            if raw_score > 0:
                signals.append(ScoringSignal(
                    signal_type=SignalType.SKILL_NAME_MATCH,
                    raw_score=raw_score,
                    weight=scorer.get_weight("skill_name_match"),
                    details={"skill": skill.name, "skill_id": skill.skill_id},
                ))

        # 2. 技能描述匹配信号
        for skill in profile.active_skills:
            if skill.description:
                desc_lower = skill.description.lower()
                keyword_matches = sum(1 for kw in question_keywords if kw in desc_lower)

                if keyword_matches > 0:
                    raw_score = min(keyword_matches / max(len(question_keywords), 1), 1.0)
                    signals.append(ScoringSignal(
                        signal_type=SignalType.SKILL_DESC_MATCH,
                        raw_score=raw_score,
                        weight=scorer.get_weight("skill_desc_match"),
                        details={"skill": skill.name, "matches": keyword_matches},
                    ))

        # 3. 上下文匹配信号
        for fragment in profile.context_fragments:
            content_lower = fragment.content.lower()

            # 完整查询匹配
            if question_lower in content_lower:
                signals.append(ScoringSignal(
                    signal_type=SignalType.CONTEXT_MATCH,
                    raw_score=0.8,
                    weight=scorer.get_weight("context_match"),
                    details={
                        "fragment": fragment.filename,
                        "kind": fragment.kind.value,
                        "match_type": "full",
                    },
                ))
            else:
                # 关键词匹配
                keyword_matches = sum(1 for kw in question_keywords if kw in content_lower)
                if keyword_matches > 0:
                    raw_score = keyword_matches / max(len(question_keywords), 1)
                    signals.append(ScoringSignal(
                        signal_type=SignalType.CONTEXT_MATCH,
                        raw_score=raw_score,
                        weight=scorer.get_weight("context_match"),
                        details={
                            "fragment": fragment.filename,
                            "kind": fragment.kind.value,
                            "match_type": "keyword",
                            "matches": keyword_matches,
                        },
                    ))

        # 4. 可搜索文本匹配信号
        searchable_lower = profile.searchable_text.lower()
        keyword_matches = sum(1 for kw in question_keywords if kw in searchable_lower)
        if keyword_matches > 0:
            raw_score = keyword_matches / max(len(question_keywords), 1)
            signals.append(ScoringSignal(
                signal_type=SignalType.SEARCHABLE_MATCH,
                raw_score=raw_score,
                weight=scorer.get_weight("searchable_match"),
                details={"keyword_matches": keyword_matches},
            ))

        # 5. Profile type bonus
        if mode == RetrievalMode.AGENT:
            # G1: DEFAULT profile 有加分
            if profile.profile_type.value == "default":
                signals.append(ScoringSignal(
                    signal_type=SignalType.PROFILE_TYPE_BONUS,
                    raw_score=0.5,
                    weight=scorer.get_weight("profile_type_bonus"),
                    details={"profile_type": profile.profile_type.value},
                ))

        # 6. G5 特有：领域覆盖分数
        if mode == RetrievalMode.EXPERT_DIAGNOSIS:
            # 计算领域覆盖
            domain_coverage = self._calculate_domain_coverage(profile)
            if domain_coverage > 0:
                signals.append(ScoringSignal(
                    signal_type=SignalType.DOMAIN_COVERAGE,
                    raw_score=domain_coverage,
                    weight=scorer.get_weight("domain_coverage"),
                    details={"coverage_score": domain_coverage},
                ))

        return signals

    def _calculate_domain_coverage(self, profile: WorkerProfile) -> float:
        """
        计算领域覆盖分数

        基于技能和上下文的多样性评估。

        Args:
            profile: Worker Profile

        Returns:
            领域覆盖分数 (0-1)
        """
        # 基于技能数量和上下文片段数量评估
        skill_count = len(profile.active_skills)
        fragment_count = len(profile.context_fragments)

        # 技能多样性（假设更多技能意味着更广的覆盖）
        skill_score = min(skill_count / 5.0, 1.0)

        # 上下文多样性
        context_kinds = set(f.kind.value for f in profile.context_fragments)
        context_score = min(len(context_kinds) / 4.0, 1.0)  # 假设有4种常见的 context 类型

        return (skill_score * 0.6 + context_score * 0.4)

    def _apply_diversity_ranking(
        self,
        results: list[RetrievalResult],
    ) -> list[RetrievalResult]:
        """
        应用多样性排序

        确保 G5 返回的结果覆盖不同领域。

        Args:
            results: 原始结果列表

        Returns:
            调整后的结果列表
        """
        if not results:
            return results

        selected: list[RetrievalResult] = []
        selected_skills: set[str] = set()

        # 贪心选择，优先选择技能多样性高的结果
        remaining = sorted(results, key=lambda r: r.total_score, reverse=True)

        for result in remaining:
            result_skills = set(s.name.lower() for s in result.profile.active_skills)

            # 计算新技能贡献
            new_skills = result_skills - selected_skills
            diversity_bonus = len(new_skills) / max(len(result_skills), 1)

            # 调整分数
            result.total_score += diversity_bonus * 0.2

            selected.append(result)
            selected_skills.update(result_skills)

        return selected

    def _extract_keywords(self, text: str) -> list[str]:
        """
        提取关键词

        简单实现：分词并过滤停用词。

        Args:
            text: 输入文本

        Returns:
            关键词列表
        """
        words = re.findall(r"\b\w+\b", text.lower())

        stopwords = {
            "a", "an", "the", "is", "are", "was", "were", "be", "been",
            "being", "have", "has", "had", "do", "does", "did", "will",
            "would", "could", "should", "may", "might", "must", "shall",
            "can", "need", "dare", "ought", "used", "to", "of", "in",
            "for", "on", "with", "at", "by", "from", "as", "into",
            "through", "during", "before", "after", "above", "below",
            "between", "under", "again", "further", "then", "once",
            "here", "there", "when", "where", "why", "how", "all",
            "each", "few", "more", "most", "other", "some", "such",
            "no", "nor", "not", "only", "own", "same", "so", "than",
            "too", "very", "just", "and", "but", "if", "or", "because",
            "until", "while", "although", "though", "i", "me", "my",
            "myself", "we", "our", "ours", "ourselves", "you", "your",
            "yours", "yourself", "yourselves", "he", "him", "his",
            "himself", "she", "her", "hers", "herself", "it", "its",
            "itself", "they", "them", "their", "theirs", "themselves",
            "what", "which", "who", "whom", "this", "that", "these",
            "those", "am", "need", "help", "want", "like",
        }

        return [w for w in words if len(w) > 2 and w not in stopwords]


__all__ = [
    "RetrievalResult",
    "RetrievalResponse",
    "ModeAwareScorer",
    "WorkerProfileRetrievalService",
    "ProfileKeyCanonicalizer",
]