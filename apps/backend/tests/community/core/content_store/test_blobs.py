"""The blob seam: what each medium does with bytes under a digest."""
from __future__ import annotations

import hashlib
import os
import time

import pytest

from agentclaw.community.core.content_store.blobs import (
    LocalBlobStore,
    ObjectStorageBlobStore,
    object_key,
)
from agentclaw.community.core.content_store.errors import ContentStoreFault
from agentclaw.community.plugin_api.object_storage import (
    ObjectCreateResult,
    ObjectReadResult,
    ObjectReadStatus,
)
from agentclaw.community.plugins.community.object_storage import CommunityFsObjectStorage
from agentclaw.community.core.content_store.service import ContentStoreService

BODY = b"blob-bytes" * 32
DIGEST = "sha256:" + hashlib.sha256(BODY).hexdigest()
HEX = DIGEST.partition(":")[2]


def test_local_blob_store_round_trip_and_missing_is_none(tmp_path):
    store = LocalBlobStore(tmp_path)
    assert store.read(DIGEST) is None
    store.write(DIGEST, BODY)
    assert store.read(DIGEST) == BODY
    assert (tmp_path / "blobs" / HEX[:2] / HEX).read_bytes() == BODY


def test_local_blob_store_rewrites_a_truncated_blob(tmp_path):
    store = LocalBlobStore(tmp_path)
    store.write(DIGEST, BODY)
    store.path(DIGEST).write_bytes(BODY[:5])
    store.write(DIGEST, BODY)
    assert store.read(DIGEST) == BODY


def test_local_blob_store_sweeps_only_stale_tmp_files(tmp_path):
    store = LocalBlobStore(tmp_path)
    shard = store.path(DIGEST).parent
    shard.mkdir(parents=True)
    stale, fresh = shard / ".tmp-stale", shard / ".tmp-fresh"
    stale.write_bytes(b"x")
    fresh.write_bytes(b"x")
    old = time.time() - 7200
    os.utime(stale, (old, old))
    store.write(DIGEST, BODY)
    assert not stale.exists() and fresh.exists()


def test_service_takes_exactly_one_of_root_and_blobs(tmp_path):
    with pytest.raises(ValueError):
        ContentStoreService(object())
    with pytest.raises(ValueError):
        ContentStoreService(object(), tmp_path, blobs=LocalBlobStore(tmp_path))


class _BaseSurfaceStorage:
    """An object storage with the base surface only (no immutable capability)."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.puts = 0
        self.fail_put = False

    def get_etag(self, key):
        return "etag" if key in self.objects else None

    def put_object(self, key, content):
        if self.fail_put:
            return False
        self.puts += 1
        self.objects[key] = content
        return True

    def get_object(self, key):
        return self.objects.get(key)


def _fs_storage(tmp_path):
    return CommunityFsObjectStorage(str(tmp_path / "objstore"))


def test_key_layout():
    assert object_key(DIGEST) == f"content-store/blobs/{HEX[:2]}/{HEX}"


def test_object_store_write_then_read_from_a_second_instance(tmp_path):
    """Two backend instances, one object storage: what the first wrote, the second reads."""
    ObjectStorageBlobStore(_fs_storage(tmp_path)).write(DIGEST, BODY)
    assert ObjectStorageBlobStore(_fs_storage(tmp_path)).read(DIGEST) == BODY
    assert (tmp_path / "objstore" / "content-store" / "blobs" / HEX[:2] / HEX).read_bytes() == BODY


def test_object_store_missing_is_none(tmp_path):
    assert ObjectStorageBlobStore(_fs_storage(tmp_path)).read(DIGEST) is None


def test_object_store_write_skips_upload_when_object_exists():
    storage = _BaseSurfaceStorage()
    store = ObjectStorageBlobStore(storage)
    store.write(DIGEST, BODY)
    store.write(DIGEST, BODY)
    assert storage.puts == 1


def test_object_store_without_immutable_capability_uses_put_and_get():
    storage = _BaseSurfaceStorage()
    store = ObjectStorageBlobStore(storage)
    store.write(DIGEST, BODY)
    assert storage.objects == {object_key(DIGEST): BODY}
    assert store.read(DIGEST) == BODY


def test_object_store_write_failure_raises_fault():
    storage = _BaseSurfaceStorage()
    storage.fail_put = True
    with pytest.raises(ContentStoreFault):
        ObjectStorageBlobStore(storage).write(DIGEST, BODY)


def test_object_store_create_failure_raises_fault(tmp_path, monkeypatch):
    storage = _fs_storage(tmp_path)
    monkeypatch.setattr(storage, "create_object_if_absent", lambda key, content: ObjectCreateResult.FAILED)
    with pytest.raises(ContentStoreFault):
        ObjectStorageBlobStore(storage).write(DIGEST, BODY)


def test_object_store_lost_create_race_is_success(tmp_path, monkeypatch):
    storage = _fs_storage(tmp_path)
    monkeypatch.setattr(storage, "create_object_if_absent", lambda key, content: ObjectCreateResult.ALREADY_EXISTS)
    ObjectStorageBlobStore(storage).write(DIGEST, BODY)


def test_object_store_read_failed_status_raises_fault_not_missing(tmp_path, monkeypatch):
    storage = _fs_storage(tmp_path)
    monkeypatch.setattr(storage, "read_object", lambda key: ObjectReadResult(ObjectReadStatus.FAILED))
    with pytest.raises(ContentStoreFault):
        ObjectStorageBlobStore(storage, legacy=LocalBlobStore(tmp_path / "old")).read(DIGEST)


def test_legacy_read_through_serves_and_copies_forward(tmp_path):
    legacy = LocalBlobStore(tmp_path / "old")
    legacy.write(DIGEST, BODY)
    store = ObjectStorageBlobStore(_fs_storage(tmp_path), legacy=legacy)
    assert store.read(DIGEST) == BODY
    # Now in the object storage: an instance with no local tree reads it.
    assert ObjectStorageBlobStore(_fs_storage(tmp_path)).read(DIGEST) == BODY


def test_legacy_blob_failing_its_digest_is_not_copied_forward(tmp_path):
    legacy = LocalBlobStore(tmp_path / "old")
    legacy.write(DIGEST, BODY)
    legacy.path(DIGEST).write_bytes(b"damaged")
    store = ObjectStorageBlobStore(_fs_storage(tmp_path), legacy=legacy)
    assert store.read(DIGEST) == b"damaged"  # the service's hash check refuses it
    assert ObjectStorageBlobStore(_fs_storage(tmp_path)).read(DIGEST) is None


def test_legacy_copy_forward_failure_does_not_fail_the_read(tmp_path):
    legacy = LocalBlobStore(tmp_path / "old")
    legacy.write(DIGEST, BODY)
    storage = _BaseSurfaceStorage()
    storage.fail_put = True
    assert ObjectStorageBlobStore(storage, legacy=legacy).read(DIGEST) == BODY


def test_legacy_miss_is_none(tmp_path):
    store = ObjectStorageBlobStore(_fs_storage(tmp_path), legacy=LocalBlobStore(tmp_path / "old"))
    assert store.read(DIGEST) is None
