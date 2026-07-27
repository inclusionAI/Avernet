"""E2E tests for Session File Sharing delete transfer flow.

Exercises the dispatcher-direct delete lifecycle:
  terminal ticket (DONE) -> delete -> OSS object removed -> ticket DELETED
  already-DELETED ticket -> idempotent delete (previous_status=DELETED)
  in-progress ticket (CREATED) -> TransferNotTerminalError
  ticket not found -> TransferNotFoundError

All tests use StubFileTransferBackend (in-memory OSS) and
MagicMock(spec=SessionTicketRepository) — zero external dependencies.
"""

from __future__ import annotations

import pytest

from secbaas.community.api.session_file_sharing import (
    TransferNotFoundError,
    TransferNotTerminalError,
)
from secbaas.community.core.service.session_file_sharing import (
    DefaultSessionFileSharingDispatcher,
)

pytestmark = pytest.mark.e2e


# ── Scenario 1: terminal transfer (DONE) -> delete -> DELETED ─────────


@pytest.mark.asyncio
async def test_delete_terminal_transfer(
    stub_oss_backend,
    mock_session_ticket_repo,
) -> None:
    """DONE ticket -> delete_transfer -> OSS content gone -> ticket DELETED."""
    from .conftest import _make_session_ticket_record

    dispatcher = DefaultSessionFileSharingDispatcher(
        file_transfer_backend=stub_oss_backend,
        ticket_repo=mock_session_ticket_repo,
    )
    mock_session_ticket_repo.create_ticket.return_value = 1

    # 1. Upload + put content + complete
    upload_resp = await dispatcher.dispatch_get_upload_url(
        tenant="t1", session_id="sess-001", filename="delete_me.txt", file_size=100,
    )
    original_content = b"content to be deleted"
    stub_oss_backend.put_content(upload_resp.upload_url, original_content)

    expected_path = stub_oss_backend.build_session_staging_path(
        tenant="t1",
        session_id="sess-001",
        transfer_id=upload_resp.transfer_id,
        filename="delete_me.txt",
    )
    mock_session_ticket_repo.get_by_transfer_id.return_value = (
        _make_session_ticket_record(
            transfer_id=upload_resp.transfer_id,
            status="CREATED",
            session_id="sess-001",
            tenant="t1",
            fileservice_staging_path=expected_path,
        )
    )
    await dispatcher.dispatch_complete_upload(upload_resp.transfer_id)

    # 2. Verify content is in stub OSS before delete
    assert stub_oss_backend.check_object_exists(expected_path), (
        "Content should exist before delete"
    )

    # 3. Delete transfer (ticket DONE)
    mock_session_ticket_repo.get_by_transfer_id.return_value = (
        _make_session_ticket_record(
            transfer_id=upload_resp.transfer_id,
            status="DONE",
            session_id="sess-001",
            tenant="t1",
            fileservice_staging_path=expected_path,
        )
    )
    delete_resp = await dispatcher.dispatch_delete_transfer(
        upload_resp.transfer_id,
    )
    assert delete_resp.previous_status == "DONE", (
        f"Expected previous_status DONE, got {delete_resp.previous_status}"
    )
    assert delete_resp.new_status == "DELETED", (
        f"Expected new_status DELETED, got {delete_resp.new_status}"
    )
    assert delete_resp.transfer_id == upload_resp.transfer_id

    # 4. Verify stub OSS content is gone after delete
    assert not stub_oss_backend.check_object_exists(expected_path), (
        "Content should be removed after delete"
    )


# ── Scenario 2: already-DELETED -> idempotent delete ──────────────────


@pytest.mark.asyncio
async def test_delete_already_deleted_idempotent(
    stub_oss_backend,
    mock_session_ticket_repo,
) -> None:
    """DELETED ticket -> delete_transfer returns previous_status=DELETED, no error."""
    from .conftest import _make_session_ticket_record

    dispatcher = DefaultSessionFileSharingDispatcher(
        file_transfer_backend=stub_oss_backend,
        ticket_repo=mock_session_ticket_repo,
    )

    mock_session_ticket_repo.get_by_transfer_id.return_value = (
        _make_session_ticket_record(
            transfer_id="tf-deleted",
            status="DELETED",
            session_id="sess-001",
            tenant="t1",
        )
    )

    delete_resp = await dispatcher.dispatch_delete_transfer("tf-deleted")
    assert delete_resp.previous_status == "DELETED", (
        f"Expected previous_status DELETED, got {delete_resp.previous_status}"
    )
    assert delete_resp.new_status == "DELETED", (
        f"Expected new_status DELETED, got {delete_resp.new_status}"
    )


# ── Scenario 3: in-progress ticket (CREATED) -> TransferNotTerminalError ─


@pytest.mark.asyncio
async def test_delete_in_progress_rejects(
    stub_oss_backend,
    mock_session_ticket_repo,
) -> None:
    """CREATED ticket -> dispatcher raises TransferNotTerminalError."""
    from .conftest import _make_session_ticket_record

    dispatcher = DefaultSessionFileSharingDispatcher(
        file_transfer_backend=stub_oss_backend,
        ticket_repo=mock_session_ticket_repo,
    )

    mock_session_ticket_repo.get_by_transfer_id.return_value = (
        _make_session_ticket_record(
            transfer_id="tf-created",
            status="CREATED",
            session_id="sess-001",
            tenant="t1",
        )
    )

    with pytest.raises(TransferNotTerminalError) as exc_info:
        await dispatcher.dispatch_delete_transfer("tf-created")
    assert exc_info.value.status == "CREATED", (
        f"Expected status 'CREATED', got {exc_info.value.status!r}"
    )


# ── Scenario 4: ticket not found -> TransferNotFoundError ─────────────


@pytest.mark.asyncio
async def test_delete_ticket_not_found(
    stub_oss_backend,
    mock_session_ticket_repo,
) -> None:
    """ticket None in repo -> dispatcher raises TransferNotFoundError."""
    dispatcher = DefaultSessionFileSharingDispatcher(
        file_transfer_backend=stub_oss_backend,
        ticket_repo=mock_session_ticket_repo,
    )

    mock_session_ticket_repo.get_by_transfer_id.return_value = None

    with pytest.raises(TransferNotFoundError):
        await dispatcher.dispatch_delete_transfer("tf-nonexistent")