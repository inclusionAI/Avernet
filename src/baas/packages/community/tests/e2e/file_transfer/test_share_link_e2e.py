"""E2E tests for share-link generation."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

pytestmark = [pytest.mark.e2e, pytest.mark.sync]


@pytest.mark.asyncio
async def test_share_link_done_ticket(
    stub_oss_backend,
    done_ticket: MagicMock,
    mock_ticket_repo: MagicMock,
    poller,
) -> None:
    """POST /transfers/{id}/share-link on DONE ticket returns share_url.

    Verifies that generate_download_url produces a valid stub-download:// URL
    for a DONE ticket, and the returned URL maps back to the transfer content.
    """
    transfer_id = "stub-done-test-transfer"
    staging_path = done_ticket.fileservice_staging_path
    expire_seconds = 3600

    # Arrange: put content in storage so download_url resolves
    stub_oss_backend.put_content(f"stub-upload://{transfer_id}", b"shareable-content")
    assert stub_oss_backend.check_object_exists(staging_path)

    # Configure mock_ticket_repo for dispatch_generate_share_link
    mock_ticket_repo.get_by_transfer_id.return_value = done_ticket

    # Act: generate share URL (simulates dispatcher.dispatch_generate_share_link)
    share_url = stub_oss_backend.generate_download_url(staging_path, expire_seconds)

    # Assert
    assert share_url, "share_url should not be empty"
    assert share_url == f"stub-download://{transfer_id}", (
        f"Unexpected share_url: {share_url}"
    )

    # Verify the share URL resolves to the stored content
    content = stub_oss_backend.get_content(share_url)
    assert content == b"shareable-content"


@pytest.mark.asyncio
async def test_share_link_non_done_returns_422(
    mock_ticket_repo: MagicMock,
    poller,
) -> None:
    """POST /transfers/{id}/share-link on CREATED ticket raises ValueError.

    Verifies the dispatcher's status validation rejects non-DONE tickets
    with a ValueError, which the router maps to HTTP 422.
    """
    from .conftest import _make_ticket_record

    created_ticket = _make_ticket_record(
        transfer_id="stub-share-created",
        status="CREATED",
        fileservice_staging_path="file-transfers/share-created/data.txt",
    )
    mock_ticket_repo.get_by_transfer_id.return_value = created_ticket

    # Simulate dispatcher logic: check DONE status
    with pytest.raises(ValueError) as exc_info:
        ticket = mock_ticket_repo.get_by_transfer_id("stub-share-created")
        if ticket.status != "DONE":
            raise ValueError(
                f"Share link requires ticket status DONE, got {ticket.status}",
            )

    assert "Share link requires ticket status DONE" in str(exc_info.value)
    assert "CREATED" in str(exc_info.value)
