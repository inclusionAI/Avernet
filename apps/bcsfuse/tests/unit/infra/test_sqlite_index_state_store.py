"""Cross-process index state storage contract."""

import time
from pathlib import Path

import pytest

from app.infra.public.stores.sqlite_index_state_store import SQLiteIndexStateStore


def test_same_collection_pair_keeps_only_one_shared_owner(tmp_path):
    path = str(tmp_path / "index-state.sqlite")
    store = SQLiteIndexStateStore(path)

    record = store.claim_owner("profiles:dense+keyword", "pod-a", lease_seconds=60)

    assert record.owner_id == "pod-a"
    assert record.index_version == 0
    record_b = store.claim_owner("profiles:dense+keyword", "pod-b", lease_seconds=60)
    assert record_b.owner_id == "pod-a"

    owner_a_from_another_process = SQLiteIndexStateStore(path)
    assert (
        owner_a_from_another_process.claim_owner(
            "profiles:dense+keyword", "pod-a", lease_seconds=60
        ).owner_id
        == "pod-a"
    )


def test_release_allows_failsafe_takeover_and_preserves_version(tmp_path):
    path = str(tmp_path / "index-state.sqlite")
    store_a = SQLiteIndexStateStore(path)
    store_b = SQLiteIndexStateStore(path)

    store_a.claim_owner("profiles", "pod-a", lease_seconds=60)
    store_a.publish_index_version("profiles", "pod-a", 10)
    store_a.release_owner("profiles", "pod-a")

    record = store_b.claim_owner("profiles", "pod-b", lease_seconds=60)
    assert record.owner_id == "pod-b"
    assert record.index_version == 10

    stale_owner = store_a.claim_owner("profiles", "pod-a", lease_seconds=60)
    assert stale_owner.owner_id == "pod-b"


def test_publishing_index_version_requires_current_owner(tmp_path):
    path = str(tmp_path / "index-state.sqlite")
    store_a = SQLiteIndexStateStore(path)
    store_b = SQLiteIndexStateStore(path)

    store_a.claim_owner("profiles", "pod-a", lease_seconds=60)
    assert store_a.publish_index_version("profiles", "pod-a", 1).index_version == 1
    assert store_b.claim_owner("profiles", "pod-a", lease_seconds=60).owner_id == "pod-a"
    assert store_b.publish_index_version("profiles", "pod-b", 2) is None

    record = store_b.read_owner("profiles")
    assert record.owner_id == "pod-a"
    assert record.index_version == 1


def test_reading_index_state_is_deterministic_after_versions(tmp_path):
    path = str(tmp_path / "index-state.sqlite")
    store = SQLiteIndexStateStore(path)

    store.claim_owner("profiles", "pod-a", lease_seconds=60)
    store.publish_index_version("profiles", "pod-a", 3)
    record = store.read_owner("profiles")

    assert record.collection_pair == "profiles"
    assert record.owner_id == "pod-a"
    assert record.index_version == 3
    assert record.index_updated_at > 0
    assert record.observed_at >= record.index_updated_at


def test_invalid_index_state_inputs_are_rejected(tmp_path):
    store = SQLiteIndexStateStore(str(tmp_path / "index-state.sqlite"))

    with pytest.raises(ValueError):
        store.claim_owner("", "pod-a", lease_seconds=60)
    with pytest.raises(ValueError):
        store.claim_owner("profiles", "", lease_seconds=60)
    with pytest.raises(ValueError):
        store.claim_owner("profiles", "pod-a", lease_seconds=0)
    with pytest.raises(ValueError):
        store.publish_index_version("profiles", "pod-a", -1)
    with pytest.raises(FileNotFoundError):
        store.read_owner("profiles")
    assert Path(store.db_path).is_file()


def test_expired_owner_cannot_publish_index_version(tmp_path):
    path = str(tmp_path / "index-state.sqlite")
    store = SQLiteIndexStateStore(path)
    other_store = SQLiteIndexStateStore(path)

    store.claim_owner("profiles", "pod-a", lease_seconds=0.001)
    time.sleep(0.02)
    other_store.claim_owner("profiles", "pod-b", lease_seconds=60)
    expired_owner = store.read_owner("profiles")

    assert expired_owner.owner_id == "pod-b"
    assert store.publish_index_version("profiles", "pod-a", 2) is None


def test_publishing_a_non_increasing_index_version_is_rejected(tmp_path):
    store = SQLiteIndexStateStore(str(tmp_path / "index-state.sqlite"))
    store.claim_owner("profiles", "pod-a", lease_seconds=60)
    assert store.publish_index_version("profiles", "pod-a", 3).index_version == 3

    assert store.publish_index_version("profiles", "pod-a", 3) is None
    assert store.publish_index_version("profiles", "pod-a", 2) is None

    assert store.read_owner("profiles").index_version == 3


def test_concurrent_first_claims_observed_after_durable_index_time(tmp_path):
    """Concurrent lease acquisition must not fail on a durable future timestamp."""
    path = str(tmp_path / "index-state.sqlite")
    store = SQLiteIndexStateStore(path)

    store.claim_owner("profiles:dense+keyword", "pod-a", lease_seconds=60)
    future = time.time() + 1.0
    store._execute(
        lambda cursor: cursor.execute(
            "UPDATE bcsfuse_index_states SET index_updated_at = ? "
            "WHERE collection_pair = ?",
            (future, "profiles:dense+keyword"),
        )
    )

    record = store.claim_owner(
        "profiles:dense+keyword", "pod-a", lease_seconds=60
    )
    assert record.observed_at >= record.index_updated_at

    read = store.read_owner("profiles:dense+keyword")
    assert read.observed_at >= read.index_updated_at
