"""Unit tests for transfer_query_router endpoints.

Tests the GET /{tenant}/{bot_uuid}/files/transfers/{transfer_id} endpoint.
"""

from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from secbaas.community.adapters.web.routers.bot_service.transfer_query_router import (
    router,
)
from secbaas.community.api.bot_runtime import (
    GetTransferStatusResponse,
    TransferNotFoundError,
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


@pytest.mark.asyncio
async def test_get_transfer_status_success(mock_dispatcher):
    """GET transfer status returns 200 with GetTransferStatusResponse."""
    mock_dispatcher.dispatch_get_transfer_status.return_value = (
        GetTransferStatusResponse(
            transfer_id="tf-001",
            status="DONE",
            direction="UPLOAD",
            filename="data.csv",
            device_path="/home/data.csv",
            download_url="https://oss.example.com/dl?token=abc",
            expires_at="2099-01-01T00:00:00",
            created_at="2025-01-01T00:00:00",
            updated_at="2025-01-01T00:01:00",
            operator="unknown",
        )
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            "/api/v1/bots/t1/bot-001/files/transfers/tf-001",
        )

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["transfer_id"] == "tf-001"
    assert data["status"] == "DONE"
    assert data["download_url"].startswith("https://")


@pytest.mark.asyncio
async def test_get_transfer_status_not_found(mock_dispatcher):
    """GET transfer status with TransferNotFoundError returns 404."""
    mock_dispatcher.dispatch_get_transfer_status.side_effect = TransferNotFoundError(
        "nope"
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            "/api/v1/bots/t1/bot-001/files/transfers/tf-001",
        )

    assert resp.status_code == 404
    assert resp.json()["detail"]["error"] == "TRANSFER_NOT_FOUND"


@pytest.mark.asyncio
async def test_get_transfer_status_generic_exception(mock_dispatcher):
    """GET transfer status with generic Exception returns 500."""
    mock_dispatcher.dispatch_get_transfer_status.side_effect = RuntimeError(
        "unexpected"
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            "/api/v1/bots/t1/bot-001/files/transfers/tf-001",
        )

    assert resp.status_code == 500
    assert resp.json()["detail"]["error"] == "INTERNAL_ERROR"
