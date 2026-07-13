"""
Profile Fragment Decomposer

将 WorkerProfile 分解为语义独立的 Fragments。
"""

from __future__ import annotations

import logging
from typing import ClassVar

from src.domain.models.profile_fragment import ProfileFragment
from src.domain.models.worker_profile import WorkerProfile
from src.infra.config.content_embedding_config import ContentEmbeddingConfig

logger = logging.getLogger(__name__)


class ProfileFragmentDecomposer:
    """
    Profile 语义分解器

    职责：
    1. 将 WorkerProfile 分解为语义独立的 Fragments
    2. 只处理启用了向量化的 contents 字段（默认: memory, profile, capabilities, skill_sets）
    3. 为每个 Fragment 分配权重
    4. 生成 FULL fragment 作为兜底

    权重控制：
    - 权重 > 0：正常参与检索和聚合
    - 权重 = 0：检索时跳过，但索引仍生成
    """

    # Contents 字段向量化配置（可通过 CONTENT_EMBEDDING_FIELDS 环境变量调整）
    # 默认向量化字段: memory, profile, capabilities, skill_sets
    DEFAULT_TYPE_WEIGHTS: ClassVar[dict[str, float]] = {
        # Contents 核心字段权重
        # "memory": 0.0,        # 已禁用：记忆内容不参与向量化（权重设为0表示跳过）
        "profile": 0.65,       # 画像描述（高权重）
        "capabilities": 0.1,    # 能力标签（高权重）
        "skill_sets": 0.15,    # 技能集（高权重）
        "ecb_summary": 0.01,   # ECB 总结内容（高权重）
        # 兜底
        "full": 0.1,           # 全量内容兜底
    }

    def __init__(
        self,
        type_weights: dict[str, float] | None = None,
    ):
        """
        初始化分解器

        Args:
            type_weights: 可选的权重覆盖配置
        """
        self._type_weights = dict(self.DEFAULT_TYPE_WEIGHTS)
        if type_weights:
            self._type_weights.update(type_weights)

    @classmethod
    def get_active_types(cls) -> list[str]:
        """
        获取启用的 fragment 类型（权重 > 0）

        Returns:
            启用的类型列表
        """
        return [
            t for t, w in cls.DEFAULT_TYPE_WEIGHTS.items()
            if w > 0
        ]

    @classmethod
    def is_enabled(cls, fragment_type: str) -> bool:
        """
        判断类型是否启用（权重 > 0）

        Args:
            fragment_type: 片段类型

        Returns:
            是否启用
        """
        return cls.DEFAULT_TYPE_WEIGHTS.get(fragment_type, 0) > 0

    def get_weight(self, fragment_type: str) -> float:
        """
        获取指定类型的权重

        Args:
            fragment_type: 片段类型

        Returns:
            权重值（未配置返回 0）
        """
        return self._type_weights.get(fragment_type, 0.0)

    def decompose(self, profile: WorkerProfile) -> list[ProfileFragment]:
        """
        将 Profile 分解为 Fragments

        简化策略：
        1. 只处理启用了向量化的 contents 字段（默认: memory, profile, capabilities, skill_sets）
        2. FULL → 总是生成（作为兜底/兼容）

        Args:
            profile: WorkerProfile

        Returns:
            ProfileFragment 列表
        """
        fragments = []
        enabled_fields = ContentEmbeddingConfig.get_embedding_fields()

        # Step 1: 处理来自 contents 的特定字段
        for cf in profile.context_fragments:
            # 从 metadata 获取字段名
            field_name = cf.metadata.get("embedding_field")
            if not field_name or field_name not in enabled_fields:
                continue

            content = cf.content.strip() if cf.content else ""
            if not content:
                continue

            fragments.append(ProfileFragment(
                fragment_type=field_name,
                content=content,
                weight=self._type_weights.get(field_name, 0.1),
                description=f"Contents: {cf.filename}",
            ))

        # Step 2: FULL fragment（总是生成，作为兜底）
        full_fragment = self._extract_full_fragment(profile)
        if full_fragment:
            fragments.append(full_fragment)

        logger.debug(
            "[ProfileFragmentDecomposer] Generated %d fragments for %s: %s",
            len(fragments),
            profile.profile_key,
            [f.fragment_type for f in fragments]
        )

        return fragments

    def _extract_full_fragment(self, profile: WorkerProfile) -> ProfileFragment | None:
        """
        提取 FULL fragment（全量内容兜底）

        按权重倒序拼接 contents 字段内容，权重高的字段内容在前。

        Args:
            profile: WorkerProfile

        Returns:
            ProfileFragment 或 None
        """
        enabled_fields = ContentEmbeddingConfig.get_embedding_fields()

        # 1. 收集所有启用的 contents 片段，按字段分组
        field_contents: dict[str, list[str]] = {field: [] for field in enabled_fields}
        for cf in profile.context_fragments:
            field_name = cf.metadata.get("embedding_field")
            if not field_name or field_name not in enabled_fields:
                continue
            content = cf.content.strip() if cf.content else ""
            if content:
                field_contents[field_name].append(content)

        # 2. 按权重倒序排序字段（权重高的在前）
        sorted_fields = sorted(
            enabled_fields,
            key=lambda f: self._type_weights.get(f, 0.0),
            reverse=True
        )

        # 3. 按权重顺序拼接内容，每个字段最多1000字符
        text_parts = []
        for field_name in sorted_fields:
            contents = field_contents.get(field_name, [])
            if not contents:
                continue

            # 拼接该字段的所有内容，并截断到1000字符
            field_text = " ".join(contents)
            field_text = field_text[:1000]  # 每个字段最多1000字符
            text_parts.append(field_text)

        # 4. 技能信息（按权重顺序放在最后，技能权重为0.1）
        if profile.active_skills:
            skill_names = [s.name for s in profile.active_skills[:10]]
            # 只有在技能有内容时才添加
            if skill_names:
                text_parts.append("Skills: " + ", ".join(skill_names))

        # 如果没有内容，生成一个基础的 profile 标识
        if not text_parts:
            return ProfileFragment(
                fragment_type="full",
                content=f"Profile: {profile.profile_key}",
                weight=self._type_weights.get("full", 0.1),
                description="Empty profile fallback"
            )

        # 5. 拼接并截断到5000字符
        full_text = " ".join(text_parts)
        max_length = 5000
        if len(full_text) > max_length:
            full_text = full_text[:max_length]

        return ProfileFragment(
            fragment_type="full",
            content=full_text,
            weight=self._type_weights.get("full", 0.1),
            description="Full contents aggregation"
        )


__all__ = ["ProfileFragmentDecomposer"]
