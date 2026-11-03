"""TDD contract for dense-first partial index readiness."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.bootstrap.app_factory import create_bcsfuse_app
from app.bootstrap.application_context import ApplicationContext
from app.application.services.index_state_vector_store import (
    IndexStateMaintainingVectorStore,
)
from app.application.services.literal_fallback_maintenance import (
    LiteralFallbackMaintainingVectorStore,
    LiteralFallbackSnapshot,
)
from app.bootstrap.provider_registry import ProviderRegistry
from app.domain.models.index_state import IndexStateRecord
from app.infra.public.vectorstores.qdrant_mysql_vector_store import (
    QdrantMySQLVectorStore,
)
from search_core.config.qdrant import QdrantConnection


@pytest.fixture(autouse=True)
def disable_background_index(monkeypatch):
    """Health-route tests isolate readiness from optional startup reconciliation."""
    from app.infra.config.feature_flags import FeatureFlags

    FeatureFlags.reset()
    monkeypatch.setenv("ENABLE_PROFILE_EMBEDDING_INDEX", "false")
    FeatureFlags._get_settings()
    yield
    FeatureFlags.reset()


class StartupProvider:
    async def initialize(self) -> None:
        return None

    async def shutdown(self) -> None:
        return None


class PartialVectorStore:
    def __init__(
        self,
        *,
        dense_ready: bool,
        keyword_ready: bool,
        index_stale: bool = False,
        index_state_unavailable: bool = False,
        startup_rebuild_failed: bool = False,
        literal_fallback_profiles: int | None = None,
    ) -> None:
        self.startup_rebuild_failed = startup_rebuild_failed
        self.connection = type("Connection", (), {"mode": "server"})()
        self.keyword_connection = type("Connection", (), {"mode": "server"})()
        self.collection_name = "worker_profiles"
        self.index_owner = "pod-a"
        self.index_version = 7
        self.index_lag_seconds = 0.0
        self.index_stale = index_stale
        self.index_state_unavailable = index_state_unavailable
        self.read_only = False
        self._dense_ready = dense_ready
        self._keyword_ready = keyword_ready
        self._index_dependency_readiness = {
            "dense_qdrant_ready": dense_ready,
            "keyword_qdrant_ready": keyword_ready,
        }
        self.literal_fallback_index = (
            ServingLiteralFallback(profiles=literal_fallback_profiles)
            if literal_fallback_profiles is not None
            else None
        )

    def index_dependency_readiness(self) -> dict[str, bool]:
        return dict(self._index_dependency_readiness)

    def check_ready(self) -> None:
        # Partial readiness takes precedence over the legacy aggregate probe.
        # It would otherwise turn a keyword-only outage into full unavailability.
        if not self._dense_ready:
            raise RuntimeError("dense qdrant probe failed")


class ServingLiteralFallback:
    source = "durable"
    snapshot_index_version = 7

    def __init__(self, *, profiles: int) -> None:
        self._profiles = profiles

    @property
    def profile_count(self) -> int:
        return self._profiles


class LegacyVectorStore:
    def __init__(self, *, is_ready: bool) -> None:
        self.connection = type("Connection", (), {"mode": "server"})()
        self.keyword_connection = None
        self.collection_name = "worker_profiles"
        self.index_owner = "pod-a"
        self.index_version = 7
        self.index_lag_seconds = 0.0
        self.index_stale = False
        self.index_state_unavailable = False
        self.read_only = False
        self.check_ready = lambda: is_ready


def build_context(vector_store) -> ApplicationContext:
    registry = ProviderRegistry()
    registry.register("config", object())
    registry.register("secret_provider", object())
    registry.register("startup_provider", StartupProvider())
    registry.register("vector_store", vector_store)
    return ApplicationContext(
        mode="runtime",
        startup_profile="opensource",
        registry=registry,
    )


def ready_response(vector_store):
    app = create_bcsfuse_app(build_context(vector_store))
    with TestClient(app) as client:
        return client.get("/ready")


def test_keyword_qdrant_outage_is_degraded_but_ready() -> None:
    response = ready_response(
        PartialVectorStore(dense_ready=True, keyword_ready=False)
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ready"] is True
    assert body["degraded"] is True
    assert body["dense_qdrant_available"] is True
    assert body["keyword_qdrant_available"] is False
    assert body["dense_qdrant_unavailable"] is False
    assert body["keyword_qdrant_unavailable"] is True


def test_dense_qdrant_outage_with_serving_snapshot_stays_ready() -> None:
    response = ready_response(
        PartialVectorStore(
            dense_ready=False,
            keyword_ready=True,
            literal_fallback_profiles=504,
        )
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ready"] is True
    assert body["degraded_serving_ready"] is True
    assert body["dense_qdrant_available"] is False
    assert body["keyword_qdrant_available"] is True


def test_dual_qdrant_outage_with_serving_snapshot_stays_ready() -> None:
    response = ready_response(
        PartialVectorStore(
            dense_ready=False,
            keyword_ready=False,
            literal_fallback_profiles=504,
        )
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ready"] is True
    assert body["degraded_serving_ready"] is True
    assert body["dense_qdrant_available"] is False
    assert body["keyword_qdrant_available"] is False


def test_stale_index_with_serving_snapshot_stays_in_serving_pool() -> None:
    response = ready_response(
        PartialVectorStore(
            dense_ready=False,
            keyword_ready=False,
            index_stale=True,
            literal_fallback_profiles=504,
        )
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ready"] is True
    assert body["degraded_serving_ready"] is True
    assert body["index_stale"] is True


def test_dense_qdrant_outage_makes_pod_not_ready() -> None:
    response = ready_response(
        PartialVectorStore(dense_ready=False, keyword_ready=True)
    )

    assert response.status_code == 503
    body = response.json()
    assert body["ready"] is False
    assert body["degraded"] is True
    assert body["dense_qdrant_available"] is False
    assert body["keyword_qdrant_available"] is True
    assert body["dense_qdrant_unavailable"] is True
    assert body["keyword_qdrant_unavailable"] is False


def test_healthy_split_clusters_are_not_marked_degraded() -> None:
    response = ready_response(
        PartialVectorStore(dense_ready=True, keyword_ready=True)
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ready"] is True
    assert body["degraded"] is False
    assert body["dense_qdrant_unavailable"] is False
    assert body["keyword_qdrant_unavailable"] is False


def test_startup_rebuild_failure_makes_pod_degraded_and_not_ready() -> None:
    response = ready_response(
        PartialVectorStore(
            dense_ready=True,
            keyword_ready=True,
            startup_rebuild_failed=True,
        )
    )

    assert response.status_code == 503
    body = response.json()
    assert body["ready"] is False
    assert body["degraded"] is True
    assert body["startup_rebuild_failed"] is True


def test_partial_readiness_does_not_override_stale_index_state() -> None:
    response = ready_response(
        PartialVectorStore(
            dense_ready=True,
            keyword_ready=False,
            index_stale=True,
        )
    )

    assert response.status_code == 503
    body = response.json()
    assert body["ready"] is False
    assert body["degraded"] is True
    assert body["keyword_qdrant_unavailable"] is True
    assert body["index_stale"] is True


def test_partial_readiness_does_not_override_index_state_unavailable() -> None:
    response = ready_response(
        PartialVectorStore(
            dense_ready=True,
            keyword_ready=True,
            index_state_unavailable=True,
        )
    )

    assert response.status_code == 503
    body = response.json()
    assert body["ready"] is False
    assert body["degraded"] is False
    assert body["index_state_unavailable"] is True


def test_partial_readiness_does_not_leak_backend_failure_details() -> None:
    class LeakingVectorStore(PartialVectorStore):
        def index_dependency_readiness(self) -> dict[str, bool]:
            raise RuntimeError("dense cluster endpoint contains probe secret")

    response = ready_response(
        LeakingVectorStore(dense_ready=True, keyword_ready=False)
    )

    assert response.status_code == 503
    assert "dense cluster endpoint" not in response.text
    assert "probe secret" not in response.text
    assert response.json()["vector_store_error"] == "RuntimeError"


def test_legacy_readiness_contract_remains_unavailable_when_probe_fails() -> None:
    response = ready_response(LegacyVectorStore(is_ready=False))

    assert response.status_code == 503
    body = response.json()
    assert body["ready"] is False
    assert body["vector_store_available"] is True


def test_metrics_expose_partial_index_degradation() -> None:
    app = create_bcsfuse_app(
        build_context(PartialVectorStore(dense_ready=True, keyword_ready=False))
    )
    with TestClient(app) as client:
        response = client.get("/metrics")

    assert response.status_code == 200
    assert "bcsfuse_index_degraded" in response.text


def partial_store() -> QdrantMySQLVectorStore:
    connection = QdrantConnection(
        mode="server",
        url="http://dense-qdrant.example.test:6333",
    )
    keyword_connection = QdrantConnection(
        mode="server",
        url="http://keyword-qdrant.example.test:7333",
    )
    return QdrantMySQLVectorStore(
        collection_name="partial_readiness_contract",
        dimension=2,
        persistence_backend=object(),
        connection=connection,
        keyword_connection=keyword_connection,
    )


class ProbeStore:
    def __init__(
        self,
        *,
        connection: QdrantConnection,
        collection_name: str,
        get_collection=None,
    ) -> None:
        self.connection = connection
        self.collection_name = collection_name

        def successful_probe(*args, **kwargs):
            return None

        self._client = type(
            "Client",
            (),
            {"get_collection": get_collection or successful_probe},
        )()

    def _ensure_client(self) -> None:
        return None

    def _ensure_collection(self) -> None:
        return None

    _collection_initialized = True

    def close(self) -> None:
        return None


def test_qdrant_store_reports_dense_and_keyword_outages_independently() -> None:
    store = partial_store()
    try:
        def fail_dense(*args, **kwargs):
            raise RuntimeError("dense cluster unavailable")

        def fail_keyword(*args, **kwargs):
            raise RuntimeError("keyword cluster unavailable")

        store._keywords_ready = True
        store._qdrant = ProbeStore(
            connection=store.connection,
            collection_name=store.collection_name,
            get_collection=fail_dense,
        )
        store._keywords = ProbeStore(
            connection=store.keyword_connection,
            collection_name=f"{store.collection_name}_keywords",
            get_collection=fail_keyword,
        )
        result = store.index_dependency_readiness()
    finally:
        store.close()

    assert result == {
        "dense_qdrant_ready": False,
        "keyword_qdrant_ready": False,
        "dense_qdrant_unavailable": True,
        "keyword_qdrant_unavailable": True,
    }


def test_qdrant_store_partial_readiness_hides_failure_messages() -> None:
    store = partial_store()
    try:
        store._keywords_ready = True

        def fail_keyword(*args, **kwargs):
            raise RuntimeError("keyword cluster secret endpoint")

        store._qdrant = ProbeStore(
            connection=store.connection,
            collection_name=store.collection_name,
        )
        store._keywords = ProbeStore(
            connection=store.keyword_connection,
            collection_name=f"{store.collection_name}_keywords",
            get_collection=fail_keyword,
        )
        result = store.index_dependency_readiness()
    finally:
        store.close()

    assert result["dense_qdrant_ready"] is True
    assert result["keyword_qdrant_ready"] is False
    assert "secret endpoint" not in str(result)



class PartialIndexStateBackend:
    def index_dependency_readiness(self) -> dict[str, bool]:
        return {
            "dense_qdrant_ready": True,
            "keyword_qdrant_ready": False,
            "dense_qdrant_unavailable": False,
            "keyword_qdrant_unavailable": True,
        }

    def get_vector_ids(self) -> list[str]:
        return []

    def get(self, point_id: str):
        return None

    def close(self) -> None:
        return None


class AvailableIndexStateStore:
    def claim_owner(self, collection_pair, owner_id, *, lease_seconds):
        return self._record(owner_id)

    def read_owner(self, collection_pair):
        return self._record("pod-a")

    @staticmethod
    def _record(owner_id):
        return IndexStateRecord(
            collection_pair="worker_profiles:dense+keyword",
            owner_id=owner_id,
            owner_expires_at=103.0,
            index_version=7,
            index_updated_at=100.0,
            observed_at=200.0,
        )


class FailingReadIndexStateStore(AvailableIndexStateStore):
    def read_owner(self, collection_pair):
        raise RuntimeError("index state backend unavailable")


def partial_owner(store) -> IndexStateMaintainingVectorStore:
    return IndexStateMaintainingVectorStore(
        PartialIndexStateBackend(),
        store=store,
        owner_id="pod-a",
        collection_pair="worker_profiles:dense+keyword",
        stale_after_seconds=60,
        lease_seconds=120,
    )


def test_index_state_wrapper_adds_durable_state_to_partial_readiness() -> None:
    owner = partial_owner(AvailableIndexStateStore())
    owner._local_version = owner.index_version

    result = owner.index_dependency_readiness()

    assert result["dense_qdrant_ready"] is True
    assert result["keyword_qdrant_ready"] is False
    assert result["index_state_ready"] is True


def test_index_state_failure_overrides_a_healthy_dense_probe() -> None:
    owner = partial_owner(FailingReadIndexStateStore())

    result = owner.index_dependency_readiness()

    assert result["dense_qdrant_ready"] is True
    assert result["keyword_qdrant_ready"] is False
    assert result["index_state_ready"] is False
    assert owner.index_state_unavailable is True


def test_literal_fallback_wrapper_propagates_partial_readiness() -> None:
    owner = partial_owner(AvailableIndexStateStore())
    owner._local_version = owner.index_version
    literal_store = LiteralFallbackMaintainingVectorStore(
        owner, LiteralFallbackSnapshot()
    )

    result = literal_store.index_dependency_readiness()

    assert result["dense_qdrant_ready"] is True
    assert result["keyword_qdrant_ready"] is False
    assert result["index_state_ready"] is True


class AdvancingPartialIndexStateStore:
    def claim_owner(self, collection_pair, owner_id, *, lease_seconds):
        return self._record()

    def read_owner(self, collection_pair):
        return self._record()

    @staticmethod
    def _record():
        return IndexStateRecord(
            collection_pair="worker_profiles:dense+keyword",
            owner_id="pod-owner",
            owner_expires_at=103.0,
            index_version=7,
            index_updated_at=100.0,
            observed_at=200.0,
        )


def test_partial_readiness_recovers_stale_read_only_replica() -> None:
    owner = IndexStateMaintainingVectorStore(
        PartialIndexStateBackend(),
        store=AdvancingPartialIndexStateStore(),
        owner_id="pod-reader",
        collection_pair="worker_profiles:dense+keyword",
        stale_after_seconds=60,
        lease_seconds=120,
    )

    result = owner.index_dependency_readiness()

    assert owner.index_revision_stale is False
    assert owner.index_stale is False
    assert result["index_state_ready"] is True


def test_unavailable_dense_readiness_does_not_probe_index_size() -> None:
    class DenseDownWithSize(PartialVectorStore):
        def __init__(self) -> None:
            super().__init__(dense_ready=False, keyword_ready=True)
            self.size_called = False

        def size(self) -> int:
            self.size_called = True
            return 1512

    store = DenseDownWithSize()
    response = ready_response(store)

    assert response.status_code == 503
    assert response.json()["dense_qdrant_available"] is False
    assert store.size_called is False
