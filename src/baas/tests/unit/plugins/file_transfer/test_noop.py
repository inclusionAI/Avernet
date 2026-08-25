"""Unit tests for NoopFileTransferBackend.

Tests all methods raise NotImplementedError with the correct message.
"""

import pytest

from secbaas.community.plugins.file_transfer._noop import NoopFileTransferBackend
from secbaas.community.spi.file_transfer import FileTransferBackend, PartInfo


@pytest.fixture
def backend():
    return NoopFileTransferBackend()


class _RealBackend(FileTransferBackend):
    """A minimal real backend that does not override ``disabled``.

    Exercises the protocol's default ``disabled`` implementation
    (a real backend is enabled unless it says otherwise).
    """

    def generate_upload_url(
        self, staging_path: str, expire_seconds: int, content_type: str | None = None
    ) -> str:
        return ""


def test_real_backend_reports_enabled_by_default():
    """A backend that does not override ``disabled`` is enabled by default."""
    assert _RealBackend().disabled is False


_EXPECTED_MSG = "file_transfer is disabled in this deployment"


def test_noop_reports_disabled(backend):
    assert backend.disabled is True


def test_generate_upload_url_raises(backend):
    with pytest.raises(NotImplementedError, match=_EXPECTED_MSG):
        backend.generate_upload_url("path", 3600)


def test_check_object_exists_raises(backend):
    with pytest.raises(NotImplementedError, match=_EXPECTED_MSG):
        backend.check_object_exists("path")


def test_generate_download_url_raises(backend):
    with pytest.raises(NotImplementedError, match=_EXPECTED_MSG):
        backend.generate_download_url("path", 3600)


def test_initiate_multipart_upload_raises(backend):
    with pytest.raises(NotImplementedError, match=_EXPECTED_MSG):
        backend.initiate_multipart_upload("path", 3600, 2)


def test_list_parts_raises(backend):
    with pytest.raises(NotImplementedError, match=_EXPECTED_MSG):
        backend.list_parts("path", "session-1")


def test_complete_multipart_upload_raises(backend):
    part = PartInfo(part_number=1, upload_url="", etag="etag-1")
    with pytest.raises(NotImplementedError, match=_EXPECTED_MSG):
        backend.complete_multipart_upload("path", "session-1", [part])


def test_abort_multipart_upload_raises(backend):
    with pytest.raises(NotImplementedError, match=_EXPECTED_MSG):
        backend.abort_multipart_upload("path", "session-1")


def test_delete_object_raises(backend):
    with pytest.raises(NotImplementedError, match=_EXPECTED_MSG):
        backend.delete_object("key")


def test_build_staging_path_raises(backend):
    with pytest.raises(NotImplementedError, match=_EXPECTED_MSG):
        backend.build_staging_path("tenant", "tf-001", "file.txt")


def test_build_session_staging_path_raises(backend):
    with pytest.raises(NotImplementedError, match=_EXPECTED_MSG):
        backend.build_session_staging_path("tenant", "session-1", "tf-001", "file.txt")
