"""The blob seam: what each medium does with bytes under a digest."""
from __future__ import annotations

import hashlib
import os
import time

import pytest

from agentclaw.community.core.content_store.blobs import (
    BlobStore,
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
from agentclaw.community.testing.content_blob_store import LocalBlobStore

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


def test_object_store_lost_create_race_is_success(tmp_path, monkeypatch):
    storage = _fs_storage(tmp_path)
    monkeypatch.setattr(storage, "create_object_if_absent", lambda key, content: ObjectCreateResult.ALREADY_EXISTS)
    ObjectStorageBlobStore(storage).write(DIGEST, BODY)


def test_object_store_read_failed_status_raises_fault_not_missing(tmp_path, monkeypatch):
    storage = _fs_storage(tmp_path)
    monkeypatch.setattr(storage, "read_object", lambda key: ObjectReadResult(ObjectReadStatus.FAILED))
    with pytest.raises(ContentStoreFault):
        ObjectStorageBlobStore(storage).read(DIGEST)


def test_object_store_skips_create_when_the_object_exists(tmp_path, monkeypatch):
    storage = _fs_storage(tmp_path)
    store = ObjectStorageBlobStore(storage)
    store.write(DIGEST, BODY)
    monkeypatch.setattr(storage, "create_object_if_absent", lambda key, content: pytest.fail("uploaded again"))
    store.write(DIGEST, BODY)


def test_object_store_failed_create_never_falls_back_to_a_plain_put(tmp_path, monkeypatch):
    """A non-atomic put after a failed create could leave a partial object the existence check then trusts."""
    storage = _fs_storage(tmp_path)
    monkeypatch.setattr(storage, "create_object_if_absent", lambda key, content: ObjectCreateResult.FAILED)
    monkeypatch.setattr(storage, "put_object", lambda key, content: pytest.fail("fell back to a plain put"))
    store = ObjectStorageBlobStore(storage)
    with pytest.raises(ContentStoreFault):
        store.write(DIGEST, BODY)
    assert store.read(DIGEST) is None


def test_object_store_found_without_content_is_a_fault(tmp_path, monkeypatch):
    storage = _fs_storage(tmp_path)
    monkeypatch.setattr(storage, "read_object", lambda key: ObjectReadResult(ObjectReadStatus.FOUND, None))
    with pytest.raises(ContentStoreFault):
        ObjectStorageBlobStore(storage).read(DIGEST)


def test_a_damaged_object_is_not_repaired_by_a_later_write(tmp_path):
    """Pinned limit: the existence check cannot see damage, so an operator deletes the object to heal it."""
    storage = _fs_storage(tmp_path)
    store = ObjectStorageBlobStore(storage)
    store.write(DIGEST, BODY)
    damaged = tmp_path / "objstore" / "content-store" / "blobs" / HEX[:2] / HEX
    damaged.write_bytes(BODY[:5])

    store.write(DIGEST, BODY)
    assert store.read(DIGEST) == BODY[:5]  # the service's hash check refuses it

    damaged.unlink()
    store.write(DIGEST, BODY)
    assert store.read(DIGEST) == BODY


def test_both_media_satisfy_the_blob_contract(tmp_path):
    assert isinstance(LocalBlobStore(tmp_path), BlobStore)
    assert isinstance(ObjectStorageBlobStore(_fs_storage(tmp_path)), BlobStore)
