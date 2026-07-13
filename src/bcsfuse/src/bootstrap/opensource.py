"""
OpenSource Provider Builder

Builds provider registry for OSS deployments in different modes.
"""
import os
import logging
from typing import Optional
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


def _safe_host(url: str | None) -> str:
    """Extract safe host from URL for logging (no secrets)."""
    if not url:
        return ""
    try:
        parsed = urlparse(url)
        return parsed.netloc or parsed.path.split("/")[0]
    except Exception:
        return "<invalid-url>"


def build_opensource_provider_registry(mode: str = "runtime") -> "ProviderRegistry":
    """Build provider registry for OSS deployment.

    Args:
        mode: Deployment mode. One of:
            - "runtime": Production mode with MySQL + Qdrant + Real HTTP providers
            - "dev": Development mode with SQLite + Faiss + Real HTTP providers
            - "dev_smoke": Dev smoke test mode with SQLite + Faiss + Fake/Noop providers (offline)
            - "test": Test mode with InMemory + Fake/Noop providers

    Returns:
        Configured ProviderRegistry instance.

    Raises:
        ValueError: If mode is not one of "runtime", "dev", "dev_smoke", or "test".
    """
    from src.bootstrap.provider_registry import ProviderRegistry
    from src.infra.public.config.yaml_env_config_provider import YamlEnvConfigProvider
    from src.infra.public.auth.simple_token_auth_provider import SimpleTokenAuthProvider
    from src.infra.public.cache.in_memory_cache_provider import InMemoryCacheProvider
    from src.infra.public.object_storage.local_runtime_object_storage_provider import LocalRuntimeObjectStorageProvider
    from src.infra.public.profile_sources.registry_worker_profile_source import RegistryWorkerProfileSource

    registry = ProviderRegistry()

    # Always register config provider
    config = YamlEnvConfigProvider()
    registry.register("config", config)

    # Always register DRM config provider (public, env-based)
    from src.infra.public.config.drm_config_provider import DrmConfigProvider
    drm_config = DrmConfigProvider(config_provider=config)
    registry.register("drm_config", drm_config)

    # Always register auth provider
    auth = SimpleTokenAuthProvider()
    registry.register("auth", auth)

    # Always register cache provider (InMemory works for all modes)
    cache = InMemoryCacheProvider()
    registry.register("cache_provider", cache)

    # Always register object storage provider
    object_storage = LocalRuntimeObjectStorageProvider()
    registry.register("object_storage_provider", object_storage)

    # Mode-specific providers
    if mode == "runtime":
        _build_runtime_providers(registry, config)
    elif mode == "dev":
        _build_dev_providers(registry, config)
    elif mode == "dev_smoke":
        _build_dev_smoke_providers(registry, config)
    elif mode == "test":
        _build_test_providers(registry, config)
    else:
        raise ValueError(f"Invalid mode: {mode}. Must be one of: runtime, dev, dev_smoke, test")

    # Always register profile source (depends on registry store)
    worker_registry_store = registry.get("worker_registry_store")
    if worker_registry_store:
        profile_source = RegistryWorkerProfileSource(worker_registry_store)
        registry.register("worker_profile_source", profile_source)

    return registry


def _build_runtime_providers(registry: "ProviderRegistry", config: "YamlEnvConfigProvider") -> None:
    """Build runtime providers (MySQL + Qdrant + Real HTTP)."""
    # Import runtime providers
    from src.infra.public.database.mysql_connection_pool import MySQLConnectionPoolProvider
    from src.infra.public.stores.mysql_worker_registry_store import MySQLWorkerRegistryStore
    from src.infra.public.stores.mysql_worker_runtime_state_store import MySQLWorkerRuntimeStateStore
    from src.infra.public.stores.mysql_worker_profile_content_store import MySQLWorkerProfileContentStore
    from src.infra.public.stores.mysql_worker_profile_binding_store import MySQLWorkerProfileBindingStore
    from src.infra.public.stores.mysql_fused_profile_store import MySQLFusedProfileStore
    from src.infra.public.audit.mysql_worker_audit_log_store import MySQLWorkerAuditLogStore
    from src.infra.public.vectorstores.qdrant_local_vector_store import QdrantLocalVectorStore
    from src.infra.public.embedding.real_embedding_provider import RealEmbeddingProvider
    from src.infra.public.reranker.http_reranker import HttpReranker
    from src.infra.public.llm.anthropic_compatible_provider import AnthropicCompatibleProvider
    from src.infra.embedding.config.embedding_settings import EmbeddingSettings
    from src.infra.llm.config.llm_settings import LLMSettings

    # R12-Pool-2 Fix: Create shared MySQL connection pool for thread-safe access
    # Pool size covers G1/G2/G5 concurrent workers + headroom
    mysql_pool = MySQLConnectionPoolProvider(
        host=os.getenv("MYSQL_HOST", "localhost"),
        port=int(os.getenv("MYSQL_PORT", "3306")),
        user=os.getenv("MYSQL_USER", "root"),
        password=os.getenv("MYSQL_PASSWORD", ""),
        database=os.getenv("MYSQL_DATABASE", "bcsfuse"),
        pool_size=15,
    )
    registry.register("mysql_connection_pool", mysql_pool)

    # MySQL stores (inject shared connection pool)
    worker_registry_store = MySQLWorkerRegistryStore(connection_pool=mysql_pool)
    registry.register("worker_registry_store", worker_registry_store)

    worker_runtime_state_store = MySQLWorkerRuntimeStateStore(connection_pool=mysql_pool)
    registry.register("worker_runtime_state_store", worker_runtime_state_store)

    worker_profile_content_store = MySQLWorkerProfileContentStore(connection_pool=mysql_pool)
    registry.register("worker_profile_content_store", worker_profile_content_store)

    # Phase B1 Fix: Register worker_profile_binding_store to ensure same instance across all paths
    # Phase C1: Inject worker_registry_store for denormalized column sync
    worker_profile_binding_store = MySQLWorkerProfileBindingStore(
        connection_pool=mysql_pool,
        worker_registry_store=worker_registry_store,
    )
    registry.register("worker_profile_binding_store", worker_profile_binding_store)

    # R12-Pool-4 Fix: Register MySQLFusedProfileStore with connection pool
    fused_profile_store = MySQLFusedProfileStore(connection_pool=mysql_pool)
    registry.register("fused_profile_store", fused_profile_store)

    worker_audit_log_store = MySQLWorkerAuditLogStore(connection_pool=mysql_pool)
    registry.register("worker_audit_log_store", worker_audit_log_store)

    # Qdrant vector store
    from src.infra.config.data_paths import resolve_data_path
    # Priority: QDRANT_LOCAL_PATH env var > resolve_data_path fallback
    qdrant_path = os.getenv("QDRANT_LOCAL_PATH") or resolve_data_path("data/qdrant_storage")
    vector_store = QdrantLocalVectorStore(
        collection_name=config.get("vector.qdrant.collection", "bcsfuse_profiles"),
        path=qdrant_path,
        dimension=config.get_int("embedding.dimension", 4096),
    )
    registry.register("vector_store", vector_store)

    # QDRANT_SINGLETON_FIX: Log shared vector_store creation for tracking
    logger.info(
        "[LOCAL_QDRANT_SINGLETON] component=Bootstrap "
        f"vector_store_id={id(vector_store)} "
        f"storage_path={vector_store.path} "
        f"collection={vector_store.collection_name} "
        f"source=created dimension={vector_store.dimension}"
    )

    # Real embedding provider
    # Log embedding config resolution for parity diagnosis
    # Priority: env var > config value > default
    embedding_base_url = (
        os.getenv("EMBEDDING_BASE_URL")
        or config.get("embedding.base_url")
        or ""
    )
    embedding_auth_token = (
        os.getenv("EMBEDDING_AUTH_TOKEN")
        or config.get("embedding.auth_token")
    )
    embedding_model = (
        os.getenv("EMBEDDING_MODEL")
        or config.get("embedding.model")
        or "Qwen3-Embedding-8B"
    )
    embedding_dimension = int(
        os.getenv("EMBEDDING_DIMENSION")
        or config.get("embedding.dimension")
        or 4096
    )

    logger.info(
        "[EMBEDDING_CONFIG_RESOLUTION] provider_mode=%s enabled=%s provider_class=%s "
        "base_url_host=%s model=%s dimension=%s env_base_url_present=%s env_model_present=%s "
        "env_token_present=%s env_dimension_present=%s fallback_default_used=%s",
        "runtime",
        True,
        "RealEmbeddingProvider",
        _safe_host(embedding_base_url),
        embedding_model,
        embedding_dimension,
        "YES" if os.getenv("EMBEDDING_BASE_URL") else "NO",
        "YES" if os.getenv("EMBEDDING_MODEL") else "NO",
        "YES" if os.getenv("EMBEDDING_AUTH_TOKEN") else "NO",
        "YES" if os.getenv("EMBEDDING_DIMENSION") else "NO",
        "YES" if (not os.getenv("EMBEDDING_BASE_URL") and not config.get("embedding.base_url")) else "NO",
    )

    embedding_settings = EmbeddingSettings(
        base_url=embedding_base_url,
        auth_token=embedding_auth_token,
        model=embedding_model,
        dimension=embedding_dimension,
    )
    embedding_provider = RealEmbeddingProvider(settings=embedding_settings)
    registry.register("embedding_provider", embedding_provider)

    # HTTP reranker
    reranker_provider = HttpReranker()
    registry.register("reranker_provider", reranker_provider)

    # Anthropic-compatible LLM provider
    llm_settings = LLMSettings(
        base_url=config.get("llm.base_url"),
        auth_token=config.get("llm.auth_token"),
        fast_model=config.get("llm.fast_model", "claude-3-sonnet"),
        reasoning_model=config.get("llm.reasoning_model", "claude-3-opus"),
    )
    llm_provider = AnthropicCompatibleProvider(settings=llm_settings)
    registry.register("llm_provider", llm_provider)


def _build_dev_providers(registry: "ProviderRegistry", config: "YamlEnvConfigProvider") -> None:
    """Build dev providers (SQLite + Faiss + Real HTTP).

    This is the standard dev mode for local development with real services.
    Uses SQLite for persistence and Faiss for vectors, but connects to real
    embedding/LLM/reranker services via HTTP.
    """
    # Import dev providers
    from src.infra.public.stores.sqlite_worker_registry_store import SQLiteWorkerRegistryStore
    from src.infra.public.stores.sqlite_worker_runtime_state_store import SQLiteWorkerRuntimeStateStore
    from src.infra.public.stores.sqlite_worker_profile_content_store import SQLiteWorkerProfileContentStore
    from src.infra.adapters.sqlite_worker_profile_binding_store import SQLiteWorkerProfileBindingStore
    from src.infra.public.audit.sqlite_worker_audit_log_store import SQLiteWorkerAuditLogStore
    from src.infra.public.vectorstores.faiss_sqlite_vector_store import FaissSqliteVectorStore
    from src.infra.public.embedding.real_embedding_provider import RealEmbeddingProvider
    from src.infra.public.reranker.http_reranker import HttpReranker
    from src.infra.public.llm.anthropic_compatible_provider import AnthropicCompatibleProvider
    from src.infra.embedding.config.embedding_settings import EmbeddingSettings
    from src.infra.llm.config.llm_settings import LLMSettings

    # SQLite stores - use env var if set, otherwise use config or default
    db_path = os.getenv("BCSFUSE_DATABASE_SQLITE_PATH") or config.get("database.sqlite.path", "data/bcsfuse.db")

    worker_registry_store = SQLiteWorkerRegistryStore(db_path=db_path)
    registry.register("worker_registry_store", worker_registry_store)

    worker_runtime_state_store = SQLiteWorkerRuntimeStateStore(db_path=db_path)
    registry.register("worker_runtime_state_store", worker_runtime_state_store)

    worker_profile_content_store = SQLiteWorkerProfileContentStore(db_path=db_path)
    registry.register("worker_profile_content_store", worker_profile_content_store)

    # Phase B1 Fix: Register worker_profile_binding_store to ensure same instance across all paths
    worker_profile_binding_store = SQLiteWorkerProfileBindingStore(db_path=db_path)
    registry.register("worker_profile_binding_store", worker_profile_binding_store)

    worker_audit_log_store = SQLiteWorkerAuditLogStore(db_path=db_path)
    registry.register("worker_audit_log_store", worker_audit_log_store)

    # Faiss+SQLite vector store
    faiss_db_path = os.getenv("BCSFUSE_FAISS_SQLITE_PATH") or config.get("vector.faiss.db_path", "data/faiss_index.db")
    vector_store = FaissSqliteVectorStore(
        dimension=config.get_int("embedding.dimension", 4096),
        db_path=faiss_db_path,
    )
    registry.register("vector_store", vector_store)

    # Real embedding provider (HTTP)
    # Log embedding config resolution for parity diagnosis
    # Priority: env var > config value > default
    embedding_base_url = (
        os.getenv("EMBEDDING_BASE_URL")
        or config.get("embedding.base_url")
        or ""
    )
    embedding_auth_token = (
        os.getenv("EMBEDDING_AUTH_TOKEN")
        or config.get("embedding.auth_token")
    )
    embedding_model = (
        os.getenv("EMBEDDING_MODEL")
        or config.get("embedding.model")
        or "Qwen3-Embedding-8B"
    )
    embedding_dimension = int(
        os.getenv("EMBEDDING_DIMENSION")
        or config.get("embedding.dimension")
        or 4096
    )

    logger.info(
        "[EMBEDDING_CONFIG_RESOLUTION] provider_mode=%s enabled=%s provider_class=%s "
        "base_url_host=%s model=%s dimension=%s env_base_url_present=%s env_model_present=%s "
        "env_token_present=%s env_dimension_present=%s fallback_default_used=%s",
        "dev",
        True,
        "RealEmbeddingProvider",
        _safe_host(embedding_base_url),
        embedding_model,
        embedding_dimension,
        "YES" if os.getenv("EMBEDDING_BASE_URL") else "NO",
        "YES" if os.getenv("EMBEDDING_MODEL") else "NO",
        "YES" if os.getenv("EMBEDDING_AUTH_TOKEN") else "NO",
        "YES" if os.getenv("EMBEDDING_DIMENSION") else "NO",
        "YES" if (not os.getenv("EMBEDDING_BASE_URL") and not config.get("embedding.base_url")) else "NO",
    )

    embedding_settings = EmbeddingSettings(
        base_url=embedding_base_url,
        auth_token=embedding_auth_token,
        model=embedding_model,
        dimension=embedding_dimension,
    )
    if embedding_settings.is_configured():
        embedding_provider = RealEmbeddingProvider(settings=embedding_settings)
        registry.register("embedding_provider", embedding_provider)
    else:
        missing = embedding_settings.missing_config()
        logger.warning(
            "Embedding not configured (missing: %s). Falling back to FakeEmbeddingProvider. "
            "Semantic search will return deterministic but meaningless results.",
            ", ".join(missing),
        )
        from src.infra.embedding.providers.fake_provider import FakeEmbeddingProvider
        embedding_provider = FakeEmbeddingProvider(dimension=embedding_dimension)
        registry.register("embedding_provider", embedding_provider)

    # HTTP reranker
    reranker_provider = HttpReranker()
    registry.register("reranker_provider", reranker_provider)

    # Anthropic-compatible LLM provider (HTTP) — fallback to FakeLLMProvider if not configured
    llm_base_url = (
        os.getenv("LLM_BASE_URL")
        or config.get("llm.base_url")
        or ""
    )
    llm_auth_token = (
        os.getenv("LLM_AUTH_TOKEN")
        or config.get("llm.auth_token")
        or ""
    )
    if llm_base_url and llm_auth_token:
        llm_settings = LLMSettings(
            base_url=llm_base_url,
            auth_token=llm_auth_token,
            fast_model=config.get("llm.fast_model", "claude-3-sonnet"),
            reasoning_model=config.get("llm.reasoning_model", "claude-3-opus"),
        )
        llm_provider = AnthropicCompatibleProvider(settings=llm_settings)
        registry.register("llm_provider", llm_provider)
    else:
        logger.warning(
            "LLM not configured (missing: %s). Falling back to FakeLLMProvider. "
            "LLM-dependent features (fusion, question-rewrite, etc.) will use echo responses.",
            ", ".join(filter(None, [
                "LLM_BASE_URL" if not llm_base_url else None,
                "LLM_AUTH_TOKEN" if not llm_auth_token else None,
            ])),
        )
        from src.infra.public.llm.fake_llm_provider import FakeLLMProvider
        llm_provider = FakeLLMProvider()
        registry.register("llm_provider", llm_provider)


def _build_dev_smoke_providers(registry: "ProviderRegistry", config: "YamlEnvConfigProvider") -> None:
    """Build dev smoke providers (SQLite + Faiss + Fake/Noop).

    This mode is for smoke/regression tests that need SQLite persistence
    but should NOT connect to any external HTTP services.

    Uses:
    - SQLite stores for persistent storage
    - FaissSqliteVectorStore for persistent vectors
    - FakeEmbeddingProvider for deterministic embeddings
    - NoopReranker for no-op reranking
    - FakeLLMProvider for echo LLM responses
    """
    # Import dev smoke providers
    from src.infra.public.stores.sqlite_worker_registry_store import SQLiteWorkerRegistryStore
    from src.infra.public.stores.sqlite_worker_runtime_state_store import SQLiteWorkerRuntimeStateStore
    from src.infra.public.stores.sqlite_worker_profile_content_store import SQLiteWorkerProfileContentStore
    from src.infra.adapters.sqlite_worker_profile_binding_store import SQLiteWorkerProfileBindingStore
    from src.infra.public.audit.sqlite_worker_audit_log_store import SQLiteWorkerAuditLogStore
    from src.infra.public.vectorstores.faiss_sqlite_vector_store import FaissSqliteVectorStore
    from src.infra.public.embedding.fake_embedding_provider import FakeEmbeddingProvider
    from src.infra.public.reranker.noop_reranker import NoopReranker
    from src.infra.public.llm.fake_llm_provider import FakeLLMProvider

    # SQLite stores - use env var if set, otherwise use config or default
    db_path = os.getenv("BCSFUSE_DATABASE_SQLITE_PATH") or config.get("database.sqlite.path", "data/bcsfuse.db")

    worker_registry_store = SQLiteWorkerRegistryStore(db_path=db_path)
    registry.register("worker_registry_store", worker_registry_store)

    worker_runtime_state_store = SQLiteWorkerRuntimeStateStore(db_path=db_path)
    registry.register("worker_runtime_state_store", worker_runtime_state_store)

    worker_profile_content_store = SQLiteWorkerProfileContentStore(db_path=db_path)
    registry.register("worker_profile_content_store", worker_profile_content_store)

    # Phase B1 Fix: Register worker_profile_binding_store to ensure same instance across all paths
    worker_profile_binding_store = SQLiteWorkerProfileBindingStore(db_path=db_path)
    registry.register("worker_profile_binding_store", worker_profile_binding_store)

    worker_audit_log_store = SQLiteWorkerAuditLogStore(db_path=db_path)
    registry.register("worker_audit_log_store", worker_audit_log_store)

    # Faiss+SQLite vector store
    faiss_db_path = os.getenv("BCSFUSE_FAISS_SQLITE_PATH") or config.get("vector.faiss.db_path", "data/faiss_index.db")
    vector_store = FaissSqliteVectorStore(
        dimension=config.get_int("embedding.dimension", 4096),
        db_path=faiss_db_path,
    )
    registry.register("vector_store", vector_store)

    # Fake embedding provider (deterministic, no HTTP)
    embedding_provider = FakeEmbeddingProvider(dimension=config.get_int("embedding.dimension", 4096))
    registry.register("embedding_provider", embedding_provider)

    # Noop reranker
    reranker_provider = NoopReranker()
    registry.register("reranker_provider", reranker_provider)

    # Fake LLM provider
    llm_provider = FakeLLMProvider()
    registry.register("llm_provider", llm_provider)


def _build_test_providers(registry: "ProviderRegistry", config: "YamlEnvConfigProvider") -> None:
    """Build test providers (InMemory + Fake/Noop)."""
    # Import test providers
    from src.infra.public.stores.in_memory_worker_registry_store import InMemoryWorkerRegistryStore
    from src.infra.public.stores.in_memory_worker_runtime_state_store import InMemoryWorkerRuntimeStateStore
    from src.infra.public.stores.in_memory_worker_profile_content_store import InMemoryWorkerProfileContentStore
    from src.infra.adapters.in_memory_worker_profile_binding_store import InMemoryWorkerProfileBindingStore
    from src.infra.public.audit.in_memory_worker_audit_log_store import InMemoryWorkerAuditLogStore
    from src.infra.public.vectorstores.in_memory_vector_store import InMemoryVectorStore
    from src.infra.public.embedding.fake_embedding_provider import FakeEmbeddingProvider
    from src.infra.public.reranker.noop_reranker import NoopReranker
    from src.infra.public.llm.fake_llm_provider import FakeLLMProvider
    from src.infra.public.profile_sources.mock_worker_profile_source import MockWorkerProfileSource

    # InMemory stores
    worker_registry_store = InMemoryWorkerRegistryStore()
    registry.register("worker_registry_store", worker_registry_store)

    worker_runtime_state_store = InMemoryWorkerRuntimeStateStore()
    registry.register("worker_runtime_state_store", worker_runtime_state_store)

    worker_profile_content_store = InMemoryWorkerProfileContentStore()
    registry.register("worker_profile_content_store", worker_profile_content_store)

    # Phase B1 Fix: Register worker_profile_binding_store to ensure same instance across all paths
    worker_profile_binding_store = InMemoryWorkerProfileBindingStore()
    registry.register("worker_profile_binding_store", worker_profile_binding_store)

    worker_audit_log_store = InMemoryWorkerAuditLogStore()
    registry.register("worker_audit_log_store", worker_audit_log_store)

    # InMemory vector store
    vector_store = InMemoryVectorStore(dimension=config.get_int("embedding.dimension", 4096))
    registry.register("vector_store", vector_store)

    # Fake embedding provider
    embedding_provider = FakeEmbeddingProvider(dimension=config.get_int("embedding.dimension", 4096))
    registry.register("embedding_provider", embedding_provider)

    # Noop reranker
    reranker_provider = NoopReranker()
    registry.register("reranker_provider", reranker_provider)

    # Fake LLM provider
    llm_provider = FakeLLMProvider()
    registry.register("llm_provider", llm_provider)

    # Mock profile source (override registry-based one for tests)
    profile_source = MockWorkerProfileSource()
    registry.register("worker_profile_source", profile_source)