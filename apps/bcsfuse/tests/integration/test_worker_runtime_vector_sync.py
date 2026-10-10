"""Runtime transitions update every owned profile without re-embedding."""

from src.domain.models.vector_point import VectorPoint
from tests.fixtures.runtime_acceptance import acceptance_run
from tests.integration.test_isolated_runtime_acceptance import TOKEN, isolated_app_factory  # noqa: F401


def test_local_faiss_state_sync_survives_reopen(tmp_path):
    from src.application.services.worker_vector_state_sync import sync_worker_vector_state
    from src.infra.public.vectorstores.faiss_sqlite_vector_store import FaissSqliteVectorStore

    path = str(tmp_path / "vectors.sqlite")
    store = FaissSqliteVectorStore(dimension=3, db_path=path)
    store.upsert([VectorPoint(id="bot:owner:default:full", vector=[1., 0., 0.], payload={
        "worker_id": "bot:owner", "runtime_state": "online", "content": "preserved",
    })])
    try:
        assert sync_worker_vector_state(store, "bot:owner", "offline") == 1
    finally:
        store._backend._conn.close()
    reopened = FaissSqliteVectorStore(dimension=3, db_path=path)
    try:
        point = reopened._faiss_store.get("bot:owner:default:full")
        assert point.payload == {"worker_id": "bot:owner", "runtime_state": "offline", "content": "preserved"}
        assert point.vector == [1., 0., 0.]
    finally:
        reopened._backend._conn.close()


def test_runtime_transitions_preserve_all_profiles_and_exact_owner(isolated_app_factory):
    with isolated_app_factory() as (client, registry, embedding):
        with acceptance_run(client, TOKEN) as acceptance:
            worker_id = acceptance.create_worker()
            acceptance.request("PUT", f"/v1/workers/{worker_id}/online")
            acceptance.put_profile(worker_id)
            acceptance.request("PUT", f"/v1/workers/{worker_id}/profiles/release/activate")
            store = registry.require("vector_store")
            original = {key: store.get(key) for key in store.get_vector_ids()}
            assert {point.payload["profile_id"] for point in original.values()} == {"default", "release"}
            neighbor = f"{worker_id}:nested:default:skills:1"
            store.upsert([VectorPoint(id=neighbor, vector=[1.0] + [0.0] * 63, payload={
                "worker_id": f"{worker_id}:nested", "runtime_state": "online",
            })])
            try:
                for state in ("offline", "online"):
                    for _ in range(2):
                        before = len(embedding.calls)
                        acceptance.request("PUT", f"/v1/workers/{worker_id}/{state}")
                        assert len(embedding.calls) == before
                        assert set(store.get_vector_ids()) == set(original) | {neighbor}
                        for key, point in original.items():
                            current = store.get(key)
                            assert current.payload == {**point.payload, "runtime_state": state}
                            assert current.vector == point.vector
                        assert store.get(neighbor).payload["runtime_state"] == "online"
                    found = {item["profile_key"] for item in acceptance.search(worker_id)}
                    assert found == (set() if state == "offline" else {
                        f"{worker_id}:default", f"{worker_id}:release",
                    })
            finally:
                store.delete(neighbor)


def test_failed_vector_write_is_reported_and_same_state_retry_repairs_it(isolated_app_factory, monkeypatch):
    with isolated_app_factory() as (client, registry, _embedding):
        with acceptance_run(client, TOKEN) as acceptance:
            worker_id = acceptance.create_worker()
            acceptance.request("PUT", f"/v1/workers/{worker_id}/online")
            store = registry.require("vector_store")
            def fail(*args, **kwargs):
                raise RuntimeError("injected vector write failure")
            with monkeypatch.context() as patch:
                patch.setattr(store, "upsert", fail)
                response = acceptance.request("PUT", f"/v1/workers/{worker_id}/offline", expected=500)
                assert response["detail"]["code"] == "RUNTIME_STATE_UPDATE_ERROR"
            assert registry.require("worker_registry_store").get_by_id(worker_id).state.runtime_state.value == "offline"
            acceptance.request("PUT", f"/v1/workers/{worker_id}/offline")
            assert not acceptance.search(worker_id)


def test_delete_clears_generated_vectors_without_profile_content_rows(isolated_app_factory):
    with isolated_app_factory() as (client, registry, _embedding):
        with acceptance_run(client, TOKEN) as acceptance:
            worker_id = acceptance.create_worker()
            acceptance.request("PUT", f"/v1/workers/{worker_id}/online")
            assert registry.require("worker_profile_content_store").list_by_worker(worker_id).total == 0
            assert acceptance.search(worker_id)
            acceptance.request("DELETE", f"/v1/workers/{worker_id}")
            assert not acceptance.search(worker_id)


def test_durable_payload_sync_includes_vectors_not_yet_in_local_index(tmp_path):
    from src.application.services.worker_runtime_state_service import WorkerRuntimeStateService
    from src.domain.models.worker import Worker, WorkerIdentity, WorkerState, WorkerType
    from src.domain.models.worker_runtime_state import WorkerRuntimeState
    from src.infra.adapters.in_memory_worker_registry_store import InMemoryWorkerRegistryStore
    from src.infra.adapters.in_memory_worker_runtime_state_store import InMemoryWorkerRuntimeStateStore
    from src.infra.public.vectorstores.qdrant_mysql_vector_store import QdrantMySQLVectorStore
    from tests.unit.infra.test_qdrant_durable_vector_store import FakePersistenceBackend

    backend = FakePersistenceBackend()
    store = QdrantMySQLVectorStore(dimension=3, qdrant_path=str(tmp_path), persistence_backend=backend)
    workers = InMemoryWorkerRegistryStore()
    states = InMemoryWorkerRuntimeStateStore()
    worker_id = "bot:owner"
    workers.create(Worker(id=worker_id, type=WorkerType.BOT,
                          identity=WorkerIdentity(name="Bot", handle="@bot"),
                          responsibilities=[], capabilities=[],
                          state=WorkerState(availability="public", trust_level="trusted",
                                            runtime_state=WorkerRuntimeState.ONLINE)))
    states.set_runtime_state(worker_id, WorkerRuntimeState.ONLINE)
    for key, owner in [("bot:owner:old:full", worker_id), ("bot:owner:release:skills:1", worker_id),
                       ("bot:owner:nested:default:full", "bot:owner:nested")]:
        backend.save(VectorPoint(id=key, vector=[1., 0., 0.], payload={
            "worker_id": owner, "runtime_state": "online", "content": "retained",
        }))
    from src.infra.adapters.in_memory_worker_audit_log_store import InMemoryWorkerAuditLogStore
    from src.infra.adapters.in_memory_worker_index_sync_adapter import InMemoryWorkerIndexSyncAdapter
    service = WorkerRuntimeStateService(
        registry_store=workers, runtime_state_store=states, vector_store=store,
        audit_log_adapter=InMemoryWorkerAuditLogStore(), index_sync_adapter=InMemoryWorkerIndexSyncAdapter(),
    )
    try:
        for state in ("offline", "online"):
            getattr(service, f"set_{state}")(worker_id)
            assert len(backend.points) == 3
            for point in backend.load_all():
                assert point.payload["runtime_state"] == (state if point.payload["worker_id"] == worker_id else "online")
                assert point.vector == [1., 0., 0.]
                assert point.payload["content"] == "retained"
            store.rebuild_from_backend()
            assert len(store.search([1., 0., 0.], top_k=10, filters={
                "worker_id": worker_id, "runtime_state": "online",
            })) == (0 if state == "offline" else 2)
    finally:
        store.close()
