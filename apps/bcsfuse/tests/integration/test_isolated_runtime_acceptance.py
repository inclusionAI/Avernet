"""Automated HTTP acceptance: real SQLite/MySQL + Qdrant, deterministic models.

Default run never contacts another application or model service. Optional MySQL
uses a UUID database on the explicitly enabled local test server only. Reopen
checks construct fresh apps and stores; they do not claim an OS-process restart.
"""

import os
import socket
from contextlib import contextmanager
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from tests.fixtures.runtime_acceptance import (
    acceptance_run, exercise_invalid_requests, exercise_lifecycle,
)


TOKEN = "test-token"


class DeterministicEmbedding:
    """Test model: exercises embedding calls, not semantic model quality."""
    dimension = 64

    def __init__(self):
        self.calls = []

    def embed(self, text):
        self.calls.append(text)
        return [1.0] + [0.0] * 63

    def embed_batch(self, texts):
        return [self.embed(text) for text in texts]


@pytest.fixture(params=["sqlite", "mysql"])
def isolated_app_factory(request, monkeypatch, tmp_path):
    use_mysql = request.param == "mysql"
    if use_mysql and os.getenv("BCSFUSE_RUN_MYSQL_INTEGRATION") != "1":
        pytest.skip("MySQL acceptance requires BCSFUSE_RUN_MYSQL_INTEGRATION=1; SQLite acceptance runs offline")

    env = {
        "BCSFUSE_PROVIDER_MODE": "runtime", "BCSFUSE_AUTH_TOKEN": TOKEN,
        "BCSFUSE_DATABASE_SQLITE_PATH": str(tmp_path / "workers.sqlite"),
        "BCSFUSE_FAISS_SQLITE_PATH": str(tmp_path / "unused-vector.sqlite"),
        "BCSFUSE_OBJECT_STORAGE_DIR": str(tmp_path / "objects"),
        "EMBEDDING_DIMENSION": "64", "EMBEDDING_BASE_URL": "",
        "EMBEDDING_AUTH_TOKEN": "", "LLM_BASE_URL": "", "LLM_AUTH_TOKEN": "",
        "RERANKER_BASE_URL": "", "RERANKER_API_KEY": "",
        "ENABLE_PROFILE_EMBEDDING_INDEX": "false", "ENABLE_CAPABILITY_VERIFY": "false",
        "LLM_ENABLED": "false", "CONTENT_EMBEDDING_FIELDS": "profile,capabilities",
    }
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    from src.infra.config.feature_flags import FeatureFlags
    monkeypatch.setattr(FeatureFlags, "_settings", None)

    # Any accidental connection beyond the disposable MySQL database is an error.
    connect = socket.socket.connect
    blocked_connections = []

    def guarded_connect(sock, address):
        if use_mysql and isinstance(address, tuple) and address[:2] == ("127.0.0.1", 3306):
            return connect(sock, address)
        blocked_connections.append(address)
        raise AssertionError("External network is disabled in isolated acceptance")

    monkeypatch.setattr(socket.socket, "connect", guarded_connect)
    database = "bcsfuse_acceptance_" + uuid4().hex
    admin = None
    pool = None
    if use_mysql:
        import mysql.connector

        config = {"host": "127.0.0.1", "port": 3306, "user": "root", "password": ""}
        admin = mysql.connector.connect(**config)
        with admin.cursor() as cursor:
            cursor.execute(f"CREATE DATABASE `{database}`")

        class DisposablePool:
            def get_connection(self):
                return mysql.connector.connect(**config, database=database, autocommit=True)

            def close(self):
                # Connections are individually owned/closed by each store call.
                pass

        pool = DisposablePool()

    @contextmanager
    def open_app():
        from src.bootstrap.application_context import build_application_context
        from src.bootstrap.app_factory import create_bcsfuse_app
        from src.infra.public.vectorstores.qdrant_mysql_vector_store import QdrantMySQLVectorStore
        from src.infra.vectorstore_backends.sqlite_vector_persistence_backend import SQLiteVectorPersistenceBackend
        from src.interfaces.api.dependencies import fusion_dependencies

        fusion_dependencies.reset_fusion_services()
        monkeypatch.setenv("ENABLE_PROFILE_EMBEDDING_INDEX", "false")
        FeatureFlags.reset()
        context = build_application_context(mode="dev_smoke")
        registry = context.registry
        if pool:
            from src.infra.public.stores.mysql_worker_registry_store import MySQLWorkerRegistryStore
            from src.infra.public.stores.mysql_worker_runtime_state_store import MySQLWorkerRuntimeStateStore
            from src.infra.public.stores.mysql_worker_profile_content_store import MySQLWorkerProfileContentStore
            from src.infra.public.stores.mysql_worker_profile_binding_store import MySQLWorkerProfileBindingStore
            from src.infra.public.audit.mysql_worker_audit_log_store import MySQLWorkerAuditLogStore

            for key, provider in (
                ("worker_registry_store", MySQLWorkerRegistryStore),
                ("worker_runtime_state_store", MySQLWorkerRuntimeStateStore),
                ("worker_profile_content_store", MySQLWorkerProfileContentStore),
                ("worker_profile_binding_store", MySQLWorkerProfileBindingStore),
                ("worker_audit_log_store", MySQLWorkerAuditLogStore),
            ):
                close = getattr(registry.require(key), "close", None)
                if close:
                    close()
                if key == "worker_profile_binding_store":
                    instance = provider(pool, worker_registry_store=registry.require("worker_registry_store"))
                else:
                    instance = provider(pool)
                registry.register(key, instance)
        # Exercise the composed durable store, including its derived keyword index.
        vector_store = QdrantMySQLVectorStore(
            qdrant_path=str(tmp_path / "qdrant"), dimension=64,
            persistence_backend=SQLiteVectorPersistenceBackend(str(tmp_path / "vectors.sqlite")),
        )
        vector_store.rebuild_from_backend()
        embedding = DeterministicEmbedding()
        registry.register("vector_store", vector_store)
        registry.register("embedding_provider", embedding)
        app = create_bcsfuse_app(context)
        try:
            with TestClient(app) as client:
                # Enable request indexing after construction: no background rebuild race.
                monkeypatch.setenv("ENABLE_PROFILE_EMBEDDING_INDEX", "true")
                FeatureFlags.reset()
                yield client, registry, embedding
        finally:
            fusion_dependencies.reset_fusion_services()
            for key in registry.keys():
                provider = registry.get(key)
                close = getattr(provider, "close", None)
                if close:
                    close()

    try:
        yield open_app
    finally:
        if admin:
            with admin.cursor() as cursor:
                cursor.execute(f"DROP DATABASE `{database}`")
            admin.close()
        assert not blocked_connections, "Isolated acceptance attempted an external connection"


def test_http_lifecycle_and_gateway_contract(isolated_app_factory):
    with isolated_app_factory() as (client, registry, embedding):
        with acceptance_run(client, TOKEN) as acceptance:
            exercise_invalid_requests(acceptance)
            exercise_lifecycle(acceptance)
        assert embedding.calls, "HTTP lifecycle/search must invoke the test embedding provider"
        assert registry.require("worker_registry_store").count() == 0
        assert registry.require("vector_store").size() == 0


def test_fresh_app_reopens_profile_runtime_binding_and_vectors(isolated_app_factory):
    with isolated_app_factory() as (client, registry, embedding):
        from tests.fixtures.runtime_acceptance import RuntimeAcceptance

        acceptance = RuntimeAcceptance(client, TOKEN)
        worker_id = acceptance.create_worker()
        acceptance.put_profile(worker_id)
        acceptance.request("PUT", f"/v1/workers/{worker_id}/online")
        acceptance.request("PUT", f"/v1/workers/{worker_id}/profiles/release/activate")
        assert acceptance.search(worker_id)
        first_store = registry.require("worker_registry_store")

    with isolated_app_factory() as (client, registry, embedding):
        assert registry.require("worker_registry_store") is not first_store
        with acceptance_run(client, TOKEN) as reopened:
            reopened.worker_ids.append(worker_id)
            worker = reopened.request("GET", f"/v1/workers/{worker_id}")
            assert worker["runtime_state"] == "online"
            profile = reopened.request("GET", f"/v1/workers/{worker_id}/profiles/release")
            assert profile["is_active"] is True
            binding = registry.require("worker_profile_binding_store").get_active_binding(worker_id)
            assert binding.profile_key == f"{worker_id}:release"
            assert reopened.search(worker_id)


def test_cleanup_runs_after_interrupted_http_scenario(isolated_app_factory):
    with isolated_app_factory() as (client, registry, embedding):
        with pytest.raises(RuntimeError, match="scenario interrupted"):
            with acceptance_run(client, TOKEN) as acceptance:
                worker_id = acceptance.create_worker()
                acceptance.put_profile(worker_id)
                raise RuntimeError("scenario interrupted")
        assert registry.require("worker_registry_store").get_by_id(worker_id) is None
        assert registry.require("worker_profile_content_store").get(worker_id, "release") is None


def test_keyword_recommendation_http_metadata_and_missing_model_fallback(isolated_app_factory):
    with isolated_app_factory() as (client, registry, embedding):
        with acceptance_run(client, TOKEN) as acceptance:
            worker_id = acceptance.create_worker()
            acceptance.put_profile(worker_id)
            acceptance.request("PUT", f"/v1/workers/{worker_id}/online")
            acceptance.request("PUT", f"/v1/workers/{worker_id}/profiles/release/activate")
            body = {"question": worker_id, "topK": 3, "min_score": .95,
                    "enable_rerank": False, "filters": {"runtime_state": ["online"]}}
            baseline = acceptance.request("POST", "/api/v1/recommend", json=body)
            assert baseline["recommendations"][0]["worker_id"] == worker_id
            assert baseline["metadata"]["score_source"] == "hybrid_rrf"
            assert baseline["metadata"]["candidate_source"] == "hybrid"
            assert baseline["metadata"]["keyword_search_used"] is True
            assert baseline["metadata"]["rerank_degraded"] is False
            fallback = acceptance.request("POST", "/api/v1/recommend", json={**body, "enable_rerank": True})
            assert [(r["profile_key"], r["score"]) for r in fallback["recommendations"]] == [
                (r["profile_key"], r["score"]) for r in baseline["recommendations"]
            ]
            assert fallback["metadata"]["score_source"] == "hybrid_rrf"
            assert fallback["metadata"]["rerank_degraded"] is True
