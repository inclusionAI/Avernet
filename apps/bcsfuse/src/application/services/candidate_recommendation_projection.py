"""Project indexed profile metadata into candidate recommendations."""

import logging
from typing import Any, Union

from src.domain.models.candidate_recommendation import CandidateRecommendation

logger = logging.getLogger(__name__)


def build_recommendation_from_metadata(
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
