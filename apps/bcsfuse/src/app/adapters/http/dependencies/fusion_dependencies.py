"""
Fusion Dependencies

Stage 1 Phase 4.5: Production Wiring Verification

Fusion 相关服务的依赖注入配置。

提供 G1/G2/G5 所需的服务实例，确保 Registry-aware filtering 生效。

使用方式：
```python
from app.adapters.http.dependencies.fusion_dependencies import (
    get_group_fusion_service,
    get_expert_diagnosis_service,
    get_candidate_recommendation_service,
    get_registry_aware_filter,
)
```

服务链：
GroupFusionService
    └── ExpertDiagnosisService (G5)
            └── WorkerCandidateRecommendationImpl
                    └── WorkerProfileRetrievalService
                            └── RegistryAwareWorkerFilter (optional)
                                    ├── WorkerRegistryStoreAdapter
                                    └── WorkerRuntimeStateStoreAdapter
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING, Optional

from app.application.services.literal_fallback_builder import build_literal_fallback_index
from app.application.services.registry_aware_worker_filter import RegistryAwareWorkerFilter
from app.application.services.retrieval_branch_runner import RetrievalBranchRunner
from app.application.services.worker_candidate_recommendation_impl import WorkerCandidateRecommendationImpl
from app.domain.services.worker_profile_retrieval_service import WorkerProfileRetrievalService
from app.domain.services.worker_profile_source import WorkerProfileSource
from app.infra.config.data_paths import resolve_data_path, get_default_vector_store_path

if TYPE_CHECKING:
    from app.application.services.worker_candidate_recommendation_impl import EmbeddingGenerator
    from app.application.services.worker_vector_match_service import WorkerVectorMatchService
    from app.bootstrap.application_context import ApplicationContext

logger = logging.getLogger(__name__)

# 添加详细的日志格式
import sys

# 配置详细的日志格式
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    stream=sys.stdout
)


# =============================================================================
# Global Service Instances (Singletons)
# =============================================================================

# Profile Source (Composite of API + FILE)
_profile_source: Optional[WorkerProfileSource] = None

# API Profile Content Store (for API-registered profiles)
_api_profile_store = None

# Registry-aware Filter
_registry_filter: Optional[RegistryAwareWorkerFilter] = None

# Retrieval Service with filter
_retrieval_service: Optional[WorkerProfileRetrievalService] = None

# Candidate Recommendation Service
_candidate_recommendation_service: Optional[WorkerCandidateRecommendationImpl] = None

# Embedding Generator
_embedding_generator: Optional["EmbeddingGenerator"] = None

# Vector Match Service
_vector_match_service: Optional["WorkerVectorMatchService"] = None
_retrieval_branch_runner: Optional["RetrievalBranchRunner"] = None

# Application Context (for accessing provider registry)
_app_context: Optional["ApplicationContext"] = None

# These service states are retained on the facade because existing tests and
# configuration paths monkeypatch them directly.
_group_fusion_service = None
_fused_profile_storage_service = None
_profile_merge_service = None
_fusion_expert_chat_service = None


# Configuration flags
# 从 FeatureFlags 获取默认值，与环境变量配置保持一致
from app.infra.config.feature_flags import FeatureFlags
_filter_enabled: bool = FeatureFlags.is_registry_aware_filtering_enabled()
_strict_mode: bool = False
_max_parallel_workers: int = 5  # 并行收集视角的最大线程数（默认5，最大建议8）


# =============================================================================
# Application Context Management
# =============================================================================

def set_app_context(context: "ApplicationContext") -> None:
    """
    Set the global ApplicationContext for accessing shared providers.

    This is called once during app initialization in opensource_app.py.
    It allows services without Request access to use the shared provider registry.

    Args:
        context: ApplicationContext instance from app.state.context
    """
    global _app_context
    _app_context = context
    logger.info("[FusionDependencies] Application context set globally")


def get_app_context() -> Optional["ApplicationContext"]:
    """
    Get the global ApplicationContext.

    Returns:
        ApplicationContext if set, None otherwise.
    """
    return _app_context


# =============================================================================
# Configuration Functions
# =============================================================================

def configure_filter(enabled: bool = True, strict_mode: bool = False) -> None:
    """
    配置 Registry-aware filtering

    Args:
        enabled: 是否启用过滤（默认 True）
        strict_mode: 是否启用严格模式（默认 False）
            - False（兼容模式）：未注册的 profile 放行
            - True（严格模式）：未注册的 profile 过滤掉
    """
    global _filter_enabled, _strict_mode
    _filter_enabled = enabled
    _strict_mode = strict_mode
    logger.info(f"Registry-aware filter configured: enabled={enabled}, strict_mode={strict_mode}")


def configure_parallel_workers(max_workers: int = 5) -> None:
    """
    配置并行收集视角的最大线程数

    优化效果：
    - 4 participants 时，串行收集需要 ~60s（每个 15s 超时）
    - 并行收集后只需要 ~15s（4 个同时进行）

    Args:
        max_workers: 最大并行线程数（默认 5）
            - 建议值：2-8，根据下游服务承受能力和响应时间调整
            - 值过小：无法充分利用并行优势
            - 值过大：可能对下游服务造成过大压力
    """
    global _max_parallel_workers
    _max_parallel_workers = max(min(max_workers, 8), 1)  # 限制在 1-8 范围内
    logger.info(f"Parallel workers configured: max_workers={_max_parallel_workers}")


def set_profile_source(source: WorkerProfileSource) -> None:
    """
    设置 Profile Source（用于测试或自定义配置）

    Args:
        source: WorkerProfileSource 实例
    """
    global _profile_source
    _profile_source = source


def reset_fusion_services() -> None:
    """重置所有服务实例（用于测试）"""
    global _profile_source, _api_profile_store, _registry_filter, _retrieval_service
    global _candidate_recommendation_service
    global _max_parallel_workers
    global _embedding_generator, _vector_match_service  # 🔧 BUG FIX: 添加缺失的全局变量声明
    global _retrieval_branch_runner
    global _group_fusion_service, _profile_merge_service
    global _fused_profile_storage_service, _fusion_expert_chat_service

    _profile_source = None
    _api_profile_store = None
    _registry_filter = None
    _retrieval_service = None
    _candidate_recommendation_service = None
    _vector_match_service = None
    if _retrieval_branch_runner is not None:
        _retrieval_branch_runner.close()
    _retrieval_branch_runner = None
    _max_parallel_workers = 5  # 恢复默认值
    _embedding_generator = None  # 🔧 BUG FIX: 重置embedding generator
    _group_fusion_service = None
    _profile_merge_service = None
    _fused_profile_storage_service = None
    _fusion_expert_chat_service = None
    from app.adapters.http.dependencies.fusion_dependency_services import reset_runtime_services as reset_service_runtime
    from app.adapters.http.dependencies.fusion_dependency_runtime import reset_runtime_services as reset_late_runtime
    reset_service_runtime()
    reset_late_runtime()


# =============================================================================
# Dependency Injection Functions
# =============================================================================

def _get_profile_source() -> WorkerProfileSource:
    """
    获取 Profile Source 实例

    使用 CompositeWorkerProfileSource 合并 Registry + API + FILE 来源。
    优先级：Registry active profile > API active profile > FILE profile

    Registry Source: 从已注册 Worker 构建 Profile（最高优先级）
    API Source: 从 worker_profile_contents 表加载
    FILE Source: 从文件系统加载
    """
    global _profile_source
    if _profile_source is None:
        from app.infra.worker_profiles.sources.composite_worker_profile_source import CompositeWorkerProfileSource
        from app.infra.worker_profiles.sources.file_worker_profile_source import FileWorkerProfileSource
        # ApiWorkerProfileSource 已废弃，使用 APIProfileSource
        from app.infra.worker_profiles.sources.registry_worker_profile_source import RegistryWorkerProfileSource

        # 创建 Registry 来源（最高优先级）
        registry_source = None
        try:
            registry_store = _get_registry_store()
            runtime_state_store = _get_runtime_state_store()
            if registry_store:
                registry_source = RegistryWorkerProfileSource(
                    registry_store=registry_store,
                    runtime_state_store=runtime_state_store,
                    include_offline=False,  # 只包含 online 状态的 Worker
                )
                logger.info("Registry profile source created")
        except Exception as e:
            logger.warning(f"Failed to create Registry profile source: {e}")

        # 创建 FILE 来源（已禁用，避免 cleanup-all 后从文件系统加载旧数据）
        file_source = None

        # 创建 API 来源（使用新的 APIProfileSource 解决 sparse context 问题）
        # Phase R8-Fix: Inject content_store from app context to match request-context path
        # This fixes G5 retrieval using different content_store instance than Worker/Profile API
        api_source = None
        try:
            from app.infra.worker_profiles.sources.api_profile_source import APIProfileSource

            # CRITICAL FIX: Inject content_store from app context
            injected_content_store = None
            app_context_present = _app_context is not None

            if app_context_present:
                # OSS runtime mode - must use shared content_store from registry
                worker_profile_content_store = _app_context.registry.get('worker_profile_content_store')

                if worker_profile_content_store:
                    injected_content_store = worker_profile_content_store
                    logger.info(
                        "[DEP-DIAG] API Profile Source: Injecting content_store from app context registry"
                    )
                    logger.info(
                        "[DEP-DIAG]   - provider_registry_id=%d", id(_app_context.registry)
                    )
                    logger.info(
                        "[DEP-DIAG]   - injected_profile_content_store_id=%d",
                        id(worker_profile_content_store)
                    )
                    logger.info(
                        "[DEP-DIAG]   - injected_profile_content_store_type=%s",
                        type(worker_profile_content_store).__name__
                    )
                else:
                    # OSS runtime mode but content_store not in registry - CRITICAL ERROR
                    logger.error(
                        "[DEP-DIAG] CRITICAL ERROR: App context exists but worker_profile_content_store not in registry"
                    )
                    logger.error(
                        "[DEP-DIAG] Available registry keys: %s",
                        list(_app_context.registry._providers.keys()) if hasattr(_app_context.registry, '_providers') else 'unknown'
                    )
                    raise RuntimeError(
                        "OSS Runtime Mode: worker_profile_content_store not found in registry. "
                        "Cannot create APIProfileSource without shared content_store. "
                        "Fix: Ensure worker_profile_content_store is registered in OSS provider registry."
                    )
            else:
                # Dev/test mode without app context - allow fallback but warn
                logger.warning(
                    "[DEP-DIAG] App context not set. This is acceptable for dev/test mode."
                )
                logger.warning(
                    "[DEP-DIAG] APIProfileSource will create its own content_store instance."
                )
                logger.warning(
                    "[DEP-DIAG] profile_source_mode=fallback_local_store, fallback_used=true"
                )

            # Create APIProfileSource with injected or fallback content_store
            api_source = APIProfileSource(content_store=injected_content_store)

            if injected_content_store:
                logger.info(
                    "[DEP-DIAG] API Profile Source created with injected content_store: "
                    "api_source_id=%d, api_source_content_store_id=%d, profile_source_mode=app_context_injected",
                    id(api_source),
                    id(injected_content_store)
                )
            else:
                logger.warning(
                    "[DEP-DIAG] API Profile Source created WITHOUT content_store injection (fallback mode). "
                    "This may cause instance mismatch issues in OSS runtime mode."
                )

        except RuntimeError:
            # Re-raise RuntimeError (OSS runtime mode error)
            raise
        except Exception as e:
            logger.error(f"[DEP-DIAG] Failed to create API profile source: {e}")
            raise

        # 创建组合来源
        composite = CompositeWorkerProfileSource(
            registry_source=registry_source,
            api_source=api_source,
            file_source=file_source,
        )

        _profile_source = composite
        logger.info("[DEP-DIAG] Composite profile source created (Registry + API + FILE)")
        logger.info(f"[DEP-DIAG]   - Registry source: {'✓' if registry_source else '✗'}")
        logger.info(f"[DEP-DIAG]   - API source: {'✓' if api_source else '✗'}")
        logger.info(f"[DEP-DIAG]   - FILE source: {'✓' if file_source else '✗'}")
    return _profile_source


def _get_api_profile_store():
    """Use the application's profile provider; retain SQLite for standalone use."""
    if _app_context is not None:
        profile_store = _app_context.registry.get("worker_profile_content_store")
        if profile_store is None:
            raise RuntimeError(
                "Application registry missing worker_profile_content_store; "
                "fusion must use the same profile store as profile CRUD."
            )
        return profile_store

    global _api_profile_store
    if _api_profile_store is None:
        try:
            # SQLite 模式：本地存储（open-core only）
            from app.infra.adapters.sqlite_worker_profile_content_store import SQLiteWorkerProfileContentStore
            from app.infra.config.worker_registry_settings import WorkerRegistrySettings

            settings = WorkerRegistrySettings()
            db_path = settings.get_effective_db_path()

            _api_profile_store = SQLiteWorkerProfileContentStore(db_path)
            logger.info(f"[Open-Core] API profile content store created (SQLite), path={db_path}")
        except Exception as e:
            logger.warning(f"[Open-Core] Failed to create API profile store: {e}")
            return None
    return _api_profile_store


def _get_registry_store():
    """获取 Registry Store 实例"""
    from app.adapters.http.dependencies.worker_dependencies import get_registry_store
    return get_registry_store()


def _get_runtime_state_store():
    """获取 Runtime State Store 实例"""
    from app.adapters.http.dependencies.worker_dependencies import _get_runtime_state_store
    return _get_runtime_state_store()


def _get_profile_binding_store():
    """获取 Profile Binding Store 实例"""
    from app.adapters.http.dependencies.worker_dependencies import _get_profile_binding_store
    return _get_profile_binding_store()


def get_registry_aware_filter() -> RegistryAwareWorkerFilter:
    """
    获取 Registry-aware Filter 实例

    这个过滤器会根据 Worker Registry 的状态过滤可用的候选。
    只返回 lifecycle_state == active 且 runtime_state == online 的 worker 对应的 profile。
    """
    global _registry_filter
    if _registry_filter is None:
        if _filter_enabled:
            _registry_filter = RegistryAwareWorkerFilter(
                registry_store=_get_registry_store(),
                runtime_state_store=_get_runtime_state_store(),
                strict_mode=_strict_mode,
            )
            logger.info("Registry-aware filter created and enabled")
        else:
            # 如果禁用，创建一个 no-op filter
            _registry_filter = _NoOpProfileFilter()
            logger.info("Registry-aware filter disabled (using no-op filter)")
    return _registry_filter


def get_profile_retrieval_service() -> WorkerProfileRetrievalService:
    """
    获取 Profile Retrieval Service 实例

    这个服务会自动应用 Registry-aware filtering（如果启用）。
    同时支持 profile key canonicalization（通过 binding store）。
    """
    global _retrieval_service
    if _retrieval_service is None:
        # 获取 filter（根据配置可能是真实的 filter 或 no-op）
        profile_filter = get_registry_aware_filter() if _filter_enabled else None

        # 获取 binding store（用于 profile key canonicalization）
        binding_store = _get_profile_binding_store()

        _retrieval_service = WorkerProfileRetrievalService(
            source=_get_profile_source(),
            profile_filter=profile_filter,
            binding_store=binding_store,
        )
        logger.info("Profile retrieval service created with filter=%s, binding_store=%s",
                   'enabled' if _filter_enabled else 'disabled',
                   'enabled' if binding_store else 'disabled')
    return _retrieval_service


def get_candidate_recommendation_service() -> WorkerCandidateRecommendationImpl:
    """
    获取 Candidate Recommendation Service 实例

    这个服务用于 G5 专家会诊场景的候选人推荐。
    它会使用带 Registry-aware filtering 的 retrieval service。

    Phase F: 现在会注入 vector_match_service 和 embedding_generator（如果配置完整）
    """
    global _candidate_recommendation_service
    if _candidate_recommendation_service is None:
        # 尝试创建 embedding generator 和 vector match service
        embedding_generator = _get_embedding_generator()
        vector_match_service = _get_vector_match_service() if embedding_generator else None

        _candidate_recommendation_service = WorkerCandidateRecommendationImpl(
            retrieval_service=get_profile_retrieval_service(),
            min_experts=3,
            default_max_candidates=5,
            vector_match_service=vector_match_service,
            embedding_generator=embedding_generator,
        )

        if vector_match_service:
            logger.info("Candidate recommendation service created with vector match enabled")
        else:
            logger.info("Candidate recommendation service created (keyword-only mode)")
    return _candidate_recommendation_service


def _get_profile_embedding_store():
    """
    获取 Profile Embedding Store 实例

    用于删除 profile 向量数据。

    Returns:
        ProfileEmbeddingStore 或 None（如果初始化失败）
    """
    try:
        from app.adapters.http.dependencies.worker_dependencies import _get_profile_embedding_store as get_store
        return get_store()
    except Exception as e:
        logger.debug(f"[FusionDependencies] Failed to get profile embedding store: {e}")
        return None


def _build_vector_index_for_worker(worker_id: str) -> bool:
    """
    为指定 Worker 构建向量索引

    用于 Worker online 时重新构建向量索引。

    Args:
        worker_id: Worker ID

    Returns:
        是否构建成功
    """
    try:
        logger.info(f"[INDEX-BUILD] 开始为 worker 构建向量索引: {worker_id}")

        embedding_gen = _get_embedding_generator()
        profile_src = _get_profile_source()

        if not embedding_gen:
            logger.warning("[INDEX-BUILD] Embedding generator 不可用，跳过索引构建")
            return False

        if not profile_src:
            logger.warning("[INDEX-BUILD] Profile source 不可用，跳过索引构建")
            return False

        # 加载 worker，以 active_profile_key 作为期望的 profile_key
        expected_profile_key = None
        try:
            from app.adapters.http.dependencies.worker_dependencies import _get_registry_store
            registry_store = _get_registry_store()
            if registry_store:
                worker = registry_store.get_by_id(worker_id)
                if worker and worker.active_profile_key:
                    expected_profile_key = worker.active_profile_key
        except Exception as e_worker:
            logger.warning(f"[INDEX-BUILD] 加载 worker 失败，跳过 active_profile_key 感知: {e_worker}")

        # 扫描指定 worker 的 profile
        # 使用 staff_id 精确匹配，而非 profile_key.startswith(worker_id)
        # 原因：worker_id 可能包含 ":"（如 "bot_id:owner_id"），
        # startswith 会误匹配前缀相同的其他 worker（如 "default:334018" 匹配 "default:3340183"）
        from app.domain.services.worker_profile_source import WorkerScopedProfileSource

        scan_result = (
            profile_src.scan_for_workers([worker_id])
            if isinstance(profile_src, WorkerScopedProfileSource)
            else profile_src.scan()
        )

        def _profile_belongs_to_worker(profile):
            return getattr(profile, 'staff_id', None) == worker_id or getattr(profile, 'worker_id', None) == worker_id

        worker_profiles = [p for p in scan_result.profiles if _profile_belongs_to_worker(p)]

        # 优先选择匹配 active_profile_key 的 profile，否则取第一个
        target_profile = None
        if expected_profile_key:
            for profile in worker_profiles:
                if profile.profile_key == expected_profile_key:
                    target_profile = profile
                    logger.info(f"[INDEX-BUILD] 命中 active_profile_key 对应 profile: {profile.profile_key}")
                    break

        if not target_profile and worker_profiles:
            target_profile = worker_profiles[0]
            logger.info(f"[INDEX-BUILD] fallback 到第一个 profile: {target_profile.profile_key}")

        if not target_profile:
            logger.warning(f"[INDEX-BUILD] 未找到 worker 的 profile: {worker_id}")
            return False

        logger.info(f"[INDEX-BUILD] 选中 profile: {target_profile.profile_key}")

        # 使用单例 ProfileEmbedding Store - 避免重复创建 Qdrant 客户端导致锁冲突
        profile_store = _get_profile_embedding_store()

        # 清理同 worker 其他 profile_key 的残留向量
        # 策略：从向量存储里读出该 worker 的所有向量 ID，删除不属于 target_profile_key 的
        if profile_store and hasattr(profile_store, '_vector_store') and hasattr(profile_store._vector_store, 'get_vector_ids') and hasattr(profile_store._vector_store, 'delete'):
            try:
                all_ids = profile_store._vector_store.get_vector_ids()
                prefix = f"{worker_id}:"
                # vector_id format: {profile_key}:{fragment_type}[:index]
                # profile_key = {staff_id}:{profile_id} (may contain ':')
                def _extract_profile_key(vid):
                    if ':' not in vid:
                        return vid
                    return vid.rsplit(':', 1)[0]

                stale_ids = [
                    vid for vid in all_ids
                    if vid.startswith(prefix) and _extract_profile_key(vid) != target_profile.profile_key
                ]
                if stale_ids:
                    profile_store._vector_store.delete(stale_ids)
                    logger.info(f"[INDEX-BUILD] 清理 {len(stale_ids)} 条同 worker 其他 profile 残留向量: worker={worker_id}, target={target_profile.profile_key}")
            except Exception as e_clear:
                logger.warning(f"[INDEX-BUILD] 清理残留向量失败: {e_clear}")

        if not profile_store:
            logger.warning("[INDEX-BUILD] Profile embedding store 不可用，跳过索引构建")
            return False

        # 创建索引器
        from app.domain.services.profile_embedding_indexer import ProfileEmbeddingIndexer
        indexer = ProfileEmbeddingIndexer(
            embedding_provider=embedding_gen,
            profile_store=profile_store,
        )

        # 获取 worker 状态（availability/runtime_state）
        worker_states = {}
        try:
            from app.adapters.http.dependencies.worker_dependencies import _get_registry_store, _get_runtime_state_store
            registry_store = _get_registry_store()
            runtime_state_store = _get_runtime_state_store()

            if registry_store and runtime_state_store:
                worker = registry_store.get_by_id(worker_id)
                if worker:
                    # Phase 2.6.3: Fix availability access - use enum value directly
                    availability = worker.state.availability.value  # "private", "protected", or "public"

                    # Phase 2.6.4: Fix runtime_state default - check lifecycle_state first
                    runtime_state_row = runtime_state_store.get_runtime_state(worker_id)
                    if runtime_state_row:
                        # Use runtime_state from store if available
                        # Note: MySQL store returns dict, SQLite store returns WorkerRuntimeState enum
                        if isinstance(runtime_state_row, dict):
                            runtime_state_str = runtime_state_row.get("state", "offline")
                        elif hasattr(runtime_state_row, "value"):
                            # WorkerRuntimeState enum
                            runtime_state_str = runtime_state_row.value
                        else:
                            runtime_state_str = str(runtime_state_row)
                    else:
                        # No runtime_state in store - infer from lifecycle_state
                        # If lifecycle_state is ACTIVE, assume online; otherwise offline
                        from app.domain.models.worker_lifecycle_state import WorkerLifecycleState
                        lifecycle_state = getattr(worker, 'lifecycle_state', None)
                        if lifecycle_state == WorkerLifecycleState.ACTIVE:
                            runtime_state_str = "online"
                        else:
                            runtime_state_str = "offline"
                        logger.info(
                            f"[INDEX-BUILD] No runtime_state in store for {worker_id}, "
                            f"inferred from lifecycle_state={lifecycle_state}: runtime_state={runtime_state_str}"
                        )

                    worker_states[worker_id] = {
                        "availability": availability,
                        "runtime_state": runtime_state_str,
                    }
                    # Also add handle as key (profile.staff_id might be handle)
                    # Phase 2.6.4: Add hasattr check to prevent AttributeError
                    if hasattr(worker, 'handle') and worker.handle and worker.handle.startswith("@"):
                        worker_states[worker.handle[1:]] = {
                            "availability": availability,
                            "runtime_state": runtime_state_str,
                        }
                    logger.info(f"[INDEX-BUILD] Worker states for {worker_id}: availability={availability}, runtime_state={runtime_state_str}")
        except Exception as e:
            logger.warning(f"[INDEX-BUILD] Failed to load worker states: {e}. Proceeding without availability/runtime_state filters.")

        # Phase 2.6.4: Diagnostic log for worker_states keys
        logger.info(
            "[WORKER-STATE-TRACE] stage=before_update_index_smart, worker_states_keys=%s, target_profile_staff_id=%s, target_profile_key=%s",
            sorted((worker_states or {}).keys())[:20],
            getattr(target_profile, "staff_id", None),
            getattr(target_profile, "profile_key", None)
        )

        # 智能增量索引：复用已有向量，只重新计算变更的 fragments
        result = indexer.update_index_smart([target_profile], worker_states=worker_states)

        if result.indexed_count > 0:
            logger.info(
                f"✅ [INDEX-BUILD] 向量索引构建成功: worker={worker_id}, "
                f"indexed={result.indexed_count}"
            )
            return True
        else:
            logger.warning(f"⚠️ [INDEX-BUILD] 向量索引构建失败: {worker_id}")
            return False

    except Exception as e:
        logger.error(f"❌ [INDEX-BUILD] 向量索引构建异常: {e}", exc_info=True)
        return False


def _get_embedding_generator():
    """
    获取 Embedding Generator 实例

    如果 embedding 配置完整，创建 RealEmbeddingProvider 作为 generator。
    否则返回 None，使用 keyword-only 模式。

    Returns:
        RealEmbeddingProvider 或 None
    """
    global _embedding_generator
    context = get_app_context()
    if context is not None:
        _embedding_generator = context.registry.get("embedding_provider")
        return _embedding_generator
    if _embedding_generator is None:
        try:
            from app.infra.embedding.config.embedding_settings import EmbeddingSettings
            from app.infra.embedding.providers.real_provider import RealEmbeddingProvider

            settings = EmbeddingSettings()
            if settings.is_configured():
                _embedding_generator = RealEmbeddingProvider(settings=settings)
                logger.info(f"Embedding generator created: model={settings.model}, dimension={settings.dimension}")
            else:
                missing = settings.missing_config()
                logger.warning(f"Embedding not configured, missing: {missing}. Using keyword-only mode.")
                return None
        except Exception as e:
            logger.warning(f"Failed to create embedding generator: {e}. Using keyword-only mode.")
            return None
    return _embedding_generator


def _get_vector_match_service():
    """
    获取 Vector Match Service 实例 - Open-Core (Local Only)

    需要 embedding generator 和向量索引数据。
    如果向量存储为空，返回 None。

    Open-core版本统一使用 QdrantLocalVectorStore (Qdrant + 本地文件持久化)

    IMPORTANT: This function now uses the shared vector_store from the application
    context registry to avoid Qdrant embedded client lock errors. It NO LONGER creates
    its own QdrantLocalVectorStore instance.

    Returns:
        WorkerVectorMatchService 或 None
    """
    global _vector_match_service, _retrieval_branch_runner
    if _vector_match_service is None:
        try:
            from app.application.services.worker_vector_match_service import WorkerVectorMatchService
            from app.infra.metadatastores.file_metadata_store_adapter import FileMetadataStoreAdapter
            from app.infra.embedding.config.embedding_settings import EmbeddingSettings

            settings = EmbeddingSettings()
            dimension = settings.dimension

            # CRITICAL FIX: Use shared vector_store from provider registry to avoid
            # Qdrant embedded client concurrent access lock error
            # See: Phase F of OPENCORE-P1-LOCAL-MACOS-DEEP-BUSINESS-E2E-WITH-REAL-SERVICES
            vector_store = None

            # Try to get from global app context first (preferred)
            if _app_context is not None:
                vector_store = _app_context.registry.get('vector_store')
                if vector_store:
                    logger.info(
                        "[LOCAL_QDRANT_SINGLETON] component=VectorMatchService "
                        f"vector_store_id={id(vector_store)} "
                        f"storage_path={getattr(vector_store, 'path', 'N/A')} "
                        f"collection={getattr(vector_store, 'collection_name', 'N/A')} "
                        f"source=registry"
                    )
                else:
                    logger.warning("[VectorMatch] ⚠️ App context exists but vector_store not in registry")

            # Fallback: create standalone instance (NOT recommended for embedded Qdrant)
            if vector_store is None:
                logger.error("=" * 80)
                logger.error("❌ [VectorMatch] CRITICAL: No shared vector_store found in app context!")
                logger.error("❌ [VectorMatch] Creating standalone QdrantLocalVectorStore instance.")
                logger.error("❌ [VectorMatch] This WILL cause Qdrant embedded client lock errors if:")
                logger.error("❌ [VectorMatch]   - Search endpoint uses registry vector_store")
                logger.error("❌ [VectorMatch]   - Activation indexing uses this standalone instance")
                logger.error("❌ [VectorMatch] RECOMMENDATION: Call set_app_context() during app startup")
                logger.error("=" * 80)

                from app.infra.public.vectorstores.qdrant_local_vector_store import QdrantLocalVectorStore
                # Priority: QDRANT_LOCAL_PATH env var > resolve_data_path fallback
                storage_path = os.getenv("QDRANT_LOCAL_PATH") or resolve_data_path("data/qdrant_storage")
                vector_store = QdrantLocalVectorStore(
                    collection_name="bcsfuse_profiles",
                    path=storage_path,
                    dimension=dimension,
                )
                logger.warning(
                    "[LOCAL_QDRANT_SINGLETON] component=VectorMatchService "
                    f"vector_store_id={id(vector_store)} "
                    f"storage_path={storage_path} "
                    f"source=created WARNING=STANDALONE_FALLBACK dimension={dimension}"
                )

            metadata_store = FileMetadataStoreAdapter()

            # 创建服务
            # 根据 feature flag 决定是否注入 profile_filter
            # 默认关闭，直接使用向量搜索的 payload 过滤（availability/runtime_state）
            from app.infra.config.feature_flags import FeatureFlags
            if FeatureFlags.is_registry_aware_filtering_enabled():
                from app.application.services.registry_aware_worker_filter import RegistryAwareWorkerFilter
                profile_filter = RegistryAwareWorkerFilter(
                    registry_store=_get_registry_store(),
                    runtime_state_store=_get_runtime_state_store(),
                    strict_mode=False,
                )
                logger.info("[VectorMatch] Registry-aware filter enabled")
            else:
                profile_filter = None
                logger.info("[VectorMatch] Registry-aware filter disabled, using vector store payload filtering only")

            fallback_index = build_literal_fallback_index(vector_store)
            from app.application.services.retrieval_branch_runner import RetrievalBranchRunner
            if _retrieval_branch_runner is None:
                _retrieval_branch_runner = RetrievalBranchRunner(max_workers=64)
            _vector_match_service = WorkerVectorMatchService(
                vector_store=vector_store,
                metadata_store=metadata_store,
                profile_filter=profile_filter,
                fallback_index=fallback_index,
                retrieval_runner=_retrieval_branch_runner,
            )

            # Phase B: Inject profile content store for fragment content reload
            try:
                if _app_context is not None:
                    profile_content_store = _app_context.registry.get('worker_profile_content_store')
                    if profile_content_store:
                        _vector_match_service.set_profile_content_store(profile_content_store)
                        logger.info("[VectorMatch] ✅ Profile content store injected for fragment content reload")
                    else:
                        logger.warning("[VectorMatch] ⚠️ Profile content store not found in app context registry")
                else:
                    logger.warning("[VectorMatch] ⚠️ App context not set, cannot inject profile content store")
            except Exception as e:
                logger.warning("[VectorMatch] ⚠️ Failed to inject profile content store: %s", e)

            logger.info(f"[Open-Core] Vector match service created (dimension={dimension}, mode=local)")

        except Exception as e:
            logger.error(f"❌ [VectorMatch] Failed to create vector match service: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return None
    return _vector_match_service


# GroupContextService 单例（G9 用于获取群组对话上下文）
_group_context_service = None


# FusionExpertChatService 单例（G9 用于构建对话 Prompt 和调用 LLM）
_fusion_expert_chat_service = None


# =============================================================================
# Helper Functions
# =============================================================================

# Perspective Provider Instance
_perspective_provider = None


# =============================================================================
# No-Op Filter (for when filtering is disabled)
# =============================================================================


__all__ = [
    # Configuration
    "configure_filter",
    "configure_parallel_workers",  # 并行收集视角配置
    "set_profile_source",
    "reset_fusion_services",
    # Dependency Injection
    "get_registry_aware_filter",
    "get_profile_retrieval_service",
    "get_candidate_recommendation_service",
    "get_expert_diagnosis_service",
    "get_conflict_alignment_service",  # G2 V2
    "get_group_fusion_service",
    "get_question_rewrite_service",
    # OSS-Safe Request-Context Injection (P1 Fix)
    "get_availability_checker_from_request",
    # Profile Source (for scripts)
    "get_worker_profile_source",
]


# =====================================
# Capability Verify — DI Registration
# =====================================


# =============================================================================
# OSS-Safe Request-Context Dependency Injection
# =============================================================================


# Split modules preserve the legacy import path. They are imported after the
# facade-local functions so test monkeypatch targets remain on this module.
from app.adapters.http.dependencies.fusion_dependency_services import (  # noqa: E402
    _create_g5_expert_enhancer,
    _create_llm_provider,
    _create_llm_recommendation_service,
    _get_availability_checker,
    _get_conflict_dimension_analyzer,
    _get_context_preparation_service,
    _get_fused_profile_storage_service,
    _get_fusion_expert_chat_service,
    _get_group_context_service,
    _get_llm_gateway_service,
    _get_perspective_provider,
    _get_profile_merge_service,
    _get_structured_signal_extractor,
    get_conflict_alignment_service,
    get_expert_diagnosis_service,
    get_group_fusion_service,
    set_fused_profile_storage_service,
)
from app.adapters.http.dependencies.fusion_dependency_runtime import (  # noqa: E402
    _NoOpProfileFilter,
    get_capability_verify_service,
    get_question_rewrite_service,
    get_worker_profile_source,
)
from app.adapters.http.dependencies.fusion_request_dependencies import (  # noqa: E402
    get_availability_checker_from_request,
    get_profile_source_from_request,
)
