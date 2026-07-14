"""
ExpertDiagnosisService

G5: Expert Diagnosis Layer

专家会诊服务，负责 G5 场景的风险评估、关键问题检测、专家建议生成和上线条件提取。

职责：
1. 风险评估聚合（overall risk 规则钉死）
2. 关键问题检测
3. 专家建议生成
4. 上线条件提取
5. 诊断摘要生成

Stage 4 Phase 4:
6. 候选人推荐集成（participants 不足时推荐补充）

Feature Flags:
- ENABLE_G5_EXPERT_DIAGNOSIS: 控制是否启用 G5 专家诊断功能

约束：
- GroupFusionService 只做模式分发，业务逻辑在此
- overall risk 聚合规则钉死
- critical_issues/recommendations/go_live_conditions 职责分开
- partial success 语义沿用现有模式
- candidate_recommendation_service 可选注入，失败回退
- G5-first，不污染 G1/G2
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Optional

from src.domain.models.fusion_result import (
    FusionResult,
    Perspective,
    Recommendation,
    FusionTiming,
)
from src.domain.models.expert_risk_assessment import RiskLevel, RiskAssessment, Domain
from src.domain.models.expert_diagnosis import (
    CriticalIssue,
    ExpertRecommendation,
    Priority,
)
from src.domain.models.retrieval_mode import RetrievalMode
from src.domain.models.structured_risk_assessment import (
    RiskFactor,
    BlockingCondition,
    ExpertEvidence,
    ScenarioPriorRisk,
    StructuredRiskAssessment,
)
from src.infra.config.feature_flags import FeatureFlags
from src.infra.observability.fallback_logger import get_fallback_logger, get_fallback_metrics
from src.domain.taxonomy import get_taxonomy_registry, reset_taxonomy_registry, TaxonomyRegistry

if TYPE_CHECKING:
    from src.application.services.fusion_recommendation_service import FusionRecommendationService
    from src.domain.services.g5_expert_enhancer import G5ExpertEnhancer
    from src.domain.services.worker_candidate_recommendation_service import (
        WorkerCandidateRecommendationService,
    )

logger = logging.getLogger(__name__)


class ExpertDiagnosisService:
    """
    专家会诊服务

    负责 G5: Expert Diagnosis 场景的核心处理逻辑。

    核心方法：
    - diagnose(): 执行专家会诊

    Attributes:
        _recommendation_service: 融合建议服务（可选）
    """

    # overall risk 聚合规则（钉死）
    # 任一 critical → overall = critical
    # 否则任一 high → overall = high
    # 否则任一 medium → overall = medium
    # 否则 low
    RISK_PRIORITY = {
        RiskLevel.CRITICAL: 4,
        RiskLevel.HIGH: 3,
        RiskLevel.MEDIUM: 2,
        RiskLevel.LOW: 1,
    }

    def __init__(
        self,
        recommendation_service: Optional["FusionRecommendationService"] = None,
        g5_enhancer: Optional["G5ExpertEnhancer"] = None,
        candidate_recommendation_service: Optional["WorkerCandidateRecommendationService"] = None,
    ):
        """
        初始化服务

        Args:
            recommendation_service: 融合建议服务（可选，用于生成 recommendation）
            g5_enhancer: G5 专家增强器（可选，用于增强专家视角）
            candidate_recommendation_service: 候选人推荐服务（可选，用于推荐专家候选人）
        """
        self._recommendation_service = recommendation_service
        self._g5_enhancer = g5_enhancer
        self._candidate_recommendation_service = candidate_recommendation_service

    def diagnose(
        self,
        question: str,
        perspectives: list[Perspective],
        driver_bot_id: Optional[str] = None,
        include_recommendation: bool = True,
        expert_risks: Optional[dict[str, RiskLevel]] = None,
        participants: Optional[list[str]] = None,
        strict_participants: bool = False,
    ) -> FusionResult:
        """
        执行专家会诊

        Args:
            question: 待诊断的问题
            perspectives: 收集到的视角列表
            driver_bot_id: Driver bot ID
            include_recommendation: 是否生成单一建议
            expert_risks: 专家风险评估映射 {participant_id: RiskLevel}
            participants: 参与者列表（可选，用于 G5 enhancer）
            strict_participants: 是否启用严格参与者模式
                - False（默认）: 参与者过滤失败时允许 fallback
                - True: 参与者过滤失败时禁止 fallback

        Returns:
            FusionResult: 诊断结果
        """
        # ========== 详尽诊断日志开始 ==========
        import os
        import sys

        fusion_id = f"fus-{uuid.uuid4().hex[:12]}"
        logger.info("="*80)
        logger.info("[G5-DIAG] ========== G5 专家会诊开始 [fusion_id=%s] ==========", fusion_id)
        logger.info("[G5-DIAG] 调用时间: %s", datetime.now().isoformat())
        logger.info("[G5-DIAG] Python进程PID: %d", os.getpid())
        logger.info("[G5-DIAG] Python版本: %s", sys.version.split()[0])
        logger.info("[G5-DIAG]question长度: %d, 预览: %s", len(question), question[:100] if len(question) > 100 else question)
        logger.info("[G5-DIAG] perspectives数量: %d", len(perspectives))
        for i, p in enumerate(perspectives):
            logger.info("[G5-DIAG]   perspectives[%d]: id=%s, role=%s, status=%s, summary_len=%d",
                       i, p.participant_id, p.role, p.status, len(p.summary) if p.summary else 0)
        logger.info("[G5-DIAG] driver_bot_id: %s", driver_bot_id)
        logger.info("[G5-DIAG] participants: %s", participants)
        logger.info("[G5-DIAG] include_recommendation: %s", include_recommendation)

        # ========== Step 1: Feature Flag 检查==========
        logger.info("[G5-DIAG] Step 1: 检查 Feature Flags...")
        all_flags = FeatureFlags.get_all_flags()
        logger.info("[G5-DIAG] 所有 Feature Flags: %s", all_flags)
        g5_enabled = FeatureFlags.is_g5_expert_diagnosis_enabled()
        logger.info("[G5-DIAG] ENABLE_G5_EXPERT_DIAGNOSIS: %s", g5_enabled)

        # 检查环境变量
        env_g5 = os.environ.get("ENABLE_G5_EXPERT_DIAGNOSIS", "not_set")
        logger.info("[G5-DIAG] 环境变量 ENABLE_G5_EXPERT_DIAGNOSIS: %s", env_g5)

        if not g5_enabled:
            logger.warning("[G5-DIAG] ⚠️G5 专家诊断功能未启用，将降级到基本处理")
            fallback_logger = get_fallback_logger()
            fallback_metrics = get_fallback_metrics()
            fallback_logger.log_fallback(
                fallback_type="g5_to_basic",
                reason="ENABLE_G5_EXPERT_DIAGNOSIS is False",
                affected_component="expert_diagnosis_service",
            )
            fallback_metrics.increment("g5_fallback_count")
            result = self._build_fallback_result(
                question=question,
                perspectives=perspectives,
                driver_bot_id=driver_bot_id,
            )
            logger.info("[G5-DIAG] ========== G5 降级处理完成[fusion_id=%s] ==========", fusion_id)
            return result

        # ========== Step 2: 检查服务注入状态 ==========
        logger.info("[G5-DIAG] Step 2: 检查服务注入状态...")
        logger.info("[G5-DIAG] _recommendation_service 注入状态: %s", self._recommendation_service is not None)
        logger.info("[G5-DIAG] _g5_enhancer 注入状态: %s", self._g5_enhancer is not None)
        logger.info("[G5-DIAG] _candidate_recommendation_service 注入状态: %s", self._candidate_recommendation_service is not None)

        if self._g5_enhancer is not None:
            logger.info("[G5-DIAG] _g5_enhancer 类型: %s", type(self._g5_enhancer).__name__)
            logger.info("[G5-DIAG] _g5_enhancer id: %d", id(self._g5_enhancer))
        else:
            logger.warning("[G5-DIAG] ⚠️ _g5_enhancer 为 None，将使用原始 perspectives")

        started_at = datetime.now()

        # ========== Step 3: 候选人推荐 ==========
        logger.info("[G5-DIAG] Step 3: 候选人推荐...")
        logger.info("[G5-DIAG] strict_participants: %s (传递到候选推荐服务)", strict_participants)

        # Initialize vector supplement diagnostics
        vector_supplement_diagnostics = {
            "vector_supplement_enabled": self._candidate_recommendation_service is not None,
            "strict_participants": strict_participants,
            "participants_before_supplement": participants if participants else [],
            "participants_resolved_before_supplement": len(participants) if participants else 0,
            "supplement_trigger_condition_met": False,  # Will be updated below
            "supplement_search_called": False,
            "supplement_candidates_count": 0,
            "supplemented_participants_count": 0,
            "participants_after_supplement": [],
            "supplement_skip_reason": None,
        }

        # Determine if supplement should be triggered
        # Trigger condition: participants is None or empty, or strict_participants is False
        if participants is None or len(participants) == 0:
            vector_supplement_diagnostics["supplement_trigger_condition_met"] = True
            logger.info("[G5-DIAG] Supplement trigger condition met: participants is empty")
        elif not strict_participants:
            vector_supplement_diagnostics["supplement_trigger_condition_met"] = True
            logger.info("[G5-DIAG] Supplement trigger condition met: strict_participants=False")
        else:
            vector_supplement_diagnostics["supplement_trigger_condition_met"] = False
            logger.info("[G5-DIAG] Supplement trigger condition NOT met: strict_participants=True and participants=%d", len(participants))

        recommended_participants = participants
        if self._candidate_recommendation_service is not None:
            logger.info("[G5-DIAG] _candidate_recommendation_service 已注入，开始推荐...")
            try:
                response = self._candidate_recommendation_service.recommend(
                    question=question,
                    mode=RetrievalMode.EXPERT_DIAGNOSIS,
                    participants=participants,
                    strict_participants=strict_participants,
                )
                logger.info("[G5-DIAG] 推荐结果: recommendations=%d", len(response.recommendations) if response.recommendations else 0)

                # ========== Phase R7-1-A: Trace Candidate Recommendations ==========
                logger.info("[G5-TRACE] ========== TRACE-A: Candidate Recommendations Output ==========")
                logger.info("[G5-TRACE] candidate_recommendations_count: %d", len(response.recommendations) if response.recommendations else 0)
                if response.recommendations:
                    for i, r in enumerate(response.recommendations):
                        logger.info("[G5-TRACE]   candidate[%d]: profile_key=%s, worker_id=%s, role=%s, score=%.3f, is_supplement=%s",
                                   i, r.profile_key, r.worker_id, r.domain, r.score, r.is_supplement)
                    logger.info("[G5-TRACE] candidate_profile_keys: %s", [r.profile_key for r in response.recommendations])
                    logger.info("[G5-TRACE] candidate_worker_ids: %s", [r.worker_id for r in response.recommendations])
                    logger.info("[G5-TRACE] candidate_roles: %s", [r.domain for r in response.recommendations])
                    logger.info("[G5-TRACE] candidate_scores: %s", [round(r.score, 3) for r in response.recommendations])
                    logger.info("[G5-TRACE] candidate_is_supplement: %s", [r.is_supplement for r in response.recommendations])
                logger.info("[G5-TRACE] ========== TRACE-A END ==========")

                # Update vector supplement diagnostics from response
                vector_supplement_diagnostics["supplement_search_called"] = True
                vector_supplement_diagnostics["supplement_candidates_count"] = (
                    response.total_candidates if hasattr(response, 'total_candidates') and response.total_candidates
                    else len(response.recommendations) if response.recommendations
                    else 0
                )

                # Extract profile_keys
                recommended_participants = [r.profile_key for r in response.recommendations] if response.recommendations else participants

                # Calculate supplemented participants count
                if recommended_participants and participants:
                    supplemented = [p for p in recommended_participants if p not in participants]
                    vector_supplement_diagnostics["supplemented_participants_count"] = len(supplemented)
                elif recommended_participants and not participants:
                    # All recommended are supplements
                    vector_supplement_diagnostics["supplemented_participants_count"] = len(recommended_participants)

                vector_supplement_diagnostics["participants_after_supplement"] = recommended_participants if recommended_participants else []

            except Exception as e:
                logger.error("[G5-DIAG] 候选人推荐失败: %s", e, exc_info=True)
                recommended_participants = participants
                vector_supplement_diagnostics["supplement_skip_reason"] = f"recommendation_failed: {str(e)}"
        else:
            logger.info("[G5-DIAG] _candidate_recommendation_service 未注入，跳过推荐")
            vector_supplement_diagnostics["supplement_skip_reason"] = "candidate_recommendation_service_not_injected"

        # ========== Step 4: G5 专家视角增强 ==========
        logger.info("[G5-DIAG] Step 4: G5 专家视角增强...")
        logger.info("[G5-DIAG] strict_participants: %s", strict_participants)

        # ========== Phase R7-1-B: Trace Before G5Enhancer Call ==========
        logger.info("[G5-TRACE] ========== TRACE-B: Before G5Enhancer Call ==========")
        logger.info("[G5-TRACE] base_perspectives_count: %d", len(perspectives))
        if perspectives:
            logger.info("[G5-TRACE] base_perspective_participant_ids: %s", [p.participant_id for p in perspectives])
        logger.info("[G5-TRACE] recommended_participants_count: %d", len(recommended_participants) if recommended_participants else 0)
        if recommended_participants:
            logger.info("[G5-TRACE] recommended_participant_profile_keys: %s", recommended_participants)
        logger.info("[G5-TRACE] strict_participants: %s", strict_participants)
        logger.info("[G5-TRACE] final_participants_to_enhancer: %s", recommended_participants)
        logger.info("[G5-TRACE] ========== TRACE-B END ==========")

        enhanced_perspectives = perspectives
        if self._g5_enhancer is not None:
            logger.info("[G5-DIAG] _g5_enhancer 已注入，开始调用 enhance()...")
            logger.info("[G5-DIAG] enhance() 参数: question_len=%d, base_perspectives=%d, participants=%s, strict_participants=%s",
                       len(question), len(perspectives), recommended_participants, strict_participants)
            try:
                enhance_start = datetime.now()
                enhanced_perspectives = self._g5_enhancer.enhance(
                    question=question,
                    base_perspectives=perspectives,
                    participants=recommended_participants,
                    driver_bot_id=driver_bot_id,
                    strict_participants=strict_participants,
                )
                enhance_elapsed = (datetime.now() - enhance_start).total_seconds()
                logger.info("[G5-DIAG] enhance() 完成，耗时: %.2fs", enhance_elapsed)
                logger.info("[G5-DIAG] enhance() 返回 perspectives 数量: %d",
                           len(enhanced_perspectives) if enhanced_perspectives else 0)

                if not enhanced_perspectives:
                    # ⚠️ 关键判断：strict 模式下不应回退
                    if strict_participants:
                        logger.warning("[G5-DIAG] ⚠️ strict 模式：enhance() 返回空列表，保持空结果")
                        # 不回退，使用空列表
                        enhanced_perspectives = []
                    else:
                        logger.warning("[G5-DIAG] ⚠️ enhance() 返回空列表，回退到原 perspectives [degraded]")
                        enhanced_perspectives = perspectives
                else:
                    for i, p in enumerate(enhanced_perspectives[:5]):
                        logger.info("[G5-DIAG]   enhanced[%d]: id=%s, role=%s, status=%s, confidence=%s, summary_len=%d",
                                   i, p.participant_id, p.role, p.status,
                                   getattr(p, 'confidence', None),
                                   len(p.summary) if p.summary else 0)
            except Exception as e:
                logger.error("[G5-DIAG] ❌ enhance() 执行失败: %s", e, exc_info=True)
                enhanced_perspectives = perspectives
        else:
            logger.warning("[G5-DIAG] ⚠️ _g5_enhancer 未注入，使用原始 perspectives")

        # 使用增强后的 perspectives 进行诊断
        perspectives = enhanced_perspectives

        # 计算 partial success
        completed_count = sum(1 for p in perspectives if p.status == "completed")
        total_count = len(perspectives)
        partial_success = completed_count > 0 and completed_count < total_count

        # 收集警告
        warnings: list[str] = []
        for p in perspectives:
            if p.status == "timed_out":
                warnings.append(f"participant {p.participant_id} timed out")
            elif p.status == "failed":
                warnings.append(f"participant {p.participant_id} failed")
            elif p.status == "skipped":
                warnings.append(f"participant {p.participant_id} was skipped")

        # 只对成功完成的视角进行分析
        completed_perspectives = [p for p in perspectives if p.status == "completed"]
        logger.info("[G5] 开始风险评估聚合，已完成视角数量: %d", len(completed_perspectives))

        # 1. 风险评估聚合（增强版：传入问题上下文）
        risk_assessment = self._aggregate_risks(
            perspectives=completed_perspectives,
            expert_risks=expert_risks,
            question=question,
        )
        logger.info("[G5] 风险评估完成: overall=%s, categories=%s",
                    risk_assessment.overall, risk_assessment.categories)

        # 2. 关键问题检测（问题清单）
        logger.info("[G5] 开始关键问题检测...")
        critical_issues = self._detect_critical_issues(
            perspectives=completed_perspectives,
            risk_assessment=risk_assessment,
            expert_risks=expert_risks,
            question=question,
        )
        logger.info("[G5] 检测到 %d 个关键问题", len(critical_issues))

        # 3. 专家建议生成（行动项清单）
        logger.info("[G5] 开始生成专家建议...")
        recommendations = self._generate_expert_recommendations(
            perspectives=completed_perspectives,
            critical_issues=critical_issues,
            risk_assessment=risk_assessment,
        )
        logger.info("[G5] 生成 %d 条专家建议", len(recommendations))

        # 4. 上线条件提取（前置条件）
        logger.info("[G5] 开始提取上线条件...")
        go_live_conditions = self._extract_go_live_conditions(
            perspectives=completed_perspectives,
            critical_issues=critical_issues,
            recommendations=recommendations,
        )
        logger.info("[G5] 提取 %d 条上线条件", len(go_live_conditions))

        # 5. 诊断摘要生成
        summary = self._generate_summary(
            question=question,
            perspectives=completed_perspectives,
            risk_assessment=risk_assessment,
            critical_issues=critical_issues,
        )
        logger.info("[G5] 诊断摘要: %s", summary[:100] if len(summary) > 100 else summary)

        # ========== V2: 结构化风险评估 ==========
        structured_risk = None
        if FeatureFlags.is_g5_structured_risk_enabled():
            logger.info("[G5] V2: 启用结构化风险评估")
            structured_risk = self._generate_structured_risk_assessment(
                question=question,
                perspectives=completed_perspectives,
                risk_assessment=risk_assessment,
                critical_issues=critical_issues,
                expert_risks=expert_risks,
            )
            logger.info("[G5] V2: 结构化风险评估完成, risk_level=%s, factors=%d",
                       structured_risk.risk_level.value, len(structured_risk.risk_factors))
        else:
            logger.info("[G5] 结构化风险评估未启用 (ENABLE_G5_STRUCTURED_RISK=False)")

        # 6. 单一建议（可选）
        recommendation = None
        if include_recommendation:
            logger.info("[G5] 开始生成单一建议...")
            recommendation = self._generate_recommendation(
                question=question,
                perspectives=perspectives,
                risk_assessment=risk_assessment,
                critical_issues=critical_issues,
                partial_success=partial_success,
                warnings=warnings,
            )
            logger.info("[G5] 单一建议: decision=%s", recommendation.decision if recommendation else None)

        finished_at = datetime.now()
        duration_ms = int((finished_at - started_at).total_seconds() * 1000)

        timing = FusionTiming(
            started_at=started_at,
            finished_at=finished_at,
            duration_ms=duration_ms,
        )

        # ========== Phase E2: Aggregate metadata from perspectives ==========
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
        llm_perspectives_generated_count = 0
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
                    llm_perspectives_generated_count += 1

        # Set metadata counters
        metadata['online_workers_count'] = online_workers_count if online_workers_count > 0 else len([p for p in perspectives if p.status == "completed"])
        metadata['active_profiles_loaded_count'] = active_profiles_loaded_count if active_profiles_loaded_count > 0 else len([p for p in perspectives if p.status == "completed"])
        metadata['participants_resolved_count'] = len(perspectives)
        metadata['llm_perspectives_generated_count'] = llm_perspectives_generated_count

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

        # Add vector supplement diagnostics
        metadata['vector_supplement_diagnostics'] = vector_supplement_diagnostics

        # Extract key metrics for logging
        vs_diag = vector_supplement_diagnostics
        logger.info(
            "[G5][VectorSupplement] enabled=%s, strict=%s, before=%d, search_called=%s, "
            "candidates=%d, supplemented=%d, after=%d, reason=%s",
            vs_diag['vector_supplement_enabled'],
            vs_diag['strict_participants'],
            len(vs_diag['participants_before_supplement']) if vs_diag['participants_before_supplement'] else 0,
            vs_diag['supplement_search_called'],
            vs_diag['supplement_candidates_count'],
            vs_diag['supplemented_participants_count'],
            len(vs_diag['participants_after_supplement']) if vs_diag['participants_after_supplement'] else 0,
            vs_diag['supplement_skip_reason'] or 'none'
        )

        logger.info(
            "[G5][Diagnostics] online_workers=%d, active_profiles=%d, content_loaded=%d, "
            "content_non_empty=%d, llm_generated=%d, format=%s, fallback=%d",
            metadata['online_workers_count'],
            metadata['active_profiles_loaded_count'],
            profile_diagnostics['profile_content_loaded_count'],
            profile_diagnostics['profile_content_non_empty_count'],
            llm_perspectives_generated_count,
            metadata.get('profile_format', 'N/A'),
            fallback_perspective_count
        )

        # ========== Phase R7-1-E: Trace Before FusionResult Construction ==========
        logger.info("[G5-TRACE] ========== TRACE-E: Before FusionResult Construction ==========")
        logger.info("[G5-TRACE] perspectives_before_result_count: %d", len(perspectives))
        if perspectives:
            logger.info("[G5-TRACE] perspectives_before_result_participant_ids: %s", [p.participant_id for p in perspectives])
            logger.info("[G5-TRACE] perspectives_before_result_roles: %s", [p.role for p in perspectives])
            logger.info("[G5-TRACE] perspectives_before_result_statuses: %s", [p.status for p in perspectives])
        logger.info("[G5-TRACE] vector_supplement_diagnostics: %s", vector_supplement_diagnostics)
        logger.info("[G5-TRACE] ========== TRACE-E END ==========")

        return FusionResult(
            group_id="",  # 由 GroupFusionService 设置
            fusion_id=fusion_id,
            question=question,
            driver_bot_id=driver_bot_id,
            perspectives=perspectives,
            recommendation=recommendation,
            partial_success=partial_success,
            warnings=warnings,
            errors=[],
            timing=timing,
            fusion_mode="expert_diagnosis",
            # G5 特有字段
            risk_assessment=risk_assessment,
            critical_issues=critical_issues,
            recommendations=recommendations,
            go_live_conditions=go_live_conditions,
            summary=summary,
            # G5 V2 字段
            structured_risk=structured_risk,
            # Phase E2: Add metadata
            metadata=metadata,
        )

    def _get_recommended_participants(
        self,
        question: str,
        participants: list[str] | None,
        strict_participants: bool = False,
    ) -> list[str] | None:
        """
        获取推荐的 participants（候选人推荐）

        核心逻辑：
        1. 调用 recommendation service 获取推荐
        2. 保留 ordered profile_keys（显式在前，补充在后）
        3. strict 模式下禁止补充推荐
        4. 失败时回退到原 participants

        Args:
            question: 问题
            participants: 原始 participants
            strict_participants: 是否启用严格参与者模式

        Returns:
            list[str] | None: 推荐的 participants（或原 participants）
        """
        try:
            response = self._candidate_recommendation_service.recommend(
                question=question,
                mode=RetrievalMode.EXPERT_DIAGNOSIS,
                participants=participants,
                strict_participants=strict_participants,
            )

            # Log vector supplement diagnostics
            logger.info(
                "[G5-DIAG][CandidateRecommend] question_len=%d, participants=%s, strict=%s, "
                "recommendations_count=%d, candidates_count=%d",
                len(question),
                participants,
                strict_participants,
                len(response.recommendations) if response.recommendations else 0,
                response.total_candidates if hasattr(response, 'total_candidates') else 'N/A'
            )

            # 提取 profile_keys（保持顺序：显式在前，补充在后）
            recommended = [r.profile_key for r in response.recommendations]

            # Update vector supplement diagnostics
            # Note: These will be stored in the diagnose() method's vector_supplement_diagnostics

            if recommended:
                return recommended

            # 如果推荐为空，回退到原 participants
            return participants

        except Exception as e:
            logger.warning(f"Candidate recommendation failed: {e}")
            # 失败时回退到原 participants
            return participants

    def _aggregate_risks(
        self,
        perspectives: list[Perspective],
        expert_risks: Optional[dict[str, RiskLevel]],
        question: str = None,
    ) -> RiskAssessment:
        """
        聚合风险评估（增强版 v2）

        规则（钉死）：
        - 任一专家 critical → overall = critical
        - 否则任一 high → overall = high
        - 否则任一 medium → overall = medium
        - 否则 low

        增强版：
        - 基于问题上下文推断基线风险，作为最低风险保障

        Args:
            perspectives: 完成的视角列表
            expert_risks: 专家风险评估映射
            question: 问题（用于基线风险推断）

        Returns:
            RiskAssessment: 聚合后的风险评估
        """
        if expert_risks is None:
            expert_risks = {}

        # 默认 low
        overall = RiskLevel.LOW
        categories: dict[str, RiskLevel] = {}

        # 遍历专家视角
        for p in perspectives:
            if p.role != "expert":
                continue

            participant_id = p.participant_id
            # 从提供的风险映射中获取，或尝试从 summary 推断
            risk = expert_risks.get(participant_id)
            if risk is None:
                risk = self._infer_risk_from_summary(p.summary)

            # 记录分领域风险
            domain = self._infer_domain_from_participant(participant_id)
            categories[domain] = risk

            # 聚合 overall（按优先级）
            if self.RISK_PRIORITY[risk] > self.RISK_PRIORITY[overall]:
                overall = risk

        # ========== 基线风险记录（仅供参考，不覆盖专家意见） ==========
        # 注意：基线风险仅作为参考信息记录，不应覆盖专家判断
        # 专家意见优先，系统预判不应干预
        if question:
            baseline_risk = self._infer_baseline_risk_from_question(question)
            logger.info("[G5] 问题基线风险(仅供参考，不影响overall): %s", baseline_risk.value)

            # 仅将基线风险添加到 categories 作为参考信息
            # 不再覆盖 overall，专家意见优先
            if "baseline" not in categories or self.RISK_PRIORITY[baseline_risk] > self.RISK_PRIORITY[categories.get("baseline", RiskLevel.LOW)]:
                categories["baseline"] = baseline_risk

        return RiskAssessment(
            overall=overall,
            categories=categories,
        )

    def _infer_risk_from_summary(self, summary: str) -> RiskLevel:
        """
        从摘要推断风险等级

        V2 增强：
        - 当 ENABLE_TAXONOMY_REGISTRY 开启时，从 TaxonomyRegistry 读取关键词

        规则（优先级从高到低）：
        - Critical: 严重、critical、高危、紧急、必须立即、需要立即
        - High: 高风险词汇（注入、漏洞、缺失、不通过等）
        - Medium: 中等、需要关注、有条件
        - Low: 其他

        Args:
            summary: 视角摘要

        Returns:
            RiskLevel: 推断的风险等级
        """
        summary_lower = summary.lower()
        logger.debug("[G5] 推断风险等级: summary_len=%d, preview=%s", len(summary), summary[:50] if summary else "")

        # ========== V2: 使用 TaxonomyRegistry ==========
        if FeatureFlags.is_taxonomy_registry_enabled():
            registry = get_taxonomy_registry()
            risk_level_keywords = registry.get_risk_level_keywords()

            # Critical 关键词
            if any(kw in summary_lower for kw in risk_level_keywords.critical):
                logger.info("[G5] 风险等级=CRITICAL (TaxonomyRegistry 关键词匹配)")
                return RiskLevel.CRITICAL

            # High 关键词
            matched_high = [kw for kw in risk_level_keywords.high if kw in summary_lower]
            if matched_high:
                logger.info("[G5] 风险等级=HIGH (TaxonomyRegistry 关键词匹配: %s)", matched_high[:3])
                return RiskLevel.HIGH

            # Medium 关键词
            matched_medium = [kw for kw in risk_level_keywords.medium if kw in summary_lower]
            if matched_medium:
                logger.info("[G5] 风险等级=MEDIUM (TaxonomyRegistry 关键词匹配: %s)", matched_medium[:3])
                return RiskLevel.MEDIUM

            # Low
            logger.info("[G5] 风险等级=LOW (TaxonomyRegistry 无匹配)")
            return RiskLevel.LOW

        # ========== Legacy: 硬编码关键词 ==========
        # Critical 关键词 - 严重/紧急情况
        if any(kw in summary_lower for kw in ["严重", "critical", "高危", "紧急", "必须立即", "需要立即", "禁止上线"]):
            logger.info("[G5] 风险等级=CRITICAL (匹配关键词: 严重/critical/高危/紧急)")
            return RiskLevel.CRITICAL

        # High 关键词 - 明显风险信号
        high_keywords = [
            "注入", "漏洞", "缺失", "泄露", "攻击", "违规",
            "高", "high", "不通过", "不可行", "反对",
            "风险", "隐患", "威胁", "安全隐患",
        ]
        matched_high = [kw for kw in high_keywords if kw in summary_lower]
        if matched_high:
            logger.info("[G5] 风险等级=HIGH (匹配关键词: %s)", matched_high)
            return RiskLevel.HIGH

        # Medium 关键词 - 中等风险
        medium_keywords = ["中等", "medium", "需要关注", "有条件", "待确认", "建议", "关注"]
        matched_medium = [kw for kw in medium_keywords if kw in summary_lower]
        if matched_medium:
            logger.info("[G5] 风险等级=MEDIUM (匹配关键词: %s)", matched_medium)
            return RiskLevel.MEDIUM

        # Low 关键词 - 无明显风险
        logger.info("[G5] 风险等级=LOW (无匹配关键词)")
        return RiskLevel.LOW

    def _infer_baseline_risk_from_question(self, question: str) -> RiskLevel:
        """
        从问题上下文推断基线风险（增强版 v2）

        用于在专家视角不足或风险评估偏低时，提供最低风险保障。

        V2 增强：
        - 当 ENABLE_TAXONOMY_REGISTRY 开启时，从 TaxonomyRegistry 读取关键词

        检测维度：
        1. 高危场景关键词（数据泄露、安全事故等）
        2. 影响规模（用户数量、金额等）
        3. 监管合规场景（跨境支付、牌照等）
        4. 系统变更场景（架构升级、迁移等）

        Args:
            question: 问题文本

        Returns:
            RiskLevel: 推断的基线风险等级
        """
        if not question:
            return RiskLevel.LOW

        question_lower = question.lower()
        logger.info("[G5] 开始推断问题基线风险，问题长度: %d", len(question))

        # ========== V2: 使用 TaxonomyRegistry ==========
        if FeatureFlags.is_taxonomy_registry_enabled():
            registry = get_taxonomy_registry()
            risk_level, confidence = registry.match_text_for_risk(question_lower)
            if risk_level:
                logger.info("[G5] TaxonomyRegistry 匹配: risk=%s, confidence=%.2f", risk_level, confidence)
                return RiskLevel(risk_level)
            # 无匹配时 fallback 到 legacy
            logger.info("[G5] TaxonomyRegistry 无匹配，fallback 到 legacy")

        # ========== Legacy: 硬编码关键词 ==========
        # ========== 1. Critical 场景 ==========
        critical_patterns = [
            # 数据泄露/安全事件（扩展关键词覆盖 G5-4 场景）
            (["数据泄露", "信息泄露", "隐私泄露", "用户数据泄露",
              "用户泄露", "信息外泄", "数据被盗", "数据外泄",
              "数据暴露", "敏感数据泄露", "用户信息泄露"], "数据泄露事件"),
            (["安全事件", "安全事故", "安全漏洞", "被攻击",
              "系统被入侵", "数据被盗取", "遭受攻击"], "安全事件"),
            (["监管函", "整改通知", "处罚", "立案调查"], "监管处罚"),
            (["资金损失", "资金风险", "资金安全"], "资金安全"),
        ]

        for keywords, desc in critical_patterns:
            if any(kw in question_lower for kw in keywords):
                logger.info("[G5] 基线风险=CRITICAL (匹配: %s - %s)", desc, keywords[0])
                return RiskLevel.CRITICAL

        # ========== 2. High 场景 ==========
        high_patterns = [
            # 大规模影响
            (["核心系统", "核心交易", "支付系统", "资金流转"], "核心系统"),
            (["架构升级", "系统迁移", "技术升级", "数据库迁移",
              "java升级", "版本升级", "数据库切换", "技术栈升级"], "系统变更"),
            # 监管合规（扩展关键词覆盖 G5-6 场景）
            (["跨境支付", "牌照申请", "监管准入", "合规准入",
              "跨境业务", "境外支付", "外汇支付", "国际支付",
              "金融牌照", "支付牌照", "准入评估"], "监管准入"),
            (["反洗钱", "整改", "合规风险"], "合规整改"),
            # 大规模用户影响
            (["100万", "百万用户", "千万用户", "大规模"], "大规模影响"),
            # 组织风险
            (["组织架构调整", "人员优化", "大规模裁员"], "组织变革"),
            # 投融资风险
            (["股权融资", "并购", "投融资"], "投融资交易"),
            # 大型活动风险（扩展 G5-3 场景）
            (["大促活动", "营销活动风险", "活动风险评审"], "大型活动"),
        ]

        for keywords, desc in high_patterns:
            if any(kw in question_lower for kw in keywords):
                logger.info("[G5] 基线风险=HIGH (匹配: %s - %s)", desc, keywords[0])
                return RiskLevel.HIGH

        # ========== 3. Medium 场景 ==========
        medium_patterns = [
            # 业务场景
            (["新业务", "新产品", "业务拓展"], "新业务"),
            (["大促", "双11", "618", "活动"], "大促活动"),
            # 技术场景
            (["性能优化", "功能开发", "系统改造"], "技术改造"),
            # 运营场景
            (["运营策略", "营销活动", "用户增长"], "运营策略"),
        ]

        for keywords, desc in medium_patterns:
            if any(kw in question_lower for kw in keywords):
                logger.info("[G5] 基线风险=MEDIUM (匹配: %s - %s)", desc, keywords[0])
                return RiskLevel.MEDIUM

        # ========== 4. 默认 Low ==========
        logger.info("[G5] 基线风险=LOW (无匹配高危场景)")
        return RiskLevel.LOW

    def _infer_domain_from_participant(self, participant_id: str) -> str:
        """
        从参与者 ID 推断领域

        Args:
            participant_id: 参与者 ID

        Returns:
            str: 推断的领域（使用 Domain 枚举值）
        """
        # 统一的领域映射表
        domain_map = {
            # 安全
            "anquan": Domain.SECURITY,
            "security": Domain.SECURITY,
            "安全": Domain.SECURITY,
            # 法务
            "fawu": Domain.LEGAL,
            "legal": Domain.LEGAL,
            "法务": Domain.LEGAL,
            # 数据库
            "dba": Domain.DATABASE,
            "database": Domain.DATABASE,
            # 运维
            "ops": Domain.OPS,
            "devops": Domain.OPS,
            "运维": Domain.OPS,
            # 技术/开发
            "dev": Domain.TECH,
            "developer": Domain.TECH,
            "开发": Domain.TECH,
            # 架构
            "architect": Domain.ARCHITECTURE,
            "架构": Domain.ARCHITECTURE,
        }

        participant_lower = participant_id.lower()
        for key, domain in domain_map.items():
            if key in participant_lower:
                return domain.value

        # 默认返回原 participant_id（小写）
        return participant_lower

    def _detect_critical_issues(
        self,
        perspectives: list[Perspective],
        risk_assessment: RiskAssessment,
        expert_risks: Optional[dict[str, RiskLevel]],
        question: str = None,
    ) -> list[CriticalIssue]:
        """
        检测关键问题（问题清单）（增强版 v2）

        注意：这是问题清单，不是行动项

        增强版：
        - 基于问题上下文推断基线风险
        - 当专家视角不足时，补充基于问题的关键问题

        Args:
            perspectives: 完成的视角列表
            risk_assessment: 风险评估
            expert_risks: 专家风险评估映射
            question: 问题（用于推断基线风险）

        Returns:
            list[CriticalIssue]: 关键问题列表
        """
        issues: list[CriticalIssue] = []
        logger.info("[G5] 开始检测关键问题，视角数量: %d", len(perspectives))

        if expert_risks is None:
            expert_risks = {}

        for p in perspectives:
            if p.role != "expert":
                logger.debug("[G5] 跳过非专家视角: %s (role=%s)", p.participant_id, p.role)
                continue

            participant_id = p.participant_id
            risk = expert_risks.get(participant_id) or self._infer_risk_from_summary(p.summary)
            domain = self._infer_domain_from_participant(participant_id)

            logger.info("[G5] 专家 %s: 风险=%s, 领域=%s, summary_preview=%s",
                       participant_id, risk.value, domain, p.summary[:50] if p.summary else "")

            # 只记录 medium 及以上的问题
            if self.RISK_PRIORITY[risk] >= self.RISK_PRIORITY[RiskLevel.MEDIUM]:
                # 从 summary 提取问题描述
                issue_text = self._extract_issue_from_summary(p.summary)
                logger.info("[G5] 检测到问题: %s (severity=%s)", issue_text[:50], risk.value)

                issues.append(CriticalIssue(
                    issue=issue_text,
                    severity=risk,
                    domain=domain,
                    source=participant_id,
                    description=p.summary if len(p.summary) > len(issue_text) else None,
                ))
            else:
                logger.debug("[G5] 风险等级 %s 低于 MEDIUM，不记录为关键问题", risk.value)

        # ========== 增强版 v2：基于问题上下文补充关键问题 ==========
        if question and len(issues) == 0:
            baseline_risk = self._infer_baseline_risk_from_question(question)
            logger.info("[G5] 专家视角不足，基于问题推断基线风险: %s", baseline_risk.value)

            # 如果基线风险为 medium 及以上，生成基于问题的关键问题
            if self.RISK_PRIORITY[baseline_risk] >= self.RISK_PRIORITY[RiskLevel.MEDIUM]:
                # 从问题中提取关键问题描述
                baseline_issue = self._extract_baseline_issue_from_question(question, baseline_risk)
                issues.append(baseline_issue)
                logger.info("[G5] 基于问题添加关键问题: %s (severity=%s)",
                           baseline_issue.issue[:50], baseline_issue.severity.value)

        logger.info("[G5] 关键问题检测完成，共 %d 个问题", len(issues))
        return issues

    def _extract_baseline_issue_from_question(self, question: str, risk: RiskLevel) -> CriticalIssue:
        """
        从问题中提取基线关键问题

        Args:
            question: 问题文本
            risk: 风险等级

        Returns:
            CriticalIssue: 关键问题
        """
        question_lower = question.lower()

        # 定义问题模式
        issue_patterns = [
            (["数据泄露", "信息泄露", "隐私泄露"], "数据安全事件风险"),
            (["安全事件", "安全事故"], "安全事件响应风险"),
            (["监管函", "整改通知"], "监管合规风险"),
            (["核心系统", "核心交易"], "核心系统风险"),
            (["架构升级", "系统迁移"], "系统变更风险"),
            (["跨境支付", "牌照申请"], "监管准入风险"),
            (["组织架构调整", "人员优化"], "组织变革风险"),
            (["股权融资", "投融资"], "投融资交易风险"),
            (["大促", "双11", "618"], "大促活动风险"),
            (["新业务", "新产品"], "新业务风险"),
        ]

        issue_text = "需要诊断评估的潜在风险"
        for keywords, desc in issue_patterns:
            if any(kw in question_lower for kw in keywords):
                issue_text = desc
                break

        return CriticalIssue(
            issue=issue_text,
            severity=risk,
            domain="baseline",
            source="question_context",
            description=f"基于问题上下文推断的基线风险: {question[:100]}..." if len(question) > 100 else f"基于问题上下文推断的基线风险: {question}",
        )

    def _extract_issue_from_summary(self, summary: str) -> str:
        """
        从摘要提取问题描述

        Args:
            summary: 视角摘要

        Returns:
            str: 问题描述
        """
        # 简单提取：去除"视角"、"角度"等前缀
        prefixes = ["视角：", "角度：", "：", ": "]
        issue = summary
        for prefix in prefixes:
            if prefix in issue:
                issue = issue.split(prefix, 1)[-1]
                break

        # 限制长度
        if len(issue) > 200:
            issue = issue[:200] + "..."

        return issue.strip()

    def _generate_expert_recommendations(
        self,
        perspectives: list[Perspective],
        critical_issues: list[CriticalIssue],
        risk_assessment: RiskAssessment,
    ) -> list[ExpertRecommendation]:
        """
        生成专家建议（行动项清单）

        注意：这是行动项清单，不是问题

        Args:
            perspectives: 完成的视角列表
            critical_issues: 关键问题列表
            risk_assessment: 风险评估

        Returns:
            list[ExpertRecommendation]: 专家建议列表
        """
        recommendations: list[ExpertRecommendation] = []

        # 基于关键问题生成建议
        for issue in critical_issues:
            # 根据严重程度确定优先级
            if issue.severity == RiskLevel.CRITICAL:
                priority = Priority.P0
            elif issue.severity == RiskLevel.HIGH:
                priority = Priority.P1
            else:
                priority = Priority.P2

            # 从问题生成行动建议
            action = self._generate_action_from_issue(issue)

            recommendations.append(ExpertRecommendation(
                priority=priority,
                action=action,
                owner=self._suggest_owner(issue.domain),
                domain=issue.domain,
            ))

        # 按优先级排序
        priority_order = {Priority.P0: 0, Priority.P1: 1, Priority.P2: 2}
        recommendations.sort(key=lambda r: priority_order[r.priority])

        return recommendations[:10]  # 最多返回 10 个建议

    def _generate_action_from_issue(self, issue: CriticalIssue) -> str:
        """
        从问题生成行动建议

        Args:
            issue: 关键问题

        Returns:
            str: 行动建议
        """
        # 简单的行动建议生成
        issue_text = issue.issue

        # 如果问题已经是"缺少xxx"格式，转为"补充xxx"
        if "缺少" in issue_text or "缺失" in issue_text:
            return f"补充{issue_text.replace('缺少', '').replace('缺失', '').strip()}"

        # 如果问题已经是"存在xxx问题"格式，转为"解决xxx问题"
        if "存在" in issue_text and "问题" in issue_text:
            return f"解决{issue_text.replace('存在', '').strip()}"

        # 默认：处理该问题
        return f"处理{issue_text}"

    def _suggest_owner(self, domain: str) -> str:
        """
        建议责任方

        Args:
            domain: 领域

        Returns:
            str: 建议的责任方
        """
        owner_map = {
            Domain.SECURITY.value: "security_team",
            Domain.LEGAL.value: "legal_team",
            Domain.DATABASE.value: "dba_team",
            Domain.OPS.value: "ops_team",
            Domain.TECH.value: "dev_team",
            Domain.ARCHITECTURE.value: "arch_team",
        }
        return owner_map.get(domain, f"{domain}_team")

    def _extract_go_live_conditions(
        self,
        perspectives: list[Perspective],
        critical_issues: list[CriticalIssue],
        recommendations: list[ExpertRecommendation],
    ) -> list[str]:
        """
        提取上线条件（前置条件）

        注意：这是前置条件，不是问题或行动项

        Args:
            perspectives: 完成的视角列表
            critical_issues: 关键问题列表
            recommendations: 专家建议列表

        Returns:
            list[str]: 上线条件列表
        """
        conditions: list[str] = []

        # 基于 P0/P1 建议生成上线条件
        for rec in recommendations:
            if rec.priority in [Priority.P0, Priority.P1]:
                # 将行动转为完成条件
                condition = self._action_to_condition(rec.action)
                conditions.append(condition)

        # 从视角中提取隐含条件
        for p in perspectives:
            if p.role != "expert":
                continue
            # 检查 summary 中是否包含条件关键词
            if "需要" in p.summary or "必须" in p.summary:
                condition = self._extract_condition_from_summary(p.summary)
                if condition and condition not in conditions:
                    conditions.append(condition)

        return conditions[:10]  # 最多返回 10 个条件

    def _action_to_condition(self, action: str) -> str:
        """
        将行动转为完成条件

        Args:
            action: 行动建议

        Returns:
            str: 完成条件
        """
        # 行动 -> 条件
        # "处理xxx" -> "完成xxx处理"
        # "补充xxx" -> "完成xxx补充"
        if action.startswith("处理"):
            return f"完成{action[2:]}处理"
        elif action.startswith("补充"):
            return f"完成{action[2:]}补充"
        elif action.startswith("解决"):
            return f"完成{action[2:]}解决"
        elif action.startswith("修复"):
            return f"完成{action}"
        else:
            return f"完成{action}"

    def _extract_condition_from_summary(self, summary: str) -> Optional[str]:
        """
        从摘要提取条件

        Args:
            summary: 视角摘要

        Returns:
            Optional[str]: 条件文本
        """
        import re

        # 匹配"需要xxx"或"必须xxx"
        match = re.search(r"(?:需要|必须)([^，。！？]+)", summary)
        if match:
            return f"完成{match.group(1).strip()}"

        return None

    def _generate_summary(
        self,
        question: str,
        perspectives: list[Perspective],
        risk_assessment: RiskAssessment,
        critical_issues: list[CriticalIssue],
    ) -> str:
        """
        生成诊断摘要

        优化：当专家视角数量不足时，增加警告提示

        Args:
            question: 问题
            perspectives: 完成的视角列表
            risk_assessment: 风险评估
            critical_issues: 关键问题列表

        Returns:
            str: 诊断摘要
        """
        expert_count = sum(1 for p in perspectives if p.role == "expert")
        overall_risk = risk_assessment.overall

        # 风险描述
        risk_desc = {
            RiskLevel.CRITICAL: "存在严重风险",
            RiskLevel.HIGH: "存在较高风险",
            RiskLevel.MEDIUM: "存在中等风险",
            RiskLevel.LOW: "风险较低",
        }

        # 基础摘要
        if expert_count == 0:
            return "无专家视角参与诊断，建议补充专家意见。"

        summary_parts = [f"综合 {expert_count} 位专家视角"]

        # 优化：专家视角数量不足警告（G5-3, G5-8 场景）
        # 对于复杂问题，建议至少 2 个专家视角
        min_experts = 2
        complex_keywords = ["架构升级", "系统迁移", "大促", "活动", "合规", "跨境", "安全事件", "数据泄露"]
        is_complex_scenario = any(kw in question.lower() for kw in complex_keywords)

        if expert_count < min_experts and is_complex_scenario:
            summary_parts.append("专家视角数量不足")
            logger.info("[G5] 检测到复杂场景但专家视角数量不足: expert_count=%d", expert_count)

        # 风险判断
        summary_parts.append(risk_desc[overall_risk])

        # 问题数量
        if len(critical_issues) > 0:
            p0_count = sum(1 for i in critical_issues if i.severity == RiskLevel.CRITICAL)
            if p0_count > 0:
                summary_parts.append(f"发现 {p0_count} 个严重问题")
            else:
                summary_parts.append(f"发现 {len(critical_issues)} 个需要关注的问题")
            summary_parts.append("建议暂缓上线")
        else:
            summary_parts.append("未发现关键问题")

        # 专家视角不足时的建议
        if expert_count < min_experts and is_complex_scenario:
            summary_parts.append("建议补充更多专家视角以获得全面评估")

        return "，".join(summary_parts) + "。"

    def _generate_recommendation(
        self,
        question: str,
        perspectives: list[Perspective],
        risk_assessment: RiskAssessment,
        critical_issues: list[CriticalIssue],
        partial_success: bool,
        warnings: list[str],
    ) -> Optional[Recommendation]:
        """
        生成单一建议

        优化策略：
        1. 优先使用 LLM（保证质量），但优化输入数据
        2. 只传关键信息（不传完整的 perspective 对象）
        3. 要求 concise output（缩短响应时间）
        4. Fallback 到规则（当 LLM 失败时）

        Args:
            question: 问题
            perspectives: 视角列表
            risk_assessment: 风险评估
            critical_issues: 关键问题列表
            partial_success: 是否部分成功
            warnings: 警告列表

        Returns:
            Optional[Recommendation]: 建议对象
        """
        logger.info("[G5-REC] 开始生成单一建议（优化版本）")

        # 防御性类型检查：确保 perspectives 是列表
        if perspectives is None:
            logger.warning("[G5-REC] perspectives 为 None，返回 None")
            return None

        if not isinstance(perspectives, list):
            logger.error("[G5-REC] perspectives 不是列表，类型: %s，值: %s",
                        type(perspectives).__name__, perspectives)
            # 尝试转换为列表
            if isinstance(perspectives, Perspective):
                perspectives = [perspectives]
                logger.info("[G5-REC] 已将单个 Perspective 对象转换为列表")
            else:
                logger.error("[G5-REC] 无法转换 perspectives，返回 None")
                return None

        logger.info("[G5-REC] perspectives 列表长度: %d", len(perspectives))

        # 优先使用 LLM（保证质量）
        if self._recommendation_service is not None:
            try:
                # 关键优化：创建精简版的 perspectives 数据
                # 不传完整的 perspective 对象，只传关键字段
                condensed_perspectives = self._condense_perspectives_for_llm(perspectives)

                # 安全地计算和日志
                try:
                    before_count = sum(len(p.model_dump()) for p in perspectives)
                    after_count = sum(len(p.model_dump()) for p in condensed_perspectives)
                    logger.info("[G5-REC] 优化输入: perspectives 数据从 %d 字段精简到 %d 字段",
                               before_count, after_count)
                except Exception as log_err:
                    logger.warning("[G5-REC] 计算精简数据量失败: %s", log_err)

                fusion_rec = self._recommendation_service.generate(
                    question=question,
                    driver_bot_id=None,
                    perspectives=condensed_perspectives,  # 传入精简数据
                    partial_success=partial_success,
                    warnings=warnings,
                )

                logger.info("[G5-REC] LLM 生成成功: decision=%s", fusion_rec.decision.value)
                return Recommendation(
                    summary=fusion_rec.summary,
                    decision=fusion_rec.decision.value,
                    risks=fusion_rec.risks,
                    next_actions=fusion_rec.next_actions,
                )
            except Exception as e:
                logger.warning("[G5-REC] LLM 生成失败: %s，使用规则 fallback", e)
                # 不要完全失败，fallback 到规则
        else:
            logger.info("[G5-REC] _recommendation_service 未注入，使用规则 fallback")

        # Fallback: 规则生成（保证可用性）
        return self._generate_fallback_recommendation(
            perspectives=perspectives,
            risk_assessment=risk_assessment,
            critical_issues=critical_issues,
            partial_success=partial_success,
            warnings=warnings,
        )

    def _condense_perspectives_for_llm(self, perspectives: list[Perspective]) -> list[Perspective]:
        """
        精简 perspectives 数据，减少 LLM 输入量

        优化点：
        1. 限制 summary 长度（只保留关键结论）
        2. 只保留 top-3 key_points 和 concerns
        3. 移除冗长的 evidence

        Args:
            perspectives: 原始视角列表

        Returns:
            list[Perspective]: 精简后的视角列表
        """
        # 防御性类型检查
        if perspectives is None:
            logger.warning("[G5-CONDENSE] perspectives 为 None，返回空列表")
            return []

        if not isinstance(perspectives, list):
            logger.error("[G5-CONDENSE] perspectives 不是列表，类型: %s", type(perspectives).__name__)
            if isinstance(perspectives, Perspective):
                perspectives = [perspectives]
                logger.info("[G5-CONDENSE] 已将单个 Perspective 对象转换为列表")
            else:
                logger.error("[G5-CONDENSE] 无法转换 perspectives，返回空列表")
                return []

        condensed = []
        for p in perspectives:
            # 类型检查每个元素
            if not isinstance(p, Perspective):
                logger.warning("[G5-CONDENSE] 跳过非 Perspective 元素: %s", type(p).__name__)
                continue
            # 精简 summary：只保留前 300 字符（核心结论通常在前面）
            summary = p.summary[:300] if p.summary and len(p.summary) > 300 else p.summary

            # 精简 key_points：只保留前 3 个
            key_points = p.key_points[:3] if p.key_points else []

            # 精简 concerns：只保留前 3 个
            concerns = p.concerns[:3] if p.concerns else []

            # 精简 evidence：只保留前 2 个
            evidence = p.evidence[:2] if p.evidence else []

            # 创建精简版的 Perspective
            condensed_p = Perspective(
                participant_id=p.participant_id,
                participant_type=p.participant_type,
                role=p.role,
                summary=summary,
                confidence=p.confidence,
                evidence=evidence,
                status=p.status,
                key_points=key_points,
                concerns=concerns,
            )
            condensed.append(condensed_p)

        return condensed

    def _generate_structured_risk_assessment(
        self,
        question: str,
        perspectives: list[Perspective],
        risk_assessment: RiskAssessment,
        critical_issues: list[CriticalIssue],
        expert_risks: Optional[dict[str, RiskLevel]],
    ) -> "StructuredRiskAssessment":
        """
        生成结构化风险评估（G5 V2）

        当 ENABLE_G5_STRUCTURED_RISK 开启时调用。

        Args:
            question: 问题
            perspectives: 完成的视角列表
            risk_assessment: 基础风险评估
            critical_issues: 关键问题列表
            expert_risks: 专家风险评估映射

        Returns:
            StructuredRiskAssessment: 结构化风险评估
        """
        from src.domain.models.structured_risk_assessment import (
            RiskFactor,
            BlockingCondition,
            ExpertEvidence,
            ScenarioPriorRisk,
            StructuredRiskAssessment,
        )

        logger.info("[G5-V2] 开始生成结构化风险评估")

        # 1. 场景先验风险
        scenario_prior_risk = None
        if FeatureFlags.is_g5_scenario_prior_risk_enabled():
            baseline_risk = self._infer_baseline_risk_from_question(question)
            scenario_prior_risk = ScenarioPriorRisk(
                scenario_type=self._detect_scenario_type(question),
                matched_keywords=self._extract_matched_keywords(question),
                baseline_risk=baseline_risk,
                confidence=0.8,
            )
            logger.info("[G5-V2] 场景先验风险: type=%s, baseline=%s",
                       scenario_prior_risk.scenario_type, baseline_risk.value)

        # 2. 风险因素列表
        risk_factors: list[RiskFactor] = []
        factor_id = 0
        for issue in critical_issues:
            factor_id += 1
            risk_factors.append(RiskFactor(
                factor_id=f"RF-{factor_id:03d}",
                description=issue.issue,
                category=issue.domain,
                severity=issue.severity,
                likelihood="high" if issue.severity in [RiskLevel.CRITICAL, RiskLevel.HIGH] else "medium",
                impact="high" if issue.severity in [RiskLevel.CRITICAL, RiskLevel.HIGH] else "medium",
                evidence=[issue.description] if issue.description else [],
                expert_sources=[issue.source] if issue.source else [],
            ))

        # 3. 阻塞条件
        blocking_conditions: list[BlockingCondition] = []
        for issue in critical_issues:
            if issue.severity in [RiskLevel.CRITICAL, RiskLevel.HIGH]:
                blocking_conditions.append(BlockingCondition(
                    condition_id=f"BC-{issue.domain}-{len(blocking_conditions)+1}",
                    description=f"需要解决: {issue.issue}",
                    blocking_reason=f"存在{issue.severity.value}风险",
                    required_actions=[f"处理 {issue.source} 反馈的问题"],
                ))

        # 4. 专家证据
        supporting_evidence: list[ExpertEvidence] = []
        for p in perspectives:
            if p.role == "expert" and p.summary:
                supporting_evidence.append(ExpertEvidence(
                    expert_id=p.participant_id,
                    expert_domain=self._infer_domain_from_participant(p.participant_id),
                    evidence_text=p.summary[:500] if len(p.summary) > 500 else p.summary,
                    evidence_type="opinion",
                    confidence=p.confidence or 0.5,
                ))

        # 5. 决策理由
        decision_rationale = self._generate_decision_rationale(
            risk_assessment=risk_assessment,
            critical_issues=critical_issues,
        )

        # 6. 最终风险等级
        final_risk_level = risk_assessment.overall
        # 如果有阻塞条件，风险等级至少为 high
        if blocking_conditions and final_risk_level == RiskLevel.MEDIUM:
            final_risk_level = RiskLevel.HIGH

        logger.info("[G5-V2] 结构化风险评估完成: risk=%s, factors=%d, blocking=%d",
                   final_risk_level.value, len(risk_factors), len(blocking_conditions))

        return StructuredRiskAssessment(
            risk_level=final_risk_level,
            baseline_risk=risk_assessment.categories.get("baseline"),
            risk_factors=risk_factors,
            blocking_conditions=blocking_conditions,
            supporting_evidence=supporting_evidence,
            decision_rationale=decision_rationale,
            scenario_prior_risk=scenario_prior_risk,
        )

    def _detect_scenario_type(self, question: str) -> str:
        """
        检测场景类型

        Args:
            question: 问题文本

        Returns:
            str: 场景类型
        """
        question_lower = question.lower()

        # 使用 TaxonomyRegistry 检测场景
        if FeatureFlags.is_taxonomy_registry_enabled():
            from src.domain.taxonomy import get_taxonomy_registry
            registry = get_taxonomy_registry()
            signal = registry.find_risk_signal_by_keyword(question_lower)
            if signal:
                return signal[1].name

        # Legacy 场景检测
        scenario_types = [
            (["数据泄露", "信息泄露", "隐私泄露"], "data_leakage"),
            (["安全事件", "安全事故"], "security_incident"),
            (["跨境支付", "牌照申请"], "regulatory_access"),
            (["架构升级", "系统迁移"], "system_migration"),
            (["大促", "双11", "618"], "promotion_activity"),
            (["新业务", "新产品"], "new_business"),
        ]

        for keywords, scenario_type in scenario_types:
            if any(kw in question_lower for kw in keywords):
                return scenario_type

        return "general"

    def _extract_matched_keywords(self, question: str) -> list[str]:
        """
        提取匹配的关键词

        Args:
            question: 问题文本

        Returns:
            list[str]: 匹配的关键词列表
        """
        question_lower = question.lower()
        matched = []

        # 使用 TaxonomyRegistry 提取关键词
        if FeatureFlags.is_taxonomy_registry_enabled():
            from src.domain.taxonomy import get_taxonomy_registry
            registry = get_taxonomy_registry()
            # 检查各风险等级关键词
            for kw in registry.get_critical_keywords():
                if kw in question_lower and kw not in matched:
                    matched.append(kw)
            for kw in registry.get_high_keywords()[:10]:  # 限制数量
                if kw in question_lower and kw not in matched:
                    matched.append(kw)
            return matched[:10]  # 最多返回 10 个

        # Legacy 关键词提取
        critical_keywords = [
            "数据泄露", "信息泄露", "安全事件", "监管函", "处罚",
        ]
        high_keywords = [
            "核心系统", "架构升级", "跨境支付", "合规", "整改",
        ]

        for kw in critical_keywords + high_keywords:
            if kw in question_lower and kw not in matched:
                matched.append(kw)

        return matched[:10]

    def _generate_decision_rationale(
        self,
        risk_assessment: RiskAssessment,
        critical_issues: list[CriticalIssue],
    ) -> str:
        """
        生成决策理由

        Args:
            risk_assessment: 风险评估
            critical_issues: 关键问题列表

        Returns:
            str: 决策理由
        """
        parts = []

        # 风险等级描述
        risk_desc = {
            RiskLevel.CRITICAL: "存在严重风险，建议暂缓上线",
            RiskLevel.HIGH: "存在较高风险，需要重点关注",
            RiskLevel.MEDIUM: "存在中等风险，需要适当关注",
            RiskLevel.LOW: "风险较低",
        }
        parts.append(risk_desc[risk_assessment.overall])

        # 问题统计
        if critical_issues:
            critical_count = sum(1 for i in critical_issues if i.severity == RiskLevel.CRITICAL)
            high_count = sum(1 for i in critical_issues if i.severity == RiskLevel.HIGH)
            if critical_count > 0:
                parts.append(f"发现 {critical_count} 个严重问题")
            if high_count > 0:
                parts.append(f"发现 {high_count} 个高风险问题")

        return "；".join(parts) + "。"

    def _generate_fallback_recommendation(
        self,
        perspectives: list[Perspective],
        risk_assessment: RiskAssessment,
        critical_issues: list[CriticalIssue],
        partial_success: bool,
        warnings: list[str],
    ) -> Recommendation:
        """
        Fallback: 规则生成 recommendation（当 LLM 失败时）

        基于 G5 已有的结构化数据生成合理的 recommendation。

        Args:
            perspectives: 视角列表
            risk_assessment: 风险评估
            critical_issues: 关键问题列表
            partial_success: 是否部分成功
            warnings: 警告列表

        Returns:
            Recommendation: 基于规则的建议
        """
        logger.info("[G5-REC-FALLBACK] 使用规则生成 recommendation")

        completed = [p for p in perspectives if p.status == "completed"]
        failed = [p for p in perspectives if p.status in ("failed", "timed_out", "skipped")]

        # 风险汇总
        risks: list[str] = []
        for p in failed:
            risks.append(f"{p.participant_id} 专家视角缺失")

        for issue in critical_issues:
            if issue.severity in [RiskLevel.CRITICAL, RiskLevel.HIGH]:
                risks.append(f"存在{issue.severity.value}风险: {issue.issue[:50]}")

        # 下一步行动
        next_actions: list[str] = []
        for issue in critical_issues[:3]:
            next_actions.append(f"处理 {issue.source} 反馈的问题")

        if partial_success:
            next_actions.append("补充缺失的专家视角")

        # 决策逻辑（基于风险评估）
        if risk_assessment.overall == RiskLevel.CRITICAL:
            decision = "no"
            summary = "存在严重风险，建议暂缓上线，优先处理关键问题。"
        elif risk_assessment.overall == RiskLevel.HIGH:
            decision = "conditional_yes"
            summary = "存在较高风险，建议有条件推进，需优先解决高风险问题。"
        elif risk_assessment.overall == RiskLevel.MEDIUM:
            decision = "conditional_yes"
            summary = "存在中等风险，建议有条件推进，关注风险项。"
        elif len(failed) > 0 and len(completed) < 2:
            decision = "needs_more_information"
            summary = "专家视角不足，建议补充更多专家意见。"
        else:
            decision = "yes"
            summary = "风险较低，可以推进，建议持续关注潜在风险。"

        logger.info("[G5-REC-FALLBACK] 规则生成完成: decision=%s", decision)

        return Recommendation(
            summary=summary,
            decision=decision,
            risks=risks,
            next_actions=next_actions,
        )

    def _build_fallback_result(
        self,
        question: str,
        perspectives: list[Perspective],
        driver_bot_id: Optional[str] = None,
    ) -> FusionResult:
        """
        构建 fallback 结果（当 G5 feature flag 关闭时）

        Args:
            question: 问题
            perspectives: 视角列表
            driver_bot_id: Driver bot ID

        Returns:
            FusionResult: 基本的融合结果
        """
        import uuid

        # 生成 fusion_id
        fusion_id = f"fus-{uuid.uuid4().hex[:12]}"

        started_at = datetime.now()

        # 计算基本统计
        completed = [p for p in perspectives if p.status == "completed"]
        warnings = [f"{p.participant_id} {p.status}" for p in perspectives if p.status != "completed"]

        # 生成基本建议
        summary = f"综合 {len(completed)} 个视角的分析结果。"
        if warnings:
            summary += f" 注意：{len(warnings)} 个视角未成功完成。"

        recommendation = Recommendation(
            summary=summary,
            decision="conditional_yes" if len(completed) > 0 else "needs_more_information",
            risks=warnings[:3],
            next_actions=["建议启用 G5 专家诊断功能以获得更详细的诊断结果"],
        )

        finished_at = datetime.now()
        duration_ms = int((finished_at - started_at).total_seconds() * 1000)

        return FusionResult(
            group_id="",  # 由 GroupFusionService 设置
            fusion_id=fusion_id,
            question=question,
            driver_bot_id=driver_bot_id,
            perspectives=perspectives,
            recommendation=recommendation,
            partial_success=len(completed) > 0 and len(completed) < len(perspectives),
            warnings=warnings,
            errors=[],
            timing=FusionTiming(
                started_at=started_at,
                finished_at=finished_at,  # 修复：使用正确的字段名
                duration_ms=duration_ms,
            ),
            fusion_mode="expert_diagnosis",  # G5 模式
            risk_assessment=None,  # 无 LLM 分析，无法评估风险
            critical_issues=[],
            recommendations=[],
            go_live_conditions=[],
            summary="G5 专家诊断功能未启用，已使用基础融合逻辑。",
        )


__all__ = [
    "ExpertDiagnosisService",
]