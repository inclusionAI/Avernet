"""
ProfileSemanticRanker

Phase C: G1 Semantic Rerank V2

V2 Profile 语义排序器，作为 G1 V2 评分的唯一来源。

实现两阶段评分：
- Phase 1: Base Score 计算（单候选评分）
- Phase 2: Diversity-Aware Rerank（结果集重排）

Base Score 公式：
    base_score = semantic_similarity * 0.44
               + capability_coverage * 0.28
               + scenario_match * 0.18
               + availability_score * 0.10

strict_participants 约束：
- strict=true 时，diversity rerank 不能引入 participants 外的候选
- 由调用方确保传入正确的候选集
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from src.application.services.semantic_match_service import SemanticMatchService
from src.domain.models.profile_match_score import (
    AVAILABILITY_SCORE_WEIGHT,
    CAPABILITY_COVERAGE_WEIGHT,
    ProfileMatchScore,
    SCENARIO_MATCH_WEIGHT,
    SEMANTIC_SIMILARITY_WEIGHT,
    ScoreComponent,
)
from src.domain.models.worker_profile import WorkerProfile
from src.domain.taxonomy.registry import TaxonomyRegistry, get_taxonomy_registry
from src.infra.config.feature_flags import FeatureFlags

logger = logging.getLogger(__name__)


@dataclass
class RerankContext:
    """
    Rerank 上下文

    包含重排序所需的额外信息。

    Attributes:
        question: 问题文本
        mode: 检索模式
        strict_participants: 是否启用严格参与者模式
        is_online_map: profile_key -> 是否在线 的映射
        profile_keys: 显式指定的 profile_keys（用于 strict 检查）
    """
    question: str
    mode: str = "agent"
    strict_participants: bool = False
    is_online_map: Optional[dict[str, bool]] = None
    profile_keys: Optional[list[str]] = None


class ProfileSemanticRanker:
    """
    V2 Profile 语义排序器

    作为 G1 V2 评分的唯一来源，实现两阶段评分：
    1. Phase 1: Base Score 计算
    2. Phase 2: Diversity-Aware Rerank

    使用方式：
        ranker = ProfileSemanticRanker()
        scores = ranker.rank(profiles, context, top_k=5)
    """

    def __init__(
        self,
        semantic_match_service: Optional[SemanticMatchService] = None,
        taxonomy_registry: Optional[TaxonomyRegistry] = None,
    ):
        """
        初始化排序器

        Args:
            semantic_match_service: 语义匹配服务（可选）
            taxonomy_registry: Taxonomy 注册表（可选）
        """
        self._semantic_match = semantic_match_service or SemanticMatchService()
        self._taxonomy = taxonomy_registry or get_taxonomy_registry()

    def rank(
        self,
        profiles: list[WorkerProfile],
        context: RerankContext,
        top_k: Optional[int] = None,
    ) -> list[tuple[WorkerProfile, ProfileMatchScore]]:
        """
        对 profiles 进行 V2 评分和排序

        Args:
            profiles: 待排序的 profiles
            context: Rerank 上下文
            top_k: 返回数量限制

        Returns:
            list[tuple[WorkerProfile, ProfileMatchScore]]:
                排序后的 (profile, score) 元组列表
        """
        if not profiles:
            return []

        # 读取 Feature Flags
        use_v2_rerank = FeatureFlags.is_g1_profile_rerank_enabled()
        use_semantic_expansion = FeatureFlags.is_g1_semantic_match_enabled()
        output_breakdown = FeatureFlags.is_g1_score_breakdown_output_enabled()

        logger.debug(
            "[ProfileSemanticRanker] rank: profiles=%d, v2_rerank=%s, semantic_expansion=%s",
            len(profiles), use_v2_rerank, use_semantic_expansion
        )

        # Phase 1: Base Score 计算
        scored_profiles: list[tuple[WorkerProfile, ProfileMatchScore]] = []
        for profile in profiles:
            score = self.compute_base_score(
                profile=profile,
                question=context.question,
                context=context,
                use_semantic_expansion=use_semantic_expansion,
            )
            scored_profiles.append((profile, score))

        # Phase 2: Diversity-Aware Rerank
        if use_v2_rerank and top_k is not None:
            scored_profiles = self.diversity_rerank(
                scored_profiles=scored_profiles,
                top_k=top_k,
                context=context,
            )

        # 按 final_score 降序排序
        scored_profiles.sort(key=lambda x: x[1].final_score, reverse=True)

        # 应用 top_k
        if top_k is not None:
            scored_profiles = scored_profiles[:top_k]

        return scored_profiles

    def compute_base_score(
        self,
        profile: WorkerProfile,
        question: str,
        context: RerankContext,
        use_semantic_expansion: Optional[bool] = None,
    ) -> ProfileMatchScore:
        """
        Phase 1: 计算单个候选的 base_score

        base_score = semantic_similarity * 0.44
                   + capability_coverage * 0.28
                   + scenario_match * 0.18
                   + availability_score * 0.10

        Args:
            profile: Worker Profile
            question: 问题文本
            context: Rerank 上下文
            use_semantic_expansion: 是否使用语义扩展

        Returns:
            ProfileMatchScore: 完整的评分结果
        """
        # 从 FeatureFlags 读取默认值
        if use_semantic_expansion is None:
            use_semantic_expansion = FeatureFlags.is_g1_semantic_match_enabled()

        flags_enabled = []
        if FeatureFlags.is_g1_profile_rerank_enabled():
            flags_enabled.append("ENABLE_G1_PROFILE_RERANK")
        if FeatureFlags.is_g1_semantic_match_enabled():
            flags_enabled.append("ENABLE_G1_SEMANTIC_MATCH")
        if FeatureFlags.is_g1_score_breakdown_output_enabled():
            flags_enabled.append("ENABLE_G1_SCORE_BREAKDOWN_OUTPUT")

        # 1. semantic_similarity
        semantic_score, semantic_details = self._semantic_match.compute_semantic_similarity(
            question=question,
            profile=profile,
            use_semantic_expansion=use_semantic_expansion,
        )
        semantic_component = ScoreComponent(
            raw_score=semantic_score,
            weight=SEMANTIC_SIMILARITY_WEIGHT,
            weighted_score=semantic_score * SEMANTIC_SIMILARITY_WEIGHT,
            details=semantic_details,
        )

        # 2. capability_coverage
        capability_score, capability_details = self._compute_capability_coverage(
            profile=profile,
            question=question,
        )
        capability_component = ScoreComponent(
            raw_score=capability_score,
            weight=CAPABILITY_COVERAGE_WEIGHT,
            weighted_score=capability_score * CAPABILITY_COVERAGE_WEIGHT,
            details=capability_details,
        )

        # 3. scenario_match
        scenario_score, scenario_details = self._compute_scenario_match(
            profile=profile,
            question=question,
        )
        scenario_component = ScoreComponent(
            raw_score=scenario_score,
            weight=SCENARIO_MATCH_WEIGHT,
            weighted_score=scenario_score * SCENARIO_MATCH_WEIGHT,
            details=scenario_details,
        )

        # 4. availability_score
        availability_score, availability_details = self._compute_availability(
            profile=profile,
            context=context,
        )
        availability_component = ScoreComponent(
            raw_score=availability_score,
            weight=AVAILABILITY_SCORE_WEIGHT,
            weighted_score=availability_score * AVAILABILITY_SCORE_WEIGHT,
            details=availability_details,
        )

        # 计算 base_score
        base_score = (
            semantic_component.weighted_score
            + capability_component.weighted_score
            + scenario_component.weighted_score
            + availability_component.weighted_score
        )

        return ProfileMatchScore(
            final_score=min(1.0, max(0.0, base_score)),
            semantic_similarity=semantic_component,
            capability_coverage=capability_component,
            scenario_match=scenario_component,
            availability_score=availability_component,
            scorer_version="v2",
            flags_enabled=flags_enabled,
        )

    def diversity_rerank(
        self,
        scored_profiles: list[tuple[WorkerProfile, ProfileMatchScore]],
        top_k: int,
        context: RerankContext,
    ) -> list[tuple[WorkerProfile, ProfileMatchScore]]:
        """
        Phase 2: Diversity-aware rerank (greedy 风格)

        目的：避免结果集同质化

        实现：
        1. Greedy 选择
        2. 每次选择时考虑对已选结果集的多样性贡献
        3. 更新 final_score = base_score + diversity_delta

        strict_participants 约束：
        - 当 strict_participants=True 时，不引入新候选
        - 仅在已有候选集内做重排

        Args:
            scored_profiles: 已评分的 profiles
            top_k: 目标数量
            context: Rerank 上下文

        Returns:
            list[tuple[WorkerProfile, ProfileMatchScore]]: 重排后的结果
        """
        if not scored_profiles or top_k <= 0:
            return scored_profiles

        # strict 模式：不做扩展，仅在现有候选内重排
        if context.strict_participants and context.profile_keys:
            logger.debug(
                "[ProfileSemanticRanker] strict 模式，限制候选集为 %d 个",
                len(scored_profiles)
            )

        selected: list[tuple[WorkerProfile, ProfileMatchScore]] = []
        selected_skills: set[str] = set()
        selected_domains: set[str] = set()

        # 按 base_score 初始排序
        remaining = sorted(
            scored_profiles,
            key=lambda x: x[1].base_score,
            reverse=True
        )

        for _ in range(min(top_k, len(remaining))):
            best_idx = self._select_with_diversity(
                remaining=remaining,
                selected=selected,
                selected_skills=selected_skills,
                selected_domains=selected_domains,
            )

            if best_idx is not None:
                profile, score = remaining.pop(best_idx)

                # 计算多样性增量
                profile_skills = set(s.name.lower() for s in profile.active_skills)
                new_skills = profile_skills - selected_skills
                diversity_delta = len(new_skills) / max(len(profile_skills), 1) * 0.1

                # 更新分数
                score.diversity_adjusted = True
                score.diversity_delta = diversity_delta
                score.final_score = min(1.0, score.final_score + diversity_delta)

                selected.append((profile, score))
                selected_skills.update(profile_skills)
                # 更新领域信息（从 skills 或 context 推断）
                selected_domains.update(s.name.lower() for s in profile.active_skills)

        return selected

    def _select_with_diversity(
        self,
        remaining: list[tuple[WorkerProfile, ProfileMatchScore]],
        selected: list[tuple[WorkerProfile, ProfileMatchScore]],
        selected_skills: set[str],
        selected_domains: set[str],
    ) -> Optional[int]:
        """
        选择对已选结果集多样性贡献最大的候选

        Args:
            remaining: 剩余候选
            selected: 已选候选
            selected_skills: 已选技能集
            selected_domains: 已选领域集

        Returns:
            Optional[int]: 最佳候选在 remaining 中的索引
        """
        if not remaining:
            return None

        best_idx = 0
        best_score = -1.0

        for idx, (profile, score) in enumerate(remaining):
            # 计算多样性贡献
            profile_skills = set(s.name.lower() for s in profile.active_skills)
            new_skills = profile_skills - selected_skills
            diversity_contribution = len(new_skills) / max(len(profile_skills), 1)

            # 综合评分：base_score + 多样性奖励
            combined_score = score.base_score + diversity_contribution * 0.2

            if combined_score > best_score:
                best_score = combined_score
                best_idx = idx

        return best_idx

    def _compute_capability_coverage(
        self,
        profile: WorkerProfile,
        question: str,
    ) -> tuple[float, dict]:
        """
        计算能力覆盖度

        基于 profile capabilities/skills 对问题需求的覆盖程度。

        Args:
            profile: Worker Profile
            question: 问题文本

        Returns:
            tuple[float, dict]: (分数 0-1, 详细信息)
        """
        question_lower = question.lower()

        # 从问题中提取可能的能力需求关键词
        capability_keywords = self._extract_capability_keywords(question)

        if not capability_keywords:
            return 0.5, {"reason": "no capability keywords extracted"}

        # 计算匹配
        matched_capabilities = []
        for skill in profile.active_skills:
            skill_name_lower = skill.name.lower()
            skill_desc_lower = (skill.description or "").lower()

            for kw in capability_keywords:
                if kw in skill_name_lower or kw in skill_desc_lower:
                    matched_capabilities.append(skill.name)
                    break

        coverage = len(matched_capabilities) / len(capability_keywords) if capability_keywords else 0.0

        return min(1.0, coverage), {
            "matched_count": len(matched_capabilities),
            "total_keywords": len(capability_keywords),
            "matched_capabilities": matched_capabilities[:5],
        }

    def _compute_scenario_match(
        self,
        profile: WorkerProfile,
        question: str,
    ) -> tuple[float, dict]:
        """
        计算场景匹配度

        基于 taxonomy scenarios 判断问题场景与 profile 匹配度。

        Args:
            profile: Worker Profile
            question: 问题文本

        Returns:
            tuple[float, dict]: (分数 0-1, 详细信息)
        """
        question_lower = question.lower()

        # 从 taxonomy 查找匹配的场景
        matched_scenarios = []
        config = self._taxonomy.get_config()

        for scenario_id, scenario in config.scenarios.business_scenarios.items():
            for kw in scenario.keywords:
                if kw.lower() in question_lower:
                    matched_scenarios.append({
                        "id": scenario_id,
                        "name": scenario.name,
                        "weight": scenario.risk_weight,
                    })
                    break

        if not matched_scenarios:
            return 0.5, {"reason": "no scenario matched"}

        # 场景权重越高，匹配度越高
        avg_weight = sum(s["weight"] for s in matched_scenarios) / len(matched_scenarios)
        # 归一化到 0-1
        scenario_score = min(1.0, avg_weight)

        return scenario_score, {
            "matched_count": len(matched_scenarios),
            "matched_scenarios": matched_scenarios[:3],
        }

    def _compute_availability(
        self,
        profile: WorkerProfile,
        context: RerankContext,
    ) -> tuple[float, dict]:
        """
        计算可用性评分

        基于 worker registry 状态，给予在线 Worker 加分。

        Args:
            profile: Worker Profile
            context: Rerank 上下文

        Returns:
            tuple[float, dict]: (分数 0-1, 详细信息)
        """
        # 默认可用
        if context.is_online_map is None:
            return 1.0, {"reason": "no availability info, assuming available"}

        # 检查在线状态
        is_online = context.is_online_map.get(profile.profile_key, False)

        if is_online:
            return 1.0, {"status": "online"}
        else:
            return 0.5, {"status": "offline"}

    def _extract_capability_keywords(self, text: str) -> list[str]:
        """
        从文本中提取能力关键词

        基于 taxonomy domains 和常见技能词汇。

        Args:
            text: 输入文本

        Returns:
            list[str]: 能力关键词列表
        """
        text_lower = text.lower()
        keywords = []

        # 从 taxonomy domains 提取
        config = self._taxonomy.get_config()
        for domain_id, domain in config.domains.technical_domains.items():
            for kw in domain.keywords:
                if kw.lower() in text_lower:
                    keywords.append(domain_id)
                    break

        for domain_id, domain in config.domains.business_domains.items():
            for kw in domain.keywords:
                if kw.lower() in text_lower:
                    keywords.append(domain_id)
                    break

        # 去重
        return list(set(keywords))


__all__ = [
    "ProfileSemanticRanker",
    "RerankContext",
]