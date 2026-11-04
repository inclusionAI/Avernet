"""TDD contract for short durable index leases and audited owner takeover."""

from __future__ import annotations

import logging
from typing import Any

from app.bootstrap.opensource import resolve_index_lease_settings
from app.domain.models.index_state import IndexStateRecord
from app.infra.public.stores.sqlite_index_state_store import SQLiteIndexStateStore


class RecordingBackend:
    def __init__(self) -> None:
        self.points: list[str] = []

    def upsert(self, points, **kwargs):
        self.points.extend(points)

    def check_ready(self) -> bool:
        return True

    def rebuild_from_backend(self, *args: Any, **kwargs: Any):
        return {"points": len(self.points)}

    def close(self) -> None:
        return None


class MappingConfig:
    def __init__(self, values: dict[str, float]) -> None:
        self._values = values

    def get_float(self, key: str, default: float) -> float:
        return self._values.get(key, default)


def test_default_index_lease_is_bounded_for_fast_takeover() -> None:
    settings = resolve_index_lease_settings(MappingConfig({}))

    assert settings.stale_after_seconds == 20.0
    assert settings.lease_seconds == 30.0
    assert settings.heartbeat_seconds == 10.0


def test_index_lease_settings_can_be_overridden_without_slowing_defaults() -> None:
    settings = resolve_index_lease_settings(
        MappingConfig(
            {
                "vector.index.stale_after_seconds": 15.0,
                "vector.index.lease_seconds": 25.0,
            }
        )
    )

    assert settings.stale_after_seconds == 15.0
    assert settings.lease_seconds == 25.0
    assert settings.heartbeat_seconds == 25.0 / 3


def test_runtime_composition_uses_short_lease_defaults(monkeypatch) -> None:
    from app.bootstrap.opensource import build_opensource_provider_registry

    class NoopMySQLDependency:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            return None

    class ConfiguredIndexStateStore:
        instances: list["ConfiguredIndexStateStore"] = []

        def __init__(self, *args: Any, **kwargs: Any) -> None:
            self.instances.append(self)

        def claim_owner(self, collection_pair, owner_id, *, lease_seconds):
            return IndexStateRecord(
                collection_pair=collection_pair,
                owner_id=owner_id,
                owner_expires_at=lease_seconds,
                index_version=0,
                index_updated_at=1.0,
                observed_at=1.0,
            )

        def read_owner(self, collection_pair):
            return IndexStateRecord(
                collection_pair=collection_pair,
                owner_id="pod-owner",
                owner_expires_at=30.0,
                index_version=0,
                index_updated_at=1.0,
                observed_at=1.0,
            )

        def release_owner(self, collection_pair, owner_id) -> None:
            return None

    import importlib

    mysql_modules = [
        "app.infra.public.database.mysql_connection_pool",
        "app.infra.public.stores.mysql_worker_registry_store",
        "app.infra.public.stores.mysql_worker_runtime_state_store",
        "app.infra.public.stores.mysql_worker_profile_content_store",
        "app.infra.public.stores.mysql_worker_profile_binding_store",
        "app.infra.public.stores.mysql_fused_profile_store",
        "app.infra.public.audit.mysql_worker_audit_log_store",
    ]
    for module_name in mysql_modules:
        module = importlib.import_module(module_name)
        for name in vars(module):
            if name.startswith("MySQL") or name.endswith("PoolProvider"):
                monkeypatch.setattr(module, name, NoopMySQLDependency)

    state_store_module = importlib.import_module(
        "app.infra.public.stores.mysql_index_state_store"
    )
    monkeypatch.setattr(
        state_store_module, "MySQLIndexStateStore", ConfiguredIndexStateStore
    )
    monkeypatch.setattr(
        "app.bootstrap.opensource._with_literal_fallback", lambda store, snapshot_store=None: store
    )
    embedding_base_env = "EMBEDDING_" + "BASE_URL"
    embedding_credential_env = "EMBEDDING_" + "AUTH_TOKEN"
    embedding_credential = "isolated-" + "test-token"
    monkeypatch.setenv(embedding_base_env, "http://127.0.0.1:1")
    monkeypatch.setenv(embedding_credential_env, embedding_credential)

    llm_base_env = "LLM_" + "BASE_URL"
    llm_credential_env = "LLM_" + "AUTH_TOKEN"
    llm_credential = "isolated-" + "test-token"
    monkeypatch.setenv(llm_base_env, "http://127.0.0.1:1")
    monkeypatch.setenv(llm_credential_env, llm_credential)
    reranker_base_env = "RERANKER_" + "BASE_URL"
    reranker_credential_env = "RERANKER_" + "API_KEY"
    reranker_credential = "isolated-" + "test-token"
    monkeypatch.setenv(reranker_base_env, "http://127.0.0.1:1")
    monkeypatch.setenv(reranker_credential_env, reranker_credential)

    monkeypatch.setenv("QDRANT_MODE", "server")
    monkeypatch.setenv("QDRANT_URL", "http://127.0.0.1:6333")
    monkeypatch.setenv("QDRANT_PREFER_GRPC", "false")
    monkeypatch.setenv(
        "BCSFUSE_LITERAL_FALLBACK_SNAPSHOT_PATH",
        "/var/lib/bcsfuse/bcsfuse-contract-snapshot.json",
    )

    registry = build_opensource_provider_registry(mode="runtime")
    vector_store = registry.get("vector_store")

    try:
        assert vector_store.stale_after_seconds == 20.0
        assert vector_store.lease_seconds == 30.0
        assert vector_store.lease_heartbeat_seconds == 10.0
    finally:
        vector_store.close()


def owner_wrapper(state_store, owner_id: str, *, stale=20, lease=30):
    from app.application.services.index_state_vector_store import (
        IndexStateMaintainingVectorStore,
    )

    return IndexStateMaintainingVectorStore(
        RecordingBackend(),
        store=state_store,
        owner_id=owner_id,
        collection_pair="profiles:dense+keyword",
        stale_after_seconds=stale,
        lease_seconds=lease,
    )


def test_expired_owner_can_be_taken_over_within_default_lease_window(
    tmp_path, monkeypatch
) -> None:
    from app.infra.public.stores import sqlite_index_state_store

    monkeypatch.setattr(sqlite_index_state_store.time, "time", lambda: 1000.0)
    state_store = SQLiteIndexStateStore(str(tmp_path / "index-state.sqlite"))
    first = owner_wrapper(state_store, "pod-a")

    monkeypatch.setattr(
        sqlite_index_state_store.time, "time", lambda: 1029.9
    )
    stale_reader = owner_wrapper(state_store, "pod-b")
    assert stale_reader.read_only is True

    monkeypatch.setattr(sqlite_index_state_store.time, "time", lambda: 1030.0)
    takeover = owner_wrapper(state_store, "pod-c")

    try:
        assert first.owner_id == "pod-a"
        assert stale_reader.read_only is True
        assert takeover.owner_id == "pod-c"
        assert takeover.read_only is False
    finally:
        first.close()
        stale_reader.close()
        takeover.close()


def test_owner_change_is_audited_and_exposes_takeover_metrics(
    tmp_path, caplog
) -> None:
    from app.application.services.index_state_vector_store import (
        IndexStateMaintainingVectorStore,
    )

    class ExpiringOwnerStateStore:
        def __init__(self) -> None:
            self.owner_id = "pod-a"
            self.observed_at = 100.0

        def claim_owner(self, collection_pair, owner_id, *, lease_seconds):
            return self._record(self.owner_id)

        def read_owner(self, collection_pair):
            return self._record(self.owner_id)

        def change_owner(self, owner_id: str, observed_at: float) -> None:
            self.owner_id = owner_id
            self.observed_at = observed_at

        def _record(self, owner_id: str):
            return IndexStateRecord(
                collection_pair="profiles:dense+keyword",
                owner_id=owner_id,
                owner_expires_at=self.observed_at + 30.0,
                index_version=1,
                index_updated_at=self.observed_at,
                observed_at=self.observed_at,
            )

    caplog.clear()
    with caplog.at_level(logging.INFO):
        state_store = ExpiringOwnerStateStore()
        owner = IndexStateMaintainingVectorStore(
            RecordingBackend(),
            store=state_store,
            owner_id="pod-b",
            collection_pair="profiles:dense+keyword",
            stale_after_seconds=20,
            lease_seconds=30,
        )

        state_store.change_owner("pod-c", 131.0)
        owner.renew_owner_lease()

    assert owner.owner_id == "pod-c"
    assert owner.index_owner_changes == 1
    assert owner.index_owner_takeover_seconds == 31.0
    assert "owner changed" in caplog.text
    assert "old=pod-a" in caplog.text
    assert "new=pod-c" in caplog.text


def test_readiness_and_metrics_expose_owner_change_audit(tmp_path) -> None:
    from fastapi.testclient import TestClient

    from app.bootstrap.app_factory import create_bcsfuse_app
    from app.bootstrap.application_context import ApplicationContext
    from app.bootstrap.provider_registry import ProviderRegistry

    class StartupProvider:
        async def initialize(self) -> None:
            return None

        async def shutdown(self) -> None:
            return None

    class AuditedStore:
        connection = type("Connection", (), {"mode": "server"})()
        keyword_connection = type("Connection", (), {"mode": "server"})()
        collection_name = "worker_profiles"
        index_owner = "pod-c"
        index_version = 1
        index_lag_seconds = 0.0
        index_stale = False
        index_state_unavailable = False
        read_only = False
        stale_after_seconds = 20.0
        lease_seconds = 30.0
        lease_heartbeat_seconds = 10.0
        index_owner_changes = 1
        index_owner_takeover_seconds = 31.0

        def check_ready(self) -> bool:
            return True

    registry = ProviderRegistry()
    registry.register("config", object())
    registry.register("secret_provider", object())
    registry.register("startup_provider", StartupProvider())
    registry.register("vector_store", AuditedStore())
    app = create_bcsfuse_app(
        ApplicationContext(
            mode="runtime",
            startup_profile="opensource",
            registry=registry,
        )
    )

    with TestClient(app) as client:
        ready = client.get("/ready").json()
        metrics = client.get("/metrics").text

    assert ready["index_owner_changes"] == 1
    assert ready["index_owner_takeover_seconds"] == 31.0
    assert "bcsfuse_index_owner_changes" in metrics
    assert "bcsfuse_index_owner_takeover_seconds" in metrics
