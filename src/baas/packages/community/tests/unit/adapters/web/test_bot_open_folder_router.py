# mypy: disable-error-code="arg-type"
"""Unit tests for bot_open_folder_router endpoints.

Tests the open-folder router including:
- Successful open_folder (POST)
- Bot not found (404)
- No devices found (404)
- No active devices (503)
- Platform not supported (501)
- Facade error (500)
- Internal server error (500)
- Device affinity parameter passthrough
"""

from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from secbaas.adapters.web.routers.bot_service.open_folder_router import router
from secbaas.bootstrap import ApplicationContainer
from secbaas.core.service.paas import DeviceFacadeException, ErrorCode, PaasError
from tests.unit.adapters.web.conftest import iter_api_routes

app = FastAPI()
app.include_router(router)


@pytest.fixture
def mock_dispatcher():
    """Override the Provide dependency to return a mock DefaultBotOpenFolderDispatcher."""
    mock_instance = AsyncMock()
    old_overrides = dict(app.dependency_overrides)
    for route in iter_api_routes(app):
        for dep in route.dependant.dependencies:
            if dep.name == "dispatcher":
                app.dependency_overrides[dep.call] = lambda: mock_instance
    yield mock_instance
    app.dependency_overrides = old_overrides


class TestOpenFolderEndpoint:
    """Test the POST /api/v1/bots/{tenant}/{bot_uuid}/open-folder endpoint."""

    @pytest.mark.asyncio
    async def test_open_folder_success(self, mock_dispatcher):
        """Successful open_folder returns 200 with SuccessResponse."""
        mock_dispatcher.dispatch_bot_open_folder.return_value = True

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/v1/bots/test_tenant/bot-uuid-001/open-folder",
                json={"folder_path": "/home/user/projects"},
            )

        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["success"] is True

        mock_dispatcher.dispatch_bot_open_folder.assert_awaited_once()
        call_kwargs = mock_dispatcher.dispatch_bot_open_folder.call_args.kwargs
        assert call_kwargs["bot_uuid"] == "bot-uuid-001"
        assert call_kwargs["folder_path"] == "/home/user/projects"
        assert call_kwargs["tenant"] == "test_tenant"
        assert call_kwargs["device_affinity"] is None

    @pytest.mark.asyncio
    async def test_open_folder_no_request_body(self, mock_dispatcher):
        """open_folder with no request body uses default folder_path=None."""
        mock_dispatcher.dispatch_bot_open_folder.return_value = True

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/v1/bots/test_tenant/bot-uuid-001/open-folder",
            )

        assert resp.status_code == 200
        call_kwargs = mock_dispatcher.dispatch_bot_open_folder.call_args.kwargs
        assert call_kwargs["folder_path"] is None

    @pytest.mark.asyncio
    async def test_open_folder_empty_body(self, mock_dispatcher):
        """open_folder with empty JSON body uses default folder_path=None."""
        mock_dispatcher.dispatch_bot_open_folder.return_value = True

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/v1/bots/test_tenant/bot-uuid-001/open-folder",
                json={},
            )

        assert resp.status_code == 200
        call_kwargs = mock_dispatcher.dispatch_bot_open_folder.call_args.kwargs
        assert call_kwargs["folder_path"] is None

    @pytest.mark.asyncio
    async def test_open_folder_with_affinity(self, mock_dispatcher):
        """device_affinity query parameter is passed through to the service."""
        mock_dispatcher.dispatch_bot_open_folder.return_value = True

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/v1/bots/test_tenant/bot-uuid-001/open-folder"
                "?device_affinity=session-abc",
                json={"folder_path": "/tmp"},
            )

        assert resp.status_code == 200
        call_kwargs = mock_dispatcher.dispatch_bot_open_folder.call_args.kwargs
        assert call_kwargs["device_affinity"] == "session-abc"
        assert call_kwargs["folder_path"] == "/tmp"

    @pytest.mark.asyncio
    async def test_open_folder_bot_not_found_404(self, mock_dispatcher):
        """BotNotFoundError returns 404."""
        from secbaas.api.bot_runtime import BotNotFoundError

        mock_dispatcher.dispatch_bot_open_folder.side_effect = BotNotFoundError(
            "Bot not found: nonexistent"
        )

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/v1/bots/test_tenant/nonexistent/open-folder",
                json={"folder_path": "/tmp"},
            )

        assert resp.status_code == 404
        assert resp.json()["detail"]["error"] == "BOT_NOT_FOUND"

    @pytest.mark.asyncio
    async def test_open_folder_no_devices_found_404(self, mock_dispatcher):
        """NoDevicesFoundError returns 404."""
        from secbaas.api.bot_runtime import NoDevicesFoundError

        mock_dispatcher.dispatch_bot_open_folder.side_effect = NoDevicesFoundError(
            "No devices found"
        )

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/v1/bots/test_tenant/bot-uuid-001/open-folder",
                json={"folder_path": "/tmp"},
            )

        assert resp.status_code == 404
        assert resp.json()["detail"]["error"] == "NO_DEVICES_FOUND"

    @pytest.mark.asyncio
    async def test_open_folder_no_active_devices_503(self, mock_dispatcher):
        """NoActiveDevicesError returns 503."""
        from secbaas.api.bot_runtime import NoActiveDevicesError

        mock_dispatcher.dispatch_bot_open_folder.side_effect = NoActiveDevicesError(
            "No active devices"
        )

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/v1/bots/test_tenant/bot-uuid-001/open-folder",
                json={"folder_path": "/tmp"},
            )

        assert resp.status_code == 503
        assert resp.json()["detail"]["error"] == "NO_ACTIVE_DEVICES"

    @pytest.mark.asyncio
    async def test_open_folder_platform_not_supported_501(self, mock_dispatcher):
        """DeviceFacadeException with PLATFORM_ERROR returns 501."""
        mock_dispatcher.dispatch_bot_open_folder.side_effect = DeviceFacadeException(
            operation="open_folder",
            platform_type="ARCA",
            template_id=42,
            paas_device_id="container--machine--user",
            original_error=PaasError(
                ErrorCode.PLATFORM_ERROR,
                "open_folder is not supported on ArcaPaasService",
            ),
        )

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/v1/bots/test_tenant/bot-uuid-001/open-folder",
                json={"folder_path": "/tmp"},
            )

        assert resp.status_code == 501
        assert resp.json()["detail"]["error"] == "NOT_IMPLEMENTED"

    @pytest.mark.asyncio
    async def test_open_folder_facade_error_500(self, mock_dispatcher):
        """DeviceFacadeException with non-PLATFORM_ERROR returns 500."""
        mock_dispatcher.dispatch_bot_open_folder.side_effect = DeviceFacadeException(
            operation="open_folder",
            platform_type="LOCAL",
            template_id=42,
            paas_device_id="container--machine--user",
            original_error=PaasError(ErrorCode.COMMAND_FAILED, "Command failed"),
        )

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/v1/bots/test_tenant/bot-uuid-001/open-folder",
                json={"folder_path": "/tmp"},
            )

        assert resp.status_code == 500
        detail = resp.json()["detail"]
        assert detail["error"] == "COMMAND_FAILED"

    @pytest.mark.asyncio
    async def test_open_folder_internal_error_500(self, mock_dispatcher):
        """Unexpected exception returns 500 with INTERNAL_ERROR."""
        mock_dispatcher.dispatch_bot_open_folder.side_effect = RuntimeError(
            "Unexpected failure"
        )

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/v1/bots/test_tenant/bot-uuid-001/open-folder",
                json={"folder_path": "/tmp"},
            )

        assert resp.status_code == 500
        assert resp.json()["detail"]["error"] == "INTERNAL_ERROR"
