# mypy: disable-error-code="arg-type"
"""Unit tests for bot_start_progress_router endpoints.

Tests the start-progress router including:
- Successful fetch_start_progress (GET)
- Bot not found (404)
- No devices found (404)
- No active devices (503)
- Platform not supported (501)
- Facade error (500)
- Internal server error (500)
- Device affinity parameter passthrough
- Extra field passthrough via model extra="allow"
"""

from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from secbaas.community.adapters.web.routers.bot_service.bot_start_progress_router import (
    router,
)
from secbaas.community.core.service.paas import (
    DeviceFacadeException,
    ErrorCode,
    PaasError,
)
from tests.unit.adapters.web.conftest import iter_api_routes

app = FastAPI()
app.include_router(router)


@pytest.fixture
def mock_dispatcher():
    """Override the Provide dependency to return a mock BotFetchStartProgressDispatcher."""
    mock_instance = AsyncMock()
    old_overrides = dict(app.dependency_overrides)
    for route in iter_api_routes(app):
        for dep in route.dependant.dependencies:
            if dep.name == "dispatcher":
                app.dependency_overrides[dep.call] = lambda: mock_instance
    yield mock_instance
    app.dependency_overrides = old_overrides


@pytest.fixture
def mock_progress_response():
    """Create a mock BotStartProgressResponse."""
    from secbaas.community.api.bot_manage import BotStartProgressResponse

    return BotStartProgressResponse(
        progress="in_progress",
        current_phase="pulling_image",
        error_message=None,
    )


class TestFetchStartProgressEndpoint:
    """Test the GET /api/v1/bots/{bot_uuid}/start-progress endpoint."""

    @pytest.mark.asyncio
    async def test_fetch_start_progress_ok(
        self, mock_dispatcher, mock_progress_response
    ):
        """Successful fetch_start_progress returns 200 with BotStartProgressResponse."""
        mock_dispatcher.dispatch_bot_fetch_start_progress.return_value = (
            mock_progress_response
        )

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                "/api/v1/bots/bot-uuid-001/start-progress?tenant=test_tenant",
            )

        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["progress"] == "in_progress"
        assert data["current_phase"] == "pulling_image"
        assert data["error_message"] is None

        mock_dispatcher.dispatch_bot_fetch_start_progress.assert_awaited_once()
        call_kwargs = mock_dispatcher.dispatch_bot_fetch_start_progress.call_args.kwargs
        assert call_kwargs["bot_uuid"] == "bot-uuid-001"
        assert call_kwargs["tenant"] == "test_tenant"
        assert call_kwargs["device_affinity"] is None

    @pytest.mark.asyncio
    async def test_response_includes_extra_fields(self, mock_dispatcher):
        """HTTP response JSON includes extra fields passed through by mng daemon."""
        from secbaas.community.api.bot_manage import BotStartProgressResponse

        mock_dispatcher.dispatch_bot_fetch_start_progress.return_value = (
            BotStartProgressResponse(
                progress="completed",
                current_phase="ready",
                overall_status="completed",
                custom_field=42,
            )
        )

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                "/api/v1/bots/bot-uuid-001/start-progress?tenant=test_tenant",
            )

        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["progress"] == "completed"
        assert data["current_phase"] == "ready"
        assert data["overall_status"] == "completed"
        assert data["custom_field"] == 42

    @pytest.mark.asyncio
    async def test_fetch_start_progress_with_affinity(
        self, mock_dispatcher, mock_progress_response
    ):
        """device_affinity query parameter is passed through to the dispatcher."""
        mock_dispatcher.dispatch_bot_fetch_start_progress.return_value = (
            mock_progress_response
        )

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                "/api/v1/bots/bot-uuid-001/start-progress"
                "?tenant=test_tenant&device_affinity=session-abc",
            )

        assert resp.status_code == 200
        call_kwargs = mock_dispatcher.dispatch_bot_fetch_start_progress.call_args.kwargs
        assert call_kwargs["device_affinity"] == "session-abc"

    @pytest.mark.asyncio
    async def test_fetch_start_progress_completed(self, mock_dispatcher):
        """Successful fetch_start_progress with completed status."""
        from secbaas.community.api.bot_manage import BotStartProgressResponse

        mock_dispatcher.dispatch_bot_fetch_start_progress.return_value = (
            BotStartProgressResponse(
                progress="completed",
                current_phase="ready",
            )
        )

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                "/api/v1/bots/bot-uuid-001/start-progress?tenant=test_tenant",
            )

        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["progress"] == "completed"
        assert data["current_phase"] == "ready"

    @pytest.mark.asyncio
    async def test_fetch_start_progress_failed(self, mock_dispatcher):
        """Successful fetch_start_progress with failed status and error message."""
        from secbaas.community.api.bot_manage import BotStartProgressResponse

        mock_dispatcher.dispatch_bot_fetch_start_progress.return_value = (
            BotStartProgressResponse(
                progress="failed",
                current_phase="starting_process",
                error_message="Container exited with code 1",
            )
        )

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                "/api/v1/bots/bot-uuid-001/start-progress?tenant=test_tenant",
            )

        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["progress"] == "failed"
        assert data["current_phase"] == "starting_process"
        assert data["error_message"] == "Container exited with code 1"

    @pytest.mark.asyncio
    async def test_fetch_start_progress_bot_not_found_404(self, mock_dispatcher):
        """BotNotFoundError returns 404."""
        from secbaas.community.api.bot_runtime import BotNotFoundError

        mock_dispatcher.dispatch_bot_fetch_start_progress.side_effect = (
            BotNotFoundError("Bot not found: nonexistent")
        )

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                "/api/v1/bots/nonexistent/start-progress?tenant=test_tenant",
            )

        assert resp.status_code == 404
        assert resp.json()["detail"]["error"] == "BOT_NOT_FOUND"

    @pytest.mark.asyncio
    async def test_fetch_start_progress_no_devices_found_503(self, mock_dispatcher):
        """NoDevicesFoundError returns 503 with Retry-After header (D-01)."""
        from secbaas.community.api.bot_runtime import NoDevicesFoundError

        mock_dispatcher.dispatch_bot_fetch_start_progress.side_effect = (
            NoDevicesFoundError("No devices found")
        )

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                "/api/v1/bots/bot-uuid-001/start-progress?tenant=test_tenant",
            )

        assert resp.status_code == 503
        assert resp.json()["detail"]["error"] == "NO_DEVICES_FOUND"
        assert "Retry-After" in resp.headers
        assert resp.headers["Retry-After"] == "5"

    @pytest.mark.asyncio
    async def test_fetch_start_progress_no_active_devices_503(self, mock_dispatcher):
        """NoActiveDevicesError returns 503."""
        from secbaas.community.api.bot_runtime import NoActiveDevicesError

        mock_dispatcher.dispatch_bot_fetch_start_progress.side_effect = (
            NoActiveDevicesError("No active devices")
        )

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                "/api/v1/bots/bot-uuid-001/start-progress?tenant=test_tenant",
            )

        assert resp.status_code == 503
        assert resp.json()["detail"]["error"] == "NO_ACTIVE_DEVICES"

    @pytest.mark.asyncio
    async def test_fetch_start_progress_platform_not_supported_501(
        self, mock_dispatcher
    ):
        """DeviceFacadeException with PLATFORM_ERROR returns 501."""
        mock_dispatcher.dispatch_bot_fetch_start_progress.side_effect = (
            DeviceFacadeException(
                operation="fetch_start_progress",
                platform_type="ARCA",
                template_id=42,
                paas_device_id="container--machine--user",
                original_error=PaasError(
                    ErrorCode.PLATFORM_ERROR,
                    "fetch_start_progress is not supported on ArcaPaasService",
                ),
            )
        )

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                "/api/v1/bots/bot-uuid-001/start-progress?tenant=test_tenant",
            )

        assert resp.status_code == 501
        assert resp.json()["detail"]["error"] == "NOT_IMPLEMENTED"

    @pytest.mark.asyncio
    async def test_fetch_start_progress_facade_error_500(self, mock_dispatcher):
        """DeviceFacadeException with non-PLATFORM_ERROR returns 500."""
        mock_dispatcher.dispatch_bot_fetch_start_progress.side_effect = (
            DeviceFacadeException(
                operation="fetch_start_progress",
                platform_type="LOCAL",
                template_id=42,
                paas_device_id="container--machine--user",
                original_error=PaasError(ErrorCode.COMMAND_FAILED, "Command failed"),
            )
        )

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                "/api/v1/bots/bot-uuid-001/start-progress?tenant=test_tenant",
            )

        assert resp.status_code == 500
        detail = resp.json()["detail"]
        assert detail["error"] == "COMMAND_FAILED"

    @pytest.mark.asyncio
    async def test_fetch_start_progress_internal_error_500(self, mock_dispatcher):
        """Unexpected exception returns 500 with INTERNAL_ERROR."""
        mock_dispatcher.dispatch_bot_fetch_start_progress.side_effect = RuntimeError(
            "Unexpected failure"
        )

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                "/api/v1/bots/bot-uuid-001/start-progress?tenant=test_tenant",
            )

        assert resp.status_code == 500
        assert resp.json()["detail"]["error"] == "INTERNAL_ERROR"

    # ============ NEW TESTS: D-02 bot_status refinement ============

    @pytest.mark.asyncio
    async def test_fetch_start_progress_bot_released_404(self, mock_dispatcher):
        """BotNotFoundError with bot_status='RELEASED' returns 404 BOT_RELEASED."""
        from secbaas.community.api.bot_runtime import BotNotFoundError

        mock_dispatcher.dispatch_bot_fetch_start_progress.side_effect = (
            BotNotFoundError("Bot not found: released-bot", bot_status="RELEASED")
        )

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                "/api/v1/bots/released-bot/start-progress?tenant=test_tenant",
            )

        assert resp.status_code == 404
        detail = resp.json()["detail"]
        assert detail["error"] == "BOT_RELEASED"
        assert detail["bot_uuid"] == "released-bot"

    @pytest.mark.asyncio
    async def test_fetch_start_progress_bot_failed_404(self, mock_dispatcher):
        """BotNotFoundError with bot_status='FAILED' returns 404 BOT_FAILED."""
        from secbaas.community.api.bot_runtime import BotNotFoundError

        mock_dispatcher.dispatch_bot_fetch_start_progress.side_effect = (
            BotNotFoundError("Bot not found: failed-bot", bot_status="FAILED")
        )

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                "/api/v1/bots/failed-bot/start-progress?tenant=test_tenant",
            )

        assert resp.status_code == 404
        detail = resp.json()["detail"]
        assert detail["error"] == "BOT_FAILED"
        assert detail["bot_uuid"] == "failed-bot"

    @pytest.mark.asyncio
    async def test_fetch_start_progress_retry_after_header_on_no_devices(
        self, mock_dispatcher
    ):
        """NoDevicesFoundError includes Retry-After: 5 header."""
        from secbaas.community.api.bot_runtime import NoDevicesFoundError

        mock_dispatcher.dispatch_bot_fetch_start_progress.side_effect = (
            NoDevicesFoundError("No devices found")
        )

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                "/api/v1/bots/bot-uuid-001/start-progress?tenant=test_tenant",
            )

        assert "Retry-After" in resp.headers
        assert resp.headers["Retry-After"] == "5"

    @pytest.mark.asyncio
    async def test_fetch_start_progress_no_active_devices_retry_after(
        self, mock_dispatcher
    ):
        """NoActiveDevicesError includes Retry-After: 5 header."""
        from secbaas.community.api.bot_runtime import NoActiveDevicesError

        mock_dispatcher.dispatch_bot_fetch_start_progress.side_effect = (
            NoActiveDevicesError("No active devices")
        )

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                "/api/v1/bots/bot-uuid-001/start-progress?tenant=test_tenant",
            )

        assert "Retry-After" in resp.headers
        assert resp.headers["Retry-After"] == "5"

    # ============ NEW TESTS: D-03 lookup table ============

    @pytest.mark.asyncio
    async def test_fetch_start_progress_platform_error_501(self, mock_dispatcher):
        """DeviceFacadeException with PLATFORM_ERROR returns 501 NOT_IMPLEMENTED."""
        from secbaas.community.core.service.paas import (
            DeviceFacadeException,
            ErrorCode,
            PaasError,
        )

        mock_dispatcher.dispatch_bot_fetch_start_progress.side_effect = (
            DeviceFacadeException(
                operation="fetch_start_progress",
                platform_type="ARCA",
                template_id=42,
                paas_device_id="container--machine--user",
                original_error=PaasError(
                    ErrorCode.PLATFORM_ERROR,
                    "not supported on ARCA",
                ),
            )
        )

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                "/api/v1/bots/bot-uuid-001/start-progress?tenant=test_tenant",
            )

        assert resp.status_code == 501
        detail = resp.json()["detail"]
        assert detail["error"] == "NOT_IMPLEMENTED"
        assert "context" in detail

    @pytest.mark.asyncio
    async def test_fetch_start_progress_template_not_found_404(self, mock_dispatcher):
        """DeviceFacadeException with TEMPLATE_NOT_FOUND returns 404."""
        from secbaas.community.core.service.paas import (
            DeviceFacadeException,
            ErrorCode,
            PaasError,
        )

        mock_dispatcher.dispatch_bot_fetch_start_progress.side_effect = (
            DeviceFacadeException(
                operation="fetch_start_progress",
                platform_type="LOCAL",
                template_id=42,
                paas_device_id="container--machine--user",
                original_error=PaasError(
                    ErrorCode.TEMPLATE_NOT_FOUND, "Template not found"
                ),
            )
        )

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                "/api/v1/bots/bot-uuid-001/start-progress?tenant=test_tenant",
            )

        assert resp.status_code == 404
        assert resp.json()["detail"]["error"] == "TEMPLATE_NOT_FOUND"

    @pytest.mark.asyncio
    async def test_fetch_start_progress_command_timeout_504(self, mock_dispatcher):
        """DeviceFacadeException with COMMAND_TIMEOUT returns 504."""
        from secbaas.community.core.service.paas import (
            DeviceFacadeException,
            ErrorCode,
            PaasError,
        )

        mock_dispatcher.dispatch_bot_fetch_start_progress.side_effect = (
            DeviceFacadeException(
                operation="fetch_start_progress",
                platform_type="LOCAL",
                template_id=42,
                paas_device_id="container--machine--user",
                original_error=PaasError(
                    ErrorCode.COMMAND_TIMEOUT, "Command timed out"
                ),
            )
        )

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                "/api/v1/bots/bot-uuid-001/start-progress?tenant=test_tenant",
            )

        assert resp.status_code == 504
        assert resp.json()["detail"]["error"] == "COMMAND_TIMEOUT"

    @pytest.mark.asyncio
    async def test_fetch_start_progress_unknown_error_code_500(self, mock_dispatcher):
        """DeviceFacadeException with unlisted ErrorCode falls back to 500."""
        from secbaas.community.core.service.paas import (
            DeviceFacadeException,
            ErrorCode,
            PaasError,
        )

        mock_dispatcher.dispatch_bot_fetch_start_progress.side_effect = (
            DeviceFacadeException(
                operation="fetch_start_progress",
                platform_type="LOCAL",
                template_id=42,
                paas_device_id="container--machine--user",
                original_error=PaasError(
                    ErrorCode.DEVICE_CREATION_FAILED, "Creation failed"
                ),
            )
        )

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                "/api/v1/bots/bot-uuid-001/start-progress?tenant=test_tenant",
            )

        assert resp.status_code == 500
        assert resp.json()["detail"]["error"] == "DEVICE_CREATION_FAILED"
