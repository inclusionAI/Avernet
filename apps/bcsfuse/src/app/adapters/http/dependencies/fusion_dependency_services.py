"""Composition for G2/G5/G9 fusion services."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import TYPE_CHECKING, Optional

from app.application.services.expert_diagnosis_service import ExpertDiagnosisService
from app.application.services.group_fusion_service import GroupFusionService

if TYPE_CHECKING:
    from app.domain.services.llm_provider import LLMProvider
    from app.infra.llm.config.llm_settings import LLMSettings

from app.adapters.http.dependencies import fusion_dependencies as fusion

logger = logging.getLogger(__name__)

# Cached fusion service instances. They are intentionally local to this layer;
# reset_fusion_services() delegates their invalidation to reset_runtime_services().
_expert_diagnosis_service: Optional["ExpertDiagnosisService"] = None
_g5_expert_enhancer = None
_context_preparation_service = None
_llm_gateway_service = None
_group_fusion_service: Optional["GroupFusionService"] = None
_structured_signal_extractor = None
_conflict_dimension_analyzer = None
_fused_profile_storage_service = None
_profile_merge_service = None
_group_context_service = None
_conflict_alignment_service = None
_perspective_provider = None


def reset_runtime_services() -> None:
    """Invalidate only service instances owned by this module."""
    global _expert_diagnosis_service, _g5_expert_enhancer
    global _context_preparation_service, _llm_gateway_service
    global _group_fusion_service, _structured_signal_extractor
    global _conflict_dimension_analyzer, _conflict_alignment_service
    global _perspective_provider, _profile_merge_service
    global _group_context_service

    _expert_diagnosis_service = None
    _g5_expert_enhancer = None
    _context_preparation_service = None
    _llm_gateway_service = None
    _group_fusion_service = None
    _structured_signal_extractor = None
    _conflict_dimension_analyzer = None
    _conflict_alignment_service = None
    _perspective_provider = None
    _profile_merge_service = None
    _group_context_service = None




def set_fused_profile_storage_service(storage_service) -> None:
    """Replace composed G9 storage and invalidate services that captured it."""
    global _fused_profile_storage_service
    global _profile_merge_service, _fusion_expert_chat_service
    global _group_fusion_service

    fusion._fused_profile_storage_service = storage_service
    fusion._profile_merge_service = None
    fusion._fusion_expert_chat_service = None
    fusion._group_fusion_service = None


def get_expert_diagnosis_service() -> ExpertDiagnosisService:
    """
    获取 Expert Diagnosis Service 实例

    这个服务用于 G5 专家会诊场景。
    它会使用带 Registry-aware filtering 的 candidate recommendation service。

    Phase Fix: 注入 g5_enhancer 和 recommendation_service 以启用真正的 LLM 链路。
    """
    global _expert_diagnosis_service
    if _expert_diagnosis_service is None:
        import os
        from datetime import datetime
        logger.info("="*80)
        logger.info("[DEP-DIAG] ========== 创建 ExpertDiagnosisService 单例 ==========")
        logger.info("[DEP-DIAG] PID: %d", os.getpid())
        logger.info("[DEP-DIAG] 调用时间: %s", datetime.now().isoformat())

        # 创建 LLM recommendation service
        logger.info("[DEP-DIAG] Step 1: 创建 LLM recommendation service...")
        recommendation_service = fusion._create_llm_recommendation_service()
        logger.info("[DEP-DIAG] recommendation_service: %s", "已创建" if recommendation_service else "None")

        # 创建 G5 expert enhancer（需要 LLM gateway）
        logger.info("[DEP-DIAG] Step 2: 创建 G5 expert enhancer...")
        g5_enhancer = fusion._create_g5_expert_enhancer()
        logger.info("[DEP-DIAG] g5_enhancer: %s", "已创建" if g5_enhancer else "None")

        # 获取 candidate recommendation service
        logger.info("[DEP-DIAG] Step 3: 获取 candidate recommendation service...")
        candidate_rec_svc = fusion.get_candidate_recommendation_service()
        logger.info("[DEP-DIAG] candidate_recommendation_service: %s", "已创建" if candidate_rec_svc else "None")
        if candidate_rec_svc:
            logger.info("[DEP-DIAG]   - vector_match_service: %s",
                       "已注入" if candidate_rec_svc._vector_match_service else "None")

        _expert_diagnosis_service = ExpertDiagnosisService(
            recommendation_service=recommendation_service,
            g5_enhancer=g5_enhancer,
            candidate_recommendation_service=candidate_rec_svc,
        )

        if g5_enhancer:
            logger.info("[DEP-DIAG] ✅ Expert diagnosis service 创建完成，带 G5 LLM enhancer")
        else:
            logger.warning("[DEP-DIAG] ⚠️ Expert diagnosis service 创建完成，但无 G5 LLM enhancer (仅使用规则)")
        logger.info("[DEP-DIAG] ========== ExpertDiagnosisService 单例创建完成 ==========")
        logger.info("="*80)
    else:
        logger.debug("[DEP-DIAG] 返回缓存的 ExpertDiagnosisService 单例 (id=%d)", id(_expert_diagnosis_service))

    return _expert_diagnosis_service


def _get_context_preparation_service():
    """
    获取 Context Preparation Service 实例

    Returns:
        WorkerContextPreparationService 或 None
    """
    global _context_preparation_service
    if _context_preparation_service is None:
        import os
        from datetime import datetime
        logger.info("[DEP-DIAG] 创建 ContextPreparationService 单例...")
        logger.info("[DEP-DIAG] PID: %d, 时间: %s", os.getpid(), datetime.now().isoformat())
        from app.domain.services.worker_context_preparation_service import WorkerContextPreparationService
        _context_preparation_service = WorkerContextPreparationService()
        logger.info("[DEP-DIAG] ContextPreparationService 创建完成 (id=%d)", id(_context_preparation_service))
    return _context_preparation_service


def _create_llm_provider(settings: "LLMSettings") -> "LLMProvider":
    """Create an LLM provider based on LLM_API_TYPE environment variable."""
    import os
    llm_api_type = os.environ.get("LLM_API_TYPE", "anthropic").lower()
    if llm_api_type in ("openai", "openai-completions"):
        from app.infra.llm.providers.openai_compatible_provider import OpenAICompatibleProvider
        logger.info("[DEP-DIAG] Using OpenAICompatibleProvider (LLM_API_TYPE=%s)", llm_api_type)
        return OpenAICompatibleProvider(settings=settings)
    from app.infra.llm.providers.anthropic_compatible_provider import AnthropicCompatibleProvider
    logger.info("[DEP-DIAG] Using AnthropicCompatibleProvider (LLM_API_TYPE=%s)", llm_api_type)
    return AnthropicCompatibleProvider(settings=settings)


def _get_llm_gateway_service():
    """
    获取 LLM Gateway Service 实例

    Returns:
        LLMGatewayService 或 None（如果 LLM 未配置）
    """
    global _llm_gateway_service
    if _llm_gateway_service is None:
        import os
        from datetime import datetime
        logger.info("="*80)
        logger.info("[DEP-DIAG] ========== 创建 LLMGatewayService 单例 ==========")
        logger.info("[DEP-DIAG] PID: %d", os.getpid())
        logger.info("[DEP-DIAG] 调用时间: %s", datetime.now().isoformat())

        # 检查是否启用 LLM - 支持 canonical flag 和 legacy flag
        canonical_enabled = os.environ.get("ENABLE_REAL_LLM", "").lower() == "true"
        legacy_enabled = os.environ.get("LLM_ENABLED", "").lower() == "true"
        llm_enabled = canonical_enabled or legacy_enabled

        logger.info("[DEP-DIAG] 环境变量 ENABLE_REAL_LLM: %s", os.environ.get("ENABLE_REAL_LLM", "not_set"))
        logger.info("[DEP-DIAG] 环境变量 LLM_ENABLED: %s", os.environ.get("LLM_ENABLED", "not_set"))
        logger.info("[DEP-DIAG] LLM enabled: %s (source: %s)", llm_enabled, "ENABLE_REAL_LLM" if canonical_enabled else "LLM_ENABLED" if legacy_enabled else "none")

        if not llm_enabled:
            logger.warning("[DEP-DIAG] ⚠️ LLM 未启用 (ENABLE_REAL_LLM 和 LLM_ENABLED 都不是 true)，LLM gateway 不会被创建")
            logger.info("[DEP-DIAG] ========== LLMGatewayService 创建跳过 ==========")
            return None

        # 检查必要的环境变量。Token 值保持在 LLMSettings 内部，不落日志、不留局部凭据变量。
        base_url = os.environ.get("LLM_BASE_URL")
        from app.infra.llm.config.llm_settings import LLMSettings
        settings = LLMSettings()
        logger.info("[DEP-DIAG] LLM_BASE_URL: %s", base_url if base_url else "未设置")
        logger.info("[DEP-DIAG] LLM_AUTH_TOKEN: %s", "已设置" if settings.auth_token else "未设置")

        if not base_url or not settings.auth_token:
            logger.warning("[DEP-DIAG] ⚠️ LLM 配置不完整，gateway 不会被创建")
            logger.info("[DEP-DIAG] ========== LLMGatewayService 创建失败 ==========")
            return None

        try:
            from app.infra.llm.config.llm_settings import LLMSettings
            from app.infra.llm.routing.static_llm_router import StaticLLMRouter
            from app.application.services.llm_gateway_service import LLMGatewayService

            logger.info("[DEP-DIAG] 创建 LLMSettings...")
            settings = LLMSettings()
            logger.info("[DEP-DIAG] LLMSettings: model=%s", getattr(settings, 'model', 'N/A'))

            provider = fusion._create_llm_provider(settings)
            logger.info("[DEP-DIAG] provider id: %d", id(provider))

            logger.info("[DEP-DIAG] 创建 StaticLLMRouter...")
            router = StaticLLMRouter(settings=settings)
            logger.info("[DEP-DIAG] router id: %d", id(router))

            logger.info("[DEP-DIAG] 创建 LLMGatewayService...")
            _llm_gateway_service = LLMGatewayService(provider=provider, router=router)
            logger.info("[DEP-DIAG] ✅ LLMGatewayService 创建完成 (id=%d)", id(_llm_gateway_service))
            logger.info("[DEP-DIAG] ========== LLMGatewayService 单例创建完成 ==========")
            logger.info("="*80)
        except Exception as e:
            logger.error("[DEP-DIAG] ❌ 创建 LLMGatewayService 失败: %s", e, exc_info=True)
            logger.info("[DEP-DIAG] ========== LLMGatewayService 创建失败 ==========")
            return None

    else:
        logger.debug("[DEP-DIAG] 返回缓存的 LLMGatewayService 单例 (id=%d)", id(_llm_gateway_service))

    return _llm_gateway_service


def _create_g5_expert_enhancer():
    """
    创建 G5 Expert Enhancer 实例

    G5 Expert Enhancer 需要：
    1. LLM Gateway Service - 用于 LLM 调用
    2. Profile Retrieval Service - 用于检索专家 profile
    3. Context Preparation Service - 用于准备上下文
    4. Profile Source - 用于获取 profile 内容

    Returns:
        G5ExpertEnhancerImpl 或 None（如果依赖不可用）
    """
    global _g5_expert_enhancer
    if _g5_expert_enhancer is None:
        import os
        from datetime import datetime
        logger.info("="*80)
        logger.info("[DEP-DIAG] ========== 创建 G5ExpertEnhancer 单例 ==========")
        logger.info("[DEP-DIAG] PID: %d", os.getpid())
        logger.info("[DEP-DIAG] 调用时间: %s", datetime.now().isoformat())

        # 获取 LLM gateway（必需）
        logger.info("[DEP-DIAG] Step 1: 获取 LLM gateway...")
        gateway = fusion._get_llm_gateway_service()
        if gateway is None:
            logger.warning("[DEP-DIAG] ⚠️ 无法创建 G5 enhancer: LLM gateway 不可用")
            logger.info("[DEP-DIAG] ========== G5ExpertEnhancer 创建失败 (无 LLM) ==========")
            logger.info("="*80)
            return None
        logger.info("[DEP-DIAG] gateway已获取 (id=%d)", id(gateway))

        # 获取 retrieval service（必需）
        logger.info("[DEP-DIAG] Step 2: 获取 retrieval service...")
        retrieval_service = fusion.get_profile_retrieval_service()
        logger.info("[DEP-DIAG] retrieval_service 已获取 (id=%d)", id(retrieval_service))

        # 获取 context preparation service（必需）
        logger.info("[DEP-DIAG] Step 3: 获取 context preparation service...")
        preparation_service = fusion._get_context_preparation_service()
        logger.info("[DEP-DIAG] preparation_service 已获取 (id=%d)", id(preparation_service))

        # 获取 profile source（必需）
        logger.info("[DEP-DIAG] Step 4: 获取 profile source...")
        profile_source = fusion._get_profile_source()
        logger.info("[DEP-DIAG] profile_source 已获取 (id=%d, 类型=%s)",
                   id(profile_source), type(profile_source).__name__)

        try:
            from app.application.services.g5_expert_enhancer_impl import G5ExpertEnhancerImpl

            logger.info("[DEP-DIAG] Step 5: 创建 G5ExpertEnhancerImpl...")
            _g5_expert_enhancer = G5ExpertEnhancerImpl(
                gateway=gateway,
                retrieval_service=retrieval_service,
                preparation_service=preparation_service,
                profile_source=profile_source,
                max_experts=3,
            )
            logger.info("[DEP-DIAG] ✅ G5ExpertEnhancerImpl 创建完成 (id=%d)", id(_g5_expert_enhancer))
            logger.info("[DEP-DIAG] ========== G5ExpertEnhancer 单例创建完成 ==========")
            logger.info("="*80)
        except Exception as e:
            logger.error("[DEP-DIAG] ❌ 创建 G5ExpertEnhancerImpl 失败: %s", e, exc_info=True)
            logger.info("[DEP-DIAG] ========== G5ExpertEnhancer 创建失败 (异常) ==========")
            logger.info("="*80)
            return None

    else:
        logger.debug("[DEP-DIAG] 返回缓存的 G5ExpertEnhancer 单例 (id=%d)", id(_g5_expert_enhancer))

    return _g5_expert_enhancer


def get_group_fusion_service() -> GroupFusionService:
    """
    获取 Group Fusion Service 实例

    这是 Fusion API 的主入口服务。
    G2 模式会使用带 V2 分析能力的 conflict alignment service。
    G5 模式会使用带 Registry-aware filtering 的 expert diagnosis service。
    G9 模式会使用 Profile Fusion Service 融合多个 participant 的 Profile。

    Stage 1 Phase 5:
    - 注入 ParticipantAvailabilityChecker 用于检查显式 participants 是否 offline
    """
    if fusion._group_fusion_service is None:
        import os
        logger.info("="*80)
        logger.info("[DEP-DIAG] ========== 创建 GroupFusionService 单例 ==========")
        logger.info("[DEP-DIAG] PID: %d", os.getpid())
        logger.info("[DEP-DIAG] 调用时间: %s", datetime.now().isoformat())

        # 尝试创建 LLM recommendation service
        logger.info("[DEP-DIAG] Step 1: 创建 LLM recommendation service...")
        llm_rec_service = fusion._create_llm_recommendation_service()
        logger.info("[DEP-DIAG] llm_rec_service: %s", "已创建" if llm_rec_service else "None")

        # Step 1.5: 获取 LLM Gateway（基础服务，其他服务可能依赖）
        logger.info("[DEP-DIAG] Step 1.5: 获取 LLM Gateway...")
        llm_gateway = fusion._get_llm_gateway_service()
        logger.info("[DEP-DIAG] llm_gateway: %s", "已创建" if llm_gateway else "None")

        # Step 2: 创建 perspective provider（LLM 或 Stub）
        logger.info("[DEP-DIAG] Step 2: 创建 perspective provider...")
        perspective_provider = fusion._get_perspective_provider()
        logger.info("[DEP-DIAG] perspective_provider: %s", type(perspective_provider).__name__ if perspective_provider else "None")

        # Step 3: 创建 availability checker
        logger.info("[DEP-DIAG] Step 3: 创建 availability checker...")
        availability_checker = fusion._get_availability_checker()
        logger.info("[DEP-DIAG] availability_checker: %s", "已创建" if availability_checker else "None")

        # 获取 expert diagnosis service（这一步会触发 G5 enhancer 的创建）
        logger.info("[DEP-DIAG] Step 4: 获取 expert diagnosis service...")
        expert_diagnosis_svc = fusion.get_expert_diagnosis_service()
        logger.info("[DEP-DIAG] expert_diagnosis_service: %s", "已创建" if expert_diagnosis_svc else "None")
        if expert_diagnosis_svc:
            logger.info("[DEP-DIAG]   - _g5_enhancer: %s",
                       "已注入" if expert_diagnosis_svc._g5_enhancer else "None")
            logger.info("[DEP-DIAG]   - _candidate_recommendation_service: %s",
                       "已注入" if expert_diagnosis_svc._candidate_recommendation_service else "None")

        # G2 V2: 获取 conflict alignment service（这一步会触发 V2 依赖的创建）
        logger.info("[DEP-DIAG] Step 5: 获取 conflict alignment service...")
        conflict_alignment_svc = fusion.get_conflict_alignment_service()
        logger.info("[DEP-DIAG] conflict_alignment_service: %s", "已创建" if conflict_alignment_svc else "None")
        if conflict_alignment_svc:
            logger.info("[DEP-DIAG]   - _signal_extractor: %s",
                       "已注入" if conflict_alignment_svc._signal_extractor else "None")
            logger.info("[DEP-DIAG]   - _conflict_analyzer: %s",
                       "已注入" if conflict_alignment_svc._conflict_analyzer else "None")

        # G9: 获取 profile merge service
        logger.info("[DEP-DIAG] Step 6: 获取 profile merge service...")
        profile_merge_svc = fusion._get_profile_merge_service()
        logger.info("[DEP-DIAG] profile_merge_service: %s", "已创建" if profile_merge_svc else "None")
        if profile_merge_svc is None:
            logger.warning("[DEP-DIAG] ⚠️ ProfileMergeService 创建失败，G9 模式将不可用")
            logger.warning("[DEP-DIAG] ⚠️ 如果需要使用 G9 模式，请检查:")
            logger.warning("[DEP-DIAG]   - ENABLE_REAL_LLM=true 或 LLM_ENABLED=true 环境变量")
            logger.warning("[DEP-DIAG]   - LLM_BASE_URL 和 LLM_AUTH_TOKEN 配置")
            logger.warning("[DEP-DIAG]   - Profile Store (ZDAS/SQLite) 配置和数据")

        # G9: 获取群组上下文服务（会话总结）
        logger.info("[DEP-DIAG] Step 6.5: 获取 group context service...")
        group_context_svc = fusion._get_group_context_service()
        logger.info("[DEP-DIAG] group_context_service: %s", "已创建" if group_context_svc else "None")

        # G9: 获取 FusionExpertChat 服务（Prompt构建 + LLM调用 + 结果构建）
        logger.info("[DEP-DIAG] Step 6.6: 获取 fusion expert chat service...")
        fusion_expert_chat_svc = fusion._get_fusion_expert_chat_service()
        logger.info("[DEP-DIAG] fusion_expert_chat_service: %s", "已创建" if fusion_expert_chat_svc else "None")

        fusion._group_fusion_service = GroupFusionService(
            provider=perspective_provider,  # 使用 LLM 或 Stub provider
            recommendation_service=llm_rec_service,
            conflict_alignment_service=conflict_alignment_svc,  # G2 V2 服务
            expert_diagnosis_service=expert_diagnosis_svc,  # 带过滤器的 G5 服务
            profile_merge_service=profile_merge_svc,  # G9 Profile Merge 服务
            fusion_expert_chat_service=fusion_expert_chat_svc,  # G9 FusionExpertChat 服务（Prompt构建 + LLM调用）
            group_context_service=group_context_svc,  # G9: 群组上下文服务（会话总结）
            availability_checker=availability_checker,  # Phase 5: offline participant warning
            worker_store=fusion._get_registry_store(),  # G9: batch_get_configs 批量查 fusion_enable
            max_parallel_workers=fusion._max_parallel_workers,  # 并行收集视角配置
        )
        logger.info("[DEP-DIAG] GroupFusionService 创建完成")
        logger.info("[DEP-DIAG] ========== GroupFusionService 单例创建完成 ==========")
        logger.info("="*80)
    else:
        logger.debug("[DEP-DIAG] 返回缓存的 GroupFusionService 单例 (id=%d)", id(fusion._group_fusion_service))

    return fusion._group_fusion_service


def _get_availability_checker():
    """
    获取 Participant Availability Checker 实例

    Stage 1 Phase 5: 用于检查显式 participants 是否可用

    Returns:
        ParticipantAvailabilityChecker 或 None（如果依赖不可用）

    注意：必须使用与 Profile Binding 创建时相同的存储实例（_get_profile_binding_store），
    否则在 ZDAS 模式下会导致绑定查询失败。
    """
    try:
        from app.application.services.participant_availability_checker import ParticipantAvailabilityChecker

        # 从 worker_dependencies 获取 stores
        # 关键：使用 _get_profile_binding_store 而不是创建新的 SQLite 实例
        from app.adapters.http.dependencies.worker_dependencies import (
            get_registry_store,
            _get_runtime_state_store,
            _get_profile_binding_store,  # 使用统一的存储实例
        )

        profile_binding_store = fusion._get_profile_binding_store()  # 使用统一的存储实例（支持 ZDAS）
        runtime_state_store = fusion._get_runtime_state_store()
        registry_store = get_registry_store()  # P1 修复: 注入 registry_store 支持 worker_id fallback

        return ParticipantAvailabilityChecker(
            profile_binding_store=profile_binding_store,
            runtime_state_store=runtime_state_store,
            registry_store=registry_store,  # P1 修复: 允许直接使用 worker_id 作为 participant_id
        )
    except Exception as e:
        logger.warning(f"Failed to create availability checker: {e}")
        return None


def _get_structured_signal_extractor():
    """
    获取 StructuredSignalExtractor 实例（G2 V2）

    Returns:
        StructuredSignalExtractor 或 None
    """
    global _structured_signal_extractor
    if _structured_signal_extractor is None:
        try:
            from app.application.services.structured_signal_extractor import StructuredSignalExtractor
            _structured_signal_extractor = StructuredSignalExtractor()
            logger.info("[G2-V2] StructuredSignalExtractor 创建完成")
        except Exception as e:
            logger.warning(f"[G2-V2] Failed to create StructuredSignalExtractor: {e}")
            return None
    return _structured_signal_extractor


def _get_conflict_dimension_analyzer():
    """
    获取 ConflictDimensionAnalyzer 实例（G2 V2）

    Returns:
        ConflictDimensionAnalyzer 或 None
    """
    global _conflict_dimension_analyzer
    if _conflict_dimension_analyzer is None:
        try:
            from app.domain.services.conflict_dimension_analyzer import ConflictDimensionAnalyzer
            _conflict_dimension_analyzer = ConflictDimensionAnalyzer()
            logger.info("[G2-V2] ConflictDimensionAnalyzer 创建完成")
        except Exception as e:
            logger.warning(f"[G2-V2] Failed to create ConflictDimensionAnalyzer: {e}")
            return None
    return _conflict_dimension_analyzer


def _get_fused_profile_storage_service():
    """
    获取 FusedProfileStorageService 实例（G9）- SQLite only for open-core

    用于 G9 Profile Fusion 模式的融合结果存储。
    包含 L1 内存缓存 + L2 持久化。

    Open-core版本仅支持SQLite存储后端。

    Returns:
        FusedProfileStorageService 或 None（如果依赖不可用）
    """
    if fusion._fused_profile_storage_service is None:
        try:
            from app.application.services.bot_fuse.fused_profile_storage_service import FusedProfileStorageService
            from app.infra.repositories.fused_profile_repository import FusedProfileRepository
            from app.infra.adapters.sqlite_fused_profile_store import SQLiteFusedProfileStore
            from app.infra.config.worker_registry_settings import WorkerRegistrySettings

            # SQLite 模式：本地存储（open-core only）
            registry_settings = WorkerRegistrySettings()
            db_path = registry_settings.get_effective_db_path()
            store = SQLiteFusedProfileStore(db_path)
            logger.info("[Open-Core] Using SQLite FusedProfileStore, path=%s", db_path)

            fusion._fused_profile_storage_service = FusedProfileStorageService(
                repository=store,
                enable_memory_cache=True,
                cache_ttl_seconds=86400,  # 1 天缓存
            )
            logger.info("[Open-Core] FusedProfileStorageService 创建完成")
        except Exception as e:
            logger.error("[Open-Core] Failed to create FusedProfileStorageService: %s", e)
            return None
    return fusion._fused_profile_storage_service


def _get_profile_merge_service():
    """
    获取 ProfileMergeService 实例（G9）

    用于 G9 Profile Merge 模式，融合多个 participant 的 Profile。

    Returns:
        ProfileMergeService 或 None（如果依赖不可用）
    """
    if fusion._profile_merge_service is None:
        try:
            import traceback
            from app.application.services.bot_fuse.profile_merge_service import ProfileMergeService

            # 获取 Profile Store
            logger.info("[G9] Step 1: Getting profile store...")
            profile_store = fusion._get_api_profile_store()
            if profile_store is None:
                logger.warning("[G9] Failed to get profile store, ProfileMergeService unavailable")
                logger.warning("[G9] Check if ZDAS/SQLite database is properly configured")
                return None
            logger.info("[G9] Step 1: profile_store = %s", type(profile_store).__name__)

            # 获取 LLM Gateway
            logger.info("[G9] Step 2: Getting LLM gateway...")
            llm_gateway = fusion._get_llm_gateway_service()
            if llm_gateway is None:
                logger.warning("[G9] Failed to get LLM gateway, ProfileMergeService unavailable")
                logger.warning("[G9] Check if ENABLE_REAL_LLM=true or LLM_ENABLED=true and LLM_BASE_URL is set")
                return None
            logger.info("[G9] Step 2: llm_gateway = %s", type(llm_gateway).__name__)

            # 获取 Storage Service
            logger.info("[G9] Step 3: Getting storage service...")
            storage_service = fusion._get_fused_profile_storage_service()
            if storage_service is None:
                logger.warning("[G9] Failed to get storage service, ProfileMergeService unavailable")
                return None
            logger.info("[G9] Step 3: storage_service = %s", type(storage_service).__name__)

            fusion._profile_merge_service = ProfileMergeService(
                profile_store=profile_store,
                llm_gateway=llm_gateway,
                storage_service=storage_service,
            )
            logger.info("[G9] ProfileMergeService 创建完成")
        except Exception as e:
            logger.error("[G9] Failed to create ProfileMergeService: %s", e)
            logger.error("[G9] Traceback: %s", traceback.format_exc())
            return None
    return fusion._profile_merge_service


def _get_group_context_service():
    """
    获取 GroupContextService 实例（G9）

    用于 G9 Profile Fusion 模式，获取群组会话历史并生成摘要。

    Returns:
        GroupContextService 实例或 None（如果依赖不可用）
    """
    global _group_context_service
    if _group_context_service is None:
        try:
            import os

            # 检查是否启用群组上下文功能
            enable_group_context = os.environ.get("ENABLE_GROUP_CONTEXT", "true").lower() == "true"
            if not enable_group_context:
                logger.info("[G9] GroupContextService 已禁用 (ENABLE_GROUP_CONTEXT=false)")
                return None

            from app.application.services.bot_fuse.group_context_service import GroupContextService

            # 获取 LLM Gateway（必需）
            llm_gateway = fusion._get_llm_gateway_service()
            if llm_gateway is None:
                logger.warning("[G9] GroupContextService 创建失败: LLMGatewayService 不可用")
                return None

            # 从环境变量读取 BCN API 配置
            bcn_base_url = os.environ.get("BCN_BASE_URL", "")
            context_limit = int(os.environ.get("BCN_CONTEXT_LIMIT", "100"))

            _group_context_service = GroupContextService(
                llm_gateway=llm_gateway,
                bcn_base_url=bcn_base_url,
                context_limit=context_limit,
            )
            logger.info("[G9] GroupContextService 创建完成: bcn_url=%s, limit=%d", bcn_base_url, context_limit)
        except Exception as e:
            logger.warning(f"[G9] Failed to create GroupContextService: {e}")
            return None
    return _group_context_service


def _get_fusion_expert_chat_service():
    """
    获取 FusionExpertChatService 实例（G9）

    用于 G9 Profile Fusion 模式，负责 Prompt 构建、LLM 调用和结果构建。

    Returns:
        FusionExpertChatService 实例或 None（如果依赖不可用）
    """
    if fusion._fusion_expert_chat_service is None:
        try:
            from app.application.services.bot_fuse.fusion_expert_chat_service import FusionExpertChatService

            # 获取 LLM Gateway（必需）
            llm_gateway = fusion._get_llm_gateway_service()
            if llm_gateway is None:
                logger.warning("[G9] FusionExpertChatService 创建失败: LLMGatewayService 不可用")
                return None

            # 获取 Storage Service（可选，用于记录对话轮次）
            storage_service = fusion._get_fused_profile_storage_service()

            fusion._fusion_expert_chat_service = FusionExpertChatService(
                llm_gateway=llm_gateway,
                storage_service=storage_service,
            )
            logger.info("[G9] FusionExpertChatService 创建完成")
        except Exception as e:
            logger.warning(f"[G9] Failed to create FusionExpertChatService: {e}")
            return None
    return fusion._fusion_expert_chat_service


def get_conflict_alignment_service():
    """
    获取 ConflictAlignmentService 实例（G2）

    这个服务用于 G2 冲突对齐模式。
    会自动注入 V2 依赖（StructuredSignalExtractor 和 ConflictDimensionAnalyzer）。
    会自动注入 Layer 1 依赖（LLMConflictAnalyzer）。

    Returns:
        ConflictAlignmentService 实例
    """
    global _conflict_alignment_service
    if _conflict_alignment_service is None:
        import os
        logger.info("="*80)
        logger.info("[DEP-G2] ========== 创建 ConflictAlignmentService 单例 ==========")
        logger.info("[DEP-G2] PID: %d", os.getpid())

        # 获取 LLM recommendation service（可选）
        logger.info("[DEP-G2] Step 1: 获取 LLM recommendation service...")
        llm_rec_service = fusion._create_llm_recommendation_service()
        logger.info("[DEP-G2] llm_rec_service: %s", "已创建" if llm_rec_service else "None")

        # 获取 Layer 1: LLM冲突分析器
        logger.info("[DEP-G2] Step 2: 获取 Layer 1 LLM冲突分析器...")
        llm_analyzer = None
        try:
            from app.application.services.llm_conflict_analyzer import LLMConflictAnalyzer
            from app.infra.config.feature_flags import FeatureFlags
            if FeatureFlags.is_enabled("ENABLE_G2_LLM_CONFLICT_ANALYSIS"):
                # 获取 LLM Gateway，从中提取 provider 和 router
                gateway = fusion._get_llm_gateway_service()
                if gateway:
                    provider = getattr(gateway, '_provider', None)
                    router = getattr(gateway, '_router', None)
                    llm_analyzer = LLMConflictAnalyzer(
                        llm_provider=provider,
                        router=router,
                    )
                    logger.info("[DEP-G2] llm_analyzer: 已创建（使用 gateway 的 provider 和 router）")
                else:
                    logger.warning("[DEP-G2] LLM Gateway 不可用，Layer 1 将无法工作")
            else:
                logger.info("[DEP-G2] llm_analyzer: Feature flag 未启用")
        except Exception as e:
            logger.warning(f"[DEP-G2] Failed to create LLMConflictAnalyzer: {e}")
            import traceback
            traceback.print_exc()
            llm_analyzer = None

        # 获取 V2 依赖
        logger.info("[DEP-G2] Step 3: 获取 V2 依赖...")
        signal_extractor = fusion._get_structured_signal_extractor()
        conflict_analyzer = fusion._get_conflict_dimension_analyzer()
        logger.info("[DEP-G2] signal_extractor: %s", "已创建" if signal_extractor else "None")
        logger.info("[DEP-G2] conflict_analyzer: %s", "已创建" if conflict_analyzer else "None")

        from app.application.services.conflict_alignment_service import ConflictAlignmentService

        _conflict_alignment_service = ConflictAlignmentService(
            recommendation_service=llm_rec_service,
            llm_analyzer=llm_analyzer,  # Layer 1: LLM深度研判
            signal_extractor=signal_extractor,
            conflict_analyzer=conflict_analyzer,
        )

        if llm_analyzer:
            logger.info("[DEP-G2] ✅ ConflictAlignmentService 创建完成，带 Layer 1 LLM分析能力")
        elif signal_extractor and conflict_analyzer:
            logger.info("[DEP-G2] ✅ ConflictAlignmentService 创建完成，带 V2 分析能力")
        else:
            logger.warning("[DEP-G2] ⚠️ ConflictAlignmentService 创建完成，但 V2 分析不可用（缺少依赖）")
        logger.info("[DEP-G2] ========== ConflictAlignmentService 单例创建完成 ==========")
        logger.info("="*80)

    return _conflict_alignment_service


def _get_perspective_provider():
    """
    获取 Perspective Provider 实例

    如果 LLM 可用，返回 LLMPerspectiveProvider（真实 LLM 调用）。
    否则返回 StubPerspectiveProvider（空洞性响应）。

    Returns:
        PerspectiveProvider 实例
    """
    global _perspective_provider
    if _perspective_provider is None:
        import os
        logger.info("="*80)
        logger.info("[DEP-DIAG] ========== 创建 Perspective Provider ==========")

        # 首先检查 LLM Gateway 是否可用
        gateway = fusion._get_llm_gateway_service()

        if gateway is not None:
            # LLM 可用，创建 LLMPerspectiveProvider
            try:
                from app.infra.providers.llm_perspective_provider import LLMPerspectiveProvider

                profile_source = fusion._get_profile_source()
                if profile_source is None:
                    logger.warning("[DEP-DIAG] ⚠️ Profile source 不可用，回退到 Stub")
                    from app.infra.providers.stub_perspective_provider import StubPerspectiveProvider
                    _perspective_provider = StubPerspectiveProvider()
                else:
                    _perspective_provider = LLMPerspectiveProvider(
                        gateway=gateway,
                        profile_source=profile_source,
                    )
                    logger.info("[DEP-DIAG] ✅ LLMPerspectiveProvider 创建完成")
            except Exception as e:
                logger.warning("[DEP-DIAG] ⚠️ LLMPerspectiveProvider 创建失败: %s，回退到 Stub", str(e))
                from app.infra.providers.stub_perspective_provider import StubPerspectiveProvider
                _perspective_provider = StubPerspectiveProvider()
        else:
            # LLM 不可用，使用 Stub
            logger.warning("[DEP-DIAG] ⚠️ LLM 不可用，使用 StubPerspectiveProvider")
            from app.infra.providers.stub_perspective_provider import StubPerspectiveProvider
            _perspective_provider = StubPerspectiveProvider()

        logger.info("[DEP-DIAG] ========== Perspective Provider 创建完成 ==========")
        logger.info("="*80)

    return _perspective_provider


def _create_llm_recommendation_service():
    """
    创建 LLM Recommendation Service（如果环境配置正确）

    Returns:
        FusionRecommendationService 或 None
    """
    import os

    # 检查是否启用 LLM - 支持 canonical flag 和 legacy flag
    canonical_enabled = os.environ.get("ENABLE_REAL_LLM", "").lower() == "true"
    legacy_enabled = os.environ.get("LLM_ENABLED", "").lower() == "true"
    llm_enabled = canonical_enabled or legacy_enabled

    if not llm_enabled:
        logger.info("[Fusion] LLM not enabled (both ENABLE_REAL_LLM and LLM_ENABLED are not 'true')")
        return None

    # 检查必要的环境变量；token 值由 LLMSettings 管理，不在装配层保留局部变量。
    base_url = os.environ.get("LLM_BASE_URL")
    from app.infra.llm.config.llm_settings import LLMSettings

    if not base_url or not LLMSettings().auth_token:
        return None

    # 创建 LLM 服务链
    try:
        from app.infra.llm.routing.static_llm_router import StaticLLMRouter
        from app.application.services.llm_gateway_service import LLMGatewayService
        from app.application.services.fusion_recommendation_service import FusionRecommendationService

        settings = LLMSettings()
        provider = fusion._create_llm_provider(settings)
        router = StaticLLMRouter(settings=settings)
        gateway = LLMGatewayService(provider=provider, router=router)
        return FusionRecommendationService(gateway=gateway)
    except Exception as e:
        logger.warning(f"Failed to create LLM recommendation service: {e}")
        return None
