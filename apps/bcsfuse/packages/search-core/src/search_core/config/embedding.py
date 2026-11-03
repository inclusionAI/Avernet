"""
EmbeddingSettings

Embedding 配置加载。

配置从环境变量加载，使用 EMBEDDING_ 前缀。
"""

from __future__ import annotations

import os
from typing import Optional

from pydantic import BaseModel, Field, field_validator, model_validator


_QWEN_MODEL_DIMENSIONS: dict[str, set[int]] = {
    "Qwen3-Embedding-0.6B": {1024},
    "Qwen3-Embedding-4B": {1024, 2048, 2560},
    "Qwen3-Embedding-8B": {1024, 2048, 4096},
}

_QWEN_NATIVE_DIMENSIONS: dict[str, int] = {
    "Qwen3-Embedding-0.6B": 1024,
    "Qwen3-Embedding-4B": 2560,
    "Qwen3-Embedding-8B": 4096,
}


class EmbeddingSettings(BaseModel):
    """
    Embedding 配置

    从环境变量加载配置。

    环境变量：
        EMBEDDING_BASE_URL: Embedding API 基础 URL
        EMBEDDING_AUTH_TOKEN: 认证 token
        EMBEDDING_MODEL: 模型名称
        EMBEDDING_DIMENSION: 向量维度（默认 4096）
        EMBEDDING_TIMEOUT_MS: 请求超时（毫秒）
        EMBEDDING_REQUEST_DIMENSIONS: 是否显式请求 dimensions

    Attributes:
        base_url: API 基础 URL
        auth_token: 认证 token
        model: 模型名称
        dimension: 向量维度
        timeout_ms: 超时时间
    """

    # 基础配置
    base_url: Optional[str] = Field(
        default=None,
        description="Embedding API 基础 URL",
    )

    auth_token: Optional[str] = Field(
        default=None,
        description="认证 token",
    )

    model: Optional[str] = Field(
        default=None,
        description="Embedding 模型名称",
    )

    # 默认参数
    dimension: int = Field(
        default=4096,
        ge=64,
        le=8192,
        description="向量维度",
    )

    request_dimensions: bool = Field(
        default=False,
        description="是否在 OpenAI-compatible 请求中发送 dimensions",
    )

    timeout_ms: int = Field(
        default=30000,
        ge=1000,
        le=120000,
        description="请求超时（毫秒）",
    )

    @field_validator("base_url", "auth_token", "model", mode="after")
    @classmethod
    def normalize_optional_text(cls, value: Optional[str]) -> Optional[str]:
        """Treat external text input as configuration, not wire header bytes."""

        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @model_validator(mode="after")
    def validate_model_dimension_contract(self) -> "EmbeddingSettings":
        """Reject known model dimensions that cannot be requested safely.

        The allowlist is intentionally limited to the production models whose
        dimensions have already been verified against the actual model service.
        Unknown models remain compatible with the generic OpenAI wire contract.
        """

        model_name = (self.model or "").strip()
        if model_name not in _QWEN_MODEL_DIMENSIONS:
            return self

        allowed_dimensions = _QWEN_MODEL_DIMENSIONS[model_name]
        native_dimension = _QWEN_NATIVE_DIMENSIONS[model_name]
        if self.dimension not in allowed_dimensions:
            raise ValueError(
                f"Embedding model {model_name} does not support "
                f"dimension {self.dimension}"
            )
        if not self.request_dimensions and self.dimension != native_dimension:
            raise ValueError(
                "Embedding request_dimensions must be true when a non-native "
                f"dimension is selected for {model_name}"
            )
        return self

    def __init__(self, **data):
        """
        初始化配置

        优先从环境变量加载。
        """
        # 从环境变量加载
        env_data = self._load_from_env()

        # 合并参数（参数优先于环境变量）
        merged = {**env_data, **data}

        super().__init__(**merged)

    @staticmethod
    def _load_from_env() -> dict:
        """从环境变量加载配置"""
        data = {}

        # 基础配置
        if os.environ.get("EMBEDDING_BASE_URL"):
            data["base_url"] = os.environ["EMBEDDING_BASE_URL"].strip()
        if os.environ.get("EMBEDDING_AUTH_TOKEN"):
            data["auth_token"] = os.environ["EMBEDDING_AUTH_TOKEN"].strip()
        if os.environ.get("EMBEDDING_MODEL"):
            data["model"] = os.environ["EMBEDDING_MODEL"].strip()

        # 默认参数
        if os.environ.get("EMBEDDING_DIMENSION"):
            data["dimension"] = int(os.environ["EMBEDDING_DIMENSION"])
        if os.environ.get("EMBEDDING_TIMEOUT_MS"):
            data["timeout_ms"] = int(os.environ["EMBEDDING_TIMEOUT_MS"])
        if os.environ.get("EMBEDDING_REQUEST_DIMENSIONS"):
            data["request_dimensions"] = os.environ[
                "EMBEDDING_REQUEST_DIMENSIONS"
            ].strip().lower() in ("1", "true", "yes", "on")

        return data

    def is_configured(self) -> bool:
        """
        检查是否配置齐全

        Returns:
            bool: 配置是否齐全
        """
        return all(
            [
                self.base_url,
                self.auth_token,
                self.model,
            ]
        )

    def missing_config(self) -> list[str]:
        """
        获取缺失的配置项

        Returns:
            list[str]: 缺失的配置项名称
        """
        missing = []
        if not self.base_url:
            missing.append("EMBEDDING_BASE_URL")
        if not self.auth_token:
            missing.append("EMBEDDING_AUTH_TOKEN")
        if not self.model:
            missing.append("EMBEDDING_MODEL")
        return missing


__all__ = [
    "EmbeddingSettings",
]
