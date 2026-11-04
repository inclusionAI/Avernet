"""Fuse composition over the current TC catalog, shared search and model providers.

All four legacy modes keep their business services. Candidate selection and G5
retrieval share TC search; no filesystem source or second vector index is built.
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING

from app.application.services.expert_diagnosis_service import ExpertDiagnosisService
from app.application.services.group_fusion_service import GroupFusionService
from app.domain.services.worker_profile_source import WorkerProfileSource
from app.infra.config.feature_flags import FeatureFlags

if TYPE_CHECKING:
    from app.bootstrap.application_context import ApplicationContext

logger = logging.getLogger(__name__)

_app_context: ApplicationContext | None = None
_profile_source: WorkerProfileSource | None = None
_registry_filter = None
_retrieval_service = None
_expert_diagnosis_service = None
_g5_expert_enhancer = None
_context_preparation_service = None
_llm_gateway_service = None
_group_fusion_service = None
_structured_signal_extractor = None
_conflict_dimension_analyzer = None
_fused_profile_storage_service = None
_profile_merge_service = None
_conflict_alignment_service = None
_group_context_service = None
_fusion_expert_chat_service = None
_perspective_provider = None
_question_rewrite_service = None
_capability_verify_service = None
_filter_enabled = FeatureFlags.is_registry_aware_filtering_enabled()
_strict_mode = False
_max_parallel_workers = 5


def set_app_context(context: ApplicationContext) -> None:
    global _app_context
    _app_context = context


def get_app_context() -> ApplicationContext | None:
    return _app_context


def _require_provider(name):
    if _app_context is None:
        raise RuntimeError(f"Fusion requires a composed application provider: {name}")
    return _app_context.registry.require(name)


def set_fused_profile_storage_service(storage_service) -> None:
    global _fused_profile_storage_service, _profile_merge_service
    global _fusion_expert_chat_service, _group_fusion_service
    _fused_profile_storage_service = storage_service
    _profile_merge_service = _fusion_expert_chat_service = _group_fusion_service = None


def configure_filter(enabled=True, strict_mode=False) -> None:
    global _filter_enabled, _strict_mode, _registry_filter
    _filter_enabled, _strict_mode = enabled, strict_mode
    _registry_filter = None


def configure_parallel_workers(max_workers=5) -> None:
    global _max_parallel_workers
    _max_parallel_workers = max(min(max_workers, 8), 1)


def set_profile_source(source: WorkerProfileSource) -> None:
    global _profile_source, _retrieval_service
    _profile_source, _retrieval_service = source, None


def reset_fusion_services() -> None:
    global _profile_source, _registry_filter, _retrieval_service
    global _expert_diagnosis_service, _group_fusion_service, _g5_expert_enhancer
    global _context_preparation_service, _llm_gateway_service, _structured_signal_extractor
    global _conflict_dimension_analyzer, _conflict_alignment_service, _perspective_provider
    global _question_rewrite_service, _profile_merge_service, _capability_verify_service
    global _group_context_service, _fusion_expert_chat_service, _fused_profile_storage_service
    global _max_parallel_workers
    if _group_fusion_service is not None:
        _group_fusion_service.shutdown()
    _profile_source = _registry_filter = _retrieval_service = None
    _expert_diagnosis_service = _group_fusion_service = _g5_expert_enhancer = None
    _context_preparation_service = _llm_gateway_service = _structured_signal_extractor = None
    _conflict_dimension_analyzer = _conflict_alignment_service = _perspective_provider = None
    _question_rewrite_service = _profile_merge_service = _capability_verify_service = None
    _group_context_service = _fusion_expert_chat_service = _fused_profile_storage_service = None
    _max_parallel_workers = 5


def _get_profile_source() -> WorkerProfileSource:
    return _profile_source if _profile_source is not None else _require_provider("worker_profile_source")


def get_profile_source_from_request(request):
    return request.app.state.context.registry.require("worker_profile_source")


def _get_api_profile_store():
    return _require_provider("worker_profile_content_store")


def _get_registry_store():
    return _require_provider("worker_registry_store")


def _get_runtime_state_store():
    return _require_provider("worker_runtime_state_store")


def get_registry_aware_filter():
    global _registry_filter
    if _registry_filter is None:
        from app.tc.fuse import TcProfileFilter
        _registry_filter = TcProfileFilter(_get_availability_checker(), _filter_enabled)
    return _registry_filter


def get_profile_retrieval_service():
    global _retrieval_service
    if _retrieval_service is None:
        from app.tc.fuse import TcProfileRetrieval
        _retrieval_service = TcProfileRetrieval(get_candidate_recommendation_service(), _get_profile_source())
    return _retrieval_service


def get_candidate_recommendation_service():
    return _require_provider("tc_search")


def get_expert_diagnosis_service() -> ExpertDiagnosisService:
    global _expert_diagnosis_service
    if _expert_diagnosis_service is None:
        _expert_diagnosis_service = ExpertDiagnosisService(
            recommendation_service=_create_llm_recommendation_service(),
            g5_enhancer=_create_g5_expert_enhancer(),
            candidate_recommendation_service=get_candidate_recommendation_service(),
        )
    return _expert_diagnosis_service


def _get_context_preparation_service():
    global _context_preparation_service
    if _context_preparation_service is None:
        from app.domain.services.worker_context_preparation_service import (
            WorkerContextPreparationService,
        )
        _context_preparation_service = WorkerContextPreparationService()
    return _context_preparation_service


def _create_llm_provider(settings):
    api_type = os.environ.get("LLM_API_TYPE", "anthropic").lower()
    if api_type in ("openai", "openai-completions"):
        from app.infra.llm.providers.openai_compatible_provider import (
            OpenAICompatibleProvider,
        )
        return OpenAICompatibleProvider(settings=settings)
    from app.infra.llm.providers.anthropic_compatible_provider import (
        AnthropicCompatibleProvider,
    )
    return AnthropicCompatibleProvider(settings=settings)


def _get_llm_gateway_service():
    global _llm_gateway_service
    if _llm_gateway_service is None:
        enabled = any(os.environ.get(key, "").lower() == "true" for key in ("ENABLE_REAL_LLM", "LLM_ENABLED"))
        if not enabled or not os.environ.get("LLM_BASE_URL") or not os.environ.get("LLM_AUTH_TOKEN"):
            return None
        try:
            from app.application.services.llm_gateway_service import LLMGatewayService
            from app.infra.llm.config.llm_settings import LLMSettings
            from app.infra.llm.routing.static_llm_router import StaticLLMRouter
            settings = LLMSettings()
            _llm_gateway_service = LLMGatewayService(
                provider=_create_llm_provider(settings), router=StaticLLMRouter(settings=settings),
            )
        except Exception:
            logger.exception("Failed to create LLM gateway")
            return None
    return _llm_gateway_service


def _create_g5_expert_enhancer():
    global _g5_expert_enhancer
    if _g5_expert_enhancer is None:
        gateway = _get_llm_gateway_service()
        if gateway is None:
            return None
        from app.application.services.g5_expert_enhancer_impl import (
            G5ExpertEnhancerImpl,
        )
        _g5_expert_enhancer = G5ExpertEnhancerImpl(
            gateway=gateway, retrieval_service=get_profile_retrieval_service(),
            preparation_service=_get_context_preparation_service(), profile_source=_get_profile_source(),
            max_experts=3,
        )
    return _g5_expert_enhancer


def get_group_fusion_service() -> GroupFusionService:
    global _group_fusion_service
    if _group_fusion_service is None:
        _group_fusion_service = GroupFusionService(
            provider=_get_perspective_provider(), recommendation_service=_create_llm_recommendation_service(),
            conflict_alignment_service=get_conflict_alignment_service(),
            expert_diagnosis_service=get_expert_diagnosis_service(), profile_merge_service=_get_profile_merge_service(),
            fusion_expert_chat_service=_get_fusion_expert_chat_service(), group_context_service=_get_group_context_service(),
            availability_checker=_get_availability_checker(), worker_store=_get_registry_store(),
            max_parallel_workers=_max_parallel_workers,
        )
    return _group_fusion_service


def _get_availability_checker():
    from app.tc.fuse import TcAvailabilityChecker
    return TcAvailabilityChecker(_require_provider("tc_catalog"), _get_registry_store())


def _get_structured_signal_extractor():
    global _structured_signal_extractor
    if _structured_signal_extractor is None:
        from app.application.services.structured_signal_extractor import (
            StructuredSignalExtractor,
        )
        _structured_signal_extractor = StructuredSignalExtractor()
    return _structured_signal_extractor


def _get_conflict_dimension_analyzer():
    global _conflict_dimension_analyzer
    if _conflict_dimension_analyzer is None:
        from app.domain.services.conflict_dimension_analyzer import (
            ConflictDimensionAnalyzer,
        )
        _conflict_dimension_analyzer = ConflictDimensionAnalyzer()
    return _conflict_dimension_analyzer


def _get_fused_profile_storage_service():
    global _fused_profile_storage_service
    if _fused_profile_storage_service is None:
        from app.application.services.bot_fuse.fused_profile_storage_service import (
            FusedProfileStorageService,
        )
        _fused_profile_storage_service = FusedProfileStorageService(repository=_require_provider("fused_profile_store"))
    return _fused_profile_storage_service


def _get_profile_merge_service():
    global _profile_merge_service
    if _profile_merge_service is None:
        gateway = _get_llm_gateway_service()
        if gateway is None:
            return None
        from app.application.services.bot_fuse.profile_merge_service import (
            ProfileMergeService,
        )
        _profile_merge_service = ProfileMergeService(
            profile_store=_get_api_profile_store(), llm_gateway=gateway,
            storage_service=_get_fused_profile_storage_service(),
        )
    return _profile_merge_service


def _get_group_context_service():
    global _group_context_service
    if _group_context_service is None:
        if os.environ.get("ENABLE_GROUP_CONTEXT", "true").lower() != "true":
            return None
        gateway = _get_llm_gateway_service()
        if gateway is None:
            return None
        try:
            from app.application.services.bot_fuse.group_context_service import (
                GroupContextService,
            )
            _group_context_service = GroupContextService(
                llm_gateway=gateway, bcn_base_url=os.environ.get("BCN_BASE_URL", ""),
                context_limit=int(os.environ.get("BCN_CONTEXT_LIMIT", "100")),
            )
        except Exception:
            logger.exception("Failed to create group context service")
            return None
    return _group_context_service


def _get_fusion_expert_chat_service():
    global _fusion_expert_chat_service
    if _fusion_expert_chat_service is None:
        gateway = _get_llm_gateway_service()
        if gateway is None:
            return None
        from app.application.services.bot_fuse.fusion_expert_chat_service import (
            FusionExpertChatService,
        )
        _fusion_expert_chat_service = FusionExpertChatService(
            llm_gateway=gateway, storage_service=_get_fused_profile_storage_service(),
        )
    return _fusion_expert_chat_service


def get_conflict_alignment_service():
    global _conflict_alignment_service
    if _conflict_alignment_service is None:
        llm_analyzer = None
        if FeatureFlags.is_enabled("ENABLE_G2_LLM_CONFLICT_ANALYSIS"):
            gateway = _get_llm_gateway_service()
            if gateway is not None:
                from app.application.services.llm_conflict_analyzer import (
                    LLMConflictAnalyzer,
                )
                llm_analyzer = LLMConflictAnalyzer(
                    llm_provider=getattr(gateway, "_provider", None), router=getattr(gateway, "_router", None),
                )
        from app.application.services.conflict_alignment_service import (
            ConflictAlignmentService,
        )
        _conflict_alignment_service = ConflictAlignmentService(
            recommendation_service=_create_llm_recommendation_service(), llm_analyzer=llm_analyzer,
            signal_extractor=_get_structured_signal_extractor(), conflict_analyzer=_get_conflict_dimension_analyzer(),
        )
    return _conflict_alignment_service


def _get_perspective_provider():
    global _perspective_provider
    if _perspective_provider is None:
        gateway = _get_llm_gateway_service()
        if gateway is None:
            from app.infra.providers.stub_perspective_provider import (
                StubPerspectiveProvider,
            )
            _perspective_provider = StubPerspectiveProvider()
        else:
            from app.infra.providers.llm_perspective_provider import (
                LLMPerspectiveProvider,
            )
            _perspective_provider = LLMPerspectiveProvider(gateway=gateway, profile_source=_get_profile_source())
    return _perspective_provider


def _create_llm_recommendation_service():
    gateway = _get_llm_gateway_service()
    if gateway is None:
        return None
    from app.application.services.fusion_recommendation_service import (
        FusionRecommendationService,
    )
    return FusionRecommendationService(gateway=gateway)


def get_question_rewrite_service():
    global _question_rewrite_service
    if _question_rewrite_service is None:
        gateway = _get_llm_gateway_service()
        if gateway is None:
            return None
        from app.application.services.question_rewrite_service import (
            QuestionRewriteService,
        )
        _question_rewrite_service = QuestionRewriteService(llm_gateway=gateway)
    return _question_rewrite_service


def get_worker_profile_source() -> WorkerProfileSource:
    return _get_profile_source()


def get_capability_verify_service():
    global _capability_verify_service
    if _capability_verify_service is not None:
        return _capability_verify_service
    from app.application.utils.drm_config_helper import is_capability_verify_enabled
    enabled = is_capability_verify_enabled()
    if enabled is None:
        enabled = FeatureFlags.is_capability_verify_enabled()
    if not enabled:
        return None
    gateway = _get_llm_gateway_service()
    if gateway is None:
        return None
    try:
        from app.application.services.capability_verify_service import (
            CapabilityVerifyService,
        )
        from app.application.services.peer_review_service import PeerReviewService
        from app.application.services.verify_executor import VerifyExecutor
        from app.application.services.verify_judge import VerifyJudge
        from app.application.services.verify_prompt_composer import VerifyPromptComposer
        from app.domain.events import WorkerProfileCreatedEvent, get_event_bus
        from app.infra.config.capability_verify_settings import CapabilityVerifySettings
        settings = CapabilityVerifySettings()
        executor = VerifyExecutor(
            bcn_chat_base_url=os.environ.get("BCN_BASE_URL", ""), bcn_chat_token=settings.bcn_chat_token,
            bcn_chat_cookie=settings.bcn_chat_cookie, timeout=settings.bcn_chat_timeout,
            probe_delay_seconds=settings.probe_delay_seconds, max_retries=settings.max_retries,
        )
        peers = PeerReviewService(
            executor=executor, worker_repo=_get_registry_store(),
            recommendation_service=get_candidate_recommendation_service(),
            top_k=settings.peer_top_k, min_similarity=settings.peer_min_similarity,
        )
        _capability_verify_service = CapabilityVerifyService(
            prompt_composer=VerifyPromptComposer(llm_gateway=gateway), executor=executor,
            judge=VerifyJudge(llm_gateway=gateway), worker_repo=_get_registry_store(),
            profile_repo=_get_api_profile_store(), peer_review_service=peers, total_timeout=settings.total_timeout,
            debug_output_dir=settings.debug_output_dir, profile_analysis_poll_interval=settings.profile_analysis_poll_interval,
            profile_analysis_max_wait=settings.profile_analysis_max_wait, queue_max_size=settings.queue_max_size,
            consumer_count=settings.consumer_count,
        )
        get_event_bus().subscribe(WorkerProfileCreatedEvent, _capability_verify_service.on_worker_profile_created)
    except Exception:
        logger.exception("Failed to create capability verification service")
        return None
    return _capability_verify_service


def get_availability_checker_from_request(request):
    from app.tc.fuse import TcAvailabilityChecker
    registry = request.app.state.context.registry
    return TcAvailabilityChecker(registry.require("tc_catalog"), registry.require("worker_registry_store"))


__all__ = [
    "configure_filter", "configure_parallel_workers", "get_availability_checker_from_request",
    "get_candidate_recommendation_service", "get_conflict_alignment_service", "get_expert_diagnosis_service",
    "get_group_fusion_service", "get_profile_retrieval_service", "get_question_rewrite_service",
    "get_registry_aware_filter", "get_worker_profile_source", "reset_fusion_services", "set_profile_source",
]
