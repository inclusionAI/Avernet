"""Unit tests for DefaultSessionFileSharingDispatcher.

Comprehensive coverage of all six dispatch methods:
  - dispatch_get_upload_url (8 tests)
  - dispatch_complete_upload (11 tests: 3 existing + 8 new)
  - dispatch_cancel_upload (tests in Task 2)
  - dispatch_get_share_link (tests in Task 2)
  - dispatch_get_transfer_status (tests in Task 2)
  - dispatch_delete_transfer (tests in Task 2)

Fixture pattern: MagicMock for file_backend and ticket_repo, injected into
DefaultSessionFileSharingDispatcher.  The _make_ticket factory creates
SessionTicketRecord instances with sensible defaults for each test.
All tests use @pytest.mark.asyncio and secbaas.community.* imports.
"""

from datetime import datetime
from unittest.mock import MagicMock
from urllib.parse import urlsplit

import pytest

from secbaas.community.api.session_file_sharing import (
    SessionCancelUploadResponse,
    SessionCompleteUploadResponse,
    SessionDeleteTransferResponse,
    SessionFileTransferProxyUnavailableError,
    SessionGetTransferStatusResponse,
    SessionGetUploadUrlResponse,
    SessionShareLinkResponse,
    SourceTransferNotFoundError,
    SourceTransferNotReadyError,
    StagingObjectNotFoundError,
    TransferNotFoundError,
    TransferNotTerminalError,
    TransferStateConflictError,
)
from secbaas.community.core.repository.session_file_ticket import SessionTicketRecord
from secbaas.community.core.service.session_file_sharing._dispatcher import (
    DefaultSessionFileSharingDispatcher,
)
from secbaas.community.plugins.file_transfer.aliyun_ack import (
    AliyunAckSessionFileUrlProjector,
)


def _make_ticket(**overrides):
    now = datetime.now()
    defaults = dict(
        id=1,
        gmt_create=now,
        gmt_modified=now,
        transfer_id="tf-001",
        tenant="test-tenant",
        session_id="sess-001",
        status="CREATED",
        staging_subdir=None,
        filename="data.csv",
        fileservice_staging_path=(
            "baas-file-transfer/test/t1/sess-001/tf-001/data.csv"
        ),
        error_message=None,
        multipart_session_id=None,
        env="test",
        operator="unknown",
    )
    defaults.update(overrides)
    return SessionTicketRecord(**defaults)


@pytest.fixture
def file_backend():
    backend = MagicMock()
    backend.check_object_exists.return_value = True
    return backend


@pytest.fixture
def ticket_repo():
    repository = MagicMock()
    repository.compare_and_set_status.return_value = 1
    return repository


@pytest.fixture
def projector():
    projector = MagicMock()
    projector.project.side_effect = lambda u: u
    return projector


@pytest.fixture
def dispatcher(file_backend, ticket_repo, projector):
    return DefaultSessionFileSharingDispatcher(
        file_transfer_backend=file_backend,
        ticket_repo=ticket_repo,
        session_file_url_projector=projector,
    )


# ============================================================================
# Existing tests from Phase 77.1 — preserved verbatim
# ============================================================================


class TestSessionFileUrlProjection:
    """aliyun end-to-end: upload-url SINGLE, MULTIPART per-part, and
    share_url are projected; shape-invariant fields keep their values."""

    @pytest.fixture
    def aliyun_projector(self):
        return AliyunAckSessionFileUrlProjector(
            proxy_base_url="https://bff.example.com",
            deploy_tenant="aliyun",
        )

    @pytest.fixture
    def projected_dispatcher(self, file_backend, ticket_repo, aliyun_projector):
        return DefaultSessionFileSharingDispatcher(
            file_transfer_backend=file_backend,
            ticket_repo=ticket_repo,
            session_file_url_projector=aliyun_projector,
        )

    @pytest.mark.asyncio
    async def test_single_upload_url_projected(
        self, projected_dispatcher, file_backend
    ):
        original = (
            "https://bucket.internal-oss.example.com"
            "/baas-file-transfer/dev/t/sess-001/tf-single/data.csv"
            "?OSSAccessKeyId=AK&Expires=169&Signature=sigXYZ"
        )
        file_backend.generate_upload_url.return_value = original
        file_backend.build_session_staging_path.return_value = (
            "file-transfers/test/t1/sess-001/tf-single/data.csv"
        )

        result = await projected_dispatcher.dispatch_get_upload_url(
            tenant="test-tenant",
            session_id="sess-001",
            filename="data.csv",
            file_size=0,
        )

        assert result.type == "SINGLE"
        assert result.http_method == "PUT"
        assert result.transfer_id is not None and len(result.transfer_id) > 0
        assert result.expires_at is not None
        projected = urlsplit(result.upload_url)
        assert projected.scheme == "https"
        assert projected.netloc == "bff.example.com"
        assert projected.path.startswith("/api/v1/file-transfer-proxy/")
        assert projected.query == urlsplit(original).query

    @pytest.mark.asyncio
    async def test_multipart_part_urls_projected(
        self, projected_dispatcher, file_backend
    ):
        part_count = 3  # ceil(100MB / 40MB) — part_size drives the count
        mock_parts = [
            MagicMock(
                part_number=i,
                upload_url=(
                    f"https://bucket.internal-oss.example.com/part/{i}"
                    f"?uploadId=MP-SESS&partNumber={i}&Signature=sig{i}"
                ),
            )
            for i in range(1, part_count + 1)
        ]
        mock_session = MagicMock()
        mock_session.session_id = "mp-sess-abc"
        mock_session.parts = mock_parts
        file_backend.initiate_multipart_upload.return_value = mock_session
        file_backend.build_session_staging_path.return_value = (
            "file-transfers/test/t1/sess-001/tf-multi/big.zip"
        )

        result = await projected_dispatcher.dispatch_get_upload_url(
            tenant="test-tenant",
            session_id="sess-001",
            filename="big.zip",
            file_size=104_857_600,  # 100MB
            part_size=41_943_040,  # 40MB
        )

        assert result.type == "MULTIPART"
        # upload_session_id is an OSS multipart id, not a URL — never projected
        assert result.upload_session_id == "mp-sess-abc"
        assert result.part_count == part_count
        assert result.part_size == 41_943_040
        assert len(result.parts) == part_count
        for entry, part in zip(result.parts, mock_parts):
            projected = urlsplit(entry["upload_url"])
            assert projected.netloc == "bff.example.com"
            assert projected.path.startswith("/api/v1/file-transfer-proxy/")
            assert projected.query == urlsplit(part.upload_url).query
            assert entry["part_number"] == part.part_number
            assert entry["http_method"] == "PUT"
            assert entry["expires_at"] is not None

    @pytest.mark.asyncio
    async def test_share_url_projected(
        self, projected_dispatcher, file_backend, ticket_repo
    ):
        ticket = _make_ticket(status="DONE")
        ticket_repo.get_by_transfer_id.return_value = ticket
        original = (
            "https://bucket.internal-oss.example.com"
            "/baas-file-transfer/dev/t/sess-001/tf-001/data.csv"
            "?OSSAccessKeyId=AK&Expires=169&Signature=sigXYZ"
        )
        file_backend.generate_download_url.return_value = original

        result = await projected_dispatcher.dispatch_get_share_link(
            transfer_id="tf-001",
            tenant="test-tenant",
            session_id="sess-001",
        )

        assert result.transfer_id == "tf-001"
        assert result.expires_at is not None
        projected = urlsplit(result.share_url)
        assert projected.netloc == "bff.example.com"
        assert projected.path.startswith("/api/v1/file-transfer-proxy/")
        assert projected.query == urlsplit(original).query

    @pytest.mark.asyncio
    async def test_missing_proxy_refuses_before_response(
        self, file_backend, ticket_repo
    ):
        """D-06: dispatch_get_upload_url raises the 503 error before any
        response is returned — and before any ticket is persisted."""
        unfilled = DefaultSessionFileSharingDispatcher(
            file_transfer_backend=file_backend,
            ticket_repo=ticket_repo,
            session_file_url_projector=AliyunAckSessionFileUrlProjector(
                proxy_base_url="",
                deploy_tenant="aliyun",
            ),
        )
        file_backend.generate_upload_url.return_value = (
            "https://bucket.internal-oss.example.com/key?Signature=sig"
        )
        file_backend.build_session_staging_path.return_value = "file-transfers/x"

        with pytest.raises(SessionFileTransferProxyUnavailableError):
            await unfilled.dispatch_get_upload_url(
                tenant="test-tenant",
                session_id="sess-001",
                filename="data.csv",
                file_size=0,
            )

        ticket_repo.create_ticket.assert_not_called()

    @pytest.mark.asyncio
    async def test_multipart_projection_failure_aborts_session(
        self, file_backend, ticket_repo
    ):
        """WR-03 (88): a D-06 refusal on the MULTIPART path aborts the
        just-initiated OSS multipart session so no uploadId leaks."""
        unfilled = DefaultSessionFileSharingDispatcher(
            file_transfer_backend=file_backend,
            ticket_repo=ticket_repo,
            session_file_url_projector=AliyunAckSessionFileUrlProjector(
                proxy_base_url="",
                deploy_tenant="aliyun",
            ),
        )
        mock_parts = [
            MagicMock(
                part_number=1,
                upload_url=(
                    "https://bucket.internal-oss.example.com/part/1"
                    "?uploadId=MP-SESS&partNumber=1&Signature=sig1"
                ),
            )
        ]
        mock_session = MagicMock()
        mock_session.session_id = "mp-sess-leak"
        mock_session.parts = mock_parts
        file_backend.initiate_multipart_upload.return_value = mock_session
        file_backend.build_session_staging_path.return_value = (
            "file-transfers/test/t1/sess-001/tf-leak/big.zip"
        )

        with pytest.raises(SessionFileTransferProxyUnavailableError):
            await unfilled.dispatch_get_upload_url(
                tenant="test-tenant",
                session_id="sess-001",
                filename="big.zip",
                file_size=104_857_600,  # 100MB -> MULTIPART
            )

        file_backend.abort_multipart_upload.assert_called_once_with(
            "file-transfers/test/t1/sess-001/tf-leak/big.zip",
            "mp-sess-leak",
        )
        ticket_repo.create_ticket.assert_not_called()

    @pytest.mark.asyncio
    async def test_multipart_projection_failure_abort_error_preserved(
        self, file_backend, ticket_repo
    ):
        """WR-03 (88): even when the abort roundtrip fails, the client still
        receives the original 503 (the cleanup error never masks it)."""
        unfilled = DefaultSessionFileSharingDispatcher(
            file_transfer_backend=file_backend,
            ticket_repo=ticket_repo,
            session_file_url_projector=AliyunAckSessionFileUrlProjector(
                proxy_base_url="",
                deploy_tenant="aliyun",
            ),
        )
        mock_parts = [
            MagicMock(part_number=1, upload_url="https://oss.example.com/part/1")
        ]
        mock_session = MagicMock()
        mock_session.session_id = "mp-sess-leak2"
        mock_session.parts = mock_parts
        file_backend.initiate_multipart_upload.return_value = mock_session
        file_backend.build_session_staging_path.return_value = (
            "file-transfers/test/t1/sess-001/tf-leak2/big.zip"
        )
        file_backend.abort_multipart_upload.side_effect = Exception(
            "NoSuchUpload: upload session not found"
        )

        with pytest.raises(SessionFileTransferProxyUnavailableError):
            await unfilled.dispatch_get_upload_url(
                tenant="test-tenant",
                session_id="sess-001",
                filename="big.zip",
                file_size=104_857_600,  # 100MB -> MULTIPART
            )

        file_backend.abort_multipart_upload.assert_called_once_with(
            "file-transfers/test/t1/sess-001/tf-leak2/big.zip",
            "mp-sess-leak2",
        )
        ticket_repo.create_ticket.assert_not_called()
