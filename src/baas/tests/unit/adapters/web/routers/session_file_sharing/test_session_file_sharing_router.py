"""Unit tests for Session File Sharing HTTP endpoints.

Tests all error handling paths for:
- POST /{tenant}/{session_id}/files/upload-url
- POST /{tenant}/{session_id}/files/upload-url/{transfer_id}/complete
- DELETE /{tenant}/{session_id}/files/upload-url/{transfer_id}
- POST /{tenant}/{session_id}/files/transfers/{transfer_id}/share-link
- GET /{tenant}/{session_id}/transfers/{transfer_id}
- DELETE /{tenant}/{session_id}/transfers/{transfer_id}
"""

from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from secbaas.community.adapters.web.routers.session_file_sharing.session_file_sharing_router import (  # noqa: E501
    _get_session_file_sharing_dispatcher,
    router,
)
from secbaas.community.api.session_file_sharing import (
    SessionCancelUploadResponse,
    SessionCompleteUploadResponse,
    SessionDeleteTransferResponse,
    SessionFileSharingDispatcher,
    SessionFileSharingError,
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

app = FastAPI()
app.include_router(router)


# ---------------------------------------------------------------------------
# DI override fixture
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_dispatcher():
    """Override _get_session_file_sharing_dispatcher to return a mock.

    Session Router uses Depends(_get_session_file_sharing_dispatcher) — a
    plain callable — not Provide[...] + @inject.  The override therefore
    targets the callable directly (not iter_api_routes, which is for
    Provide[...] injection).
    """
    mock_instance = AsyncMock()
    old_overrides = dict(app.dependency_overrides)
    app.dependency_overrides[_get_session_file_sharing_dispatcher] = lambda: (
        mock_instance
    )
    yield mock_instance
    app.dependency_overrides = old_overrides


# ---------------------------------------------------------------------------
# Async HTTP helpers (mirror Bot Router test pattern)
# ---------------------------------------------------------------------------


async def _post(uri: str, json_data: dict | None = None):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.post(uri, json=json_data or {})


async def _get(uri: str):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.get(uri)


async def _delete(uri: str):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.delete(uri)


# ==========================================================================
# Upload URL endpoint tests
# ==========================================================================


class TestGetUploadUrl:
    """POST /{tenant}/{session_id}/files/upload-url"""

    @pytest.mark.asyncio
    async def test_upload_url_single_success(self, mock_dispatcher):
        """Single-file upload URL returns 200 with transfer_id."""
        mock_dispatcher.dispatch_get_upload_url.return_value = (
            SessionGetUploadUrlResponse(
                upload_url="https://oss.example.com/upload?token=abc",
                transfer_id="tf-001",
                expires_at="2099-01-01T00:00:00",
                type="SINGLE",
            )
        )

        resp = await _post(
            "/api/v1/sessions/t1/sess-001/files/upload-url",
            json_data={"filename": "data.csv"},
        )

        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["transfer_id"] == "tf-001"
        assert data["type"] == "SINGLE"

    @pytest.mark.asyncio
    async def test_upload_url_multipart_success(self, mock_dispatcher):
        """Multipart upload URL returns 200 with parts list."""
        mock_dispatcher.dispatch_get_upload_url.return_value = (
            SessionGetUploadUrlResponse(
                upload_url=None,
                transfer_id="tf-002",
                expires_at=None,
                type="MULTIPART",
                upload_session_id="oss-mpu-001",
                part_size=10485760,
                part_count=3,
                parts=[
                    {"part_number": 1, "upload_url": "https://oss.example.com/part1"},
                    {"part_number": 2, "upload_url": "https://oss.example.com/part2"},
                    {"part_number": 3, "upload_url": "https://oss.example.com/part3"},
                ],
            )
        )

        resp = await _post(
            "/api/v1/sessions/t1/sess-001/files/upload-url",
            json_data={"filename": "large.zip", "file_size": 30000000},
        )

        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["transfer_id"] == "tf-002"
        assert data["type"] == "MULTIPART"
        assert data["part_count"] == 3

    @pytest.mark.asyncio
    async def test_upload_url_value_error_400(self, mock_dispatcher):
        """ValueError returns 400 with INVALID_PARAMETER."""
        mock_dispatcher.dispatch_get_upload_url.side_effect = ValueError(
            "invalid subdir"
        )

        resp = await _post(
            "/api/v1/sessions/t1/sess-001/files/upload-url",
            json_data={"filename": "data.csv", "staging_subdir": "../etc"},
        )

        assert resp.status_code == 400
        detail = resp.json()["detail"]
        assert detail["error_code"] == "INVALID_PARAMETER"

    @pytest.mark.asyncio
    async def test_upload_url_domain_error(self, mock_dispatcher):
        """TransferStateConflictError returns 409 with TRANSFER_STATE_CONFLICT."""
        mock_dispatcher.dispatch_get_upload_url.side_effect = (
            TransferStateConflictError("bad state")
        )

        resp = await _post(
            "/api/v1/sessions/t1/sess-001/files/upload-url",
            json_data={"filename": "data.csv"},
        )

        assert resp.status_code == 409
        detail = resp.json()["detail"]
        assert detail["error_code"] == "TRANSFER_STATE_CONFLICT"

    @pytest.mark.asyncio
    async def test_upload_url_unhandled_500(self, mock_dispatcher):
        """Generic Exception returns 500 with INTERNAL_ERROR."""
        mock_dispatcher.dispatch_get_upload_url.side_effect = RuntimeError("unexpected")

        resp = await _post(
            "/api/v1/sessions/t1/sess-001/files/upload-url",
            json_data={"filename": "data.csv"},
        )

        assert resp.status_code == 500
        detail = resp.json()["detail"]
        assert detail["error_code"] == "INTERNAL_ERROR"

    @pytest.mark.asyncio
    async def test_upload_url_not_implemented_501(self, mock_dispatcher):
        """NotImplementedError returns 501 with NOT_IMPLEMENTED."""
        mock_dispatcher.dispatch_get_upload_url.side_effect = NotImplementedError(
            "not ready"
        )

        resp = await _post(
            "/api/v1/sessions/t1/sess-001/files/upload-url",
            json_data={"filename": "data.csv"},
        )

        assert resp.status_code == 501
        detail = resp.json()["detail"]
        assert detail["error_code"] == "NOT_IMPLEMENTED"


# ==========================================================================
# Complete Upload endpoint tests
# ==========================================================================


class TestCompleteUpload:
    """POST /{tenant}/{session_id}/files/upload-url/{transfer_id}/complete"""

    @pytest.mark.asyncio
    async def test_complete_upload_success(self, mock_dispatcher):
        """Complete upload returns 200 with DONE status."""
        mock_dispatcher.dispatch_complete_upload.return_value = (
            SessionCompleteUploadResponse(
                transfer_id="tf-001",
                status="DONE",
            )
        )

        resp = await _post(
            "/api/v1/sessions/t1/sess-001/files/upload-url/tf-001/complete",
        )

        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["transfer_id"] == "tf-001"
        assert data["status"] == "DONE"

    @pytest.mark.asyncio
    async def test_complete_transfer_not_found_404(self, mock_dispatcher):
        """TransferNotFoundError returns 404."""
        mock_dispatcher.dispatch_complete_upload.side_effect = TransferNotFoundError(
            "no ticket"
        )

        resp = await _post(
            "/api/v1/sessions/t1/sess-001/files/upload-url/tf-001/complete",
        )

        assert resp.status_code == 404
        detail = resp.json()["detail"]
        assert detail["error_code"] == "TRANSFER_NOT_FOUND"

    @pytest.mark.asyncio
    async def test_complete_staging_not_found_409(self, mock_dispatcher):
        """StagingObjectNotFoundError returns 409 with transfer_id in detail."""
        mock_dispatcher.dispatch_complete_upload.side_effect = (
            StagingObjectNotFoundError(staging_path="oss://missing/tf-001")
        )

        resp = await _post(
            "/api/v1/sessions/t1/sess-001/files/upload-url/tf-001/complete",
        )

        assert resp.status_code == 409
        detail = resp.json()["detail"]
        assert detail["error_code"] == "STAGING_OBJECT_NOT_FOUND"
        assert detail["transfer_id"] == "tf-001"

    @pytest.mark.asyncio
    async def test_complete_state_conflict_409(self, mock_dispatcher):
        """TransferStateConflictError returns 409."""
        mock_dispatcher.dispatch_complete_upload.side_effect = (
            TransferStateConflictError("bad state")
        )

        resp = await _post(
            "/api/v1/sessions/t1/sess-001/files/upload-url/tf-001/complete",
        )

        assert resp.status_code == 409
        detail = resp.json()["detail"]
        assert detail["error_code"] == "TRANSFER_STATE_CONFLICT"

    @pytest.mark.asyncio
    async def test_complete_value_error_422(self, mock_dispatcher):
        """ValueError returns 422 with INVALID_TRANSITION."""
        mock_dispatcher.dispatch_complete_upload.side_effect = ValueError(
            "invalid transition"
        )

        resp = await _post(
            "/api/v1/sessions/t1/sess-001/files/upload-url/tf-001/complete",
        )

        assert resp.status_code == 422
        detail = resp.json()["detail"]
        assert detail["error_code"] == "INVALID_TRANSITION"

    @pytest.mark.asyncio
    async def test_complete_domain_error(self, mock_dispatcher):
        """Generic SessionFileSharingError (DomainError) returns 400."""
        mock_dispatcher.dispatch_complete_upload.side_effect = SessionFileSharingError(
            "session file sharing error"
        )

        resp = await _post(
            "/api/v1/sessions/t1/sess-001/files/upload-url/tf-001/complete",
        )

        assert resp.status_code == 400
        detail = resp.json()["detail"]
        assert detail["error_code"] == "SESSION_FILE_SHARING_ERROR"


# ==========================================================================
# Cancel Upload endpoint tests
# ==========================================================================


class TestCancelUpload:
    """DELETE /{tenant}/{session_id}/files/upload-url/{transfer_id}"""

    @pytest.mark.asyncio
    async def test_cancel_upload_success(self, mock_dispatcher):
        """Cancel upload returns 200 with CANCELLED status."""
        mock_dispatcher.dispatch_cancel_upload.return_value = (
            SessionCancelUploadResponse(
                transfer_id="tf-001",
                status="CANCELLED",
            )
        )

        resp = await _delete(
            "/api/v1/sessions/t1/sess-001/files/upload-url/tf-001",
        )

        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["transfer_id"] == "tf-001"
        assert data["status"] == "CANCELLED"

    @pytest.mark.asyncio
    async def test_cancel_not_found_404(self, mock_dispatcher):
        """TransferNotFoundError returns 404."""
        mock_dispatcher.dispatch_cancel_upload.side_effect = TransferNotFoundError(
            "no ticket"
        )

        resp = await _delete(
            "/api/v1/sessions/t1/sess-001/files/upload-url/tf-001",
        )

        assert resp.status_code == 404
        detail = resp.json()["detail"]
        assert detail["error_code"] == "TRANSFER_NOT_FOUND"

    @pytest.mark.asyncio
    async def test_cancel_state_conflict_409(self, mock_dispatcher):
        """TransferStateConflictError returns 409."""
        mock_dispatcher.dispatch_cancel_upload.side_effect = TransferStateConflictError(
            "bad state"
        )

        resp = await _delete(
            "/api/v1/sessions/t1/sess-001/files/upload-url/tf-001",
        )

        assert resp.status_code == 409
        detail = resp.json()["detail"]
        assert detail["error_code"] == "TRANSFER_STATE_CONFLICT"

    @pytest.mark.asyncio
    async def test_cancel_value_error_422(self, mock_dispatcher):
        """ValueError returns 422 with INVALID_TRANSITION."""
        mock_dispatcher.dispatch_cancel_upload.side_effect = ValueError(
            "invalid transition"
        )

        resp = await _delete(
            "/api/v1/sessions/t1/sess-001/files/upload-url/tf-001",
        )

        assert resp.status_code == 422
        detail = resp.json()["detail"]
        assert detail["error_code"] == "INVALID_TRANSITION"


# ==========================================================================
# Generate Share Link endpoint tests
# ==========================================================================


class TestGenerateShareLink:
    """POST /{tenant}/{session_id}/files/transfers/{transfer_id}/share-link"""

    @pytest.mark.asyncio
    async def test_share_link_success(self, mock_dispatcher):
        """Generate share link returns 200 with share_url."""
        mock_dispatcher.dispatch_get_share_link.return_value = SessionShareLinkResponse(
            share_url="https://oss.example.com/dl?token=abc",
            transfer_id="tf-001",
            expires_at="2099-01-01T00:00:00",
        )

        resp = await _post(
            "/api/v1/sessions/t1/sess-001/files/transfers/tf-001/share-link",
            json_data={"expire_seconds": 3600, "show": False, "operator": "test-user"},
        )

        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["share_url"].startswith("https://")
        assert data["transfer_id"] == "tf-001"
        assert data["expires_at"] is not None

    @pytest.mark.asyncio
    async def test_share_link_source_not_found_404(self, mock_dispatcher):
        """SourceTransferNotFoundError returns 404 with transfer_id in detail."""
        mock_dispatcher.dispatch_get_share_link.side_effect = (
            SourceTransferNotFoundError(transfer_id="tf-001")
        )

        resp = await _post(
            "/api/v1/sessions/t1/sess-001/files/transfers/tf-001/share-link",
            json_data={"expire_seconds": 3600, "show": False, "operator": "test-user"},
        )

        assert resp.status_code == 404
        detail = resp.json()["detail"]
        assert detail["error_code"] == "SOURCE_TRANSFER_NOT_FOUND"
        assert detail["transfer_id"] == "tf-001"

    @pytest.mark.asyncio
    async def test_share_link_not_ready_409(self, mock_dispatcher):
        """SourceTransferNotReadyError returns 409 with transfer_id and current_status."""
        mock_dispatcher.dispatch_get_share_link.side_effect = (
            SourceTransferNotReadyError(
                transfer_id="tf-001",
                current_status="CREATED",
            )
        )

        resp = await _post(
            "/api/v1/sessions/t1/sess-001/files/transfers/tf-001/share-link",
            json_data={"expire_seconds": 3600, "show": False, "operator": "test-user"},
        )

        assert resp.status_code == 409
        detail = resp.json()["detail"]
        assert detail["error_code"] == "SOURCE_TRANSFER_NOT_READY"
        assert detail["transfer_id"] == "tf-001"
        assert detail["current_status"] == "CREATED"

    @pytest.mark.asyncio
    async def test_share_link_value_error_422(self, mock_dispatcher):
        """ValueError returns 422 with INVALID_TRANSITION."""
        mock_dispatcher.dispatch_get_share_link.side_effect = ValueError("not DONE")

        resp = await _post(
            "/api/v1/sessions/t1/sess-001/files/transfers/tf-001/share-link",
            json_data={"expire_seconds": 3600, "show": False, "operator": "test-user"},
        )

        assert resp.status_code == 422
        detail = resp.json()["detail"]
        assert detail["error_code"] == "INVALID_TRANSITION"

    @pytest.mark.asyncio
    async def test_share_link_domain_error(self, mock_dispatcher):
        """TransferStateConflictError returns 409."""
        mock_dispatcher.dispatch_get_share_link.side_effect = (
            TransferStateConflictError("bad state")
        )

        resp = await _post(
            "/api/v1/sessions/t1/sess-001/files/transfers/tf-001/share-link",
            json_data={"expire_seconds": 3600, "show": False, "operator": "test-user"},
        )

        assert resp.status_code == 409
        detail = resp.json()["detail"]
        assert detail["error_code"] == "TRANSFER_STATE_CONFLICT"


# ==========================================================================
# Get Transfer Status endpoint tests
# ==========================================================================


class TestGetTransferStatus:
    """GET /{tenant}/{session_id}/transfers/{transfer_id}"""

    @pytest.mark.asyncio
    async def test_status_success(self, mock_dispatcher):
        """Get transfer status returns 200 with session_id in response."""
        mock_dispatcher.dispatch_get_transfer_status.return_value = (
            SessionGetTransferStatusResponse(
                transfer_id="tf-001",
                status="DONE",
                filename="test.txt",
                session_id="sess-001",
                error_message=None,
                created_at="2025-01-01T00:00:00",
                updated_at="2025-01-01T01:00:00",
                operator="test-user",
            )
        )

        resp = await _get(
            "/api/v1/sessions/t1/sess-001/transfers/tf-001",
        )

        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["transfer_id"] == "tf-001"
        assert data["status"] == "DONE"
        assert data["session_id"] == "sess-001"
        assert data["filename"] == "test.txt"

    @pytest.mark.asyncio
    async def test_status_not_found_404(self, mock_dispatcher):
        """TransferNotFoundError returns 404."""
        mock_dispatcher.dispatch_get_transfer_status.side_effect = (
            TransferNotFoundError("no ticket")
        )

        resp = await _get(
            "/api/v1/sessions/t1/sess-001/transfers/tf-001",
        )

        assert resp.status_code == 404
        detail = resp.json()["detail"]
        assert detail["error_code"] == "TRANSFER_NOT_FOUND"

    @pytest.mark.asyncio
    async def test_status_domain_error(self, mock_dispatcher):
        """TransferStateConflictError returns 409."""
        mock_dispatcher.dispatch_get_transfer_status.side_effect = (
            TransferStateConflictError("bad state")
        )

        resp = await _get(
            "/api/v1/sessions/t1/sess-001/transfers/tf-001",
        )

        assert resp.status_code == 409
        detail = resp.json()["detail"]
        assert detail["error_code"] == "TRANSFER_STATE_CONFLICT"


# ==========================================================================
# Delete Transfer endpoint tests
# ==========================================================================


class TestDeleteTransfer:
    """DELETE /{tenant}/{session_id}/transfers/{transfer_id}"""

    @pytest.mark.asyncio
    async def test_delete_success(self, mock_dispatcher):
        """Delete transfer returns 200 with previous_status and new_status."""
        mock_dispatcher.dispatch_delete_transfer.return_value = (
            SessionDeleteTransferResponse(
                transfer_id="tf-001",
                previous_status="DONE",
                new_status="DELETED",
            )
        )

        resp = await _delete(
            "/api/v1/sessions/t1/sess-001/transfers/tf-001",
        )

        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["transfer_id"] == "tf-001"
        assert data["previous_status"] == "DONE"
        assert data["new_status"] == "DELETED"

    @pytest.mark.asyncio
    async def test_delete_not_found_404(self, mock_dispatcher):
        """TransferNotFoundError returns 404."""
        mock_dispatcher.dispatch_delete_transfer.side_effect = TransferNotFoundError(
            "no ticket"
        )

        resp = await _delete(
            "/api/v1/sessions/t1/sess-001/transfers/tf-001",
        )

        assert resp.status_code == 404
        detail = resp.json()["detail"]
        assert detail["error_code"] == "TRANSFER_NOT_FOUND"

    @pytest.mark.asyncio
    async def test_delete_not_terminal_409(self, mock_dispatcher):
        """TransferNotTerminalError returns 409 with transfer_id in detail."""
        mock_dispatcher.dispatch_delete_transfer.side_effect = TransferNotTerminalError(
            transfer_id="tf-001", status="CREATED"
        )

        resp = await _delete(
            "/api/v1/sessions/t1/sess-001/transfers/tf-001",
        )

        assert resp.status_code == 409
        detail = resp.json()["detail"]
        assert detail["error_code"] == "TRANSFER_NOT_TERMINAL"
        assert detail["transfer_id"] == "tf-001"

    @pytest.mark.asyncio
    async def test_delete_state_conflict_409(self, mock_dispatcher):
        """TransferStateConflictError returns 409."""
        mock_dispatcher.dispatch_delete_transfer.side_effect = (
            TransferStateConflictError("bad state")
        )

        resp = await _delete(
            "/api/v1/sessions/t1/sess-001/transfers/tf-001",
        )

        assert resp.status_code == 409
        detail = resp.json()["detail"]
        assert detail["error_code"] == "TRANSFER_STATE_CONFLICT"

    @pytest.mark.asyncio
    async def test_delete_domain_error(self, mock_dispatcher):
        """Generic SessionFileSharingError (DomainError) returns 400."""
        mock_dispatcher.dispatch_delete_transfer.side_effect = SessionFileSharingError(
            "session file sharing error"
        )

        resp = await _delete(
            "/api/v1/sessions/t1/sess-001/transfers/tf-001",
        )

        assert resp.status_code == 400
        detail = resp.json()["detail"]
        assert detail["error_code"] == "SESSION_FILE_SHARING_ERROR"
