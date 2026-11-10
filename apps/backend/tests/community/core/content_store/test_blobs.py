"""The blob seam: what each medium does with bytes under a digest."""
from __future__ import annotations

import hashlib
import os
import time

import pytest

from agentclaw.community.core.content_store.blobs import LocalBlobStore
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
