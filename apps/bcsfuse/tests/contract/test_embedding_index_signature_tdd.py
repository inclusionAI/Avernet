"""Durable embedding model-space contracts for the shared vector index."""

from __future__ import annotations

from typing import Any

import pytest

from app.application.services.index_state_vector_store import (
    EmbeddingIndexSignatureMismatch,
    EmbeddingIndexSignatureUnavailable,
    IndexStateMaintainingVectorStore,
)
from app.domain.models.index_state import IndexStateRecord


class SignatureStore:
    def __init__(
        self,
        signature: tuple[str, int] | None = None,
        *,
        fail: bool = False,
    ) -> None:
        self.signature = signature
        self.fail = fail
        self.registered: list[tuple[str, str, int]] = []

    def register_embedding_index_signature(
        self, collection_pair: str, *, embedding_model: str, embedding_dimension: int
    ) -> tuple[str, int]:
        if self.fail:
            raise RuntimeError("signature store unavailable")
        self.registered.append((collection_pair, embedding_model, embedding_dimension))
        if self.signature is None:
            self.signature = (embedding_model, embedding_dimension)
        return self.signature


class SearchBackend:
    def __init__(self) -> None:
        self.search_calls = 0
        self.rebuild_calls = 0
        self.sync_calls = 0

    def index_dependency_readiness(self) -> dict[str, bool]:
        return {
            "dense_qdrant_ready": True,
            "keyword_qdrant_ready": True,
            "dense_qdrant_unavailable": False,
            "keyword_qdrant_unavailable": False,
        }

    def get_vector_ids(self) -> list[str]:
        return ["bot:owner:default:full"]

    def get(self, point_id: str):
        return None

    def check_ready(self) -> None:
        return None

    def close(self) -> None:
        return None

    def search(self, *args: Any, **kwargs: Any) -> list[str]:
        self.search_calls += 1
        return ["compatible-hit"]

    def search_keyword(self, *args: Any, **kwargs: Any) -> list[str]:
        return ["keyword-fallback-hit"]

    def rebuild_from_backend(self, *args: Any, **kwargs: Any) -> dict[str, int]:
        self.rebuild_calls += 1
        return {"rebuilt": 1}

    def sync_incremental(self, *args: Any, **kwargs: Any) -> dict[str, int]:
        self.sync_calls += 1
        return {"synchronized": 1}


class RecordStore:
    def __init__(self, *, fail_claim: bool = False, fail_read: bool = False) -> None:
        self.fail_claim = fail_claim
        self.fail_read = fail_read

    def claim_owner(self, collection_pair: str, owner_id: str, *, lease_seconds: float):
        if self.fail_claim:
            raise RuntimeError("index state unavailable")
        return self._record()

    def read_owner(self, collection_pair: str):
        if self.fail_read:
            raise RuntimeError("index state unavailable")
        return self._record()

    def release_owner(self, collection_pair: str, owner_id: str) -> None:
        return None

    def publish_index_version(
        self, collection_pair: str, owner_id: str, index_version: int
    ):
        return self._record(index_version=index_version)

    @staticmethod
    def _record(index_version: int = 1) -> IndexStateRecord:
        return IndexStateRecord(
            collection_pair="worker_profiles:dense+keyword",
            owner_id="pod-a",
            owner_expires_at=130.0,
            index_version=index_version,
            index_updated_at=100.0,
            observed_at=120.0,
        )


def make_owner(
    *,
    model: str = "Qwen3-Embedding-8B",
    dimension: int = 1024,
    signature: tuple[str, int] | None = None,
    fail_signature: bool = False,
    fail_claim: bool = False,
) -> IndexStateMaintainingVectorStore:
    return IndexStateMaintainingVectorStore(
        SearchBackend(),
        store=RecordStore(fail_claim=fail_claim),
        owner_id="pod-a",
        collection_pair="worker_profiles:dense+keyword",
        stale_after_seconds=20,
        lease_seconds=30,
        embedding_model=model,
        embedding_dimension=dimension,
        embedding_index_signature_store=SignatureStore(
            signature, fail=fail_signature
        ),
    )


def test_missing_signature_is_registered_once_and_allows_dense_search() -> None:
    owner = make_owner()
    assert owner.embedding_index_signature_mismatch is False
    assert owner.embedding_index_signature_unavailable is False
    assert owner.search([1.0], 1) == ["compatible-hit"]
    assert owner.backend.search_calls == 1


def test_model_space_mismatch_blocks_dense_search_and_writes() -> None:
    owner = make_owner(signature=("Qwen3-Embedding-0.6B", 1024))
    assert owner.embedding_index_signature_mismatch is True

    with pytest.raises(EmbeddingIndexSignatureMismatch):
        owner.search([1.0], 1)
    assert owner.backend.search_calls == 0

    with pytest.raises(EmbeddingIndexSignatureMismatch):
        owner.upsert([])
    readiness = owner.index_dependency_readiness()
    assert readiness["dense_qdrant_ready"] is False
    assert readiness["keyword_qdrant_ready"] is True
    assert readiness["embedding_index_signature_mismatch"] is True
    assert owner.check_ready() is False


def test_dimension_mismatch_is_not_treated_as_a_compatible_model_space() -> None:
    owner = make_owner(signature=("Qwen3-Embedding-8B", 4096))

    with pytest.raises(EmbeddingIndexSignatureMismatch):
        owner.search([1.0], 1)


def test_signature_store_failure_fails_closed_without_leaking_backend_hits() -> None:
    owner = make_owner(fail_signature=True)
    assert owner.embedding_index_signature_unavailable is True

    with pytest.raises(EmbeddingIndexSignatureUnavailable):
        owner.search([1.0], 1)
    assert owner.backend.search_calls == 0
    readiness = owner.index_dependency_readiness()
    assert readiness["dense_qdrant_ready"] is False
    assert readiness["embedding_index_signature_unavailable"] is True


def test_initial_owner_state_failure_still_verifies_model_space() -> None:
    signature_store = SignatureStore()
    owner = IndexStateMaintainingVectorStore(
        SearchBackend(),
        store=RecordStore(fail_claim=True),
        owner_id="pod-a",
        collection_pair="worker_profiles:dense+keyword",
        stale_after_seconds=20,
        lease_seconds=30,
        embedding_model="Qwen3-Embedding-8B",
        embedding_dimension=1024,
        embedding_index_signature_store=signature_store,
    )

    assert owner.index_state_unavailable is True
    assert owner.embedding_index_signature_unavailable is False
    assert signature_store.registered == [
        (
            "worker_profiles:dense+keyword",
            "Qwen3-Embedding-8B",
            1024,
        )
    ]


def test_initial_owner_state_failure_keeps_unverified_signature_dense_fail_closed() -> None:
    owner = make_owner(fail_claim=True, fail_signature=True)

    assert owner.index_state_unavailable is True
    assert owner.embedding_index_signature_unavailable is True
    with pytest.raises(EmbeddingIndexSignatureUnavailable):
        owner.search([1.0], 1)
    assert owner.backend.search_calls == 0
    readiness = owner.index_dependency_readiness()
    assert readiness["dense_qdrant_ready"] is False
    assert readiness["keyword_qdrant_ready"] is True
    assert readiness["embedding_index_signature_unavailable"] is True


def test_sqlite_signature_registration_is_idempotent_and_preserves_active_model(
    tmp_path,
) -> None:
    from app.infra.public.stores.sqlite_index_state_store import SQLiteIndexStateStore

    store = SQLiteIndexStateStore(str(tmp_path / "index-state.sqlite"))
    registered = store.register_embedding_index_signature(
        "worker_profiles:dense+keyword",
        embedding_model="Qwen3-Embedding-0.6B",
        embedding_dimension=1024,
    )
    second = store.register_embedding_index_signature(
        "worker_profiles:dense+keyword",
        embedding_model="Qwen3-Embedding-8B",
        embedding_dimension=4096,
    )

    assert registered == ("Qwen3-Embedding-0.6B", 1024)
    assert second == ("Qwen3-Embedding-0.6B", 1024)


def test_maintenance_rebuild_and_incremental_sync_fail_closed_on_model_space_drift() -> None:
    owner = make_owner(signature=("Qwen3-Embedding-0.6B", 1024))

    with pytest.raises(EmbeddingIndexSignatureMismatch):
        owner.rebuild_from_backend()
    with pytest.raises(EmbeddingIndexSignatureMismatch):
        owner.sync_incremental()

    assert owner.backend.rebuild_calls == 0
    assert owner.backend.sync_calls == 0


def test_shared_replicas_keep_keyword_fallback_when_model_space_drifts(
    tmp_path,
) -> None:
    from app.infra.public.stores.sqlite_index_state_store import SQLiteIndexStateStore

    shared_store = SQLiteIndexStateStore(str(tmp_path / "index-state.sqlite"))

    def make_replica(
        owner_id: str, *, model: str, dimension: int
    ) -> IndexStateMaintainingVectorStore:
        return IndexStateMaintainingVectorStore(
            SearchBackend(),
            store=shared_store,
            owner_id=owner_id,
            collection_pair="worker_profiles:dense+keyword",
            stale_after_seconds=20,
            lease_seconds=30,
            embedding_model=model,
            embedding_dimension=dimension,
            embedding_index_signature_store=shared_store,
        )

    first = make_replica("pod-a", model="Qwen3-Embedding-8B", dimension=1024)
    compatible = make_replica("pod-b", model="Qwen3-Embedding-8B", dimension=1024)
    drifted = make_replica("pod-c", model="Qwen3-Embedding-0.6B", dimension=1024)

    assert first.search([1.0], 1) == ["compatible-hit"]
    assert compatible.search([1.0], 1) == ["compatible-hit"]

    with pytest.raises(EmbeddingIndexSignatureMismatch):
        drifted.search([1.0], 1)
    assert drifted.search_keyword("python") == ["keyword-fallback-hit"]
    readiness = drifted.index_dependency_readiness()
    assert readiness["dense_qdrant_ready"] is False
    assert readiness["keyword_qdrant_ready"] is True
    assert readiness["embedding_index_signature_mismatch"] is True

    first.close()
    compatible.close()
    drifted.close()


def test_readiness_reports_model_space_mismatch() -> None:
    from fastapi.testclient import TestClient

    from app.bootstrap.app_factory import create_bcsfuse_app
    from app.bootstrap.application_context import ApplicationContext
    from app.bootstrap.provider_registry import ProviderRegistry

    owner = make_owner(signature=("Qwen3-Embedding-0.6B", 1024))
    registry = ProviderRegistry()
    registry.register("config", object())
    registry.register("secret_provider", object())

    class Startup:
        async def initialize(self):
            return None

        async def shutdown(self):
            return None

    registry.register("startup_provider", Startup())
    registry.register("vector_store", owner)
    app = create_bcsfuse_app(
        ApplicationContext(
            mode="runtime",
            startup_profile="opensource",
            registry=registry,
        )
    )

    with TestClient(app) as client:
        response = client.get("/ready")

    assert response.status_code == 503
    body = response.json()
    assert body["ready"] is False
    assert body["embedding_model"] == "Qwen3-Embedding-8B"
    assert body["embedding_dimension"] == 1024
    assert body["embedding_index_signature_ready"] is False
    assert body["embedding_index_signature_mismatch"] is True
