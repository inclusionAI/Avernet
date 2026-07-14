"""
Worker Context Preparation Service

Worker Profile Retrieval & Fusion Simulation Baseline

Task-specific context 裁剪服务，从完整 WorkerProfile 中提取与当前任务相关的上下文。
"""

from __future__ import annotations

import re
from typing import Optional

from src.domain.models.context_fragment import ContextFragment
from src.domain.models.retrieval_mode import RetrievalMode
from src.domain.models.skill_profile import SkillProfile
from src.domain.models.worker_context_digest import WorkerContextDigest
from src.domain.models.worker_profile import WorkerProfile


class WorkerContextPreparationService:
    """
    Worker Context 准备服务

    职责：
    - 从完整 WorkerProfile 中裁剪出与当前任务相关的上下文
    - 对 fragments 和 skills 进行评分和筛选
    - 生成 task-specific 的上下文摘要

    特点：
    - mode-aware: 不同模式可能有不同的关注度
    - 可配置: 支持限制返回的 fragments/skills 数量
    """

    def __init__(
        self,
        default_max_fragments: int = 5,
        default_max_skills: int = 5,
        min_fragment_score: float = 0.01,  # 降低阈值，允许弱匹配通过
        min_skill_score: float = 0.01,  # 降低阈值，允许弱匹配通过
    ):
        """
        初始化服务

        Args:
            default_max_fragments: 默认最大片段数
            default_max_skills: 默认最大技能数
            min_fragment_score: 片段最低分数阈值
            min_skill_score: 技能最低分数阈值
        """
        self.default_max_fragments = default_max_fragments
        self.default_max_skills = default_max_skills
        self.min_fragment_score = min_fragment_score
        self.min_skill_score = min_skill_score

    def prepare(
        self,
        profile: WorkerProfile,
        question: str,
        mode: RetrievalMode,
        max_fragments: Optional[int] = None,
        max_skills: Optional[int] = None,
    ) -> WorkerContextDigest:
        """
        准备 task-specific context digest

        Args:
            profile: Worker Profile
            question: 问题/任务描述
            mode: 检索模式
            max_fragments: 最大片段数（None 表示使用默认值）
            max_skills: 最大技能数（None 表示使用默认值）

        Returns:
            WorkerContextDigest: 任务相关的上下文摘要
        """
        max_fragments = max_fragments or self.default_max_fragments
        max_skills = max_skills or self.default_max_skills

        question_lower = question.lower()
        keywords = self._extract_keywords(question)

        # 评分和筛选 fragments
        fragment_scores = self._score_fragments(
            profile.context_fragments, question_lower, keywords, mode
        )
        selected_fragments = self._select_top_items(
            profile.context_fragments,
            fragment_scores,
            max_fragments,
            self.min_fragment_score,
            key_func=lambda f: f.filename,
        )

        # 评分和筛选 skills
        skill_scores = self._score_skills(
            profile.active_skills, question_lower, keywords, mode
        )
        selected_skills = self._select_top_items(
            profile.active_skills,
            skill_scores,
            max_skills,
            self.min_skill_score,
            key_func=lambda s: s.name,
        )

        # 生成上下文摘要
        context_summary = self._generate_summary(
            selected_fragments, selected_skills, question, mode
        )

        # 生成选择理由
        reasons = self._generate_reasons(
            selected_fragments, selected_skills, fragment_scores, skill_scores
        )

        return WorkerContextDigest(
            profile_key=profile.profile_key,
            mode=mode,
            question=question,
            context_summary=context_summary,
            relevant_fragments=selected_fragments,
            relevant_skills=selected_skills,
            fragment_scores=fragment_scores,
            skill_scores=skill_scores,
            reasons=reasons,
            total_fragments=len(profile.context_fragments),
            selected_fragments=len(selected_fragments),
            total_skills=len(profile.active_skills),
            selected_skills=len(selected_skills),
        )

    def _score_fragments(
        self,
        fragments: list[ContextFragment],
        question_lower: str,
        keywords: list[str],
        mode: RetrievalMode,
    ) -> dict[str, float]:
        """
        对 fragments 进行评分

        Args:
            fragments: 片段列表
            question_lower: 小写问题
            keywords: 关键词列表
            mode: 检索模式

        Returns:
            片段文件名 -> 分数的映射
        """
        scores: dict[str, float] = {}

        for fragment in fragments:
            score = self._calculate_fragment_score(fragment, question_lower, keywords, mode)
            scores[fragment.filename] = score

        return scores

    def _calculate_fragment_score(
        self,
        fragment: ContextFragment,
        question_lower: str,
        keywords: list[str],
        mode: RetrievalMode,
    ) -> float:
        """
        计算单个 fragment 的分数

        Args:
            fragment: 上下文片段
            question_lower: 小写问题
            keywords: 关键词列表
            mode: 检索模式

        Returns:
            分数 (0-1)
        """
        if not fragment.content:
            return 0.0

        content_lower = fragment.content.lower()
        score = 0.0

        # 1. 完整问题匹配（最高分数）
        if question_lower in content_lower:
            score += 0.5

        # 2. 关键词匹配
        keyword_matches = sum(1 for kw in keywords if kw in content_lower)
        if keywords and keyword_matches > 0:
            keyword_ratio = keyword_matches / len(keywords)
            score += keyword_ratio * 0.4

        # 3. 片段类型权重（不同模式可能关注不同类型）
        type_weight = self._get_fragment_type_weight(fragment.kind.value, mode)
        score *= type_weight

        # 4. 内容长度因子（略长内容可能更有价值，但不应该太长）
        length_factor = min(len(fragment.content) / 500, 1.0)  # 500 字符为理想长度
        score += length_factor * 0.1

        return min(score, 1.0)

    def _get_fragment_type_weight(self, kind: str, mode: RetrievalMode) -> float:
        """
        获取片段类型权重

        不同模式可能关注不同类型的上下文。

        Args:
            kind: 片段类型
            mode: 检索模式

        Returns:
            权重 (0-1)
        """
        # 基础权重映射
        base_weights = {
            "agent": 1.0,    # AGENTS.md 通常是最重要的
            "soul": 0.9,     # SOUL.md 包含核心价值观
            "tools": 0.7,    # TOOLS.md 工具使用
            "boot": 0.6,     # BOOT.md 启动配置
            "heartbeat": 0.5,
            "rules": 0.8,    # RULES.md 规则约束
            "memory": 0.6,
            "user": 0.7,
            "other": 0.5,
        }

        weight = base_weights.get(kind, 0.5)

        # 根据模式调整
        if mode == RetrievalMode.EXPERT_DIAGNOSIS:
            # G5 关注全面信息
            weight = min(weight * 1.1, 1.0)
        elif mode == RetrievalMode.CONFLICT_ALIGNMENT:
            # G2 关注视角差异
            if kind in ("soul", "rules"):
                weight = min(weight * 1.2, 1.0)

        return weight

    def _score_skills(
        self,
        skills: list[SkillProfile],
        question_lower: str,
        keywords: list[str],
        mode: RetrievalMode,
    ) -> dict[str, float]:
        """
        对 skills 进行评分

        Args:
            skills: 技能列表
            question_lower: 小写问题
            keywords: 关键词列表
            mode: 检索模式

        Returns:
            技能名称 -> 分数的映射
        """
        scores: dict[str, float] = {}

        for skill in skills:
            score = self._calculate_skill_score(skill, question_lower, keywords, mode)
            scores[skill.name] = score

        return scores

    def _calculate_skill_score(
        self,
        skill: SkillProfile,
        question_lower: str,
        keywords: list[str],
        mode: RetrievalMode,
    ) -> float:
        """
        计算单个 skill 的分数

        Args:
            skill: 技能
            question_lower: 小写问题
            keywords: 关键词列表
            mode: 检索模式

        Returns:
            分数 (0-1)
        """
        score = 0.0
        skill_name_lower = skill.name.lower()

        # 1. 技能名称完全匹配或包含
        if skill_name_lower == question_lower:
            score += 1.0
        elif skill_name_lower in question_lower or question_lower in skill_name_lower:
            score += 0.8
        else:
            # 关键词匹配
            for keyword in keywords:
                if keyword in skill_name_lower:
                    score += 0.3
                    break

        # 2. 技能描述匹配
        if skill.description:
            desc_lower = skill.description.lower()

            # 完整问题匹配
            if question_lower in desc_lower:
                score += 0.3

            # 关键词匹配
            keyword_matches = sum(1 for kw in keywords if kw in desc_lower)
            if keywords and keyword_matches > 0:
                score += (keyword_matches / len(keywords)) * 0.2

        return min(score, 1.0)

    def _select_top_items(
        self,
        items: list,
        scores: dict[str, float],
        max_count: int,
        min_score: float,
        key_func,
    ) -> list:
        """
        选择得分最高的项

        Args:
            items: 候选项列表
            scores: score 映射
            max_count: 最大数量
            min_score: 最低分数阈值
            key_func: 获取 score key 的函数

        Returns:
            选中的项列表
        """
        # 过滤低分项
        qualified = [
            item for item in items
            if scores.get(key_func(item), 0) >= min_score
        ]

        # 按分数降序排序
        qualified.sort(key=lambda x: scores.get(key_func(x), 0), reverse=True)

        # 如果没有符合条件的项，但列表不为空，则选择分数最高的前N项作为fallback
        if not qualified and items:
            all_items = list(items)
            all_items.sort(key=lambda x: scores.get(key_func(x), 0), reverse=True)
            qualified = all_items[:max_count]

        # 返回 top N
        return qualified[:max_count]

    def _generate_summary(
        self,
        fragments: list[ContextFragment],
        skills: list[SkillProfile],
        question: str,
        mode: RetrievalMode,
    ) -> str:
        """
        生成上下文摘要

        Args:
            fragments: 选中的片段
            skills: 选中的技能
            question: 问题
            mode: 模式

        Returns:
            上下文摘要文本
        """
        parts: list[str] = []

        # 技能概述
        if skills:
            skill_names = [s.name for s in skills[:3]]  # 最多显示 3 个
            parts.append(f"Relevant skills: {', '.join(skill_names)}")

        # 片段内容预览
        for fragment in fragments[:2]:  # 最多显示 2 个片段预览
            kind = fragment.kind.value.upper()
            preview = fragment.content[:150].replace('\n', ' ')
            if len(fragment.content) > 150:
                preview += "..."
            parts.append(f"[{kind}]: {preview}")

        if not parts:
            return "No highly relevant context found for the given question."

        return "\n".join(parts)

    def _generate_reasons(
        self,
        fragments: list[ContextFragment],
        skills: list[SkillProfile],
        fragment_scores: dict[str, float],
        skill_scores: dict[str, float],
    ) -> list[str]:
        """
        生成选择理由

        Args:
            fragments: 选中的片段
            skills: 选中的技能
            fragment_scores: 片段分数
            skill_scores: 技能分数

        Returns:
            理由列表
        """
        reasons: list[str] = []

        # 技能选择理由
        if skills:
            top_skill = skills[0]
            score = skill_scores.get(top_skill.name, 0)
            if score > 0.5:
                reasons.append(f"High skill relevance: {top_skill.name} (score: {score:.2f})")
            else:
                reasons.append(f"Partial skill match: {top_skill.name}")

        # 片段选择理由
        if fragments:
            fragment_kinds = list(set(f.kind.value for f in fragments))
            if len(fragment_kinds) == 1:
                reasons.append(f"Context from {fragment_kinds[0].upper()}")
            else:
                kinds_str = ", ".join(k.upper() for k in fragment_kinds)
                reasons.append(f"Context from multiple sources: {kinds_str}")

        # 数量统计
        if fragments or skills:
            reasons.append(
                f"Selected {len(fragments)} fragments and {len(skills)} skills"
            )

        if not reasons:
            reasons.append("No highly relevant content matched the question")

        return reasons

    def _extract_keywords(self, text: str) -> list[str]:
        """
        提取关键词

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
            "those", "am", "help", "want", "like", "tell", "give",
            "design", "implement", "create", "build", "make",
        }

        return [w for w in words if len(w) > 2 and w not in stopwords]


__all__ = ["WorkerContextPreparationService"]