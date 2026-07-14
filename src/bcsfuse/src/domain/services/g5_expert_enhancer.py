"""
G5ExpertEnhancer Interface

Stage 3: Worker Profile-Driven Expert Execution Preparation

G5 专家视角增强接口定义。

职责:
- 定义如何增强 G5 模式的专家视角
- 使用 Worker Profile 检索 + LLM 生成增强视角
- 供 ExpertDiagnosisService 可选注入

约束:
- 仅用于 G5 模式
- 不改变 G1/G2 的行为
- 支持可选依赖注入
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from src.domain.models.fusion_result import Perspective


@runtime_checkable
class G5ExpertEnhancer(Protocol):
    """
    G5 Expert Enhancer 接口

    定义如何增强 G5 Expert Diagnosis 模式的专家视角。

    实现者应：
    1. 使用 WorkerProfileRetrievalService 检索相关专家
    2. 使用 WorkerContextPreparationService 准备上下文
    3. 使用 LLMGatewayService 生成增强视角
    4. 合并或替换 base perspectives

    使用 Protocol 允许 duck typing。
    实现者可以是真实的 LLM 调用、fake stub 或 mock。
    """

    def enhance(
        self,
        question: str,
        base_perspectives: list[Perspective],
        participants: list[str] | None = None,
        driver_bot_id: str | None = None,
    ) -> list[Perspective]:
        """
        增强 G5 专家视角

        Args:
            question: 待诊断的问题
            base_perspectives: 基础视角列表（原有 provider 收集的）
            participants: 参与者列表（用于 candidate recommendation）
            driver_bot_id: Driver bot ID

        Returns:
            list[Perspective]: 增强后的视角列表

        Note:
            - 实现者可以根据 participants 检索相关专家 profile
            - 实现者可以使用 LLM 生成增强视角
            - 如果增强失败，应返回原 base_perspectives（fallback）
            - 不应抛出异常，错误应在 Perspective 状态中表达
        """
        ...


__all__ = [
    "G5ExpertEnhancer",
]