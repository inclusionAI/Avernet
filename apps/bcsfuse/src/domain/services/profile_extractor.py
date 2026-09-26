"""
Profile Extractor Interface

M2: Worker Profiling & Extraction

定义 Profiling 抽取器的接口。

职责：
- 定义从输入文档抽取 Worker 画像的抽象接口
- 不依赖具体解析实现
- 供 application 层调用，由 infra 层实现
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from src.domain.models.profiling_input import ProfilingInput
from src.domain.models.profiling_result import WorkerProfileExtractionResult


@runtime_checkable
class ProfileExtractor(Protocol):
    """
    Profile Extractor 接口

    定义从输入文档抽取 Worker 画像的基本操作。

    使用 Protocol 而非 ABC，允许 duck typing，
    但仍能通过 isinstance 检查。
    """

    def extract(self, input_data: ProfilingInput) -> WorkerProfileExtractionResult:
        """
        从输入数据抽取 Worker 画像

        Args:
            input_data: Profiling 输入数据，包含文档、技能、资源等

        Returns:
            WorkerProfileExtractionResult: 抽取结果，包含：
                - capabilities: 抽取的能力
                - domains: 抽取的领域
                - responsibilities: 抽取的职责
                - constraints: 抽取的约束
                - escalation_triggers: 抽取的上报触发点
                - collaboration_style: 抽取的协作风格
                - skills: 抽取的技能
                - resources: 抽取的资源
                - memory_episodes: 抽取的记忆片段
                - warnings: 警告列表
                - errors: 错误列表

        注意：
            - 抽取结果可能不完整（存在 errors）
            - 抽取结果应支持部分成功（存在 warnings 但无 errors）
            - 每个抽取结果都应包含来源引用 (source_ref)
        """
        ...


__all__ = ["ProfileExtractor"]