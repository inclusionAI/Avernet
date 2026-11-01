"""TDD: a real, atomically published local snapshot survives a first-boot DB outage."""

from __future__ import annotations

import json
from pathlib import Path

from search_core.plugin_api.vector_point import VectorPoint


def _point() -> VectorPoint:
    return VectorPoint(
        id="worker:default:full",
        vector=[],
        payload={
            "profile_key": "worker:default",
            "worker_id": "worker",
            "profile_id": "default",
            "content": "search recommendation availability fallback",
            "runtime_state": "online",
            "availability": "public",
        },
    )


class DurableBackend:
    def __init__(self, *, read_only: bool = False, fail: bool = False) -> None:
        self.read_only = read_only
        self.fail = fail

    @property
    def index_version(self) -> int:
        return 7

    def load_fallback_points(self) -> list[VectorPoint]:
        if self.fail:
            raise RuntimeError("MySQL unavailable")
        return [_point()]


def _snapshot_store(tmp_path: Path):
    from app.infra.public.stores.literal_fallback_snapshot_store import (
        LiteralFallbackSnapshotFileStore,
    )

    return LiteralFallbackSnapshotFileStore(tmp_path / "snapshot.json")


def test_owner_durable_load_publishes_real_payload_snapshot_without_vectors(tmp_path):
    from app.application.services.literal_fallback_maintenance import (
        wrap_vector_store_with_literal_fallback,
    )

    file_store = _snapshot_store(tmp_path)
    store = wrap_vector_store_with_literal_fallback(
        DurableBackend(read_only=False),
        snapshot_store=file_store,
    )

    assert store.literal_fallback_index.profile_count == 1
    snapshot = json.loads(file_store.path.read_text(encoding="utf-8"))
    assert snapshot["schema_version"] == 1
    assert snapshot["index_version"] == 7
    assert snapshot["point_count"] == 1
    assert snapshot["points"][0]["id"] == "worker:default:full"
    assert snapshot["points"][0]["profile_key"] == "worker:default"
    assert snapshot["points"][0]["payload"]["content"] == (
        "search recommendation availability fallback"
    )
    assert "vector" not in snapshot["points"][0]
    assert store.literal_fallback_index.source == "durable"


def test_first_boot_db_outage_loads_previously_published_real_snapshot(tmp_path):
    from app.application.services.literal_fallback_maintenance import (
        LiteralFallbackSnapshot,
        wrap_vector_store_with_literal_fallback,
    )

    file_store = _snapshot_store(tmp_path)
    snapshot = LiteralFallbackSnapshot()
    snapshot.upsert([_point()])
    file_store.save(
        entries=snapshot.export_entries(),
        index_version=7,
    )

    store = wrap_vector_store_with_literal_fallback(
        DurableBackend(read_only=True, fail=True),
        snapshot_store=file_store,
    )

    assert store.literal_fallback_index.profile_count == 1
    assert store.literal_fallback_index.source == "file"
    assert store.literal_fallback_index.snapshot_index_version == 7
    hits = store.literal_fallback_index.keyword_search(
        "search availability",
        1,
        {"runtime_state": ("online",), "availability": ("public",)},
        None,
    )
    assert [hit.payload["profile_key"] for hit in hits] == ["worker:default"]
    from app.application.services.local_retrieval_snapshot import snapshot_metadata

    metadata = snapshot_metadata(
        store.literal_fallback_index,
        hit_count=1,
        strategy="keyword",
    )
    assert metadata["snapshot_source"] == "file"
    assert metadata["snapshot_index_version"] == 7
    assert metadata["snapshot_age_seconds"] is not None
    assert metadata["snapshot_age_seconds"] >= 0
    assert store.literal_fallback_index.snapshot_saved_at_unix > 0


def test_read_only_replica_does_not_publish_snapshot(tmp_path):
    from app.application.services.literal_fallback_maintenance import (
        wrap_vector_store_with_literal_fallback,
    )

    file_store = _snapshot_store(tmp_path)
    store = wrap_vector_store_with_literal_fallback(
        DurableBackend(read_only=True),
        snapshot_store=file_store,
    )

    assert store.literal_fallback_index.profile_count == 1
    assert not file_store.path.exists()


def test_invalid_snapshot_is_rejected_without_partial_load(tmp_path):
    from app.infra.public.stores.literal_fallback_snapshot_store import (
        LiteralFallbackSnapshotDataError,
    )

    file_store = _snapshot_store(tmp_path)
    file_store.path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "index_version": 7,
                "point_count": 1,
                "points": [{"id": "partial", "profile_key": "worker:default"}],
            }
        ),
        encoding="utf-8",
    )

    try:
        file_store.load()
    except LiteralFallbackSnapshotDataError:
        pass
    else:
        raise AssertionError("invalid snapshot must fail closed")


def test_snapshot_publication_failure_is_explicit_but_does_not_failover(tmp_path):
    from app.application.services.literal_fallback_maintenance import (
        wrap_vector_store_with_literal_fallback,
    )
    from app.infra.public.stores.literal_fallback_snapshot_store import (
        LiteralFallbackSnapshotFileStore,
    )

    class UnwritableStore(LiteralFallbackSnapshotFileStore):
        def save(self, *, entries, index_version):
            raise OSError("snapshot volume unavailable")

    store = wrap_vector_store_with_literal_fallback(
        DurableBackend(read_only=False),
        snapshot_store=UnwritableStore(tmp_path / "snapshot.json"),
    )

    assert store.literal_fallback_index.profile_count == 1
    assert store.snapshot_publish_error_type == "OSError"


def test_runtime_composition_selects_configured_snapshot_path(monkeypatch):
    import inspect
    from pathlib import Path

    from app.bootstrap import opensource

    snapshot_path = Path(
        "/var/lib/bcsfuse/published-snapshot.json"
    ).resolve(strict=False)
    monkeypatch.setenv(
        "BCSFUSE_LITERAL_FALLBACK_SNAPSHOT_PATH",
        str(snapshot_path),
    )
    from app.infra.public.config.yaml_env_config_provider import (
        YamlEnvConfigProvider,
    )

    config_path = Path(opensource.__file__).parents[3] / "configs" / "application.yaml"
    config = YamlEnvConfigProvider(config_path=str(config_path))
    store = opensource._literal_fallback_snapshot_store(config, enabled=True)

    assert store is not None
    assert store.path == snapshot_path

    source = inspect.getsource(opensource._build_runtime_providers)
    assert (
        "snapshot_store=_literal_fallback_snapshot_store(config, enabled=True)"
        in source
    )



def test_failed_startup_rebuild_preserves_published_snapshot(tmp_path) -> None:
    from app.application.services.literal_fallback_maintenance import (
        LiteralFallbackSnapshot,
        wrap_vector_store_with_literal_fallback,
    )

    class FailingRebuildBackend(DurableBackend):
        def rebuild_from_backend(self) -> dict:
            raise RuntimeError("rebuild unavailable")

    file_store = _snapshot_store(tmp_path)
    published_snapshot = LiteralFallbackSnapshot()
    published_snapshot.upsert([_point()])
    file_store.save(
        entries=published_snapshot.export_entries(),
        index_version=7,
    )

    try:
        store = wrap_vector_store_with_literal_fallback(
            FailingRebuildBackend(read_only=False, fail=True),
            snapshot_store=file_store,
        )
        store.rebuild_from_backend()
    except RuntimeError:
        pass
    else:
        raise AssertionError("failed rebuild must propagate")

    assert store.literal_fallback_index.source == "file"
    assert store.literal_fallback_index.profile_count == 1
