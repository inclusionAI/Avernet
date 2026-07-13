"""
LLMSettings

LLM Gateway / Provider Layer

LLM 配置加载，包含逻辑模型位注册表。

配置从环境变量加载，使用中性变量名，不绑定具体厂商。
"""

from __future__ import annotations

import os
from typing import Optional

from pydantic import BaseModel, Field

from src.domain.models.model_profile import (
    ModelProfile,
    ModelTier,
    CostClass,
    LatencyClass,
)
from src.domain.models.llm_task_spec import TaskType


class LLMSettings(BaseModel):
    """
    LLM 配置

    从环境变量加载配置，并持有逻辑模型位注册表。

    环境变量（中性命名，不绑定厂商）：
        LLM_BASE_URL: API 基础 URL
        LLM_AUTH_TOKEN: 认证 token（运行时注入，不要硬编码）
        LLM_FAST_MODEL: 快速模型名称
        LLM_BALANCED_MODEL: 均衡模型名称
        LLM_REASONING_MODEL: 推理模型名称
        LLM_LONG_CONTEXT_MODEL: 长上下文模型名称
        LLM_EXTRACTION_MODEL: 提取模型名称
        LLM_DEFAULT_TIMEOUT_MS: 默认超时（毫秒）
        LLM_DEFAULT_MAX_TOKENS: 默认最大 token
        LLM_DEFAULT_TEMPERATURE: 默认温度
        LLM_REASONING_TIMEOUT_MS: 推理任务超时
        LLM_SUMMARY_TIMEOUT_MS: 摘要任务超时
        LLM_EXTRACTION_TIMEOUT_MS: 提取任务超时
        LLM_ENABLE_FALLBACK: 是否启用 fallback
        LLM_ENABLE_RETRY: 是否启用重试
        LLM_MAX_RETRIES: 最大重试次数

    Attributes:
        base_url: API 基础 URL
        auth_token: 认证 token
        default_timeout_ms: 默认超时
        default_max_tokens: 默认最大 token
        default_temperature: 默认温度
        enable_fallback: 是否启用 fallback
        enable_retry: 是否启用重试
        max_retries: 最大重试次数
        model_registry: 模型注册表
    """

    # 基础配置
    base_url: Optional[str] = Field(
        default=None,
        description="API 基础 URL",
    )

    auth_token: Optional[str] = Field(
        default=None,
        description="认证 token",
    )

    # 默认参数
    default_timeout_ms: int = Field(
        default=15000,
        ge=1000,
        le=600000,  # 最大 10 分钟，支持长时间推理任务
        description="默认超时（毫秒）",
    )

    default_max_tokens: int = Field(
        default=4096,
        ge=1,
        le=128000,
        description="默认最大 token",
    )

    default_temperature: float = Field(
        default=0.2,
        ge=0.0,
        le=2.0,
        description="默认温度",
    )

    # 任务特定超时
    reasoning_timeout_ms: Optional[int] = Field(
        default=None,
        description="推理任务超时",
    )

    summary_timeout_ms: Optional[int] = Field(
        default=None,
        description="摘要任务超时",
    )

    extraction_timeout_ms: Optional[int] = Field(
        default=None,
        description="提取任务超时",
    )

    # 重试配置
    enable_fallback: bool = Field(
        default=True,
        description="是否启用 fallback",
    )

    enable_retry: bool = Field(
        default=True,
        description="是否启用重试",
    )

    max_retries: int = Field(
        default=1,
        ge=0,
        le=3,
        description="最大重试次数",
    )

    # 并发配置
    parallel_max_workers: int = Field(
        default=5,
        ge=1,
        le=20,
        description="LLM并行调用最大并发数（G2立场提取、G5专家视角生成）",
    )

    # 模型注册表
    model_registry: list[ModelProfile] = Field(
        default_factory=list,
        description="模型注册表",
    )

    def __init__(self, **data):
        """
        初始化配置

        优先从环境变量加载，再构建模型注册表。
        """
        # 从环境变量加载
        env_data = self._load_from_env()

        # 合并参数（参数优先于环境变量）
        merged = {**env_data, **data}

        # 构建模型注册表
        if "model_registry" not in merged:
            merged["model_registry"] = self._build_model_registry(merged)

        super().__init__(**merged)

    @staticmethod
    def _load_from_env() -> dict:
        """从环境变量加载配置"""
        data = {}

        # 基础配置（清理空白字符）
        if os.environ.get("LLM_BASE_URL"):
            data["base_url"] = os.environ["LLM_BASE_URL"].strip()
        if os.environ.get("LLM_AUTH_TOKEN"):
            data["auth_token"] = os.environ["LLM_AUTH_TOKEN"].strip()

        # 默认参数
        if os.environ.get("LLM_DEFAULT_TIMEOUT_MS"):
            data["default_timeout_ms"] = int(os.environ["LLM_DEFAULT_TIMEOUT_MS"])
        if os.environ.get("LLM_DEFAULT_MAX_TOKENS"):
            data["default_max_tokens"] = int(os.environ["LLM_DEFAULT_MAX_TOKENS"])
        if os.environ.get("LLM_DEFAULT_TEMPERATURE"):
            data["default_temperature"] = float(os.environ["LLM_DEFAULT_TEMPERATURE"])

        # 任务特定超时
        if os.environ.get("LLM_REASONING_TIMEOUT_MS"):
            data["reasoning_timeout_ms"] = int(os.environ["LLM_REASONING_TIMEOUT_MS"])
        if os.environ.get("LLM_SUMMARY_TIMEOUT_MS"):
            data["summary_timeout_ms"] = int(os.environ["LLM_SUMMARY_TIMEOUT_MS"])
        if os.environ.get("LLM_EXTRACTION_TIMEOUT_MS"):
            data["extraction_timeout_ms"] = int(os.environ["LLM_EXTRACTION_TIMEOUT_MS"])

        # 重试配置
        if os.environ.get("LLM_ENABLE_FALLBACK"):
            data["enable_fallback"] = os.environ["LLM_ENABLE_FALLBACK"].lower() == "true"
        if os.environ.get("LLM_ENABLE_RETRY"):
            data["enable_retry"] = os.environ["LLM_ENABLE_RETRY"].lower() == "true"
        if os.environ.get("LLM_MAX_RETRIES"):
            data["max_retries"] = int(os.environ["LLM_MAX_RETRIES"])

        # 并发配置
        if os.environ.get("LLM_PARALLEL_MAX_WORKERS"):
            data["parallel_max_workers"] = int(os.environ["LLM_PARALLEL_MAX_WORKERS"])

        # 模型名称（用于构建注册表，清理空白字符）
        if os.environ.get("LLM_FAST_MODEL"):
            data["fast_model"] = os.environ["LLM_FAST_MODEL"].strip()
        if os.environ.get("LLM_BALANCED_MODEL"):
            data["balanced_model"] = os.environ["LLM_BALANCED_MODEL"].strip()
        if os.environ.get("LLM_REASONING_MODEL"):
            data["reasoning_model"] = os.environ["LLM_REASONING_MODEL"].strip()
        if os.environ.get("LLM_LONG_CONTEXT_MODEL"):
            data["long_context_model"] = os.environ["LLM_LONG_CONTEXT_MODEL"].strip()
        if os.environ.get("LLM_EXTRACTION_MODEL"):
            data["extraction_model"] = os.environ["LLM_EXTRACTION_MODEL"].strip()

        return data

    @staticmethod
    def _build_model_registry(config: dict) -> list[ModelProfile]:
        """构建模型注册表"""
        # 默认模型名称
        fast_model = config.get("fast_model", "default-fast")
        balanced_model = config.get("balanced_model", "default-balanced")
        reasoning_model = config.get("reasoning_model", "default-reasoning")
        long_context_model = config.get("long_context_model", "default-long-context")
        extraction_model = config.get("extraction_model", "default-extraction")

        return [
            # Fast tier
            ModelProfile(
                logical_model_id="fast.default",
                provider_id="default",
                physical_model_name=fast_model,
                tier=ModelTier.FAST,
                supports_json=True,
                supports_long_context=False,
                cost_class=CostClass.LOW,
                latency_class=LatencyClass.LOW,
                recommended_for=[TaskType.SUMMARY],
                description="快速响应模型，适合简单任务",
            ),
            # Balanced tier
            ModelProfile(
                logical_model_id="balanced.default",
                provider_id="default",
                physical_model_name=balanced_model,
                tier=ModelTier.BALANCED,
                supports_json=True,
                supports_long_context=False,
                cost_class=CostClass.MEDIUM,
                latency_class=LatencyClass.MEDIUM,
                recommended_for=[TaskType.TASK_UNDERSTANDING, TaskType.PLANNING],
                description="均衡模型，适合大多数任务",
            ),
            # Reasoning tier
            ModelProfile(
                logical_model_id="reasoning.default",
                provider_id="default",
                physical_model_name=reasoning_model,
                tier=ModelTier.REASONING,
                supports_json=True,
                supports_long_context=False,
                cost_class=CostClass.HIGH,
                latency_class=LatencyClass.HIGH,
                recommended_for=[TaskType.FUSION_RECOMMENDATION, TaskType.PLANNING, TaskType.RATIONALE_GENERATION],
                description="推理模型，适合复杂分析和决策",
            ),
            # Long context tier
            ModelProfile(
                logical_model_id="long_context.default",
                provider_id="default",
                physical_model_name=long_context_model,
                tier=ModelTier.LONG_CONTEXT,
                supports_json=True,
                supports_long_context=True,
                cost_class=CostClass.HIGH,
                latency_class=LatencyClass.HIGH,
                recommended_for=[],
                max_context_tokens=128000,
                description="长上下文模型，适合大文档处理",
            ),
            # Extraction tier
            ModelProfile(
                logical_model_id="extraction.default",
                provider_id="default",
                physical_model_name=extraction_model,
                tier=ModelTier.EXTRACTION,
                supports_json=True,
                supports_long_context=False,
                cost_class=CostClass.MEDIUM,
                latency_class=LatencyClass.LOW,
                recommended_for=[TaskType.EXTRACTION],
                description="提取模型，适合结构化信息抽取",
            ),
        ]

    def get_model_profile(self, logical_model_id: str) -> Optional[ModelProfile]:
        """
        获取模型档案

        Args:
            logical_model_id: 逻辑模型 ID

        Returns:
            ModelProfile 或 None
        """
        for profile in self.model_registry:
            if profile.logical_model_id == logical_model_id:
                return profile
        return None

    def get_timeout_for_task(self, task_type: TaskType) -> int:
        """
        获取任务类型的超时配置

        Args:
            task_type: 任务类型

        Returns:
            超时时间（毫秒）
        """
        # 推理类任务
        if task_type in (TaskType.FUSION_RECOMMENDATION, TaskType.PLANNING, TaskType.RATIONALE_GENERATION):
            return self.reasoning_timeout_ms or self.default_timeout_ms

        # 摘要任务
        if task_type == TaskType.SUMMARY:
            return self.summary_timeout_ms or self.default_timeout_ms

        # 提取任务
        if task_type == TaskType.EXTRACTION:
            return self.extraction_timeout_ms or self.default_timeout_ms

        # 默认
        return self.default_timeout_ms


__all__ = [
    "LLMSettings",
]