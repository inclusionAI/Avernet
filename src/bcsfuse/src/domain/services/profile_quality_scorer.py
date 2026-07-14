"""
Profile Quality Scorer Service

Profile 质量评分与过滤系统 - 核心评分服务

评分维度：
- SOUL维度 (0.40): SOUL存在(0.10) + SOUL长度分档(0.30)
- SKILL维度 (0.30): SKILL数量分档(0.20) + SKILL描述比例(0.10)
- 内容维度 (0.30): 文本长度分档(0.30)
"""

import logging
import time

from src.domain.models.profile_quality_score import ProfileQualityScore
from src.domain.models.worker_profile_content import WorkerProfileContent


logger = logging.getLogger(__name__)


# =============================================================================
# 阈值常量
# =============================================================================

# SOUL长度分档
SOUL_LENGTH_EXCELLENT = 500  # 满分
SOUL_LENGTH_GOOD = 200       # 中间分

# SKILL数量分档
SKILL_COUNT_EXCELLENT = 5    # 满分
SKILL_COUNT_GOOD = 3         # 中间分
SKILL_COUNT_MINIMAL = 1      # 最低分

# 文本长度分档
TEXT_LENGTH_EXCELLENT = 800  # 满分
TEXT_LENGTH_MINIMAL = 200    # 最低分


class ProfileQualityScorer:
    """
    Profile 质量评分器

    支持 WorkerProfileContent 模型的评分。
    """

    def score_profile_content(self, content: WorkerProfileContent) -> ProfileQualityScore:
        """评分 WorkerProfileContent"""
        start_time = time.time()

        score = 0.0
        issues = []

        # =========================================================================
        # SOUL维度 (0.40): SOUL存在(0.10) + SOUL长度分档(0.30)
        # =========================================================================
        has_soul = content.soul_md and content.soul_md.strip()
        if has_soul:
            score += 0.10
        else:
            issues.append("[WARN] 缺少SOUL.md定义")

        soul_len = len(content.soul_md.strip()) if has_soul else 0
        if soul_len >= SOUL_LENGTH_EXCELLENT:
            score += 0.30
        elif soul_len >= SOUL_LENGTH_GOOD:
            score += 0.15
            issues.append(f"[SUGGEST] SOUL.md内容不够丰富，建议补充到{SOUL_LENGTH_EXCELLENT}+字符，当前{soul_len}字符")
        elif soul_len > 0:
            score += 0.05
            issues.append(f"[SUGGEST] SOUL.md内容不够丰富，建议补充到{SOUL_LENGTH_GOOD}+字符，当前{soul_len}字符")

        # =========================================================================
        # SKILL维度 (0.30): SKILL数量分档(0.20) + SKILL描述比例(0.10)
        # =========================================================================
        skill_count = len(content.skill_sets)
        if skill_count >= SKILL_COUNT_EXCELLENT:
            score += 0.20
        elif skill_count >= SKILL_COUNT_GOOD:
            score += 0.15
            issues.append(f"[SUGGEST] SKILL数量较少，建议添加更多SKILL，当前{skill_count}个，建议{SKILL_COUNT_EXCELLENT}+")
        elif skill_count >= SKILL_COUNT_MINIMAL:
            score += 0.10
            issues.append(f"[SUGGEST] SKILL数量较少，建议添加更多SKILL，当前{skill_count}个，建议{SKILL_COUNT_GOOD}+")
        else:
            issues.append("[WARN] 未配置SKILL")

        # SKILL描述比例评分（按比例直接算分）
        if skill_count > 0:
            desc_count = sum(1 for s in content.skill_sets if s.description and s.description.strip())
            # 得分 = 0.10 × (描述非空技能数 / 总技能数)
            skill_desc_score = 0.10 * (desc_count / skill_count)
            score += skill_desc_score
            if desc_count < skill_count:
                issues.append(f"[SUGGEST] 部分SKILL缺少描述，建议补充({desc_count}/{skill_count}已有描述)")

        # =========================================================================
        # 内容维度 (0.30): 文本长度分档(>=800:0.30, >=200:0.15)
        # =========================================================================
        text_len = self._get_total_text_length(content)
        if text_len >= TEXT_LENGTH_EXCELLENT:
            score += 0.30
        elif text_len >= TEXT_LENGTH_MINIMAL:
            score += 0.15
            issues.append(f"[SUGGEST] Profile内容不够丰富，建议添加更多MD描述，当前{text_len}字符，建议{TEXT_LENGTH_EXCELLENT}+")
        else:
            issues.append("[WARN] Profile内容过短")

        duration_ms = (time.time() - start_time) * 1000
        logger.info(
            "[QualityScorer] Content scored: key=%s, score=%.2f, duration=%.2fms",
            content.profile_key, score, duration_ms
        )

        return ProfileQualityScore(
            profile_key=content.profile_key,
            total_score=score,
            issues=issues,
        )

    def _get_total_text_length(self, content: WorkerProfileContent) -> int:
        """计算总文本长度"""
        length = 0
        for field in [content.soul_md, content.agents_md, content.tools_md, content.boot_md, content.heartbeat_md]:
            if field:
                length += len(field)
        for c in content.contents.values():
            length += len(c)
        return length


__all__ = ["ProfileQualityScorer"]