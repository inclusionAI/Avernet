"""
Content Embedding 配置

控制 contents 字段中哪些类型参与向量化。
"""

from __future__ import annotations

import os
from typing import Any, ClassVar


class ContentEmbeddingConfig:
    """
    Contents 向量化配置

    控制 WorkerProfileContent.contents 中哪些字段会被向量化。

    默认向量化字段：
    - memory: 记忆内容
    - profile: 画像描述
    - capabilities: 能力描述
    - skill_sets: 技能集

    可通过环境变量 CONTENT_EMBEDDING_FIELDS 覆盖，逗号分隔。
    """

    # 默认可向量化的 contents 字段
    DEFAULT_EMBEDDING_FIELDS: ClassVar[set[str]] = {
        # "memory",  # 已禁用：记忆内容不参与向量化
        "profile",
        "capabilities",
        "skill_sets",
        "ecb_summary",
    }

    @classmethod
    def get_embedding_fields(cls) -> set[str]:
        """获取当前配置的向量化字段集合"""
        env_value = os.getenv("CONTENT_EMBEDDING_FIELDS")
        if env_value:
            # 环境变量覆盖，逗号分隔
            return {f.strip().lower() for f in env_value.split(",") if f.strip()}
        return cls.DEFAULT_EMBEDDING_FIELDS.copy()

    @classmethod
    def is_embedding_enabled(cls, field_name: str) -> bool:
        """
        检查指定字段是否启用向量化

        Args:
            field_name: contents 中的字段名

        Returns:
            是否启用向量化
        """
        return field_name.lower() in cls.get_embedding_fields()

    @classmethod
    def filter_contents(cls, contents: dict[str, Any]) -> dict[str, Any]:
        """
        过滤 contents，只保留需要向量化的字段

        Args:
            contents: 原始 contents dict

        Returns:
            过滤后的 contents dict
        """
        enabled_fields = cls.get_embedding_fields()
        return {
            k: v for k, v in contents.items()
            if k.lower().split("/")[-1].split(".")[0] in enabled_fields
        }


__all__ = ["ContentEmbeddingConfig"]
