"""E2E tests for Session File Sharing upload flow.

Exercises the dispatcher-direct upload lifecycle:
  get_upload_url -> put content to stub OSS -> complete -> DONE
  get_upload_url -> cancel -> CANCELLED
  multipart upload (file_size > 100MB) -> parts verification
  build_session_staging_path format verification

All tests use StubFileTransferBackend (in-memory OSS) and
MagicMock(spec=SessionTicketRepository) — zero external dependencies.
"""

from __future__ import annotations

import pytest

from secbaas.community.core.service.session_file_sharing import (
    DefaultSessionFileSharingDispatcher,
)

pytestmark = pytest.mark.e2e


# ── Scenario 1: upload -> put content -> complete -> DONE ─────────────


@pytest.mark.asyncio
async def test_upload_single_complete_flow(
    stub_oss_backend,
    mock_session_ticket_repo,
) -> None:
    """Full SINGLE upload -> complete flow: content matches, ticket ends DONE."""
    from .conftest import _make_session_ticket_record

    # 1. Create dispatcher
    dispatcher = DefaultSessionFileSharingDispatcher(
        file_transfer_backend=stub_oss_backend,
        ticket_repo=mock_session_ticket_repo,
    )

    # 2. Configure mock: create_ticket returns an ID
    mock_session_ticket_repo.create_ticket.return_value = 1

    # 3. Get upload URL (SINGLE — file_size=100 < 100MB threshold)
    upload_resp = await dispatcher.dispatch_get_upload_url(
        tenant="t1",
        session_id="sess-001",
        filename="test.txt",
        file_size=100,
    )
    assert upload_resp.type == "SINGLE", f"Expected SINGLE, got {upload_resp.type}"
    assert upload_resp.upload_url is not None, "upload_url should not be None"
    assert upload_resp.transfer_id, "transfer_id should not be empty"

    # 4. Put content to stub OSS
    original_content = b"hello e2e"
    stub_oss_backend.put_content(upload_resp.upload_url, original_content)

    # 5. Configure get_by_transfer_id -> CREATED ticket for complete
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
            fileservice_staging_path=expected_path,
        )
    )

    # 6. Complete upload
    complete_resp = await dispatcher.dispatch_complete_upload(
        upload_resp.transfer_id,
    )
    assert complete_resp.status == "DONE", f"Expected DONE, got {complete_resp.status}"
    assert complete_resp.transfer_id == upload_resp.transfer_id

    # 7. Verify content is in stub OSS (check_object_exists)
    assert stub_oss_backend.check_object_exists(expected_path), (
        "Stub OSS should have the uploaded file"
    )


# ── Scenario 2: upload -> cancel -> CANCELLED ─────────────────────────


@pytest.mark.asyncio
async def test_upload_and_cancel_flow(
    stub_oss_backend,
    mock_session_ticket_repo,
) -> None:
    """Get upload URL -> cancel upload -> ticket status CANCELLED."""
    from .conftest import _make_session_ticket_record

    dispatcher = DefaultSessionFileSharingDispatcher(
        file_transfer_backend=stub_oss_backend,
        ticket_repo=mock_session_ticket_repo,
    )
    mock_session_ticket_repo.create_ticket.return_value = 1

    # 1. Get upload URL
    upload_resp = await dispatcher.dispatch_get_upload_url(
        tenant="t1",
        session_id="sess-001",
        filename="test.txt",
        file_size=100,
    )

    # 2. Configure get_by_transfer_id -> CREATED ticket
    mock_session_ticket_repo.get_by_transfer_id.return_value = (
        _make_session_ticket_record(
            transfer_id=upload_resp.transfer_id,
            status="CREATED",
        )
    )

    # 3. Cancel upload
    cancel_resp = await dispatcher.dispatch_cancel_upload(
        upload_resp.transfer_id,
    )
    assert cancel_resp.status == "CANCELLED", (
        f"Expected CANCELLED, got {cancel_resp.status}"
    )
    assert cancel_resp.transfer_id == upload_resp.transfer_id


# ── Scenario 3: multipart upload (file_size > 100MB) ──────────────────


@pytest.mark.asyncio
async def test_multipart_upload_flow(
    stub_oss_backend,
    mock_session_ticket_repo,
) -> None:
    """MULTIPART upload: file_size > 100MB -> MULTIPART response with parts."""
    from .conftest import _make_session_ticket_record

    dispatcher = DefaultSessionFileSharingDispatcher(
        file_transfer_backend=stub_oss_backend,
        ticket_repo=mock_session_ticket_repo,
    )
    mock_session_ticket_repo.create_ticket.return_value = 1

    # 1. Get upload URL with file_size above 100MB threshold
    upload_resp = await dispatcher.dispatch_get_upload_url(
        tenant="t1",
        session_id="sess-001",
        filename="large.bin",
        file_size=200_000_000,  # ~200MB > 100MB threshold
    )

    # 2. Verify MULTIPART response
    assert upload_resp.type == "MULTIPART", (
        f"Expected MULTIPART, got {upload_resp.type}"
    )
    assert upload_resp.upload_session_id is not None, (
        "upload_session_id should not be None for MULTIPART"
    )
    assert upload_resp.parts is not None, "parts should not be None for MULTIPART"
    assert len(upload_resp.parts) > 0, "parts list should not be empty"
    assert upload_resp.part_count > 0, "part_count should be positive"

    # 3. Verify each part has expected fields
    for part in upload_resp.parts:
        assert "part_number" in part
        assert "upload_url" in part
        assert "http_method" in part

    # 4. Simulate uploading parts to stub OSS
    for part in upload_resp.parts:
        stub_oss_backend.put_multipart_content(
            upload_resp.transfer_id,
            b"x" * 10_485_760,  # 10MB per part (default part size)
            part_number=part["part_number"],
        )

    # 5. Configure mock for complete_upload
    expected_path = stub_oss_backend.build_session_staging_path(
        tenant="t1",
        session_id="sess-001",
        transfer_id=upload_resp.transfer_id,
        filename="large.bin",
    )
    mock_session_ticket_repo.get_by_transfer_id.return_value = (
        _make_session_ticket_record(
            transfer_id=upload_resp.transfer_id,
            status="CREATED",
            fileservice_staging_path=expected_path,
            multipart_session_id=upload_resp.upload_session_id,
        )
    )

    # 6. Complete the multipart upload (this calls list_parts + complete_multipart_upload)
    complete_resp = await dispatcher.dispatch_complete_upload(
        upload_resp.transfer_id,
    )
    assert complete_resp.status == "DONE", (
        f"Expected DONE for multipart, got {complete_resp.status}"
    )


# ── Scenario 4: build_session_staging_path format verification ────────


def test_upload_staging_path_format(stub_oss_backend) -> None:
    """verify build_session_staging_path returns path with env/tenant/session_id pattern."""
    path = stub_oss_backend.build_session_staging_path(
        tenant="t1",
        session_id="sess-001",
        transfer_id="tf-001",
        filename="test.txt",
        subdir=None,
    )
    # Path pattern: {root}/{env}/{tenant}/{session_id}/{transfer_id}/{filename}
    assert "t1" in path, f"tenant 't1' should appear in path: {path}"
    assert "sess-001" in path, f"session_id should appear in path: {path}"
    assert "tf-001" in path, f"transfer_id should appear in path: {path}"
    assert path.endswith("test.txt"), f"Path should end with filename: {path}"
    # Verify path does NOT end with "/" (no trailing subdir marker)
    assert not path.endswith("/"), f"Path should not end with /: {path}"
