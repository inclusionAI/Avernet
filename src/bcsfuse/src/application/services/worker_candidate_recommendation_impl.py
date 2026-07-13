"""
WorkerCandidateRecommendationImpl

Stage 4 Phase 2: Candidate Recommendation Service

G5 候选人推荐服务实现。

职责：
- 基于 question 推荐候选人
- 基于 participants 判断充足性
- 显式 participants 优先，补充推荐标记 is_supplement=True
- 输出 CandidateRecommendationResponse

Phase C: G1 Semantic Rerank V2
- 支持 score_breakdown 输出到 CandidateRecommendation
- 仅当 ENABLE_G1_SCORE_BREAKDOWN_OUTPUT=true 时输出

不做的：
- context trimming
- LLM 调用
- diagnose 聚合
- prompt 构造
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Optional, Protocol, Union

from src.domain.models.candidate_recommendation import (
    CandidateRecommendation,
    CandidateRecommendationResponse,
)
from src.domain.models.domain_coverage import DomainCoverage
from src.domain.models.retrieval_mode import RetrievalMode
from src.domain.services.participants_sufficiency_checker import (
    ParticipantsSufficiencyChecker,
)
from src.infra.config.feature_flags import FeatureFlags

if TYPE_CHECKING:
    from src.domain.models.profile_match_score import ProfileMatchScore
    from src.domain.models.worker_profile import WorkerProfile
    from src.domain.services.worker_profile_retrieval_service import (
        WorkerProfileRetrievalService,
    )
    from src.application.services.worker_vector_match_service import (
        WorkerVectorMatchService,
    )


class EmbeddingGenerator(Protocol):
    """Embedding 生成器协议

    用于生成文本的向量嵌入。

    注意：统一使用 embed() 方法名，与 EmbeddingProvider 协议保持一致。
    """

    def embed(self, text: str) -> list[float]:
        """生成文本的 embedding 向量

        Args:
            text: 输入文本

        Returns:
            list[float]: Embedding 向量
        """
        ...

logger = logging.getLogger(__name__)


class WorkerCandidateRecommendationImpl:
    """
    Worker Candidate Recommendation 服务实现

    用于 G5 Expert Diagnosis 模式的候选人推荐。

    核心逻辑：
    1. participants=None → 直接推荐候选人
    2. participants 充足 → 只返回显式 participants，不补充
    3. participants 不足 → 保留显式 participants，按需补充

    输出顺序：
    (1) 显式 participants（is_supplement=False）
    (2) 补充推荐（is_supplement=True）

    Vector Match 集成（G5-first）：
    - 仅 EXPERT_DIAGNOSIS 模式使用 vector match
    - 显式 participants 永远优先
    - Vector match 失败时 graceful fallback
    """

    def __init__(
        self,
        retrieval_service: "WorkerProfileRetrievalService",
        min_experts: int = 3,
        default_max_candidates: int = 5,
        vector_match_service: "WorkerVectorMatchService | None" = None,
        embedding_generator: "EmbeddingGenerator | None" = None,
    ):
        """
        初始化服务

        Args:
            retrieval_service: Worker Profile 检索服务
            min_experts: 最小专家数阈值（默认 3）
            default_max_candidates: 默认最大候选人数（默认 5）
            vector_match_service: 向量匹配服务（可选，用于 G5 模式增强）
            embedding_generator: Embedding 生成器（可选，配合 vector_match_service 使用）
        """
        self._retrieval_service = retrieval_service
        self._min_experts = min_experts
        self._default_max_candidates = default_max_candidates
        self._sufficiency_checker = ParticipantsSufficiencyChecker(min_experts=min_experts)
        self._vector_match_service = vector_match_service
        self._embedding_generator = embedding_generator

    def _inject_default_visibility_filters(
        self,
        filters: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """
        注入默认可见性过滤条件

        Stage 1 Phase 4: 默认过滤器确保 offline/private worker 不出现在推荐结果中

        规则：
        - runtime_state: 只返回 "online" 的 worker（排除 offline/busy/error）
        - availability: 只返回 "public" 或 "protected" 的 worker（排除 private）

        Args:
            filters: 用户提供的过滤器

        Returns:
            合并后的过滤器（包含默认可见性过滤）
        """
        # 默认可见性过滤器
        default_visibility_filters = {
            "runtime_state": ["online"],  # 只返回 online 的 worker
            "availability": ["public", "protected"]  # 排除 private worker
        }

        # 如果用户未提供过滤器，直接返回默认
        if not filters:
            logger.debug(
                "[VISIBILITY-TRACE] stage=inject_default_filters, endpoint=recommend, user_filters=None, "
                "default_filters=%s",
                default_visibility_filters
            )
            return default_visibility_filters

        # 合并用户过滤器和默认过滤器（AND 语义）
        merged_filters = dict(filters)

        # 如果用户指定了 runtime_state，使用用户的（优先级更高）
        if "runtime_state" not in merged_filters:
            merged_filters["runtime_state"] = default_visibility_filters["runtime_state"]
            logger.debug(
                "[VISIBILITY-TRACE] stage=inject_default_filters, endpoint=recommend, field=runtime_state, "
                "user_not_specified=True, using_default=%s",
                default_visibility_filters["runtime_state"]
            )

        # 如果用户指定了 availability，使用用户的（优先级更高）
        if "availability" not in merged_filters:
            merged_filters["availability"] = default_visibility_filters["availability"]
            logger.debug(
                "[VISIBILITY-TRACE] stage=inject_default_filters, endpoint=recommend, field=availability, "
                "user_not_specified=True, using_default=%s",
                default_visibility_filters["availability"]
            )

        logger.info(
            "[VISIBILITY-TRACE] stage=final_filters, endpoint=recommend, user_filters=%s, "
            "default_filters=%s, merged_filters=%s",
            filters,
            default_visibility_filters,
            merged_filters
        )

        return merged_filters

    def recommend(
        self,
        question: str,
        mode: RetrievalMode,
        participants: list[str] | None = None,
        max_candidates: int | None = None,
        min_experts: int | None = None,
        strict_participants: bool = False,
        runtime_config: dict[str, Any] | None = None,
        filters: dict[str, Any] | None = None,
        vector_min_score: float = 0.01,  # Phase B: 向量召回阈值
        rerank_min_score: float | None = None,  # Phase B: rerank 后质量阈值
        min_score: float | None = None,  # DEPRECATED: Use vector_min_score and rerank_min_score instead
    ) -> CandidateRecommendationResponse:
        """
        推荐候选人

        Args:
            question: 问题/任务描述
            mode: 检索模式
            participants: 显式 participants 列表（可选）
            max_candidates: 最大候选人数（None 则使用默认值）
            min_experts: 最小专家数阈值（None 则使用默认值）
            strict_participants: 是否启用严格参与者模式
                - False（默认）: 允许补充推荐
                - True: 禁止补充推荐，只返回显式找到的 participants
            runtime_config: 向量搜索运行时配置（可选）
                - expand_factor: int (1-10)
                - reranker_model: str | None
            filters: 元数据过滤条件（可选）
                - availability: list[str] - 可用性过滤，如 ["protected", "public"]
                - runtime_state: list[str] - 运行时状态过滤，如 ["online"]
            vector_min_score: 向量召回阶段的最小相似度阈值（默认 0.01）
            rerank_min_score: rerank 后的最小质量阈值（None 表示不应用额外阈值）
            min_score: (DEPRECATED) 旧参数，向后兼容。优先使用 vector_min_score 和 rerank_min_score。

        Returns:
            CandidateRecommendationResponse: 推荐响应
        """
        # Phase B: 向后兼容旧的 min_score 参数
        # 只有当 min_score 传入，且 vector_min_score 和 rerank_min_score 都未指定时，才使用 min_score
        if min_score is not None and rerank_min_score is None:
            # 使用 min_score 作为两个阈值的默认值（向后兼容）
            vector_min_score = min_score
            rerank_min_score = min_score

        # Stage 1 Phase 4: 注入默认可见性过滤器
        # 确保 offline/private worker 不出现在推荐结果中
        filters = self._inject_default_visibility_filters(filters)

        # 使用传入值或默认值
        actual_max_candidates = max_candidates if max_candidates is not None else self._default_max_candidates
        actual_min_experts = min_experts if min_experts is not None else self._min_experts

        # 更新 checker 的 min_experts（如果需要）
        if actual_min_experts != self._sufficiency_checker.min_experts:
            self._sufficiency_checker = ParticipantsSufficiencyChecker(min_experts=actual_min_experts)

        try:
            # Step 1: 加载显式 participants 对应的 profiles
            explicit_profiles = self._load_explicit_participants(
                participants=participants,
                question=question,
                mode=mode,
            )

            # Step 2: 推断显式 participants 的领域覆盖
            covered_domains = self._infer_covered_domains(explicit_profiles)

            # Step 3: 评估充足性
            sufficiency_result = self._assess_sufficiency(
                participants=participants,
                covered_domains=covered_domains,
                question=question,
            )

            # Step 4: 构建显式推荐
            explicit_recommendations = self._build_explicit_recommendations(
                profiles=explicit_profiles,
            )

            # Step 5: 如果不足，构建补充推荐（strict 模式下禁止补充）
            supplement_recommendations = []
            if not sufficiency_result.is_sufficient:
                # strict 模式：只有在有显式 participants 时才禁止补充
                # 如果没有显式 participants（participants=None 或 []），仍允许全库推荐
                has_explicit_participants = participants is not None and len(participants) > 0
                if strict_participants and has_explicit_participants:
                    logger.info("[CandidateRec] strict 模式：禁止补充推荐，只返回显式 participants")
                else:
                    supplement_recommendations = self._build_supplement_recommendations(
                        question=question,
                        mode=mode,
                        exclude_profile_keys=[p.profile_key for p in explicit_profiles],
                        max_supplements=actual_max_candidates - len(explicit_recommendations),
                        runtime_config=runtime_config,
                        filters=filters,
                        vector_min_score=vector_min_score,  # Phase B: 传递向量召回阈值
                        rerank_min_score=rerank_min_score,  # Phase B: 传递 rerank 后质量阈值
                        min_score=min_score,  # Phase B: 向后兼容
                    )

            # Step 6: 合并推荐（显式在前，补充在后）
            all_recommendations = self._merge_recommendations_in_order(
                explicit=explicit_recommendations,
                supplements=supplement_recommendations,
            )

            # Step 7: 应用 max_candidates 限制
            all_recommendations = all_recommendations[:actual_max_candidates]

            # Step 8: 构建领域覆盖分析
            domain_coverage = self._build_domain_coverage(
                question=question,
                recommendations=all_recommendations,
            )

            # Step 9: 构建响应
            # Phase C: 收集诊断 metadata
            # R43: 提取 reranker_called 信息（从 runtime_config 中获取）
            reranker_called_from_vector_match = runtime_config.get("_reranker_called", False) if runtime_config else False

            metadata = {
                "candidate_source": "vector" if supplement_recommendations else "explicit",
                "vector_search_used": len(supplement_recommendations) > 0,
                "fragment_embedding_enabled": True,  # 当前实现始终使用 fragment embedding
                "content_reload_enabled": True,  # Phase B: 启用 content reload
                "content_reload_source": "mysql/profile_store",
                "vector_min_score": vector_min_score,  # Phase B: 使用正确的阈值
                "rerank_min_score": rerank_min_score,  # Phase B: 使用正确的阈值
                "enable_rerank": runtime_config.get("reranker_model") is not None if runtime_config else False,
                "reranker_model": runtime_config.get("reranker_model") if runtime_config else None,
                "reranker_called": reranker_called_from_vector_match,  # R43: 实际是否调用了 reranker
                "expand_factor": runtime_config.get("expand_factor", 2) if runtime_config else 2,
            }

            return CandidateRecommendationResponse(
                recommendations=all_recommendations,
                question=question,
                mode=mode,
                domain_coverage=domain_coverage,
                participants_given=participants is not None and len(participants) > 0,
                participants_sufficient=sufficiency_result.is_sufficient,
                total_candidates=len(all_recommendations),
                selected_candidates=len(all_recommendations),
                min_experts=actual_min_experts,
                metadata=metadata,
            )

        except Exception as e:
            logger.warning(f"WorkerCandidateRecommendation: Error during recommendation: {e}")
            return self._build_empty_response(
                question=question,
                mode=mode,
                participants=participants,
            )

    # =========================================================================
    # 内部方法
    # =========================================================================

    def _load_explicit_participants(
        self,
        participants: list[str] | None,
        question: str,
        mode: RetrievalMode,
    ) -> list["WorkerProfile"]:
        """
        加载显式 participants 对应的 profiles

        Args:
            participants: 显式 participants 列表
            question: 问题
            mode: 检索模式

        Returns:
            list[WorkerProfile]: 显式 participants 的 profiles
        """
        if not participants:
            return []

        try:
            # 使用 retrieval service 按 profile_keys 获取
            response = self._retrieval_service.retrieve(
                question=question,
                mode=mode,
                profile_keys=participants,
            )
            return [r.profile for r in response.results]
        except Exception as e:
            logger.warning(f"Failed to load explicit participants: {e}")
            return []

    def _infer_covered_domains(self, profiles: list["WorkerProfile"]) -> list[str]:
        """
        推断 profiles 覆盖的领域

        Args:
            profiles: Profile 列表

        Returns:
            list[str]: 覆盖的领域列表
        """
        domains = set()
        for profile in profiles:
            domain = self._infer_domain(profile)
            if domain:
                domains.add(domain)
        return list(domains)

    def _infer_domain(self, profile: "WorkerProfile") -> str:
        """
        从 profile 推断领域

        基于 skills 和 context 推断主要领域。

        Args:
            profile: Worker Profile

        Returns:
            str: 推断的领域
        """
        # 优先从技能推断
        if profile.active_skills:
            # 取第一个技能作为主要领域
            primary_skill = profile.active_skills[0]
            skill_name = primary_skill.name.lower()

            # 简单映射
            domain_mapping = {
                "security": "security",
                "legal": "legal",
                "database": "database",
                "architecture": "architecture",
                "frontend": "frontend",
                "backend": "backend",
                "devops": "devops",
            }

            for key, domain in domain_mapping.items():
                if key in skill_name:
                    return domain

            # 如果没有匹配，使用技能名称
            return primary_skill.name.lower()

        # 从 context 推断
        for fragment in profile.context_fragments:
            content = fragment.content.lower() if fragment.content else ""

            keywords = ["security", "legal", "database", "architecture"]
            for kw in keywords:
                if kw in content:
                    return kw

        return "general"

    def _assess_sufficiency(
        self,
        participants: list[str] | None,
        covered_domains: list[str],
        question: str,
    ) -> "SufficiencyCheckResult":
        """
        评估 participants 充足性

        Args:
            participants: 显式 participants 列表
            covered_domains: 已覆盖领域
            question: 问题

        Returns:
            SufficiencyCheckResult: 充足性检查结果
        """
        # 从问题推断所需领域（简化实现）
        required_domains = self._infer_required_domains(question)

        return self._sufficiency_checker.check(
            participants=participants,
            covered_domains=covered_domains,
            required_domains=required_domains,
        )

    def _infer_required_domains(self, question: str) -> list[str]:
        """
        从问题推断所需领域

        简化实现：基于关键词匹配。

        Args:
            question: 问题

        Returns:
            list[str]: 所需领域列表
        """
        question_lower = question.lower()
        domains = []

        keywords = ["security", "legal", "database", "architecture", "frontend", "backend"]
        for kw in keywords:
            if kw in question_lower:
                domains.append(kw)

        return domains

    def _build_explicit_recommendations(
        self,
        profiles: list["WorkerProfile"],
    ) -> list[CandidateRecommendation]:
        """
        构建显式推荐

        Args:
            profiles: 显式 participants 的 profiles

        Returns:
            list[CandidateRecommendation]: 显式推荐列表
        """
        recommendations = []
        for profile in profiles:
            rec = self._build_recommendation_from_profile(
                profile=profile,
                score=0.9,  # 显式参与者默认高分
                is_supplement=False,
            )
            recommendations.append(rec)
        return recommendations

    def _build_supplement_recommendations(
        self,
        question: str,
        mode: RetrievalMode,
        exclude_profile_keys: list[str],
        max_supplements: int,
        runtime_config: dict[str, Any] | None = None,
        filters: dict[str, Any] | None = None,
        vector_min_score: float = 0.01,  # Phase B: 向量召回阈值
        rerank_min_score: float | None = None,  # Phase B: rerank 后质量阈值
        min_score: float | None = None,  # DEPRECATED: Use vector_min_score and rerank_min_score instead
    ) -> list[CandidateRecommendation]:
        """
        构建补充推荐

        G5-first 策略：
        1. 如果是 EXPERT_DIAGNOSIS 模式且 vector_match_service 可用，优先使用向量匹配
        2. 向量匹配失败或返回空结果时，返回空列表
        3. 非 G5 模式返回空列表

        Feature Flags:
        - ENABLE_VECTOR_AWARE_RECOMMENDATION: 控制是否使用向量匹配
        - ENABLE_REAL_EMBEDDING: 控制是否使用 real embedding

        Args:
            question: 问题
            mode: 检索模式
            exclude_profile_keys: 要排除的 profile keys
            max_supplements: 最大补充数
            runtime_config: 向量搜索运行时配置（可选）
            filters: 元数据过滤条件（可选）
            vector_min_score: 向量召回阶段的最小相似度阈值
            rerank_min_score: rerank 后的最小质量阈值
            min_score: (DEPRECATED) 旧参数，向后兼容

        Returns:
            list[CandidateRecommendation]: 补充推荐列表
        """
        # Phase B: 向后兼容旧的 min_score 参数
        if min_score is not None and rerank_min_score is None:
            vector_min_score = min_score
            rerank_min_score = min_score

        logger.info(
            "[CandidateRec-Supplement] START | "
            f"mode={mode.value}, max_supplements={max_supplements}, "
            f"exclude_count={len(exclude_profile_keys)}, vector_min_score={vector_min_score}, rerank_min_score={rerank_min_score}"
        )

        if max_supplements <= 0:
            logger.info("[CandidateRec-Supplement] SKIP | max_supplements <= 0")
            return []

        # 检查 vector-aware recommendation 是否启用
        vector_aware_enabled = FeatureFlags.is_vector_aware_recommendation_enabled()
        if not vector_aware_enabled:
            logger.warning(
                "[CandidateRec-Supplement] VECTOR_DISABLED | "
                "ENABLE_VECTOR_AWARE_RECOMMENDATION=false, returning empty list"
            )
            return []

        # G5-first: 使用向量匹配
        if mode == RetrievalMode.EXPERT_DIAGNOSIS:
            if self._vector_match_service is not None:
                logger.info(
                    "[CandidateRec-Supplement] USING_VECTOR_MATCH | "
                    "mode=EXPERT_DIAGNOSIS, vector_match_service available, calling _try_vector_match"
                )
                result = self._try_vector_match(
                    question=question,
                    top_k=max_supplements,
                    exclude_profile_keys=exclude_profile_keys,
                    runtime_config=runtime_config,
                    filters=filters,
                    vector_min_score=vector_min_score,  # Phase B: 传递向量召回阈值
                    rerank_min_score=rerank_min_score,  # Phase B: 传递 rerank 后质量阈值
                )
                if result:
                    recommendations, reranker_called = result
                    logger.info(
                        "[CandidateRec-Supplement] VECTOR_SUCCESS | "
                        f"got {len(recommendations)} recommendations from vector match, "
                        f"reranker_called={reranker_called}"
                    )
                    # R43: 将 reranker_called 信息添加到运行时 metadata（通过 runtime_config 传递）
                    if runtime_config is None:
                        runtime_config = {}
                    runtime_config["_reranker_called"] = reranker_called
                    return recommendations
                else:
                    logger.warning(
                        "[CandidateRec-Supplement] VECTOR_FAILED | "
                        "_try_vector_match returned None/empty, falling back to empty list"
                    )
                    return []
            else:
                logger.error(
                    "[CandidateRec-Supplement] NO_SERVICE | "
                    "mode=EXPERT_DIAGNOSIS but vector_match_service is None! "
                    "This indicates a wiring problem."
                )
                return []
        else:
            logger.info(
                "[CandidateRec-Supplement] WRONG_MODE | "
                f"mode={mode.value} (not EXPERT_DIAGNOSIS), returning empty list"
            )
            return []

    def _try_vector_match(
        self,
        question: str,
        top_k: int,
        exclude_profile_keys: list[str],
        runtime_config: dict[str, Any] | None = None,
        filters: dict[str, Any] | None = None,
        vector_min_score: float = 0.01,  # Phase B: 向量召回阈值
        rerank_min_score: float | None = None,  # Phase B: rerank 后质量阈值
        min_score: float | None = None,  # DEPRECATED: 向后兼容
    ) -> tuple[list[CandidateRecommendation], bool] | None:
        """
        尝试使用向量匹配获取补充推荐

        Args:
            question: 问题/任务描述
            top_k: 最大返回数量
            exclude_profile_keys: 排除的 profile keys
            runtime_config: 向量搜索运行时配置
            filters: 元数据过滤条件
            vector_min_score: 向量召回阶段的最小相似度阈值
            rerank_min_score: rerank 后的最小质量阈值
            min_score: (DEPRECATED) 旧参数，向后兼容

        Returns:
            tuple: (推荐列表, reranker是否被调用)，失败返回 None
        """
        # Phase B: 向后兼容旧的 min_score 参数
        if min_score is not None and rerank_min_score is None:
            vector_min_score = min_score
            rerank_min_score = min_score

        logger.info(
            "[VectorMatch-Try] START | "
            f"question_len={len(question)}, top_k={top_k}, "
            f"exclude_count={len(exclude_profile_keys)}, vector_min_score={vector_min_score}, rerank_min_score={rerank_min_score}"
        )

        if self._embedding_generator is None:
            logger.error(
                "[VectorMatch-Try] NO_EMBEDDING_GEN | "
                "embedding_generator is None! Cannot perform vector match."
            )
            return None

        # 检查 real embedding 是否启用
        real_embedding_enabled = FeatureFlags.is_real_embedding_enabled()
        if not real_embedding_enabled:
            logger.error(
                "[VectorMatch-Try] EMBEDDING_DISABLED | "
                "ENABLE_REAL_EMBEDDING=false, cannot use vector match."
            )
            return None

        logger.info(
            "[VectorMatch-Try] SERVICES_OK | "
            f"has_embedding_gen=True, has_vector_match=True, "
            f"real_embedding_flag={real_embedding_enabled}"
        )

        try:
            # 生成查询向量
            logger.info("[VectorMatch-Try] GENERATING_EMBEDDING | question_length=%d", len(question))
            query_embedding = self._embedding_generator.embed(question)
            logger.info(
                "[VectorMatch-Try] EMBEDDING_GENERATED | "
                f"dimension={len(query_embedding)}, "
                f"first_3_values=[{query_embedding[0]:.4f}, {query_embedding[1]:.4f}, {query_embedding[2]:.4f}]"
            )

            # 执行向量匹配（传入运行时配置和阈值）
            # Phase B: 传递两个独立的阈值
            logger.info(
                "[VectorMatch-Try] CALLING_MATCH | "
                f"runtime_config={runtime_config}, filters={filters}, "
                f"vector_min_score={vector_min_score}, rerank_min_score={rerank_min_score}"
            )
            match_results = self._vector_match_service.match(
                query_embedding=query_embedding,
                top_k=top_k,
                excluded_profile_keys=exclude_profile_keys,
                query=question,
                runtime_config=runtime_config,
                filters=filters,
                vector_min_score=vector_min_score,  # Phase B: 向量召回阈值
                rerank_min_score=rerank_min_score,  # Phase B: rerank 后质量阈值
            )

            if not match_results:
                logger.warning(
                    "[VectorMatch-Try] NO_RESULTS | "
                    "vector_match_service.match() returned empty list. "
                    "This could mean: (1) vector store is empty, "
                    "(2) no profiles match filters, or (3) min_score too high."
                )
                return None

            logger.info(
                "[VectorMatch-Try] GOT_RESULTS | "
                f"match_count={len(match_results)}, "
                f"top_scores=[{', '.join([f'{r.score:.4f}' for r in match_results[:3]])}]"
            )

            # 直接使用 MatchResult.metadata 中的 payload 数据构建推荐
            # 避免调用 retrieval service，大幅提升性能
            logger.info("[VectorMatch-Try] BUILDING_RECOMMENDATIONS | from metadata payload")

            recommendations = []
            for result in match_results:
                # 从 metadata (MetadataRecord) 直接构建推荐
                rec = self._build_recommendation_from_metadata(
                    metadata=result.metadata,
                    score=result.score,
                    is_supplement=True,
                    fragment_matches=result.fragment_matches,
                    aggregated_score=result.aggregated_score,
                )
                if rec:
                    recommendations.append(rec)

            # R43: 检查是否有任何结果经过 rerank
            reranker_called = any(r.is_reranked for r in match_results) if match_results else False

            logger.info(
                "[VectorMatch-Try] SUCCESS | "
                f"built {len(recommendations)} recommendations, "
                f"reranker_called={reranker_called}, "
                f"keys=[{', '.join(r.profile_key for r in recommendations[:5])}]"
            )
            return (recommendations, reranker_called)

        except Exception as e:
            logger.error(
                "[VectorMatch-Try] EXCEPTION | "
                f"error_type={type(e).__name__}, error_msg={str(e)}",
                exc_info=True
            )
            return None

    def _get_profiles_for_match_results(self, match_results: list[Any]) -> "dict[str, WorkerProfile]":
        """
        批量从 MatchResult 获取 WorkerProfile

        优化：一次性获取所有需要的 profiles，避免多次 scan 带来的性能问题。

        Args:
            match_results: 向量匹配结果列表

        Returns:
            dict[str, WorkerProfile]: profile_key -> Profile 对象的映射
        """
        if not match_results:
            return {}

        try:
            # 收集所有需要的 profile_keys
            all_profile_keys = [r.profile_key for r in match_results]
            logger.debug("Batch fetching %d profiles", len(all_profile_keys))

            # 一次性获取所有 profiles（单次 scan）
            response = self._retrieval_service.retrieve(
                question="",
                mode=RetrievalMode.EXPERT_DIAGNOSIS,
                profile_keys=all_profile_keys,
            )

            # 构建 profile_key -> profile 的映射
            profile_map: dict[str, Any] = {}
            for result in response.results:
                if result.profile:
                    profile_map[result.profile.profile_key] = result.profile

            logger.debug("Batch fetch done: %d/%d profiles", len(profile_map), len(all_profile_keys))
            return profile_map

        except Exception as e:
            logger.warning(f"Failed to batch get profiles: {e}")
            return {}

    def _build_recommendation_from_metadata(
        self,
        metadata: Any,  # MetadataRecord
        score: float,
        is_supplement: bool,
        fragment_matches: list[Any] | None = None,
        aggregated_score: float | None = None,
    ) -> CandidateRecommendation | None:
        """
        从 MetadataRecord 直接构建推荐（优化：避免 retrieval 查询）

        Args:
            metadata: MetadataRecord，包含 profile_key, staff_id, domains, active_skill_names 等
            score: 推荐分数
            is_supplement: 是否为补充推荐
            fragment_matches: Fragment 匹配详情列表（可选）
            aggregated_score: 聚合分数（可选）

        Returns:
            CandidateRecommendation | None: 推荐结果，失败返回 None
        """
        try:
            # 从 metadata 提取字段
            profile_key = metadata.profile_key
            worker_id = metadata.staff_id
            domains = metadata.domains or []
            active_skills = metadata.active_skill_names or []
            short_profile = getattr(metadata, 'short_profile', '')  # 新增：精简画像
            logger.debug("[CandidateRec-Build] profile_key=%s: short_profile='%s' from metadata", profile_key, short_profile)

            # 推断领域（使用 metadata 中的 domains）
            domain = domains[0] if domains else "general"

            # 构建推荐理由
            reasons: list[Union[str, dict[str, Any]]] = []

            # 添加技能信息
            if active_skills:
                reasons.append(f"Relevant skills: {', '.join(active_skills[:3])}")

            # 添加结构化 fragment 得分详情
            if fragment_matches:
                fragments_data = [
                    {
                        "type": fm.fragment_type,
                        "score": round(fm.score, 4),
                        "weighted": round(fm.weighted_score, 4),
                    }
                    for fm in fragment_matches
                ]
                fragment_info: dict[str, Any] = {
                    "fragments": fragments_data,
                    "aggregated_score": round(aggregated_score, 4) if aggregated_score else round(score, 4),
                    "final_score": round(score, 4),
                }
                reasons.append(fragment_info)

            return CandidateRecommendation(
                profile_key=profile_key,
                worker_id=worker_id,
                score=score,
                reasons=reasons,
                domain=domain,
                domain_confidence=0.7 if active_skills else 0.5,
                matched_skills=active_skills,
                matched_contexts=[],  # metadata 中无此字段
                is_supplement=is_supplement,
                short_profile=short_profile,  # 新增：精简画像
            )
        except Exception as e:
            logger.warning(f"Failed to build recommendation from metadata: {e}")
            return None

    def _build_recommendation_from_profile(
        self,
        profile: "WorkerProfile",
        score: float,
        is_supplement: bool,
        reasons: list[Union[str, dict[str, Any]]] | None = None,
        score_breakdown: Optional["ProfileMatchScore"] = None,
    ) -> CandidateRecommendation:
        """
        从 profile 构建推荐

        Args:
            profile: Worker Profile
            score: 推荐分数
            is_supplement: 是否为补充推荐
            reasons: 推荐理由列表（可选，由调用方提供），支持字符串或结构化 dict
            score_breakdown: V2 评分明细（可选，需 ENABLE_G1_SCORE_BREAKDOWN_OUTPUT=true）

        Returns:
            CandidateRecommendation: 推荐结果
        """
        # 推断领域
        domain = self._infer_domain(profile)

        # 构建推荐理由（如果未提供）
        if reasons is None:
            reasons = []
            if profile.active_skills:
                skill_names = [s.name for s in profile.active_skills[:3]]
                reasons.append(f"Relevant skills: {', '.join(skill_names)}")

            if profile.context_fragments:
                reasons.append(f"Has {len(profile.context_fragments)} context fragments")

        # 匹配的技能和上下文
        matched_skills = [s.name for s in profile.active_skills]
        matched_contexts = [f.filename for f in profile.context_fragments]

        # Phase C: 处理 score_breakdown
        # 仅当 Feature Flag 开启且提供了 score_breakdown 时才输出
        output_score_breakdown = None
        if FeatureFlags.is_g1_score_breakdown_output_enabled() and score_breakdown is not None:
            output_score_breakdown = score_breakdown

        return CandidateRecommendation(
            profile_key=profile.profile_key,
            worker_id=profile.staff_id,
            score=score,
            reasons=reasons,
            domain=domain,
            domain_confidence=0.7 if profile.active_skills else 0.5,
            matched_skills=matched_skills,
            matched_contexts=matched_contexts,
            is_supplement=is_supplement,
            score_breakdown=output_score_breakdown,
        )

    def _merge_recommendations_in_order(
        self,
        explicit: list[CandidateRecommendation],
        supplements: list[CandidateRecommendation],
    ) -> list[CandidateRecommendation]:
        """
        合并推荐（显式在前，补充在后）

        Args:
            explicit: 显式推荐
            supplements: 补充推荐

        Returns:
            list[CandidateRecommendation]: 合并后的推荐列表
        """
        return explicit + supplements

    def _build_domain_coverage(
        self,
        question: str,
        recommendations: list[CandidateRecommendation],
    ) -> DomainCoverage:
        """
        构建领域覆盖分析

        Args:
            question: 问题
            recommendations: 推荐列表

        Returns:
            DomainCoverage: 领域覆盖分析
        """
        # 推断所需领域
        required_domains = self._infer_required_domains(question)

        # 收集已覆盖领域
        covered_domains = list(set(r.domain for r in recommendations if r.domain))

        # 计算缺失领域
        missing_domains = [d for d in required_domains if d not in covered_domains]

        # 计算覆盖分数
        if not required_domains:
            coverage_score = 1.0
        else:
            coverage_score = len(covered_domains) / len(required_domains)

        return DomainCoverage(
            required_domains=required_domains,
            covered_domains=covered_domains,
            missing_domains=missing_domains,
            coverage_score=coverage_score,
        )

    def _build_empty_response(
        self,
        question: str,
        mode: RetrievalMode,
        participants: list[str] | None,
    ) -> CandidateRecommendationResponse:
        """
        构建空响应（降级）

        Args:
            question: 问题
            mode: 检索模式
            participants: 显式 participants

        Returns:
            CandidateRecommendationResponse: 空响应
        """
        return CandidateRecommendationResponse(
            recommendations=[],
            question=question,
            mode=mode,
            domain_coverage=DomainCoverage(),
            participants_given=participants is not None and len(participants) > 0,
            participants_sufficient=False,
            total_candidates=0,
            selected_candidates=0,
        )


__all__ = ["WorkerCandidateRecommendationImpl"]