"""Unit tests for the Session file URL projectors.

Covers the AliyunAckSessionFileUrlProjector fidelity + guard rows
(D-01 / D-03 / D-06):
  - query preserved byte-for-byte (incl. multipart uploadId/partNumber
    and response-content-disposition params)
  - scheme/host replaced by proxy_base_url, /api/v1/file-transfer-proxy/
    path prefix prepended
  - non-aliyun tenants pass through untouched with a
    SESSION_URL_PROJECTOR_PASSTHROUGH warning
  - aliyun with an empty proxy_base_url raises
    SessionFileTransferProxyUnavailableError (503)
"""

import logging
from urllib.parse import urlsplit

import pytest

from secbaas.community.api.session_file_sharing import (
    SessionFileTransferProxyUnavailableError,
)
from secbaas.community.plugins.file_transfer import NoopSessionFileUrlProjector
from secbaas.community.plugins.file_transfer.aliyun_ack import (
    AliyunAckSessionFileUrlProjector,
)
from secbaas.community.plugins.file_transfer.aliyun_ack._projector import (
    logger as _projector_logger,
)
from secbaas.community.spi.file_transfer import SessionFileUrlProjector

_ORIGINAL_UPLOAD_URL = (
    "https://bucket.internal-oss.example.com/baas-file-transfer/dev/t/sess/abc/file.txt"
    "?OSSAccessKeyId=AK&Expires=169&Signature=sig%2F%3D"
    "&response-content-disposition=attachment%3B%20filename%3D%22a%20b.txt%22"
)

_MULTIPART_PART_URL = (
    "https://bucket.internal-oss.example.com/baas-file-transfer/dev/t/sess/abc/big.bin"
    "?uploadId=SESSION123&partNumber=2&OSSAccessKeyId=AK&Signature=sigXYZ"
)


@pytest.fixture(autouse=True)
def _enable_log_propagation():
    """BareLoggerPlugin sets propagate=False, which breaks caplog.

    Patch the logger the projector actually uses — derived from the module
    constant instead of a hardcoded second name, so a logger rename cannot
    silently turn this fixture back into a no-op (IN-03/88).
    """
    logger = logging.getLogger(_projector_logger.name)
    old = logger.propagate
    logger.propagate = True
    yield
    logger.propagate = old


@pytest.fixture
def aliyun_projector():
    return AliyunAckSessionFileUrlProjector(
        proxy_base_url="https://bff.example.com",
        deploy_tenant="aliyun",
    )


class TestAliyunAckProjectionFidelity:
    """D-03: pure urlsplit transform — scheme/host replaced, path prefixed,
    query byte-identical."""

    def test_projection_preserves_query_byte_for_byte(self, aliyun_projector):
        original = _ORIGINAL_UPLOAD_URL

        projected = aliyun_projector.project(original)

        p = urlsplit(projected)
        assert p.scheme == "https"
        assert p.netloc == "bff.example.com"
        assert p.path == (
            "/api/v1/file-transfer-proxy/baas-file-transfer/dev/t/sess/abc/file.txt"
        )
        assert p.query == urlsplit(original).query
        assert "OSSAccessKeyId=AK" in p.query
        assert "Signature=sig%2F%3D" in p.query

    def test_multipart_part_url_query_params_preserved(self, aliyun_projector):
        original = _MULTIPART_PART_URL

        projected = aliyun_projector.project(original)

        p = urlsplit(projected)
        assert p.scheme == "https"
        assert p.netloc == "bff.example.com"
        assert p.path.startswith("/api/v1/file-transfer-proxy/")
        assert p.query == urlsplit(original).query
        assert "uploadId=SESSION123" in p.query
        assert "partNumber=2" in p.query

    def test_proxy_base_url_trailing_slash_stripped(self):
        projector = AliyunAckSessionFileUrlProjector(
            proxy_base_url="https://bff.example.com/",
            deploy_tenant="aliyun",
        )

        projected = projector.project(_ORIGINAL_UPLOAD_URL)

        p = urlsplit(projected)
        assert p.netloc == "bff.example.com"
        assert (
            p.path
            == "/api/v1/file-transfer-proxy" + urlsplit(_ORIGINAL_UPLOAD_URL).path
        )

    def test_projection_implements_protocol(self, aliyun_projector):
        assert isinstance(aliyun_projector, SessionFileUrlProjector)


class TestPassthroughGuards:
    """D-01: any tenant other than aliyun passes the URL through
    untouched with a structured WARNING — main-site misconfig immunity."""

    @pytest.mark.parametrize("deploy_tenant", ["", "SIGMA", "aliyun_ack"])
    def test_non_aliyun_ack_tenant_passthrough(self, deploy_tenant, caplog):
        projector = AliyunAckSessionFileUrlProjector(
            proxy_base_url="https://bff.example.com",
            deploy_tenant=deploy_tenant,
        )

        with caplog.at_level(logging.WARNING):
            result = projector.project(_ORIGINAL_UPLOAD_URL)

        assert result == _ORIGINAL_UPLOAD_URL
        assert any(
            "SESSION_URL_PROJECTOR_PASSTHROUGH" in record.getMessage()
            for record in caplog.records
        )


class TestProxyUnavailableGuard:
    """D-06: aliyun with an empty proxy_base_url refuses instead of
    handing out a bare OSS URL."""

    def test_proxy_missing_raises(self):
        projector = AliyunAckSessionFileUrlProjector(
            proxy_base_url="",
            deploy_tenant="aliyun",
        )

        with pytest.raises(SessionFileTransferProxyUnavailableError) as exc_info:
            projector.project(_ORIGINAL_UPLOAD_URL)

        assert exc_info.value.error_code == "SESSION_FILE_TRANSFER_PROXY_UNAVAILABLE"
        assert exc_info.value.http_status == 503
        assert "proxy_base_url" in exc_info.value.reason
        assert "deploy_tenant=aliyun" in exc_info.value.reason


class TestProxyBaseUrlConstructionValidation:
    """WR-02/89: a malformed non-empty proxy_base_url for the aliyun tenant
    fails closed at construction (the schema ``pattern`` never sees YAML
    override values) instead of emitting a relative client URL."""

    @pytest.mark.parametrize(
        "malformed",
        [
            "bff.example.com",  # missing scheme
            "https://",  # empty authority
            "ftp://bff.example.com",  # non-http scheme
            "/just/a/path",  # scheme-less relative
        ],
    )
    def test_aliyun_malformed_base_raises_at_construction(self, malformed):
        with pytest.raises(SessionFileTransferProxyUnavailableError) as exc_info:
            AliyunAckSessionFileUrlProjector(
                proxy_base_url=malformed,
                deploy_tenant="aliyun",
            )

        assert exc_info.value.error_code == "SESSION_FILE_TRANSFER_PROXY_UNAVAILABLE"
        assert "proxy_base_url" in exc_info.value.reason
        assert "not a valid absolute http(s) URL" in exc_info.value.reason

    def test_aliyun_empty_base_still_constructs(self):
        """D-06: the request-time 503 for the empty base is preserved."""
        projector = AliyunAckSessionFileUrlProjector(
            proxy_base_url="",
            deploy_tenant="aliyun",
        )

        with pytest.raises(SessionFileTransferProxyUnavailableError):
            projector.project(_ORIGINAL_UPLOAD_URL)

    def test_aliyun_valid_base_constructs(self):
        projector = AliyunAckSessionFileUrlProjector(
            proxy_base_url="https://bff.example.com",
            deploy_tenant="aliyun",
        )

        assert projector._proxy_base_url == "https://bff.example.com"

    @pytest.mark.parametrize("deploy_tenant", ["", "SIGMA"])
    def test_non_aliyun_malformed_base_passthrough_unchanged(
        self, deploy_tenant, caplog
    ):
        """D-01: non-aliyun tenants never read the base — constructing with
        a malformed one must not fail main-site-like deployments."""
        projector = AliyunAckSessionFileUrlProjector(
            proxy_base_url="bff.example.com",
            deploy_tenant=deploy_tenant,
        )

        with caplog.at_level(logging.WARNING):
            result = projector.project(_ORIGINAL_UPLOAD_URL)

        assert result == _ORIGINAL_UPLOAD_URL
        assert any(
            "SESSION_URL_PROJECTOR_PASSTHROUGH" in record.getMessage()
            for record in caplog.records
        )


class TestNoopSessionFileUrlProjector:
    """Noop matrix rows: identity passthrough for every non-aliyun
    tenant (row 1), and the D-06 refusal when the stub projector is
    selected for aliyun (row 3 — naked-URL leak prevention)."""

    def test_noop_identity_passthrough_default_tenant(self):
        projector = NoopSessionFileUrlProjector()

        result = projector.project(_MULTIPART_PART_URL)

        assert result == _MULTIPART_PART_URL
        assert urlsplit(result).query == urlsplit(_MULTIPART_PART_URL).query

    @pytest.mark.parametrize("deploy_tenant", ["", "SIGMA"])
    def test_noop_identity_passthrough_explicit_tenant(self, deploy_tenant):
        projector = NoopSessionFileUrlProjector(deploy_tenant=deploy_tenant)

        result = projector.project(_ORIGINAL_UPLOAD_URL)

        assert result == _ORIGINAL_UPLOAD_URL

    def test_stub_raises_for_aliyun_ack_tenant(self):
        projector = NoopSessionFileUrlProjector(deploy_tenant="aliyun")

        with pytest.raises(SessionFileTransferProxyUnavailableError) as exc_info:
            projector.project(_ORIGINAL_UPLOAD_URL)

        assert exc_info.value.error_code == "SESSION_FILE_TRANSFER_PROXY_UNAVAILABLE"
        assert exc_info.value.http_status == 503
        assert "'stub'" in exc_info.value.reason
        assert "aliyun_ack" in exc_info.value.reason

    def test_noop_implements_protocol(self):
        assert isinstance(NoopSessionFileUrlProjector(), SessionFileUrlProjector)
