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

import pytest

from secbaas.community.api.session_file_sharing import (
    SessionCancelUploadResponse,
    SessionCompleteUploadResponse,
    SessionDeleteTransferResponse,
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
    return MagicMock()


@pytest.fixture
def dispatcher(file_backend, ticket_repo):
    return DefaultSessionFileSharingDispatcher(
        file_transfer_backend=file_backend,
        ticket_repo=ticket_repo,
    )


# ============================================================================
# Existing tests from Phase 77.1 — preserved verbatim
# ============================================================================


class TestDispatchCompleteUploadSingle:
    """SINGLE upload path in dispatch_complete_upload."""

    @pytest.mark.asyncio
    async def test_object_missing_raises_staging_object_not_found_error(
        self, dispatcher, file_backend, ticket_repo
    ):
        """When check_object_exists returns False, raise StagingObjectNotFoundError.

        This is the RED test — currently the dispatcher raises ValueError
        instead of StagingObjectNotFoundError, so this test MUST fail until
        the implementation is updated.
        """
        ticket = _make_ticket()
        ticket_repo.get_by_transfer_id.return_value = ticket
        file_backend.check_object_exists.return_value = False

        with pytest.raises(StagingObjectNotFoundError) as exc_info:
            await dispatcher.dispatch_complete_upload(transfer_id="tf-001")

        assert exc_info.value.staging_path == ticket.fileservice_staging_path

    @pytest.mark.asyncio
    async def test_object_exists_completes_normally(
        self, dispatcher, file_backend, ticket_repo
    ):
        """When check_object_exists returns True, complete to DONE."""
        ticket = _make_ticket()
        ticket_repo.get_by_transfer_id.return_value = ticket
        file_backend.check_object_exists.return_value = True

        result = await dispatcher.dispatch_complete_upload(transfer_id="tf-001")

        assert isinstance(result, SessionCompleteUploadResponse)
        assert result.transfer_id == "tf-001"
        assert result.status == "DONE"

    @pytest.mark.asyncio
    async def test_transfer_not_found_raises(self, dispatcher, ticket_repo):
        """When ticket is not found, raise TransferNotFoundError."""
        ticket_repo.get_by_transfer_id.return_value = None

        with pytest.raises(TransferNotFoundError):
            await dispatcher.dispatch_complete_upload(transfer_id="nonexistent")


# ============================================================================
# TestDispatchGetUploadUrl — all 8 test cases
# ============================================================================


class TestDispatchGetUploadUrl:
    """Tests for dispatch_get_upload_url covering SINGLE, MULTIPART, and validation."""

    @pytest.mark.asyncio
    async def test_single_upload_success(self, dispatcher, file_backend, ticket_repo):
        """file_size=0 routes to SINGLE mode with upload_url present."""
        file_backend.generate_upload_url.return_value = (
            "https://oss.example.com/put?token=abc"
        )
        file_backend.build_session_staging_path.return_value = (
            "file-transfers/test/t1/sess-001/tf-single/data.csv"
        )

        result = await dispatcher.dispatch_get_upload_url(
            tenant="test-tenant",
            session_id="sess-001",
            filename="data.csv",
            file_size=0,
        )

        assert isinstance(result, SessionGetUploadUrlResponse)
        assert result.type == "SINGLE"
        assert result.upload_url == "https://oss.example.com/put?token=abc"
        assert result.http_method == "PUT"
        assert result.transfer_id is not None and len(result.transfer_id) > 0
        assert result.expires_at is not None

    @pytest.mark.asyncio
    async def test_multipart_upload_success(
        self, dispatcher, file_backend, ticket_repo
    ):
        """file_size=200M (>100MB threshold) routes to MULTIPART mode."""
        part_count = 20
        mock_parts = [
            MagicMock(
                part_number=i,
                upload_url=f"https://oss.example.com/part/{i}",
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

        result = await dispatcher.dispatch_get_upload_url(
            tenant="test-tenant",
            session_id="sess-001",
            filename="big.zip",
            file_size=209_715_200,  # 200MB
        )

        assert isinstance(result, SessionGetUploadUrlResponse)
        assert result.type == "MULTIPART"
        assert result.upload_session_id == "mp-sess-abc"
        assert result.parts is not None and len(result.parts) == part_count
        assert result.part_size > 0
        assert result.part_count == part_count

    @pytest.mark.asyncio
    async def test_invalid_staging_subdir_traversal(
        self, dispatcher, file_backend, ticket_repo
    ):
        """staging_subdir='../etc' raises ValueError (400)."""
        with pytest.raises(ValueError):
            await dispatcher.dispatch_get_upload_url(
                tenant="test-tenant",
                session_id="sess-001",
                filename="data.csv",
                staging_subdir="../etc",
            )

    @pytest.mark.asyncio
    async def test_negative_file_size(self, dispatcher, file_backend, ticket_repo):
        """file_size=-1 raises ValueError (400)."""
        with pytest.raises(ValueError):
            await dispatcher.dispatch_get_upload_url(
                tenant="test-tenant",
                session_id="sess-001",
                filename="data.csv",
                file_size=-1,
            )

    @pytest.mark.asyncio
    async def test_empty_operator_defaults_to_unknown(
        self, dispatcher, file_backend, ticket_repo
    ):
        """operator='' defaults to 'unknown' — no crash; ticket created with operator='unknown'."""
        file_backend.generate_upload_url.return_value = (
            "https://oss.example.com/put?token=abc"
        )
        file_backend.build_session_staging_path.return_value = (
            "file-transfers/test/t1/sess-001/tf-op/data.csv"
        )

        await dispatcher.dispatch_get_upload_url(
            tenant="test-tenant",
            session_id="sess-001",
            filename="data.csv",
            operator="",
        )

        # Assert ticket_repo.create_ticket was called with operator="unknown"
        call_kwargs = ticket_repo.create_ticket.call_args.kwargs
        assert call_kwargs["operator"] == "unknown"

    @pytest.mark.asyncio
    async def test_zero_part_size_for_multipart(
        self, dispatcher, file_backend, ticket_repo
    ):
        """file_size=200M, part_size=0 raises ValueError (400)."""
        file_backend.build_session_staging_path.return_value = (
            "file-transfers/test/t1/sess-001/tf-badps/data.csv"
        )

        with pytest.raises(ValueError):
            await dispatcher.dispatch_get_upload_url(
                tenant="test-tenant",
                session_id="sess-001",
                filename="data.csv",
                file_size=209_715_200,  # 200MB
                part_size=0,
            )

    @pytest.mark.asyncio
    async def test_very_large_file_part_count_capped(
        self, dispatcher, file_backend, ticket_repo
    ):
        """file_size=100GB → dispatcher auto-adjusts part_size; no ValueError, type=MULTIPART."""
        # 100GB = 107_374_182_400 bytes, default 10MB parts = 10,240 parts > 10,000
        mock_parts = [
            MagicMock(part_number=1, upload_url="https://oss.example.com/part/1")
        ]
        mock_session = MagicMock()
        mock_session.session_id = "mp-sess-huge"
        mock_session.parts = mock_parts
        file_backend.initiate_multipart_upload.return_value = mock_session
        file_backend.build_session_staging_path.return_value = (
            "file-transfers/test/t1/sess-001/tf-huge/big.iso"
        )

        result = await dispatcher.dispatch_get_upload_url(
            tenant="test-tenant",
            session_id="sess-001",
            filename="big.iso",
            file_size=107_374_182_400,  # 100GB
        )

        assert isinstance(result, SessionGetUploadUrlResponse)
        assert result.type == "MULTIPART"
        # Part count should be capped at or below 10,000
        assert result.part_count <= 10_000

    @pytest.mark.asyncio
    async def test_operator_preserved(self, dispatcher, file_backend, ticket_repo):
        """operator='alice' → ticket_repo.create_ticket called with operator='alice'."""
        file_backend.generate_upload_url.return_value = (
            "https://oss.example.com/put?token=abc"
        )
        file_backend.build_session_staging_path.return_value = (
            "file-transfers/test/t1/sess-001/tf-alice/data.csv"
        )

        await dispatcher.dispatch_get_upload_url(
            tenant="test-tenant",
            session_id="sess-001",
            filename="data.csv",
            operator="alice",
        )

        call_kwargs = ticket_repo.create_ticket.call_args.kwargs
        assert call_kwargs["operator"] == "alice"

    @pytest.mark.asyncio
    async def test_single_upload_with_content_type(
        self, dispatcher, file_backend, ticket_repo
    ):
        """content_type='image/png' → passed to generate_upload_url as 3rd arg."""
        file_backend.generate_upload_url.return_value = (
            "https://oss.example.com/put?token=abc"
        )
        file_backend.build_session_staging_path.return_value = (
            "file-transfers/test/t1/sess-001/tf-ct/data.png"
        )

        await dispatcher.dispatch_get_upload_url(
            tenant="test-tenant",
            session_id="sess-001",
            filename="data.png",
            content_type="image/png",
        )

        call_args = file_backend.generate_upload_url.call_args
        assert call_args[0][2] == "image/png"

    @pytest.mark.asyncio
    async def test_single_upload_without_content_type(
        self, dispatcher, file_backend, ticket_repo
    ):
        """content_type not passed → generate_upload_url called with content_type=None."""
        file_backend.generate_upload_url.return_value = (
            "https://oss.example.com/put?token=abc"
        )
        file_backend.build_session_staging_path.return_value = (
            "file-transfers/test/t1/sess-001/tf-noct/data.csv"
        )

        await dispatcher.dispatch_get_upload_url(
            tenant="test-tenant",
            session_id="sess-001",
            filename="data.csv",
        )

        call_args = file_backend.generate_upload_url.call_args
        assert len(call_args[0]) == 3  # staging_path, expire_seconds, content_type=None
        assert call_args[0][2] is None

    @pytest.mark.asyncio
    async def test_content_type_empty_string_raises(
        self, dispatcher, file_backend, ticket_repo
    ):
        """content_type='' raises ValueError (D-04)."""
        with pytest.raises(ValueError):
            await dispatcher.dispatch_get_upload_url(
                tenant="test-tenant",
                session_id="sess-001",
                filename="data.csv",
                content_type="",
            )

    @pytest.mark.asyncio
    async def test_content_type_whitespace_raises(
        self, dispatcher, file_backend, ticket_repo
    ):
        """content_type='   ' raises ValueError (D-04)."""
        with pytest.raises(ValueError):
            await dispatcher.dispatch_get_upload_url(
                tenant="test-tenant",
                session_id="sess-001",
                filename="data.csv",
                content_type="   ",
            )


# ============================================================================
# TestDispatchCompleteUploadFull — tests 4-11 extending existing 3
# ============================================================================


class TestDispatchCompleteUploadFull:
    """Additional complete_upload tests beyond the 3 existing SINGLE-path tests."""

    @pytest.mark.asyncio
    async def test_complete_single_idempotent(
        self, dispatcher, file_backend, ticket_repo
    ):
        """ticket already DONE → returns DONE status, no update_status call."""
        ticket = _make_ticket(status="DONE")
        ticket_repo.get_by_transfer_id.return_value = ticket

        result = await dispatcher.dispatch_complete_upload(transfer_id="tf-001")

        assert isinstance(result, SessionCompleteUploadResponse)
        assert result.transfer_id == "tf-001"
        assert result.status == "DONE"
        # update_status should NOT be called for already-DONE tickets
        ticket_repo.update_status.assert_not_called()

    @pytest.mark.asyncio
    async def test_complete_terminal_state_rejects(
        self, dispatcher, file_backend, ticket_repo
    ):
        """ticket status=FAILED → raises ValueError with 'terminal state'."""
        ticket = _make_ticket(status="FAILED")
        ticket_repo.get_by_transfer_id.return_value = ticket

        with pytest.raises(ValueError) as exc_info:
            await dispatcher.dispatch_complete_upload(transfer_id="tf-001")

        assert "terminal state" in str(exc_info.value).lower()

    @pytest.mark.asyncio
    async def test_complete_multipart_success(
        self, dispatcher, file_backend, ticket_repo
    ):
        """ticket with multipart_session_id, backend.list_parts returns non-empty → DONE."""
        ticket = _make_ticket(multipart_session_id="mp-sess-01")
        ticket_repo.get_by_transfer_id.return_value = ticket
        file_backend.list_parts.return_value = [MagicMock(part_number=1)]

        result = await dispatcher.dispatch_complete_upload(transfer_id="tf-001")

        assert isinstance(result, SessionCompleteUploadResponse)
        assert result.transfer_id == "tf-001"
        assert result.status == "DONE"
        file_backend.complete_multipart_upload.assert_called_once()
        ticket_repo.update_status.assert_called_once_with("tf-001", "DONE")

    @pytest.mark.asyncio
    async def test_complete_multipart_no_parts(
        self, dispatcher, file_backend, ticket_repo
    ):
        """ticket with multipart_session_id, backend.list_parts returns empty → raises ValueError."""
        ticket = _make_ticket(multipart_session_id="mp-sess-01")
        ticket_repo.get_by_transfer_id.return_value = ticket
        file_backend.list_parts.return_value = []

        with pytest.raises(ValueError) as exc_info:
            await dispatcher.dispatch_complete_upload(transfer_id="tf-001")

        assert "No parts uploaded" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_complete_cas_conflict_resolved(
        self, dispatcher, file_backend, ticket_repo
    ):
        """update_status raises TransferStateConflictError, re-read returns DONE → returns idempotently."""
        ticket = _make_ticket()
        ticket_repo.get_by_transfer_id.return_value = ticket
        file_backend.check_object_exists.return_value = True

        done_ticket = _make_ticket(status="DONE")

        # First call returns the CREATED ticket, second call returns DONE
        ticket_repo.get_by_transfer_id.side_effect = [ticket, done_ticket]
        ticket_repo.update_status.side_effect = TransferStateConflictError(
            f"CAS conflict for {ticket.transfer_id}"
        )

        result = await dispatcher.dispatch_complete_upload(transfer_id="tf-001")

        assert isinstance(result, SessionCompleteUploadResponse)
        assert result.transfer_id == "tf-001"
        assert result.status == "DONE"

    @pytest.mark.asyncio
    async def test_complete_cas_conflict_reraise(
        self, dispatcher, file_backend, ticket_repo
    ):
        """update_status raises TransferStateConflictError, re-read returns non-DONE → re-raises."""
        ticket = _make_ticket()
        ticket_repo.get_by_transfer_id.return_value = ticket
        file_backend.check_object_exists.return_value = True

        still_created = _make_ticket(status="CREATED")

        ticket_repo.get_by_transfer_id.side_effect = [ticket, still_created]
        ticket_repo.update_status.side_effect = TransferStateConflictError(
            f"CAS conflict for {ticket.transfer_id}"
        )

        with pytest.raises(TransferStateConflictError):
            await dispatcher.dispatch_complete_upload(transfer_id="tf-001")

    @pytest.mark.asyncio
    async def test_ownership_mismatch(self, dispatcher, file_backend, ticket_repo):
        """ticket.session_id != provided session_id → raises TransferNotFoundError (does NOT reveal existence)."""
        ticket = _make_ticket(session_id="sess-001")
        ticket_repo.get_by_transfer_id.return_value = ticket

        with pytest.raises(TransferNotFoundError):
            await dispatcher.dispatch_complete_upload(
                transfer_id="tf-001",
                session_id="sess-other",
            )

    @pytest.mark.asyncio
    async def test_tenant_scope_respected(self, dispatcher, file_backend, ticket_repo):
        """get_by_transfer_id called with tenant param."""
        file_backend.check_object_exists.return_value = True

        await dispatcher.dispatch_complete_upload(
            transfer_id="tf-001",
            tenant="my-tenant",
        )

        # Verify repo was called with tenant param
        ticket_repo.get_by_transfer_id.assert_called_with("tf-001", tenant="my-tenant")


# ============================================================================
# TestCancelUpload — 8 test cases
# ============================================================================


class TestCancelUpload:
    """Tests for dispatch_cancel_upload covering success, idempotency, multipart, and errors."""

    @pytest.mark.asyncio
    async def test_cancel_upload_success(self, dispatcher, file_backend, ticket_repo):
        """ticket CREATED, no multipart → status CANCELLED."""
        ticket = _make_ticket()
        ticket_repo.get_by_transfer_id.return_value = ticket

        result = await dispatcher.dispatch_cancel_upload(transfer_id="tf-001")

        assert isinstance(result, SessionCancelUploadResponse)
        assert result.transfer_id == "tf-001"
        assert result.status == "CANCELLED"
        ticket_repo.update_status.assert_called_once_with("tf-001", "CANCELLED")

    @pytest.mark.asyncio
    async def test_cancel_idempotent_already_cancelled(
        self, dispatcher, file_backend, ticket_repo
    ):
        """ticket status CANCELLED → returns CANCELLED idempotently."""
        ticket = _make_ticket(status="CANCELLED")
        ticket_repo.get_by_transfer_id.return_value = ticket

        result = await dispatcher.dispatch_cancel_upload(transfer_id="tf-001")

        assert isinstance(result, SessionCancelUploadResponse)
        assert result.transfer_id == "tf-001"
        assert result.status == "CANCELLED"
        ticket_repo.update_status.assert_not_called()

    @pytest.mark.asyncio
    async def test_cancel_idempotent_already_done(
        self, dispatcher, file_backend, ticket_repo
    ):
        """ticket status DONE → returns DONE idempotently."""
        ticket = _make_ticket(status="DONE")
        ticket_repo.get_by_transfer_id.return_value = ticket

        result = await dispatcher.dispatch_cancel_upload(transfer_id="tf-001")

        assert isinstance(result, SessionCancelUploadResponse)
        assert result.transfer_id == "tf-001"
        assert result.status == "DONE"

    @pytest.mark.asyncio
    async def test_cancel_with_multipart_aborts(
        self, dispatcher, file_backend, ticket_repo
    ):
        """ticket has multipart_session_id → abort_multipart_upload called."""
        ticket = _make_ticket(multipart_session_id="mp-sess-01")
        ticket_repo.get_by_transfer_id.return_value = ticket

        result = await dispatcher.dispatch_cancel_upload(transfer_id="tf-001")

        assert isinstance(result, SessionCancelUploadResponse)
        assert result.transfer_id == "tf-001"
        assert result.status == "CANCELLED"
        file_backend.abort_multipart_upload.assert_called_once_with(
            ticket.fileservice_staging_path,
            ticket.multipart_session_id,
        )

    @pytest.mark.asyncio
    async def test_cancel_multipart_abort_no_such_upload_tolerated(
        self, dispatcher, file_backend, ticket_repo
    ):
        """abort_multipart_upload raises NoSuchUpload → caught, ticket still CANCELLED."""
        ticket = _make_ticket(multipart_session_id="mp-sess-01")
        ticket_repo.get_by_transfer_id.return_value = ticket
        file_backend.abort_multipart_upload.side_effect = Exception(
            "NoSuchUpload: upload session not found"
        )

        result = await dispatcher.dispatch_cancel_upload(transfer_id="tf-001")

        assert isinstance(result, SessionCancelUploadResponse)
        assert result.transfer_id == "tf-001"
        assert result.status == "CANCELLED"

    @pytest.mark.asyncio
    async def test_cancel_not_found(self, dispatcher, file_backend, ticket_repo):
        """ticket None → raises TransferNotFoundError."""
        ticket_repo.get_by_transfer_id.return_value = None

        with pytest.raises(TransferNotFoundError):
            await dispatcher.dispatch_cancel_upload(transfer_id="nonexistent")

    @pytest.mark.asyncio
    async def test_cancel_ownership_mismatch(
        self, dispatcher, file_backend, ticket_repo
    ):
        """ticket.session_id != session_id → raises TransferNotFoundError (404)."""
        ticket = _make_ticket(session_id="sess-001")
        ticket_repo.get_by_transfer_id.return_value = ticket

        with pytest.raises(TransferNotFoundError):
            await dispatcher.dispatch_cancel_upload(
                transfer_id="tf-001",
                session_id="sess-other",
            )

    @pytest.mark.asyncio
    async def test_cancel_cas_conflict_resolved(
        self, dispatcher, file_backend, ticket_repo
    ):
        """update_status raises TransferStateConflictError, re-read terminal → returns idempotently."""
        ticket = _make_ticket()
        ticket_repo.get_by_transfer_id.return_value = ticket

        cancelled_ticket = _make_ticket(status="CANCELLED")
        ticket_repo.get_by_transfer_id.side_effect = [ticket, cancelled_ticket]
        ticket_repo.update_status.side_effect = TransferStateConflictError(
            f"CAS conflict for {ticket.transfer_id}"
        )

        result = await dispatcher.dispatch_cancel_upload(transfer_id="tf-001")

        assert isinstance(result, SessionCancelUploadResponse)
        assert result.transfer_id == "tf-001"
        assert result.status == "CANCELLED"


# ============================================================================
# TestGetShareLink — 5 test cases
# ============================================================================


class TestGetShareLink:
    """Tests for dispatch_get_share_link covering success, show flag, and errors."""

    @pytest.mark.asyncio
    async def test_share_link_success(self, dispatcher, file_backend, ticket_repo):
        """ticket DONE → generate_download_url called with response-disposition: attachment when show=False."""
        ticket = _make_ticket(status="DONE")
        ticket_repo.get_by_transfer_id.return_value = ticket
        file_backend.generate_download_url.return_value = (
            "https://oss.example.com/dl?token=abc"
        )

        result = await dispatcher.dispatch_get_share_link(
            transfer_id="tf-001",
            tenant="test-tenant",
            session_id="sess-001",
        )

        assert isinstance(result, SessionShareLinkResponse)
        assert result.share_url == "https://oss.example.com/dl?token=abc"
        assert result.transfer_id == "tf-001"
        assert result.expires_at is not None

        # response_params is passed as positional arg (3rd position after staging_path, expire_seconds)
        call_args = file_backend.generate_download_url.call_args
        response_params_arg = call_args[0][2] if len(call_args[0]) > 2 else None
        assert response_params_arg == {"response-content-disposition": "attachment"}

    @pytest.mark.asyncio
    async def test_share_link_show_true_inline(
        self, dispatcher, file_backend, ticket_repo
    ):
        """show=True → generate_download_url called with response_params={'response-content-disposition': 'inline'}."""
        ticket = _make_ticket(status="DONE")
        ticket_repo.get_by_transfer_id.return_value = ticket
        file_backend.generate_download_url.return_value = (
            "https://oss.example.com/dl?token=abc"
        )

        await dispatcher.dispatch_get_share_link(
            transfer_id="tf-001",
            tenant="test-tenant",
            session_id="sess-001",
            show=True,
        )

        # show=True → response_params should be {"response-content-disposition": "inline"}
        call_args = file_backend.generate_download_url.call_args
        response_params_arg = call_args[0][2] if len(call_args[0]) > 2 else None
        assert response_params_arg == {"response-content-disposition": "inline"}

    @pytest.mark.asyncio
    async def test_share_link_show_none_no_intervention(
        self, dispatcher, file_backend, ticket_repo
    ):
        """show=None → generate_download_url called with response_params=None."""
        ticket = _make_ticket(status="DONE")
        ticket_repo.get_by_transfer_id.return_value = ticket
        file_backend.generate_download_url.return_value = (
            "https://oss.example.com/dl?token=abc"
        )

        await dispatcher.dispatch_get_share_link(
            transfer_id="tf-001",
            tenant="test-tenant",
            session_id="sess-001",
            show=None,
        )

        # show=None → response_params should be None (no OSS intervention)
        call_args = file_backend.generate_download_url.call_args
        response_params_arg = call_args[0][2] if len(call_args[0]) > 2 else None
        assert response_params_arg is None

    @pytest.mark.asyncio
    async def test_share_link_ticket_not_found(
        self, dispatcher, file_backend, ticket_repo
    ):
        """ticket None → raises SourceTransferNotFoundError."""
        ticket_repo.get_by_transfer_id.return_value = None

        with pytest.raises(SourceTransferNotFoundError):
            await dispatcher.dispatch_get_share_link(
                transfer_id="nonexistent",
                tenant="test-tenant",
                session_id="sess-001",
            )

    @pytest.mark.asyncio
    async def test_share_link_ownership_mismatch(
        self, dispatcher, file_backend, ticket_repo
    ):
        """ticket.session_id != session_id → raises SourceTransferNotFoundError (404)."""
        ticket = _make_ticket(session_id="sess-001", status="DONE")
        ticket_repo.get_by_transfer_id.return_value = ticket

        with pytest.raises(SourceTransferNotFoundError):
            await dispatcher.dispatch_get_share_link(
                transfer_id="tf-001",
                tenant="test-tenant",
                session_id="sess-other",
            )

    @pytest.mark.asyncio
    async def test_share_link_not_done_raises(
        self, dispatcher, file_backend, ticket_repo
    ):
        """ticket status CREATED → raises SourceTransferNotReadyError."""
        ticket = _make_ticket(status="CREATED")
        ticket_repo.get_by_transfer_id.return_value = ticket

        with pytest.raises(SourceTransferNotReadyError) as exc_info:
            await dispatcher.dispatch_get_share_link(
                transfer_id="tf-001",
                tenant="test-tenant",
                session_id="sess-001",
            )

        assert exc_info.value.transfer_id == "tf-001"
        assert exc_info.value.current_status == "CREATED"


# ============================================================================
# TestGetTransferStatus — 4 test cases
# ============================================================================


class TestGetTransferStatus:
    """Tests for dispatch_get_transfer_status covering success, not-found, ownership, and FAILED."""

    @pytest.mark.asyncio
    async def test_status_success(self, dispatcher, file_backend, ticket_repo):
        """ticket found → returns SessionGetTransferStatusResponse with all fields."""
        ticket = _make_ticket()
        ticket_repo.get_by_transfer_id.return_value = ticket

        result = await dispatcher.dispatch_get_transfer_status(transfer_id="tf-001")

        assert isinstance(result, SessionGetTransferStatusResponse)
        assert result.transfer_id == "tf-001"
        assert result.status == "CREATED"
        assert result.filename == "data.csv"
        assert result.session_id == "sess-001"

    @pytest.mark.asyncio
    async def test_status_not_found(self, dispatcher, file_backend, ticket_repo):
        """ticket None → raises TransferNotFoundError."""
        ticket_repo.get_by_transfer_id.return_value = None

        with pytest.raises(TransferNotFoundError):
            await dispatcher.dispatch_get_transfer_status(transfer_id="nonexistent")

    @pytest.mark.asyncio
    async def test_status_ownership_mismatch(
        self, dispatcher, file_backend, ticket_repo
    ):
        """ticket.session_id != session_id → raises TransferNotFoundError."""
        ticket = _make_ticket(session_id="sess-001")
        ticket_repo.get_by_transfer_id.return_value = ticket

        with pytest.raises(TransferNotFoundError):
            await dispatcher.dispatch_get_transfer_status(
                transfer_id="tf-001",
                session_id="sess-other",
            )

    @pytest.mark.asyncio
    async def test_status_failed_includes_error(
        self, dispatcher, file_backend, ticket_repo
    ):
        """ticket status FAILED with error_message='test error' → response.error_message is set."""
        ticket = _make_ticket(status="FAILED", error_message="test error")
        ticket_repo.get_by_transfer_id.return_value = ticket

        result = await dispatcher.dispatch_get_transfer_status(transfer_id="tf-001")

        assert isinstance(result, SessionGetTransferStatusResponse)
        assert result.transfer_id == "tf-001"
        assert result.status == "FAILED"
        assert result.error_message == "test error"


# ============================================================================
# TestDeleteTransfer — 6 test cases
# ============================================================================


class TestDeleteTransfer:
    """Tests for dispatch_delete_transfer covering success, idempotency, and errors."""

    @pytest.mark.asyncio
    async def test_delete_success(self, dispatcher, file_backend, ticket_repo):
        """ticket DONE → delete_object called, ticket transitions to DELETED."""
        ticket = _make_ticket(status="DONE")
        ticket_repo.get_by_transfer_id.return_value = ticket

        result = await dispatcher.dispatch_delete_transfer(transfer_id="tf-001")

        assert isinstance(result, SessionDeleteTransferResponse)
        assert result.transfer_id == ticket.transfer_id
        assert result.previous_status == "DONE"
        assert result.new_status == "DELETED"
        file_backend.delete_object.assert_called_once_with(
            ticket.fileservice_staging_path
        )
        ticket_repo.update_status.assert_called_once_with(ticket.transfer_id, "DELETED")

    @pytest.mark.asyncio
    async def test_delete_idempotent_already_deleted(
        self, dispatcher, file_backend, ticket_repo
    ):
        """ticket DELETED → returns previous_status=DELETED, new_status=DELETED, no delete_object call."""
        ticket = _make_ticket(status="DELETED")
        ticket_repo.get_by_transfer_id.return_value = ticket

        result = await dispatcher.dispatch_delete_transfer(transfer_id="tf-001")

        assert isinstance(result, SessionDeleteTransferResponse)
        assert result.transfer_id == ticket.transfer_id
        assert result.previous_status == "DELETED"
        assert result.new_status == "DELETED"
        file_backend.delete_object.assert_not_called()

    @pytest.mark.asyncio
    async def test_delete_not_found(self, dispatcher, file_backend, ticket_repo):
        """ticket None → raises TransferNotFoundError."""
        ticket_repo.get_by_transfer_id.return_value = None

        with pytest.raises(TransferNotFoundError):
            await dispatcher.dispatch_delete_transfer(transfer_id="nonexistent")

    @pytest.mark.asyncio
    async def test_delete_ownership_mismatch(
        self, dispatcher, file_backend, ticket_repo
    ):
        """ticket.session_id != session_id → raises TransferNotFoundError."""
        ticket = _make_ticket(session_id="sess-001", status="DONE")
        ticket_repo.get_by_transfer_id.return_value = ticket

        with pytest.raises(TransferNotFoundError):
            await dispatcher.dispatch_delete_transfer(
                transfer_id="tf-001",
                session_id="sess-other",
            )

    @pytest.mark.asyncio
    async def test_delete_in_progress_rejects(
        self, dispatcher, file_backend, ticket_repo
    ):
        """ticket status CREATED → raises TransferNotTerminalError."""
        ticket = _make_ticket(status="CREATED")
        ticket_repo.get_by_transfer_id.return_value = ticket

        with pytest.raises(TransferNotTerminalError) as exc_info:
            await dispatcher.dispatch_delete_transfer(transfer_id="tf-001")

        assert exc_info.value.transfer_id == ticket.transfer_id
        assert exc_info.value.status == "CREATED"

    @pytest.mark.asyncio
    async def test_delete_cas_conflict_resolved(
        self, dispatcher, file_backend, ticket_repo
    ):
        """update_status raises TransferStateConflictError, re-read DELETED → returns idempotently."""
        ticket = _make_ticket(status="DONE")
        ticket_repo.get_by_transfer_id.return_value = ticket

        deleted_ticket = _make_ticket(status="DELETED")
        ticket_repo.get_by_transfer_id.side_effect = [ticket, deleted_ticket]
        ticket_repo.update_status.side_effect = TransferStateConflictError(
            f"CAS conflict for {ticket.transfer_id}"
        )

        result = await dispatcher.dispatch_delete_transfer(transfer_id="tf-001")

        assert isinstance(result, SessionDeleteTransferResponse)
        assert result.transfer_id == ticket.transfer_id
        assert result.previous_status == "DONE"
        assert result.new_status == "DELETED"
