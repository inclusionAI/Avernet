"""
Worker Dependencies (Open-Core Safe - SQLite Only)

Worker API 的依赖注入模块。

**Open-Core版本：仅支持SQLite存储**
- SQLite: 本地 SQLite 数据库（单实例）

**内部版本使用 ZDAS/MySQL**：
- 内部运行时使用 bcsfuse_internal.providers.storage.zdas_* providers
- 内部依赖注入通过 internal_app_factory 配置

使用方式：
```python
from fastapi import Depends
from src.interfaces.api.dependencies.worker_dependencies import (
    get_worker_import_service,
    get_worker_runtime_state_service,
)

@router.post("/workers")
async def create_worker(
    service: WorkerImportService = Depends(get_worker_import_service),
):
    ...
```
"""

from __future__ import annotations

import logging
import os
from functools import lru_cache

from src.infra.config.data_paths import resolve_data_path
from typing import Generator, Protocol, runtime_checkable

from src.application.services.worker_import_service import WorkerImportService
from src.application.services.worker_runtime_state_service import WorkerRuntimeStateService
from src.infra.adapters.sqlite_worker_registry_store import SQLiteWorkerRegistryStore
from src.infra.adapters.sqlite_worker_runtime_state_store import SQLiteWorkerRuntimeStateStore
from src.infra.adapters.sqlite_worker_profile_binding_store import SQLiteWorkerProfileBindingStore
from src.infra.adapters.sqlite_worker_audit_log_store import SQLiteWorkerAuditLogStore
from src.infra.adapters.in_memory_worker_index_sync_adapter import InMemoryWorkerIndexSyncAdapter
from src.infra.config.worker_registry_settings import WorkerRegistrySettings
from src.infra.config.ecb_settings import ECBSettings
from src.domain.protocols.bot_cognition_protocol import BotCognitionProvider
from src.infra.providers.ecb_bot_recognition_provider import ECBBotRecognitionProvider, StubBotCognitionProvider

logger = logging.getLogger(__name__)

# 启动时记录日志
logger.info("[Open-Core] worker_dependencies module loaded (SQLite only)")


# ============================================================================
# Store Protocol (for type hints)
# ============================================================================

@runtime_checkable
class RegistryStoreProtocol(Protocol):
    """Registry Store 协议"""
    def create(self, worker): ...
    def get_by_id(self, worker_id: str): ...
    def list(self, lifecycle_states=None, source_types=None, domains=None, limit=None, offset=None): ...
    def update(self, worker): ...
    def delete(self, worker_id: str): ...
    def exists(self, worker_id: str): ...
    def count(self, lifecycle_states=None): ...


@runtime_checkable
class RuntimeStateStoreProtocol(Protocol):
    """Runtime State Store 协议"""
    def get_runtime_state(self, worker_id: str): ...
    def set_runtime_state(self, worker_id: str, runtime_state, updated_by=None): ...
    def batch_get_runtime_states(self, worker_ids: list): ...
    def count_by_state(self, runtime_state): ...


@runtime_checkable
class ProfileBindingStoreProtocol(Protocol):
    """Profile Binding Store 协议"""
    def bind_profile(self, worker_id: str, profile_key: str, source_type): ...
    def unbind_profile(self, worker_id: str, profile_key: str): ...
    def get_active_binding(self, worker_id: str): ...
    def set_active_profile(self, worker_id: str, profile_key: str): ...
    def list_bindings_by_worker(self, worker_id: str): ...
    def get_binding_by_profile_key(self, profile_key: str): ...


@runtime_checkable
class AuditLogStoreProtocol(Protocol):
    """Audit Log Store 协议"""
    def append_log(self, audit_log): ...
    def list_logs(self, worker_id=None, actions=None, limit=100, offset=0): ...
    def get_latest_log(self, worker_id: str): ...


# ============================================================================
# Global Instances (Singleton pattern)
# ============================================================================

_registry_store: RegistryStoreProtocol | None = None
_runtime_state_store: RuntimeStateStoreProtocol | None = None
_profile_binding_store: ProfileBindingStoreProtocol | None = None
_audit_log_store: AuditLogStoreProtocol | None = None
_index_sync_adapter: InMemoryWorkerIndexSyncAdapter | None = None
_profile_embedding_store = None  # ProfileEmbeddingStore 单例
_bot_cognition_provider: BotCognitionProvider | None = None  # Bot 认知 Provider 单例


def _get_registry_store() -> RegistryStoreProtocol:
    """
    获取 Worker Registry Store（单例）

    R15-E FIX: Use store from ProviderRegistry if available (runtime mode with MySQL).
    Fallback to SQLite for dev/test mode.

    This fixes the critical bug where write path uses MySQL but read path uses SQLite.
    """
    global _registry_store
    if _registry_store is None:
        # PRIORITY 1: Use store from ProviderRegistry if available (runtime mode)
        # QDRANT_SINGLETON_FIX: Use shared _app_context instead of build_application_context()
        try:
            from src.interfaces.api.dependencies.fusion_dependencies import get_app_context
            app_context = get_app_context()
            if app_context is not None:
                registry = app_context.registry
                if registry.has("worker_registry_store"):
                    logger.info("[Open-Core] Using Registry Store from ProviderRegistry (MySQL)")
                    _registry_store = registry.get("worker_registry_store")
                    logger.info("[Open-Core] Registry Store initialized from ProviderRegistry")
                    return _registry_store
        except Exception as e:
            logger.warning(f"[Open-Core] Failed to get registry store from ProviderRegistry: {e}")

        # FALLBACK: Create SQLite store (dev/test mode)
        logger.info("[Open-Core] Initializing SQLite Registry Store (fallback)...")
        registry_settings = WorkerRegistrySettings()
        db_path = registry_settings.get_effective_db_path()
        logger.info("[Open-Core] SQLite database path: %s", db_path)
        _registry_store = SQLiteWorkerRegistryStore(db_path)
        logger.info("[Open-Core] SQLite Worker Registry Store initialized")

    return _registry_store


def _get_runtime_state_store() -> RuntimeStateStoreProtocol:
    """
    获取 Runtime State Store（单例）

    R15-E FIX: Use store from ProviderRegistry if available (runtime mode with MySQL).
    Fallback to SQLite for dev/test mode.

    This fixes the critical bug where write path uses MySQL but read path uses SQLite.
    """
    global _runtime_state_store
    if _runtime_state_store is None:
        # PRIORITY 1: Use store from ProviderRegistry if available (runtime mode)
        # QDRANT_SINGLETON_FIX: Use shared _app_context instead of build_application_context()
        try:
            from src.interfaces.api.dependencies.fusion_dependencies import get_app_context
            app_context = get_app_context()
            if app_context is not None:
                registry = app_context.registry
                if registry.has("worker_runtime_state_store"):
                    logger.info("[Open-Core] Using Runtime State Store from ProviderRegistry (MySQL)")
                _runtime_state_store = registry.get("worker_runtime_state_store")
                logger.info("[Open-Core] Runtime State Store initialized from ProviderRegistry")
                return _runtime_state_store
        except Exception as e:
            logger.warning(f"[Open-Core] Failed to get runtime state store from ProviderRegistry: {e}")

        # FALLBACK: Create SQLite store (dev/test mode)
        logger.info("[Open-Core] Initializing SQLite Runtime State Store (fallback)...")
        registry_settings = WorkerRegistrySettings()
        db_path = registry_settings.get_effective_db_path()
        _runtime_state_store = SQLiteWorkerRuntimeStateStore(db_path)
        logger.info("[Open-Core] SQLite Runtime State Store initialized")

    return _runtime_state_store


def _get_profile_binding_store() -> ProfileBindingStoreProtocol:
    """
    获取 Profile Binding Store（单例）

    R15-E FIX: Use store from ProviderRegistry if available (runtime mode with MySQL).
    Fallback to SQLite for dev/test mode.

    This fixes the critical bug where write path uses MySQL but read path uses SQLite.
    """
    global _profile_binding_store
    if _profile_binding_store is None:
        # PRIORITY 1: Use store from ProviderRegistry if available (runtime mode)
        # QDRANT_SINGLETON_FIX: Use shared _app_context instead of build_application_context()
        try:
            from src.interfaces.api.dependencies.fusion_dependencies import get_app_context
            app_context = get_app_context()
            if app_context is not None:
                registry = app_context.registry
                if registry.has("worker_profile_binding_store"):
                    logger.info("[Open-Core] Using Profile Binding Store from ProviderRegistry (MySQL)")
                _profile_binding_store = registry.get("worker_profile_binding_store")
                logger.info("[Open-Core] Profile Binding Store initialized from ProviderRegistry")
                return _profile_binding_store
        except Exception as e:
            logger.warning(f"[Open-Core] Failed to get profile binding store from ProviderRegistry: {e}")

        # FALLBACK: Create SQLite store (dev/test mode)
        logger.info("[Open-Core] Initializing SQLite Profile Binding Store (fallback)...")
        registry_settings = WorkerRegistrySettings()
        db_path = registry_settings.get_effective_db_path()
        _profile_binding_store = SQLiteWorkerProfileBindingStore(db_path)
        logger.info("[Open-Core] SQLite Profile Binding Store initialized")

    return _profile_binding_store


def get_audit_log_store() -> AuditLogStoreProtocol:
    """获取 Audit Log Store（单例）- SQLite only for open-core"""
    global _audit_log_store
    if _audit_log_store is None:
        logger.info("[Open-Core] Initializing SQLite Audit Log Store...")
        registry_settings = WorkerRegistrySettings()
        db_path = registry_settings.get_effective_db_path()
        _audit_log_store = SQLiteWorkerAuditLogStore(db_path)
        logger.info("[Open-Core] SQLite Audit Log Store initialized")

    return _audit_log_store


def _get_index_sync_adapter() -> InMemoryWorkerIndexSyncAdapter:
    """获取 Index Sync Adapter（单例）"""
    global _index_sync_adapter
    if _index_sync_adapter is None:
        _index_sync_adapter = InMemoryWorkerIndexSyncAdapter()
    return _index_sync_adapter


def _get_profile_embedding_store():
    """
    获取 Profile Embedding Store（单例）- Local/SQLite only for open-core

    用于 worker 下线时删除向量数据。

    Returns:
        ProfileEmbeddingStore 实例或 None（如果初始化失败）
    """
    global _profile_embedding_store
    if _profile_embedding_store is None:
        try:
            from src.infra.indexing.profile_embedding_store import ProfileEmbeddingStore
            from src.infra.embedding.config.embedding_settings import EmbeddingSettings

            settings = EmbeddingSettings()

            # Try to get the shared vector store from app context to avoid Qdrant lock conflicts
            # QDRANT_SINGLETON_FIX: Use shared _app_context instead of build_application_context()
            shared_vector_store = None
            try:
                from src.interfaces.api.dependencies.fusion_dependencies import get_app_context
                app_context = get_app_context()
                if app_context is not None and app_context.registry.has("vector_store"):
                    shared_vector_store = app_context.registry.get("vector_store")
                    logger.info(
                        "[LOCAL_QDRANT_SINGLETON] component=ProfileEmbeddingStore(worker_dep) "
                        f"vector_store_id={id(shared_vector_store)} "
                        f"storage_path={getattr(shared_vector_store, 'path', 'N/A')} "
                        f"source=registry"
                    )
                else:
                    logger.warning("[LOCAL_QDRANT_SINGLETON] component=ProfileEmbeddingStore(worker_dep) "
                                 "app_context not available or vector_store not in registry")
            except Exception as e:
                logger.warning(f"[Open-Core] Failed to get shared vector_store: {e}. Creating new instance.")

            # Open-core always uses local mode (no ZDAS/Database dependency)
            _profile_embedding_store = ProfileEmbeddingStore(
                dimension=settings.dimension,
                index_type="local",  # Always local for open-core
                db_path=resolve_data_path("data/vector_store.db"),
                database=None,  # No database for open-core
                datasource_name="agentclaw_ds",
                vector_store=shared_vector_store,  # Pass shared vector store
            )
            logger.info("[Open-Core] Profile Embedding Store initialized (type=local)")
        except Exception as e:
            logger.warning(f"[Open-Core] Failed to initialize Profile Embedding Store: {e}")
            _profile_embedding_store = None

    return _profile_embedding_store


def _get_bot_cognition_provider() -> BotCognitionProvider | None:
    """
    获取 Bot 认知 Provider（单例）

    根据配置选择真实 Provider 或 Stub 实现。
    部署到 ACP 后通过 ACE 登录态自动鉴权。

    当 ECB_ENABLED=false 时不初始化 Provider，返回 None。

    Returns:
        BotCognitionProvider 实现或 None
    """
    global _bot_cognition_provider
    if _bot_cognition_provider is None:
        settings = ECBSettings()
        if not settings.enabled:
            logger.info("[BotCognition] Bot 认知功能已关闭 (ECB_ENABLED=false)")
            _bot_cognition_provider = None
        elif settings.is_configured and settings.base_url:
            logger.info("[BotCognition] Initializing with base_url=%s", settings.base_url)
            _bot_cognition_provider = ECBBotRecognitionProvider(
                base_url=settings.base_url,
                timeout_ms=settings.timeout_ms,
            )
        else:
            logger.info("[BotCognition] Using StubBotCognitionProvider")
            _bot_cognition_provider = StubBotCognitionProvider()
    return _bot_cognition_provider


# ============================================================================
# FastAPI Dependencies
# ============================================================================

def get_worker_import_service() -> WorkerImportService:
    """
    获取 WorkerImportService 依赖

    用于 API 注册 Worker。

    Returns:
        WorkerImportService 实例
    """
    return WorkerImportService(
        registry_store=_get_registry_store(),
        runtime_state_store=_get_runtime_state_store(),
        profile_binding_store=_get_profile_binding_store(),
        audit_log_adapter=get_audit_log_store(),
        index_sync_adapter=_get_index_sync_adapter(),
    )


def get_worker_profile_content_service():
    """
    获取 WorkerProfileContentService 依赖

    R15-E FIX: Use store from ProviderRegistry if available (runtime mode with MySQL).
    Fallback to SQLite for dev/test mode.

    用于删除 Worker 时级联删除 Profiles。

    Returns:
        WorkerProfileContentService 实例
    """
    from src.infra.embedding.config.embedding_settings import EmbeddingSettings
    from src.infra.config.data_paths import resolve_data_path
    from src.domain.services.profile_embedding_indexer import ProfileEmbeddingIndexer
    from src.infra.indexing.profile_embedding_store import ProfileEmbeddingStore
    from src.infra.embedding.providers.real_provider import RealEmbeddingProvider

    # R15-E FIX: Use store from ProviderRegistry if available (runtime mode)
    # QDRANT_SINGLETON_FIX: Use shared _app_context instead of build_application_context()
    content_store = None
    try:
        from src.interfaces.api.dependencies.fusion_dependencies import get_app_context
        app_context = get_app_context()
        if app_context is not None:
            registry = app_context.registry
            if registry.has("worker_profile_content_store"):
                logger.info("[Open-Core] Using Profile Content Store from ProviderRegistry (MySQL)")
            content_store = registry.get("worker_profile_content_store")
            logger.info("[Open-Core] Profile Content Store initialized from ProviderRegistry")
    except Exception as e:
        logger.warning(f"[Open-Core] Failed to get profile content store from ProviderRegistry: {e}")

    # FALLBACK: Create SQLite store (dev/test mode)
    if content_store is None:
        from src.infra.adapters.sqlite_worker_profile_content_store import SQLiteWorkerProfileContentStore
        from src.infra.config.worker_registry_settings import WorkerRegistrySettings
        logger.info("[Open-Core] Initializing SQLite Profile Content Store (fallback)...")
        registry_settings = WorkerRegistrySettings()
        db_path = registry_settings.get_effective_db_path()
        content_store = SQLiteWorkerProfileContentStore(db_path)
        logger.info("[Open-Core] SQLite Profile Content Store initialized")

    # 创建 Vector Indexer（用于删除向量）
    vector_indexer = None
    profile_store = None
    try:
        settings = EmbeddingSettings()
        if settings.is_configured():
            embedding_provider = RealEmbeddingProvider(settings=settings)

            # QDRANT_SINGLETON_FIX: Get shared vector_store from app context to avoid lock conflicts
            # QDRANT_SINGLETON_FIX: Use shared _app_context instead of build_application_context()
            shared_vector_store = None
            try:
                from src.interfaces.api.dependencies.fusion_dependencies import get_app_context
                app_context = get_app_context()
                if app_context is not None and app_context.registry.has("vector_store"):
                    shared_vector_store = app_context.registry.get("vector_store")
                    logger.info("[LOCAL_QDRANT_SINGLETON] component=ProfileEmbeddingStore(worker_content_svc) "
                              f"vector_store_id={id(shared_vector_store)} "
                              f"storage_path={getattr(shared_vector_store, 'path', 'N/A')} "
                              f"source=registry")
                else:
                    logger.warning("[LOCAL_QDRANT_SINGLETON] component=ProfileEmbeddingStore(worker_content_svc) "
                                 "app_context not available or vector_store not in registry")
            except Exception as e:
                logger.error("[LOCAL_QDRANT_SINGLETON] component=ProfileEmbeddingStore(worker_content_svc) "
                           f"failed to get app context: {e}")

            # Open-core always uses local mode (no ZDAS/Database dependency)
            profile_store = ProfileEmbeddingStore(
                dimension=settings.dimension,
                index_type="local",  # Always local for open-core
                db_path=resolve_data_path("data/vector_store.db"),
                database=None,  # No database for open-core
                datasource_name="agentclaw_ds",
                vector_store=shared_vector_store,  # QDRANT_SINGLETON_FIX: Pass shared vector_store
            )
            vector_indexer = ProfileEmbeddingIndexer(
                embedding_provider=embedding_provider,
                profile_store=profile_store,
            )
    except Exception:
        pass  # 向量索引器创建失败不影响主流程

    from src.application.services.worker_profile_content_service import WorkerProfileContentService
    return WorkerProfileContentService(
        content_store,
        vector_indexer=vector_indexer,
        registry_store=_get_registry_store(),
        runtime_state_store=_get_runtime_state_store(),
        profile_store=profile_store,
    )


def get_worker_runtime_state_service() -> WorkerRuntimeStateService:
    """
    获取 WorkerRuntimeStateService 依赖

    用于 online/offline 切换。

    Returns:
        WorkerRuntimeStateService 实例
    """
    return WorkerRuntimeStateService(
        registry_store=_get_registry_store(),
        runtime_state_store=_get_runtime_state_store(),
        audit_log_adapter=get_audit_log_store(),
        index_sync_adapter=_get_index_sync_adapter(),
        vector_store=_get_profile_embedding_store(),
    )


def get_registry_store() -> RegistryStoreProtocol:
    """
    获取 Registry Store 依赖

    用于直接查询 Worker。

    Returns:
        Registry Store 实例
    """
    return _get_registry_store()


def get_bot_cognition_provider() -> BotCognitionProvider | None:
    """
    获取 Bot 认知 Provider 依赖

    用于获取 Bot 认知信息，支持 Profile 经验能力构建。

    部署到 ACP 后自动通过 ACE 登录态鉴权。
    未配置时返回 Stub 实现。

    Returns:
        BotCognitionProvider 实现或 None
    """
    return _get_bot_cognition_provider()


# ============================================================================
# Test Utilities
# ============================================================================

def reset_stores() -> None:
    """
    重置所有存储实例（SQLite only for open-core）

    用于测试清理。
    """
    global _registry_store, _runtime_state_store
    global _profile_binding_store, _audit_log_store, _index_sync_adapter
    global _profile_embedding_store, _bot_cognition_provider

    # SQLite stores need close
    if _registry_store is not None and hasattr(_registry_store, 'close'):
        try:
            _registry_store.close()
        except Exception:
            pass
    if _runtime_state_store is not None and hasattr(_runtime_state_store, 'close'):
        try:
            _runtime_state_store.close()
        except Exception:
            pass
    if _profile_binding_store is not None and hasattr(_profile_binding_store, 'close'):
        try:
            _profile_binding_store.close()
        except Exception:
            pass
    if _audit_log_store is not None and hasattr(_audit_log_store, 'close'):
        try:
            _audit_log_store.close()
        except Exception:
            pass

    _registry_store = None
    _runtime_state_store = None
    _profile_binding_store = None
    _audit_log_store = None
    _index_sync_adapter = None
    _profile_embedding_store = None
    _bot_cognition_provider = None


def use_in_memory_stores() -> None:
    """
    切换到内存存储

    用于测试。
    """
    os.environ["WORKER_REGISTRY_DATABASE_MODE"] = "sqlite"
    os.environ["WORKER_REGISTRY_SQLITE_DB_PATH"] = ":memory:"
    reset_stores()


__all__ = [
    "get_worker_import_service",
    "get_worker_runtime_state_service",
    "get_registry_store",
    "get_audit_log_store",
    "get_worker_profile_content_service",
    "get_bot_cognition_provider",
    "reset_stores",
    "use_in_memory_stores",
]
