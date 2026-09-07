"""Conformance contract for SessionFileUrlProjector implementations."""

from __future__ import annotations

from urllib.parse import urlsplit

from secbaas.community.plugins.file_transfer import (
    AliyunAckSessionFileUrlProjector,
    NoopSessionFileUrlProjector,
)
from secbaas.community.spi.file_transfer import SessionFileUrlProjector

# A presigned-style OSS URL: the query carries the signature-bound
# parameters that any projector must forward byte-for-byte (D-03).
_PRESIGNED_URL = (
    "https://my-bucket.oss-cn-hangzhou.aliyuncs.com/staging/tenant-42/"
    "transfer-abc/upload-file.bin?OSSAccessKeyId=AKIDEXAMPLE&Expires="
    "1735689600&Signature=abc123%2F%2B%3D&response-content-disposition="
    "attachment%3Bfilename%3Dreport.pdf"
)


class SessionFileUrlProjectorContract:
    """Abstract conformance contract for SessionFileUrlProjector implementations.

    Every SessionFileUrlProjector implementation must pass these tests.
    """

    plugin: SessionFileUrlProjector

    def test_project_returns_str(self) -> None:
        projected = self.plugin.project("https://oss.example.com/key/blob.bin")
        assert isinstance(projected, str)
        assert projected

    def test_project_preserves_query_byte_for_byte(self) -> None:
        projected = self.plugin.project(_PRESIGNED_URL)
        assert urlsplit(projected).query == urlsplit(_PRESIGNED_URL).query


class TestNoopSessionFileUrlProjector(SessionFileUrlProjectorContract):
    def setup_method(self) -> None:
        self.plugin = NoopSessionFileUrlProjector()

    def test_identity_returns_url_unchanged(self) -> None:
        assert self.plugin.project(_PRESIGNED_URL) == _PRESIGNED_URL


class TestAliyunAckSessionFileUrlProjector(SessionFileUrlProjectorContract):
    def setup_method(self) -> None:
        self.plugin = AliyunAckSessionFileUrlProjector(
            proxy_base_url="https://bff.example.com",
            deploy_tenant="ALIYUN_ACK",
        )

    def test_projected_path_has_proxy_prefix_and_original_suffix(self) -> None:
        source = (
            "https://my-bucket.oss-cn-hangzhou.aliyuncs.com/"
            "staging/tenant-42/transfer-abc/upload-file.bin"
        )
        projected = self.plugin.project(source)
        assert urlsplit(projected).path.startswith("/api/v1/file-transfer-proxy/")
        assert urlsplit(projected).path.endswith(urlsplit(source).path)

    def test_projected_scheme_and_netloc_replaced(self) -> None:
        source = "http://oss.example.com/staging/obj.bin?x=1"
        projected = self.plugin.project(source)
        assert urlsplit(projected).scheme == "https"
        assert urlsplit(projected).netloc == "bff.example.com"
