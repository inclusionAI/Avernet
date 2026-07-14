# mypy: disable-error-code="arg-type"
"""Unit tests for publish start-progress endpoint.

Tests the GET /api/v1/publishes/{publish_id}/start-progress endpoint including:
- Successful fetch_start_progress via publish_id (200)
- Publish not found (404 with PUBLISH_NOT_FOUND)
- Bot not found (404 with BOT_NOT_FOUND)
- No devices found (404 with NO_DEVICES_FOUND)
- No active devices (503 with NO_ACTIVE_DEVICES)
- Platform not supported (501 with NOT_IMPLEMENTED)
- Facade error (500 with FACADE_ERROR / COMMAND_FAILED)
- Internal server error (500 with INTERNAL_ERROR)
- device_affinity parameter passthrough
- Tenant parameter passthrough
- Extra field passthrough via model extra="allow"
- Publish service call verification
"""

from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from secbaas.community.adapters.web.routers.bot_service.publish_router import router
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
def mock_publish_service():
    """Override the Provide dependency to return a mock PublishService."""
    mock_instance = AsyncMock()
    old_overrides = dict(app.dependency_overrides)
    for route in iter_api_routes(app):
        for dep in route.dependant.dependencies:
            if dep.name == "publish_service":
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


class TestFetchStartProgressViaPublishId:
    """Test the GET /api/v1/publishes/{publish_id}/start-progress endpoint."""

    @pytest.mark.asyncio
    async def test_fetch_start_progress_ok(
        self, mock_dispatcher, mock_publish_service, mock_progress_response
    ):
        """Successful start-progress fetch via publish_id returns 200."""
        mock_publish_service.get_publish_bot_uuid.return_value = "bot-uuid-001"
        mock_dispatcher.dispatch_bot_fetch_start_progress.return_value = (
            mock_progress_response
        )

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                "/api/v1/publishes/42/start-progress?tenant=test_tenant",
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
    async def test_publish_not_found_404(self, mock_dispatcher, mock_publish_service):
        """Invalid/unknown publish_id returns 404 with PUBLISH_NOT_FOUND."""
        from secbaas.community.api.publish_manage import PublishNotFoundError

        mock_publish_service.get_publish_bot_uuid.side_effect = PublishNotFoundError(
            999
        )

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                "/api/v1/publishes/999/start-progress?tenant=test_tenant",
            )

        assert resp.status_code == 404
        assert resp.json()["detail"]["error"] == "PUBLISH_NOT_FOUND"

    @pytest.mark.asyncio
    async def test_bot_not_found_404(self, mock_dispatcher, mock_publish_service):
        """Publish resolves but bot is missing returns 404 with BOT_NOT_FOUND."""
        from secbaas.community.api.bot_runtime import BotNotFoundError

        mock_publish_service.get_publish_bot_uuid.return_value = "bot-uuid-missing"
        mock_dispatcher.dispatch_bot_fetch_start_progress.side_effect = (
            BotNotFoundError("Bot not found: bot-uuid-missing")
        )

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                "/api/v1/publishes/42/start-progress?tenant=test_tenant",
            )

        assert resp.status_code == 404
        assert resp.json()["detail"]["error"] == "BOT_NOT_FOUND"

    @pytest.mark.asyncio
    async def test_no_devices_found_503(self, mock_dispatcher, mock_publish_service):
        """Bot has no devices returns 503 with Retry-After + NO_DEVICES_FOUND (D-01)."""
        from secbaas.community.api.bot_runtime import NoDevicesFoundError

        mock_publish_service.get_publish_bot_uuid.return_value = "bot-uuid-no-devices"
        mock_dispatcher.dispatch_bot_fetch_start_progress.side_effect = (
            NoDevicesFoundError("No devices found")
        )

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                "/api/v1/publishes/42/start-progress?tenant=test_tenant",
            )

        assert resp.status_code == 503
        assert resp.json()["detail"]["error"] == "NO_DEVICES_FOUND"
        assert "Retry-After" in resp.headers
        assert resp.headers["Retry-After"] == "5"

    @pytest.mark.asyncio
    async def test_no_active_devices_503(self, mock_dispatcher, mock_publish_service):
        """Bot has devices but none active returns 503 with NO_ACTIVE_DEVICES."""
        from secbaas.community.api.bot_runtime import NoActiveDevicesError

        mock_publish_service.get_publish_bot_uuid.return_value = "bot-uuid-no-active"
        mock_dispatcher.dispatch_bot_fetch_start_progress.side_effect = (
            NoActiveDevicesError("No active devices")
        )

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                "/api/v1/publishes/42/start-progress?tenant=test_tenant",
            )

        assert resp.status_code == 503
        assert resp.json()["detail"]["error"] == "NO_ACTIVE_DEVICES"

    @pytest.mark.asyncio
    async def test_platform_not_supported_501(
        self, mock_dispatcher, mock_publish_service
    ):
        """Non-LOCAL platform returns 501 with NOT_IMPLEMENTED."""
        mock_publish_service.get_publish_bot_uuid.return_value = "bot-uuid-arca"
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
                "/api/v1/publishes/42/start-progress?tenant=test_tenant",
            )

        assert resp.status_code == 501
        assert resp.json()["detail"]["error"] == "NOT_IMPLEMENTED"

    @pytest.mark.asyncio
    async def test_facade_error_500(self, mock_dispatcher, mock_publish_service):
        """Paas internal error (non-PLATFORM_ERROR) returns 500."""
        mock_publish_service.get_publish_bot_uuid.return_value = "bot-uuid-local"
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
                "/api/v1/publishes/42/start-progress?tenant=test_tenant",
            )

        assert resp.status_code == 500
        detail = resp.json()["detail"]
        assert detail["error"] == "COMMAND_FAILED"

    @pytest.mark.asyncio
    async def test_internal_error_500(self, mock_dispatcher, mock_publish_service):
        """Unexpected exception returns 500 with INTERNAL_ERROR."""
        mock_publish_service.get_publish_bot_uuid.return_value = "bot-uuid-boom"
        mock_dispatcher.dispatch_bot_fetch_start_progress.side_effect = RuntimeError(
            "Unexpected failure"
        )

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                "/api/v1/publishes/42/start-progress?tenant=test_tenant",
            )

        assert resp.status_code == 500
        assert resp.json()["detail"]["error"] == "INTERNAL_ERROR"

    @pytest.mark.asyncio
    async def test_device_affinity_passthrough(
        self, mock_dispatcher, mock_publish_service, mock_progress_response
    ):
        """device_affinity query parameter is passed through to dispatcher."""
        mock_publish_service.get_publish_bot_uuid.return_value = "bot-uuid-001"
        mock_dispatcher.dispatch_bot_fetch_start_progress.return_value = (
            mock_progress_response
        )

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                "/api/v1/publishes/42/start-progress"
                "?tenant=test_tenant&device_affinity=sticky-key",
            )

        assert resp.status_code == 200
        call_kwargs = mock_dispatcher.dispatch_bot_fetch_start_progress.call_args.kwargs
        assert call_kwargs["device_affinity"] == "sticky-key"

    @pytest.mark.asyncio
    async def test_tenant_passthrough(
        self, mock_dispatcher, mock_publish_service, mock_progress_response
    ):
        """tenant query parameter flows through correctly."""
        mock_publish_service.get_publish_bot_uuid.return_value = "bot-uuid-001"
        mock_dispatcher.dispatch_bot_fetch_start_progress.return_value = (
            mock_progress_response
        )

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                "/api/v1/publishes/42/start-progress?tenant=tenant-a",
            )

        assert resp.status_code == 200
        call_kwargs = mock_dispatcher.dispatch_bot_fetch_start_progress.call_args.kwargs
        assert call_kwargs["tenant"] == "tenant-a"

    @pytest.mark.asyncio
    async def test_extra_fields_passthrough(
        self, mock_dispatcher, mock_publish_service
    ):
        """Response includes extra fields from mng daemon via extra='allow'."""
        from secbaas.community.api.bot_manage import BotStartProgressResponse

        mock_publish_service.get_publish_bot_uuid.return_value = "bot-uuid-001"
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
                "/api/v1/publishes/42/start-progress?tenant=test_tenant",
            )

        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["progress"] == "completed"
        assert data["current_phase"] == "ready"
        assert data["overall_status"] == "completed"
        assert data["custom_field"] == 42

    @pytest.mark.asyncio
    async def test_publish_service_call_kwargs(
        self, mock_dispatcher, mock_publish_service, mock_progress_response
    ):
        """get_publish_bot_uuid is called with correct tenant and publish_id."""
        mock_publish_service.get_publish_bot_uuid.return_value = "bot-uuid-001"
        mock_dispatcher.dispatch_bot_fetch_start_progress.return_value = (
            mock_progress_response
        )

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                "/api/v1/publishes/42/start-progress?tenant=test_tenant",
            )

        assert resp.status_code == 200
        mock_publish_service.get_publish_bot_uuid.assert_awaited_once_with(
            tenant="test_tenant", publish_id=42
        )

    # ============ NEW TESTS: D-02 bot_status refinement (publish_id) ============

    @pytest.mark.asyncio
    async def test_bot_released_404(self, mock_dispatcher, mock_publish_service):
        """BotNotFoundError bot_status=RELEASED → 404 BOT_RELEASED, detail includes publish_id (W-4)."""
        from secbaas.community.api.bot_runtime import BotNotFoundError

        mock_publish_service.get_publish_bot_uuid.return_value = "bot-uuid-released"
        mock_dispatcher.dispatch_bot_fetch_start_progress.side_effect = (
            BotNotFoundError("Bot not found: bot-uuid-released", bot_status="RELEASED")
        )

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                "/api/v1/publishes/42/start-progress?tenant=test_tenant",
            )

        assert resp.status_code == 404
        detail = resp.json()["detail"]
        assert detail["error"] == "BOT_RELEASED"
        assert detail["bot_uuid"] == "bot-uuid-released"
        assert detail["publish_id"] == 42

    @pytest.mark.asyncio
    async def test_bot_failed_404(self, mock_dispatcher, mock_publish_service):
        """BotNotFoundError bot_status=FAILED → 404 BOT_FAILED, detail includes publish_id (W-4)."""
        from secbaas.community.api.bot_runtime import BotNotFoundError

        mock_publish_service.get_publish_bot_uuid.return_value = "bot-uuid-failed"
        mock_dispatcher.dispatch_bot_fetch_start_progress.side_effect = (
            BotNotFoundError("Bot not found: bot-uuid-failed", bot_status="FAILED")
        )

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                "/api/v1/publishes/42/start-progress?tenant=test_tenant",
            )

        assert resp.status_code == 404
        detail = resp.json()["detail"]
        assert detail["error"] == "BOT_FAILED"
        assert detail["bot_uuid"] == "bot-uuid-failed"
        assert detail["publish_id"] == 42

    # ============ NEW TESTS: W-4 publish_id propagation ============

    @pytest.mark.asyncio
    async def test_no_active_devices_503_publish_id(
        self, mock_dispatcher, mock_publish_service
    ):
        """NoActiveDevicesError → 503, detail includes publish_id (W-4)."""
        from secbaas.community.api.bot_runtime import NoActiveDevicesError

        mock_publish_service.get_publish_bot_uuid.return_value = "bot-uuid-no-active"
        mock_dispatcher.dispatch_bot_fetch_start_progress.side_effect = (
            NoActiveDevicesError("No active devices")
        )

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                "/api/v1/publishes/42/start-progress?tenant=test_tenant",
            )

        assert resp.status_code == 503
        detail = resp.json()["detail"]
        assert detail["error"] == "NO_ACTIVE_DEVICES"
        assert detail["publish_id"] == 42

    @pytest.mark.asyncio
    async def test_facade_template_not_found_404_publish_id(
        self, mock_dispatcher, mock_publish_service
    ):
        """DeviceFacadeException TEMPLATE_NOT_FOUND → 404, detail includes publish_id (W-4)."""
        from secbaas.community.core.service.paas import (
            DeviceFacadeException,
            ErrorCode,
            PaasError,
        )

        mock_publish_service.get_publish_bot_uuid.return_value = "bot-uuid-local"
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
                "/api/v1/publishes/42/start-progress?tenant=test_tenant",
            )

        assert resp.status_code == 404
        detail = resp.json()["detail"]
        assert detail["error"] == "TEMPLATE_NOT_FOUND"
        assert detail["publish_id"] == 42

    @pytest.mark.asyncio
    async def test_facade_command_timeout_504_publish_id(
        self, mock_dispatcher, mock_publish_service
    ):
        """DeviceFacadeException COMMAND_TIMEOUT → 504, detail includes publish_id (W-4)."""
        from secbaas.community.core.service.paas import (
            DeviceFacadeException,
            ErrorCode,
            PaasError,
        )

        mock_publish_service.get_publish_bot_uuid.return_value = "bot-uuid-local"
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
                "/api/v1/publishes/42/start-progress?tenant=test_tenant",
            )

        assert resp.status_code == 504
        detail = resp.json()["detail"]
        assert detail["error"] == "COMMAND_TIMEOUT"
        assert detail["publish_id"] == 42

    @pytest.mark.asyncio
    async def test_facade_unknown_code_500_publish_id(
        self, mock_dispatcher, mock_publish_service
    ):
        """DeviceFacadeException unlisted ErrorCode → 500, detail includes publish_id (W-4)."""
        from secbaas.community.core.service.paas import (
            DeviceFacadeException,
            ErrorCode,
            PaasError,
        )

        mock_publish_service.get_publish_bot_uuid.return_value = "bot-uuid-local"
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
                "/api/v1/publishes/42/start-progress?tenant=test_tenant",
            )

        assert resp.status_code == 500
        detail = resp.json()["detail"]
        assert detail["error"] == "DEVICE_CREATION_FAILED"
        assert detail["publish_id"] == 42

    @pytest.mark.asyncio
    async def test_catchall_500_publish_id(self, mock_dispatcher, mock_publish_service):
        """Catch-all Exception → 500 INTERNAL_ERROR, detail includes publish_id (W-4)."""
        mock_publish_service.get_publish_bot_uuid.return_value = "bot-uuid-boom"
        mock_dispatcher.dispatch_bot_fetch_start_progress.side_effect = RuntimeError(
            "Unexpected failure"
        )

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                "/api/v1/publishes/42/start-progress?tenant=test_tenant",
            )

        assert resp.status_code == 500
        detail = resp.json()["detail"]
        assert detail["error"] == "INTERNAL_ERROR"
        assert detail["publish_id"] == 42

    @pytest.mark.asyncio
    async def test_facade_platform_error_501_publish_id(
        self, mock_dispatcher, mock_publish_service
    ):
        """DeviceFacadeException PLATFORM_ERROR → 501, detail includes publish_id (W-4)."""
        from secbaas.community.core.service.paas import (
            DeviceFacadeException,
            ErrorCode,
            PaasError,
        )

        mock_publish_service.get_publish_bot_uuid.return_value = "bot-uuid-arca"
        mock_dispatcher.dispatch_bot_fetch_start_progress.side_effect = (
            DeviceFacadeException(
                operation="fetch_start_progress",
                platform_type="ARCA",
                template_id=42,
                paas_device_id="container--machine--user",
                original_error=PaasError(
                    ErrorCode.PLATFORM_ERROR, "not supported on ARCA"
                ),
            )
        )

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                "/api/v1/publishes/42/start-progress?tenant=test_tenant",
            )

        assert resp.status_code == 501
        detail = resp.json()["detail"]
        assert detail["error"] == "NOT_IMPLEMENTED"
        assert detail["publish_id"] == 42
