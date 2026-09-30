"""
G5ExpertEnhancerImpl

Stage 3: Worker Profile-Driven Expert Execution Preparation

G5 专家视角增强实现。

职责分层:
- Layer 1 (Candidate Retrieval): 检索相关专家 profile
- Layer 2 (Context Preparation): 准备上下文、构建 ExpertContextPack
- Layer 3 (LLM Generation): 调用 LLM、解析、fallback

约束:
- 仅用于 G5 模式
- 不改变 G1/G2 的行为
- 支持 fallback
- 保持 profile_key traceability
"""

from __future__ import annotations

import logging
import os
from datetime import datetime
from typing import TYPE_CHECKING, Optional

from src.domain.models.fusion_result import Perspective
from src.domain.models.expert_context_pack import ExpertContextPack
from src.domain.models.llm_expert_perspective import LLMExpertPerspective
from src.domain.models.llm_request import LLMRequest
from src.domain.models.llm_task_spec import LLMTaskSpec, TaskType
from src.domain.models.retrieval_mode import RetrievalMode
from src.infra.llm.prompts.expert_perspective_prompt import build_expert_perspective_prompt

if TYPE_CHECKING:
    from src.application.services.llm_gateway_service import LLMGatewayService
    from src.domain.services.worker_profile_retrieval_service import (
        WorkerProfileRetrievalService,
        RetrievalResult,
    )
    from src.domain.services.worker_context_preparation_service import WorkerContextPreparationService
    from src.domain.services.worker_profile_source import WorkerProfileSource
    from src.domain.models.worker_profile import WorkerProfile
    from src.domain.models.worker_context_digest import WorkerContextDigest


logger = logging.getLogger(__name__)


def _get_max_workers() -> int:
    """从配置获取最大并发数，默认5"""
    try:
        value = os.environ.get("LLM_PARALLEL_MAX_WORKERS", "5")
        return int(value)
    except (ValueError, TypeError):
        return 5


class G5ExpertEnhancerImpl:
    """
    G5 Expert Enhancer 实现

    使用 Worker Profile 和 LLM 增强 G5 专家视角。

    分层职责：
    - Layer 1 (Candidate Retrieval): _retrieve_candidate_profiles()
    - Layer 2 (Context Preparation): _prepare_context_digest(), _build_expert_context_pack()
    - Layer 3 (LLM Generation): _generate_perspective_via_llm(), _generate_fallback_perspective()

    Attributes:
        _gateway: LLM Gateway 服务
        _retrieval_service: Worker Profile 检索服务
        _preparation_service: 上下文准备服务
        _profile_source: Profile 来源
        _max_experts: 最大专家数量
    """

    def __init__(
        self,
        gateway: "LLMGatewayService",
        retrieval_service: "WorkerProfileRetrievalService",
        preparation_service: "WorkerContextPreparationService",
        profile_source: "WorkerProfileSource",
        max_experts: int = 3,
    ):
        """
        初始化 G5 Expert Enhancer

        Args:
            gateway: LLM Gateway 服务
            retrieval_service: Worker Profile 检索服务
            preparation_service: 上下文准备服务
            profile_source: Profile 来源
            max_experts: 最大专家数量（默认 3）
        """
        self._gateway = gateway
        self._retrieval_service = retrieval_service
        self._preparation_service = preparation_service
        self._profile_source = profile_source
        self._max_experts = max_experts

    # =========================================================================
    # Main Entry Point
    # =========================================================================

    def enhance(
        self,
        question: str,
        base_perspectives: list[Perspective],
        participants: Optional[list[str]] = None,
        driver_bot_id: Optional[str] = None,
        strict_participants: bool = False,
    ) -> list[Perspective]:
        """
        增强 G5 专家视角

        编排三层的完整流程。

        Args:
            question: 待诊断的问题
            base_perspectives: 基础视角列表（原有 provider 收集的）
            participants: 参与者列表（推荐专家 profile_keys）
            driver_bot_id: Driver bot ID
            strict_participants: 是否启用严格参与者模式
                - False（默认）: 参与者过滤失败时允许 fallback 到全库检索
                - True: 参与者过滤失败时禁止 fallback，返回 base_perspectives

        Returns:
            list[Perspective]: 增强后的视角列表
        """
        import os
        logger.info("[G5-ENHANCER] ========== enhance() 开始 ==========")
        logger.info("[G5-ENHANCER] PID: %d", os.getpid())
        logger.info("[G5-ENHANCER] question_len: %d, preview: %s", len(question), question[:80] if len(question) > 80 else question)
        logger.info("[G5-ENHANCER] base_perspectives: %d 个", len(base_perspectives))
        logger.info("[G5-ENHANCER] participants: %s", participants)
        logger.info("[G5-ENHANCER] driver_bot_id: %s", driver_bot_id)
        logger.info("[G5-ENHANCER] strict_participants: %s", strict_participants)

        # 检查内部状态
        logger.info("[G5-ENHANCER] 内部状态检查:")
        logger.info("[G5-ENHANCER]   _gateway 类型: %s", type(self._gateway).__name__ if self._gateway else "None")
        logger.info("[G5-ENHANCER]   _gateway id: %d", id(self._gateway) if self._gateway else 0)
        logger.info("[G5-ENHANCER]   _retrieval_service 类型: %s", type(self._retrieval_service).__name__ if self._retrieval_service else "None")
        logger.info("[G5-ENHANCER]   _preparation_service 类型: %s", type(self._preparation_service).__name__ if self._preparation_service else "None")
        logger.info("[G5-ENHANCER]   _profile_source 类型: %s", type(self._profile_source).__name__ if self._profile_source else "None")
        logger.info("[G5-ENHANCER]   _max_experts: %d", self._max_experts)

        # Layer 1: 检索候选专家 profile
        logger.info("[G5-ENHANCER] ---------- Layer 1: 检索候选专家 profile ----------")

        # ========== Phase R7-1-C: Trace Retrieval Input ==========
        logger.info("[G5-TRACE] ========== TRACE-C: G5Enhancer Retrieval ==========")
        logger.info("[G5-TRACE] enhance_input_participants_count: %d", len(participants) if participants else 0)
        logger.info("[G5-TRACE] enhance_input_profile_keys: %s", participants)
        logger.info("[G5-TRACE] strict_participants: %s", strict_participants)
        logger.info("[G5-TRACE] ========== TRACE-C INPUT END ==========")

        profiles = self._retrieve_candidate_profiles(question, participants, strict_participants)

        # ========== Phase R7-1-C: Trace Retrieval Output ==========
        logger.info("[G5-TRACE] ========== TRACE-C: Retrieval Results ==========")
        logger.info("[G5-TRACE] retrieval_response_profile_count: %d", len(profiles) if profiles else 0)
        if profiles:
            logger.info("[G5-TRACE] retrieval_response_profile_keys: %s", [p.profile_key for p in profiles])
            # Safely extract worker_id from profile_key
            worker_ids = []
            for p in profiles:
                if hasattr(p, 'worker_id'):
                    worker_ids.append(p.worker_id)
                elif ':' in p.profile_key:
                    worker_ids.append(p.profile_key.split(':')[0])
                else:
                    worker_ids.append(p.profile_key)
            logger.info("[G5-TRACE] retrieval_response_worker_ids: %s", worker_ids)
            logger.info("[G5-TRACE] retrieval_response_roles: %s", [getattr(p, 'role', 'unknown') for p in profiles])
            for i, p in enumerate(profiles[:10]):  # Limit to first 10
                logger.info("[G5-TRACE]   retrieved_profile[%d]: key=%s, role=%s, skills=%d",
                           i, p.profile_key, getattr(p, 'role', 'unknown'),
                           len(p.active_skills) if p.active_skills else 0)
        logger.info("[G5-TRACE] ========== TRACE-C OUTPUT END ==========")

        if not profiles:
            # 检查 base_perspectives 是否全是 skipped
            all_skipped = all(p.status == "skipped" for p in base_perspectives) if base_perspectives else False
            has_usable_base = any(p.status in ("completed", "partial") for p in base_perspectives) if base_perspectives else False

            if strict_participants:
                logger.warning("[G5-ENHANCER] ⚠️ strict 模式: 没有找到任何 profile，返回空列表（不回退 base_perspectives）")
                logger.info("[G5-ENHANCER] strict 模式语义: 确保结果只来自显式指定的可用 participants")
            elif all_skipped:
                logger.warning("[G5-ENHANCER] ⚠️ 没有 profile 且 base_perspectives 全是 skipped，返回空列表")
                logger.info("[G5-ENHANCER] 不回退到 skipped perspectives，保持结果语义清晰")
            elif has_usable_base:
                logger.warning("[G5-ENHANCER] ⚠️ 没有找到任何 profile，回退到 base_perspectives（有可用视角）")
                logger.info("[G5-ENHANCER] ========== enhance() 结束 (回退 base) ==========")
                return base_perspectives
            else:
                logger.warning("[G5-ENHANCER] ⚠️ 没有找到任何 profile 且 base_perspectives 无可用视角，返回空列表")

            logger.info("[G5-ENHANCER] ========== enhance() 结束 (无 profile) ==========")
            return []  # 返回空列表而不是 base_perspectives

        logger.info("[G5-ENHANCER] 检索到 %d 个 profile", len(profiles))

        # Phase 2.7.2: 当有明确的 participants 时，处理所有检索到的 profile
        # 而不是限制为 _max_experts
        if participants and len(participants) > 0:
            max_profiles_to_process = len(participants)
            logger.info("[G5-ENHANCER] Phase 2.7.2: Processing %d profiles (participants count, not limited by _max_experts=%d)",
                       min(len(profiles), max_profiles_to_process), self._max_experts)
        else:
            max_profiles_to_process = self._max_experts
            logger.info("[G5-ENHANCER] No explicit participants, using _max_experts=%d", self._max_experts)

        for i, p in enumerate(profiles[:max_profiles_to_process]):
            logger.info("[G5-ENHANCER]   profile[%d]: key=%s, id=%s, skills=%d",
                       i, p.profile_key, p.profile_id, len(p.active_skills) if p.active_skills else 0)

        # Layer 2 & 3: 并发生成视角
        logger.info("[G5-ENHANCER] ---------- Layer 2&3: 并发生成视角 ----------")
        enhanced_perspectives: list[Perspective] = []

        # 使用 ThreadPoolExecutor 并发执行
        import concurrent.futures
        profiles_to_process = profiles[:max_profiles_to_process]
        logger.info("[G5-ENHANCER] 并发处理 %d 个 profile", len(profiles_to_process))

        layer_start = datetime.now()

        # 从配置获取并发数上限，动态调整
        config_max_workers = _get_max_workers()
        max_workers = min(len(profiles_to_process), config_max_workers)
        logger.info("[G5-ENHANCER] 并发配置: max_workers=%d (config=%d)", max_workers, config_max_workers)

        # ========== Phase R10-2: Profile Processing Table ==========
        profile_processing_results = []

        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            # 提交所有任务
            future_to_profile = {
                executor.submit(self._generate_perspective_for_profile, question, profile): profile
                for profile in profiles_to_process
            }

            # 收集结果
            for future in concurrent.futures.as_completed(future_to_profile):
                profile = future_to_profile[future]
                logger.info("[G5-ENHANCER] 处理完成: %s", profile.profile_key)
                try:
                    perspective = future.result()
                    if perspective:
                        enhanced_perspectives.append(perspective)
                        logger.info("[G5-ENHANCER]   -> 生成成功: id=%s, status=%s, summary_len=%d",
                                   perspective.participant_id, perspective.status,
                                   len(perspective.summary) if perspective.summary else 0)
                        # Track success
                        profile_processing_results.append({
                            "profile_key": profile.profile_key,
                            "worker_id": worker_id if 'worker_id' in locals() else (profile.worker_id if hasattr(profile, 'worker_id') else profile.profile_key.split(':')[0] if ':' in profile.profile_key else profile.profile_key),
                            "status": "success",
                            "perspective_id": perspective.participant_id,
                        })
                    else:
                        logger.warning("[G5-ENHANCER]   -> 生成返回 None")
                        profile_processing_results.append({
                            "profile_key": profile.profile_key,
                            "worker_id": worker_id if 'worker_id' in locals() else (profile.worker_id if hasattr(profile, 'worker_id') else profile.profile_key.split(':')[0] if ':' in profile.profile_key else profile.profile_key),
                            "status": "failed",
                            "perspective_id": None,
                            "reason": "returned_none",
                        })
                except Exception as e:
                    logger.error("[G5-ENHANCER]   -> 生成失败: %s", e, exc_info=True)
                    profile_processing_results.append({
                        "profile_key": profile.profile_key,
                        "worker_id": worker_id if 'worker_id' in locals() else (profile.worker_id if hasattr(profile, 'worker_id') else profile.profile_key.split(':')[0] if ':' in profile.profile_key else profile.profile_key),
                        "status": "exception",
                        "perspective_id": None,
                        "reason": str(e),
                    })

        layer_elapsed = (datetime.now() - layer_start).total_seconds()
        logger.info("[G5-ENHANCER] 并发生成完成，耗时: %.2fs, 成功 %d 个", layer_elapsed, len(enhanced_perspectives))

        # ========== Phase R10-2: Profile Processing Summary Table ==========
        logger.info("[G5-TRACE] ========== PROFILE PROCESSING SUMMARY ==========")
        logger.info("[G5-TRACE] Total profiles processed: %d", len(profile_processing_results))
        logger.info("[G5-TRACE] Successful perspectives: %d", len(enhanced_perspectives))
        logger.info("[G5-TRACE] Profile Processing Table:")
        for i, result in enumerate(profile_processing_results):
            logger.info("[G5-TRACE]   [%d] profile_key=%s | worker_id=%s | status=%s | perspective_id=%s",
                       i, result.get("profile_key", "N/A"), result.get("worker_id", "N/A"),
                       result.get("status", "N/A"), result.get("perspective_id", "N/A"))
            if result.get("reason"):
                logger.info("[G5-TRACE]       reason: %s", result.get("reason"))
        logger.info("[G5-TRACE] ========== END PROFILE PROCESSING SUMMARY ==========")

        # ========== Phase R7-1-D: Trace Enhanced Perspectives ==========
        logger.info("[G5-TRACE] ========== TRACE-D: G5Enhancer Output Perspectives ==========")
        logger.info("[G5-TRACE] enhanced_perspectives_count: %d", len(enhanced_perspectives))
        if enhanced_perspectives:
            logger.info("[G5-TRACE] enhanced_perspective_participant_ids: %s", [p.participant_id for p in enhanced_perspectives])
            logger.info("[G5-TRACE] enhanced_perspective_roles: %s", [p.role for p in enhanced_perspectives])
            llm_generated = sum(1 for p in enhanced_perspectives if "llm" in p.participant_id.lower() or p.metadata.get("llm_success"))
            fallback_used = sum(1 for p in enhanced_perspectives if p.summary and "fallback" in p.summary.lower())
            logger.info("[G5-TRACE] llm_generated_perspective_count: %d", llm_generated)
            logger.info("[G5-TRACE] fallback_perspective_count: %d", fallback_used)
            for i, p in enumerate(enhanced_perspectives[:10]):  # Limit to first 10
                logger.info("[G5-TRACE]   enhanced_perspective[%d]: id=%s, role=%s, status=%s, summary_len=%d",
                           i, p.participant_id, p.role, p.status, len(p.summary) if p.summary else 0)
        logger.info("[G5-TRACE] ========== TRACE-D END ==========")

        if not enhanced_perspectives:
            # 检查 base_perspectives 是否全是 skipped
            all_skipped = all(p.status == "skipped" for p in base_perspectives) if base_perspectives else False
            has_usable_base = any(p.status in ("completed", "partial") for p in base_perspectives) if base_perspectives else False

            if strict_participants:
                logger.warning("[G5-ENHANCER] ⚠️ strict 模式: 所有 profile 生成失败，返回空列表（不回退 base_perspectives）")
            elif all_skipped:
                logger.warning("[G5-ENHANCER] ⚠️ 所有 profile 生成失败且 base_perspectives 全是 skipped，返回空列表")
            elif has_usable_base:
                logger.warning("[G5-ENHANCER] ⚠️ 所有 profile 生成失败，回退到 base_perspectives（有可用视角）")
                logger.info("[G5-ENHANCER] ========== enhance() 结束 (回退 base) ==========")
                return base_perspectives
            else:
                logger.warning("[G5-ENHANCER] ⚠️ 所有 profile 生成失败且无可用 base_perspectives，返回空列表")

            logger.info("[G5-ENHANCER] ========== enhance() 结束 (全部失败) ==========")
            return []

        logger.info("[G5-ENHANCER] 成功生成 %d 个视角", len(enhanced_perspectives))

        # ========== Phase 2.7.1: Preserve Skipped Status from base_perspectives ==========
        # Build a map of participant_id -> Perspective from base_perspectives
        base_perspective_map = {p.participant_id: p for p in base_perspectives}
        logger.info("[G5-TRACE] base_perspective_map keys: %s", list(base_perspective_map.keys()))
        for pid, p in base_perspective_map.items():
            logger.info("[G5-TRACE]   base_perspective[%s]: status=%s, role=%s", pid, p.status, p.role)

        # Merge: preserve skipped status from base_perspectives
        merged_perspectives = []
        enhanced_participant_ids = set()

        for enhanced in enhanced_perspectives:
            enhanced_participant_ids.add(enhanced.participant_id)
            base = base_perspective_map.get(enhanced.participant_id)
            if base and base.status == "skipped":
                # Preserve the skipped perspective from base
                logger.info("[G5-TRACE] Preserving skipped perspective for %s", enhanced.participant_id)
                merged_perspectives.append(base)
            else:
                # Use the enhanced perspective
                merged_perspectives.append(enhanced)

        # Add any base_perspectives that were skipped but not in enhanced_perspectives
        for base in base_perspectives:
            if base.participant_id not in enhanced_participant_ids:
                if base.status == "skipped":
                    logger.info("[G5-TRACE] Adding skipped perspective not in enhanced: %s", base.participant_id)
                    merged_perspectives.append(base)

        logger.info("[G5-TRACE] Merged perspectives count: %d (enhanced: %d, base: %d)",
                   len(merged_perspectives), len(enhanced_perspectives), len(base_perspectives))
        for i, p in enumerate(merged_perspectives):
            logger.info("[G5-TRACE]   merged_perspective[%d]: id=%s, status=%s, role=%s",
                       i, p.participant_id, p.status, p.role)

        logger.info("[G5-ENHANCER] ========== enhance() 结束 (成功) ==========")
        return merged_perspectives

    # =========================================================================
    # Layer 1: Candidate Retrieval
    # =========================================================================

    def _retrieve_candidate_profiles(
        self,
        question: str,
        participants: Optional[list[str]] = None,
        strict_participants: bool = False,
    ) -> list["WorkerProfile"]:
        """
        Layer 1: 检索候选专家 profile

        策略:
        1. 如果 participants 有值，优先使用推荐的专家 profile_keys
        2. 如果 participants 为空或检索失败：
           - strict_participants=False: fallback 到全库检索
           - strict_participants=True: 返回空列表，禁止 fallback

        Args:
            question: 问题
            participants: 推荐的专家 profile_keys（格式: "worker_id:profile_id"）
            strict_participants: 是否启用严格参与者模式

        Returns:
            list[WorkerProfile]: 检索到的 profile 列表
        """
        logger.info("[G5-ENHANCER-L1] ========== _retrieve_candidate_profiles 开始 ==========")
        logger.info("[G5-ENHANCER-L1] question_len: %d", len(question))
        logger.info("[G5-ENHANCER-L1] participants: %s", participants)
        logger.info("[G5-ENHANCER-L1] strict_participants: %s", strict_participants)
        logger.info("[G5-ENHANCER-L1] _retrieval_service 类型: %s", type(self._retrieval_service).__name__)
        logger.info("[G5-ENHANCER-L1] _retrieval_service id: %d", id(self._retrieval_service))
        logger.info("[G5-ENHANCER-L1] RetrievalMode: %s", RetrievalMode.EXPERT_DIAGNOSIS)
        logger.info("[G5-ENHANCER-L1] top_k: %d", self._max_experts)

        # 优先使用推荐的专家
        if participants and len(participants) > 0:
            logger.info("[G5-ENHANCER-L1] 使用推荐的专家 profile_keys: %s", participants)

            try:
                retrieval_start = datetime.now()
                logger.info("[G5-ENHANCER-L1] 调用 retrieve() 使用 profile_keys...")

                # Phase 2.7.2: 当有明确的 participants 时，top_k 应该等于 len(participants)
                # 以确保所有推荐的专家都能被检索和增强
                actual_top_k = len(participants)
                logger.info("[G5-ENHANCER-L1] Phase 2.7.2: Using top_k=%d (participants count) instead of _max_experts=%d",
                           actual_top_k, self._max_experts)

                # 使用推荐的 profile_keys，并传递 strict_participants
                retrieval_result = self._retrieval_service.retrieve(
                    question=question,
                    mode=RetrievalMode.EXPERT_DIAGNOSIS,
                    profile_keys=participants,
                    top_k=actual_top_k,
                    strict_participants=strict_participants,
                )

                retrieval_elapsed = (datetime.now() - retrieval_start).total_seconds()
                logger.info("[G5-ENHANCER-L1] retrieve() 完成，耗时: %.2fs", retrieval_elapsed)
                logger.info("[G5-ENHANCER-L1] retrieval_result 类型: %s", type(retrieval_result).__name__)

                if retrieval_result and retrieval_result.results:
                    logger.info("[G5-ENHANCER-L1] retrieval_result.results 数量: %d", len(retrieval_result.results))
                    profiles = [r.profile for r in retrieval_result.results]
                    logger.info("[G5-ENHANCER-L1] ✅ 提取到 %d 个推荐专家 profile", len(profiles))
                    return profiles
                else:
                    # 检索返回空
                    if strict_participants:
                        logger.error("[G5-ENHANCER-L1] ❌ 严格模式：推荐专家 profile_keys 过滤后为空，禁止 fallback")
                        return []
                    else:
                        logger.warning("[G5-ENHANCER-L1] ⚠️ 使用推荐专家检索返回空，fallback 到全库检索 [degraded]")

            except Exception as e:
                logger.error("[G5-ENHANCER-L1] ❌ 使用推荐专家检索失败: %s", e, exc_info=True)
                if strict_participants:
                    logger.error("[G5-ENHANCER-L1] ❌ 严格模式：禁止 fallback")
                    return []
                # 兼容模式：继续 fallback
        else:
            # participants 为空
            if strict_participants:
                logger.warning("[G5-ENHANCER-L1] ⚠️ 严格模式：participants 为空，允许全库检索")
            else:
                logger.info("[G5-ENHANCER-L1] participants 为空，执行全库检索")

        # Fallback: 全库检索
        logger.info("[G5-ENHANCER-L1] Fallback: 执行全库检索")
        try:
            retrieval_start = datetime.now()
            logger.info("[G5-ENHANCER-L1] 调用 retrieve() 全库检索...")

            retrieval_result = self._retrieval_service.retrieve(
                question=question,
                mode=RetrievalMode.EXPERT_DIAGNOSIS,
                top_k=self._max_experts,
            )

            retrieval_elapsed = (datetime.now() - retrieval_start).total_seconds()
            logger.info("[G5-ENHANCER-L1] retrieve() 完成，耗时: %.2fs", retrieval_elapsed)
            logger.info("[G5-ENHANCER-L1] retrieval_result 类型: %s", type(retrieval_result).__name__)

            if retrieval_result is None:
                logger.error("[G5-ENHANCER-L1] ❌ retrieval_result 为 None!")
                return []

            logger.info("[G5-ENHANCER-L1] retrieval_result.results 数量: %d",
                       len(retrieval_result.results) if retrieval_result.results else 0)

            profiles = [r.profile for r in retrieval_result.results]
            logger.info("[G5-ENHANCER-L1] 提取到 %d 个 profile", len(profiles))

            return profiles
        except Exception as e:
            logger.error("[G5-ENHANCER-L1] ❌ 全库检索失败: %s", e, exc_info=True)
            return []

    # =========================================================================
    # Layer 2: Context Preparation
    # =========================================================================

    def _prepare_context_digest(
        self,
        profile: "WorkerProfile",
        question: str,
    ) -> "WorkerContextDigest":
        """
        Layer 2: 准备上下文 digest

        Args:
            profile: Worker Profile
            question: 问题

        Returns:
            WorkerContextDigest: 准备好的上下文 digest
        """
        logger.debug(f"[G5Enhancer] Preparing context digest for profile={profile.profile_key}, question_len={len(question)}")
        try:
            result = self._preparation_service.prepare(
                profile=profile,
                question=question,  # 修复：参数名是 question 而非 task_description
                mode=RetrievalMode.EXPERT_DIAGNOSIS,
            )
            logger.info(f"[G5Enhancer] Context digest prepared: profile={profile.profile_key}, "
                       f"fragments={result.selected_fragments}/{result.total_fragments}, "
                       f"skills={result.selected_skills}/{result.total_skills}")
            return result
        except Exception as e:
            logger.error(f"[G5Enhancer] Context preparation FAILED for profile={profile.profile_key}: {e}", exc_info=True)
            raise

    def _build_expert_context_pack(
        self,
        question: str,
        digest: "WorkerContextDigest",
        domain: str,
    ) -> ExpertContextPack:
        """
        Layer 2: 构建 ExpertContextPack

        Args:
            question: 问题
            digest: Worker Context Digest
            domain: 领域

        Returns:
            ExpertContextPack
        """
        # 构建 profile_key（包含 timestamp 用于 traceability）
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        profile_key = f"{digest.profile_key}:{timestamp}"

        # 提取技能名称
        relevant_skills = [s.name for s in digest.relevant_skills]

        # 提取上下文要点
        context_highlights = [
            f.content[:200] for f in digest.relevant_fragments[:5]
        ]

        pack = ExpertContextPack(
            question=question,
            expert_id=digest.profile_key,
            profile_key=profile_key,
            domain=domain,
            expertise_summary=digest.context_summary or "Expert profile",
            relevant_skills=relevant_skills,
            context_highlights=context_highlights,
            task_context=digest.question,
        )

        # 记录 traceability
        logger.info(f"G5ExpertEnhancer: built context pack with profile_key={pack.profile_key}")

        return pack

    def _build_richer_expert_context_pack(
        self,
        question: str,
        profile: "WorkerProfile",
        digest: "WorkerContextDigest",
        domain: str,
    ) -> ExpertContextPack:
        """
        Layer 2: 构建更丰富的 ExpertContextPack

        使用评分和选择逻辑，选择与问题最相关的内容。

        Args:
            question: 问题
            profile: Worker Profile
            digest: Worker Context Digest
            domain: 领域

        Returns:
            ExpertContextPack
        """
        # 构建 profile_key（包含 timestamp 用于 traceability）
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        profile_key = f"{digest.profile_key}:{timestamp}"

        # 使用评分选择最相关的 context highlights
        all_fragments = profile.context_fragments
        context_highlights = self._select_context_highlights(
            fragments=all_fragments,
            question=question,
            max_highlights=5,
        )

        # 使用评分选择最相关的 skills
        all_skills = profile.active_skills
        relevant_skills_profiles = self._select_relevant_skills(
            skills=all_skills,
            question=question,
            max_skills=5,
        )
        relevant_skills = [s.name for s in relevant_skills_profiles]

        # 构建更准确的 expertise_summary
        expertise_summary = self._build_expertise_summary(
            profile=profile,
            digest=digest,
            relevant_skills=relevant_skills_profiles,
        )

        pack = ExpertContextPack(
            question=question,
            expert_id=digest.profile_key,
            profile_key=profile_key,
            domain=domain,
            expertise_summary=expertise_summary,
            relevant_skills=relevant_skills,
            context_highlights=context_highlights,
            task_context=digest.question,
        )

        # 记录 traceability
        logger.info(f"G5ExpertEnhancer: built richer context pack with profile_key={pack.profile_key}")

        return pack

    def _build_expertise_summary(
        self,
        profile: "WorkerProfile",
        digest: "WorkerContextDigest",
        relevant_skills: list,
    ) -> str:
        """
        构建专长摘要

        Args:
            profile: Worker Profile
            digest: Worker Context Digest
            relevant_skills: 相关的 SkillProfile 列表

        Returns:
            str: 专长摘要
        """
        parts = []

        # 1. 从 digest 获取摘要
        if digest.context_summary:
            parts.append(digest.context_summary)

        # 2. 从选中的 skills 补充
        if relevant_skills:
            skill_names = [s.name for s in relevant_skills[:3]]
            parts.append(f"Key skills: {', '.join(skill_names)}")

        # 3. 从 context fragments 补充
        if profile.context_fragments:
            for fragment in profile.context_fragments[:1]:
                if fragment.content:
                    # 提取前 100 字符
                    preview = fragment.content[:100].strip()
                    if preview:
                        parts.append(preview)

        if not parts:
            return "Expert profile"

        return " | ".join(parts)

    # =========================================================================
    # Layer 3: LLM Generation + Fallback
    # =========================================================================

    def _generate_perspective_for_profile(
        self,
        question: str,
        profile: "WorkerProfile",
    ) -> Optional[Perspective]:
        """
        为单个 profile 生成视角（编排 Layer 2 & 3）

        Args:
            question: 问题
            profile: Worker Profile

        Returns:
            Optional[Perspective]: 生成的视角（可能为 fallback 或 sparse context perspective）
        """
        # ========== Phase R10-2: Per-Profile Trace Start ==========
        import hashlib
        # Safely extract worker_id from profile_key
        worker_id = None
        if hasattr(profile, 'worker_id'):
            worker_id = profile.worker_id
        elif ':' in profile.profile_key:
            worker_id = profile.profile_key.split(':')[0]
        else:
            worker_id = profile.profile_key

        profile_trace = {
            "profile_key": profile.profile_key,
            "worker_id": worker_id,
            "role": getattr(profile, 'role', 'general'),
            "content_length": 0,
            "content_hash_prefix": "",
            "metadata_keys": [],
            "is_current_run": False,
            "pre_context_build_status": "not_started",
            "context_length": 0,
            "llm_call_attempted": False,
            "llm_call_success": False,
            "llm_error_type": "",
            "parse_success": False,
            "perspective_created": False,
            "drop_reason": "",
        }

        # Calculate content info
        if profile.context_fragments:
            total_content = "".join([f.content or "" for f in profile.context_fragments])
            profile_trace["content_length"] = len(total_content)
            if total_content:
                profile_trace["content_hash_prefix"] = hashlib.sha256(total_content.encode()).hexdigest()[:8]

        # Extract metadata keys
        if hasattr(profile, 'metadata') and profile.metadata:
            profile_trace["metadata_keys"] = list(profile.metadata.keys())

        # Check if current run
        import os
        run_id_prefix = os.environ.get("RUN_ID", "")
        if run_id_prefix and worker_id and worker_id.startswith(run_id_prefix):
            profile_trace["is_current_run"] = True

        logger.info("[G5-TRACE-PROFILE] ========== Profile Processing Start ==========")
        logger.info("[G5-TRACE-PROFILE] profile_key: %s", profile_trace["profile_key"])
        logger.info("[G5-TRACE-PROFILE] worker_id: %s", profile_trace["worker_id"])
        logger.info("[G5-TRACE-PROFILE] role: %s", profile_trace["role"])
        logger.info("[G5-TRACE-PROFILE] content_length: %d", profile_trace["content_length"])
        logger.info("[G5-TRACE-PROFILE] content_hash_prefix: %s", profile_trace["content_hash_prefix"])
        logger.info("[G5-TRACE-PROFILE] metadata_keys: %s", profile_trace["metadata_keys"])
        logger.info("[G5-TRACE-PROFILE] is_current_run: %s", profile_trace["is_current_run"])
        # ========== End Phase R10-2: Per-Profile Trace Start ==========

        logger.info("[G5-ENHANCER-GEN] ----- _generate_perspective_for_profile 开始 -----")
        logger.info("[G5-ENHANCER-GEN] profile_key: %s", profile.profile_key)
        logger.info("[G5-ENHANCER-GEN] profile_id: %s", profile.profile_id)
        logger.info("[G5-ENHANCER-GEN] profile 名称: %s", getattr(profile, 'name', 'N/A'))

        try:
            # Layer 2: 准备上下文
            logger.info("[G5-ENHANCER-GEN] Layer 2: 准备上下文 digest...")
            profile_trace["pre_context_build_status"] = "starting"
            digest = self._prepare_context_digest(profile, question)
            profile_trace["pre_context_build_status"] = "success"
            profile_trace["context_length"] = len(digest.context_summary) if digest.context_summary else 0
            logger.info("[G5-ENHANCER-GEN] digest 准备完成: fragments=%d, skills=%d",
                       getattr(digest, 'selected_fragments', 0), getattr(digest, 'selected_skills', 0))
            logger.info("[G5-TRACE-PROFILE] pre_context_build_status: %s", profile_trace["pre_context_build_status"])
            logger.info("[G5-TRACE-PROFILE] context_length: %d", profile_trace["context_length"])

            # ========= Preflight Check: Sparse Context Detection =========
            should_skip, skip_reason = _should_skip_llm_for_sparse_context(profile, digest)
            if should_skip:
                logger.info("[G5-ENHANCER-GEN] Preflight: sparse context detected, skipping LLM")
                profile_trace["llm_call_attempted"] = False
                profile_trace["drop_reason"] = f"sparse_context:{skip_reason}"
                logger.info("[G5-TRACE-PROFILE] llm_call_attempted: %s", profile_trace["llm_call_attempted"])
                logger.info("[G5-TRACE-PROFILE] drop_reason: %s", profile_trace["drop_reason"])
                logger.info("[G5-TRACE-PROFILE] ========== Profile Processing End (Sparse Context) ==========")
                return _build_sparse_context_perspective(profile, question, skip_reason)

            # ========= End Preflight Check =========

            # 使用新的领域推断方法（从 profile 的多维度推断）
            domain = self._infer_domain_from_profile(profile)
            logger.info("[G5-ENHANCER-GEN] 推断领域: %s", domain)

            # 构建更丰富的 context pack
            logger.info("[G5-ENHANCER-GEN] 构建 context_pack...")
            context_pack = self._build_richer_expert_context_pack(
                question=question,
                profile=profile,
                digest=digest,
                domain=domain,
            )
            logger.info("[G5-ENHANCER-GEN] context_pack 构建: expert_id=%s, domain=%s, skills=%d",
                       context_pack.expert_id, context_pack.domain, len(context_pack.relevant_skills))

            # Layer 3: LLM 生成
            logger.info("[G5-ENHANCER-GEN] Layer 3: 调用 LLM...")
            profile_trace["llm_call_attempted"] = True
            logger.info("[G5-TRACE-PROFILE] llm_call_attempted: %s", profile_trace["llm_call_attempted"])
            result = self._generate_perspective_via_llm(context_pack)

            # Trace LLM result
            profile_trace["llm_call_success"] = result.status == "completed"
            profile_trace["parse_success"] = result.status == "completed" and "fallback" not in getattr(result, 'summary', '').lower()
            profile_trace["perspective_created"] = True
            profile_trace["drop_reason"] = ""

            logger.info("[G5-ENHANCER-GEN] LLM 返回: participant_id=%s, status=%s",
                       result.participant_id, result.status)
            logger.info("[G5-TRACE-PROFILE] llm_call_success: %s", profile_trace["llm_call_success"])
            logger.info("[G5-TRACE-PROFILE] parse_success: %s", profile_trace["parse_success"])
            logger.info("[G5-TRACE-PROFILE] perspective_created: %s", profile_trace["perspective_created"])
            logger.info("[G5-TRACE-PROFILE] ========== Profile Processing End (Success) ==========")
            return result

        except Exception as e:
            logger.error("[G5-ENHANCER-GEN] ❌ 生成失败: %s", e, exc_info=True)

            # Trace exception
            profile_trace["llm_call_success"] = False
            profile_trace["parse_success"] = False
            profile_trace["perspective_created"] = False
            profile_trace["drop_reason"] = f"exception:{type(e).__name__}"
            if "Authentication failed" in str(e) or "401" in str(e):
                profile_trace["llm_error_type"] = "auth_error"
            elif "timeout" in str(e).lower():
                profile_trace["llm_error_type"] = "timeout"
            else:
                profile_trace["llm_error_type"] = "unknown"

            logger.info("[G5-TRACE-PROFILE] llm_call_success: %s", profile_trace["llm_call_success"])
            logger.info("[G5-TRACE-PROFILE] llm_error_type: %s", profile_trace["llm_error_type"])
            logger.info("[G5-TRACE-PROFILE] drop_reason: %s", profile_trace["drop_reason"])

            # 完全失败时的 fallback
            fallback = self._generate_fallback_perspective_from_profile(profile, question)
            profile_trace["perspective_created"] = True  # Fallback is still a perspective
            logger.info("[G5-ENHANCER-GEN] 使用 fallback: participant_id=%s", fallback.participant_id)
            logger.info("[G5-TRACE-PROFILE] perspective_created: %s (fallback)", profile_trace["perspective_created"])
            logger.info("[G5-TRACE-PROFILE] ========== Profile Processing End (Fallback) ==========")
            return fallback

    def _generate_perspective_via_llm(
        self,
        context_pack: ExpertContextPack,
    ) -> Perspective:
        """
        Layer 3: 通过 LLM 生成视角

        包含解析和 fallback 逻辑。

        Args:
            context_pack: Expert Context Pack

        Returns:
            Perspective: 生成的视角（可能为 fallback）
        """
        logger.info("[G5-ENHANCER-LLM] ----- _generate_perspective_via_llm 开始 -----")
        logger.info("[G5-ENHANCER-LLM] context_pack.expert_id: %s", context_pack.expert_id)
        logger.info("[G5-ENHANCER-LLM] context_pack.domain: %s", context_pack.domain)

        try:
            llm_start = datetime.now()
            logger.info("[G5-ENHANCER-LLM] 调用 _call_llm()...")
            llm_response = self._call_llm(context_pack)
            llm_elapsed = (datetime.now() - llm_start).total_seconds()
            logger.info("[G5-ENHANCER-LLM] _call_llm() 完成，耗时: %.2fs", llm_elapsed)

            logger.info("[G5-ENHANCER-LLM] llm_response 类型: %s", type(llm_response).__name__)
            logger.info("[G5-ENHANCER-LLM] llm_response.parse_success: %s", llm_response.parse_success)
            logger.info("[G5-ENHANCER-LLM] llm_response.structured_data 是否存在: %s",
                       llm_response.structured_data is not None)

            if llm_response.parse_success and llm_response.structured_data:
                # Layer 3a: 解析成功
                logger.info("[G5-ENHANCER-LLM] 解析成功，开始 model_validate...")
                llm_perspective = LLMExpertPerspective.model_validate(llm_response.structured_data)
                logger.info("[G5-ENHANCER-LLM] model_validate 成功: summary_len=%d",
                           len(llm_perspective.summary) if llm_perspective.summary else 0)
                perspective = self._convert_to_perspective(llm_perspective, context_pack.expert_id)
                logger.info("[G5-ENHANCER-LLM] ✅ LLM 成功生成视角")
                return perspective
            else:
                # Layer 3b: Parse failure fallback
                logger.warning("[G5-ENHANCER-LLM] ⚠️ 解析失败，使用 fallback")
                if hasattr(llm_response, 'raw_response'):
                    logger.warning("[G5-ENHANCER-LLM] raw_response 预览: %s",
                                 str(llm_response.raw_response)[:200] if llm_response.raw_response else "None")
                return self._generate_fallback_perspective(context_pack, reason="parse_failure")

        except Exception as e:
            # Layer 3c: LLM exception fallback
            logger.error("[G5-ENHANCER-LLM] ❌ LLM 异常: %s", e, exc_info=True)
            return self._generate_fallback_perspective(context_pack, reason="llm_exception")

    def _call_llm(self, context_pack: ExpertContextPack):
        """
        Layer 3: 调用 LLM

        Args:
            context_pack: Expert Context Pack

        Returns:
            LLMResponse
        """
        import os
        logger.info("[G5-ENHANCER-CALL] ----- _call_llm 开始 -----")
        logger.info("[G5-ENHANCER-CALL] PID: %d", os.getpid())

        # 检查 gateway 状态
        logger.info("[G5-ENHANCER-CALL] _gateway 类型: %s", type(self._gateway).__name__)
        logger.info("[G5-ENHANCER-CALL] _gateway id: %d", id(self._gateway))

        # 检查 LLM 环境变量
        llm_enabled = os.environ.get("LLM_ENABLED", "not_set")
        llm_base_url = os.environ.get("LLM_BASE_URL", "not_set")
        llm_auth_token_set = "set" if os.environ.get("LLM_AUTH_TOKEN") else "not_set"
        logger.info("[G5-ENHANCER-CALL] 环境变量 LLM_ENABLED: %s", llm_enabled)
        logger.info("[G5-ENHANCER-CALL] 环境变量 LLM_BASE_URL: %s", llm_base_url)
        logger.info("[G5-ENHANCER-CALL] 环境变量 LLM_AUTH_TOKEN: %s", llm_auth_token_set)

        # 构建 prompt
        logger.info("[G5-ENHANCER-CALL] 构建 prompt...")
        system_prompt, user_prompt = build_expert_perspective_prompt(
            question=context_pack.question,
            expert_id=context_pack.expert_id,
            domain=context_pack.domain,
            expertise_summary=context_pack.expertise_summary,
            relevant_skills=context_pack.relevant_skills,
            context_highlights=context_pack.context_highlights,
            task_context=context_pack.task_context,
        )
        logger.info("[G5-ENHANCER-CALL] system_prompt 长度: %d", len(system_prompt))
        logger.info("[G5-ENHANCER-CALL] user_prompt 长度: %d", len(user_prompt))
        logger.info("[G5-ENHANCER-CALL] user_prompt 预览: %s", user_prompt[:200] if len(user_prompt) > 200 else user_prompt)

        # 创建请求
        task_spec = LLMTaskSpec(
            task_type=TaskType.EXTRACTION,
            need_structured_output=True,
            require_explanation=False,
        )

        request = LLMRequest(
            task_spec=task_spec,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            expected_schema_name="ExpertPerspective",
            temperature=0.2,
            max_tokens=4096,
        )
        logger.info("[G5-ENHANCER-CALL] LLMRequest 创建完成")

        # 调用 gateway
        logger.info("[G5-ENHANCER-CALL] 调用 _gateway.generate()...")
        try:
            response = self._gateway.generate(request)
            logger.info("[G5-ENHANCER-CALL] _gateway.generate() 返回成功")
            return response
        except Exception as e:
            logger.error("[G5-ENHANCER-CALL] ❌ _gateway.generate() 失败: %s", e, exc_info=True)
            raise

    def _generate_fallback_perspective(
        self,
        context_pack: ExpertContextPack,
        reason: str = "unknown",
    ) -> Perspective:
        """
        Layer 3: 生成 fallback 视角

        Args:
            context_pack: Expert Context Pack
            reason: fallback 原因

        Returns:
            Perspective: fallback 视角
        """
        # 基于上下文生成简化的视角
        summary = f"[Fallback] Based on expertise in {context_pack.domain}"
        if context_pack.expertise_summary:
            summary = f"[Fallback] {context_pack.expertise_summary[:200]}"

        # 记录 fallback 原因
        logger.info(f"G5ExpertEnhancer: using fallback for profile_key={context_pack.profile_key}, reason={reason}")

        return Perspective(
            participant_id=context_pack.expert_id,
            participant_type="bot",
            role="expert",
            summary=summary,
            confidence=0.5,  # fallback 使用较低的置信度
            evidence=context_pack.context_highlights[:3],
            status="completed",
            key_points=[],
            concerns=[f"LLM generation failed ({reason}), using fallback perspective"],
        )

    def _generate_fallback_perspective_from_profile(
        self,
        profile: "WorkerProfile",
        question: str,
    ) -> Perspective:
        """
        Layer 3: 从 profile 生成 fallback 视角（完全失败时）

        Args:
            profile: Worker Profile
            question: 问题

        Returns:
            Perspective: fallback 视角
        """
        # 提取 profile 中的信息
        skills = [s.name for s in profile.active_skills[:3]]
        summary = f"[Fallback] Expert in {', '.join(skills)}" if skills else "[Fallback] Expert perspective"

        logger.info(f"G5ExpertEnhancer: using profile fallback for {profile.profile_key}")

        return Perspective(
            participant_id=profile.profile_key,
            participant_type="bot",
            role="expert",
            summary=summary,
            confidence=0.4,  # 更低的置信度
            evidence=[f"Has skills: {', '.join(skills)}"] if skills else [],
            status="completed",
            key_points=[],
            concerns=["Failed to generate enhanced perspective, using profile fallback"],
        )

    def _convert_to_perspective(
        self,
        llm_perspective: LLMExpertPerspective,
        expert_id: str,
    ) -> Perspective:
        """
        Layer 3: 将 LLMExpertPerspective 转换为 Perspective

        Args:
            llm_perspective: LLM 生成的专家视角
            expert_id: 专家 ID

        Returns:
            Perspective: 转换后的视角
        """
        return Perspective(
            participant_id=expert_id,
            participant_type="bot",
            role="expert",
            summary=llm_perspective.summary,
            confidence=llm_perspective.confidence,
            evidence=llm_perspective.evidence_summary,
            status="completed",
            key_points=llm_perspective.key_points,
            concerns=llm_perspective.concerns,
        )

    # =========================================================================
    # Helper Methods
    # =========================================================================

    def _infer_domain(self, profile_id: str) -> str:
        """
        从 profile_id 推断领域（旧方法，保留向后兼容）

        Args:
            profile_id: Profile ID

        Returns:
            str: 推断的领域
        """
        profile_lower = profile_id.lower()

        domain_keywords = {
            "security": ["anquan", "security", "安全"],
            "legal": ["fawu", "legal", "法务"],
            "database": ["dba", "database", "db"],
            "ops": ["ops", "devops", "运维", "sre"],
            "architecture": ["architect", "架构"],
        }

        for domain, keywords in domain_keywords.items():
            if any(kw in profile_lower for kw in keywords):
                return domain

        return "tech"

    def _infer_domain_from_profile(self, profile: "WorkerProfile") -> str:
        """
        从 profile 的多个维度推断领域

        优先级：
        1. active_skills（最可靠）
        2. context_fragments 内容
        3. profile_key 字符串（回退）

        Args:
            profile: Worker Profile

        Returns:
            str: 推断的领域
        """
        # 领域关键词映射
        domain_keywords = {
            "security": ["security", "anquan", "安全", "auth", "penetration", "漏洞", "渗透"],
            "legal": ["legal", "fawu", "法务", "compliance", "gdpr", "privacy", "合规"],
            "database": ["database", "dba", "db", "sql", "mysql", "postgresql", "数据库"],
            "ops": ["ops", "devops", "sre", "运维", "kubernetes", "docker", "deploy"],
            "architecture": ["architect", "架构", "design", "系统设计"],
            "frontend": ["frontend", "前端", "react", "vue", "css", "javascript"],
            "backend": ["backend", "后端", "api", "server", "service"],
        }

        # 1. 优先从 skills 推断
        if profile.active_skills:
            for skill in profile.active_skills:
                skill_name_lower = skill.name.lower()
                skill_desc_lower = (skill.description or "").lower()

                for domain, keywords in domain_keywords.items():
                    # 检查技能名称
                    for kw in keywords:
                        if kw in skill_name_lower:
                            return domain
                    # 检查技能描述
                    for kw in keywords:
                        if kw in skill_desc_lower:
                            return domain

        # 2. 从 context fragments 推断
        if profile.context_fragments:
            for fragment in profile.context_fragments:
                content_lower = (fragment.content or "").lower()

                for domain, keywords in domain_keywords.items():
                    for kw in keywords:
                        if kw in content_lower:
                            return domain

        # 3. 从 profile_key 回退（保持旧逻辑）
        return self._infer_domain(profile.profile_key)

    def _score_context_fragments(
        self,
        fragments: list,
        question: str,
    ) -> dict[str, float]:
        """
        对 context fragments 进行评分

        基于 question 的关键词匹配度评分。

        Args:
            fragments: ContextFragment 列表
            question: 问题

        Returns:
            dict[str, float]: filename -> score 映射
        """
        scores: dict[str, float] = {}
        question_lower = question.lower()
        question_keywords = self._extract_keywords(question)

        for fragment in fragments:
            score = 0.0
            content_lower = (fragment.content or "").lower()

            # 1. 完整问题匹配
            if question_lower in content_lower:
                score += 0.5

            # 2. 关键词匹配
            keyword_matches = sum(1 for kw in question_keywords if kw in content_lower)
            if question_keywords:
                score += (keyword_matches / len(question_keywords)) * 0.4

            # 3. Fragment 类型权重
            kind_weights = {
                "agent": 1.0,  # AGENTS.md 最重要
                "soul": 0.8,
                "rules": 0.7,
                "tools": 0.6,
                "boot": 0.5,
            }
            kind_weight = kind_weights.get(fragment.kind.value, 0.5)
            score *= kind_weight

            # 4. 内容长度因子
            length_factor = min(len(fragment.content or "") / 500, 1.0)
            score += length_factor * 0.1

            scores[fragment.filename] = min(score, 1.0)

        return scores

    def _score_skills(
        self,
        skills: list,
        question: str,
    ) -> dict[str, float]:
        """
        对 skills 进行评分

        基于 question 的关键词匹配度评分。

        Args:
            skills: SkillProfile 列表
            question: 问题

        Returns:
            dict[str, float]: skill_name -> score 映射
        """
        scores: dict[str, float] = {}
        question_lower = question.lower()
        question_keywords = self._extract_keywords(question)

        for skill in skills:
            score = 0.0
            skill_name_lower = skill.name.lower()
            skill_desc_lower = (skill.description or "").lower()

            # 1. 技能名称匹配
            if skill_name_lower in question_lower or question_lower in skill_name_lower:
                score += 0.6

            # 2. 技能名称关键词匹配
            for kw in question_keywords:
                if kw in skill_name_lower:
                    score += 0.2
                    break

            # 3. 技能描述匹配
            if skill_desc_lower:
                if question_lower in skill_desc_lower:
                    score += 0.3

                keyword_matches = sum(1 for kw in question_keywords if kw in skill_desc_lower)
                if question_keywords:
                    score += (keyword_matches / len(question_keywords)) * 0.2

            scores[skill.name] = min(score, 1.0)

        return scores

    def _select_context_highlights(
        self,
        fragments: list,
        question: str,
        max_highlights: int = 5,
    ) -> list[str]:
        """
        选择最相关的 context highlights

        Args:
            fragments: ContextFragment 列表
            question: 问题
            max_highlights: 最大数量

        Returns:
            list[str]: 选中的 highlights
        """
        if not fragments:
            return []

        # 评分
        scores = self._score_context_fragments(fragments, question)

        # 排序并选择
        sorted_fragments = sorted(
            fragments,
            key=lambda f: scores.get(f.filename, 0),
            reverse=True,
        )

        # 提取内容（截断到合理长度）
        highlights = []
        for fragment in sorted_fragments[:max_highlights]:
            content = fragment.content or ""
            # 截断到 300 字符
            highlight = content[:300] if len(content) > 300 else content
            if highlight:
                highlights.append(highlight)

        return highlights

    def _select_relevant_skills(
        self,
        skills: list,
        question: str,
        max_skills: int = 5,
    ) -> list:
        """
        选择最相关的 skills

        Args:
            skills: SkillProfile 列表
            question: 问题
            max_skills: 最大数量

        Returns:
            list[SkillProfile]: 选中的 skills
        """
        if not skills:
            return []

        # 评分
        scores = self._score_skills(skills, question)

        # 排序并选择
        sorted_skills = sorted(
            skills,
            key=lambda s: scores.get(s.name, 0),
            reverse=True,
        )

        return sorted_skills[:max_skills]

    def _extract_keywords(self, text: str) -> list[str]:
        """
        从文本提取关键词

        简单实现：分词并过滤停用词。

        Args:
            text: 输入文本

        Returns:
            list[str]: 关键词列表
        """
        import re
        words = re.findall(r"\b\w+\b", text.lower())

        stopwords = {
            "a", "an", "the", "is", "are", "was", "were", "be", "been",
            "being", "have", "has", "had", "do", "does", "did", "will",
            "would", "could", "should", "may", "might", "must", "shall",
            "can", "need", "to", "of", "in", "for", "on", "with", "at",
            "by", "from", "as", "into", "through", "how", "what", "which",
            "and", "or", "but", "if", "i", "me", "my", "we", "our", "you",
            "your", "he", "she", "it", "they", "this", "that", "these",
            "help", "want", "need", "tell", "give", "design", "implement",
            "create", "build", "make",
        }

        return [w for w in words if len(w) > 2 and w not in stopwords]


__all__ = [
    "G5ExpertEnhancerImpl",
]


# =============================================================================
# Sparse Context Detection Constants
# =============================================================================

# 占位符模式，用于检测无效的 expertise summary
SPARSE_CONTEXT_PLACEHOLDER_PATTERNS = [
    "expert profile",
    "no relevant context",
    "no context available",
    "n/a",
    "-",
    "...",
]


def _should_skip_llm_for_sparse_context(
    profile: "WorkerProfile",
    digest: "WorkerContextDigest",
) -> tuple[bool, str]:
    """
    Preflight 检查：判断 context 是否过于稀疏，应跳过 LLM 调用

    保守规则（首版）：
    1. fragments == 0 且 skills == 0
    2. expertise summary 为空或明显占位符
    3. profile 缺少足够可用描述字段（name, description, context_fragments 全空）

    Args:
        profile: Worker Profile
        digest: Worker Context Digest

    Returns:
        tuple[bool, str]: (是否应跳过, 原因说明)
    """
    reasons = []

    # 1. 检查 fragments 和 skills
    selected_fragments = getattr(digest, 'selected_fragments', 0) or 0
    selected_skills = getattr(digest, 'selected_skills', 0) or 0
    total_fragments = getattr(digest, 'total_fragments', 0) or 0
    total_skills = getattr(digest, 'total_skills', 0) or 0

    if total_fragments == 0 and total_skills == 0:
        reasons.append(f"no_context: fragments={total_fragments}, skills={total_skills}")
    elif selected_fragments == 0 and selected_skills == 0:
        reasons.append(f"no_selected: selected_fragments={selected_fragments}, selected_skills={selected_skills}")

    # 2. 检查 expertise summary
    expertise_summary = getattr(digest, 'context_summary', '') or ''
    summary_lower = expertise_summary.lower().strip()
    if not summary_lower:
        reasons.append("empty_summary")
    elif summary_lower in SPARSE_CONTEXT_PLACEHOLDER_PATTERNS:
        reasons.append(f"placeholder_summary: '{summary_lower}'")

    # 3. 检查 profile 是否有足够的描述字段
    has_name = bool(getattr(profile, 'name', None))
    has_description = bool(getattr(profile, 'description', None))
    has_context_fragments = bool(getattr(profile, 'context_fragments', None))
    has_active_skills = bool(getattr(profile, 'active_skills', None))

    if not has_name and not has_description and not has_context_fragments and not has_active_skills:
        reasons.append("empty_profile: no useful fields")

    if reasons:
        reason_str = "; ".join(reasons)
        logger.info(
            "[G5-ENHANCER-PREFLIGHT] sparse context detected: profile_key=%s, fragments=%d/%d, skills=%d/%d, reason=%s",
            profile.profile_key, selected_fragments, total_fragments, selected_skills, total_skills, reason_str
        )
        return True, reason_str

    return False, ""


def _build_sparse_context_perspective(
    profile: "WorkerProfile",
    question: str,
    skip_reason: str,
) -> Perspective:
    """
    构建 sparse context 降级视角

    语义要求：
    - status=skipped（不是 completed）
    - summary 明确说明"专家画像/上下文不足"
    - confidence 低（不伪装高质量）
    - evidence 说明缺失原因

    Args:
        profile: Worker Profile
        question: 问题
        skip_reason: 跳过原因

    Returns:
        Perspective: 降级视角
    """
    logger.info(
        "[G5-ENHANCER-PREFLIGHT] building sparse context perspective: profile_key=%s, reason=%s",
        profile.profile_key, skip_reason
    )

    return Perspective(
        participant_id=profile.profile_key,
        participant_type="bot",
        role="expert",
        summary="专家画像/上下文不足，无法形成有效专家视角",
        confidence=0.1,  # 明确的低置信度
        evidence=[
            f"EXPERTISE_SUMMARY: No highly relevant context found for the given question.",
            f"RELEVANT_SKILLS: []",
            f"CONTEXT_HIGHLIGHTS: []",
        ],
        status="skipped",  # 关键：使用 skipped 而非 completed
        key_points=[],
        concerns=[f"Profile context is sparse: {skip_reason}"],
    )