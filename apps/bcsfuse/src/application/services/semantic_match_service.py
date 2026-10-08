"""
SemanticMatchService

Phase C: G1 Semantic Rerank V2

语义匹配服务，计算 semantic_similarity 组件分数。

首版实现：
- 不是 query/profile embedding cosine similarity
- 基于 taxonomy/scenario/domain expansion 扩展问题语义
- 与 profile 的 searchable_text/capabilities/domains 进行文本匹配
- 不依赖 profile embedding

未来升级方向：
- 具备 profile embedding 缓存/持久化后
- 可升级为真正的向量相似度计算
"""

from __future__ import annotations

import logging
import re
from typing import Optional

from src.domain.models.worker_profile import WorkerProfile
from src.domain.taxonomy.registry import TaxonomyRegistry, get_taxonomy_registry
from src.infra.config.feature_flags import FeatureFlags

logger = logging.getLogger(__name__)

# 分词正则（中英文分词）
_WORD_PATTERN = re.compile(r'[\w\u4e00-\u9fff]+')


class SemanticMatchService:
    """
    语义匹配服务

    计算 semantic_similarity 组件分数。

    支持两种模式：
    1. 语义扩展匹配（use_semantic_expansion=True）：使用 taxonomy 扩展问题语义
    2. 基础关键词匹配（use_semantic_expansion=False）：纯关键词匹配

    首版实现为 semantic-oriented text similarity，不依赖 embedding。
    """

    def __init__(
        self,
        taxonomy_registry: Optional[TaxonomyRegistry] = None,
    ):
        """
        初始化服务

        Args:
            taxonomy_registry: Taxonomy 注册表（可选，默认使用全局单例）
        """
        self._taxonomy = taxonomy_registry or get_taxonomy_registry()

    def compute_semantic_similarity(
        self,
        question: str,
        profile: WorkerProfile,
        use_semantic_expansion: Optional[bool] = None,
    ) -> tuple[float, dict]:
        """
        计算语义相似度

        Args:
            question: 问题文本
            profile: Worker Profile
            use_semantic_expansion: 是否使用语义扩展（默认从 FeatureFlags 读取）

        Returns:
            tuple[float, dict]: (分数 0-1, 详细信息字典)
        """
        # 从 FeatureFlags 读取默认值
        if use_semantic_expansion is None:
            use_semantic_expansion = FeatureFlags.is_g1_semantic_match_enabled()

        if use_semantic_expansion:
            return self._compute_with_expansion(question, profile)
        else:
            return self._compute_basic(question, profile)

    def _compute_with_expansion(
        self,
        question: str,
        profile: WorkerProfile,
    ) -> tuple[float, dict]:
        """
        使用 taxonomy 扩展进行语义匹配

        实现步骤：
        1. 从 taxonomy 提取问题相关的关键词和场景
        2. 扩展问题语义词汇集
        3. 与 profile 的 searchable_text、capabilities 进行匹配

        Args:
            question: 问题文本
            profile: Worker Profile

        Returns:
            tuple[float, dict]: (分数 0-1, 详细信息字典)
        """
        # 1. 提取问题原始词
        question_words = self._tokenize(question)

        # 2. 从 taxonomy 扩展语义词汇
        expanded_terms = self._expand_with_taxonomy(question, question_words)

        # 3. 获取 profile 可匹配内容
        profile_content = self._get_profile_matchable_content(profile)

        # 4. 计算匹配分数
        score, details = self._compute_match_score(
            expanded_terms,
            profile_content,
            question_words,
        )

        details["expansion_used"] = True
        details["expanded_term_count"] = len(expanded_terms)
        details["original_word_count"] = len(question_words)

        return score, details

    def _compute_basic(
        self,
        question: str,
        profile: WorkerProfile,
    ) -> tuple[float, dict]:
        """
        基础关键词匹配

        不使用 taxonomy 扩展，仅做纯关键词匹配。

        Args:
            question: 问题文本
            profile: Worker Profile

        Returns:
            tuple[float, dict]: (分数 0-1, 详细信息字典)
        """
        question_words = self._tokenize(question)
        profile_content = self._get_profile_matchable_content(profile)

        score, details = self._compute_match_score(
            question_words,
            profile_content,
            question_words,
        )

        details["expansion_used"] = False

        return score, details

    def _expand_with_taxonomy(
        self,
        question: str,
        base_words: set[str],
    ) -> set[str]:
        """
        使用 taxonomy 扩展语义词汇

        从以下方面扩展：
        1. 匹配的领域关键词
        2. 匹配的场景关键词
        3. 风险信号关键词（如果存在）

        Args:
            question: 问题文本
            base_words: 基础词集合

        Returns:
            set[str]: 扩展后的词集合
        """
        expanded = set(base_words)
        question_lower = question.lower()

        # 1. 扩展领域关键词
        config = self._taxonomy.get_config()
        for domain_id, domain in config.domains.technical_domains.items():
            for kw in domain.keywords:
                if kw.lower() in question_lower:
                    expanded.update(k.lower() for k in domain.keywords)
                    expanded.add(domain.name.lower())
                    break

        for domain_id, domain in config.domains.business_domains.items():
            for kw in domain.keywords:
                if kw.lower() in question_lower:
                    expanded.update(k.lower() for k in domain.keywords)
                    expanded.add(domain.name.lower())
                    break

        # 2. 扩展场景关键词
        for scenario_id, scenario in config.scenarios.business_scenarios.items():
            for kw in scenario.keywords:
                if kw.lower() in question_lower:
                    expanded.update(k.lower() for k in scenario.keywords)
                    expanded.add(scenario.name.lower())
                    break

        # 3. 扩展风险信号关键词
        risk_signals = [
            (self._taxonomy.get_critical_keywords(), "critical"),
            (self._taxonomy.get_high_keywords(), "high"),
            (self._taxonomy.get_medium_keywords(), "medium"),
        ]

        for keywords, level in risk_signals:
            for kw in keywords:
                if kw.lower() in question_lower:
                    # 添加同级别关键词作为扩展
                    expanded.update(k.lower() for k in keywords[:10])
                    break

        return expanded

    def _get_profile_matchable_content(self, profile: WorkerProfile) -> str:
        """
        获取 Profile 可匹配的内容文本

        包括：
        1. searchable_text
        2. active_skills 名称和描述
        3. context_fragments 内容（部分）

        Args:
            profile: Worker Profile

        Returns:
            str: 可匹配的内容文本
        """
        parts = []

        # 1. searchable_text
        if profile.searchable_text:
            parts.append(profile.searchable_text)

        # 2. active_skills
        for skill in profile.active_skills:
            parts.append(skill.name)
            if skill.description:
                parts.append(skill.description)

        # 3. context_fragments（前 200 字符）
        for fragment in profile.context_fragments[:3]:
            if fragment.content:
                parts.append(fragment.content[:200])

        return " ".join(parts).lower()

    def _compute_match_score(
        self,
        query_terms: set[str],
        profile_content: str,
        original_words: set[str],
    ) -> tuple[float, dict]:
        """
        计算匹配分数

        策略：
        1. 计算原始词匹配率（权重 0.6）
        2. 计算扩展词匹配率（权重 0.4）
        3. 综合得分

        Args:
            query_terms: 查询词集合（可能已扩展）
            profile_content: Profile 内容（已小写）
            original_words: 原始问题词集合

        Returns:
            tuple[float, dict]: (分数 0-1, 详细信息字典)
        """
        # 1. 原始词匹配
        original_matches = sum(
            1 for word in original_words
            if word.lower() in profile_content
        )
        original_score = original_matches / len(original_words) if original_words else 0.0

        # 2. 扩展词匹配
        expanded_matches = sum(
            1 for term in query_terms
            if term.lower() in profile_content
        )
        expanded_score = expanded_matches / len(query_terms) if query_terms else 0.0

        # 3. 综合得分
        # 原始词权重 0.6，扩展词权重 0.4
        final_score = original_score * 0.6 + expanded_score * 0.4

        details = {
            "original_match_count": original_matches,
            "original_total": len(original_words),
            "original_score": round(original_score, 3),
            "expanded_match_count": expanded_matches,
            "expanded_total": len(query_terms),
            "expanded_score": round(expanded_score, 3),
        }

        return min(1.0, final_score), details

    def _tokenize(self, text: str) -> set[str]:
        """
        分词

        简单实现，支持中英文分词。

        Args:
            text: 输入文本

        Returns:
            set[str]: 词集合
        """
        words = _WORD_PATTERN.findall(text.lower())
        return set(words) if words else set()


__all__ = ["SemanticMatchService"]