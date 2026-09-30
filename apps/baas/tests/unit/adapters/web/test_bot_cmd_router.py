# mypy: disable-error-code="arg-type"
"""Unit tests for bot_cmd_router endpoints.

Tests the command execution router including:
- Successful command execution (POST)
- Bot not found (404)
- No devices found (404)
- No active devices (503)
- Device facade error (500)
- Internal server error (500)
- Device affinity parameter passthrough
- Custom timeout and env passthrough
"""

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from secbaas.community.adapters.web.routers.bot_service.cmd_router import router
from secbaas.community.api.device_manage import CommandResult
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
    """Override the Provide dependency to return a mock BotCmdDispatcher."""
    mock_instance = AsyncMock()
    old_overrides = dict(app.dependency_overrides)
    for route in iter_api_routes(app):
        for dep in route.dependant.dependencies:
            if dep.name == "dispatcher":
                app.dependency_overrides[dep.call] = lambda: mock_instance
    yield mock_instance
    app.dependency_overrides = old_overrides


@pytest.mark.asyncio
async def test_execute_command_success(mock_dispatcher):
    """Successful command execution returns 200 with CommandResult."""
    mock_dispatcher.dispatch_bot_execute_command.return_value = CommandResult(
        exit_code=0,
        stdout="deploy complete",
        stderr="",
        execution_time_ms=1500,
        command="echo hello",
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/bots/test_tenant/bot-uuid-001/execute-command",
            json={"cmd": "echo hello"},
        )

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["exit_code"] == 0
    assert data["stdout"] == "deploy complete"
    assert data["stderr"] == ""

    mock_dispatcher.dispatch_bot_execute_command.assert_awaited_once()
    call_kwargs = mock_dispatcher.dispatch_bot_execute_command.call_args.kwargs
    assert call_kwargs["bot_uuid"] == "bot-uuid-001"
    assert call_kwargs["cmd"] == "echo hello"
    assert call_kwargs["tenant"] == "test_tenant"
    assert call_kwargs["timeout_seconds"] == 30
    assert call_kwargs["device_affinity"] is None


@pytest.mark.asyncio
async def test_execute_command_with_env_and_timeout(mock_dispatcher):
    """Custom env and timeout_seconds are passed through."""
    mock_dispatcher.dispatch_bot_execute_command.return_value = CommandResult(
        exit_code=0,
        stdout="",
        stderr="",
        execution_time_ms=500,
        command="deploy.sh",
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/bots/test_tenant/bot-uuid-001/execute-command",
            json={
                "cmd": "deploy.sh",
                "env": {"MODE": "prod"},
                "timeout_seconds": 60,
            },
        )

    assert resp.status_code == 200
    call_kwargs = mock_dispatcher.dispatch_bot_execute_command.call_args.kwargs
    assert call_kwargs["cmd"] == "deploy.sh"
    assert call_kwargs["cmd_env"] == {"MODE": "prod"}
    assert call_kwargs["timeout_seconds"] == 60


@pytest.mark.asyncio
async def test_execute_command_with_affinity(mock_dispatcher):
    """device_affinity query parameter is passed through to the service."""
    mock_dispatcher.dispatch_bot_execute_command.return_value = CommandResult(
        exit_code=0,
        stdout="ok",
        stderr="",
        execution_time_ms=100,
        command="test",
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/bots/test_tenant/bot-uuid-001/execute-command"
            "?device_affinity=session-abc",
            json={"cmd": "test"},
        )

    assert resp.status_code == 200
    call_kwargs = mock_dispatcher.dispatch_bot_execute_command.call_args.kwargs
    assert call_kwargs["device_affinity"] == "session-abc"


@pytest.mark.asyncio
async def test_execute_command_affinity_defaults_to_none(mock_dispatcher):
    """device_affinity is optional and defaults to None."""
    mock_dispatcher.dispatch_bot_execute_command.return_value = CommandResult(
        exit_code=0,
        stdout="",
        stderr="",
        execution_time_ms=100,
        command="test",
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/bots/test_tenant/bot-uuid-001/execute-command",
            json={"cmd": "test"},
        )

    assert resp.status_code == 200
    call_kwargs = mock_dispatcher.dispatch_bot_execute_command.call_args.kwargs
    assert call_kwargs["device_affinity"] is None


@pytest.mark.asyncio
async def test_execute_command_bot_not_found_404(mock_dispatcher):
    """BotNotFoundError returns 404."""
    from secbaas.community.api.bot_runtime import BotNotFoundError

    mock_dispatcher.dispatch_bot_execute_command.side_effect = BotNotFoundError(
        "Bot not found: nonexistent"
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/bots/test_tenant/nonexistent/execute-command",
            json={"cmd": "test"},
        )

    assert resp.status_code == 404
    assert resp.json()["detail"]["error"] == "BOT_NOT_FOUND"


@pytest.mark.asyncio
async def test_execute_command_no_devices_found_404(mock_dispatcher):
    """NoDevicesFoundError returns 404."""
    from secbaas.community.api.bot_runtime import NoDevicesFoundError

    mock_dispatcher.dispatch_bot_execute_command.side_effect = NoDevicesFoundError(
        "No devices found"
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/bots/test_tenant/bot-uuid-001/execute-command",
            json={"cmd": "test"},
        )

    assert resp.status_code == 404
    assert resp.json()["detail"]["error"] == "NO_DEVICES_FOUND"


@pytest.mark.asyncio
async def test_execute_command_no_active_devices_503(mock_dispatcher):
    """NoActiveDevicesError returns 503."""
    from secbaas.community.api.bot_runtime import NoActiveDevicesError

    mock_dispatcher.dispatch_bot_execute_command.side_effect = NoActiveDevicesError(
        "No active devices"
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/bots/test_tenant/bot-uuid-001/execute-command",
            json={"cmd": "test"},
        )

    assert resp.status_code == 503
    assert resp.json()["detail"]["error"] == "NO_ACTIVE_DEVICES"


@pytest.mark.asyncio
async def test_execute_command_facade_error_500(mock_dispatcher):
    """DeviceFacadeException returns 500 with error details."""
    mock_dispatcher.dispatch_bot_execute_command.side_effect = DeviceFacadeException(
        operation="execute_command",
        platform_type="LOCAL",
        template_id=42,
        paas_device_id="container--machine--user",
        original_error=PaasError(ErrorCode.COMMAND_FAILED, "Command failed"),
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/bots/test_tenant/bot-uuid-001/execute-command",
            json={"cmd": "test"},
        )

    assert resp.status_code == 500
    detail = resp.json()["detail"]
    assert detail["error"] == "COMMAND_FAILED"


@pytest.mark.asyncio
async def test_execute_command_internal_error_500(mock_dispatcher):
    """Unexpected exception returns 500 with INTERNAL_ERROR."""
    mock_dispatcher.dispatch_bot_execute_command.side_effect = RuntimeError(
        "Unexpected failure"
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/bots/test_tenant/bot-uuid-001/execute-command",
            json={"cmd": "test"},
        )

    assert resp.status_code == 500
    assert resp.json()["detail"]["error"] == "INTERNAL_ERROR"


@pytest.mark.asyncio
async def test_execute_command_facade_timeout_error_500(mock_dispatcher):
    """DeviceFacadeException with COMMAND_TIMEOUT returns 500 with timeout error code."""
    mock_dispatcher.dispatch_bot_execute_command.side_effect = DeviceFacadeException(
        operation="execute_command",
        platform_type="LOCAL",
        template_id=42,
        paas_device_id="container--machine--user",
        original_error=PaasError(ErrorCode.COMMAND_TIMEOUT, "Command timed out"),
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/bots/test_tenant/bot-uuid-001/execute-command",
            json={"cmd": "sleep 100", "timeout_seconds": 5},
        )

    assert resp.status_code == 500
    assert resp.json()["detail"]["error"] == "COMMAND_TIMEOUT"


@pytest.mark.asyncio
async def test_execute_command_facade_device_not_found_error(mock_dispatcher):
    """DeviceFacadeException with DEVICE_NOT_FOUND returns 500 with error code."""
    mock_dispatcher.dispatch_bot_execute_command.side_effect = DeviceFacadeException(
        operation="execute_command",
        platform_type="LOCAL",
        template_id=42,
        paas_device_id="container--machine--user",
        original_error=PaasError(ErrorCode.DEVICE_NOT_FOUND, "Device not found"),
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/bots/test_tenant/bot-uuid-001/execute-command",
            json={"cmd": "test"},
        )

    assert resp.status_code == 500
    assert resp.json()["detail"]["error"] == "DEVICE_NOT_FOUND"


@pytest.mark.asyncio
async def test_execute_command_empty_cmd_returns_422(mock_dispatcher):
    """Empty cmd string returns validation error (422)."""

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/bots/test_tenant/bot-uuid-001/execute-command",
            json={"cmd": ""},
        )

    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_execute_command_timeout_exceeds_max_returns_422(mock_dispatcher):
    """timeout_seconds > 300 returns validation error (422)."""

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/bots/test_tenant/bot-uuid-001/execute-command",
            json={"cmd": "test", "timeout_seconds": 999},
        )

    assert resp.status_code == 422
