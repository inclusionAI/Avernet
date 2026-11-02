"""Late-bound runtime dependencies kept compatible with the legacy facade."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Optional

from app.domain.services.worker_profile_source import WorkerProfileSource

if TYPE_CHECKING:
    from app.application.services.question_rewrite_service import QuestionRewriteService

from app.adapters.http.dependencies import fusion_dependencies as fusion

logger = logging.getLogger(__name__)

_question_rewrite_service = None
_capability_verify_service = None


def reset_runtime_services() -> None:
    """Invalidate only the service instances owned by this module."""
    global _question_rewrite_service, _capability_verify_service
    _question_rewrite_service = None
    _capability_verify_service = None




class _NoOpProfileFilter:
    """
    空操作的 Profile Filter

    当过滤被禁用时使用，直接放行所有 profile。
    """

    def filter_profiles(self, profiles):
        """直接返回所有 profiles"""
        return profiles

    def get_allowed_profile_keys(self, all_profile_keys=None):
        """直接返回所有 keys"""
        if all_profile_keys is None:
            return set()
        return set(all_profile_keys)

    def is_profile_allowed(self, profile_key):
        """直接返回 True"""
        return True


def get_question_rewrite_service() -> Optional["QuestionRewriteService"]:
    """
    获取 Question Rewrite Service 实例

    当用户提供了 group_id 时，该服务会:
    1. 从 BCN API 获取群组最近上下文
    2. 调用 LLM 改写问题（补充上下文/替换代词）

    Returns:
        QuestionRewriteService 或 None（如果 LLM 不可用）
    """
    global _question_rewrite_service
    if _question_rewrite_service is None:
        gateway = fusion._get_llm_gateway_service()
        if gateway is None:
            logger.warning("[DEP-DIAG] ⚠️ LLM Gateway 不可用, QuestionRewriteService 无法创建")
            return None

        try:
            from app.application.services.question_rewrite_service import QuestionRewriteService
            _question_rewrite_service = QuestionRewriteService(llm_gateway=gateway)
            logger.info("[DEP-DIAG] ✅ QuestionRewriteService 创建完成")
        except Exception as e:
            logger.warning(f"[DEP-DIAG] ⚠️ QuestionRewriteService 创建失败: {e}")
            return None

    return _question_rewrite_service


def get_worker_profile_source() -> WorkerProfileSource:
    """
    公共 API：获取 Worker Profile Source 实例

    用于外部脚本（如 build_profile_embedding_index.py）访问 Profile Source。

    Returns:
        WorkerProfileSource: 组合 Profile Source 实例
    """
    return fusion._get_profile_source()


def get_capability_verify_service():
    """
    获取 CapabilityVerifyService 单例并自动订阅 EventBus。

    开关优先级：DRM 动态配置 > 本地 FeatureFlags 静态配置。
    仅在开关为 true 时创建。
    """
    global _capability_verify_service
    if _capability_verify_service is not None:
        return _capability_verify_service

    from app.application.utils.drm_config_helper import is_capability_verify_enabled as drm_capability_verify
    _cap_verify_on = drm_capability_verify()
    if _cap_verify_on is None:
        from app.infra.config.feature_flags import FeatureFlags
        _cap_verify_on = FeatureFlags.is_capability_verify_enabled()
    if not _cap_verify_on:
        logger.info("[DI] Capability verify disabled, skipping service creation")
        return None

    try:
        import os
        gateway = fusion._get_llm_gateway_service()
        if gateway is None:
            logger.warning("[DI] LLM Gateway not available, CapabilityVerifyService cannot be created")
            return None

        from app.application.services.verify_prompt_composer import VerifyPromptComposer
        from app.application.services.verify_executor import VerifyExecutor
        from app.application.services.verify_judge import VerifyJudge
        from app.application.services.capability_verify_service import CapabilityVerifyService
        from app.application.services.peer_review_service import PeerReviewService
        from app.domain.events import get_event_bus, WorkerProfileCreatedEvent
        from app.infra.config.capability_verify_settings import CapabilityVerifySettings

        settings = CapabilityVerifySettings()

        prompt_composer = VerifyPromptComposer(llm_gateway=gateway)
        executor = VerifyExecutor(
            bcn_chat_base_url=os.environ.get("BCN_BASE_URL", ""),
            bcn_chat_token=settings.bcn_chat_token,
            bcn_chat_cookie=settings.bcn_chat_cookie,
            timeout=settings.bcn_chat_timeout,
            probe_delay_seconds=settings.probe_delay_seconds,
            max_retries=settings.max_retries,
        )
        judge = VerifyJudge(llm_gateway=gateway)

        # Peer review service（召回相似 bot 进行面试）
        peer_review_service = PeerReviewService(
            executor=executor,
            worker_repo=fusion._get_registry_store(),
            recommendation_service=fusion.get_candidate_recommendation_service(),
            top_k=settings.peer_top_k,
            min_similarity=settings.peer_min_similarity,
        )

        _capability_verify_service = CapabilityVerifyService(
            prompt_composer=prompt_composer,
            executor=executor,
            judge=judge,
            worker_repo=fusion._get_registry_store(),
            profile_repo=fusion._get_api_profile_store(),
            peer_review_service=peer_review_service,
            total_timeout=settings.total_timeout,
            debug_output_dir=settings.debug_output_dir,
            profile_analysis_poll_interval=settings.profile_analysis_poll_interval,
            profile_analysis_max_wait=settings.profile_analysis_max_wait,
            queue_max_size=settings.queue_max_size,
            consumer_count=settings.consumer_count,
        )

        # Subscribe to event bus
        event_bus = get_event_bus()
        event_bus.subscribe(WorkerProfileCreatedEvent, _capability_verify_service.on_worker_profile_created)

        logger.info("[DI] CapabilityVerifyService created and subscribed to EventBus")
    except Exception as e:
        logger.warning("[DI] CapabilityVerifyService creation failed: %s", e)
        return None

    return _capability_verify_service
