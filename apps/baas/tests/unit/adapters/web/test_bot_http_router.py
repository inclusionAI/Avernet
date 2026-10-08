# mypy: disable-error-code="arg-type"
"""Unit tests for bot_http_router endpoints.

Tests the HTTP invocation router including:
- Successful HTTP proxy invocation (GET/POST/PUT/DELETE)
- Bot not found (404)
- No devices found (404)
- No active devices (503)
- Device facade error (500)
- Device affinity parameter passthrough
- Hop-by-hop header filtering
"""

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from secbaas.community.adapters.web.routers.bot_service.http_router import router
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
    """Override the Provide dependency to return a mock BotHttpDispatcher."""
    mock_instance = AsyncMock()
    old_overrides = dict(app.dependency_overrides)
    for route in iter_api_routes(app):
        for dep in route.dependant.dependencies:
            if dep.name == "dispatcher":
                app.dependency_overrides[dep.call] = lambda: mock_instance
    yield mock_instance
    app.dependency_overrides = old_overrides


@pytest.mark.asyncio
async def test_http_invoke_get_success(mock_dispatcher):
    """Successful GET proxy returns 200 with response data."""
    mock_dispatcher.dispatch_bot_http_invoke.return_value = {
        "status_code": 200,
        "headers": {"content-type": "application/json"},
        "body": "eyJzdGF0dXMiOiAib2sifQ==",
    }

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            "/api/v1/bots/test_tenant/bot-uuid-001/invoke-http/8080/api/health",
        )

    assert resp.status_code == 200
    assert resp.headers.get("content-type") == "application/json"
    assert resp.json() == {"status": "ok"}

    mock_dispatcher.dispatch_bot_http_invoke.assert_awaited_once()
    call_kwargs = mock_dispatcher.dispatch_bot_http_invoke.call_args.kwargs
    assert call_kwargs["bot_uuid"] == "bot-uuid-001"
    assert call_kwargs["method"] == "GET"
    assert call_kwargs["port"] == 8080
    assert call_kwargs["path"] == "/api/health"
    assert call_kwargs["tenant"] == "test_tenant"


@pytest.mark.asyncio
async def test_http_invoke_post_with_body(mock_dispatcher):
    """Successful POST with body returns 200."""
    mock_dispatcher.dispatch_bot_http_invoke.return_value = {
        "status_code": 200,
        "headers": {"content-type": "application/json"},
        "body": "eyJpZCI6IDEyM30=",
    }

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/bots/test_tenant/bot-uuid-001/invoke-http/443/api/command",
            content=b'{"action": "deploy"}',
            headers={"content-type": "application/json"},
        )

    assert resp.status_code == 200
    assert resp.json() == {"id": 123}

    call_kwargs = mock_dispatcher.dispatch_bot_http_invoke.call_args.kwargs
    assert call_kwargs["method"] == "POST"
    assert call_kwargs["port"] == 443
    assert call_kwargs["path"] == "/api/command"
    assert call_kwargs["body"] == b'{"action": "deploy"}'
    assert call_kwargs["tenant"] == "test_tenant"


@pytest.mark.asyncio
async def test_http_invoke_put(mock_dispatcher):
    """PUT method is supported."""
    mock_dispatcher.dispatch_bot_http_invoke.return_value = {
        "status_code": 200,
        "headers": {},
        "body": "",
    }

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.put(
            "/api/v1/bots/test_tenant/bot-uuid-001/invoke-http/8080/api/config",
            content=b'{"key": "value"}',
        )

    assert resp.status_code == 200
    call_kwargs = mock_dispatcher.dispatch_bot_http_invoke.call_args.kwargs
    assert call_kwargs["method"] == "PUT"
    assert call_kwargs["tenant"] == "test_tenant"


@pytest.mark.asyncio
async def test_http_invoke_delete(mock_dispatcher):
    """DELETE method is supported."""
    mock_dispatcher.dispatch_bot_http_invoke.return_value = {
        "status_code": 204,
        "headers": {},
        "body": "",
    }

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.delete(
            "/api/v1/bots/test_tenant/bot-uuid-001/invoke-http/8080/api/resource/42",
        )

    assert resp.status_code == 204
    call_kwargs = mock_dispatcher.dispatch_bot_http_invoke.call_args.kwargs
    assert call_kwargs["method"] == "DELETE"
    assert call_kwargs["tenant"] == "test_tenant"


@pytest.mark.asyncio
async def test_http_invoke_with_affinity(mock_dispatcher):
    """device_affinity query parameter is passed through to the service."""
    mock_dispatcher.dispatch_bot_http_invoke.return_value = {
        "status_code": 200,
        "headers": {},
        "body": "",
    }

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            "/api/v1/bots/test_tenant/bot-uuid-001/invoke-http/8080/api/test"
            "?device_affinity=session-abc",
            headers={"x-custom": "value"},
        )

    assert resp.status_code == 200
    call_kwargs = mock_dispatcher.dispatch_bot_http_invoke.call_args.kwargs
    assert call_kwargs["bot_uuid"] == "bot-uuid-001"
    assert call_kwargs["device_affinity"] == "session-abc"
    assert call_kwargs["tenant"] == "test_tenant"
    assert call_kwargs["headers"].get("x-custom") == "value"


@pytest.mark.asyncio
async def test_http_invoke_bot_not_found_404(mock_dispatcher):
    """BotNotFoundError returns 404."""
    from secbaas.community.api.bot_runtime import BotNotFoundError

    mock_dispatcher.dispatch_bot_http_invoke.side_effect = BotNotFoundError(
        "Bot not found: nonexistent"
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            "/api/v1/bots/test_tenant/nonexistent/invoke-http/8080/api/test",
        )

    assert resp.status_code == 404
    assert resp.json()["detail"]["error"] == "BOT_NOT_FOUND"


@pytest.mark.asyncio
async def test_http_invoke_no_devices_found_404(mock_dispatcher):
    """NoDevicesFoundError returns 404."""
    from secbaas.community.api.bot_runtime import NoDevicesFoundError

    mock_dispatcher.dispatch_bot_http_invoke.side_effect = NoDevicesFoundError(
        "No devices found"
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            "/api/v1/bots/test_tenant/bot-uuid-001/invoke-http/8080/api/test",
        )

    assert resp.status_code == 404
    assert resp.json()["detail"]["error"] == "NO_DEVICES_FOUND"


@pytest.mark.asyncio
async def test_http_invoke_no_active_devices_503(mock_dispatcher):
    """NoActiveDevicesError returns 503."""
    from secbaas.community.api.bot_runtime import NoActiveDevicesError

    mock_dispatcher.dispatch_bot_http_invoke.side_effect = NoActiveDevicesError(
        "No active devices"
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            "/api/v1/bots/test_tenant/bot-uuid-001/invoke-http/8080/api/test",
        )

    assert resp.status_code == 503
    assert resp.json()["detail"]["error"] == "NO_ACTIVE_DEVICES"


@pytest.mark.asyncio
async def test_http_invoke_facade_error_500(mock_dispatcher):
    """DeviceFacadeException returns 500 with error details."""
    mock_dispatcher.dispatch_bot_http_invoke.side_effect = DeviceFacadeException(
        operation="invoke_http_in_device",
        platform_type="LOCAL",
        template_id=42,
        paas_device_id="container--machine--user",
        original_error=PaasError(ErrorCode.DEVICE_NOT_FOUND, "Device not found"),
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            "/api/v1/bots/test_tenant/bot-uuid-001/invoke-http/8080/api/test",
        )

    assert resp.status_code == 500
    detail = resp.json()["detail"]
    assert detail["error"] == "DEVICE_NOT_FOUND"


@pytest.mark.asyncio
async def test_http_invoke_path_without_leading_slash(mock_dispatcher):
    """Path without leading slash is normalized."""
    mock_dispatcher.dispatch_bot_http_invoke.return_value = {
        "status_code": 200,
        "headers": {},
        "body": "",
    }

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            "/api/v1/bots/test_tenant/bot-uuid-001/invoke-http/8080/api/test",
        )

    assert resp.status_code == 200
    call_kwargs = mock_dispatcher.dispatch_bot_http_invoke.call_args.kwargs
    assert call_kwargs["path"] == "/api/test"


@pytest.mark.asyncio
async def test_http_invoke_with_query_string(mock_dispatcher):
    """Query string from URL is passed to the service."""
    mock_dispatcher.dispatch_bot_http_invoke.return_value = {
        "status_code": 200,
        "headers": {},
        "body": "",
    }

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            "/api/v1/bots/test_tenant/bot-uuid-001/invoke-http/8080/api/data"
            "?foo=bar&baz=1",
        )

    assert resp.status_code == 200
    call_kwargs = mock_dispatcher.dispatch_bot_http_invoke.call_args.kwargs
    assert "foo=bar" in call_kwargs["query_string"]
    assert "baz=1" in call_kwargs["query_string"]


@pytest.mark.asyncio
async def test_http_invoke_allows_missing_device_affinity(mock_dispatcher):
    """device_affinity is optional and defaults to None."""
    mock_dispatcher.dispatch_bot_http_invoke.return_value = {
        "status_code": 200,
        "headers": {},
        "body": "",
    }

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            "/api/v1/bots/test_tenant/bot-uuid-001/invoke-http/8080/api/test",
        )

    assert resp.status_code == 200
    call_kwargs = mock_dispatcher.dispatch_bot_http_invoke.call_args.kwargs
    assert call_kwargs["device_affinity"] is None


# ============================================================================
# Generic Exception Handler Tests
# ============================================================================


@pytest.mark.asyncio
async def test_http_invoke_generic_exception_500(mock_dispatcher):
    """Catch-all Exception returns 500 with INTERNAL_ERROR detail."""
    mock_dispatcher.dispatch_bot_http_invoke.side_effect = ValueError(
        "Something completely unexpected happened"
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            "/api/v1/bots/test_tenant/bot-uuid-001/invoke-http/8080/api/test",
        )

    assert resp.status_code == 500
    detail = resp.json()["detail"]
    assert detail["error"] == "INTERNAL_ERROR"
    assert "Something completely unexpected happened" in detail["message"]
    assert detail["bot_uuid"] == "bot-uuid-001"


# ============================================================================
# DeviceFacadeException without original_error — uses fallback error code
# ============================================================================


@pytest.mark.asyncio
async def test_http_invoke_facade_error_no_original_error(mock_dispatcher):
    """DeviceFacadeException with original_error=None falls back to FACADE_ERROR."""
    facade_exc = DeviceFacadeException(
        operation="invoke_http_in_device",
        platform_type="LOCAL",
        template_id=42,
        paas_device_id=None,
        original_error=PaasError(ErrorCode.DEVICE_NOT_FOUND, "Device not found"),
    )
    # Simulate edge-case where original_error is cleared after construction
    facade_exc.original_error = None
    mock_dispatcher.dispatch_bot_http_invoke.side_effect = facade_exc

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            "/api/v1/bots/test_tenant/bot-uuid-001/invoke-http/8080/api/test",
        )

    assert resp.status_code == 500
    detail = resp.json()["detail"]
    assert detail["error"] == "FACADE_ERROR"
