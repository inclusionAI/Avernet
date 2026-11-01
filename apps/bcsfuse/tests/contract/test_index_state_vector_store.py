"""Durable ownership and staleness behavior around the composed vector store."""

import pytest
from fastapi.testclient import TestClient

from app.application.services.index_state_vector_store import (
    IndexStateMaintainingVectorStore,
    IndexStateUnavailable,
    IndexStateWriteForbidden,
    wrap_vector_store_with_index_state,
)
from app.domain.models.index_state import IndexStateRecord
from app.infra.public.stores.sqlite_index_state_store import SQLiteIndexStateStore


class RecordingVectorStore:
    def __init__(self, *, ready=True, fail=False):
        self.ready = ready
        self.fail = fail
        self.points = []
        self.deleted = []
        self.rebuilds = 0
        self.syncs = 0
        self.closed = False

    def _maybe_fail(self):
        if self.fail:
            raise RuntimeError("index operation failed")

    def upsert(self, points, **kwargs):
        self._maybe_fail()
        self.points.extend(points)
        return None

    def delete(self, ids, **kwargs):
        self._maybe_fail()
        self.deleted.extend(ids)
        return len(ids)

    def update_payload_by_worker(self, worker_id, updates):
        self._maybe_fail()
        return 1

    def delete_by_worker(self, worker_id):
        self._maybe_fail()
        return 1

    def delete_by_profile(self, worker_id, profile_id):
        self._maybe_fail()
        return 1

    def clear(self):
        self._maybe_fail()
        self.points.clear()

    def rebuild_from_backend(self):
        self._maybe_fail()
        self.rebuilds += 1
        return {"points": len(self.points)}

    def sync_incremental(self):
        self._maybe_fail()
        self.syncs += 1
        return {"upserted": 0, "deleted": 0}

    def check_ready(self):
        return self.ready

    def close(self):
        self.closed = True


def wrapped(tmp_path, owner_id, backend=None, **kwargs):
    store = SQLiteIndexStateStore(str(tmp_path / "index-state.sqlite"))
    return wrap_vector_store_with_index_state(
        backend or RecordingVectorStore(),
        store=store,
        owner_id=owner_id,
        collection_pair="profiles:dense+keyword",
        stale_after_seconds=60,
        lease_seconds=120,
        **kwargs,
    )


class OldEmptyIndexStateStore:
    """Simulates a durable empty index that was never updated or synchronized."""

    def __init__(self, owner_id):
        self.owner_id = owner_id
        self.claims = 0

    def claim_owner(self, collection_pair, owner_id, *, lease_seconds):
        self.claims += 1
        return self._record(owner_id)

    def read_owner(self, collection_pair):
        return self._record(self.owner_id)

    @staticmethod
    def _record(owner_id):
        return IndexStateRecord(
            collection_pair="profiles:dense+keyword",
            owner_id=owner_id,
            owner_expires_at=103.0,
            index_version=0,
            index_updated_at=100.0,
            observed_at=200.0,
        )


class NoneReadyVectorStore(RecordingVectorStore):
    def check_ready(self):
        return None


def test_successful_none_ready_probe_remains_ready(tmp_path):
    owner = wrapped(tmp_path, "pod-a", backend=NoneReadyVectorStore())

    assert owner.check_ready() is True


class OldCurrentIndexStateStore:
    """A reader that is synchronized to the current shared revision."""

    def __init__(self, owner_id):
        self.owner_id = owner_id

    def claim_owner(self, collection_pair, owner_id, *, lease_seconds):
        return self._record(owner_id)

    def read_owner(self, collection_pair):
        return self._record(self.owner_id)

    @staticmethod
    def _record(owner_id):
        return IndexStateRecord(
            collection_pair="profiles:dense+keyword",
            owner_id=owner_id,
            owner_expires_at=103.0,
            index_version=1,
            index_updated_at=100.0,
            observed_at=200.0,
        )


def test_synchronized_reader_has_zero_revision_lag(tmp_path):
    reader = IndexStateMaintainingVectorStore(
        RecordingVectorStore(),
        store=OldCurrentIndexStateStore("pod-a"),
        owner_id="pod-b",
        collection_pair="profiles:dense+keyword",
        stale_after_seconds=2,
        lease_seconds=3,
    )

    result = reader.synchronize_read_view()

    assert result == {"read_only": True, "synchronized_version": 1}
    assert reader.index_lag_seconds == 0.0
    assert reader.index_stale is False


def test_empty_index_age_is_not_staleness(tmp_path):
    owner = IndexStateMaintainingVectorStore(
        RecordingVectorStore(),
        store=OldEmptyIndexStateStore("pod-a"),
        owner_id="pod-a",
        collection_pair="profiles:dense+keyword",
        stale_after_seconds=2,
        lease_seconds=3,
    )
    reader = IndexStateMaintainingVectorStore(
        RecordingVectorStore(),
        store=OldEmptyIndexStateStore("pod-a"),
        owner_id="pod-b",
        collection_pair="profiles:dense+keyword",
        stale_after_seconds=2,
        lease_seconds=3,
    )

    assert owner.index_lag_seconds == 0.0
    assert owner.index_stale is False
    assert reader.index_lag_seconds == 0.0
    assert reader.index_stale is False


def test_first_replica_acquires_ownership_and_publishes_versions(tmp_path):
    owner = wrapped(tmp_path, "pod-a")

    assert owner.index_owner == "pod-a"
    assert owner.read_only is False
    owner.upsert(["point"])
    assert owner.index_version == 1

    owner.close()
    assert owner.backend.closed is True


def test_second_replica_is_read_only_and_cannot_reach_storage(tmp_path):
    first = wrapped(tmp_path, "pod-a")
    second = wrapped(tmp_path, "pod-b")

    assert first.index_owner == "pod-a"
    assert second.index_owner == "pod-a"
    assert second.read_only is True

    with pytest.raises(IndexStateWriteForbidden):
        second.upsert(["point"])

    assert second.backend.points == []
    first.close()
    second.close()


def test_release_enables_second_replica_takeover_after_owner_leases(tmp_path):
    first = wrapped(tmp_path, "pod-a")
    first.close()
    second = wrapped(tmp_path, "pod-b")

    assert second.index_owner == "pod-b"
    assert second.read_only is False
    second.close()


def test_owner_version_change_marks_reader_stale_until_rebuild(tmp_path):
    first = wrapped(tmp_path, "pod-a")
    second = wrapped(tmp_path, "pod-b")
    first.upsert(["point"])
    second.check_ready()

    assert second.index_version == 1
    assert second.index_lag_seconds >= 0
    assert second.index_stale is True

    assert second.synchronize_read_view() == {
        "read_only": True,
        "synchronized_version": 1,
    }
    assert second.backend.rebuilds == 0
    assert second.index_stale is False

    first.close()
    second.close()


def test_backend_failure_does_not_publish_a_new_index_version(tmp_path):
    owner = wrapped(tmp_path, "pod-a")
    owner.backend.fail = True

    with pytest.raises(RuntimeError, match="index operation failed"):
        owner.upsert(["point"])
    owner.backend.fail = False
    owner.check_ready()
    assert owner.index_version == 0
    owner.close()


def test_state_store_outage_is_recoverable_and_explicit(tmp_path):
    owner = wrapped(tmp_path, "pod-a")
    store = owner._store
    original_claim = store.claim_owner

    assert owner.index_state_unavailable is False

    def failing_claim(*args, **kwargs):
        raise RuntimeError("state unavailable")

    store.claim_owner = failing_claim
    with pytest.raises(
        IndexStateUnavailable, match="index owner lease renewal failed"
    ):
        owner.renew_owner_lease()

    assert owner.index_state_unavailable is True

    store.claim_owner = original_claim
    assert owner.renew_owner_lease() is True
    assert owner.index_state_unavailable is False

    owner.close()


def test_state_store_failure_block_writes_and_marks_index_stale(tmp_path):
    backend = RecordingVectorStore()
    state_store = SQLiteIndexStateStore(str(tmp_path / "state.sqlite"))
    state_store.claim_owner = lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("state unavailable"))
    owner = IndexStateMaintainingVectorStore(
        backend,
        store=state_store,
        owner_id="pod-a",
        collection_pair="profiles:dense+keyword",
        stale_after_seconds=60,
        lease_seconds=120,
    )

    with pytest.raises(IndexStateUnavailable):
        owner.upsert(["point"])

    assert backend.points == []
    assert owner.read_only is True
    assert owner.index_stale is True


def test_other_direct_write_methods_use_the_same_ownership_boundary(tmp_path):
    owner = wrapped(tmp_path, "pod-a")
    reader = wrapped(tmp_path, "pod-b")

    methods = [
        ("upsert", ("point",)),
        ("delete", ("point",)),
        ("update_payload_by_worker", ("worker", {"runtime_state": "offline"})),
        ("delete_by_worker", ("worker",)),
        ("delete_by_profile", ("worker", "default")),
        ("clear", ()),
    ]
    for name, args in methods:
        with pytest.raises(IndexStateWriteForbidden):
            getattr(reader, name)(*args)

    assert reader.backend.points == []
    assert reader.backend.deleted == []
    owner.close()
    reader.close()


def test_composed_readiness_and_lifecycle_view_owner_and_version(tmp_path):
    from app.application.services.literal_fallback_maintenance import (
        wrap_vector_store_with_literal_fallback,
    )
    from app.bootstrap.application_context import ApplicationContext
    from app.bootstrap.app_factory import create_bcsfuse_app
    from app.bootstrap.provider_registry import ProviderRegistry

    class StartupProvider:
        async def initialize(self):
            return None

        async def shutdown(self):
            return None

    owner = wrapped(tmp_path, "pod-a")
    composed = wrap_vector_store_with_literal_fallback(owner)
    registry = ProviderRegistry()
    registry.register("config", object())
    registry.register("secret_provider", object())
    registry.register("startup_provider", StartupProvider())
    registry.register("vector_store", composed)
    app = create_bcsfuse_app(
        ApplicationContext(
            mode="runtime",
            startup_profile="opensource",
            registry=registry,
        )
    )

    with TestClient(app) as client:
        response = client.get("/ready")
        assert response.status_code == 200
        body = response.json()
        assert body["index_owner"] == "pod-a"
        assert body["index_version"] == 0
        assert body["index_stale"] is False
        assert body["read_only"] is False


class SharedPoint:
    def __init__(self, point_id: str, payload: dict):
        self.id = point_id
        self.payload = payload


class SharedViewVectorStore(RecordingVectorStore):
    def __init__(self):
        super().__init__()
        self.view_loads = 0

    def get_vector_ids(self):
        self.view_loads += 1
        return [point.id for point in self.points]

    def get(self, point_id):
        for point in self.points:
            if point.id == point_id:
                return point
        return None


def _make_point(profile_key="worker:default"):
    return SharedPoint(
        f"{profile_key}:full",
        {
            "profile_key": profile_key,
            "worker_id": profile_key.rsplit(":", 1)[0],
            "profile_id": profile_key.rsplit(":", 1)[1],
            "content": "支付 账单 助手",
            "runtime_state": "online",
            "availability": "public",
        },
    )


def test_owner_resumes_from_the_latest_shared_revision(tmp_path):
    import sqlite3
    import time

    backend = RecordingVectorStore()
    state_store = SQLiteIndexStateStore(str(tmp_path / "index-state.sqlite"))
    collection_pair = "profiles:dense+keyword"

    def wrapped_owner(owner_id: str):
        return wrap_vector_store_with_index_state(
            backend,
            store=state_store,
            owner_id=owner_id,
            collection_pair=collection_pair,
            stale_after_seconds=60,
            lease_seconds=120,
        )

    def expire_active_owner():
        connection = sqlite3.connect(state_store.db_path)
        try:
            connection.execute(
                "UPDATE bcsfuse_index_states SET owner_expires_at = ? "
                "WHERE collection_pair = ?",
                (time.time() - 0.001, collection_pair),
            )
            connection.commit()
        finally:
            connection.close()

    owner = wrapped_owner("pod-a")
    owner.upsert(["first"])

    expire_active_owner()
    takeover = wrapped_owner("pod-b")
    takeover.upsert(["second"])
    state_store.release_owner(collection_pair, "pod-b")

    owner.upsert(["resumed"])

    assert owner.index_version == 3
    assert state_store.read_owner(collection_pair).index_version == 3
    assert backend.points == ["first", "second", "resumed"]
    owner.close()


def test_read_only_rebuild_is_rejected_before_backend_rebuild(tmp_path):
    backend = SharedViewVectorStore()
    owner = wrapped(tmp_path, "pod-a", backend=backend)
    reader = wrapped(tmp_path, "pod-b", backend=backend)

    with pytest.raises(IndexStateWriteForbidden, match="owned by 'pod-a'"):
        reader.rebuild_from_backend()

    assert backend.rebuilds == 0
    assert reader.read_only is True
    owner.close()
    reader.close()


def test_read_only_incremental_sync_is_rejected_before_backend_sync(tmp_path):
    backend = SharedViewVectorStore()
    owner = wrapped(tmp_path, "pod-a", backend=backend)
    reader = wrapped(tmp_path, "pod-b", backend=backend)

    with pytest.raises(IndexStateWriteForbidden, match="owned by 'pod-a'"):
        reader.sync_incremental()

    assert backend.syncs == 0
    owner.close()
    reader.close()


def test_composed_reader_rebuild_synchronizes_view_without_mutating_qdrant(
    tmp_path,
):
    from app.application.services.literal_fallback_maintenance import (
        wrap_vector_store_with_literal_fallback,
    )

    backend = SharedViewVectorStore()
    owner_composed = wrap_vector_store_with_literal_fallback(
        wrapped(tmp_path, "pod-a", backend=backend)
    )
    reader_composed = wrap_vector_store_with_literal_fallback(
        wrapped(tmp_path, "pod-b", backend=backend)
    )

    owner_composed.upsert([_make_point()])
    assert reader_composed.check_ready() is True
    assert reader_composed.index_stale is False
    assert reader_composed.index_revision_stale is False
    rebuilds_before = backend.rebuilds

    result = reader_composed.rebuild_from_backend()

    assert reader_composed.index_stale is False
    assert reader_composed.read_only is True
    assert backend.rebuilds == rebuilds_before
    assert result == {
        "read_only": True,
        "synchronized_version": 1,
    }
    hits = reader_composed.literal_fallback_index.keyword_search(
        "支付 账单",
        1,
        {"runtime_state": ("online",), "availability": ("public",)},
        None,
    )
    assert [hit.payload["profile_key"] for hit in hits] == ["worker:default"]

    owner_composed.close()
    reader_composed.close()
    assert backend.closed is True


def test_composed_owner_rebuild_still_executes_backend_rebuild(tmp_path):
    from app.application.services.literal_fallback_maintenance import (
        wrap_vector_store_with_literal_fallback,
    )

    backend = SharedViewVectorStore()
    owner_composed = wrap_vector_store_with_literal_fallback(
        wrapped(tmp_path, "pod-a", backend=backend)
    )

    owner_composed.rebuild_from_backend()

    assert backend.rebuilds == 1
    assert owner_composed.read_only is False
    owner_composed.close()

def test_composed_reader_readiness_recovers_revision_without_manual_restart(
    tmp_path,
):
    from app.application.services.literal_fallback_maintenance import (
        wrap_vector_store_with_literal_fallback,
    )

    backend = SharedViewVectorStore()
    owner_composed = wrap_vector_store_with_literal_fallback(
        wrapped(tmp_path, "pod-a", backend=backend)
    )
    reader_composed = wrap_vector_store_with_literal_fallback(
        wrapped(tmp_path, "pod-b", backend=backend)
    )

    owner_composed.upsert([_make_point()])
    view_loads_start = backend.view_loads
    rebuilds_start = backend.rebuilds

    assert reader_composed.check_ready() is True

    assert reader_composed.index_stale is False
    assert reader_composed.index_revision_stale is False
    assert backend.rebuilds == rebuilds_start
    assert backend.view_loads == view_loads_start + 1

    assert reader_composed.check_ready() is True
    assert backend.view_loads == view_loads_start + 1

    hits = reader_composed.literal_fallback_index.keyword_search(
        "支付 账单",
        1,
        {"runtime_state": ("online",), "availability": ("public",)},
        None,
    )
    assert [hit.payload["profile_key"] for hit in hits] == ["worker:default"]

    owner_composed.close()
    reader_composed.close()


def test_real_ready_endpoint_recovers_stale_reader_without_index_mutation(
    tmp_path,
):
    from app.application.services.literal_fallback_maintenance import (
        wrap_vector_store_with_literal_fallback,
    )
    from app.bootstrap.application_context import ApplicationContext
    from app.bootstrap.app_factory import create_bcsfuse_app
    from app.bootstrap.provider_registry import ProviderRegistry

    class StartupProvider:
        async def initialize(self):
            return None

        async def shutdown(self):
            return None

    backend = SharedViewVectorStore()
    owner_composed = wrap_vector_store_with_literal_fallback(
        wrapped(tmp_path, "pod-owner", backend=backend)
    )
    reader_composed = wrap_vector_store_with_literal_fallback(
        wrapped(tmp_path, "pod-reader", backend=backend)
    )
    owner_composed.upsert([_make_point()])

    registry = ProviderRegistry()
    registry.register("config", object())
    registry.register("secret_provider", object())
    registry.register("startup_provider", StartupProvider())
    registry.register("vector_store", reader_composed)
    app = create_bcsfuse_app(
        ApplicationContext(
            mode="runtime",
            startup_profile="opensource",
            registry=registry,
        )
    )

    with TestClient(app) as client:
        response = client.get("/ready")
        body = response.json()

    assert response.status_code == 200
    assert body["ready"] is True
    assert body["read_only"] is True
    assert body["index_stale"] is False
    assert body["index_owner"] == "pod-owner"
    assert backend.rebuilds == 0
    hits = reader_composed.literal_fallback_index.keyword_search(
        "支付 账单",
        1,
        {"runtime_state": ("online",), "availability": ("public",)},
        None,
    )
    assert [hit.payload["profile_key"] for hit in hits] == ["worker:default"]

    owner_composed.close()
    reader_composed.close()


def test_active_owner_lease_can_be_renewed_without_a_backend_write(tmp_path):
    owner = wrapped(tmp_path, "pod-a")
    store = SQLiteIndexStateStore(str(tmp_path / "index-state.sqlite"))
    before = store.read_owner("profiles:dense+keyword").owner_expires_at

    assert owner.renew_owner_lease() is True
    after = store.read_owner("profiles:dense+keyword").owner_expires_at

    assert after > before
    assert owner.read_only is False
    assert owner.backend.points == []
    owner.close()


def test_non_owner_lease_renewal_does_not_steal_a_live_owner(tmp_path):
    owner = wrapped(tmp_path, "pod-a")
    reader = wrapped(tmp_path, "pod-b")

    assert reader.renew_owner_lease() is False
    assert reader.read_only is True
    assert reader.index_owner == "pod-a"

    record = SQLiteIndexStateStore(
        str(tmp_path / "index-state.sqlite")
    ).read_owner("profiles:dense+keyword")
    assert record.owner_id == "pod-a"
    assert record.index_version == 0

    owner.close()
    reader.close()


def test_concurrent_same_owner_writes_publish_every_revision_once(tmp_path):
    """Serialize same-owner writes; shared version state is process-critical."""
    from concurrent.futures import ThreadPoolExecutor

    owner = wrapped(tmp_path, "pod-a")
    failures = []

    def write(index):
        try:
            owner.update_payload_by_worker(f"worker-{index}", {"runtime_state": "online"})
        except Exception as error:
            failures.append(error)

    with ThreadPoolExecutor(max_workers=16) as executor:
        list(executor.map(write, range(64)))

    assert failures == []
    assert owner.index_version == 64
    assert not owner.index_stale
    owner.close()
