"""Online vector writes respect a newer completed lifecycle transition."""

import pytest
from app.application.services.worker_runtime_state_service import (
    WorkerRuntimeStateService,
)
from app.domain.models.worker import Worker, WorkerIdentity, WorkerState, WorkerType
from app.domain.models.worker_lifecycle_state import WorkerLifecycleState
from app.domain.models.worker_runtime_state import WorkerRuntimeState
from app.infra.adapters.in_memory_worker_audit_log_store import (
    InMemoryWorkerAuditLogStore,
)
from app.infra.adapters.in_memory_worker_index_sync_adapter import (
    InMemoryWorkerIndexSyncAdapter,
)
from app.infra.adapters.in_memory_worker_registry_store import (
    InMemoryWorkerRegistryStore,
)
from app.infra.adapters.in_memory_worker_runtime_state_store import (
    InMemoryWorkerRuntimeStateStore,
)


class VectorStore:
    def __init__(self):
        self.payload = {"worker_id": "bot:owner", "runtime_state": "offline"}

    def update_payload_by_worker(self, worker_id, updates):
        assert worker_id == self.payload["worker_id"]
        self.payload.update(updates)
        return 1


@pytest.fixture
def stores():
    registry = InMemoryWorkerRegistryStore()
    registry.create(Worker(
        id="bot:owner", type=WorkerType.BOT,
        identity=WorkerIdentity(name="Bot", handle="@bot"),
        lifecycle_state=WorkerLifecycleState.ACTIVE,
        responsibilities=["testing"], capabilities=[], state=WorkerState(availability="public", trust_level="trusted"),
    ))
    runtime = InMemoryWorkerRuntimeStateStore()
    runtime.set_runtime_state("bot:owner", WorkerRuntimeState.OFFLINE)
    index_sync = InMemoryWorkerIndexSyncAdapter()
    vectors = VectorStore()
    service = WorkerRuntimeStateService(
        registry, runtime, InMemoryWorkerAuditLogStore(), index_sync, vectors,
    )
    return service, registry, runtime, index_sync, vectors


@pytest.mark.parametrize("newer_state", ["offline", "disabled", "inactive"])
def test_online_does_not_publish_vectors_after_newer_transition(stores, monkeypatch, newer_state):
    service, registry, runtime, index_sync, vectors = stores
    completed = []

    def finish_newer_transition(worker_id, old_state, new_state):
        if new_state != WorkerRuntimeState.ONLINE:
            return
        if newer_state == "offline":
            service.set_offline(worker_id)
        else:
            worker = registry.get_by_id(worker_id)
            worker.lifecycle_state = (
                WorkerLifecycleState.DISABLED if newer_state == "disabled"
                else WorkerLifecycleState.INACTIVE
            )
            registry.update(worker)
        completed.append(newer_state)

    monkeypatch.setattr(index_sync, "on_runtime_state_changed", finish_newer_transition)
    service.set_online("bot:owner")
    assert completed == [newer_state]
    assert vectors.payload["runtime_state"] == "offline"
    if newer_state == "offline":
        assert runtime.get_runtime_state("bot:owner") == WorkerRuntimeState.OFFLINE


def test_current_online_worker_still_updates_vectors(stores):
    service, _, runtime, _, vectors = stores
    service.set_online("bot:owner")
    assert runtime.get_runtime_state("bot:owner") == WorkerRuntimeState.ONLINE
    assert vectors.payload["runtime_state"] == "online"


def test_online_vector_write_failure_still_propagates(stores, monkeypatch):
    service, _, _, _, vectors = stores

    def fail_write(*args):
        raise RuntimeError("vector persistence failed")

    monkeypatch.setattr(vectors, "update_payload_by_worker", fail_write)
    with pytest.raises(RuntimeError, match="vector persistence failed"):
        service.set_online("bot:owner")
