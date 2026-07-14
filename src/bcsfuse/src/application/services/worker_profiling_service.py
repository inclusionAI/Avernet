"""
Worker Profiling Service

M2: Worker Profiling & Extraction

最小可用的 Worker 画像服务。

Service 职责：
- 接收 profiling 输入
- 调用 ProfileExtractor
- 汇总抽取结果
- 聚合 warnings / errors
- 保留 source references
- 支持多来源输入的最小合并逻辑
- 输出统一的 profiling result

Service 不做：
- 不解析 markdown（由 extractor 做）
- 不实现抽取规则（由 extractor 做）
- 不写回 Worker Registry
- 不更新 Worker 主数据
- 不实现复杂冲突仲裁
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from src.domain.models.profiling_input import ProfilingInput
from src.domain.models.profiling_result import (
    WorkerProfileExtractionResult,
    ExtractionError,
    ExtractedSkill,
)
from src.domain.services.profile_extractor import ProfileExtractor


class WorkerProfilingService:
    """
    Worker 画像服务

    最小可用的服务实现，负责协调 extractor 并汇总结果。
    """

    def __init__(self, extractor: ProfileExtractor):
        """
        初始化服务

        Args:
            extractor: ProfileExtractor 实现，用于执行实际的抽取逻辑
        """
        self._extractor = extractor

    def profile(self, input_data: ProfilingInput) -> WorkerProfileExtractionResult:
        """
        执行 Worker 画像抽取

        Args:
            input_data: Profiling 输入数据，包含文档和元数据

        Returns:
            WorkerProfileExtractionResult: 抽取结果
        """
        try:
            # 调用 extractor 执行抽取
            result = self._extractor.extract(input_data)

            # 应用最小去重逻辑
            result = self._apply_minimal_deduplication(result)

            # 确保 worker_id 正确
            result.worker_id = input_data.worker_id

            return result

        except Exception as e:
            # 捕获 extractor 异常，返回带有 error 的结果
            return self._create_error_result(input_data.worker_id, str(e), input_data)

    def _apply_minimal_deduplication(
        self, result: WorkerProfileExtractionResult
    ) -> WorkerProfileExtractionResult:
        """
        应用最小去重逻辑

        规则：
        - 同类抽取结果按 name 去重（保留第一个出现的）
        - 不做复杂的智能裁决
        - 去重产生 warning

        Args:
            result: 原始抽取结果

        Returns:
            去重后的抽取结果
        """
        # 对 skills 按 name 去重
        result.skills = self._deduplicate_by_name(
            result.skills,
            "skill",
            result.warnings
        )

        # 对 capabilities 按 name 去重
        result.capabilities = self._deduplicate_by_name(
            result.capabilities,
            "capability",
            result.warnings
        )

        # 对 domains 按 name 去重
        result.domains = self._deduplicate_by_name(
            result.domains,
            "domain",
            result.warnings
        )

        # 对 resources 按 id 去重
        result.resources = self._deduplicate_by_id(
            result.resources,
            result.warnings
        )

        return result

    def _deduplicate_by_name(
        self,
        items: list,
        item_type: str,
        warnings: list
    ) -> list:
        """
        按 name 字段去重

        Args:
            items: 要去重的项列表
            item_type: 项类型（用于警告消息）
            warnings: 警告列表（会添加去重警告）

        Returns:
            去重后的列表
        """
        if not items:
            return items

        seen_names: set[str] = set()
        deduplicated: list = []

        for item in items:
            name = getattr(item, "name", None)
            if name is None:
                # 没有 name 字段的项保留
                deduplicated.append(item)
                continue

            name_lower = name.lower()
            if name_lower not in seen_names:
                seen_names.add(name_lower)
                deduplicated.append(item)
            # 不添加警告，静默去重以减少噪音

        return deduplicated

    def _deduplicate_by_id(
        self,
        items: list,
        warnings: list
    ) -> list:
        """
        按 id 字段去重

        Args:
            items: 要去重的项列表
            warnings: 警告列表

        Returns:
            去重后的列表
        """
        if not items:
            return items

        seen_ids: set[str] = set()
        deduplicated: list = []

        for item in items:
            item_id = getattr(item, "id", None)
            if item_id is None:
                deduplicated.append(item)
                continue

            if item_id not in seen_ids:
                seen_ids.add(item_id)
                deduplicated.append(item)

        return deduplicated

    def _create_error_result(
        self, worker_id: str, error_message: str, input_data: ProfilingInput | None = None
    ) -> WorkerProfileExtractionResult:
        """
        创建错误结果

        Args:
            worker_id: Worker ID
            error_message: 错误消息
            input_data: 原始输入数据（可选，用于获取文档类型信息）

        Returns:
            包含错误的抽取结果
        """
        from src.domain.models.profiling_input import DocType

        result = WorkerProfileExtractionResult(worker_id=worker_id)

        # 尝试从输入数据获取文档类型信息
        doc_type = DocType.SOUL  # 默认值
        doc_name = "unknown"

        if input_data and input_data.documents:
            doc_type = input_data.documents[0].doc_type
            doc_name = input_data.documents[0].source_uri or f"{doc_type.value}.md"

        result.errors.append(ExtractionError(
            field="extraction",
            message=f"Extraction failed: {error_message}",
            doc_type=doc_type,
            doc_name=doc_name,
            severity="critical",
        ))
        return result


__all__ = ["WorkerProfilingService"]