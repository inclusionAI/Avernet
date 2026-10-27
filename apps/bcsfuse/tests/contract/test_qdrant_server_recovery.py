"""The TC durable pipeline keeps its behavior when both indexes move to Server."""

import os
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from search_core.config.qdrant import QdrantConnection

from app.bootstrap.app_factory import _register_health_routes
from app.bootstrap.application_context import ApplicationContext
from app.bootstrap.provider_registry import ProviderRegistry
from app.domain.models.vector_point import VectorPoint
from app.infra.public.vectorstores.qdrant_mysql_vector_store import (
    QdrantMySQLVectorStore,
)
from app.infra.vectorstore_backends.sqlite_vector_persistence_backend import (
    SQLiteVectorPersistenceBackend,
)


@pytest.fixture(params=[False, True], ids=["http", "grpc"])
def store(request, tmp_path):
    url = os.getenv("QDRANT_TEST_URL")
    if not url:
        pytest.skip("Set QDRANT_TEST_URL for real Server recovery tests")
    instance = QdrantMySQLVectorStore(
        dimension=2,
        collection_name="tc_contract_" + uuid4().hex,
        persistence_backend=SQLiteVectorPersistenceBackend(str(tmp_path / "db")),
        connection=QdrantConnection(
            mode="server",
            url=url,
            prefer_grpc=request.param,
            grpc_port=int(os.getenv("QDRANT_TEST_GRPC_PORT", "6334")),
        ),
    )
    instance.rebuild_from_backend()
    yield instance
    instance._qdrant._client.delete_collection(instance.collection_name)
    instance._keywords._client.delete_collection(instance._keywords.collection_name)
    instance.close()


def point(key, text="支付助手"):
    return VectorPoint(
        id=key + ":p:full",
        vector=[1.0, 0.0],
        payload={
            "worker_id": key,
            "content": text,
            "runtime_state": "online",
        },
    )


def test_rebuild_repairs_both_indexes_and_preserves_tc_policy(store):
    store.upsert([point("owner"), point("owner:nested")])
    assert store.text_search("owner", 1)[0].payload["_keyword_exact_id"] is True
    assert store.update_payload_by_worker("owner", {"runtime_state": "offline"}) == 1
    assert [
        h.id for h in store.text_search("支付", 10, {"runtime_state": "online"})
    ] == ["owner:nested:p:full"]
    # Simulate a failed prior dual-index write and an obsolete remote keyword row.
    store._keywords.upsert([point("stale")])
    store._qdrant.upsert([point("dense-stale")])
    store._persistence.delete_batch(["owner:p:full"])
    store._keywords.close()
    store._qdrant.close()
    store.rebuild_from_backend()
    assert store.get_vector_ids() == ["owner:nested:p:full"]
    assert store._keywords.get_vector_ids() == ["owner:nested:p:full"]
    assert store.search([1.0, 0.0], 10)[0].id == "owner:nested:p:full"
    store.delete(["owner:nested:p:full"])
    assert store.text_search("支付", 10) == []
    assert store.search([1.0, 0.0], 10) == []
    store.check_ready()


def test_failed_keyword_write_fails_readiness_until_rebuild(store, monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError("keyword update failed")

    with monkeypatch.context() as patch:
        patch.setattr(store._keywords, "upsert", fail)
        with pytest.raises(RuntimeError, match="DEGRADED_REBUILD_REQUIRED"):
            store.upsert([point("owner")])
    assert store.get("owner:p:full") is not None
    with pytest.raises(RuntimeError, match="requires rebuild"):
        store.check_ready()
    store.rebuild_from_backend()
    store.check_ready()
    assert store.text_search("支付", 1)[0].id == "owner:p:full"


def test_tc_server_searches_can_overlap(store, monkeypatch):
    barrier = Barrier(2)

    def query(*args, **kwargs):
        barrier.wait(timeout=3)
        return []

    monkeypatch.setattr(store._qdrant, "search", query)
    monkeypatch.setattr(store._keywords, "search", query)
    with ThreadPoolExecutor(2) as pool:
        futures = [
            pool.submit(store.search, [1.0, 0.0], 1),
            pool.submit(store.text_search, "支付", 1),
        ]
        assert [future.result() for future in futures] == [[], []]


def test_ready_returns_503_when_server_probe_fails():
    class Unavailable:
        def check_ready(self):
            raise OSError("private endpoint details must not leak")

    registry = ProviderRegistry()
    registry.register("vector_store", Unavailable())
    context = ApplicationContext(
        mode="test", startup_profile="opensource", registry=registry
    )
    app = FastAPI()
    _register_health_routes(app, context)
    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
        response = client.get("/ready")
        assert response.status_code == 503
        assert response.json()["ready"] is False
        assert "private endpoint" not in response.text


def test_ready_probes_real_server_and_reports_mode(store):
    registry = ProviderRegistry()
    registry.register("vector_store", store)
    context = ApplicationContext(mode="test", startup_profile="opensource", registry=registry)
    app = FastAPI()
    _register_health_routes(app, context)
    with TestClient(app) as client:
        response = client.get("/ready")
        assert response.status_code == 200
        assert response.json()["qdrant_mode"] == "server"
