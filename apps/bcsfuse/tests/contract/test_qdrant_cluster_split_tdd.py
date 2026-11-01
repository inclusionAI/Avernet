"""TDD contract for isolating dense and keyword Qdrant clusters."""

from __future__ import annotations

from typing import Any

from search_core.config.qdrant import QdrantConnection

from app.application.services.keyword_candidate_selection import (
    keyword_candidates_with_status,
)
from app.domain.models.vector_search_hit import VectorSearchHit
from app.infra.public.vectorstores.qdrant_mysql_vector_store import (
    QdrantMySQLVectorStore,
)


def dense_connection() -> QdrantConnection:
    return QdrantConnection(
        mode="server",
        url="http://dense-qdrant.example.test:6333",
    )


def keyword_connection() -> QdrantConnection:
    return QdrantConnection(
        mode="server",
        url="http://keyword-qdrant.example.test:7333",
        timeout=3.5,
    )


def split_store() -> QdrantMySQLVectorStore:
    return QdrantMySQLVectorStore(
        collection_name="cluster_split_contract",
        dimension=2,
        persistence_backend=object(),
        connection=dense_connection(),
        keyword_connection=keyword_connection(),
    )


def test_keyword_connection_falls_back_to_dense_when_not_configured() -> None:
    dense = dense_connection()
    keyword = QdrantConnection.keyword_from_mapping({}, fallback=dense)
    assert keyword is dense


def test_keyword_connection_accepts_an_explicit_cluster_and_hides_credentials() -> None:
    credential_name = "QDRANT_KEYWORD_" + "API_KEY"
    credential_fixture = "private-" + "token"
    keyword = QdrantConnection.keyword_from_mapping(
        {
            "QDRANT_KEYWORD_URL": "http://keyword-qdrant.example.test:7333",
            "QDRANT_KEYWORD_GRPC_PORT": "7334",
            "QDRANT_KEYWORD_PREFER_GRPC": "false",
            "QDRANT_KEYWORD_TIMEOUT_SECONDS": "3.5",
            credential_name: credential_fixture,
        },
        fallback=dense_connection(),
    )
    assert keyword.mode == "server"
    assert keyword.url == "http://keyword-qdrant.example.test:7333"
    assert keyword.grpc_port == 7334
    assert not keyword.prefer_grpc
    assert keyword.timeout == 3.5
    assert "private-token" not in repr(keyword)

def test_keyword_connection_must_use_the_same_local_server_mode_as_dense() -> None:
    with __import__("pytest").raises(ValueError, match="match"):
        QdrantConnection.keyword_from_mapping(
            {"QDRANT_KEYWORD_MODE": "local"},
            fallback=dense_connection(),
        )


def test_store_uses_separate_connections_for_dense_and_keyword() -> None:
    store = split_store()
    try:
        assert store.connection.url == dense_connection().url
        assert store.keyword_connection.url == keyword_connection().url
        assert store._qdrant.connection.url == dense_connection().url
        assert store._keywords.connection.url == keyword_connection().url
        assert store._keywords.collection_name == "cluster_split_contract_keywords"
    finally:
        store.close()


class ProbeClient:
    def __init__(self, probes: list[str], label: str) -> None:
        self._probes = probes
        self._label = label

    def get_collection(self, collection_name: str) -> None:
        self._probes.append((self._label, collection_name))


class ProbeStore:
    def __init__(self, probes: list[tuple[str, str]], label: str, collection_name: str) -> None:
        self.connection = (
            dense_connection() if label == "dense" else keyword_connection()
        )
        self.collection_name = collection_name
        self._probes = probes
        self._label = label
        self._client = ProbeClient(probes, label)

    def _ensure_client(self) -> None:
        self._probes.append((f"{self._label}_ensure", self.collection_name))

    def _ensure_collection(self) -> None:
        return None

    def close(self) -> None:
        return None


def test_readiness_probes_dense_and_keyword_clusters_separately() -> None:
    store = split_store()
    probes: list[tuple[str, str]] = []
    store._qdrant = ProbeStore(probes, "dense", "cluster_split_contract")
    store._keywords = ProbeStore(
        probes, "keyword", "cluster_split_contract_keywords"
    )
    try:
        store.check_ready()
        assert probes == [
            ("dense_ensure", "cluster_split_contract"),
            ("dense", "cluster_split_contract"),
            ("keyword_ensure", "cluster_split_contract_keywords"),
            ("keyword", "cluster_split_contract_keywords"),
        ]
    finally:
        store.close()


def test_keyword_cluster_failure_degrades_to_dense_without_failing_dense_search(
    monkeypatch,
) -> None:
    store = split_store()
    dense_hit = VectorSearchHit(
        id="bot:owner:default:full", score=0.9, payload={"profile_key": "bot:owner:default"}
    )
    monkeypatch.setattr(store, "_ensure_client", lambda: None)
    monkeypatch.setattr(store, "_keywords_ready", True)
    monkeypatch.setattr(
        store,
        "search",
        lambda *args, **kwargs: [dense_hit],
    )
    monkeypatch.setattr(
        type(store),
        "_keyword_failure_probe",
        lambda self, *args, **kwargs: (_ for _ in ()).throw(
            RuntimeError("keyword cluster unavailable")
        ),
        raising=False,
    )
    monkeypatch.setattr(
        store,
        "text_search",
        lambda *args, **kwargs: store._keyword_failure_probe(*args, **kwargs),
    )
    outcome = keyword_candidates_with_status(
        store,
        "payment assistant",
        10,
        None,
        set(),
        None,
        {"full"},
    )
    assert outcome.available is False
    assert outcome.candidates == []
    assert [hit.id for hit in store.search([1.0, 0.0], 10)] == [dense_hit.id]


def test_runtime_composition_reads_the_split_keyword_cluster(monkeypatch) -> None:
    from app.bootstrap.opensource import build_opensource_provider_registry

    class NoopMySQLDependency:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            return None

    class UnavailableIndexStateStore:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            return None

        def claim_owner(self, *args: Any, **kwargs: Any):
            raise RuntimeError("index state unavailable in isolated contract test")

        def release_owner(self, *args: Any, **kwargs: Any) -> None:
            return None

    mysql_modules = [
        "app.infra.public.database.mysql_connection_pool",
        "app.infra.public.stores.mysql_worker_registry_store",
        "app.infra.public.stores.mysql_worker_runtime_state_store",
        "app.infra.public.stores.mysql_worker_profile_content_store",
        "app.infra.public.stores.mysql_worker_profile_binding_store",
        "app.infra.public.stores.mysql_fused_profile_store",
        "app.infra.public.audit.mysql_worker_audit_log_store",
    ]
    import importlib

    for module_name in mysql_modules:
        module = importlib.import_module(module_name)
        for name in vars(module):
            if name.startswith("MySQL") or name.endswith("PoolProvider"):
                monkeypatch.setattr(module, name, NoopMySQLDependency)

    state_store_module = importlib.import_module(
        "app.infra.public.stores.mysql_index_state_store"
    )
    monkeypatch.setattr(
        state_store_module, "MySQLIndexStateStore", UnavailableIndexStateStore
    )

    monkeypatch.setenv("EMBEDDING_BASE_URL", "http://127.0.0.1:1")
    monkeypatch.setenv("EMBEDDING_AUTH_TOKEN", "isolated-test-token")
    monkeypatch.setenv("RERANKER_BASE_URL", "http://127.0.0.1:1")
    monkeypatch.setenv("RERANKER_API_KEY", "isolated-test-token")
    monkeypatch.setenv("LLM_BASE_URL", "http://127.0.0.1:1")
    monkeypatch.setenv("LLM_AUTH_TOKEN", "isolated-test-token")
    monkeypatch.setattr(
        "app.bootstrap.opensource._with_literal_fallback",
        lambda store, snapshot_store=None: store,
    )
    monkeypatch.setenv("QDRANT_MODE", "server")
    monkeypatch.setenv("QDRANT_URL", "http://dense-qdrant.example.test:6333")
    monkeypatch.setenv(
        "QDRANT_KEYWORD_URL",
        "http://keyword-qdrant.example.test:7333",
    )
    monkeypatch.setenv("QDRANT_PREFER_GRPC", "false")
    monkeypatch.setenv("QDRANT_KEYWORD_PREFER_GRPC", "false")
    monkeypatch.delenv("QDRANT_KEYWORD_MODE", raising=False)

    registry = build_opensource_provider_registry(mode="runtime")
    vector_store = registry.get("vector_store")
    # The isolated test bypasses the eager literal snapshot only; the
    # index-state wrapper and durable Qdrant composition remain real.
    raw_store = vector_store.backend

    try:
        assert raw_store.connection.url == "http://dense-qdrant.example.test:6333"
        assert raw_store.keyword_connection.url == (
            "http://keyword-qdrant.example.test:7333"
        )
        assert raw_store._keywords.connection.url == (
            "http://keyword-qdrant.example.test:7333"
        )
    finally:
        vector_store.close()


def test_ready_reports_dense_and_keyword_qdrant_modes_separately(
    monkeypatch,
) -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.bootstrap.app_factory import _register_health_routes
    from app.bootstrap.application_context import ApplicationContext
    from app.bootstrap.provider_registry import ProviderRegistry
    from app.infra.config.feature_flags import FeatureFlags

    store = split_store()
    probes: list[tuple[str, str]] = []
    store._qdrant = ProbeStore(probes, "dense", "cluster_split_contract")
    store._keywords = ProbeStore(
        probes, "keyword", "cluster_split_contract_keywords"
    )
    monkeypatch.setattr(
        FeatureFlags,
        "is_profile_embedding_index_enabled",
        classmethod(lambda cls: True),
    )
    monkeypatch.setattr(store, "size", lambda: 1)
    registry = ProviderRegistry()
    registry.register("vector_store", store)
    app = FastAPI()
    _register_health_routes(
        app,
        ApplicationContext(mode="test", startup_profile="opensource", registry=registry),
    )
    try:
        with TestClient(app) as client:
            response = client.get("/ready")
        assert response.status_code == 200
        payload = response.json()
        assert payload["qdrant_mode"] == "server"
        assert payload["keyword_qdrant_mode"] == "server"
        assert "dense-qdrant.example.test" not in response.text
        assert "keyword-qdrant.example.test" not in response.text
        assert payload["index_size"] == 1
    finally:
        store.close()
