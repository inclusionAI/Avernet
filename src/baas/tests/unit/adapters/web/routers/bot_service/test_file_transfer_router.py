"""Unit tests for file_transfer_router endpoints.

Tests all error handling paths for:
- POST /{tenant}/{bot_uuid}/files/upload-url
- POST /{tenant}/{bot_uuid}/files/download-url
- POST /{tenant}/{bot_uuid}/files/upload-url/{transfer_id}/complete
- DELETE /{tenant}/{bot_uuid}/files/upload-url/{transfer_id}
- GET /{tenant}/{bot_uuid}/files/staging
- DELETE /{tenant}/{bot_uuid}/files/staging
- POST /{tenant}/{bot_uuid}/files/transfers/{transfer_id}/share-link
- GET /{tenant}/{bot_uuid}/files/transfers/{transfer_id}
"""

from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from secbaas.community.adapters.web.routers.bot_service.file_transfer_router import (
    router,
)
from secbaas.community.api.bot_runtime import (
    BotNotFoundError,
    CancelUploadResponse,
    CompleteUploadResponse,
    DeleteTransferResponse,
    GetDownloadUrlResponse,
    GetUploadUrlResponse,
    NoActiveDevicesError,
    NoDevicesFoundError,
    StagingObjectNotFoundError,
    ShareLinkResponse,
    StagingObjectNotFoundError,
    TransferNotFoundError,
    TransferStateConflictError,
)
from secbaas.community.api.device_manage import (
    DeviceFacadeException,
    ErrorCode,
    PaasError,
)
from tests.unit.adapters.web.conftest import iter_api_routes

app = FastAPI()
app.include_router(router)


@pytest.fixture
def mock_dispatcher():
    """Override the Provide dependency to return a mock BotFileTransferDispatcher."""
    mock_instance = AsyncMock()
    old_overrides = dict(app.dependency_overrides)
    for route in iter_api_routes(app):
        for dep in route.dependant.dependencies:
            if dep.name == "dispatcher":
                app.dependency_overrides[dep.call] = lambda: mock_instance
    yield mock_instance
    app.dependency_overrides = old_overrides


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


# ── get_upload_url tests ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_upload_url_success(mock_dispatcher):
    """POST upload-url returns 200 with GetUploadUrlResponse."""
    mock_dispatcher.dispatch_get_upload_url.return_value = GetUploadUrlResponse(
        upload_url="https://oss.example.com/upload?token=abc",
        transfer_id="tf-001",
        expires_at="2099-01-01T00:00:00",
        type="SINGLE",
    )

    resp = await _post(
        "/api/v1/bots/t1/bot-001/files/upload-url",
        json_data={"device_path": "/home/data.csv", "filename": "data.csv"},
    )

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["transfer_id"] == "tf-001"
    assert data["type"] == "SINGLE"


@pytest.mark.asyncio
async def test_get_upload_url_bot_not_found(mock_dispatcher):
    """POST upload-url with BotNotFoundError returns 404."""
    mock_dispatcher.dispatch_get_upload_url.side_effect = BotNotFoundError("no bot")

    resp = await _post(
        "/api/v1/bots/t1/bot-001/files/upload-url",
        json_data={"device_path": "/x", "filename": "x"},
    )

    assert resp.status_code == 404
    assert resp.json()["detail"]["error"] == "BOT_NOT_FOUND"


@pytest.mark.asyncio
async def test_get_upload_url_no_devices_found(mock_dispatcher):
    """POST upload-url with NoDevicesFoundError returns 404."""
    mock_dispatcher.dispatch_get_upload_url.side_effect = NoDevicesFoundError(
        "no devices"
    )

    resp = await _post(
        "/api/v1/bots/t1/bot-001/files/upload-url",
        json_data={"device_path": "/x", "filename": "x"},
    )

    assert resp.status_code == 404
    assert resp.json()["detail"]["error"] == "NO_DEVICES_FOUND"


@pytest.mark.asyncio
async def test_get_upload_url_no_active_devices(mock_dispatcher):
    """POST upload-url with NoActiveDevicesError returns 503."""
    mock_dispatcher.dispatch_get_upload_url.side_effect = NoActiveDevicesError(
        "none active"
    )

    resp = await _post(
        "/api/v1/bots/t1/bot-001/files/upload-url",
        json_data={"device_path": "/x", "filename": "x"},
    )

    assert resp.status_code == 503
    assert resp.json()["detail"]["error"] == "NO_ACTIVE_DEVICES"


@pytest.mark.asyncio
async def test_get_upload_url_not_implemented(mock_dispatcher):
    """POST upload-url with NotImplementedError returns 501."""
    mock_dispatcher.dispatch_get_upload_url.side_effect = NotImplementedError(
        "not ready"
    )

    resp = await _post(
        "/api/v1/bots/t1/bot-001/files/upload-url",
        json_data={"device_path": "/x", "filename": "x"},
    )

    assert resp.status_code == 501
    assert resp.json()["detail"]["error"] == "NOT_IMPLEMENTED"


@pytest.mark.asyncio
async def test_get_upload_url_device_facade_exception(mock_dispatcher):
    """POST upload-url with DeviceFacadeException returns 502."""
    mock_dispatcher.dispatch_get_upload_url.side_effect = DeviceFacadeException(
        operation="upload",
        platform_type="ARCA",
        template_id=1,
        paas_device_id="d@1",
        original_error=PaasError(ErrorCode.PLATFORM_ERROR, "down"),
    )

    resp = await _post(
        "/api/v1/bots/t1/bot-001/files/upload-url",
        json_data={"device_path": "/x", "filename": "x"},
    )

    assert resp.status_code == 502
    assert resp.json()["detail"]["error"] == "PLATFORM_ERROR"


#
# @pytest.mark.asyncio
# async def test_get_upload_url_device_facade_no_original_error(mock_dispatcher):
#     """POST upload-url with DeviceFacadeException without original_error returns FACADE_ERROR."""
#     mock_dispatcher.dispatch_get_upload_url.side_effect = DeviceFacadeException(
#         operation="upload",
#         platform_type="ARCA",
#         template_id=1,
#         paas_device_id="d@1",
#         original_error=None,
#     )
#     resp = await _post(
#         "/api/v1/bots/t1/bot-001/files/upload-url",
#         json_data={"device_path": "/x", "filename": "x"},
#     )
#
#     assert resp.status_code == 502
#     assert resp.json()["detail"]["error"] == "FACADE_ERROR"


@pytest.mark.asyncio
async def test_get_upload_url_generic_exception(mock_dispatcher):
    """POST upload-url with generic Exception returns 500."""
    mock_dispatcher.dispatch_get_upload_url.side_effect = RuntimeError("unexpected")
    resp = await _post(
        "/api/v1/bots/t1/bot-001/files/upload-url",
        json_data={"device_path": "/x", "filename": "x"},
    )

    assert resp.status_code == 500
    assert resp.json()["detail"]["error"] == "INTERNAL_ERROR"


# ── get_download_url tests ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_download_url_success(mock_dispatcher):
    """POST download-url returns 200 with GetDownloadUrlResponse."""
    mock_dispatcher.dispatch_get_download_url.return_value = GetDownloadUrlResponse(
        transfer_id="tf-dl-001",
        expires_at="2099-01-01T00:00:00",
    )
    resp = await _post(
        "/api/v1/bots/t1/bot-001/files/download-url",
        json_data={"device_path": "/home/data.csv"},
    )

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["transfer_id"] == "tf-dl-001"


@pytest.mark.asyncio
async def test_get_download_url_bot_not_found(mock_dispatcher):
    """POST download-url with BotNotFoundError returns 404."""
    mock_dispatcher.dispatch_get_download_url.side_effect = BotNotFoundError("no bot")
    resp = await _post(
        "/api/v1/bots/t1/bot-001/files/download-url",
        json_data={"device_path": "/x"},
    )

    assert resp.status_code == 404
    assert resp.json()["detail"]["error"] == "BOT_NOT_FOUND"


@pytest.mark.asyncio
async def test_get_download_url_no_devices_found(mock_dispatcher):
    """POST download-url with NoDevicesFoundError returns 404."""
    mock_dispatcher.dispatch_get_download_url.side_effect = NoDevicesFoundError(
        "no devices"
    )
    resp = await _post(
        "/api/v1/bots/t1/bot-001/files/download-url",
        json_data={"device_path": "/x"},
    )

    assert resp.status_code == 404
    assert resp.json()["detail"]["error"] == "NO_DEVICES_FOUND"


@pytest.mark.asyncio
async def test_get_download_url_no_active_devices(mock_dispatcher):
    """POST download-url with NoActiveDevicesError returns 503."""
    mock_dispatcher.dispatch_get_download_url.side_effect = NoActiveDevicesError("none")
    resp = await _post(
        "/api/v1/bots/t1/bot-001/files/download-url",
        json_data={"device_path": "/x"},
    )

    assert resp.status_code == 503
    assert resp.json()["detail"]["error"] == "NO_ACTIVE_DEVICES"


@pytest.mark.asyncio
async def test_get_download_url_not_implemented(mock_dispatcher):
    """POST download-url with NotImplementedError returns 501."""
    mock_dispatcher.dispatch_get_download_url.side_effect = NotImplementedError("nope")
    resp = await _post(
        "/api/v1/bots/t1/bot-001/files/download-url",
        json_data={"device_path": "/x"},
    )

    assert resp.status_code == 501
    assert resp.json()["detail"]["error"] == "NOT_IMPLEMENTED"


@pytest.mark.asyncio
async def test_get_download_url_device_facade_exception(mock_dispatcher):
    """POST download-url with DeviceFacadeException returns 502."""
    mock_dispatcher.dispatch_get_download_url.side_effect = DeviceFacadeException(
        operation="download",
        platform_type="ARCA",
        template_id=1,
        paas_device_id="d@1",
        original_error=PaasError(ErrorCode.PLATFORM_ERROR, "down"),
    )
    resp = await _post(
        "/api/v1/bots/t1/bot-001/files/download-url",
        json_data={"device_path": "/x"},
    )

    assert resp.status_code == 502
    assert resp.json()["detail"]["error"] == "PLATFORM_ERROR"


@pytest.mark.asyncio
async def test_get_download_url_generic_exception(mock_dispatcher):
    """POST download-url with generic Exception returns 500."""
    mock_dispatcher.dispatch_get_download_url.side_effect = RuntimeError("boom")
    resp = await _post(
        "/api/v1/bots/t1/bot-001/files/download-url",
        json_data={"device_path": "/x"},
    )

    assert resp.status_code == 500
    assert resp.json()["detail"]["error"] == "INTERNAL_ERROR"


# ── complete_upload tests ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_complete_upload_success(mock_dispatcher):
    """POST complete upload returns 200."""
    mock_dispatcher.dispatch_complete_upload.return_value = CompleteUploadResponse(
        transfer_id="tf-001",
        status="UPLOAD_COMPLETED",
    )
    resp = await _post(
        "/api/v1/bots/t1/bot-001/files/upload-url/tf-001/complete",
    )

    assert resp.status_code == 200
    assert resp.json()["data"]["status"] == "UPLOAD_COMPLETED"


@pytest.mark.asyncio
async def test_complete_upload_transfer_not_found(mock_dispatcher):
    """POST complete upload with TransferNotFoundError returns 404."""
    mock_dispatcher.dispatch_complete_upload.side_effect = TransferNotFoundError("nope")
    resp = await _post(
        "/api/v1/bots/t1/bot-001/files/upload-url/tf-001/complete",
    )

    assert resp.status_code == 404
    assert resp.json()["detail"]["error"] == "TRANSFER_NOT_FOUND"


@pytest.mark.asyncio
async def test_complete_upload_staging_object_not_found(mock_dispatcher):
    """POST complete upload with StagingObjectNotFoundError returns 409."""
    mock_dispatcher.dispatch_complete_upload.side_effect = StagingObjectNotFoundError(
        staging_path="oss://missing",
    )
    resp = await _post(
        "/api/v1/bots/t1/bot-001/files/upload-url/tf-001/complete",
    )

    assert resp.status_code == 409
    assert resp.json()["detail"]["error"] == "STAGING_OBJECT_NOT_FOUND"


@pytest.mark.asyncio
async def test_complete_upload_state_conflict(mock_dispatcher):
    """POST complete upload with TransferStateConflictError returns 409."""
    mock_dispatcher.dispatch_complete_upload.side_effect = TransferStateConflictError(
        "bad state"
    )
    resp = await _post(
        "/api/v1/bots/t1/bot-001/files/upload-url/tf-001/complete",
    )

    assert resp.status_code == 409
    assert resp.json()["detail"]["error"] == "TRANSFER_STATE_CONFLICT"


@pytest.mark.asyncio
async def test_complete_upload_not_implemented(mock_dispatcher):
    """POST complete upload with NotImplementedError returns 501."""
    mock_dispatcher.dispatch_complete_upload.side_effect = NotImplementedError("nope")
    resp = await _post(
        "/api/v1/bots/t1/bot-001/files/upload-url/tf-001/complete",
    )

    assert resp.status_code == 501
    assert resp.json()["detail"]["error"] == "NOT_IMPLEMENTED"


@pytest.mark.asyncio
async def test_complete_upload_generic_exception(mock_dispatcher):
    """POST complete upload with generic Exception returns 500."""
    mock_dispatcher.dispatch_complete_upload.side_effect = RuntimeError("boom")
    resp = await _post(
        "/api/v1/bots/t1/bot-001/files/upload-url/tf-001/complete",
    )

    assert resp.status_code == 500
    assert resp.json()["detail"]["error"] == "INTERNAL_ERROR"


# ── cancel_upload tests ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_cancel_upload_success(mock_dispatcher):
    """DELETE cancel upload returns 200."""
    mock_dispatcher.dispatch_cancel_upload.return_value = CancelUploadResponse(
        transfer_id="tf-001",
        status="CANCELLED",
    )
    resp = await _delete(
        "/api/v1/bots/t1/bot-001/files/upload-url/tf-001",
    )

    assert resp.status_code == 200
    assert resp.json()["data"]["status"] == "CANCELLED"


@pytest.mark.asyncio
async def test_cancel_upload_transfer_not_found(mock_dispatcher):
    """DELETE cancel upload with TransferNotFoundError returns 404."""
    mock_dispatcher.dispatch_cancel_upload.side_effect = TransferNotFoundError("nope")
    resp = await _delete(
        "/api/v1/bots/t1/bot-001/files/upload-url/tf-001",
    )

    assert resp.status_code == 404
    assert resp.json()["detail"]["error"] == "TRANSFER_NOT_FOUND"


@pytest.mark.asyncio
async def test_cancel_upload_state_conflict(mock_dispatcher):
    """DELETE cancel upload with TransferStateConflictError returns 409."""
    mock_dispatcher.dispatch_cancel_upload.side_effect = TransferStateConflictError(
        "bad state"
    )
    resp = await _delete(
        "/api/v1/bots/t1/bot-001/files/upload-url/tf-001",
    )

    assert resp.status_code == 409
    assert resp.json()["detail"]["error"] == "TRANSFER_STATE_CONFLICT"


@pytest.mark.asyncio
async def test_cancel_upload_not_implemented(mock_dispatcher):
    """DELETE cancel upload with NotImplementedError returns 501."""
    mock_dispatcher.dispatch_cancel_upload.side_effect = NotImplementedError("nope")
    resp = await _delete(
        "/api/v1/bots/t1/bot-001/files/upload-url/tf-001",
    )

    assert resp.status_code == 501
    assert resp.json()["detail"]["error"] == "NOT_IMPLEMENTED"


@pytest.mark.asyncio
async def test_cancel_upload_generic_exception(mock_dispatcher):
    """DELETE cancel upload with generic Exception returns 500."""
    mock_dispatcher.dispatch_cancel_upload.side_effect = RuntimeError("boom")
    resp = await _delete(
        "/api/v1/bots/t1/bot-001/files/upload-url/tf-001",
    )

    assert resp.status_code == 500
    assert resp.json()["detail"]["error"] == "INTERNAL_ERROR"


# ── delete_transfer tests ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_delete_transfer_success(mock_dispatcher):
    """DELETE transfer returns 200 with DeleteTransferResponse."""
    mock_dispatcher.dispatch_delete_transfer.return_value = DeleteTransferResponse(
        transfer_id="tf-001",
        previous_status="DONE",
        new_status="DELETED",
    )
    resp = await _delete("/api/v1/bots/t1/bot-001/files/transfers/tf-001")

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["transfer_id"] == "tf-001"
    assert data["new_status"] == "DELETED"


@pytest.mark.asyncio
async def test_delete_transfer_not_found(mock_dispatcher):
    """DELETE transfer with TransferNotFoundError returns 404."""
    mock_dispatcher.dispatch_delete_transfer.side_effect = TransferNotFoundError("nope")
    resp = await _delete("/api/v1/bots/t1/bot-001/files/transfers/tf-001")

    assert resp.status_code == 404
    assert resp.json()["detail"]["error"] == "TRANSFER_NOT_FOUND"


@pytest.mark.asyncio
async def test_delete_transfer_state_conflict(mock_dispatcher):
    """DELETE transfer with TransferStateConflictError returns 409."""
    mock_dispatcher.dispatch_delete_transfer.side_effect = TransferStateConflictError(
        "bad state"
    )
    resp = await _delete("/api/v1/bots/t1/bot-001/files/transfers/tf-001")

    assert resp.status_code == 409
    assert resp.json()["detail"]["error"] == "TRANSFER_STATE_CONFLICT"


@pytest.mark.asyncio
async def test_delete_transfer_not_implemented(mock_dispatcher):
    """DELETE transfer with NotImplementedError returns 501."""
    mock_dispatcher.dispatch_delete_transfer.side_effect = NotImplementedError("nope")
    resp = await _delete("/api/v1/bots/t1/bot-001/files/transfers/tf-001")

    assert resp.status_code == 501
    assert resp.json()["detail"]["error"] == "NOT_IMPLEMENTED"


@pytest.mark.asyncio
async def test_delete_transfer_generic_exception(mock_dispatcher):
    """DELETE transfer with generic Exception returns 500."""
    mock_dispatcher.dispatch_delete_transfer.side_effect = RuntimeError("boom")
    resp = await _delete("/api/v1/bots/t1/bot-001/files/transfers/tf-001")

    assert resp.status_code == 500
    assert resp.json()["detail"]["error"] == "INTERNAL_ERROR"


# ── generate_share_link tests ────────────────────────────────────────


@pytest.mark.asyncio
async def test_generate_share_link_success(mock_dispatcher):
    """POST share-link returns 200."""
    mock_dispatcher.dispatch_generate_share_link.return_value = ShareLinkResponse(
        share_url="https://oss.example.com/dl?token=abc",
        transfer_id="tf-001",
        expires_at="2099-01-01T00:00:00",
    )
    resp = await _post(
        "/api/v1/bots/t1/bot-001/files/transfers/tf-001/share-link",
        json_data={"expire_seconds": 3600},
    )

    assert resp.status_code == 200
    assert resp.json()["data"]["share_url"].startswith("https://")


@pytest.mark.asyncio
async def test_generate_share_link_transfer_not_found(mock_dispatcher):
    """POST share-link with TransferNotFoundError returns 404."""
    mock_dispatcher.dispatch_generate_share_link.side_effect = TransferNotFoundError(
        "nope"
    )
    resp = await _post(
        "/api/v1/bots/t1/bot-001/files/transfers/tf-001/share-link",
        json_data={"expire_seconds": 3600},
    )

    assert resp.status_code == 404
    assert resp.json()["detail"]["error"] == "TRANSFER_NOT_FOUND"


@pytest.mark.asyncio
async def test_generate_share_link_value_error(mock_dispatcher):
    """POST share-link with ValueError returns 422."""
    mock_dispatcher.dispatch_generate_share_link.side_effect = ValueError("not DONE")
    resp = await _post(
        "/api/v1/bots/t1/bot-001/files/transfers/tf-001/share-link",
        json_data={"expire_seconds": 3600},
    )

    assert resp.status_code == 422
    assert resp.json()["detail"]["error"] == "INVALID_TRANSITION"


@pytest.mark.asyncio
async def test_generate_share_link_not_implemented(mock_dispatcher):
    """POST share-link with NotImplementedError returns 501."""
    mock_dispatcher.dispatch_generate_share_link.side_effect = NotImplementedError(
        "nope"
    )
    resp = await _post(
        "/api/v1/bots/t1/bot-001/files/transfers/tf-001/share-link",
        json_data={"expire_seconds": 3600},
    )

    assert resp.status_code == 501
    assert resp.json()["detail"]["error"] == "NOT_IMPLEMENTED"


@pytest.mark.asyncio
async def test_generate_share_link_generic_exception(mock_dispatcher):
    """POST share-link with generic Exception returns 500."""
    mock_dispatcher.dispatch_generate_share_link.side_effect = RuntimeError("boom")
    resp = await _post(
        "/api/v1/bots/t1/bot-001/files/transfers/tf-001/share-link",
        json_data={"expire_seconds": 3600},
    )

    assert resp.status_code == 500
    assert resp.json()["detail"]["error"] == "INTERNAL_ERROR"


# ── transfer_query_router tests ─────────────────────────────────────


@pytest.fixture
def query_app():
    """Create a FastAPI app with only the transfer_query_router."""
    from secbaas.community.adapters.web.routers.bot_service.transfer_query_router import (
        router as tq_router,
    )

    _app = FastAPI()
    _app.include_router(tq_router)
    return _app


@pytest.fixture
def mock_query_dispatcher(query_app):
    """Override dispatcher dependency for transfer_query_router."""
    mock_instance = AsyncMock()
    old_overrides = dict(query_app.dependency_overrides)
    for route in iter_api_routes(query_app):
        for dep in route.dependant.dependencies:
            if dep.name == "dispatcher":
                query_app.dependency_overrides[dep.call] = lambda: mock_instance
    yield mock_instance
    query_app.dependency_overrides = old_overrides


@pytest.mark.asyncio
async def test_transfer_query_bot_not_found(mock_query_dispatcher, query_app):
    """transfer_query_router: BotNotFoundError returns 404 with BOT_NOT_FOUND."""
    mock_query_dispatcher.dispatch_get_transfer_status.side_effect = BotNotFoundError(
        "no bot"
    )
    transport = ASGITransport(app=query_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/v1/bots/t1/bot-001/files/transfers/tf-001")

    assert resp.status_code == 404
    assert resp.json()["detail"]["error"] == "BOT_NOT_FOUND"
