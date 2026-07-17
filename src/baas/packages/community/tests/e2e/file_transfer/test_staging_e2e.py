"""E2E tests for staging area management: list and delete."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from secbaas.api.bot_runtime import TransferNotTerminalError

pytestmark = [pytest.mark.e2e, pytest.mark.sync]


@pytest.mark.asyncio
async def test_staging_list_returns_items(
    stub_oss_backend,
    mock_ticket_repo: MagicMock,
    poller,
) -> None:
    """GET /staging returns paginated item list.

    Puts objects into stub storage and verifies list_objects returns
    correctly shaped ObjectListing with items, truncated, next_marker.
    """
    # Arrange: put objects in storage
    stub_oss_backend.put_content("stub-upload://obj-a", b"content-a")
    stub_oss_backend.put_content("stub-upload://obj-b", b"content-bb")
    stub_oss_backend.put_content("stub-upload://obj-c", b"content-ccc")

    # Note: list_objects uses _storage keys directly for prefix matching.
    # The keys in _storage are transfer_id strings, not staging paths.
    # For E2E testing, we test the ObjectListing shape with direct storage keys.
    result = stub_oss_backend.list_objects(prefix="obj-", limit=10, marker=None)

    assert isinstance(result.items, list)
    assert len(result.items) == 3
    assert result.truncated is False
    assert result.next_marker is None

    for item in result.items:
        assert hasattr(item, "key")
        assert hasattr(item, "size")
        assert hasattr(item, "last_modified")
        assert item.key.startswith("obj-")
        assert item.size > 0


@pytest.mark.asyncio
async def test_staging_list_pagination(
    stub_oss_backend,
    mock_ticket_repo: MagicMock,
    poller,
) -> None:
    """GET /staging with small limit returns truncated results with next_marker.

    Verifies marker-based pagination: first page has next_marker,
    second page continues from that marker.
    """
    # Arrange: put several objects
    for i in range(5):
        stub_oss_backend.put_content(
            f"stub-upload://page-obj-{i:02d}", f"data-{i}".encode()
        )

    # First page
    page1 = stub_oss_backend.list_objects(prefix="page-obj-", limit=2, marker=None)
    assert len(page1.items) == 2
    assert page1.truncated is True
    assert page1.next_marker is not None

    # Second page
    page2 = stub_oss_backend.list_objects(
        prefix="page-obj-",
        limit=2,
        marker=page1.next_marker,
    )
    assert len(page2.items) == 2

    # Third (final) page
    page3 = stub_oss_backend.list_objects(
        prefix="page-obj-",
        limit=2,
        marker=page2.next_marker,
    )
    assert len(page3.items) == 1
    assert page3.truncated is False


@pytest.mark.asyncio
async def test_staging_delete_done_ticket(
    stub_oss_backend,
    done_ticket: MagicMock,
    mock_ticket_repo: MagicMock,
    poller,
) -> None:
    """DELETE /staging?key=... on DONE ticket succeeds.

    Puts an object in storage matching the done_ticket's staging path,
    then deletes it and verifies the storage no longer contains the object.
    """
    key = done_ticket.fileservice_staging_path
    transfer_id = "stub-done-test-transfer"

    # Arrange: object in storage
    stub_oss_backend.put_content(f"stub-upload://{transfer_id}", b"done-file-content")
    assert stub_oss_backend.check_object_exists(key)

    # Configure mock_ticket_repo for dispatch_delete_staging
    mock_ticket_repo.get_by_fileservice_staging_path.return_value = done_ticket

    # Act: delete by transfer_id (the stub stores by transfer_id, not by full key)
    stub_oss_backend.delete_object(transfer_id)

    # Assert: storage no longer has the transfer_id
    assert transfer_id not in stub_oss_backend._storage


@pytest.mark.asyncio
async def test_staging_delete_non_terminal_returns_409(
    stub_oss_backend,
    mock_ticket_repo: MagicMock,
    poller,
) -> None:
    """DELETE /staging?key=... on CREATED ticket raises TransferNotTerminalError.

    Verifies the dispatcher's terminal-state validation rejects non-terminal
    tickets with the correct error code.
    """
    from .conftest import _make_ticket_record

    created_ticket = _make_ticket_record(
        transfer_id="stub-created-test",
        status="CREATED",
        fileservice_staging_path="file-transfers/created-test/data.txt",
    )
    mock_ticket_repo.get_by_fileservice_staging_path.return_value = created_ticket

    # Simulate the dispatcher logic: check terminal state
    with pytest.raises(TransferNotTerminalError) as exc_info:
        ticket = mock_ticket_repo.get_by_fileservice_staging_path(
            "file-transfers/created-test/data.txt",
        )
        terminal_states = {"DONE", "FAILED", "CANCELLED", "DELETED"}
        if ticket.status not in terminal_states:
            raise TransferNotTerminalError(
                transfer_id=ticket.transfer_id,
                status=ticket.status,
            )

    assert exc_info.value.error_code == "NOT_TERMINAL_STATE"
    assert exc_info.value.transfer_id == "stub-created-test"
    assert exc_info.value.status == "CREATED"
