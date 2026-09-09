"""Unit tests for AliyunOssFileTransferBackend.

All tests patch the oss2 SDK seam (``oss2.Auth`` / ``oss2.Bucket``) so no
network call ever occurs.  Each test pins the exact SDK-call contract —
sign_url positional/keyword shapes, multipart session building, paginated
listing — and the error taxonomy (NoSuchKey tolerated on head/delete,
paired OssError/ClientError re-raised everywhere else).
"""

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import oss2
import pytest

from secbaas.community.bootstrap._configs import (
    ConfigError,
    FileTransferOssConfigSchema,
)
from secbaas.community.plugins.file_transfer._aliyun_oss import (
    AliyunOssFileTransferBackend,
)
from secbaas.community.spi.file_transfer import (
    MultipartSession,
    ObjectItem,
    ObjectListing,
    PartInfo,
)

ENDPOINT = "https://oss-cn-hangzhou.aliyuncs.com"
EXTERNAL_ENDPOINT = "https://oss-cn-hangzhou-ext.aliyuncs.com"
BUCKET = "my-bucket"
STAGING_ROOT = "baas-file-transfer"
SECRET_NAME = "svc-secret"


def _config(**overrides):
    kwargs = {
        "endpoint": ENDPOINT,
        "external_endpoint": EXTERNAL_ENDPOINT,
        "bucket_name": BUCKET,
        "staging_root_path": STAGING_ROOT,
        "secret_name": SECRET_NAME,
    }
    kwargs.update(overrides)
    return FileTransferOssConfigSchema(**kwargs)


def _oss_error():
    return oss2.exceptions.OssError(500, {}, "", {})


def _no_such_key():
    return oss2.exceptions.NoSuchKey(404, {}, "", {})


class _StubSecretStore:
    """Minimal SecretStorePlugin stand-in recording its lookup calls."""

    def __init__(self, pair):
        self._pair = pair
        self.calls = []

    def get_kv_secret(self, secret_name):
        self.calls.append(secret_name)
        return self._pair


@pytest.fixture
def oss():
    """Patch the oss2 SDK seam: one fresh MagicMock bucket per call site."""
    with patch("oss2.Auth") as auth, patch("oss2.Bucket") as bucket:
        bucket.side_effect = lambda *args, **kwargs: MagicMock()
        yield auth, bucket


@pytest.fixture
def backend(oss):
    return AliyunOssFileTransferBackend(config=_config(), credentials=("AK", "SK"))


# ── Constructor: three-state AK/SK resolution ─────────────────────────


def test_constructor_uses_credentials_tuple(oss):
    auth, bucket = oss
    backend = AliyunOssFileTransferBackend(config=_config(), credentials=("AK", "SK"))
    auth.assert_called_once_with("AK", "SK")
    internal_call, sign_call = bucket.call_args_list
    assert internal_call.args == (auth.return_value, ENDPOINT, BUCKET)
    assert sign_call.args == (auth.return_value, EXTERNAL_ENDPOINT, BUCKET)
    assert backend._bucket is not backend._sign_bucket


def test_constructor_resolves_credentials_from_secret_store(oss):
    auth, _ = oss
    store = _StubSecretStore(("S-AK", "S-SK"))
    AliyunOssFileTransferBackend(config=_config(), secret_store=store)
    assert store.calls == [SECRET_NAME]
    auth.assert_called_once_with("S-AK", "S-SK")


def test_constructor_without_credentials_or_store_raises(oss):
    with pytest.raises(ConfigError, match="requires either credentials"):
        AliyunOssFileTransferBackend(config=_config())


def test_single_endpoint_mode_aliases_sign_bucket(oss):
    _, bucket = oss
    backend = AliyunOssFileTransferBackend(
        config=_config(external_endpoint=""), credentials=("AK", "SK")
    )
    assert len(bucket.call_args_list) == 1
    assert backend._sign_bucket is backend._bucket


# ── Staging path builders ─────────────────────────────────────────────


def test_build_staging_path_shape(backend):
    path = backend.build_staging_path("tenant-a", "tf-1", "file.txt")
    assert path == "baas-file-transfer/tenant-a/tf-1/file.txt"


def test_build_staging_path_with_subdir(backend):
    path = backend.build_staging_path("tenant-a", "tf-1", "file.txt", subdir="sub")
    assert path == "baas-file-transfer/tenant-a/sub/tf-1/file.txt"


def test_build_staging_path_rstrips_staging_root(oss):
    backend = AliyunOssFileTransferBackend(
        config=_config(staging_root_path="root/"), credentials=("AK", "SK")
    )
    assert backend.build_staging_path("t", "tf", "f.txt") == "root/t/tf/f.txt"


@pytest.mark.parametrize("component", ["tenant", "transfer_id", "subdir", "filename"])
def test_build_staging_path_rejects_traversal(backend, component):
    kwargs = {"tenant": "t", "transfer_id": "tf-1", "filename": "f.txt", "subdir": None}
    kwargs[component] = ".."
    with pytest.raises(ValueError, match=f"Path traversal detected in {component}"):
        backend.build_staging_path(**kwargs)


def test_build_session_staging_path_shape(backend):
    path = backend.build_session_staging_path("tenant-a", "sess-1", "tf-1", "file.txt")
    assert path == "baas-file-transfer/tenant-a/sess-1/tf-1/file.txt"


def test_build_session_staging_path_with_subdir(backend):
    path = backend.build_session_staging_path(
        "tenant-a", "sess-1", "tf-1", "file.txt", subdir="sub"
    )
    assert path == "baas-file-transfer/tenant-a/sess-1/sub/tf-1/file.txt"


@pytest.mark.parametrize(
    "component", ["tenant", "session_id", "transfer_id", "subdir", "filename"]
)
def test_build_session_staging_path_rejects_traversal(backend, component):
    kwargs = {
        "tenant": "t",
        "session_id": "sess-1",
        "transfer_id": "tf-1",
        "filename": "f.txt",
        "subdir": None,
    }
    kwargs[component] = ".."
    with pytest.raises(ValueError, match=f"Path traversal detected in {component}"):
        backend.build_session_staging_path(**kwargs)


def test_build_staging_prefix_shape(backend):
    assert backend.build_staging_prefix("tenant-a") == "baas-file-transfer/tenant-a/"


def test_build_staging_prefix_with_subdir(backend):
    assert (
        backend.build_staging_prefix("tenant-a", subdir="sub")
        == "baas-file-transfer/tenant-a/sub/"
    )


@pytest.mark.parametrize("component", ["tenant", "subdir"])
def test_build_staging_prefix_rejects_traversal(backend, component):
    kwargs = {"tenant": "t", "subdir": None}
    kwargs[component] = ".."
    with pytest.raises(ValueError, match=f"Path traversal detected in {component}"):
        backend.build_staging_prefix(**kwargs)


# ── Presigned URL generation ──────────────────────────────────────────


def test_generate_upload_url_without_content_type(backend):
    backend._sign_bucket.sign_url.return_value = "https://oss/presigned-put"
    url = backend.generate_upload_url("root/t/f.bin", 300)
    assert url == "https://oss/presigned-put"
    backend._sign_bucket.sign_url.assert_called_once_with("PUT", "root/t/f.bin", 300)


def test_generate_upload_url_with_content_type(backend):
    backend.generate_upload_url("root/t/f.bin", 300, content_type="text/plain")
    backend._sign_bucket.sign_url.assert_called_once_with(
        "PUT", "root/t/f.bin", 300, headers={"Content-Type": "text/plain"}
    )


def test_generate_upload_url_error_reraises(backend):
    backend._sign_bucket.sign_url.side_effect = _oss_error()
    with pytest.raises(oss2.exceptions.OssError):
        backend.generate_upload_url("root/t/f.bin", 300)


def test_generate_download_url_plain(backend):
    backend._sign_bucket.sign_url.return_value = "https://oss/presigned-get"
    url = backend.generate_download_url("root/t/f.bin", 300)
    assert url == "https://oss/presigned-get"
    backend._sign_bucket.sign_url.assert_called_once_with("GET", "root/t/f.bin", 300)


def test_generate_download_url_with_response_params(backend):
    response_params = {"response-content-disposition": "attachment"}
    backend.generate_download_url("root/t/f.bin", 300, response_params=response_params)
    backend._sign_bucket.sign_url.assert_called_once_with(
        "GET", "root/t/f.bin", 300, params=response_params
    )


def test_generate_download_url_error_reraises(backend):
    backend._sign_bucket.sign_url.side_effect = _oss_error()
    with pytest.raises(oss2.exceptions.OssError):
        backend.generate_download_url("root/t/f.bin", 300)


# ── Object existence ──────────────────────────────────────────────────


def test_check_object_exists_true(backend):
    assert backend.check_object_exists("root/t/f.bin") is True
    backend._bucket.head_object.assert_called_once_with("root/t/f.bin")


def test_check_object_exists_no_such_key(backend):
    backend._bucket.head_object.side_effect = _no_such_key()
    assert backend.check_object_exists("root/t/f.bin") is False


def test_check_object_exists_error_reraises(backend):
    backend._bucket.head_object.side_effect = _oss_error()
    with pytest.raises(oss2.exceptions.OssError):
        backend.check_object_exists("root/t/f.bin")


# ── Multipart upload session ──────────────────────────────────────────


def test_initiate_multipart_upload_builds_session(backend):
    backend._bucket.init_multipart_upload.return_value.upload_id = "upload-123"
    backend._sign_bucket.sign_url.side_effect = ["p1", "p2", "p3"]

    session = backend.initiate_multipart_upload("root/t/big.bin", 3600, part_count=3)

    backend._bucket.init_multipart_upload.assert_called_once_with("root/t/big.bin")
    assert isinstance(session, MultipartSession)
    assert session.session_id == "upload-123"
    assert session.part_count == 3
    assert [p.part_number for p in session.parts] == [1, 2, 3]
    assert [p.upload_url for p in session.parts] == ["p1", "p2", "p3"]
    expected_calls = [
        (
            ("PUT", "root/t/big.bin", 3600),
            {"params": {"uploadId": "upload-123", "partNumber": str(i)}},
        )
        for i in range(1, 4)
    ]
    for (args, kwargs), ((exp_args), exp_kwargs) in zip(
        backend._sign_bucket.sign_url.call_args_list, expected_calls
    ):
        assert args == exp_args
        assert kwargs == exp_kwargs


def test_initiate_multipart_upload_with_content_type(backend):
    backend._bucket.init_multipart_upload.return_value.upload_id = "upload-123"
    backend._sign_bucket.sign_url.return_value = "p1"

    backend.initiate_multipart_upload(
        "root/t/big.bin", 3600, part_count=1, content_type="application/octet-stream"
    )
    backend._sign_bucket.sign_url.assert_called_once_with(
        "PUT",
        "root/t/big.bin",
        3600,
        params={"uploadId": "upload-123", "partNumber": "1"},
        headers={"Content-Type": "application/octet-stream"},
    )


def test_initiate_multipart_upload_error_reraises(backend):
    backend._bucket.init_multipart_upload.side_effect = _oss_error()
    with pytest.raises(oss2.exceptions.OssError):
        backend.initiate_multipart_upload("root/t/big.bin", 3600, part_count=2)


def test_list_parts_maps_etags(backend):
    backend._bucket.list_parts.return_value.parts = [
        SimpleNamespace(part_number=2, etag="etag-2"),
        SimpleNamespace(part_number=1, etag="etag-1"),
    ]
    parts = backend.list_parts("root/t/big.bin", "upload-123")
    backend._bucket.list_parts.assert_called_once_with("root/t/big.bin", "upload-123")
    assert [p.part_number for p in parts] == [2, 1]
    assert [p.etag for p in parts] == ["etag-2", "etag-1"]
    assert all(p.upload_url == "" for p in parts)


def test_list_parts_error_reraises(backend):
    backend._bucket.list_parts.side_effect = _oss_error()
    with pytest.raises(oss2.exceptions.OssError):
        backend.list_parts("root/t/big.bin", "upload-123")


# ── Multipart lifecycle ───────────────────────────────────────────────


def test_complete_multipart_upload_converts_part_info(backend):
    parts = [
        PartInfo(part_number=1, upload_url="", etag="etag-1"),
        PartInfo(part_number=2, upload_url="", etag="etag-2"),
    ]
    backend.complete_multipart_upload("root/t/big.bin", "upload-123", parts)
    call = backend._bucket.complete_multipart_upload.call_args
    assert call.args[:2] == ("root/t/big.bin", "upload-123")
    oss_parts = call.args[2]
    assert all(isinstance(p, oss2.models.PartInfo) for p in oss_parts)
    assert [(p.part_number, p.etag) for p in oss_parts] == [
        (1, "etag-1"),
        (2, "etag-2"),
    ]


def test_complete_multipart_upload_error_reraises(backend):
    backend._bucket.complete_multipart_upload.side_effect = _oss_error()
    with pytest.raises(oss2.exceptions.OssError):
        backend.complete_multipart_upload("root/t/big.bin", "upload-123", [])


def test_abort_multipart_upload_delegates(backend):
    backend.abort_multipart_upload("root/t/big.bin", "upload-123")
    backend._bucket.abort_multipart_upload.assert_called_once_with(
        "root/t/big.bin", "upload-123"
    )


def test_abort_multipart_upload_error_reraises(backend):
    backend._bucket.abort_multipart_upload.side_effect = _oss_error()
    with pytest.raises(oss2.exceptions.OssError):
        backend.abort_multipart_upload("root/t/big.bin", "upload-123")


# ── Staging object management ─────────────────────────────────────────


def test_list_objects_caps_limit(backend):
    backend._bucket.list_objects.return_value = SimpleNamespace(
        object_list=[], is_truncated=False, next_marker=None
    )
    backend.list_objects("root/t/", 5000, None)
    _, kwargs = backend._bucket.list_objects.call_args
    assert kwargs == {"prefix": "root/t/", "marker": "", "max_keys": 1000}


def test_list_objects_maps_items_and_marker(backend):
    modified = datetime(2026, 8, 1, 12, 30, 45)
    backend._bucket.list_objects.return_value = SimpleNamespace(
        object_list=[
            SimpleNamespace(key="root/t/a.bin", size=10, last_modified=modified),
        ],
        is_truncated=False,
        next_marker="ignored-when-not-truncated",
    )
    listing = backend.list_objects("root/t/", 100, "marker-1")
    _, kwargs = backend._bucket.list_objects.call_args
    assert kwargs["marker"] == "marker-1"
    assert listing == ObjectListing(
        items=[ObjectItem(key="root/t/a.bin", size=10, last_modified=str(modified))],
        truncated=False,
        next_marker=None,
    )


def test_list_objects_truncated_next_marker(backend):
    backend._bucket.list_objects.return_value = SimpleNamespace(
        object_list=[
            SimpleNamespace(key="k", size=1, last_modified=datetime(2026, 1, 1))
        ],
        is_truncated=True,
        next_marker="next-page",
    )
    listing = backend.list_objects("root/t/", 100, None)
    assert listing.truncated is True
    assert listing.next_marker == "next-page"


def test_list_objects_error_reraises(backend):
    backend._bucket.list_objects.side_effect = _oss_error()
    with pytest.raises(oss2.exceptions.OssError):
        backend.list_objects("root/t/", 100, None)


# ── Delete semantics ──────────────────────────────────────────────────


def test_delete_object_success_delegates(backend):
    backend.delete_object("root/t/f.bin")
    backend._bucket.delete_object.assert_called_once_with("root/t/f.bin")


def test_delete_object_no_such_key_idempotent(backend):
    backend._bucket.delete_object.side_effect = _no_such_key()
    backend.delete_object("root/t/f.bin")  # must not raise
    backend._bucket.delete_object.assert_called_once_with("root/t/f.bin")


def test_delete_object_error_reraises(backend):
    backend._bucket.delete_object.side_effect = _oss_error()
    with pytest.raises(oss2.exceptions.OssError):
        backend.delete_object("root/t/f.bin")
