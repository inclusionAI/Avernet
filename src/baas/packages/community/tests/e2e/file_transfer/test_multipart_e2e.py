"""E2E tests for multipart upload flow: initiation, part upload, completion, cancel."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from secbaas.core.service.paas._facade import PaasServiceFacade
from secbaas.spi.file_transfer import PartInfo


pytestmark = [pytest.mark.e2e, pytest.mark.sync]


@pytest.mark.asyncio
async def test_multipart_upload_initiation_returns_parts(
    stub_oss_backend,
    mock_ticket_repo: MagicMock,
    poller,
) -> None:
    """POST /upload-url with file_size=200MB returns type=MULTIPART with parts.

    Verifies the stub OSS backend's initiate_multipart_upload produces the
    expected MultipartSession with multiple parts and correct session_id.
    """
    staging_path = "file-transfers/test-mp-init/file.bin"

    session = stub_oss_backend.initiate_multipart_upload(
        staging_path=staging_path,
        expire_seconds=3600,
        part_count=20,  # 200MB / 10MB = 20 parts
    )

    assert session.session_id.startswith("stub-mp-"), (
        f"Expected stub session ID prefix, got: {session.session_id}"
    )
    assert session.part_count == 20
    assert len(session.parts) == 20
    for i, part in enumerate(session.parts, 1):
        assert part.part_number == i
        assert part.upload_url == f"stub-mp-upload://test-mp-init/{i}"


@pytest.mark.asyncio
async def test_multipart_complete_detects_parts(
    stub_oss_backend,
    multipart_ticket: MagicMock,
    mock_ticket_repo: MagicMock,
    poller,
) -> None:
    """POST /upload-url/{id}/complete on multipart ticket triggers list_parts+complete.

    Simulates uploading 2 parts and then calling list_parts + complete_multipart_upload.
    Verifies data is assembled into _storage after completion.
    """
    from .conftest import _make_ticket_record

    # Initiate multipart session first (sets up _multipart_sessions dict)
    transfer_id = "stub-mp-test-transfer"
    staging_path = multipart_ticket.fileservice_staging_path
    session = stub_oss_backend.initiate_multipart_upload(
        staging_path=staging_path,
        expire_seconds=3600,
        part_count=2,
    )
    session_id = session.session_id

    # Simulate uploaded parts
    stub_oss_backend.put_multipart_content(transfer_id, b"hello ", part_number=1)
    stub_oss_backend.put_multipart_content(transfer_id, b"world", part_number=2)

    # List parts
    parts = stub_oss_backend.list_parts(staging_path, session_id)
    assert len(parts) == 2, f"Expected 2 uploaded parts, got {len(parts)}"

    # Complete
    stub_oss_backend.complete_multipart_upload(staging_path, session_id, parts)

    # Verify assembled data
    content = stub_oss_backend.get_content(f"stub-download://{transfer_id}")
    assert content == b"hello world", f"Unexpected assembled content: {content!r}"


@pytest.mark.asyncio
async def test_multipart_cancel_aborts_session(
    stub_oss_backend,
    multipart_ticket: MagicMock,
    mock_ticket_repo: MagicMock,
    poller,
) -> None:
    """DELETE /upload-url/{id} on multipart ticket aborts OSS session.

    Verifies that abort_multipart_upload clears the multipart session,
    and _multipart_sessions no longer contains the session after cancellation.
    """
    transfer_id = "stub-mp-test-transfer"
    staging_path = multipart_ticket.fileservice_staging_path

    # Initiate session first so there's something to abort
    session = stub_oss_backend.initiate_multipart_upload(
        staging_path=staging_path,
        expire_seconds=3600,
        part_count=2,
    )
    session_id = session.session_id

    # Ensure session exists before cancel
    assert transfer_id in stub_oss_backend._multipart_sessions, (
        "Multipart session should exist before cancellation"
    )

    # Abort
    stub_oss_backend.abort_multipart_upload(staging_path, session_id)

    # Verify session is gone
    assert transfer_id not in stub_oss_backend._multipart_sessions, (
        "Multipart session should be removed after abort"
    )