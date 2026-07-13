"""
Worker Profile Query Service

Worker Profile Ingestion Baseline

查询服务，提供 list/get/search/recommend 功能。
"""

from __future__ import annotations

import re
from typing import Optional

from src.domain.models.worker_profile import (
    WorkerProfile,
    ProfileMatchResult,
    ProfileSearchResult,
    ProfileRecommendResult,
)
from src.domain.services.worker_profile_source import WorkerProfileSource


class WorkerProfileQueryService:
    """
    Worker Profile 查询服务

    职责：
    - list_profiles: 列出所有 profiles
    - get_profile: 获取单个 profile
    - get_profiles_by_staff: 获取员工的所有 profiles
    - search_profiles: 搜索 profiles（baseline 实现）
    - recommend_profiles: 推荐 profiles（baseline 实现）

    当前实现：
    - baseline 搜索：基于关键词匹配 searchable_text
    - baseline 推荐：基于任务描述关键词匹配
    """

    def __init__(self, source: WorkerProfileSource):
        """
        初始化服务

        Args:
            source: Worker Profile 来源
        """
        self._source = source

    def list_profiles(self) -> list[WorkerProfile]:
        """
        列出所有 profiles

        Returns:
            WorkerProfile 列表
        """
        result = self._source.scan()
        return result.profiles

    def get_profile(self, staff_id: str, profile_id: str) -> Optional[WorkerProfile]:
        """
        获取单个 profile

        Args:
            staff_id: 员工 ID
            profile_id: 画像 ID

        Returns:
            WorkerProfile 或 None
        """
        return self._source.get_profile(staff_id, profile_id)

    def get_profiles_by_staff(self, staff_id: str) -> list[WorkerProfile]:
        """
        获取员工的所有 profiles

        Args:
            staff_id: 员工 ID

        Returns:
            WorkerProfile 列表
        """
        return self._source.get_profiles_by_staff(staff_id)

    def search_profiles(
        self,
        query: str,
        top_k: Optional[int] = None,
    ) -> ProfileSearchResult:
        """
        搜索 profiles

        Baseline 实现：基于关键词匹配 searchable_text。

        Args:
            query: 搜索查询
            top_k: 返回数量限制

        Returns:
            ProfileSearchResult: 搜索结果
        """
        matches: list[ProfileMatchResult] = []
        profiles = self.list_profiles()

        # 提取查询关键词
        query_lower = query.lower()
        query_keywords = self._extract_keywords(query)

        for profile in profiles:
            score, matched_fields, reasons = self._calculate_match_score(
                profile, query_lower, query_keywords
            )

            if score > 0:
                matches.append(ProfileMatchResult(
                    profile=profile,
                    score=score,
                    matched_fields=matched_fields,
                    reasons=reasons,
                ))

        # 按分数降序排序
        matches.sort(key=lambda m: m.score, reverse=True)

        # 应用 top_k
        if top_k is not None:
            matches = matches[:top_k]

        return ProfileSearchResult(
            matches=matches,
            query=query,
            total_count=len(matches),
        )

    def recommend_profiles(
        self,
        context: str,
        top_k: Optional[int] = None,
    ) -> ProfileRecommendResult:
        """
        推荐 profiles

        Baseline 实现：基于任务描述关键词匹配。

        Args:
            context: 推荐 context（问题/任务描述）
            top_k: 返回数量限制

        Returns:
            ProfileRecommendResult: 推荐结果
        """
        matches: list[ProfileMatchResult] = []
        profiles = self.list_profiles()

        # 提取 context 关键词
        context_lower = context.lower()
        context_keywords = self._extract_keywords(context)

        for profile in profiles:
            score, matched_fields, reasons = self._calculate_match_score(
                profile, context_lower, context_keywords
            )

            if score > 0:
                matches.append(ProfileMatchResult(
                    profile=profile,
                    score=score,
                    matched_fields=matched_fields,
                    reasons=reasons,
                ))

        # 按分数降序排序
        matches.sort(key=lambda m: m.score, reverse=True)

        # 应用 top_k
        if top_k is not None:
            matches = matches[:top_k]

        return ProfileRecommendResult(
            recommendations=matches,
            context=context,
            strategy="baseline",
        )

    def _extract_keywords(self, text: str) -> list[str]:
        """
        提取关键词

        简单实现：分词并过滤停用词。

        Args:
            text: 输入文本

        Returns:
            关键词列表
        """
        # 简单分词
        words = re.findall(r"\b\w+\b", text.lower())

        # 过滤短词和常见停用词
        stopwords = {"a", "an", "the", "is", "are", "was", "were", "be", "been",
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
                     "those", "am"}

        keywords = [w for w in words if len(w) > 2 and w not in stopwords]
        return keywords

    def _calculate_match_score(
        self,
        profile: WorkerProfile,
        query_lower: str,
        query_keywords: list[str],
    ) -> tuple[float, list[str], list[str]]:
        """
        计算匹配分数

        Args:
            profile: Worker Profile
            query_lower: 小写查询
            query_keywords: 查询关键词

        Returns:
            (分数, 匹配字段, 匹配原因)
        """
        score = 0.0
        matched_fields: list[str] = []
        reasons: list[str] = []

        searchable_text = profile.searchable_text.lower()

        # 1. 完整查询匹配
        if query_lower in searchable_text:
            score += 0.3
            matched_fields.append("searchable_text.full_match")
            reasons.append(f"Full query match in profile")

        # 2. 关键词匹配
        keyword_matches = 0
        for keyword in query_keywords:
            if keyword in searchable_text:
                keyword_matches += 1
                matched_fields.append(f"keyword.{keyword}")

        if query_keywords and keyword_matches > 0:
            keyword_score = keyword_matches / len(query_keywords)
            score += keyword_score * 0.5
            if keyword_matches > 0:
                reasons.append(f"Matched {keyword_matches} keywords")

        # 3. 技能名称匹配
        for skill in profile.active_skills:
            skill_name_lower = skill.name.lower()
            if skill_name_lower in query_lower or query_lower in skill_name_lower:
                score += 0.2
                matched_fields.append(f"skill.{skill.name}")
                reasons.append(f"Skill match: {skill.name}")

            # 关键词匹配技能名称
            for keyword in query_keywords:
                if keyword in skill_name_lower:
                    score += 0.1
                    if f"skill.{skill.name}" not in matched_fields:
                        matched_fields.append(f"skill.{skill.name}")

        # 4. 上下文匹配
        for fragment in profile.context_fragments:
            content_lower = fragment.content.lower()
            if query_lower in content_lower:
                score += 0.1
                matched_fields.append(f"context.{fragment.kind.value}")
                reasons.append(f"Context match in {fragment.filename}")

        # 确保分数在 0-1 之间
        score = min(score, 1.0)

        return score, matched_fields, reasons


__all__ = ["WorkerProfileQueryService"]