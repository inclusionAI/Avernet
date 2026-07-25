"""E2E tests for Session File Sharing share-link generation.

Exercises the dispatcher-direct share-link lifecycle:
  upload -> complete -> share-link (show=False) -> URL resolves to content
  upload -> complete -> share-link (show=True) -> URL resolves to content
  non-DONE ticket -> SourceTransferNotReadyError with current_status

All tests use StubFileTransferBackend (in-memory OSS) and
MagicMock(spec=SessionTicketRepository) — zero external dependencies.
"""

from __future__ import annotations

import pytest

from secbaas.api.session_file_sharing import (
    SourceTransferNotReadyError,
)
from secbaas.core.service.session_file_sharing import (
    DefaultSessionFileSharingDispatcher,
)

pytestmark = pytest.mark.e2e


# ── Scenario 1: DONE ticket share-link (show=False → attachment) ──────


@pytest.mark.asyncio
async def test_share_link_for_done_transfer(
    stub_oss_backend,
    mock_session_ticket_repo,
) -> None:
    """Upload -> complete -> share-link (show=False): URL resolves to content."""
    from .conftest import _make_session_ticket_record

    # 1. Create dispatcher + upload
    dispatcher = DefaultSessionFileSharingDispatcher(
        file_transfer_backend=stub_oss_backend,
        ticket_repo=mock_session_ticket_repo,
    )
    mock_session_ticket_repo.create_ticket.return_value = 1

    upload_resp = await dispatcher.dispatch_get_upload_url(
        tenant="t1", session_id="sess-001", filename="test.txt", file_size=100,
    )

    # 2. Put content to stub OSS
    original_content = b"hello share-link e2e"
    stub_oss_backend.put_content(upload_resp.upload_url, original_content)

    # 3. Complete upload (ticket CREATED -> DONE)
    expected_path = stub_oss_backend.build_session_staging_path(
        tenant="t1",
        session_id="sess-001",
        transfer_id=upload_resp.transfer_id,
        filename="test.txt",
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

    # 4. Share link (ticket now DONE)
    mock_session_ticket_repo.get_by_transfer_id.return_value = (
        _make_session_ticket_record(
            transfer_id=upload_resp.transfer_id,
            status="DONE",
            session_id="sess-001",
            tenant="t1",
            fileservice_staging_path=expected_path,
        )
    )
    share_resp = await dispatcher.dispatch_get_share_link(
        transfer_id=upload_resp.transfer_id,
        tenant="t1",
        session_id="sess-001",
        show=False,
    )
    assert share_resp.share_url is not None, "share_url should not be None"
    assert share_resp.transfer_id == upload_resp.transfer_id
    assert share_resp.expires_at is not None, "expires_at should not be None"

    # 5. Verify share URL resolves to original content
    resolved = stub_oss_backend.get_content(share_resp.share_url)
    assert resolved == original_content, (
        f"Share URL should resolve to original content; got {resolved!r}"
    )


# ── Scenario 2: show=True → inline/preview URL ────────────────────────


@pytest.mark.asyncio
async def test_share_link_show_true(
    stub_oss_backend,
    mock_session_ticket_repo,
) -> None:
    """show=True -> generate_download_url called with response_params=None -> URL resolves."""
    from .conftest import _make_session_ticket_record

    dispatcher = DefaultSessionFileSharingDispatcher(
        file_transfer_backend=stub_oss_backend,
        ticket_repo=mock_session_ticket_repo,
    )
    mock_session_ticket_repo.create_ticket.return_value = 1

    # Upload + put content
    upload_resp = await dispatcher.dispatch_get_upload_url(
        tenant="t1", session_id="sess-001", filename="preview.txt", file_size=100,
    )
    original_content = b"inline preview content"
    stub_oss_backend.put_content(upload_resp.upload_url, original_content)

    # Complete
    expected_path = stub_oss_backend.build_session_staging_path(
        tenant="t1",
        session_id="sess-001",
        transfer_id=upload_resp.transfer_id,
        filename="preview.txt",
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

    # Share link with show=True
    mock_session_ticket_repo.get_by_transfer_id.return_value = (
        _make_session_ticket_record(
            transfer_id=upload_resp.transfer_id,
            status="DONE",
            session_id="sess-001",
            tenant="t1",
            fileservice_staging_path=expected_path,
        )
    )
    share_resp = await dispatcher.dispatch_get_share_link(
        transfer_id=upload_resp.transfer_id,
        tenant="t1",
        session_id="sess-001",
        show=True,
    )

    # Verify URL resolves (show=True uses no Content-Disposition param)
    resolved = stub_oss_backend.get_content(share_resp.share_url)
    assert resolved == original_content


# ── Scenario 3: non-DONE ticket → SourceTransferNotReadyError ──────────


@pytest.mark.asyncio
async def test_share_link_non_done_raises(
    stub_oss_backend,
    mock_session_ticket_repo,
) -> None:
    """ticket status CREATED -> dispatcher raises SourceTransferNotReadyError."""
    from .conftest import _make_session_ticket_record

    dispatcher = DefaultSessionFileSharingDispatcher(
        file_transfer_backend=stub_oss_backend,
        ticket_repo=mock_session_ticket_repo,
    )

    # Configure ticket as CREATED (not DONE)
    mock_session_ticket_repo.get_by_transfer_id.return_value = (
        _make_session_ticket_record(
            transfer_id="tf-001",
            status="CREATED",
            session_id="sess-001",
            tenant="t1",
        )
    )

    with pytest.raises(SourceTransferNotReadyError) as exc_info:
        await dispatcher.dispatch_get_share_link(
            transfer_id="tf-001",
            tenant="t1",
            session_id="sess-001",
        )
    assert exc_info.value.current_status == "CREATED", (
        f"Expected current_status 'CREATED', got {exc_info.value.current_status!r}"
    )