"""Unit tests for AliyunOssFileTransferBackend.

Tests all methods of the Aliyun OSS implementation with mocked oss2 SDK.
"""

from unittest.mock import MagicMock, patch

import pytest

from secbaas.community.plugins.file_transfer._aliyun_oss import (
    AliyunOssFileTransferBackend,
)
from secbaas.community.spi.file_transfer import PartInfo


@pytest.fixture
def mock_secret_store():
    store = MagicMock()
    store.get_secret.side_effect = lambda key: {
        "secbaas.oss.access_key_id": "test-ak",
        "secbaas.oss.access_key_secret": "test-sk",
    }[key]
    return store


@pytest.fixture
def mock_bucket():
    return MagicMock()


@pytest.fixture
def backend(mock_secret_store, mock_bucket):
    with (
        patch(
            "secbaas.community.plugins.file_transfer._aliyun_oss.oss2.Auth"
        ) as mock_auth,
        patch(
            "secbaas.community.plugins.file_transfer._aliyun_oss.oss2.Bucket",
            return_value=mock_bucket,
        ),
    ):
        mock_auth.return_value = MagicMock()
        return AliyunOssFileTransferBackend(secret_store=mock_secret_store)


class TestGenerateUploadUrl:
    def test_generate_upload_url(self, backend, mock_bucket):
        mock_bucket.sign_url.return_value = "https://oss.example.com/upload?token=abc"
        result = backend.generate_upload_url("path/to/file", 3600)
        assert result == "https://oss.example.com/upload?token=abc"
        mock_bucket.sign_url.assert_called_once_with("PUT", "path/to/file", 3600)


class TestCheckObjectExists:
    def test_object_exists(self, backend, mock_bucket):
        mock_bucket.head_object.return_value = MagicMock()
        assert backend.check_object_exists("path/to/file") is True

    def test_object_not_exists(self, backend, mock_bucket):
        import oss2

        mock_bucket.head_object.side_effect = oss2.exceptions.NoSuchKey(
            status=404,
            headers={},
            body="",
            details={"Code": "NoSuchKey", "Message": ""},
        )
        assert backend.check_object_exists("path/to/file") is False


class TestGenerateDownloadUrl:
    def test_generate_download_url(self, backend, mock_bucket):
        mock_bucket.sign_url.return_value = "https://oss.example.com/dl?token=abc"
        result = backend.generate_download_url("path/to/file", 3600)
        assert result == "https://oss.example.com/dl?token=abc"
        mock_bucket.sign_url.assert_called_once_with("GET", "path/to/file", 3600)


class TestMultipartUpload:
    def test_initiate_multipart_upload(self, backend, mock_bucket):
        init_result = MagicMock()
        init_result.upload_id = "upload-123"
        mock_bucket.init_multipart_upload.return_value = init_result
        mock_bucket.sign_url.return_value = "https://oss.example.com/mp-upload?part=1"

        result = backend.initiate_multipart_upload("path/to/file", 3600, 2)

        assert result.session_id == "upload-123"
        assert result.part_count == 2
        assert len(result.parts) == 2
        assert result.parts[0].part_number == 1
        assert result.parts[1].part_number == 2
        mock_bucket.init_multipart_upload.assert_called_once_with("path/to/file")

    def test_list_parts(self, backend, mock_bucket):
        part1 = MagicMock()
        part1.part_number = 1
        part1.etag = "etag-1"
        part2 = MagicMock()
        part2.part_number = 2
        part2.etag = "etag-2"

        list_result = MagicMock()
        list_result.parts = [part1, part2]
        mock_bucket.list_parts.return_value = list_result

        result = backend.list_parts("path/to/file", "upload-123")

        assert len(result) == 2
        assert result[0].part_number == 1
        assert result[0].etag == "etag-1"
        assert result[1].part_number == 2
        assert result[1].etag == "etag-2"

    def test_complete_multipart_upload(self, backend, mock_bucket):
        parts = [
            PartInfo(part_number=1, upload_url="", etag="etag-1"),
            PartInfo(part_number=2, upload_url="", etag="etag-2"),
        ]
        backend.complete_multipart_upload("path/to/file", "upload-123", parts)

        mock_bucket.complete_multipart_upload.assert_called_once()
        call_args = mock_bucket.complete_multipart_upload.call_args
        assert call_args.args[0] == "path/to/file"
        assert call_args.args[1] == "upload-123"

    def test_abort_multipart_upload(self, backend, mock_bucket):
        backend.abort_multipart_upload("path/to/file", "upload-123")
        mock_bucket.abort_multipart_upload.assert_called_once_with(
            "path/to/file", "upload-123"
        )

    def test_initiate_multipart_upload_oss_error(self, backend, mock_bucket):
        import oss2

        mock_bucket.init_multipart_upload.side_effect = oss2.exceptions.OssError(
            status=500,
            headers={},
            body="",
            details={"Code": "Error", "Message": "fail"},
        )
        with pytest.raises(oss2.exceptions.OssError):
            backend.initiate_multipart_upload("path/to/file", 3600, 2)


class TestListObjects:
    def test_list_objects(self, backend, mock_bucket):
        obj1 = MagicMock()
        obj1.key = "key1"
        obj1.size = 100
        obj1.last_modified = "2025-01-01"

        list_result = MagicMock()
        list_result.object_list = [obj1]
        list_result.is_truncated = False
        list_result.next_marker = ""
        mock_bucket.list_objects.return_value = list_result

        result = backend.list_objects("prefix/", 10, None)

        assert len(result.items) == 1
        assert result.items[0].key == "key1"
        assert result.items[0].size == 100
        assert result.truncated is False

    def test_list_objects_truncated(self, backend, mock_bucket):
        list_result = MagicMock()
        list_result.object_list = []
        list_result.is_truncated = True
        list_result.next_marker = "marker-1"
        mock_bucket.list_objects.return_value = list_result

        result = backend.list_objects("prefix/", 1, None)

        assert result.truncated is True
        assert result.next_marker == "marker-1"

    def test_list_objects_caps_limit(self, backend, mock_bucket):
        list_result = MagicMock()
        list_result.object_list = []
        list_result.is_truncated = False
        list_result.next_marker = ""
        mock_bucket.list_objects.return_value = list_result

        backend.list_objects("prefix/", 5000, None)

        # limit should be capped at 1000
        call_kwargs = mock_bucket.list_objects.call_args.kwargs
        assert call_kwargs["max_keys"] == 1000

    def test_list_objects_with_marker(self, backend, mock_bucket):
        list_result = MagicMock()
        list_result.object_list = []
        list_result.is_truncated = False
        list_result.next_marker = ""
        mock_bucket.list_objects.return_value = list_result

        backend.list_objects("prefix/", 10, "prev-marker")

        call_kwargs = mock_bucket.list_objects.call_args.kwargs
        assert call_kwargs["marker"] == "prev-marker"


class TestDeleteObject:
    def test_delete_object(self, backend, mock_bucket):
        backend.delete_object("key-to-delete")
        mock_bucket.delete_object.assert_called_once_with("key-to-delete")
