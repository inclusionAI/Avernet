# mypy: disable-error-code="arg-type"
"""Unit tests for bot_http_conn_router endpoint.

Tests the HTTP connection info resolver endpoint including:
- Successful http-info resolution
- Bot not found (404)
- No devices found (404)
- No active devices (503)
- Device facade error with original_error (500)
- Device facade error without original_error (500)
- Generic/unexpected exception (500)
- Device affinity parameter passthrough
- Optional device_affinity defaults to None
- Response model field validation
"""

from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from secbaas.community.adapters.web.routers.bot_service.http_conn_router import router
from secbaas.community.api.bot_runtime import (
    BotNotFoundError,
    HttpConnectionInfo,
    NoActiveDevicesError,
    NoDevicesFoundError,
)
from secbaas.community.core.service.paas import (
    DeviceFacadeException,
    ErrorCode,
    PaasError,
)
from tests.unit.adapters.web.conftest import iter_api_routes

app = FastAPI()
app.include_router(router)


@pytest.fixture(autouse=True)
def mock_dispatcher():
    """Override DI dependency with a mock to prevent container resolution in tests."""
    mock_instance = AsyncMock()
    old_overrides = dict(app.dependency_overrides)
    for route in iter_api_routes(app):
        for dep in route.dependant.dependencies:
            if dep.name == "dispatcher":
                app.dependency_overrides[dep.call] = lambda: mock_instance
    yield mock_instance
    app.dependency_overrides = old_overrides


# ============================================================================
# Success path
# ============================================================================


@pytest.mark.asyncio
async def test_get_http_info_success(mock_dispatcher):
    mock_dispatcher.dispatch_bot_http_conn_info.return_value = HttpConnectionInfo(
        http_url="http://agentclawproxy-pre.service.test/proxypass/ARCA_sandbox123:8080/api/health",
        token="eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJib3QifQ.signature",
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            "/api/v1/bots/bot-uuid-001/http-info"
            "?port=8080&path=/api/health&tenant=test_tenant",
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 0
    assert body["message"] == "success"
    data = body["data"]
    assert (
        data["http_url"]
        == "http://agentclawproxy-pre.service.test/proxypass/ARCA_sandbox123:8080/api/health"
    )
    assert data["token"] == "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJib3QifQ.signature"

    mock_dispatcher.dispatch_bot_http_conn_info.assert_awaited_once()
    call_kwargs = mock_dispatcher.dispatch_bot_http_conn_info.call_args.kwargs
    assert call_kwargs["bot_uuid"] == "bot-uuid-001"
    assert call_kwargs["port"] == 8080
    assert call_kwargs["path"] == "/api/health"
    assert call_kwargs["tenant"] == "test_tenant"
    assert call_kwargs["device_affinity"] is None


@pytest.mark.asyncio
async def test_get_http_info_with_device_affinity(mock_dispatcher):
    mock_dispatcher.dispatch_bot_http_conn_info.return_value = HttpConnectionInfo(
        http_url="http://agentclawproxy-pre.service.test/proxypass/ARCA_sandbox456:443/api/health",
        token="token-abc",
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            "/api/v1/bots/bot-uuid-002/http-info"
            "?port=443&path=/health&tenant=test_tenant&device_affinity=session-xyz",
        )

    assert resp.status_code == 200
    call_kwargs = mock_dispatcher.dispatch_bot_http_conn_info.call_args.kwargs
    assert call_kwargs["bot_uuid"] == "bot-uuid-002"
    assert call_kwargs["device_affinity"] == "session-xyz"
    assert call_kwargs["tenant"] == "test_tenant"
    assert call_kwargs["port"] == 443
    assert call_kwargs["path"] == "/health"


@pytest.mark.asyncio
async def test_get_http_info_response_fields_complete(mock_dispatcher):
    mock_dispatcher.dispatch_bot_http_conn_info.return_value = HttpConnectionInfo(
        http_url="http://gw/proxypass/X:1/p",
        token="tok",
        target="TECLAW_b_abc@4:1",
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            "/api/v1/bots/bot-1/http-info?port=1&path=/p&tenant=t",
        )

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert set(data.keys()) == {"http_url", "token", "target"}
    assert isinstance(data["http_url"], str)
    assert isinstance(data["token"], str)
    assert data["target"] == "TECLAW_b_abc@4:1"


@pytest.mark.asyncio
async def test_get_http_info_different_ports(mock_dispatcher):
    mock_dispatcher.dispatch_bot_http_conn_info.return_value = HttpConnectionInfo(
        http_url="http://gw/proxypass/T:65535/p",
        token="tok",
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            "/api/v1/bots/bot-1/http-info?port=65535&path=/p&tenant=t",
        )

    assert resp.status_code == 200
    call_kwargs = mock_dispatcher.dispatch_bot_http_conn_info.call_args.kwargs
    assert call_kwargs["port"] == 65535


# ============================================================================
# Error paths
# ============================================================================


@pytest.mark.asyncio
async def test_get_http_info_bot_not_found_404(mock_dispatcher):
    mock_dispatcher.dispatch_bot_http_conn_info.side_effect = BotNotFoundError(
        "Bot not found: nonexistent"
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            "/api/v1/bots/nonexistent/http-info"
            "?port=8080&path=/api/health&tenant=test_tenant",
        )

    assert resp.status_code == 404
    detail = resp.json()["detail"]
    assert detail["error"] == "BOT_NOT_FOUND"
    assert detail["bot_uuid"] == "nonexistent"


@pytest.mark.asyncio
async def test_get_http_info_no_devices_found_404(mock_dispatcher):
    mock_dispatcher.dispatch_bot_http_conn_info.side_effect = NoDevicesFoundError(
        "No devices found for bot"
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            "/api/v1/bots/bot-uuid-001/http-info"
            "?port=8080&path=/api/health&tenant=test_tenant",
        )

    assert resp.status_code == 404
    detail = resp.json()["detail"]
    assert detail["error"] == "NO_DEVICES_FOUND"
    assert detail["bot_uuid"] == "bot-uuid-001"


@pytest.mark.asyncio
async def test_get_http_info_no_active_devices_404(mock_dispatcher):
    mock_dispatcher.dispatch_bot_http_conn_info.side_effect = NoActiveDevicesError(
        "No active devices available"
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            "/api/v1/bots/bot-uuid-001/http-info"
            "?port=8080&path=/api/health&tenant=test_tenant",
        )

    assert resp.status_code == 404
    detail = resp.json()["detail"]
    assert detail["error"] == "NO_ACTIVE_DEVICES"
    assert detail["bot_uuid"] == "bot-uuid-001"


@pytest.mark.asyncio
async def test_get_http_info_facade_error_with_original_error_500(mock_dispatcher):
    mock_dispatcher.dispatch_bot_http_conn_info.side_effect = DeviceFacadeException(
        operation="get_device_http_connection_info",
        platform_type="ARCA",
        template_id=42,
        paas_device_id="sandbox-abc",
        original_error=PaasError(
            ErrorCode.DEVICE_NOT_FOUND, "Sandbox not found on platform"
        ),
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            "/api/v1/bots/bot-uuid-001/http-info"
            "?port=8080&path=/api/health&tenant=test_tenant",
        )

    assert resp.status_code == 500
    detail = resp.json()["detail"]
    assert detail["error"] == "DEVICE_NOT_FOUND"
    assert "Facade operation failed" in detail["message"]
    context = detail["context"]
    assert context["operation"] == "get_device_http_connection_info"
    assert context["platform_type"] == "ARCA"
    assert context["paas_device_id"] == "sandbox-abc"


@pytest.mark.asyncio
async def test_get_http_info_facade_error_without_original_error_500(mock_dispatcher):
    facade_exc = DeviceFacadeException(
        operation="get_device_http_connection_info",
        platform_type="ARCA",
        template_id=42,
        paas_device_id=None,
        original_error=PaasError(ErrorCode.PLATFORM_UNAVAILABLE, "Platform down"),
    )
    facade_exc.original_error = None  # type: ignore[assignment]
    mock_dispatcher.dispatch_bot_http_conn_info.side_effect = facade_exc

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            "/api/v1/bots/bot-uuid-001/http-info"
            "?port=8080&path=/api/health&tenant=test_tenant",
        )

    assert resp.status_code == 500
    detail = resp.json()["detail"]
    assert detail["error"] == "FACADE_ERROR"


@pytest.mark.asyncio
async def test_get_http_info_generic_exception_500(mock_dispatcher):
    mock_dispatcher.dispatch_bot_http_conn_info.side_effect = RuntimeError(
        "Unexpected database connection failure"
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            "/api/v1/bots/bot-uuid-001/http-info"
            "?port=8080&path=/api/health&tenant=test_tenant",
        )

    assert resp.status_code == 500
    detail = resp.json()["detail"]
    assert detail["error"] == "INTERNAL_ERROR"
    assert "Unexpected database connection failure" in detail["message"]
    assert detail["bot_uuid"] == "bot-uuid-001"


# ============================================================================
# Parameter validation
# ============================================================================


@pytest.mark.asyncio
async def test_get_http_info_port_below_range_returns_422():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            "/api/v1/bots/bot-1/http-info?port=0&path=/p&tenant=t",
        )

    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_get_http_info_port_above_range_returns_422():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            "/api/v1/bots/bot-1/http-info?port=65536&path=/p&tenant=t",
        )

    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_get_http_info_missing_required_params_returns_422():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/v1/bots/bot-1/http-info")

    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_get_http_info_optional_device_affinity_omitted(mock_dispatcher):
    mock_dispatcher.dispatch_bot_http_conn_info.return_value = HttpConnectionInfo(
        http_url="http://gw/proxypass/T:80/p",
        token="tok",
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            "/api/v1/bots/bot-1/http-info?port=80&path=/p&tenant=t",
        )

    assert resp.status_code == 200
    call_kwargs = mock_dispatcher.dispatch_bot_http_conn_info.call_args.kwargs
    assert call_kwargs["device_affinity"] is None


# ============================================================================
# Edge cases
# ============================================================================


@pytest.mark.asyncio
async def test_get_http_info_handles_empty_path(mock_dispatcher):
    mock_dispatcher.dispatch_bot_http_conn_info.return_value = HttpConnectionInfo(
        http_url="http://gw/proxypass/T:80/",
        token="tok",
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            "/api/v1/bots/bot-1/http-info?port=80&path=&tenant=t",
        )

    assert resp.status_code == 200
    call_kwargs = mock_dispatcher.dispatch_bot_http_conn_info.call_args.kwargs
    assert call_kwargs["path"] == ""


@pytest.mark.asyncio
async def test_get_http_info_handles_special_chars_in_path(mock_dispatcher):
    mock_dispatcher.dispatch_bot_http_conn_info.return_value = HttpConnectionInfo(
        http_url="http://gw/proxypass/T:80/api/health",
        token="tok",
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            "/api/v1/bots/bot-1/http-info?port=80&path=/api/health&tenant=t",
        )

    assert resp.status_code == 200
    call_kwargs = mock_dispatcher.dispatch_bot_http_conn_info.call_args.kwargs
    assert call_kwargs["path"] == "/api/health"


# ============================================================================
# Device UUID targeting
# ============================================================================


@pytest.mark.asyncio
async def test_get_http_info_with_device_uuid(mock_dispatcher):
    mock_dispatcher.dispatch_bot_http_conn_info.return_value = HttpConnectionInfo(
        http_url="http://gw/proxypass/T:8080/p",
        token="tok",
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            "/api/v1/bots/bot-1/http-info"
            "?port=8080&path=/api/health&tenant=t&device_uuid=device-xyz",
        )

    assert resp.status_code == 200
    call_kwargs = mock_dispatcher.dispatch_bot_http_conn_info.call_args.kwargs
    assert call_kwargs["device_uuid"] == "device-xyz"


@pytest.mark.asyncio
async def test_get_http_info_device_uuid_optional_omitted(mock_dispatcher):
    mock_dispatcher.dispatch_bot_http_conn_info.return_value = HttpConnectionInfo(
        http_url="http://gw/proxypass/T:80/p",
        token="tok",
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            "/api/v1/bots/bot-1/http-info?port=80&path=/p&tenant=t",
        )

    assert resp.status_code == 200
    call_kwargs = mock_dispatcher.dispatch_bot_http_conn_info.call_args.kwargs
    assert call_kwargs["device_uuid"] is None
