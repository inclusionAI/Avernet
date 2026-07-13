"""
Fusion Dependencies

Stage 1 Phase 4.5: Production Wiring Verification

Fusion 相关服务的依赖注入配置。

提供 G1/G2/G5 所需的服务实例，确保 Registry-aware filtering 生效。

使用方式：
```python
from src.interfaces.api.dependencies.fusion_dependencies import (
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
from datetime import datetime
from typing import Optional

from src.application.services.registry_aware_worker_filter import RegistryAwareWorkerFilter
from src.application.services.worker_candidate_recommendation_impl import WorkerCandidateRecommendationImpl
from src.application.services.expert_diagnosis_service import ExpertDiagnosisService
from src.application.services.group_fusion_service import GroupFusionService
from src.domain.services.worker_profile_retrieval_service import WorkerProfileRetrievalService
from src.domain.services.worker_profile_source import WorkerProfileSource
from src.infra.config.data_paths import resolve_data_path, get_default_vector_store_path

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

# Application Context (for accessing provider registry)
_app_context: Optional["ApplicationContext"] = None

# Expert Diagnosis Service
_expert_diagnosis_service: Optional[ExpertDiagnosisService] = None

# G5 Expert Enhancer
_g5_expert_enhancer = None

# Context Preparation Service
_context_preparation_service = None

# LLM Gateway Service
_llm_gateway_service = None

# Group Fusion Service
_group_fusion_service: Optional[GroupFusionService] = None

# G2 V2 Services
_structured_signal_extractor = None
_conflict_dimension_analyzer = None

# G9 Profile Fusion Service
_fused_profile_storage_service = None
_profile_merge_service = None
_conflict_alignment_service = None

# Question Rewrite Service
_question_rewrite_service = None

# Capability Verify Service
_capability_verify_service = None

# Configuration flags
# 从 FeatureFlags 获取默认值，与环境变量配置保持一致
from src.infra.config.feature_flags import FeatureFlags
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
    global _candidate_recommendation_service, _expert_diagnosis_service, _group_fusion_service
    global _g5_expert_enhancer, _context_preparation_service, _llm_gateway_service
    global _structured_signal_extractor, _conflict_dimension_analyzer, _conflict_alignment_service
    global _perspective_provider
    global _max_parallel_workers
    global _embedding_generator, _vector_match_service  # 🔧 BUG FIX: 添加缺失的全局变量声明
    global _question_rewrite_service
    global _profile_merge_service

    _profile_source = None
    _api_profile_store = None
    _registry_filter = None
    _retrieval_service = None
    _candidate_recommendation_service = None
    _expert_diagnosis_service = None
    _group_fusion_service = None
    _g5_expert_enhancer = None
    _context_preparation_service = None
    _llm_gateway_service = None
    _structured_signal_extractor = None
    _conflict_dimension_analyzer = None
    _conflict_alignment_service = None
    _perspective_provider = None
    _vector_match_service = None
    _max_parallel_workers = 5  # 恢复默认值
    _embedding_generator = None  # 🔧 BUG FIX: 重置embedding generator
    _question_rewrite_service = None
    _profile_merge_service = None
    _capability_verify_service = None


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
        from src.infra.worker_profiles.sources.composite_worker_profile_source import CompositeWorkerProfileSource
        from src.infra.worker_profiles.sources.file_worker_profile_source import FileWorkerProfileSource
        # ApiWorkerProfileSource 已废弃，使用 APIProfileSource
        from src.infra.worker_profiles.sources.registry_worker_profile_source import RegistryWorkerProfileSource

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
            from src.infra.worker_profiles.sources.api_profile_source import APIProfileSource

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


def get_profile_source_from_request(request):
    """
    Create CompositeWorkerProfileSource using stores from OSS provider registry.

    Phase B3 Fix: This function ensures Fusion profile lookup uses the SAME profile_content_store
    instance as Profile CRUD/Activate.

    Why this is needed:
    - APIProfileSource defaults to creating its own SQLiteWorkerProfileContentStore
    - Profile CRUD/Activate uses request.app.state.context.registry worker_profile_content_store
    - This caused instance mismatch: Profile written to Instance A, Fusion reads from Instance B
    - Result: Fusion active_profiles_loaded_count = 0

    Args:
        request: FastAPI Request object with app.state.context.registry

    Returns:
        CompositeWorkerProfileSource with request-context stores

    Raises:
        RuntimeError: If critical stores are missing (no silent fallback)
    """
    try:
        from src.infra.worker_profiles.sources.composite_worker_profile_source import CompositeWorkerProfileSource
        from src.infra.worker_profiles.sources.registry_worker_profile_source import RegistryWorkerProfileSource
        from src.infra.worker_profiles.sources.api_profile_source import APIProfileSource

        # Get stores from OSS provider registry
        registry = request.app.state.context.registry

        # Worker registry store for RegistryWorkerProfileSource
        worker_registry_store = registry.get('worker_registry_store')
        worker_runtime_state_store = registry.get('worker_runtime_state_store')

        # Profile content store for APIProfileSource (CRITICAL: same instance as Profile CRUD)
        worker_profile_content_store = registry.get('worker_profile_content_store')
        if worker_profile_content_store is None:
            raise RuntimeError(
                "OSS registry missing 'worker_profile_content_store'. "
                "Profile CRUD/Activate and Fusion MUST use the same store instance. "
                "Check opensource.py _build_*_providers() registration."
            )

        # Create Registry source (highest priority)
        registry_source = None
        if worker_registry_store and worker_runtime_state_store:
            registry_source = RegistryWorkerProfileSource(
                registry_store=worker_registry_store,
                runtime_state_store=worker_runtime_state_store,
                include_offline=False,  # Only include online workers
            )
            logger.info(
                "[OSS-Safe] RegistryWorkerProfileSource created from request context: "
                "registry_store_id=%d, runtime_state_store_id=%d",
                id(worker_registry_store),
                id(worker_runtime_state_store)
            )

        # Create API source with injected content_store (Phase B3 fix)
        api_source = APIProfileSource(content_store=worker_profile_content_store)
        logger.info(
            "[OSS-Safe] APIProfileSource created with injected content_store: "
            "content_store_id=%d, content_store_type=%s",
            id(worker_profile_content_store),
            type(worker_profile_content_store).__name__
        )

        # Create composite source
        composite = CompositeWorkerProfileSource(
            registry_source=registry_source,
            api_source=api_source,
            file_source=None,  # FILE source disabled
        )

        logger.info(
            "[OSS-Safe] CompositeWorkerProfileSource created from request context: "
            "registry_source=%s, api_source=%s",
            "✓" if registry_source else "✗",
            "✓" if api_source else "✗"
        )

        return composite

    except Exception as e:
        logger.error(
            "[OSS-Safe] Failed to create CompositeWorkerProfileSource from request context: %s",
            e,
            exc_info=True
        )
        raise RuntimeError(
            f"Failed to create CompositeWorkerProfileSource from OSS registry: {e}. "
            f"This is a CRITICAL error - do not fall back to global singletons."
        ) from e


def _get_api_profile_store():
    """
    获取 API Profile Content Store 实例 - SQLite only for open-core
    """
    global _api_profile_store
    if _api_profile_store is None:
        try:
            # SQLite 模式：本地存储（open-core only）
            from src.infra.adapters.sqlite_worker_profile_content_store import SQLiteWorkerProfileContentStore
            from src.infra.config.worker_registry_settings import WorkerRegistrySettings

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
    from src.interfaces.api.dependencies.worker_dependencies import get_registry_store
    return get_registry_store()


def _get_runtime_state_store():
    """获取 Runtime State Store 实例"""
    from src.interfaces.api.dependencies.worker_dependencies import _get_runtime_state_store
    return _get_runtime_state_store()


def _get_profile_binding_store():
    """获取 Profile Binding Store 实例"""
    from src.interfaces.api.dependencies.worker_dependencies import _get_profile_binding_store
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
        from src.interfaces.api.dependencies.worker_dependencies import _get_profile_embedding_store as get_store
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

        # 扫描指定 worker 的 profile
        # 使用 staff_id 精确匹配，而非 profile_key.startswith(worker_id)
        # 原因：worker_id 可能包含 ":"（如 "bot_id:owner_id"），
        # startswith 会误匹配前缀相同的其他 worker（如 "default:334018" 匹配 "default:3340183"）
        scan_result = profile_src.scan()
        target_profile = None
        for profile in scan_result.profiles:
            if getattr(profile, 'staff_id', None) == worker_id or getattr(profile, 'worker_id', None) == worker_id:
                target_profile = profile
                break

        if not target_profile:
            logger.warning(f"[INDEX-BUILD] 未找到 worker 的 profile: {worker_id}")
            return False

        logger.info(f"[INDEX-BUILD] 找到 profile: {target_profile.profile_key}")

        # 使用单例 ProfileEmbedding Store - 避免重复创建 Qdrant 客户端导致锁冲突
        profile_store = _get_profile_embedding_store()

        # 清理同 worker 其他 profile_key 的残留向量（避免 filter 字段缺失的脏数据）
        if profile_store and hasattr(profile_store, 'delete_by_profile_key'):
            for profile in scan_result.profiles:
                if (getattr(profile, 'staff_id', None) == worker_id or getattr(profile, 'worker_id', None) == worker_id) and profile.profile_key != target_profile.profile_key:
                    try:
                        deleted = profile_store.delete_by_profile_key(profile.profile_key)
                        if deleted > 0:
                            logger.info(f"[INDEX-BUILD] 清理旧 profile 残留向量: {profile.profile_key}, deleted={deleted}")
                    except Exception as e_del:
                        logger.warning(f"[INDEX-BUILD] 清理旧 profile 向量失败: {profile.profile_key}, error={e_del}")

        if not profile_store:
            logger.warning("[INDEX-BUILD] Profile embedding store 不可用，跳过索引构建")
            return False

        # 创建索引器
        from src.domain.services.profile_embedding_indexer import ProfileEmbeddingIndexer
        indexer = ProfileEmbeddingIndexer(
            embedding_provider=embedding_gen,
            profile_store=profile_store,
        )

        # 获取 worker 状态（availability/runtime_state）
        worker_states = {}
        try:
            from src.interfaces.api.dependencies.worker_dependencies import _get_registry_store, _get_runtime_state_store
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
                        from src.domain.models.worker_lifecycle_state import WorkerLifecycleState
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
    if _embedding_generator is None:
        try:
            from src.infra.embedding.config.embedding_settings import EmbeddingSettings
            from src.infra.embedding.providers.real_provider import RealEmbeddingProvider

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
    global _vector_match_service
    if _vector_match_service is None:
        try:
            from src.application.services.worker_vector_match_service import WorkerVectorMatchService
            from src.infra.metadatastores.file_metadata_store_adapter import FileMetadataStoreAdapter
            from src.infra.embedding.config.embedding_settings import EmbeddingSettings

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

                from src.infra.public.vectorstores.qdrant_local_vector_store import QdrantLocalVectorStore
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
            from src.infra.config.feature_flags import FeatureFlags
            if FeatureFlags.is_registry_aware_filtering_enabled():
                from src.application.services.registry_aware_worker_filter import RegistryAwareWorkerFilter
                profile_filter = RegistryAwareWorkerFilter(
                    registry_store=_get_registry_store(),
                    runtime_state_store=_get_runtime_state_store(),
                    strict_mode=False,
                )
                logger.info("[VectorMatch] Registry-aware filter enabled")
            else:
                profile_filter = None
                logger.info("[VectorMatch] Registry-aware filter disabled, using vector store payload filtering only")

            _vector_match_service = WorkerVectorMatchService(
                vector_store=vector_store,
                metadata_store=metadata_store,
                profile_filter=profile_filter,
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

            # 检查向量存储是否为空
            vector_size = vector_store.size()
            if vector_size == 0:
                logger.error("❌ [VectorMatch] Vector store is EMPTY. Vector match will ALWAYS return no results.")
                logger.warning("⚠️  [VectorMatch] This is expected if vector index has not been built.")

                # Open-core: No ZDAS backend to load from, proceed with auto-build
                logger.warning("⚠️  [VectorMatch] Attempting auto-build...")

                # 尝试自动构建索引（如果启用了自动构建）
                from src.infra.config.feature_flags import FeatureFlags
                if FeatureFlags.is_enabled("ENABLE_PROFILE_EMBEDDING_INDEX"):
                    logger.info("[VectorMatch] Auto-building vector index (ENABLE_PROFILE_EMBEDDING_INDEX=true)...")
                    try:
                        # 获取 embedding generator 和 profile source
                        embedding_gen = _get_embedding_generator()
                        profile_src = _get_profile_source()

                        if not embedding_gen:
                            logger.error("❌ [VectorMatch] Embedding generator not available, cannot build index")
                        elif not profile_src:
                            logger.error("❌ [VectorMatch] Profile source not available, cannot build index")
                        else:
                            from src.domain.services.profile_embedding_indexer import ProfileEmbeddingIndexer
                            from src.infra.indexing.profile_embedding_store import ProfileEmbeddingStore

                            # QDRANT_SINGLETON_FIX: Get shared vector_store from app context to avoid lock conflicts
                            shared_vector_store = None
                            if _app_context is not None and _app_context.registry.has("vector_store"):
                                shared_vector_store = _app_context.registry.get("vector_store")
                                logger.info("[LOCAL_QDRANT_SINGLETON] component=ProfileEmbeddingStore(fusion) "
                                          f"vector_store_id={id(shared_vector_store)} "
                                          f"storage_path={getattr(shared_vector_store, 'path', 'N/A')} "
                                          f"source=registry")
                            else:
                                logger.error("[LOCAL_QDRANT_SINGLETON] component=ProfileEmbeddingStore(fusion) "
                                           "vector_store_id=None source=MISSING_APP_CONTEXT")

                            # 创建 ProfileEmbeddingStore - Open-core always uses local mode
                            profile_store = ProfileEmbeddingStore(
                                dimension=dimension,
                                index_type="local",  # Always local for open-core
                                db_path=resolve_data_path("data/vector_store.db"),  # 使用绝对路径
                                database=None,  # No database for open-core
                                datasource_name="agentclaw_ds",
                                vector_store=shared_vector_store,  # QDRANT_SINGLETON_FIX: Pass shared vector_store
                            )

                            # 创建索引器（使用正确的参数名）
                            indexer = ProfileEmbeddingIndexer(
                                embedding_provider=embedding_gen,
                                profile_store=profile_store,
                            )

                            # 从 profile source 获取所有 profiles (使用 scan().profiles)
                            scan_result = profile_src.scan()
                            all_profiles = scan_result.profiles
                            logger.info(f"[VectorMatch] Found {len(all_profiles)} profiles to index")

                            if all_profiles:
                                # 构建 worker 状态字典（用于 payload 中的 availability 和 runtime_state）
                                # Key 使用 staff_id（与 profile.staff_id 匹配）
                                worker_states = {}
                                try:
                                    from src.domain.models.worker_lifecycle_state import WorkerLifecycleState
                                    from src.domain.models.worker_runtime_state import WorkerRuntimeState

                                    # 获取所有 active workers
                                    registry_store = _get_registry_store()
                                    runtime_state_store = _get_runtime_state_store()
                                    active_workers = registry_store.list(
                                        lifecycle_states=[WorkerLifecycleState.ACTIVE]
                                    )

                                    # 批量获取 runtime states
                                    worker_ids = [w.id for w in active_workers]
                                    runtime_states = runtime_state_store.batch_get_runtime_states(worker_ids)

                                    # 构建 worker_states 字典
                                    # Key 为 worker id 或 identity.handle（去掉 @ 前缀）
                                    for worker in active_workers:
                                        runtime_state = runtime_states.get(worker.id)
                                        state_info = {
                                            "availability": worker.state.availability.value,
                                            "runtime_state": runtime_state.value if runtime_state else WorkerRuntimeState.OFFLINE.value,
                                        }
                                        # 使用 worker id 作为 key
                                        worker_states[worker.id] = state_info
                                        # 也使用 handle（去掉 @）作为 key，因为 profile.staff_id 可能等于 handle
                                        handle = worker.identity.handle
                                        if handle and handle.startswith("@"):
                                            staff_id = handle[1:]  # 去掉 @ 前缀
                                            worker_states[staff_id] = state_info
                                        # 也使用 external_id 作为 key（如果存在）
                                        if hasattr(worker, 'external_id') and worker.external_id:
                                            worker_states[worker.external_id] = state_info

                                        # 调试日志（前3个worker）
                                        if len(worker_states) <= 9:  # 前3个worker，每个可能添加3个key
                                            logger.info(f"[VectorMatch] Worker state mapping: worker.id={worker.id}, handle={handle}, external_id={getattr(worker, 'external_id', None)}, state={state_info}")

                                    logger.info(f"[VectorMatch] Loaded {len(active_workers)} worker states for indexing, mapped to {len(worker_states)} keys")
                                    logger.info(f"[VectorMatch] Worker state keys sample: {list(worker_states.keys())[:10]}")  # 打印前10个
                                except Exception as e:
                                    logger.warning(f"[VectorMatch] Failed to load worker states: {e}. Proceeding without availability/runtime_state filters.")
                                    worker_states = {}

                                # 构建索引（传入 worker_states）
                                result = indexer.build_index(
                                    profiles=all_profiles,
                                    clear_existing=False,
                                    worker_states=worker_states,
                                )

                                # IndexingResult 是 dataclass，不是 dict
                                if result.indexed_count > 0:
                                    logger.info(f"✅ [VectorMatch] Auto-build completed: indexed={result.indexed_count}, failed={result.failed_count}, duration={result.duration_seconds:.2f}s")

                                    # 关键修复：同步向量到服务的索引
                                    # QdrantZdasVectorStore 使用 sync_incremental()，FaissZdasVectorStore 使用 sync_from_backend()
                                    logger.info("[VectorMatch] Syncing vectors to service index...")
                                    if hasattr(vector_store, 'sync_from_backend'):
                                        vector_store.sync_from_backend(force=True)
                                    elif hasattr(vector_store, 'sync_incremental'):
                                        vector_store.sync_incremental()
                                else:
                                    logger.error(f"❌ [VectorMatch] Auto-build failed: no profiles indexed. Errors: {result.errors}")

                                # 重新检查（现在检查的是已同步的 vector_store）
                                vector_size = vector_store.size()
                                if vector_size > 0:
                                    logger.info(f"✅ [VectorMatch] Vector store now has {vector_size} vectors in memory.")
                                else:
                                    logger.error("❌ [VectorMatch] Vector store still empty after sync.")
                                    logger.error("❌ [VectorMatch] Check profile source configuration and embedding service.")
                            else:
                                logger.error("❌ [VectorMatch] No profiles found to index. Register workers and profiles first.")

                    except Exception as build_error:
                        logger.error(f"❌ [VectorMatch] Auto-build exception: {build_error}")
                        import traceback
                        logger.error(traceback.format_exc())
                        logger.warning("⚠️  [VectorMatch] Current behavior: Will fallback to keyword-only retrieval.")
                    else:
                        logger.error("❌ [VectorMatch] ENABLE_PROFILE_EMBEDDING_INDEX not enabled")
                        logger.error("❌ [VectorMatch] Vector retrieval will not work")
                        logger.warning("⚠️  [VectorMatch] To fix: Enable ENABLE_PROFILE_EMBEDDING_INDEX in config.")
            else:
                logger.info(f"✅ [VectorMatch] Vector store has {vector_size} vectors indexed.")
        except Exception as e:
            logger.error(f"❌ [VectorMatch] Failed to create vector match service: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return None
    return _vector_match_service


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
        recommendation_service = _create_llm_recommendation_service()
        logger.info("[DEP-DIAG] recommendation_service: %s", "已创建" if recommendation_service else "None")

        # 创建 G5 expert enhancer（需要 LLM gateway）
        logger.info("[DEP-DIAG] Step 2: 创建 G5 expert enhancer...")
        g5_enhancer = _create_g5_expert_enhancer()
        logger.info("[DEP-DIAG] g5_enhancer: %s", "已创建" if g5_enhancer else "None")

        # 获取 candidate recommendation service
        logger.info("[DEP-DIAG] Step 3: 获取 candidate recommendation service...")
        candidate_rec_svc = get_candidate_recommendation_service()
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
        from src.domain.services.worker_context_preparation_service import WorkerContextPreparationService
        _context_preparation_service = WorkerContextPreparationService()
        logger.info("[DEP-DIAG] ContextPreparationService 创建完成 (id=%d)", id(_context_preparation_service))
    return _context_preparation_service


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

        # 检查必要的环境变量
        base_url = os.environ.get("LLM_BASE_URL")
        auth_token = os.environ.get("LLM_AUTH_TOKEN")
        logger.info("[DEP-DIAG] LLM_BASE_URL: %s", base_url if base_url else "未设置")
        logger.info("[DEP-DIAG] LLM_AUTH_TOKEN: %s", "已设置" if auth_token else "未设置")

        if not base_url or not auth_token:
            logger.warning("[DEP-DIAG] ⚠️ LLM 配置不完整，gateway 不会被创建")
            logger.info("[DEP-DIAG] ========== LLMGatewayService 创建失败 ==========")
            return None

        try:
            from src.infra.llm.config.llm_settings import LLMSettings
            from src.infra.llm.providers.anthropic_compatible_provider import AnthropicCompatibleProvider
            from src.infra.llm.routing.static_llm_router import StaticLLMRouter
            from src.application.services.llm_gateway_service import LLMGatewayService

            logger.info("[DEP-DIAG] 创建 LLMSettings...")
            settings = LLMSettings()
            logger.info("[DEP-DIAG] LLMSettings: model=%s", getattr(settings, 'model', 'N/A'))

            logger.info("[DEP-DIAG] 创建 AnthropicCompatibleProvider...")
            provider = AnthropicCompatibleProvider(settings=settings)
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
        gateway = _get_llm_gateway_service()
        if gateway is None:
            logger.warning("[DEP-DIAG] ⚠️ 无法创建 G5 enhancer: LLM gateway 不可用")
            logger.info("[DEP-DIAG] ========== G5ExpertEnhancer 创建失败 (无 LLM) ==========")
            logger.info("="*80)
            return None
        logger.info("[DEP-DIAG] gateway已获取 (id=%d)", id(gateway))

        # 获取 retrieval service（必需）
        logger.info("[DEP-DIAG] Step 2: 获取 retrieval service...")
        retrieval_service = get_profile_retrieval_service()
        logger.info("[DEP-DIAG] retrieval_service 已获取 (id=%d)", id(retrieval_service))

        # 获取 context preparation service（必需）
        logger.info("[DEP-DIAG] Step 3: 获取 context preparation service...")
        preparation_service = _get_context_preparation_service()
        logger.info("[DEP-DIAG] preparation_service 已获取 (id=%d)", id(preparation_service))

        # 获取 profile source（必需）
        logger.info("[DEP-DIAG] Step 4: 获取 profile source...")
        profile_source = _get_profile_source()
        logger.info("[DEP-DIAG] profile_source 已获取 (id=%d, 类型=%s)",
                   id(profile_source), type(profile_source).__name__)

        try:
            from src.application.services.g5_expert_enhancer_impl import G5ExpertEnhancerImpl

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
    global _group_fusion_service
    if _group_fusion_service is None:
        import os
        logger.info("="*80)
        logger.info("[DEP-DIAG] ========== 创建 GroupFusionService 单例 ==========")
        logger.info("[DEP-DIAG] PID: %d", os.getpid())
        logger.info("[DEP-DIAG] 调用时间: %s", datetime.now().isoformat())

        # 尝试创建 LLM recommendation service
        logger.info("[DEP-DIAG] Step 1: 创建 LLM recommendation service...")
        llm_rec_service = _create_llm_recommendation_service()
        logger.info("[DEP-DIAG] llm_rec_service: %s", "已创建" if llm_rec_service else "None")

        # Step 1.5: 获取 LLM Gateway（基础服务，其他服务可能依赖）
        logger.info("[DEP-DIAG] Step 1.5: 获取 LLM Gateway...")
        llm_gateway = _get_llm_gateway_service()
        logger.info("[DEP-DIAG] llm_gateway: %s", "已创建" if llm_gateway else "None")

        # Step 2: 创建 perspective provider（LLM 或 Stub）
        logger.info("[DEP-DIAG] Step 2: 创建 perspective provider...")
        perspective_provider = _get_perspective_provider()
        logger.info("[DEP-DIAG] perspective_provider: %s", type(perspective_provider).__name__ if perspective_provider else "None")

        # Step 3: 创建 availability checker
        logger.info("[DEP-DIAG] Step 3: 创建 availability checker...")
        availability_checker = _get_availability_checker()
        logger.info("[DEP-DIAG] availability_checker: %s", "已创建" if availability_checker else "None")

        # 获取 expert diagnosis service（这一步会触发 G5 enhancer 的创建）
        logger.info("[DEP-DIAG] Step 4: 获取 expert diagnosis service...")
        expert_diagnosis_svc = get_expert_diagnosis_service()
        logger.info("[DEP-DIAG] expert_diagnosis_service: %s", "已创建" if expert_diagnosis_svc else "None")
        if expert_diagnosis_svc:
            logger.info("[DEP-DIAG]   - _g5_enhancer: %s",
                       "已注入" if expert_diagnosis_svc._g5_enhancer else "None")
            logger.info("[DEP-DIAG]   - _candidate_recommendation_service: %s",
                       "已注入" if expert_diagnosis_svc._candidate_recommendation_service else "None")

        # G2 V2: 获取 conflict alignment service（这一步会触发 V2 依赖的创建）
        logger.info("[DEP-DIAG] Step 5: 获取 conflict alignment service...")
        conflict_alignment_svc = get_conflict_alignment_service()
        logger.info("[DEP-DIAG] conflict_alignment_service: %s", "已创建" if conflict_alignment_svc else "None")
        if conflict_alignment_svc:
            logger.info("[DEP-DIAG]   - _signal_extractor: %s",
                       "已注入" if conflict_alignment_svc._signal_extractor else "None")
            logger.info("[DEP-DIAG]   - _conflict_analyzer: %s",
                       "已注入" if conflict_alignment_svc._conflict_analyzer else "None")

        # G9: 获取 profile merge service
        logger.info("[DEP-DIAG] Step 6: 获取 profile merge service...")
        profile_merge_svc = _get_profile_merge_service()
        logger.info("[DEP-DIAG] profile_merge_service: %s", "已创建" if profile_merge_svc else "None")
        if profile_merge_svc is None:
            logger.warning("[DEP-DIAG] ⚠️ ProfileMergeService 创建失败，G9 模式将不可用")
            logger.warning("[DEP-DIAG] ⚠️ 如果需要使用 G9 模式，请检查:")
            logger.warning("[DEP-DIAG]   - ENABLE_REAL_LLM=true 或 LLM_ENABLED=true 环境变量")
            logger.warning("[DEP-DIAG]   - LLM_BASE_URL 和 LLM_AUTH_TOKEN 配置")
            logger.warning("[DEP-DIAG]   - Profile Store (ZDAS/SQLite) 配置和数据")

        # G9: 获取群组上下文服务（会话总结）
        logger.info("[DEP-DIAG] Step 6.5: 获取 group context service...")
        group_context_svc = _get_group_context_service()
        logger.info("[DEP-DIAG] group_context_service: %s", "已创建" if group_context_svc else "None")

        # G9: 获取 FusionExpertChat 服务（Prompt构建 + LLM调用 + 结果构建）
        logger.info("[DEP-DIAG] Step 6.6: 获取 fusion expert chat service...")
        fusion_expert_chat_svc = _get_fusion_expert_chat_service()
        logger.info("[DEP-DIAG] fusion_expert_chat_service: %s", "已创建" if fusion_expert_chat_svc else "None")

        _group_fusion_service = GroupFusionService(
            provider=perspective_provider,  # 使用 LLM 或 Stub provider
            recommendation_service=llm_rec_service,
            conflict_alignment_service=conflict_alignment_svc,  # G2 V2 服务
            expert_diagnosis_service=expert_diagnosis_svc,  # 带过滤器的 G5 服务
            profile_merge_service=profile_merge_svc,  # G9 Profile Merge 服务
            fusion_expert_chat_service=fusion_expert_chat_svc,  # G9 FusionExpertChat 服务（Prompt构建 + LLM调用）
            group_context_service=group_context_svc,  # G9: 群组上下文服务（会话总结）
            availability_checker=availability_checker,  # Phase 5: offline participant warning
            worker_store=_get_registry_store(),  # G9: batch_get_configs 批量查 fusion_enable
            max_parallel_workers=_max_parallel_workers,  # 并行收集视角配置
        )
        logger.info("[DEP-DIAG] GroupFusionService 创建完成")
        logger.info("[DEP-DIAG] ========== GroupFusionService 单例创建完成 ==========")
        logger.info("="*80)
    else:
        logger.debug("[DEP-DIAG] 返回缓存的 GroupFusionService 单例 (id=%d)", id(_group_fusion_service))

    return _group_fusion_service


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
        from src.application.services.participant_availability_checker import ParticipantAvailabilityChecker

        # 从 worker_dependencies 获取 stores
        # 关键：使用 _get_profile_binding_store 而不是创建新的 SQLite 实例
        from src.interfaces.api.dependencies.worker_dependencies import (
            get_registry_store,
            _get_runtime_state_store,
            _get_profile_binding_store,  # 使用统一的存储实例
        )

        profile_binding_store = _get_profile_binding_store()  # 使用统一的存储实例（支持 ZDAS）
        runtime_state_store = _get_runtime_state_store()
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
            from src.application.services.structured_signal_extractor import StructuredSignalExtractor
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
            from src.domain.services.conflict_dimension_analyzer import ConflictDimensionAnalyzer
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
    global _fused_profile_storage_service
    if _fused_profile_storage_service is None:
        try:
            from src.application.services.bot_fuse.fused_profile_storage_service import FusedProfileStorageService
            from src.infra.repositories.fused_profile_repository import FusedProfileRepository
            from src.infra.adapters.sqlite_fused_profile_store import SQLiteFusedProfileStore
            from src.infra.config.worker_registry_settings import WorkerRegistrySettings

            # SQLite 模式：本地存储（open-core only）
            registry_settings = WorkerRegistrySettings()
            db_path = registry_settings.get_effective_db_path()
            store = SQLiteFusedProfileStore(db_path)
            logger.info("[Open-Core] Using SQLite FusedProfileStore, path=%s", db_path)

            _fused_profile_storage_service = FusedProfileStorageService(
                repository=store,
                enable_memory_cache=True,
                cache_ttl_seconds=86400,  # 1 天缓存
            )
            logger.info("[Open-Core] FusedProfileStorageService 创建完成")
        except Exception as e:
            logger.error("[Open-Core] Failed to create FusedProfileStorageService: %s", e)
            return None
    return _fused_profile_storage_service


def _get_profile_merge_service():
    """
    获取 ProfileMergeService 实例（G9）

    用于 G9 Profile Merge 模式，融合多个 participant 的 Profile。

    Returns:
        ProfileMergeService 或 None（如果依赖不可用）
    """
    global _profile_merge_service
    if _profile_merge_service is None:
        try:
            import traceback
            from src.application.services.bot_fuse.profile_merge_service import ProfileMergeService

            # 获取 Profile Store
            logger.info("[G9] Step 1: Getting profile store...")
            profile_store = _get_api_profile_store()
            if profile_store is None:
                logger.warning("[G9] Failed to get profile store, ProfileMergeService unavailable")
                logger.warning("[G9] Check if ZDAS/SQLite database is properly configured")
                return None
            logger.info("[G9] Step 1: profile_store = %s", type(profile_store).__name__)

            # 获取 LLM Gateway
            logger.info("[G9] Step 2: Getting LLM gateway...")
            llm_gateway = _get_llm_gateway_service()
            if llm_gateway is None:
                logger.warning("[G9] Failed to get LLM gateway, ProfileMergeService unavailable")
                logger.warning("[G9] Check if ENABLE_REAL_LLM=true or LLM_ENABLED=true and LLM_BASE_URL is set")
                return None
            logger.info("[G9] Step 2: llm_gateway = %s", type(llm_gateway).__name__)

            # 获取 Storage Service
            logger.info("[G9] Step 3: Getting storage service...")
            storage_service = _get_fused_profile_storage_service()
            if storage_service is None:
                logger.warning("[G9] Failed to get storage service, ProfileMergeService unavailable")
                return None
            logger.info("[G9] Step 3: storage_service = %s", type(storage_service).__name__)

            _profile_merge_service = ProfileMergeService(
                profile_store=profile_store,
                llm_gateway=llm_gateway,
                storage_service=storage_service,
            )
            logger.info("[G9] ProfileMergeService 创建完成")
        except Exception as e:
            logger.error("[G9] Failed to create ProfileMergeService: %s", e)
            logger.error("[G9] Traceback: %s", traceback.format_exc())
            return None
    return _profile_merge_service


# GroupContextService 单例（G9 用于获取群组对话上下文）
_group_context_service = None


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

            from src.application.services.bot_fuse.group_context_service import GroupContextService

            # 获取 LLM Gateway（必需）
            llm_gateway = _get_llm_gateway_service()
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


# FusionExpertChatService 单例（G9 用于构建对话 Prompt 和调用 LLM）
_fusion_expert_chat_service = None


def _get_fusion_expert_chat_service():
    """
    获取 FusionExpertChatService 实例（G9）

    用于 G9 Profile Fusion 模式，负责 Prompt 构建、LLM 调用和结果构建。

    Returns:
        FusionExpertChatService 实例或 None（如果依赖不可用）
    """
    global _fusion_expert_chat_service
    if _fusion_expert_chat_service is None:
        try:
            from src.application.services.bot_fuse.fusion_expert_chat_service import FusionExpertChatService

            # 获取 LLM Gateway（必需）
            llm_gateway = _get_llm_gateway_service()
            if llm_gateway is None:
                logger.warning("[G9] FusionExpertChatService 创建失败: LLMGatewayService 不可用")
                return None

            # 获取 Storage Service（可选，用于记录对话轮次）
            storage_service = _get_fused_profile_storage_service()

            _fusion_expert_chat_service = FusionExpertChatService(
                llm_gateway=llm_gateway,
                storage_service=storage_service,
            )
            logger.info("[G9] FusionExpertChatService 创建完成")
        except Exception as e:
            logger.warning(f"[G9] Failed to create FusionExpertChatService: {e}")
            return None
    return _fusion_expert_chat_service


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
        llm_rec_service = _create_llm_recommendation_service()
        logger.info("[DEP-G2] llm_rec_service: %s", "已创建" if llm_rec_service else "None")

        # 获取 Layer 1: LLM冲突分析器
        logger.info("[DEP-G2] Step 2: 获取 Layer 1 LLM冲突分析器...")
        llm_analyzer = None
        try:
            from src.application.services.llm_conflict_analyzer import LLMConflictAnalyzer
            from src.infra.config.feature_flags import FeatureFlags
            if FeatureFlags.is_enabled("ENABLE_G2_LLM_CONFLICT_ANALYSIS"):
                # 获取 LLM Gateway，从中提取 provider 和 router
                gateway = _get_llm_gateway_service()
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
        signal_extractor = _get_structured_signal_extractor()
        conflict_analyzer = _get_conflict_dimension_analyzer()
        logger.info("[DEP-G2] signal_extractor: %s", "已创建" if signal_extractor else "None")
        logger.info("[DEP-G2] conflict_analyzer: %s", "已创建" if conflict_analyzer else "None")

        from src.application.services.conflict_alignment_service import ConflictAlignmentService

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


# =============================================================================
# Helper Functions
# =============================================================================

# Perspective Provider Instance
_perspective_provider = None


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
        gateway = _get_llm_gateway_service()

        if gateway is not None:
            # LLM 可用，创建 LLMPerspectiveProvider
            try:
                from src.infra.providers.llm_perspective_provider import LLMPerspectiveProvider

                profile_source = _get_profile_source()
                if profile_source is None:
                    logger.warning("[DEP-DIAG] ⚠️ Profile source 不可用，回退到 Stub")
                    from src.infra.providers.stub_perspective_provider import StubPerspectiveProvider
                    _perspective_provider = StubPerspectiveProvider()
                else:
                    _perspective_provider = LLMPerspectiveProvider(
                        gateway=gateway,
                        profile_source=profile_source,
                    )
                    logger.info("[DEP-DIAG] ✅ LLMPerspectiveProvider 创建完成")
            except Exception as e:
                logger.warning("[DEP-DIAG] ⚠️ LLMPerspectiveProvider 创建失败: %s，回退到 Stub", str(e))
                from src.infra.providers.stub_perspective_provider import StubPerspectiveProvider
                _perspective_provider = StubPerspectiveProvider()
        else:
            # LLM 不可用，使用 Stub
            logger.warning("[DEP-DIAG] ⚠️ LLM 不可用，使用 StubPerspectiveProvider")
            from src.infra.providers.stub_perspective_provider import StubPerspectiveProvider
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

    # 检查必要的环境变量
    base_url = os.environ.get("LLM_BASE_URL")
    auth_token = os.environ.get("LLM_AUTH_TOKEN")

    if not base_url or not auth_token:
        return None

    # 创建 LLM 服务链
    try:
        from src.infra.llm.config.llm_settings import LLMSettings
        from src.infra.llm.providers.anthropic_compatible_provider import AnthropicCompatibleProvider
        from src.infra.llm.routing.static_llm_router import StaticLLMRouter
        from src.application.services.llm_gateway_service import LLMGatewayService
        from src.application.services.fusion_recommendation_service import FusionRecommendationService

        settings = LLMSettings()
        provider = AnthropicCompatibleProvider(settings=settings)
        router = StaticLLMRouter(settings=settings)
        gateway = LLMGatewayService(provider=provider, router=router)
        return FusionRecommendationService(gateway=gateway)
    except Exception as e:
        logger.warning(f"Failed to create LLM recommendation service: {e}")
        return None


# =============================================================================
# No-Op Filter (for when filtering is disabled)
# =============================================================================

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
        gateway = _get_llm_gateway_service()
        if gateway is None:
            logger.warning("[DEP-DIAG] ⚠️ LLM Gateway 不可用, QuestionRewriteService 无法创建")
            return None

        try:
            from src.application.services.question_rewrite_service import QuestionRewriteService
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
    return _get_profile_source()


# =====================================
# Capability Verify — DI Registration
# =====================================



def get_capability_verify_service():
    """
    获取 CapabilityVerifyService 单例并自动订阅 EventBus。

    开关优先级：DRM 动态配置 > 本地 FeatureFlags 静态配置。
    仅在开关为 true 时创建。
    """
    global _capability_verify_service
    if _capability_verify_service is not None:
        return _capability_verify_service

    from src.application.utils.drm_config_helper import is_capability_verify_enabled as drm_capability_verify
    _cap_verify_on = drm_capability_verify()
    if _cap_verify_on is None:
        from src.infra.config.feature_flags import FeatureFlags
        _cap_verify_on = FeatureFlags.is_capability_verify_enabled()
    if not _cap_verify_on:
        logger.info("[DI] Capability verify disabled, skipping service creation")
        return None

    try:
        import os
        gateway = _get_llm_gateway_service()
        if gateway is None:
            logger.warning("[DI] LLM Gateway not available, CapabilityVerifyService cannot be created")
            return None

        from src.application.services.verify_prompt_composer import VerifyPromptComposer
        from src.application.services.verify_executor import VerifyExecutor
        from src.application.services.verify_judge import VerifyJudge
        from src.application.services.capability_verify_service import CapabilityVerifyService
        from src.application.services.peer_review_service import PeerReviewService
        from src.domain.events import get_event_bus, WorkerProfileCreatedEvent
        from src.infra.config.capability_verify_settings import CapabilityVerifySettings

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
            worker_repo=_get_registry_store(),
            recommendation_service=get_candidate_recommendation_service(),
            top_k=settings.peer_top_k,
            min_similarity=settings.peer_min_similarity,
        )

        _capability_verify_service = CapabilityVerifyService(
            prompt_composer=prompt_composer,
            executor=executor,
            judge=judge,
            worker_repo=_get_registry_store(),
            profile_repo=_get_api_profile_store(),
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

# =============================================================================
# OSS-Safe Request-Context Dependency Injection
# =============================================================================

def get_availability_checker_from_request(request):
    """
    Create ParticipantAvailabilityChecker using stores from OSS provider registry.

    P1 Fix: This function ensures Fusion uses the SAME store instances as OSS Worker CRUD.

    Why this is needed:
    - OSS Worker CRUD uses stores from request.app.state.context.registry
    - Old implementation used global singletons from worker_dependencies.py
    - This caused instance mismatch: Worker created in MySQL, Fusion checked in SQLite
    - Result: Fusion couldn't find workers, perspectives = 0

    Phase B3 Fix: Now also uses worker_profile_binding_store from registry.

    Args:
        request: FastAPI Request object with app.state.context.registry

    Returns:
        ParticipantAvailabilityChecker or None (if dependencies unavailable)

    Raises:
        RuntimeError: If critical stores are missing (no silent fallback)
    """
    try:
        from src.application.services.participant_availability_checker import ParticipantAvailabilityChecker

        # Get stores from OSS provider registry (CRITICAL: same as Worker CRUD)
        registry = request.app.state.context.registry

        # Worker registry store (CRITICAL: same instance as Worker CRUD)
        worker_registry_store = registry.get('worker_registry_store')
        if worker_registry_store is None:
            raise RuntimeError(
                "OSS registry missing 'worker_registry_store'. "
                "Worker CRUD and Fusion MUST use the same store instance. "
                "Check opensource.py _build_*_providers() registration."
            )

        # Worker runtime state store (CRITICAL: same instance as Worker CRUD)
        worker_runtime_state_store = registry.get('worker_runtime_state_store')
        if worker_runtime_state_store is None:
            raise RuntimeError(
                "OSS registry missing 'worker_runtime_state_store'. "
                "Worker CRUD and Fusion MUST use the same store instance. "
                "Check opensource.py _build_*_providers() registration."
            )

        # Phase B3 Fix: Worker profile binding store from registry (no more global singleton)
        worker_profile_binding_store = registry.get('worker_profile_binding_store')
        if worker_profile_binding_store is None:
            raise RuntimeError(
                "OSS registry missing 'worker_profile_binding_store'. "
                "Profile CRUD/Activate and Fusion MUST use the same store instance. "
                "Check opensource.py _build_*_providers() registration (Phase B1)."
            )

        logger.info(
            "[OSS-Safe] Created ParticipantAvailabilityChecker with stores from request context: "
            "registry_store_id=%d, runtime_state_store_id=%d, profile_binding_store_id=%d",
            id(worker_registry_store),
            id(worker_runtime_state_store),
            id(worker_profile_binding_store)
        )

        return ParticipantAvailabilityChecker(
            registry_store=worker_registry_store,
            runtime_state_store=worker_runtime_state_store,
            profile_binding_store=worker_profile_binding_store,
        )

    except Exception as e:
        logger.error(
            "[OSS-Safe] Failed to create ParticipantAvailabilityChecker from request context: %s",
            e,
            exc_info=True
        )
        raise RuntimeError(
            f"Failed to create ParticipantAvailabilityChecker from OSS registry: {e}. "
            f"This is a CRITICAL error - do not fall back to global singletons."
        ) from e
