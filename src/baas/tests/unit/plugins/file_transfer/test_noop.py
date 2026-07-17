"""Unit tests for NoopFileTransferBackend.

Tests all methods raise NotImplementedError with the correct message.
"""

import pytest

from secbaas.community.plugins.file_transfer._noop import NoopFileTransferBackend
from secbaas.community.spi.file_transfer import PartInfo


@pytest.fixture
def backend():
    return NoopFileTransferBackend()


_EXPECTED_MSG = (
    "File transfer is not configured. "
    "Set config.plugins.file_transfer to 'real' to enable."
)


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


def test_list_objects_raises(backend):
    with pytest.raises(NotImplementedError, match=_EXPECTED_MSG):
        backend.list_objects("prefix", 10, None)


def test_delete_object_raises(backend):
    with pytest.raises(NotImplementedError, match=_EXPECTED_MSG):
        backend.delete_object("key")


def test_build_staging_path_raises(backend):
    with pytest.raises(NotImplementedError, match=_EXPECTED_MSG):
        backend.build_staging_path("tenant", "tf-001", "file.txt")


def test_build_staging_prefix_raises(backend):
    with pytest.raises(NotImplementedError, match=_EXPECTED_MSG):
        backend.build_staging_prefix("tenant")
