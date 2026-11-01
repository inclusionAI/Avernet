"""TDD for dev-mode index freshness, ownership, and cache enablement."""

from __future__ import annotations

from pathlib import Path

from app.application.agent_search_cache import AgentSearchResultCache
from app.bootstrap.opensource import _build_dev_providers
from app.bootstrap.provider_registry import ProviderRegistry


class MappingConfig:
    def __init__(self, values: dict[str, object] | None = None) -> None:
        self.values = values or {}

    def get(self, key: str, default: object = None) -> object:
        return self.values.get(key, default)

    def get_int(self, key: str, default: int) -> int:
        return int(self.values.get(key, default))

    def get_float(self, key: str, default: float) -> float:
        return float(self.values.get(key, default))

    def get_bool(self, key: str, default: bool) -> bool:
        return bool(self.values.get(key, default))


class DevApplicationContext:
    def __init__(self, registry: ProviderRegistry, config: MappingConfig) -> None:
        self.registry = registry
        self.config = config


class LocalVectorBackend:
    def __init__(self, *args: object, **kwargs: object) -> None:
        self.closed = False
        self.collection_name = str(
            kwargs.get("collection_name", "bcsfuse_vectors")
        )

    def check_ready(self) -> bool:
        return True

    def close(self) -> None:
        self.closed = True


def test_dev_vector_store_exposes_durable_index_version_for_result_cache(
    tmp_path: Path, monkeypatch
) -> None:
    import importlib

    realtime_module = importlib.import_module(
        "app.infra.public.vectorstores.qdrant_sqlite_vector_store"
    )
    monkeypatch.setattr(
        realtime_module, "QdrantSQLiteVectorStore", LocalVectorBackend
    )
    monkeypatch.setattr(
        "app.bootstrap.opensource._with_literal_fallback", lambda store, snapshot_store=None: store
    )
    monkeypatch.setenv(
        "BCSFUSE_DATABASE_SQLITE_PATH", str(tmp_path / "bcsfuse.sqlite")
    )
    monkeypatch.setenv(
        "BCSFUSE_VECTOR_SQLITE_PATH", str(tmp_path / "vectors.sqlite")
    )
    monkeypatch.setenv("BCSFUSE_INDEX_OWNER_ID", "dev-result-cache-owner")
    monkeypatch.setenv("EMBEDDING_BASE_URL", "http://127.0.0.1:1/v1")
    monkeypatch.setenv("EMBEDDING_AUTH_TOKEN", "test-only")
    monkeypatch.setenv("LLM_BASE_URL", "http://127.0.0.1:1/v1")
    monkeypatch.setenv("LLM_AUTH_TOKEN", "test-only")

    registry = ProviderRegistry()
    registry.register("startup_provider", object())
    _build_dev_providers(registry, MappingConfig())
    vector_store = registry.get("vector_store")

    try:
        assert vector_store is not None
        assert vector_store.collection_name == "bcsfuse_vectors"
        assert vector_store.collection_pair == "bcsfuse_vectors:dense+keyword"
        assert vector_store.index_version == 0
        assert vector_store.read_only is False
        assert vector_store.index_stale is False
        assert vector_store.stale_after_seconds == 20
        assert vector_store.lease_seconds == 30

        cache = AgentSearchResultCache.from_context(
            DevApplicationContext(registry, MappingConfig())
        )
        assert cache is not None
        assert cache.current_index_version() == 0
        assert cache.key("search", index_version=0, material={"top_k": 3})
    finally:
        vector_store.close()


def test_dev_qdrant_store_accepts_the_composed_collection_name(tmp_path: Path) -> None:
    from app.infra.public.vectorstores.qdrant_sqlite_vector_store import (
        QdrantSQLiteVectorStore,
    )

    store = QdrantSQLiteVectorStore(
        collection_name="composed_dev_collection",
        db_path=str(tmp_path / "vectors.sqlite"),
        qdrant_path=str(tmp_path / "qdrant"),
        auto_load=False,
    )

    try:
        assert store.collection_name == "composed_dev_collection"
        assert store._keywords.collection_name == "composed_dev_collection_keywords"
    finally:
        store.close()
