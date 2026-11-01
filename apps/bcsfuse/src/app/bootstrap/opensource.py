"""
OpenSource Provider Builder

Builds provider registry for OSS deployments in different modes.
"""
import logging
import os
from dataclasses import dataclass
from typing import TYPE_CHECKING
from urllib.parse import urlparse

from search_core.config.qdrant import QdrantConnection

from app.bootstrap.embedding_defaults import resolve_embedding_runtime_defaults

if TYPE_CHECKING:
    from app.bootstrap.provider_registry import ProviderRegistry
    from app.infra.public.config.yaml_env_config_provider import (
        YamlEnvConfigProvider,
    )
    from app.infra.public.database.mysql_connection_pool import (
        MySQLConnectionPoolProvider,
    )

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


def _literal_fallback_snapshot_store(config, *, enabled: bool):
    """Resolve the optional owner-published payload-only fallback snapshot."""

    if not enabled or not config.get_bool(
        "vector.literal_fallback.snapshot_enabled", False
    ):
        return None

    from app.infra.public.stores.literal_fallback_path_policy import (
        EphemeralSnapshotPathError,
        LiteralFallbackSnapshotPathPolicy,
    )
    from app.infra.public.stores.literal_fallback_snapshot_store import (
        LiteralFallbackSnapshotFileStore,
    )

    explicit_path = os.getenv("BCSFUSE_LITERAL_FALLBACK_SNAPSHOT_PATH")
    if explicit_path is None:
        raise EphemeralSnapshotPathError(
            "runtime literal fallback snapshot requires an explicit persistent path: "
            "set BCSFUSE_LITERAL_FALLBACK_SNAPSHOT_PATH"
        )
    resolved_path = LiteralFallbackSnapshotPathPolicy().validate_runtime_path(
        explicit_path
    )
    return LiteralFallbackSnapshotFileStore(resolved_path)


def _with_literal_fallback(vector_store, snapshot_store=None):
    """Register a vector store that also maintains the final local snapshot."""
    from app.application.services.literal_fallback_maintenance import (
        wrap_vector_store_with_literal_fallback,
    )

    return wrap_vector_store_with_literal_fallback(
        vector_store,
        snapshot_store=snapshot_store,
    )


def _config_int(config: object, key: str, default: int) -> int:
    """Read an optional composition setting while retaining typed defaults."""
    method = getattr(config, "get_int", None)
    if method is not None:
        return int(method(key, default))
    try:
        return int(config.get(key, default))  # type: ignore[attr-defined]
    except AttributeError:
        return default


def _env_bool(name: str) -> bool | None:
    value = os.getenv(name)
    if value is None:
        return None
    return value.strip().lower() in ("1", "true", "yes", "on")


def _config_bool(config: object, key: str, default: bool) -> bool:
    env_value = os.getenv(key.upper())
    if env_value is not None:
        return bool(_env_bool(key.upper()))
    method = getattr(config, "get_bool", None)
    if method is not None:
        return bool(method(key, default))
    try:
        return bool(config.get(key, default))  # type: ignore[attr-defined]
    except AttributeError:
        return default


def _config_float(config: object, key: str, default: float) -> float:
    method = getattr(config, "get_float", None)
    if method is not None:
        return float(method(key, default))
    try:
        return float(config.get(key, default))  # type: ignore[attr-defined]
    except AttributeError:
        return default


def _index_owner_id() -> str:
    import socket

    owner_id = os.getenv("BCSFUSE_INDEX_OWNER_ID")
    if owner_id:
        return owner_id
    return f"{socket.gethostname()}:{os.getpid()}"


def register_runtime_service_providers(
    registry: "ProviderRegistry", config: "YamlEnvConfigProvider"
) -> None:
    """Register real public HTTP providers without selecting storage.

    Internal compositions use this seam to keep Avernet-owned embedding, LLM,
    and reranker behavior while injecting their own durable stores first.
    """
    from app.infra.embedding.config.embedding_settings import EmbeddingSettings
    from app.infra.llm.config.llm_settings import LLMSettings
    from app.infra.public.embedding.real_embedding_provider import (
        RealEmbeddingProvider,
    )
    from app.infra.public.llm.anthropic_compatible_provider import (
        AnthropicCompatibleProvider,
    )
    from app.infra.public.reranker.http_reranker import HttpReranker

    if not _config_bool(config, "embedding.cache.enabled", True):
        raise RuntimeError(
            "embedding.cache.enabled must be true for runtime serving; "
            "the query cache is a required capacity contract"
        )

    embedding_base_url = (
        os.getenv("EMBEDDING_BASE_URL")
        or config.get("embedding.base_url")
        or ""
    )
    embedding_defaults = resolve_embedding_runtime_defaults(config, os.environ)
    embedding_model = embedding_defaults.model
    embedding_dimension = embedding_defaults.dimension
    embedding_request_dimensions = embedding_defaults.request_dimensions

    logger.info(
        "[EMBEDDING_CONFIG_RESOLUTION] provider_mode=%s enabled=%s provider_class=%s "
        "base_url_host=%s model=%s dimension=%s request_dimensions=%s env_base_url_present=%s env_model_present=%s "
        "env_token_present=%s env_dimension_present=%s fallback_default_used=%s",
        "runtime",
        True,
        "RealEmbeddingProvider",
        _safe_host(embedding_base_url),
        embedding_model,
        embedding_dimension,
        embedding_request_dimensions,
        "YES" if os.getenv("EMBEDDING_BASE_URL") else "NO",
        "YES" if os.getenv("EMBEDDING_MODEL") else "NO",
        "YES" if os.getenv("EMBEDDING_AUTH_TOKEN") else "NO",
        "YES" if os.getenv("EMBEDDING_DIMENSION") else "NO",
        "YES"
        if not os.getenv("EMBEDDING_BASE_URL")
        and not config.get("embedding.base_url")
        else "NO",
    )

    from app.infra.public.embedding.cached_embedding_provider import (
        CachedEmbeddingProvider,
    )
    from app.infra.public.embedding.circuit_breaker_provider import (
        CircuitBreakerEmbeddingProvider,
    )

    backend_embedding_provider = RealEmbeddingProvider(
        settings=EmbeddingSettings(
            base_url=embedding_base_url,
            auth_token=os.getenv("EMBEDDING_AUTH_TOKEN")
            or config.get("embedding.auth_token"),
            model=embedding_model,
            dimension=embedding_dimension,
            request_dimensions=embedding_request_dimensions,
        )
    )
    circuit_embedding_provider = CircuitBreakerEmbeddingProvider(
        backend_embedding_provider,
        failure_threshold=_config_int(
            config, "embedding.circuit.failure_threshold", 3
        ),
        recovery_timeout_seconds=_config_float(
            config, "embedding.circuit.recovery_seconds", 10.0
        ),
        capacity_store=registry.get("embedding_capacity_store"),
        quota_recovery_seconds=_config_float(
            config, "embedding.quota.recovery_seconds", 60.0
        ),
        max_calls_per_minute=_config_int(
            config, "embedding.quota.max_calls_per_minute", 30
        ),
        quota_period_seconds=_config_float(
            config, "embedding.quota.period_seconds", 60.0
        ),
    )
    registry.register(
        "embedding_provider",
        CachedEmbeddingProvider(
            circuit_embedding_provider,
            max_entries=_config_int(config, "embedding.cache.max_entries", 10000),
            ttl_seconds=_config_float(config, "embedding.cache.ttl_seconds", 300.0),
            max_query_batch_size=_config_int(
                config, "embedding.cache.max_query_batch_size", 32
            ),
            query_batch_wait_seconds=_config_float(
                config, "embedding.cache.query_batch_wait_seconds", 0.05
            ),
            query_batch_peak_wait_seconds=_config_float(
                config,
                "embedding.cache.query_batch_peak_wait_seconds",
                0.15,
            ),
            query_batch_peak_threshold=_config_int(
                config, "embedding.cache.query_batch_peak_threshold", 8
            ),
            max_query_batch_workers=_config_int(
                config, "embedding.cache.max_query_batch_workers", 1
            ),
        ),
    )
    from app.infra.public.reranker.http_reranker import get_reranker

    registry.register("reranker_provider", get_reranker())
    registry.register(
        "llm_provider",
        AnthropicCompatibleProvider(
            settings=LLMSettings(
                base_url=os.getenv("LLM_BASE_URL") or config.get("llm.base_url"),
                auth_token=os.getenv("LLM_AUTH_TOKEN")
                or config.get("llm.auth_token"),
                fast_model=(
                    os.getenv("LLM_FAST_MODEL")
                    or config.get("llm.fast_model")
                    or "claude-3-sonnet"
                ),
                reasoning_model=(
                    os.getenv("LLM_REASONING_MODEL")
                    or config.get("llm.reasoning_model")
                    or "claude-3-opus"
                ),
            )
        ),
    )


@dataclass(frozen=True)
class IndexLeaseSettings:
    """Bounded owner/staleness windows selected by composition."""

    stale_after_seconds: float
    lease_seconds: float
    heartbeat_seconds: float

    def __post_init__(self) -> None:
        if self.stale_after_seconds <= 0:
            raise ValueError("stale_after_seconds must be positive")
        if self.lease_seconds <= self.stale_after_seconds:
            raise ValueError("lease_seconds must exceed stale_after_seconds")


def resolve_index_lease_settings(config) -> IndexLeaseSettings:
    # A short lease bounds worst-case owner takeover. Longer leases remain an
    # explicit deployment choice, but the public default fails over quickly.
    stale_after_seconds = config.get_float(
        "vector.index.stale_after_seconds", 20.0
    )
    lease_seconds = max(
        stale_after_seconds + 1.0,
        config.get_float("vector.index.lease_seconds", 30.0),
    )
    return IndexLeaseSettings(
        stale_after_seconds=stale_after_seconds,
        lease_seconds=lease_seconds,
        heartbeat_seconds=lease_seconds / 3.0,
    )

def build_opensource_provider_registry(mode: str = "runtime") -> "ProviderRegistry":
    """Build provider registry for OSS deployment.

    Args:
        mode: Deployment mode. One of:
            - "runtime": Production mode with MySQL + Qdrant + Real HTTP providers
            - "dev": Development mode with SQLite + Qdrant Local + Real HTTP providers
            - "dev_smoke": Dev smoke test mode with SQLite + Qdrant Local + Fake/Noop providers (offline)
            - "test": Test mode with InMemory + Fake/Noop providers

    Returns:
        Configured ProviderRegistry instance.

    Raises:
        ValueError: If mode is not one of "runtime", "dev", "dev_smoke", or "test".
    """
    from app.bootstrap.provider_registry import ProviderRegistry
    from app.infra.public.config.yaml_env_config_provider import YamlEnvConfigProvider
    from app.infra.public.auth.simple_token_auth_provider import SimpleTokenAuthProvider
    from app.infra.public.cache.in_memory_cache_provider import InMemoryCacheProvider
    from app.infra.public.object_storage.local_runtime_object_storage_provider import LocalRuntimeObjectStorageProvider
    from app.infra.public.profile_sources.registry_worker_profile_source import RegistryWorkerProfileSource
    from app.providers.public.env_secret_provider import EnvSecretProvider
    from app.providers.public.noop_context_provider import NoopContextProvider
    from app.providers.public.noop_startup_provider import NoopStartupProvider

    registry = ProviderRegistry()

    # Always register config provider
    config = YamlEnvConfigProvider()
    registry.register("config", config)

    registry.register("secret_provider", EnvSecretProvider())
    registry.register("startup_provider", NoopStartupProvider())
    registry.register("context_provider", NoopContextProvider())

    # Always register DRM config provider (public, env-based)
    from app.infra.public.config.drm_config_provider import DrmConfigProvider
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


def create_runtime_mysql_pool() -> "MySQLConnectionPoolProvider":
    """Create the runtime shared MySQL pool with bounded, scalable sizing."""

    from app.infra.public.database.mysql_connection_pool import (
        MySQLConnectionPoolProvider,
    )

    return MySQLConnectionPoolProvider(
        host=os.getenv("MYSQL_HOST", "localhost"),
        port=int(os.getenv("MYSQL_PORT", "3306")),
        user=os.getenv("MYSQL_USER", ""),
        password=os.getenv("MYSQL_PASSWORD", ""),
        database=os.getenv("MYSQL_DATABASE", "bcsfuse"),
    )


def _build_runtime_providers(registry: "ProviderRegistry", config: "YamlEnvConfigProvider") -> None:
    """Build runtime providers (MySQL + Qdrant + Real HTTP)."""
    # Import runtime providers
    from app.infra.public.database.mysql_connection_pool import MySQLConnectionPoolProvider
    from app.infra.public.stores.mysql_worker_registry_store import MySQLWorkerRegistryStore
    from app.infra.public.stores.mysql_worker_runtime_state_store import MySQLWorkerRuntimeStateStore
    from app.infra.public.stores.mysql_worker_profile_content_store import MySQLWorkerProfileContentStore
    from app.infra.public.stores.mysql_worker_profile_binding_store import MySQLWorkerProfileBindingStore
    from app.infra.public.stores.mysql_fused_profile_store import MySQLFusedProfileStore
    from app.infra.public.audit.mysql_worker_audit_log_store import MySQLWorkerAuditLogStore
    from app.infra.public.stores.mysql_shared_response_cache_store import (
        MySQLSharedResponseCacheStore,
    )
    from app.infra.public.vectorstores.qdrant_mysql_vector_store import QdrantMySQLVectorStore

    # R12-Pool-2 Fix: Create one shared, bounded pool per pod. MYSQL_POOL_SIZE
    # defaults to 8 so horizontal Pod scaling does not immediately exhaust the
    # MySQL server's global connection quota.
    mysql_pool = create_runtime_mysql_pool()
    registry.register("mysql_connection_pool", mysql_pool)

    # Shared L2 final-response cache. All runtime pods use this provider and
    # durable index-bound cache keys, while each pod keeps its L1 cache local.
    shared_response_cache = MySQLSharedResponseCacheStore(
        connection_pool=mysql_pool
    )
    registry.register("shared_response_cache", shared_response_cache)

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

    # Qdrant + MySQL durable vector store
    # Qdrant is the rebuildable index; MySQL is the durable source of truth.
    embedding_defaults = resolve_embedding_runtime_defaults(config, os.environ)
    from app.infra.config.data_paths import resolve_data_path
    qdrant_path = os.getenv("QDRANT_LOCAL_PATH") or resolve_data_path("data/qdrant_storage")
    qdrant_connection = QdrantConnection.from_mapping(
        os.environ, default_mode="server"
    )
    vector_store = QdrantMySQLVectorStore(
        connection=qdrant_connection,
        keyword_connection=QdrantConnection.keyword_from_mapping(
            os.environ, fallback=qdrant_connection
        ),
        collection_name=config.get("vector.qdrant.collection", "bcsfuse_profiles"),
        qdrant_path=qdrant_path,
        dimension=embedding_defaults.dimension,
        distance=config.get("vector.qdrant.distance", "Cosine"),
        mysql_connection_pool=mysql_pool,
    )
    from app.application.services.index_state_vector_store import (
        wrap_vector_store_with_index_state,
    )
    from app.infra.public.stores.mysql_index_state_store import MySQLIndexStateStore

    collection_name = config.get("vector.qdrant.collection", "bcsfuse_profiles")
    lease_settings = resolve_index_lease_settings(config)
    embedding_model = embedding_defaults.model
    embedding_dimension = embedding_defaults.dimension
    index_state_store = MySQLIndexStateStore(connection_pool=mysql_pool)
    vector_store = wrap_vector_store_with_index_state(
        vector_store,
        store=index_state_store,
        owner_id=_index_owner_id(),
        collection_pair=f"{collection_name}:dense+keyword",
        stale_after_seconds=lease_settings.stale_after_seconds,
        lease_seconds=lease_settings.lease_seconds,
        embedding_model=embedding_model,
        embedding_dimension=embedding_dimension,
        embedding_index_signature_store=index_state_store,
    )
    vector_store = _with_literal_fallback(
        vector_store,
        snapshot_store=_literal_fallback_snapshot_store(config, enabled=True),
    )
    registry.register("vector_store", vector_store)

    # Rebuild only after startup dependencies initialize. A failed rebuild
    # prevents readiness instead of serving an empty index.
    from app.providers.public.vector_index_startup_provider import (
        VectorIndexStartupProvider,
    )

    registry.register(
        "startup_provider",
        VectorIndexStartupProvider(
            registry.require("startup_provider"),
            vector_store,
        ),
    )

    logger.info(
        "[QdrantMySQLVectorStore] component=Bootstrap "
        f"vector_store_id={id(vector_store)} "
        f"qdrant_path={qdrant_path} "
        f"collection={vector_store.collection_name} "
        f"source=created dimension={vector_store.dimension}"
    )

    from app.infra.public.stores.mysql_embedding_capacity_store import (
        MySQLEmbeddingCapacityStore,
    )

    registry.register(
        "embedding_capacity_store",
        MySQLEmbeddingCapacityStore(connection_pool=mysql_pool),
    )

    register_runtime_service_providers(registry, config)


def _build_dev_providers(registry: "ProviderRegistry", config: "YamlEnvConfigProvider") -> None:
    """Build dev providers (SQLite + Qdrant Local + Real HTTP).

    This is the standard dev mode for local development with real services.
    Uses SQLite for persistence and Qdrant Local for vectors, but connects to real
    embedding/LLM/reranker services via HTTP.
    """
    # Import dev providers
    from app.infra.public.stores.sqlite_worker_registry_store import SQLiteWorkerRegistryStore
    from app.infra.public.stores.sqlite_worker_runtime_state_store import SQLiteWorkerRuntimeStateStore
    from app.infra.public.stores.sqlite_worker_profile_content_store import SQLiteWorkerProfileContentStore
    from app.infra.adapters.sqlite_worker_profile_binding_store import SQLiteWorkerProfileBindingStore
    from app.infra.public.audit.sqlite_worker_audit_log_store import SQLiteWorkerAuditLogStore
    from app.infra.public.vectorstores.qdrant_sqlite_vector_store import QdrantSQLiteVectorStore
    from app.infra.public.embedding.real_embedding_provider import RealEmbeddingProvider
    from app.infra.public.reranker.http_reranker import HttpReranker
    from app.infra.public.llm.anthropic_compatible_provider import AnthropicCompatibleProvider
    from app.infra.embedding.config.embedding_settings import EmbeddingSettings
    from app.infra.llm.config.llm_settings import LLMSettings

    embedding_defaults = resolve_embedding_runtime_defaults(config, os.environ)

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

    # Qdrant Local + SQLite vector store
    vector_db_path = os.getenv("BCSFUSE_VECTOR_SQLITE_PATH") or config.get("vector.sqlite.db_path", "data/vector_embeddings.db")
    qdrant_connection = QdrantConnection.from_mapping(os.environ)
    dev_collection_name = config.get(
        "vector.qdrant.collection", "bcsfuse_vectors"
    )
    vector_store = QdrantSQLiteVectorStore(
        connection=qdrant_connection,
        keyword_connection=QdrantConnection.keyword_from_mapping(
            os.environ, fallback=qdrant_connection
        ),
        collection_name=dev_collection_name,
        dimension=embedding_defaults.dimension,
        db_path=vector_db_path,
        qdrant_path=os.getenv("QDRANT_LOCAL_PATH") or config.get("vector.qdrant.path"),
        auto_load=False,
    )
    from pathlib import Path

    from app.application.services.index_state_vector_store import (
        wrap_vector_store_with_index_state,
    )
    from app.infra.public.stores.sqlite_index_state_store import (
        SQLiteIndexStateStore,
    )

    vector_path = Path(vector_db_path)
    default_index_state_path = vector_path.with_name(
        f"{vector_path.stem}-index-state{vector_path.suffix}"
    )
    index_state_path = (
        config.get("vector.index.sqlite.path", str(default_index_state_path))
    )
    collection_pair_name = dev_collection_name
    dev_lease_settings = resolve_index_lease_settings(config)
    embedding_model = embedding_defaults.model
    embedding_dimension = embedding_defaults.dimension
    index_state_store = SQLiteIndexStateStore(index_state_path)
    vector_store = wrap_vector_store_with_index_state(
        vector_store,
        store=index_state_store,
        owner_id=_index_owner_id(),
        collection_pair=f"{collection_pair_name}:dense+keyword",
        stale_after_seconds=dev_lease_settings.stale_after_seconds,
        lease_seconds=dev_lease_settings.lease_seconds,
        embedding_model=embedding_model,
        embedding_dimension=embedding_dimension,
        embedding_index_signature_store=index_state_store,
    )
    vector_store = _with_literal_fallback(
        vector_store,
        snapshot_store=_literal_fallback_snapshot_store(config, enabled=False),
    )
    registry.register("vector_store", vector_store)
    from app.providers.public.vector_index_startup_provider import VectorIndexStartupProvider
    registry.register("startup_provider", VectorIndexStartupProvider(
        registry.require("startup_provider"), vector_store,
    ))

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
    embedding_request_dimensions = embedding_defaults.request_dimensions

    from app.infra.public.stores.sqlite_embedding_capacity_store import (
        SQLiteEmbeddingCapacityStore,
    )

    capacity_db_path = os.getenv("BCSFUSE_CAPACITY_SQLITE_PATH") or config.get(
        "database.sqlite.capacity_path",
        str(Path(vector_db_path).with_name(
            f"{Path(vector_db_path).stem}-embedding-capacity{Path(vector_db_path).suffix}"
        )),
    )
    registry.register(
        "embedding_capacity_store",
        SQLiteEmbeddingCapacityStore(capacity_db_path),
    )

    logger.info(
        "[EMBEDDING_CONFIG_RESOLUTION] provider_mode=%s enabled=%s provider_class=%s "
        "base_url_host=%s model=%s dimension=%s request_dimensions=%s env_base_url_present=%s env_model_present=%s "
        "env_token_present=%s env_dimension_present=%s fallback_default_used=%s",
        "dev",
        True,
        "RealEmbeddingProvider",
        _safe_host(embedding_base_url),
        embedding_model,
        embedding_dimension,
        embedding_request_dimensions,
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
        request_dimensions=embedding_request_dimensions,
    )
    if embedding_settings.is_configured():
        from app.infra.public.embedding.cached_embedding_provider import (
            CachedEmbeddingProvider,
        )

        from app.infra.public.embedding.circuit_breaker_provider import (
            CircuitBreakerEmbeddingProvider,
        )

        embedding_provider = CachedEmbeddingProvider(
            CircuitBreakerEmbeddingProvider(
                RealEmbeddingProvider(settings=embedding_settings),
                failure_threshold=_config_int(
                    config, "embedding.circuit.failure_threshold", 3
                ),
                recovery_timeout_seconds=_config_float(
                    config, "embedding.circuit.recovery_seconds", 10.0
                ),
                capacity_store=registry.get("embedding_capacity_store"),
                quota_recovery_seconds=_config_float(
                    config, "embedding.quota.recovery_seconds", 60.0
                ),
                max_calls_per_minute=_config_int(
                    config, "embedding.quota.max_calls_per_minute", 30
                ),
                quota_period_seconds=_config_float(
                    config, "embedding.quota.period_seconds", 60.0
                ),
            ),
            max_entries=_config_int(config, "embedding.cache.max_entries", 10000),
            ttl_seconds=_config_float(config, "embedding.cache.ttl_seconds", 300.0),
            max_query_batch_size=_config_int(
                config, "embedding.cache.max_query_batch_size", 32
            ),
            query_batch_wait_seconds=_config_float(
                config, "embedding.cache.query_batch_wait_seconds", 0.05
            ),
            query_batch_peak_wait_seconds=_config_float(
                config,
                "embedding.cache.query_batch_peak_wait_seconds",
                0.15,
            ),
            query_batch_peak_threshold=_config_int(
                config, "embedding.cache.query_batch_peak_threshold", 8
            ),
            max_query_batch_workers=_config_int(
                config, "embedding.cache.max_query_batch_workers", 1
            ),
        )
        registry.register("embedding_provider", embedding_provider)
    else:
        missing = embedding_settings.missing_config()
        logger.warning(
            "Embedding not configured (missing: %s). Falling back to FakeEmbeddingProvider. "
            "Semantic search will return deterministic but meaningless results.",
            ", ".join(missing),
        )
        from app.infra.embedding.providers.fake_provider import FakeEmbeddingProvider
        embedding_provider = FakeEmbeddingProvider(dimension=embedding_dimension)
        registry.register("embedding_provider", embedding_provider)

    # HTTP reranker
    from app.infra.reranker import get_circuit_breaker_reranker

    reranker_provider = get_circuit_breaker_reranker()
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
    llm_api_type = (
        os.getenv("LLM_API_TYPE")
        or config.get("llm.api_type")
        or "anthropic"
    ).lower()
    if llm_base_url and llm_auth_token:
        llm_settings = LLMSettings(
            base_url=llm_base_url,
            auth_token=llm_auth_token,
            fast_model=config.get("llm.fast_model", "claude-3-sonnet"),
            reasoning_model=config.get("llm.reasoning_model", "claude-3-opus"),
        )
        if llm_api_type in ("openai", "openai-completions"):
            from app.infra.public.llm.openai_compatible_provider import OpenAICompatibleProvider
            llm_provider = OpenAICompatibleProvider(settings=llm_settings)
            logger.info(f"[OSS LLM] Using OpenAI-compatible provider: {llm_base_url}")
        else:
            llm_provider = AnthropicCompatibleProvider(settings=llm_settings)
            logger.info(f"[OSS LLM] Using Anthropic-compatible provider: {llm_base_url}")
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
        from app.infra.public.llm.fake_llm_provider import FakeLLMProvider
        llm_provider = FakeLLMProvider()
        registry.register("llm_provider", llm_provider)


def _build_dev_smoke_providers(registry: "ProviderRegistry", config: "YamlEnvConfigProvider") -> None:
    """Build dev smoke providers (SQLite + Qdrant Local + Fake/Noop).

    This mode is for smoke/regression tests that need SQLite persistence
    but should NOT connect to any external HTTP services.

    Uses:
    - SQLite stores for persistent storage
    - QdrantSQLiteVectorStore for persistent vectors
    - FakeEmbeddingProvider for deterministic embeddings
    - NoopReranker for no-op reranking
    - FakeLLMProvider for echo LLM responses
    """
    # Import dev smoke providers
    from app.infra.public.stores.sqlite_worker_registry_store import SQLiteWorkerRegistryStore
    from app.infra.public.stores.sqlite_worker_runtime_state_store import SQLiteWorkerRuntimeStateStore
    from app.infra.public.stores.sqlite_worker_profile_content_store import SQLiteWorkerProfileContentStore
    from app.infra.adapters.sqlite_worker_profile_binding_store import SQLiteWorkerProfileBindingStore
    from app.infra.public.audit.sqlite_worker_audit_log_store import SQLiteWorkerAuditLogStore
    from app.infra.public.vectorstores.qdrant_sqlite_vector_store import QdrantSQLiteVectorStore
    from app.infra.public.embedding.fake_embedding_provider import FakeEmbeddingProvider
    from app.infra.public.reranker.noop_reranker import NoopReranker
    from app.infra.public.llm.fake_llm_provider import FakeLLMProvider

    embedding_defaults = resolve_embedding_runtime_defaults(config, os.environ)

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

    # Qdrant Local + SQLite vector store
    vector_db_path = os.getenv("BCSFUSE_VECTOR_SQLITE_PATH") or config.get("vector.sqlite.db_path", "data/vector_embeddings.db")
    qdrant_connection = QdrantConnection.from_mapping(os.environ)
    vector_store = QdrantSQLiteVectorStore(
        connection=qdrant_connection,
        keyword_connection=QdrantConnection.keyword_from_mapping(
            os.environ, fallback=qdrant_connection
        ),
        dimension=embedding_defaults.dimension,
        db_path=vector_db_path,
        qdrant_path=os.getenv("QDRANT_LOCAL_PATH") or config.get("vector.qdrant.path"),
        auto_load=False,
    )
    vector_store = _with_literal_fallback(
        vector_store,
        snapshot_store=_literal_fallback_snapshot_store(config, enabled=False),
    )
    registry.register("vector_store", vector_store)
    from app.providers.public.vector_index_startup_provider import VectorIndexStartupProvider
    registry.register("startup_provider", VectorIndexStartupProvider(
        registry.require("startup_provider"), vector_store,
    ))

    # Fake embedding provider (deterministic, no HTTP)
    embedding_provider = FakeEmbeddingProvider(dimension=embedding_defaults.dimension)
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
    from app.infra.public.stores.in_memory_worker_registry_store import InMemoryWorkerRegistryStore
    from app.infra.public.stores.in_memory_worker_runtime_state_store import InMemoryWorkerRuntimeStateStore
    from app.infra.public.stores.in_memory_worker_profile_content_store import InMemoryWorkerProfileContentStore
    from app.infra.adapters.in_memory_worker_profile_binding_store import InMemoryWorkerProfileBindingStore
    from app.infra.public.audit.in_memory_worker_audit_log_store import InMemoryWorkerAuditLogStore
    from app.infra.public.vectorstores.in_memory_vector_store import InMemoryVectorStore
    from app.infra.public.embedding.fake_embedding_provider import FakeEmbeddingProvider
    from app.infra.public.reranker.noop_reranker import NoopReranker
    from app.infra.public.llm.fake_llm_provider import FakeLLMProvider
    from app.infra.public.profile_sources.mock_worker_profile_source import MockWorkerProfileSource

    embedding_defaults = resolve_embedding_runtime_defaults(config, os.environ)

    # InMemory stores
    worker_runtime_state_store = InMemoryWorkerRuntimeStateStore()
    registry.register("worker_runtime_state_store", worker_runtime_state_store)

    worker_profile_content_store = InMemoryWorkerProfileContentStore()
    registry.register("worker_profile_content_store", worker_profile_content_store)

    # Phase B1 Fix: Register worker_profile_binding_store to ensure same instance across all paths
    worker_profile_binding_store = InMemoryWorkerProfileBindingStore()
    registry.register("worker_profile_binding_store", worker_profile_binding_store)

    worker_registry_store = InMemoryWorkerRegistryStore(
        profile_store=worker_profile_content_store,
        binding_store=worker_profile_binding_store,
    )
    registry.register("worker_registry_store", worker_registry_store)

    worker_audit_log_store = InMemoryWorkerAuditLogStore()
    registry.register("worker_audit_log_store", worker_audit_log_store)

    # InMemory vector store
    vector_store = InMemoryVectorStore(dimension=embedding_defaults.dimension)
    vector_store = _with_literal_fallback(
        vector_store,
        snapshot_store=_literal_fallback_snapshot_store(config, enabled=False),
    )
    registry.register("vector_store", vector_store)

    # Fake embedding provider
    embedding_provider = FakeEmbeddingProvider(dimension=embedding_defaults.dimension)
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
