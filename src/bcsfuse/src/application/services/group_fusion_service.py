"""
GroupFusionService

G1: Fusion Entry Layer / G2: Conflict Alignment Layer / G5: Expert Diagnosis Layer / G9: Bot Profile Fuse Layer

融合服务，负责编排多参与者视角融合流程。

职责：
- 接收 FusionRequest
- 编排视角收集
- 根据 fusion_mode 分发到 G1/G2/G5/G9 逻辑
- 返回 FusionResult

约束：
- 不重写 M1-M10 内部链路
- 依赖可注入的 PerspectiveProvider
- 支持 partial success
- 支持可选注入 FusionRecommendationService 以启用 LLM 增强
- G1/G2/G5/G9 通过 fusion_mode 区分，服务只做分发

G1 模式（fusion_mode=agent）：
- 走原有融合逻辑
- 不填充 conflicts/alignment_points/key_insights/G5 字段

G2 模式（fusion_mode=conflict_alignment）：
- 调用 ConflictAlignmentService
- 填充冲突/对齐点/关键洞察

G5 模式（fusion_mode=expert_diagnosis）：
- 调用 ExpertDiagnosisService
- 填充风险评估/关键问题/专家建议/上线条件/诊断摘要

G9 模式（fusion_mode=bot_profile_fuse）：
- 并发调用 ProfileMergeService + GroupContextService
- 调用 FusionExpertChatService 构建 Prompt + LLM 调用
- 融合多个 participant 的 Profile 为综合答案

Stage 1 Phase 5:
- 支持显式 offline participant warning
- 显式给定的 offline participant 不自动替换，但给出 warning
"""

from __future__ import annotations

import logging
import time
import uuid
import threading
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed, wait, ALL_COMPLETED
from datetime import datetime
from typing import TYPE_CHECKING, Optional

from src.domain.enums.fuse_enums import FusionMode
from src.domain.models.fusion_request import FusionRequest
from src.domain.models.fusion_result import (
    FusionResult,
    Perspective,
    Recommendation,
    FusionTiming,
)
from src.domain.services.perspective_provider import PerspectiveProvider, PerspectiveContext
from src.domain.models.profile_fusion import FusionContext
from src.infra.context import submit_with_context

if TYPE_CHECKING:
    from src.application.services.fusion_recommendation_service import FusionRecommendationService
    from src.application.services.conflict_alignment_service import ConflictAlignmentService
    from src.application.services.expert_diagnosis_service import ExpertDiagnosisService
    from src.application.services.participant_availability_checker import ParticipantAvailabilityChecker
    from src.application.services.bot_fuse.profile_merge_service import ProfileMergeService
    from src.application.services.bot_fuse.fusion_expert_chat_service import FusionExpertChatService
    from src.application.services.bot_fuse.group_context_service import GroupContextService
    from src.domain.services.adapters.worker_registry_store_adapter import WorkerRegistryStoreAdapter

logger = logging.getLogger(__name__)


class GroupFusionService:
    """
    融合服务

    在指定 group 上对问题发起多参与者视角融合。

    核心方法：
    - fuse(): 执行融合操作，根据 fusion_mode 分发

    G9 模式三次模型调用：
    1. GroupContextService - 会话总结（改写问题+摘要）
    2. ProfileMergeService - Profile 融合
    3. FusionExpertChatService - Prompt构建 + LLM调用 + 结果构建

    三次调用共享同一个 LLM 线程池（由 _llm_executor 管理）。

    Attributes:
        _provider: 视角收集器
        _recommendation_service: 融合建议服务（可选，启用 LLM 增强）
        _conflict_alignment_service: 冲突对齐服务（可选，G2 模式使用）
        _expert_diagnosis_service: 专家会诊服务（可选，G5 模式使用）
        _profile_merge_service: Profile 融合服务（可选，G9 模式使用）
        _fusion_expert_chat_service: 对话服务（可选，G9 模式使用）
        _group_context_service: 群组上下文服务（可选，G9 模式用于获取群组会话摘要和改写问题）
        _availability_checker: 参与者可用性检查器（可选，Phase 5）
        _max_parallel_workers: 并行收集视角的最大线程数
    """

    def __init__(
        self,
        provider: PerspectiveProvider,
        recommendation_service: Optional["FusionRecommendationService"] = None,
        conflict_alignment_service: Optional["ConflictAlignmentService"] = None,
        expert_diagnosis_service: Optional["ExpertDiagnosisService"] = None,
        profile_merge_service: Optional["ProfileMergeService"] = None,
        fusion_expert_chat_service: Optional["FusionExpertChatService"] = None,
        group_context_service: Optional["GroupContextService"] = None,
        availability_checker: Optional["ParticipantAvailabilityChecker"] = None,
        worker_store: Optional["WorkerRegistryStoreAdapter"] = None,
        max_parallel_workers: int = 5,
        llm_max_concurrent: int = 10,
    ):
        """
        初始化服务

        Args:
            provider: 视角收集器，用于从 participant 收集视角
            recommendation_service: 融合建议服务（可选，启用 LLM 增强）
            conflict_alignment_service: 冲突对齐服务（可选，G2 模式使用）
            expert_diagnosis_service: 专家会诊服务（可选，G5 模式使用）
            profile_merge_service: Profile 融合服务（可选，G9 模式使用）
            fusion_expert_chat_service: 对话服务（可选，G9 模式使用，负责 Prompt 构建、LLM 调用和结果构建）
            group_context_service: 群组上下文服务（可选，G9 模式用于获取群组会话摘要和改写问题）
            availability_checker: 参与者可用性检查器（可选，Phase 5）
            worker_store: bcsfuse_workers 表存储（可选，G9 模式用于批量检查 fusion_enable）
            max_parallel_workers: 并行收集视角的最大线程数（默认5，最大建议8）
            llm_max_concurrent: G9 LLM 调用最大并发数（默认10）
        """
        self._provider = provider
        self._recommendation_service = recommendation_service
        self._conflict_alignment_service = conflict_alignment_service
        self._expert_diagnosis_service = expert_diagnosis_service
        self._profile_merge_service = profile_merge_service
        self._fusion_expert_chat_service = fusion_expert_chat_service
        self._group_context_service = group_context_service
        self._availability_checker = availability_checker
        self._worker_store = worker_store
        self._max_parallel_workers = max_parallel_workers

        # G9 LLM 线程池（控制并发）
        self._llm_max_concurrent = llm_max_concurrent
        self._llm_executor: Optional[ThreadPoolExecutor] = None

    def set_availability_checker(self, availability_checker: Optional["ParticipantAvailabilityChecker"]) -> None:
        """
        Update the participant availability checker.

        P1 Fix: Allows request-context based availability checker injection.
        This enables OSS mode to use stores from request.app.state.context.registry
        instead of global singletons from worker_dependencies.py.

        Args:
            availability_checker: New ParticipantAvailabilityChecker instance (or None)
        """
        self._availability_checker = availability_checker
        logger.debug(
            "[GroupFusionService] Updated availability_checker: new_id=%d",
            id(availability_checker) if availability_checker else 0
        )

    def set_perspective_provider(self, provider: Optional["PerspectiveProvider"]) -> None:
        """
        Update the perspective provider.

        Phase B3 Fix: Allows request-context based perspective provider injection.
        This enables OSS mode to use profile_source with stores from
        request.app.state.context.registry instead of global singletons.

        Why this is needed:
        - LLMPerspectiveProvider uses profile_source to load worker profiles
        - profile_source should use the same profile_content_store as Profile CRUD/Activate
        - Without this, Fusion reads from Instance B while Profile writes to Instance A
        - Result: active_profiles_loaded_count = 0, fallback perspective used

        Args:
            provider: New PerspectiveProvider instance (or None)
        """
        self._provider = provider
        logger.debug(
            "[GroupFusionService] Updated perspective_provider: new_id=%d, type=%s",
            id(provider) if provider else 0,
            type(provider).__name__ if provider else "None"
        )

    def _get_llm_executor(self) -> ThreadPoolExecutor:
        """
        获取或创建 LLM 线程池（懒加载）

        线程池保证始终最多 max_workers 个线程在并发执行 LLM 请求。
        同时会共享给 ProfileMergeService，实现统一的并发控制。

        Returns:
            ThreadPoolExecutor: LLM 线程池实例
        """
        if self._llm_executor is None:
            self._llm_executor = ThreadPoolExecutor(
                max_workers=self._llm_max_concurrent,
                thread_name_prefix="g9-llm-"
            )
            logger.info(
                "[G9-FUSE] LLM 线程池创建: max_workers=%d",
                self._llm_max_concurrent
            )
            # 共享给 ProfileMergeService
            if self._profile_merge_service is not None:
                self._profile_merge_service.set_llm_executor(self._llm_executor)
        return self._llm_executor

    def _check_llm_queue_available(self) -> bool:
        """
        检查 LLM 线程池队列是否有空位

        Returns:
            bool: True 表示可以提交任务，False 表示队列有等待任务（快速失败）
        """
        executor = self._get_llm_executor()
        # 获取线程池内部队列长度
        # 如果队列有等待任务，说明已经有 max_workers 个任务在执行
        queue_size = executor._work_queue.qsize()
        return queue_size == 0

    def shutdown(self) -> None:
        """关闭服务，清理线程池"""
        if self._llm_executor is not None:
            logger.info("[G9-FUSE] 关闭 LLM 线程池...")
            self._llm_executor.shutdown(wait=False)
            self._llm_executor = None

    def fuse(self, request: FusionRequest, group_id: str) -> FusionResult:
        """
        执行融合

        根据 fusion_mode 分发到 G1/G2/G5/G9 逻辑：

        - fusion_mode="agent": G1 模式，走原有融合逻辑
        - fusion_mode="conflict_alignment": G2 模式，调用 ConflictAlignmentService
        - fusion_mode="expert_diagnosis": G5 模式，调用 ExpertDiagnosisService
        - fusion_mode="bot_profile_fuse": G9 模式，调用 ProfileMergeService

        Args:
            request: 融合请求
            group_id: Group 标识符

        Returns:
            FusionResult: 融合结果
        """
        logger.info("[Fusion] ========== 开始执行融合 ==========")
        logger.info("[Fusion] group_id=%s, fusion_mode=%s", group_id, request.fusion_mode)
        logger.info("[Fusion] question=%s", request.question[:100] if len(request.question) > 100 else request.question)
        logger.info("[Fusion] participants=%s, driver_bot_id=%s", request.participants, request.driver_bot_id)
        logger.info("[Fusion] options: timeout_ms=%s, include_recommendation=%s, strict_participants=%s",
                    request.options.timeout_ms, request.options.include_recommendation, request.options.strict_participants)

        # strict_participants 已通过服务链传递:
        # GroupFusionService -> ExpertDiagnosisService -> G5ExpertEnhancer -> WorkerProfileRetrievalService
        if request.options.strict_participants and request.fusion_mode == "expert_diagnosis":
            logger.info("[Fusion] strict_participants=True 已启用，将通过服务链传递到底层检索服务")

        # G9 模式：调用 Profile 融合服务
        if request.fusion_mode == "bot_profile_fuse":
            logger.info("[Fusion] 分发到 G9 Profile 融合模式")
            return self._fuse_g9(request, group_id)

        # G5 模式：调用专家会诊服务
        if request.fusion_mode == "expert_diagnosis":
            logger.info("[Fusion] 分发到 G5 专家会诊模式")
            return self._fuse_g5(request, group_id)

        # G2 模式：调用冲突对齐服务
        if request.fusion_mode == "conflict_alignment":
            logger.info("[Fusion] 分发到 G2 冲突对齐模式")
            return self._fuse_g2(request, group_id)

        # G1 模式：走原有逻辑
        logger.info("[Fusion] 分发到 G1 基础融合模式")
        return self._fuse_g1(request, group_id)

    def _fuse_g1(self, request: FusionRequest, group_id: str) -> FusionResult:
        """
        G1 模式融合（原有逻辑）

        Args:
            request: 融合请求
            group_id: Group 标识符

        Returns:
            FusionResult: 融合结果（G1 模式）
        """
        started_at = datetime.now()
        logger.info("[G1] 开始 G1 模式融合")

        # 生成 fusion_id
        fusion_id = f"fus-{uuid.uuid4().hex[:12]}"
        logger.info("[G1] 生成 fusion_id=%s", fusion_id)

        # 确定 driver_bot_id
        driver_bot_id = request.driver_bot_id
        if driver_bot_id is None and request.participants:
            driver_bot_id = request.participants[0]
        logger.info("[G1] 确定 driver_bot_id=%s", driver_bot_id)

        # 收集视角
        logger.info("[G1] 开始收集视角...")
        perspectives, warnings, errors = self._collect_perspectives(
            request=request,
            group_id=group_id,
            driver_bot_id=driver_bot_id,
        )
        logger.info("[G1] 视角收集完成: %d 个视角, %d 个警告, %d 个错误",
                    len(perspectives), len(warnings), len(errors))

        # 判断是否部分成功
        completed_count = sum(1 for p in perspectives if p.status == "completed")
        total_count = len(perspectives)
        partial_success = completed_count > 0 and completed_count < total_count
        logger.info("[G1] 完成状态: %d/%d 完成, partial_success=%s",
                    completed_count, total_count, partial_success)

        # 生成 recommendation
        recommendation = None
        if request.options.include_recommendation:
            rec_start = time.time()
            logger.info("[PERF-G1] 开始生成推荐...")
            # 如果注入了 FusionRecommendationService，使用 LLM 生成
            if self._recommendation_service is not None:
                logger.info("[PERF-G1] 使用 LLM 生成推荐")
                recommendation = self._generate_llm_recommendation(
                    perspectives=perspectives,
                    question=request.question,
                    driver_bot_id=driver_bot_id,
                    partial_success=partial_success,
                    warnings=warnings,
                )
            else:
                logger.info("[PERF-G1] 使用规则方法生成推荐")
                # 否则使用规则基础的方法
                recommendation = self._synthesize_recommendation(
                    perspectives=perspectives,
                    question=request.question,
                )
            rec_elapsed = time.time() - rec_start
            logger.info("[PERF-G1] 推荐生成完成: decision=%s, 耗时=%.3fs",
                       recommendation.decision if recommendation else None, rec_elapsed)

        finished_at = datetime.now()
        duration_ms = int((finished_at - started_at).total_seconds() * 1000)
        logger.info("[G1] G1 融合完成: duration=%dms", duration_ms)

        timing = FusionTiming(
            started_at=started_at,
            finished_at=finished_at,
            duration_ms=duration_ms,
        )

        # Phase D2: Aggregate diagnostics from perspectives
        metadata = {}
        profile_diagnostics = {}

        # Aggregate profile loading diagnostics from perspectives
        online_workers_count = 0
        active_profiles_loaded_count = 0
        profile_content_loaded_count = 0
        profile_content_non_empty_count = 0
        profile_content_length_min = float('inf')
        profile_content_length_max = 0
        profile_formats = {}
        profile_format_conversion_success_count = 0
        fallback_perspective_count = 0

        for perspective in perspectives:
            # Check if this is a fallback perspective
            if perspective.status == "failed" or "fallback" in perspective.summary.lower():
                fallback_perspective_count += 1

            # Extract diagnostics from perspective.metadata
            if hasattr(perspective, 'metadata') and perspective.metadata:
                p_diag = perspective.metadata.get('diagnostics', {})

                # Count profile loading
                if p_diag.get('profile_loaded'):
                    profile_content_loaded_count += 1
                    online_workers_count += 1  # Profile loaded means worker is available

                # Track profile content length
                content_length = p_diag.get('profile_content_length', 0)
                if content_length > 0:
                    profile_content_non_empty_count += 1
                    profile_content_length_min = min(profile_content_length_min, content_length)
                    profile_content_length_max = max(profile_content_length_max, content_length)

                # Track profile format
                profile_format = p_diag.get('profile_format')
                if profile_format:
                    profile_formats[profile_format] = profile_formats.get(profile_format, 0) + 1

                # Track format conversion success
                if p_diag.get('profile_format_conversion_success'):
                    profile_format_conversion_success_count += 1

                # Count LLM usage
                if p_diag.get('llm_success'):
                    active_profiles_loaded_count += 1

        # Set metadata
        metadata['online_workers_count'] = online_workers_count if online_workers_count > 0 else len(perspectives)
        metadata['active_profiles_loaded_count'] = active_profiles_loaded_count if active_profiles_loaded_count > 0 else len([p for p in perspectives if p.status == "completed"])
        metadata['participants_resolved_count'] = len(perspectives)

        # Profile content diagnostics
        profile_diagnostics['profile_content_loaded_count'] = profile_content_loaded_count
        profile_diagnostics['profile_content_non_empty_count'] = profile_content_non_empty_count
        profile_diagnostics['profile_content_length_min'] = profile_content_length_min if profile_content_length_min != float('inf') else 0
        profile_diagnostics['profile_content_length_max'] = profile_content_length_max
        profile_diagnostics['fallback_perspective_used'] = fallback_perspective_count > 0

        # Profile format stats
        if profile_formats:
            most_common_format = max(profile_formats.items(), key=lambda x: x[1])[0] if profile_formats else None
            metadata['profile_format'] = most_common_format
            metadata['profile_format_conversion_success'] = profile_format_conversion_success_count > 0

        metadata['profile_diagnostics'] = profile_diagnostics

        logger.info(
            "[G1][Diagnostics] online_workers=%d, active_profiles=%d, content_loaded=%d, "
            "content_non_empty=%d, format=%s, fallback=%d",
            metadata['online_workers_count'],
            metadata['active_profiles_loaded_count'],
            profile_diagnostics['profile_content_loaded_count'],
            profile_diagnostics['profile_content_non_empty_count'],
            metadata.get('profile_format', 'N/A'),
            fallback_perspective_count
        )

        return FusionResult(
            group_id=group_id,
            fusion_id=fusion_id,
            question=request.question,
            driver_bot_id=driver_bot_id,
            perspectives=perspectives,
            recommendation=recommendation,
            partial_success=partial_success,
            warnings=warnings,
            errors=errors,
            timing=timing,
            # G1 模式：fusion_mode 为 agent，G2 字段为空
            fusion_mode="agent",
            conflicts=[],
            alignment_points=[],
            key_insights=[],
            # Phase D2: Add metadata
            metadata=metadata,
        )

    def _fuse_g2(self, request: FusionRequest, group_id: str) -> FusionResult:
        """
        G2 模式融合（冲突对齐）

        Args:
            request: 融合请求
            group_id: Group 标识符

        Returns:
            FusionResult: 融合结果（G2 模式）
        """
        g2_start = time.time()
        logger.info("[PERF-G2] ========== _fuse_g2 开始 ==========")

        # 确定 driver_bot_id
        driver_bot_id = request.driver_bot_id
        if driver_bot_id is None and request.participants:
            driver_bot_id = request.participants[0]

        # 收集视角（复用 G1 逻辑）
        perspectives, warnings, errors = self._collect_perspectives(
            request=request,
            group_id=group_id,
            driver_bot_id=driver_bot_id,
        )

        # 如果注入了 ConflictAlignmentService，使用它
        align_start = time.time()
        if self._conflict_alignment_service is not None:
            result = self._conflict_alignment_service.align(
                question=request.question,
                perspectives=perspectives,
                driver_bot_id=driver_bot_id,
                include_recommendation=request.options.include_recommendation,
            )
        else:
            # 否则创建默认的 ConflictAlignmentService
            from src.application.services.conflict_alignment_service import ConflictAlignmentService

            conflict_service = ConflictAlignmentService(
                recommendation_service=self._recommendation_service,
            )

            result = conflict_service.align(
                question=request.question,
                perspectives=perspectives,
                driver_bot_id=driver_bot_id,
                include_recommendation=request.options.include_recommendation,
            )

        align_elapsed = time.time() - align_start
        g2_elapsed = time.time() - g2_start
        logger.info("[PERF-G2] 冲突对齐完成: align耗时=%.3fs, 总耗时=%.3fs", align_elapsed, g2_elapsed)
        logger.info("[PERF-G2] ========== _fuse_g2 完成 ==========")

        # 设置 group_id
        result.group_id = group_id
        return result

    def _fuse_g5(self, request: FusionRequest, group_id: str) -> FusionResult:
        """
        G5 模式融合（专家会诊）

        Args:
            request: 融合请求
            group_id: Group 标识符

        Returns:
            FusionResult: 融合结果（G5 模式）
        """
        import os
        logger.info("="*80)
        logger.info("[G5-FUSE] ========== _fuse_g5 开始 ==========")
        logger.info("[G5-FUSE] PID: %d", os.getpid())
        logger.info("[G5-FUSE] group_id: %s", group_id)
        logger.info("[G5-FUSE] request.fusion_mode: %s", request.fusion_mode)
        logger.info("[G5-FUSE] request.question 长度: %d", len(request.question))
        logger.info("[G5-FUSE] request.participants: %s", request.participants)
        logger.info("[G5-FUSE] request.driver_bot_id: %s", request.driver_bot_id)

        # 检查服务注入状态
        logger.info("[G5-FUSE] 服务注入状态:")
        logger.info("[G5-FUSE]   - _provider: %s", type(self._provider).__name__ if self._provider else "None")
        logger.info("[G5-FUSE]   - _recommendation_service: %s", "已注入" if self._recommendation_service else "None")
        logger.info("[G5-FUSE]   - _expert_diagnosis_service: %s", "已注入" if self._expert_diagnosis_service else "None")
        logger.info("[G5-FUSE]   - _availability_checker: %s", "已注入" if self._availability_checker else "None")

        # 确定 driver_bot_id
        driver_bot_id = request.driver_bot_id
        if driver_bot_id is None and request.participants:
            driver_bot_id = request.participants[0]
        logger.info("[G5-FUSE] driver_bot_id 确定: %s", driver_bot_id)

        # 收集视角（复用 G1 逻辑）
        logger.info("[G5-FUSE] Step 1: 收集视角...")
        perspectives, warnings, errors = self._collect_perspectives(
            request=request,
            group_id=group_id,
            driver_bot_id=driver_bot_id,
        )
        logger.info("[G5-FUSE] 视角收集完成: %d 个视角, %d 个警告, %d 个错误",
                   len(perspectives), len(warnings), len(errors))
        for i, p in enumerate(perspectives):
            logger.info("[G5-FUSE]   perspectives[%d]: id=%s, role=%s, status=%s",
                       i, p.participant_id, p.role, p.status)

        # 如果注入了 ExpertDiagnosisService，使用它
        if self._expert_diagnosis_service is not None:
            logger.info("[G5-FUSE] Step 2: 使用注入的 ExpertDiagnosisService...")
            logger.info("[G5-FUSE]   ExpertDiagnosisService id: %d", id(self._expert_diagnosis_service))
            logger.info("[G5-FUSE]   ExpertDiagnosisService._g5_enhancer: %s",
                       "已注入" if self._expert_diagnosis_service._g5_enhancer else "None")

            diagnose_start = datetime.now()
            result = self._expert_diagnosis_service.diagnose(
                question=request.question,
                perspectives=perspectives,
                driver_bot_id=driver_bot_id,
                include_recommendation=request.options.include_recommendation,
                participants=request.participants,
                strict_participants=request.options.strict_participants,
            )
            diagnose_elapsed = (datetime.now() - diagnose_start).total_seconds()
            logger.info("[G5-FUSE] diagnose() 完成，耗时: %.2fs", diagnose_elapsed)

            # 设置 group_id
            result.group_id = group_id
            logger.info("[G5-FUSE] 返回结果: fusion_mode=%s, perspectives=%d",
                       result.fusion_mode, len(result.perspectives))
            logger.info("[G5-FUSE] ========== _fuse_g5 结束 ==========")
            logger.info("="*80)
            return result

        # 否则创建默认的 ExpertDiagnosisService
        logger.warning("[G5-FUSE] ⚠️ ExpertDiagnosisService 未注入，创建默认实例...")
        from src.application.services.expert_diagnosis_service import ExpertDiagnosisService

        expert_service = ExpertDiagnosisService(
            recommendation_service=self._recommendation_service,
        )

        result = expert_service.diagnose(
            question=request.question,
            perspectives=perspectives,
            driver_bot_id=driver_bot_id,
            include_recommendation=request.options.include_recommendation,
            participants=request.participants,
            strict_participants=request.options.strict_participants,
        )

        # 设置 group_id
        result.group_id = group_id
        logger.info("[G5-FUSE] ========== _fuse_g5 结束 (默认服务) ==========")
        logger.info("="*80)
        return result

    def _fuse_g9(self, request: FusionRequest, group_id: str) -> FusionResult:
        """
        G9 模式融合（Profile Fusion）- 主流程编排

        将多个 participant 的 Profile 融合为一个超级 BOT Profile，
        然后使用融合后的 Profile 作为 System Prompt 调用 LLM 回答问题。

        流程：
        1. 检查服务依赖
        2. 收集 Profile 并计算 fusion_id
        3. 并发执行：Profile 融合 + 会话总结
        4. 构建 System/User Prompt（包含改写问题和会话摘要）
        5. 调用 LLM 回答问题
        6. 返回 FusionResult

        Args:
            request: 融合请求
            group_id: Group 标识符

        Returns:
            FusionResult: 融合结果（G9 模式）
        """
        from src.domain.models.profile_fusion import GroupConversationSummary
        from src.utils.fuse_util import generate_fusion_id, worker_profile_to_dict

        start_time = time.time()
        logger.info("[G9-FUSE] ========== _fuse_g9 开始 ==========")
        logger.info("[G9-FUSE] group_id: %s", group_id)
        logger.info("[G9-FUSE] participants: %s", request.participants)
        logger.info("[G9-FUSE] driver_bot_id: %s", request.driver_bot_id)
        logger.info("[G9-FUSE] question 长度: %d 字符", len(request.question) if request.question else 0)
        logger.info("[G9-FUSE] question 预览: %s", request.question[:100] if request.question else "")

        started_at = datetime.now()
        warnings: list[str] = []
        errors: list[str] = []

        # ========== fusion_enable 检查 ==========
        # 检查每个已注册 participant 的 fusion_enable 配置，未开启则直接报错终止
        disabled_ids = self._check_fusion_enabled(request.participants, errors)
        if disabled_ids:
            logger.error("[G9-FUSE] Participants with fusion disabled: %s, aborting", disabled_ids)
            return self._fusion_expert_chat_service.build_error_result(
                group_id=group_id,
                request=request,
                warnings=warnings,
                errors=errors,
                started_at=started_at,
                fusion_id="",
                driver_bot_id=request.driver_bot_id,
            )

        # ========== Step 0: 收集 Profile 并构建上下文 ==========
        step0_start = time.time()
        logger.info("[G9-FUSE] ========== Step 0: 收集 Profile 并构建上下文 ==========")

        profiles, profile_warnings, profile_errors = self._profile_merge_service.collect_profiles(
            request.participants
        )
        warnings.extend(profile_warnings)
        errors.extend(profile_errors)

        # 构建 Fusion 上下文（内部会确定 driver_bot_id）
        profiles_dict = [worker_profile_to_dict(p) for p in profiles]
        fusion_ctx = FusionContext.from_request(
            request=request,
            group_id=group_id,
            profiles=profiles,
            profiles_dict=profiles_dict,
        )
        logger.info("[G9-FUSE] 确定 driver_bot_id=%s", fusion_ctx.driver_bot_id)

        # 计算 fusion_id
        fusion_ctx.fusion_id = generate_fusion_id(
            fusion_mode=FusionMode.BOT_PROFILE_FUSE.value,
            ctx=fusion_ctx,
        )

        step0_elapsed = time.time() - step0_start
        logger.info("[G9-FUSE] Step0 完成: profiles=%d, fusion_id=%s, 耗时=%.3fs",
                   len(profiles), fusion_ctx.fusion_id, step0_elapsed)

        # ========== Step 1: 并发执行 Profile 融合 + 会话总结 ==========
        step1_start = time.time()
        logger.info("[G9-FUSE] ========== Step 1: 并发执行 Profile 融合 + 会话总结 ==========")

        # 获取线程池
        executor = self._get_llm_executor()

        # 提交 Profile 融合任务（使用 submit_with_context 传递上下文）
        profile_future = submit_with_context(
            executor,
            self._profile_merge_service.fuse_profiles,
            fusion_ctx,
        )

        # 提交会话总结任务（使用 submit_with_context 传递上下文）
        summary_future = submit_with_context(
            executor,
            self._run_conversation_summarization,
            request.question,
            group_id,
        )

        # 并发等待两个任务（超时取最大值 120s）
        done, not_done = wait(
            [profile_future, summary_future],
            timeout=120,  # Profile 融合最长 120s，Summary 最长 30s
            return_when=ALL_COMPLETED
        )

        # 处理 Profile 融合结果（必须成功）
        fusion_result = None
        profile_success = False
        if profile_future in done:
            try:
                fusion_result = profile_future.result()
                step1_profile_elapsed = time.time() - step1_start
                logger.info(
                    "[G9-FUSE] Step1a(Profile融合)完成: cache_hit=%s, 耗时=%.3fs",
                    fusion_result.cache_hit, step1_profile_elapsed
                )
                warnings.extend(fusion_result.warnings)
                errors.extend(fusion_result.errors)
                profile_success = True
            except Exception as e:
                step1_profile_elapsed = time.time() - step1_start
                logger.error("[G9-FUSE] Step1a(Profile融合)失败: %s, 耗时=%.3fs", str(e), step1_profile_elapsed, exc_info=True)
                errors.append(f"Profile fusion failed: {str(e)}")
        else:
            # Profile 融合超时
            profile_future.cancel()
            step1_profile_elapsed = time.time() - step1_start
            logger.error("[G9-FUSE] Step1a(Profile融合)超时: 耗时=%.3fs", step1_profile_elapsed)
            errors.append("Profile fusion timed out after 120s")

        if not profile_success or fusion_result is None:
            return self._fusion_expert_chat_service.build_error_result(
                group_id=group_id,
                request=request,
                warnings=warnings,
                errors=errors,
                started_at=started_at,
                fusion_id=fusion_ctx.fusion_id,
                driver_bot_id=fusion_ctx.driver_bot_id,
            )

        # 检查 Profile 融合结果
        fused_profile = fusion_result.fused_profile
        if not fused_profile.has_content():
            logger.warning("[G9-FUSE] 融合后的 Profile 无内容")
            return self._fusion_expert_chat_service.build_error_result(
                group_id=group_id,
                request=request,
                warnings=warnings + ["Fused profile has no content"],
                errors=errors,
                started_at=started_at,
                fusion_id=fusion_ctx.fusion_id,
                driver_bot_id=fusion_ctx.driver_bot_id,
            )

        # 处理会话总结结果（失败时降级）
        conv_summary = None
        if summary_future in done:
            try:
                conv_summary = summary_future.result()
                step1_summary_elapsed = time.time() - step1_start
                logger.info(
                    "[G9-FUSE] Step1b(会话总结)完成: success=%s, context_count=%d, 耗时=%.3fs",
                    conv_summary.success, conv_summary.context_messages_count, step1_summary_elapsed
                )
                if not conv_summary.success and conv_summary.error_message:
                    warnings.append(f"Conversation summary: {conv_summary.error_message}")
            except Exception as e:
                step1_summary_elapsed = time.time() - step1_start
                logger.warning("[G9-FUSE] Step1b(会话总结)失败（继续执行）: %s, 耗时=%.3fs", str(e), step1_summary_elapsed)
        else:
            # 会话总结超时，降级处理
            summary_future.cancel()
            step1_summary_elapsed = time.time() - step1_start
            logger.warning("[G9-FUSE] Step1b(会话总结)超时（继续执行）: 耗时=%.3fs", step1_summary_elapsed)

        # 会话总结失败或超时时，降级为空摘要
        if conv_summary is None:
            conv_summary = GroupConversationSummary(
                rewritten_question=request.question,
                original_question=request.question,
                context_summary="",
                context_messages_count=0,
                success=False,
                error_message="Conversation summary failed or timed out",
            )
            warnings.append("Conversation summary fallback: failed or timed out")

        step1_elapsed = time.time() - step1_start
        logger.info("[G9-FUSE] Step1(并发)总耗时: %.3fs (实际并行执行)", step1_elapsed)

        # ========== Step 2: 构建 Prompt（包含改写问题和会话摘要）==========
        step2_start = time.time()
        system_prompt, user_prompt = self._fusion_expert_chat_service.build_prompts(
            fused_profile=fused_profile,
            original_question=request.question,
            rewritten_question=conv_summary.rewritten_question,
            context_summary=conv_summary.context_summary,
        )
        step2_elapsed = time.time() - step2_start
        logger.info("[G9-FUSE] Step2(Prompt构建)完成: 耗时=%.3fs", step2_elapsed)

        # ========== Step 3: 调用 LLM 获取推荐 ==========
        recommendation, llm_errors, step3_elapsed, token_usage = self._fusion_expert_chat_service.call_with_retry(
            system_prompt, user_prompt, request.options.timeout_ms
        )
        errors.extend(llm_errors)

        # 构建最终结果
        result = self._fusion_expert_chat_service.build_success_result(
            group_id=group_id,
            request=request,
            fused_profile=fused_profile,
            recommendation=recommendation,
            fusion_result=fusion_result,
            conv_summary=conv_summary,
            warnings=warnings,
            errors=errors,
            started_at=started_at,
            driver_bot_id=fusion_ctx.driver_bot_id,
            step_elapsed={
                "step1": step1_elapsed,
                "step2": step2_elapsed,
                "step3": step3_elapsed,
                # 细粒度耗时
                "profile_fusion": step1_profile_elapsed,
                "group_conversation": step1_summary_elapsed,
                "llm_generation": step3_elapsed,
            },
            token_usage=token_usage,
        )

        logger.info("[G9-FUSE] ========== _fuse_g9 结束 ==========")
        return result

    # =========================================================================
    # G9 私有方法：拆分出的辅助方法
    # =========================================================================

    def _check_fusion_enabled(
        self,
        participants: list[str],
        errors: list[str],
    ) -> list[str]:
        """
        检查 participant 是否开启了融合（批量查询）。

        通过 worker_store.batch_get_configs 一次性获取所有 participant 的
        fusion_enable 配置。未开启融合的成员直接报错，由调用方终止流程。

        未注册的 participant 不做拦截，由后续 collect_profiles 流程处理。

        Args:
            participants: participant ID 列表
            errors: 错误列表（就地追加）

        Returns:
            fusion_enable=False 的 participant ID 列表（空列表表示全部通过）
        """
        if not self._worker_store:
            return []

        configs, not_found_ids = self._worker_store.batch_get_configs(participants)

        disabled_ids: list[str] = []
        for pid in participants:
            if pid in not_found_ids:
                continue
            if not configs[pid].fusion_enable:
                disabled_ids.append(pid)

        if disabled_ids:
            errors.append(
                f"Workers with fusion disabled: {disabled_ids}, cannot proceed with profile fusion"
            )

        return disabled_ids

    def _run_conversation_summarization(
        self,
        question: str,
        group_id: str,
    ) -> "GroupConversationSummary":
        """
        同步包装器：在单独线程中运行异步的会话总结

        使用 asyncio.run() 在新线程中运行异步代码。

        Args:
            question: 原始问题
            group_id: 群组 ID

        Returns:
            GroupConversationSummary: 会话总结结果
        """
        import asyncio

        from src.domain.models.profile_fusion import GroupConversationSummary

        if self._group_context_service is None:
            logger.info("[G9-FUSE] GroupContextService 未注入，跳过会话总结")
            return GroupConversationSummary(
                rewritten_question=question,
                original_question=question,
                context_summary="",
                context_messages_count=0,
                success=False,
                error_message="GroupContextService not injected",
            )

        try:
            # 在新的事件循环中运行异步方法
            loop = asyncio.new_event_loop()
            try:
                result = loop.run_until_complete(
                    self._group_context_service.summarize(question, group_id)
                )
                return result
            finally:
                loop.close()
        except Exception as e:
            logger.error("[G9-FUSE] 会话总结异常: %s", str(e), exc_info=True)
            return GroupConversationSummary(
                rewritten_question=question,
                original_question=question,
                context_summary="",
                context_messages_count=0,
                success=False,
                error_message=str(e),
            )

    def _check_g9_service_dependencies(self) -> None:
        """
        检查 G9 模式所需的服务依赖是否已注入

        Raises:
            RuntimeError: 当必要服务未注入时，抛出详细错误信息
        """
        if self._profile_merge_service is None:
            logger.error("[G9-FUSE] ProfileMergeService 未注入")
            logger.error("[G9-FUSE] 可能原因:")
            logger.error("[G9-FUSE]   1. profile_store 不可用 (检查 ZDAS/SQLite 数据库配置)")
            logger.error("[G9-FUSE]   2. LLM Gateway 不可用 (检查 LLM_ENABLED=true 和 LLM_BASE_URL)")
            logger.error("[G9-FUSE]   3. Profile 内容表不存在或为空")
            raise RuntimeError(
                "ProfileMergeService not injected for G9 mode. "
                "Possible causes: "
                "(1) Profile store not available - check ZDAS/SQLite database config; "
                "(2) LLM Gateway not available - check LLM_ENABLED=true and LLM_BASE_URL; "
                "(3) Profile content table is empty or does not exist."
            )

        if self._fusion_expert_chat_service is None:
            logger.error("[G9-FUSE] FusionExpertChatService 未注入")
            logger.error("[G9-FUSE] 可能原因:")
            logger.error("[G9-FUSE]   1. LLM_ENABLED 环境变量未设置为 true")
            logger.error("[G9-FUSE]   2. LLM_BASE_URL 或 LLM_AUTH_TOKEN 环境变量未配置")
            raise RuntimeError(
                "FusionExpertChatService not injected for G9 mode. "
                "Please set LLM_ENABLED=true and configure LLM_BASE_URL and LLM_AUTH_TOKEN."
            )

    def _collect_perspectives(
        self,
        request: FusionRequest,
        group_id: str,
        driver_bot_id: Optional[str],
    ) -> tuple[list[Perspective], list[str], list[str]]:
        """
        收集所有 participant 的视角（并行优化版）

        Stage 1 Phase 5: 支持显式 offline participant warning
        Stage 2: 支持 strict_participants 语义

        对于显式给定的 offline participant:
        - strict=False（兼容模式）: 创建 status="skipped" 的 Perspective
        - strict=True（严格模式）: 不创建 perspective，只记录 warning

        性能优化:
        - 使用 ThreadPoolExecutor 并行收集视角，将串行等待改为并行执行
        - 4个participants时，收集时间从 60s 降至 15s

        Args:
            request: 融合请求
            group_id: Group 标识符
            driver_bot_id: Driver bot 标识符

        Returns:
            tuple: (perspectives, warnings, errors)
        """
        total_start = time.time()
        perspectives: list[Perspective] = []
        warnings: list[str] = []
        errors: list[str] = []

        # 获取 strict_participants 设置
        strict_participants = getattr(request.options, 'strict_participants', False) if request.options else False

        # R12-3: 入口诊断日志
        import os
        logger.info("=" * 80)
        logger.info("[COLLECT-PERSPECTIVES-R12] ========== _collect_perspectives 开始 ==========")
        logger.info("[COLLECT-PERSPECTIVES-R12] PID: %d", os.getpid())
        logger.info("[COLLECT-PERSPECTIVES-R12] fusion_mode: agent")
        logger.info("[COLLECT-PERSPECTIVES-R12] participants_count: %d", len(request.participants))
        logger.info("[COLLECT-PERSPECTIVES-R12] participant_ids: %s", request.participants)
        logger.info("[COLLECT-PERSPECTIVES-R12] driver_bot_id: %s", driver_bot_id)
        logger.info("[COLLECT-PERSPECTIVES-R12] group_id: %s", group_id)
        logger.info("[COLLECT-PERSPECTIVES-R12] strict_participants: %s", strict_participants)
        logger.info("[COLLECT-PERSPECTIVES-R12] max_parallel_workers: %d", self._max_parallel_workers)

        # R12-3: 记录 provider 和 availability_checker 的 ID
        logger.info("[COLLECT-PERSPECTIVES-R12] perspective_provider_id: %d", id(self._provider) if self._provider else 0)
        logger.info("[COLLECT-PERSPECTIVES-R12] perspective_provider_type: %s", type(self._provider).__name__ if self._provider else "None")
        logger.info("[COLLECT-PERSPECTIVES-R12] availability_checker_id: %d", id(self._availability_checker) if self._availability_checker else 0)

        logger.info("[PERF] ========== _collect_perspectives 开始 ==========")
        logger.info("[PERF] participants=%s, max_workers=%d", request.participants, self._max_parallel_workers)

        # Phase 5: 预检查所有 participants 的可用性
        unavailable_participants: dict[str, str] = {}  # participant_id -> reason
        if self._availability_checker is not None:
            logger.info("[Perspectives] 使用 availability_checker 检查参与者可用性...")
            from src.application.services.participant_availability_checker import ParticipantAvailability
            availabilities = self._availability_checker.check_batch(request.participants)
            logger.info("[Perspectives] 可用性检查结果: %s",
                        {k: v.is_available for k, v in availabilities.items()})
            for participant_id, availability in availabilities.items():
                if not availability.is_available:
                    unavailable_participants[participant_id] = availability.unavailability_reason or "unknown"
                    logger.warning(
                        "[Perspectives] Participant %s 不可用: worker_id=%s, reason=%s",
                        participant_id, availability.worker_id, availability.unavailability_reason
                    )
        else:
            logger.info("[Perspectives] availability_checker 未注入，跳过可用性检查")

        # 分离需要收集视角的 participants 和 offline participants
        online_participants: list[str] = []
        skipped_participants: list[tuple[str, str]] = []  # (participant_id, reason)

        for participant_id in request.participants:
            if participant_id in unavailable_participants:
                reason = unavailable_participants[participant_id]
                skipped_participants.append((participant_id, reason))
            else:
                online_participants.append(participant_id)

        # 处理 offline participants（直接创建 skipped perspective）
        for participant_id, reason in skipped_participants:
            logger.info("[Perspectives] 处理 offline participant: %s", participant_id)

            # 添加 warning（区分不同的不可用原因）
            if reason == "unregistered":
                warning_msg = f"participant {participant_id} is not registered in worker registry"
            else:
                warning_msg = f"participant {participant_id} is offline and cannot participate"
            warnings.append(warning_msg)

            # strict 模式：不创建 skipped perspective，只记录 warning
            if strict_participants:
                logger.warning("[Perspectives] strict 模式: Participant %s 不可用 (reason=%s)，不创建 skipped perspective",
                              participant_id, reason)
                continue

            # 兼容模式：创建 skipped perspective（保持向后兼容）
            logger.warning("[Perspectives] 兼容模式: Participant %s 不可用 (reason=%s)，创建 skipped perspective",
                          participant_id, reason)
            perspective = Perspective(
                participant_id=participant_id,
                participant_type="bot",
                role="consultant",
                summary=f"Worker is unavailable: {reason}",
                confidence=None,
                evidence=[],
                status="skipped",
            )
            perspectives.append(perspective)

        # 并行收集在线 participants 的视角
        if online_participants:
            perspectives.extend(
                self._collect_perspectives_parallel(
                    online_participants=online_participants,
                    request=request,
                    group_id=group_id,
                    driver_bot_id=driver_bot_id,
                    warnings=warnings,
                )
            )

        # 按原始顺序排序 perspectives（保持结果稳定性）
        perspective_map = {p.participant_id: p for p in perspectives}
        ordered_perspectives = []
        for participant_id in request.participants:
            if participant_id in perspective_map:
                ordered_perspectives.append(perspective_map[participant_id])

        total_elapsed = time.time() - total_start

        # R12-3: 退出诊断日志
        logger.info("[COLLECT-PERSPECTIVES-R12] ========== _collect_perspectives 完成 ==========")
        logger.info("[COLLECT-PERSPECTIVES-R12] collected_perspectives_count: %d", len(ordered_perspectives))
        logger.info("[COLLECT-PERSPECTIVES-R12] online_participants_count: %d", len(online_participants))
        logger.info("[COLLECT-PERSPECTIVES-R12] skipped_participants_count: %d", len(skipped_participants))
        logger.info("[COLLECT-PERSPECTIVES-R12] warnings_count: %d", len(warnings))
        logger.info("[COLLECT-PERSPECTIVES-R12] errors_count: %d", len(errors))
        logger.info("[COLLECT-PERSPECTIVES-R12] total_elapsed: %.3fs", total_elapsed)

        # R12-3: 记录每个 perspective 的状态
        for i, p in enumerate(ordered_perspectives):
            logger.info("[COLLECT-PERSPECTIVES-R12]   perspectives[%d]: participant_id=%s, status=%s, role=%s",
                       i, p.participant_id, p.status, p.role)

        # R12-3: 检查是否有异常
        if len(ordered_perspectives) == 0 and len(online_participants) > 0:
            logger.error("[COLLECT-PERSPECTIVES-R12] ⚠️ 异常：在线参与者不为零但收集到零个视角！")
            logger.error("[COLLECT-PERSPECTIVES-R12]   online_participants: %s", online_participants)
            logger.error("[COLLECT-PERSPECTIVES-R12]   这可能表明 provider.collect() 全部失败或抛出异常")

        logger.info("=" * 80)

        logger.info("[PERF] ========== _collect_perspectives 完成 ==========")
        logger.info("[PERF] 总耗时: %.3fs, 视角数: %d, 警告: %d, 错误: %d",
                    total_elapsed, len(ordered_perspectives), len(warnings), len(errors))
        return ordered_perspectives, warnings, errors

    def _collect_perspectives_parallel(
        self,
        online_participants: list[str],
        request: FusionRequest,
        group_id: str,
        driver_bot_id: Optional[str],
        warnings: list[str],
    ) -> list[Perspective]:
        """
        并行收集在线 participants 的视角

        使用 ThreadPoolExecutor 并行执行多个 provider.collect() 调用，
        将串行等待时间转换为并行执行时间。

        Args:
            online_participants: 在线 participant ID 列表
            request: 融合请求
            group_id: Group 标识符
            driver_bot_id: Driver bot 标识符
            warnings: 警告列表（会被修改添加新警告）

        Returns:
            list[Perspective]: 收集到的视角列表
        """
        results: list[Perspective] = []
        timeout_ms = request.options.timeout_ms if request.options else 15000

        # 记录每个 participant 的耗时
        participant_timings: dict[str, float] = {}

        def collect_single(participant_id: str) -> Perspective:
            """收集单个 participant 的视角（在独立线程中执行）"""
            thread_start = time.time()
            logger.info("[PERF-Parallel] [%s] 开始收集", participant_id)

            # R12-3: 详细的 participant 收集诊断
            import traceback
            collect_trace_id = f"collect_{participant_id}_{int(time.time()*1000)}"
            logger.info("[COLLECT-SINGLE-R12] [%s] ========== 开始收集单个视角 ==========", participant_id)
            logger.info("[COLLECT-SINGLE-R12] [%s] trace_id: %s", participant_id, collect_trace_id)
            logger.info("[COLLECT-SINGLE-R12] [%s] thread_id: %d", participant_id, threading.current_thread().ident)

            context = PerspectiveContext(
                group_id=group_id,
                question=request.question,
                participant_id=participant_id,
                driver_bot_id=driver_bot_id,
                timeout_ms=timeout_ms,
            )

            # R12-3: 记录 context 信息
            logger.info("[COLLECT-SINGLE-R12] [%s] context_created: group_id=%s, question_length=%d",
                       participant_id, context.group_id, len(context.question) if context.question else 0)

            try:
                # R12-3: 调用前检查 provider
                if self._provider is None:
                    logger.error("[COLLECT-SINGLE-R12] [%s] ⚠️ 严重错误：provider 为 None！", participant_id)
                    raise ValueError("PerspectiveProvider is None")

                logger.info("[COLLECT-SINGLE-R12] [%s] 调用 provider.collect()...", participant_id)
                logger.info("[COLLECT-SINGLE-R12] [%s]   provider_id: %d", participant_id, id(self._provider))
                logger.info("[COLLECT-SINGLE-R12] [%s]   provider_type: %s", participant_id, type(self._provider).__name__)

                perspective = self._provider.collect(context)

                elapsed = time.time() - thread_start
                participant_timings[participant_id] = elapsed

                # R12-3: 检查 perspective 结果
                if perspective is None:
                    logger.error("[COLLECT-SINGLE-R12] [%s] ⚠️ provider.collect() 返回 None！", participant_id)
                    perspective = Perspective(
                        participant_id=participant_id,
                        participant_type="bot",
                        role="consultant",
                        summary="provider.collect() returned None",
                        confidence=0.0,
                        evidence=[],
                        status="failed",
                    )

                logger.info("[COLLECT-SINGLE-R12] [%s] 收集成功: status=%s, elapsed=%.3fs",
                           participant_id, perspective.status, elapsed)
                logger.info("[COLLECT-SINGLE-R12] [%s] perspective_metadata: %s",
                           participant_id, perspective.metadata if hasattr(perspective, 'metadata') else "N/A")
                logger.info("[PERF-Parallel] [%s] 收集完成: status=%s, 耗时=%.3fs",
                           participant_id, perspective.status, elapsed)
                return perspective

            except Exception as e:
                elapsed = time.time() - thread_start
                participant_timings[participant_id] = elapsed

                # R12-3: 详细的异常诊断
                logger.error("[COLLECT-SINGLE-R12] [%s] ⚠️ 收集异常: %s", participant_id, str(e))
                logger.error("[COLLECT-SINGLE-R12] [%s]   异常类型: %s", participant_id, type(e).__name__)
                logger.error("[COLLECT-SINGLE-R12] [%s]   异常消息: %s", participant_id, str(e))
                logger.error("[COLLECT-SINGLE-R12] [%s]   耗时: %.3fs", participant_id, elapsed)
                logger.error("[COLLECT-SINGLE-R12] [%s]   Traceback:", participant_id)
                for line in traceback.format_exc().split('\n'):
                    logger.error("[COLLECT-SINGLE-R12] [%s]     %s", participant_id, line)

                logger.error("[PERF-Parallel] [%s] 收集异常: %s, 耗时=%.3fs", participant_id, str(e), elapsed)

                # 返回失败的 perspective 而不是抛出异常
                return Perspective(
                    participant_id=participant_id,
                    participant_type="bot",
                    role="consultant",
                    summary=f"视角收集异常: {str(e)}",
                    confidence=0.0,
                    evidence=[],
                    status="failed",
                )

        # 使用 ThreadPoolExecutor 并行收集
        # max_workers 控制并发数，避免对下游服务造成过大压力
        parallel_start = time.time()
        max_workers = min(len(online_participants), self._max_parallel_workers)
        logger.info("[PERF-Parallel] 启动并行收集: participants=%d, workers=%d",
                   len(online_participants), max_workers)

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            # 提交所有任务
            future_to_participant = {
                executor.submit(collect_single, pid): pid
                for pid in online_participants
            }

            # 收集结果（按完成顺序）
            for future in as_completed(future_to_participant):
                participant_id = future_to_participant[future]
                try:
                    perspective = future.result()
                    results.append(perspective)

                    # 记录状态警告
                    if perspective.status == "timed_out":
                        warnings.append(f"participant {participant_id} timed out")
                        logger.warning("[Perspectives-Parallel] %s 超时", participant_id)
                    elif perspective.status == "failed":
                        warnings.append(f"participant {participant_id} failed")
                        logger.warning("[Perspectives-Parallel] %s 失败", participant_id)
                    elif perspective.status == "skipped":
                        warnings.append(f"participant {participant_id} was skipped")
                        logger.warning("[Perspectives-Parallel] %s 被跳过", participant_id)

                except Exception as e:
                    logger.error("[Perspectives-Parallel] %s 获取结果异常: %s", participant_id, str(e))
                    warnings.append(f"participant {participant_id} encountered error: {str(e)}")
                    # 创建失败的 perspective
                    results.append(Perspective(
                        participant_id=participant_id,
                        participant_type="bot",
                        role="consultant",
                        summary=f"Failed to collect perspective: {str(e)}",
                        confidence=0.0,
                        evidence=[],
                        status="failed",
                    ))

        parallel_elapsed = time.time() - parallel_start
        logger.info("[PERF-Parallel] ========== 并行收集完成 ==========")
        logger.info("[PERF-Parallel] 总耗时: %.3fs, 成功: %d/%d", parallel_elapsed, len(results), len(online_participants))
        if participant_timings:
            max_time = max(participant_timings.values())
            min_time = min(participant_timings.values())
            avg_time = sum(participant_timings.values()) / len(participant_timings)
            logger.info("[PERF-Parallel] 单线程耗时: max=%.3fs, min=%.3fs, avg=%.3fs", max_time, min_time, avg_time)
            for pid, t in sorted(participant_timings.items(), key=lambda x: -x[1]):
                logger.info("[PERF-Parallel]   %s: %.3fs", pid, t)

        return results

    def _generate_llm_recommendation(
        self,
        perspectives: list[Perspective],
        question: str,
        driver_bot_id: Optional[str],
        partial_success: bool,
        warnings: list[str],
    ) -> Recommendation:
        """
        通过 LLM 生成建议

        使用 FusionRecommendationService 生成建议，如果失败则回退到规则方法。

        Args:
            perspectives: 收集到的视角列表
            question: 原始问题
            driver_bot_id: Driver bot ID
            partial_success: 是否部分成功
            warnings: 警告列表

        Returns:
            Recommendation: 综合建议
        """
        try:
            # 调用 FusionRecommendationService
            fusion_rec = self._recommendation_service.generate(
                question=question,
                driver_bot_id=driver_bot_id,
                perspectives=perspectives,
                partial_success=partial_success,
                warnings=warnings,
            )

            # 转换为 Recommendation
            return Recommendation(
                summary=fusion_rec.summary,
                decision=fusion_rec.decision.value,
                risks=fusion_rec.risks,
                next_actions=fusion_rec.next_actions,
            )

        except Exception:
            # LLM 调用失败，回退到规则方法
            return self._synthesize_recommendation(
                perspectives=perspectives,
                question=question,
            )

    def _synthesize_recommendation(
        self,
        perspectives: list[Perspective],
        question: str,
    ) -> Recommendation:
        """
        综合生成建议

        基于 perspectives 生成 recommendation。
        MVP 版本使用规则基础的方法。

        Args:
            perspectives: 收集到的视角列表
            question: 原始问题

        Returns:
            Recommendation: 综合建议
        """
        # 统计状态
        completed = [p for p in perspectives if p.status == "completed"]
        failed = [p for p in perspectives if p.status in ("failed", "timed_out", "skipped")]

        # 计算平均置信度
        confidences = [p.confidence for p in completed if p.confidence is not None]
        avg_confidence = sum(confidences) / len(confidences) if confidences else 0.5

        # 汇总风险
        risks: list[str] = []
        for p in failed:
            risks.append(f"{p.participant_id} 视角缺失")

        # 汇总下一步行动
        next_actions: list[str] = []
        for p in perspectives:
            if p.status == "completed" and p.summary:
                # 从 summary 中提取可能的行动建议
                if "需要" in p.summary or "建议" in p.summary:
                    next_actions.append(f"跟进 {p.participant_id} 的建议")

        # 确定决策
        if len(failed) == 0 and avg_confidence >= 0.8:
            decision = "yes"
            summary = "各方视角一致认为方案可行。"
        elif len(failed) == 0 and avg_confidence >= 0.6:
            decision = "conditional_yes"
            summary = "方案基本可行，但存在部分顾虑需要关注。"
        elif len(completed) > 0:
            decision = "conditional_yes"
            summary = f"基于 {len(completed)} 个成功视角，方案可推进，但需补齐缺失视角。"
        else:
            decision = "needs_more_information"
            summary = "所有视角收集失败，无法做出判断。"

        # 生成摘要
        if completed:
            summaries = [f"{p.participant_id}: {p.summary[:50]}..." if len(p.summary) > 50 else f"{p.participant_id}: {p.summary}" for p in completed[:3]]
            summary = f"综合 {len(completed)} 个视角：{summaries[0] if summaries else ''}"

        return Recommendation(
            summary=summary,
            decision=decision,
            risks=risks,
            next_actions=next_actions,
        )


__all__ = [
    "GroupFusionService",
]