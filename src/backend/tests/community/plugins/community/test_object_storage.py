"""Unit tests for the community object-storage impls (B3).

FS impl runs against a temp root; S3 impl runs under moto's in-process AWS mock.
"""
from __future__ import annotations

import boto3
import pytest
from moto import mock_aws

from agentclaw.community.di.config_community import CommunityS3Config
from agentclaw.community.plugins.community.object_storage import (
    CommunityFsObjectStorage,
    CommunityS3ObjectStorage,
)

_BUCKET = "b3-test-bucket"


# ── Filesystem impl ──────────────────────────────────────────────────────────

def _fs(tmp_path) -> CommunityFsObjectStorage:
    return CommunityFsObjectStorage(str(tmp_path / "store"))


def test_fs_put_object_then_list_and_get(tmp_path):
    store = _fs(tmp_path)
    assert store.put_object("a.txt", "hello") is True
    assert "a.txt" in store.list_objects("")


def test_fs_put_object_accepts_bytes(tmp_path):
    store = _fs(tmp_path)
    assert store.put_object("b.bin", b"\x00\x01") is True
    assert store.get_etag("b.bin") is not None


def test_fs_nested_key_creates_dirs(tmp_path):
    store = _fs(tmp_path)
    assert store.put_object("a/b/c.txt", "deep") is True
    assert "a/b/c.txt" in store.list_objects("a/")


def test_fs_list_objects_prefix_and_max_keys(tmp_path):
    store = _fs(tmp_path)
    store.put_object("p/1", "x")
    store.put_object("p/2", "x")
    store.put_object("q/3", "x")
    assert sorted(store.list_objects("p/")) == ["p/1", "p/2"]
    assert len(store.list_objects("", max_keys=2)) == 2


def test_fs_delete_is_idempotent(tmp_path):
    store = _fs(tmp_path)
    store.put_object("d.txt", "x")
    assert store.delete_object("d.txt") is True
    # Deleting an already-absent object is still success.
    assert store.delete_object("d.txt") is True
    assert "d.txt" not in store.list_objects("")


def test_fs_ensure_directory(tmp_path):
    store = _fs(tmp_path)
    assert store.ensure_directory("some/dir") is True
    assert (tmp_path / "store" / "some" / "dir").is_dir()


def test_fs_path_traversal_is_rejected(tmp_path):
    store = _fs(tmp_path)
    # A key escaping the root is rejected, and nothing is written outside.
    assert store.put_object("../escape.txt", "evil") is False
    assert not (tmp_path / "escape.txt").exists()
    assert store.get_etag("../escape.txt") is None


def test_fs_get_etag_stable_and_none_when_absent(tmp_path):
    store = _fs(tmp_path)
    store.put_object("e.txt", "same")
    etag1 = store.get_etag("e.txt")
    etag2 = store.get_etag("e.txt")
    assert etag1 == etag2 and etag1 is not None
    assert store.get_etag("missing.txt") is None


def test_fs_sign_url_is_file_shaped(tmp_path):
    store = _fs(tmp_path)
    store.put_object("s.txt", "x")
    assert store.sign_url("s.txt").startswith("file://")


def test_fs_set_object_acl_is_noop_success(tmp_path):
    store = _fs(tmp_path)
    store.put_object("acl.txt", "x")
    assert store.set_object_acl("acl.txt", "public-read") is True


def test_fs_put_file_copies_local_file(tmp_path):
    store = _fs(tmp_path)
    src = tmp_path / "src.bin"
    src.write_bytes(b"payload")
    assert store.put_file("dst/x.bin", str(src)) is True
    assert store.get_etag("dst/x.bin") is not None


def test_fs_put_file_missing_source_swallows_error(tmp_path):
    store = _fs(tmp_path)
    # Nonexistent source → copyfile raises OSError → swallowed to False.
    assert store.put_file("k", str(tmp_path / "nope.bin")) is False


def test_fs_io_errors_swallow_to_falsy(tmp_path):
    # Force OSError on the write/dir branches: a key whose parent is an existing
    # FILE makes mkdir/copy/unlink raise, exercising the except paths.
    store = _fs(tmp_path)
    store.put_object("f", "x")  # "f" is now a file
    assert store.put_object("f/child", "y") is False        # mkdir under a file
    assert store.ensure_directory("f/sub") is False          # mkdir under a file
    store.ensure_directory("d")                              # "d" is a directory
    assert store.delete_object("d") is False                 # unlink() on a dir
    # get_etag on a directory is not a file → None (no crash).
    assert store.get_etag("d") is None


def test_fs_sign_url_for_traversal_key_uses_fallback(tmp_path):
    store = _fs(tmp_path)
    # A traversal key has no safe path; sign_url still returns a file:// string.
    assert store.sign_url("../escape").startswith("file://")


def test_s3_put_file_uploads(s3_store, tmp_path):
    src = tmp_path / "up.bin"
    src.write_bytes(b"data")
    assert s3_store.put_file("uploaded", str(src)) is True
    assert "uploaded" in s3_store.list_objects("")


def test_s3_errors_on_missing_bucket_swallow_to_falsy(monkeypatch):
    # A store pointed at a bucket that was never created: every op that hits the
    # bucket raises ClientError and is swallowed to False / [] / None.
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    with mock_aws():
        store = CommunityS3ObjectStorage(
            CommunityS3Config(bucket="never-created", region="us-east-1")
        )
        assert store.put_object("k", "x") is False
        assert store.put_file("k", __file__) is False
        assert store.delete_object("k") is False
        assert store.list_objects("") == []
        assert store.get_etag("k") is None
        assert store.set_object_acl("k", "private") is False


# ── S3-compatible impl (moto in-process AWS mock) ────────────────────────────

@pytest.fixture
def s3_store(monkeypatch):
    # moto needs (fake) credentials present in the environment.
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    with mock_aws():
        boto3.client("s3", region_name="us-east-1").create_bucket(Bucket=_BUCKET)
        yield CommunityS3ObjectStorage(
            CommunityS3Config(bucket=_BUCKET, region="us-east-1")
        )


def test_s3_put_list_delete(s3_store):
    assert s3_store.put_object("k1", "hello") is True
    assert s3_store.put_object("k2", b"bytes") is True
    assert sorted(s3_store.list_objects("")) == ["k1", "k2"]
    assert s3_store.delete_object("k1") is True
    assert s3_store.list_objects("") == ["k2"]


def test_s3_delete_absent_is_success(s3_store):
    assert s3_store.delete_object("never-existed") is True


def test_s3_list_prefix_and_max_keys(s3_store):
    s3_store.put_object("p/1", "x")
    s3_store.put_object("p/2", "x")
    s3_store.put_object("q/3", "x")
    assert sorted(s3_store.list_objects("p/")) == ["p/1", "p/2"]
    assert len(s3_store.list_objects("", max_keys=2)) == 2


def test_s3_sign_url_returns_presigned(s3_store):
    s3_store.put_object("k", "x")
    url = s3_store.sign_url("k", expires=60)
    assert url.startswith("http")
    assert _BUCKET in url and "k" in url


def test_s3_get_etag(s3_store):
    s3_store.put_object("e", "content")
    etag = s3_store.get_etag("e")
    assert etag and '"' not in etag
    assert s3_store.get_etag("missing") is None


def test_s3_set_object_acl(s3_store):
    s3_store.put_object("a", "x")
    assert s3_store.set_object_acl("a", "public-read") is True


def test_s3_ensure_directory_is_noop_success(s3_store):
    assert s3_store.ensure_directory("any/dir") is True


def test_s3_requires_non_empty_bucket():
    # Selecting S3 without a bucket fails fast rather than failing every op.
    with pytest.raises(ValueError, match="non-empty bucket"):
        CommunityS3ObjectStorage(CommunityS3Config(bucket=""))
