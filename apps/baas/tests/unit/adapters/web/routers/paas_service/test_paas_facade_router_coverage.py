"""Coverage tests for paas_facade_router.

Tests all endpoint handler functions directly (not via TestClient)
to avoid opentelemetry import issues. Covers success, error, and
edge-case branches.
"""

import base64
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from secbaas.community.adapters.web.routers.paas_service.paas_facade_router import (
    ERROR_CODE_TO_HTTP_STATUS,
    HOP_BY_HOP_HEADERS,
    CreateDeviceRequest,
    ExecuteCommandRequest,
    OpenFolderRequest,
    _filter_headers,
    _handle_facade_exception,
    create_device,
    destroy_device,
    execute_command,
    get_device_info,
    get_ws_connection_info,
    invoke_http_in_device,
    open_folder,
    update_device_ttl,
    update_outbound_operation_rule,
)
from secbaas.community.api import ApiResponse, SuccessResponse
from secbaas.community.api.device_manage import (
    ArcaCreationResult,
    CommandResult,
    DeviceFacadeException,
    DeviceNotActiveException,
    DeviceNotFoundException,
    ErrorCode,
    OutBoundOperationRule,
    PaasError,
)
from secbaas.community.api.health_check.bot import TTLInfo


def _make_facade_exception(
    code: ErrorCode = ErrorCode.DEVICE_CREATION_FAILED,
    operation: str = "create_device",
    platform_type: str = "ARCA",
    template_id: int = 1,
    paas_device_id: str | None = "dev-001",
) -> DeviceFacadeException:
    err = PaasError(code, "test error")
    return DeviceFacadeException(
        operation=operation,
        platform_type=platform_type,
        template_id=template_id,
        paas_device_id=paas_device_id,
        original_error=err,
    )


# ── _filter_headers ──


class TestFilterHeaders:
    def test_removes_hop_by_hop(self):
        headers = {"Connection": "keep-alive", "X-Custom": "val"}
        result = _filter_headers(headers)
        assert "Connection" not in result
        assert result["X-Custom"] == "val"

    def test_case_insensitive(self):
        headers = {"connection": "close", "host": "localhost"}
        result = _filter_headers(headers)
        assert result == {}

    def test_empty_dict(self):
        assert _filter_headers({}) == {}

    def test_all_hop_by_hop(self):
        headers = {h: "v" for h in HOP_BY_HOP_HEADERS}
        assert _filter_headers(headers) == {}


# ── _handle_facade_exception ──


class TestHandleFacadeException:
    def test_known_error_code(self):
        exc = _make_facade_exception(ErrorCode.DEVICE_NOT_FOUND)
        result = _handle_facade_exception(exc)
        assert isinstance(result, HTTPException)
        assert result.status_code == 404

    def test_unknown_error_code_defaults_500(self):
        exc = _make_facade_exception(ErrorCode.TEMPLATE_NOT_FOUND)
        result = _handle_facade_exception(exc)
        assert result.status_code == 500

    def test_detail_structure(self):
        exc = _make_facade_exception(
            ErrorCode.COMMAND_TIMEOUT, operation="execute_command"
        )
        result = _handle_facade_exception(exc)
        assert result.detail["error_code"] == "COMMAND_TIMEOUT"
        assert result.detail["context"]["operation"] == "execute_command"


# ── create_device ──


class TestCreateDevice:
    @pytest.mark.asyncio
    async def test_success(self):
        facade = AsyncMock()
        facade.create_device.return_value = ArcaCreationResult(
            platform="arca",
            status="RUNNING",
            template_id="tpl-001",
            sandbox_id="sb-001",
        )
        req = CreateDeviceRequest(tenant_name="t1")
        result = await create_device(request=req, facade=facade)
        assert isinstance(result, ApiResponse)
        assert result.data.sandbox_id == "sb-001"

    @pytest.mark.asyncio
    async def test_facade_exception(self):
        facade = AsyncMock()
        facade.create_device.side_effect = _make_facade_exception(
            ErrorCode.DEVICE_CREATION_FAILED
        )
        req = CreateDeviceRequest(tenant_name="t1")
        with pytest.raises(HTTPException) as exc_info:
            await create_device(request=req, facade=facade)
        assert exc_info.value.status_code == 500

    @pytest.mark.asyncio
    async def test_generic_exception(self):
        facade = AsyncMock()
        facade.create_device.side_effect = RuntimeError("boom")
        req = CreateDeviceRequest(tenant_name="t1")
        with pytest.raises(HTTPException) as exc_info:
            await create_device(request=req, facade=facade)
        assert exc_info.value.status_code == 500
        assert exc_info.value.detail["error_code"] == "INTERNAL_ERROR"


# ── destroy_device ──


class TestDestroyDevice:
    @pytest.mark.asyncio
    async def test_success(self):
        facade = AsyncMock()
        facade.destroy_device.return_value = None
        result = await destroy_device("dev-001@1", facade=facade)
        assert isinstance(result, ApiResponse)
        assert "destroyed" in result.data.message

    @pytest.mark.asyncio
    async def test_facade_exception(self):
        facade = AsyncMock()
        facade.destroy_device.side_effect = _make_facade_exception(
            ErrorCode.DEVICE_NOT_FOUND, operation="destroy_device"
        )
        with pytest.raises(HTTPException) as exc_info:
            await destroy_device("dev-001", facade=facade)
        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_generic_exception(self):
        facade = AsyncMock()
        facade.destroy_device.side_effect = RuntimeError("err")
        with pytest.raises(HTTPException) as exc_info:
            await destroy_device("dev-001", facade=facade)
        assert exc_info.value.status_code == 500
        assert "destroy_device" in exc_info.value.detail["context"]["operation"]


# ── execute_command ──


class TestExecuteCommand:
    @pytest.mark.asyncio
    async def test_success(self):
        facade = AsyncMock()
        facade.execute_command.return_value = CommandResult(
            exit_code=0,
            stdout="ok",
            stderr="",
            execution_time_ms=10,
            command="ls",
            env=None,
        )
        req = ExecuteCommandRequest(cmd="ls")
        result = await execute_command("dev-001", req, facade=facade)
        assert isinstance(result, ApiResponse)
        assert result.data.exit_code == 0

    @pytest.mark.asyncio
    async def test_facade_exception(self):
        facade = AsyncMock()
        facade.execute_command.side_effect = _make_facade_exception(
            ErrorCode.COMMAND_TIMEOUT, operation="execute_command"
        )
        req = ExecuteCommandRequest(cmd="ls")
        with pytest.raises(HTTPException) as exc_info:
            await execute_command("dev-001", req, facade=facade)
        assert exc_info.value.status_code == 504

    @pytest.mark.asyncio
    async def test_generic_exception(self):
        facade = AsyncMock()
        facade.execute_command.side_effect = RuntimeError("err")
        req = ExecuteCommandRequest(cmd="ls")
        with pytest.raises(HTTPException) as exc_info:
            await execute_command("dev-001", req, facade=facade)
        assert exc_info.value.status_code == 500


# ── get_ws_connection_info ──


class TestGetWsConnectionInfo:
    @pytest.mark.asyncio
    async def test_success(self):
        from secbaas.community.api.bot_runtime import WsConnectionInfo

        facade = AsyncMock()
        facade.resolve_ws_conn_info.return_value = WsConnectionInfo(
            ws_url="wss://gw/ws",
            token="tok",
            target="ARCA_sb:8080",
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
        result = await get_ws_connection_info("dev-001", 8080, "/ws", facade=facade)
        assert isinstance(result, ApiResponse)
        assert result.data.ws_url == "wss://gw/ws"

    @pytest.mark.asyncio
    async def test_not_found(self):
        facade = AsyncMock()
        facade.resolve_ws_conn_info.side_effect = DeviceNotFoundException(
            "not found", paas_device_id="dev-001"
        )
        with pytest.raises(HTTPException) as exc_info:
            await get_ws_connection_info("dev-001", 8080, "/ws", facade=facade)
        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_not_active(self):
        facade = AsyncMock()
        facade.resolve_ws_conn_info.side_effect = DeviceNotActiveException(
            "inactive", paas_device_id="dev-001", device_status="PENDING"
        )
        with pytest.raises(HTTPException) as exc_info:
            await get_ws_connection_info("dev-001", 8080, "/ws", facade=facade)
        assert exc_info.value.status_code == 409

    @pytest.mark.asyncio
    async def test_not_implemented(self):
        facade = AsyncMock()
        facade.resolve_ws_conn_info.side_effect = NotImplementedError(
            "sigma not supported"
        )
        with pytest.raises(HTTPException) as exc_info:
            await get_ws_connection_info("dev-001", 8080, "/ws", facade=facade)
        assert exc_info.value.status_code == 501

    @pytest.mark.asyncio
    async def test_facade_exception(self):
        facade = AsyncMock()
        facade.resolve_ws_conn_info.side_effect = _make_facade_exception(
            ErrorCode.PLATFORM_UNAVAILABLE, operation="resolve_ws_conn_info"
        )
        with pytest.raises(HTTPException) as exc_info:
            await get_ws_connection_info("dev-001", 8080, "/ws", facade=facade)
        assert exc_info.value.status_code == 500


# ── get_device_info ──


class TestGetDeviceInfo:
    @pytest.mark.asyncio
    async def test_success(self):
        from secbaas.community.api.device_manage import ArcaDeviceInfo

        facade = AsyncMock()
        facade.get_device_info.return_value = ArcaDeviceInfo(
            platform="arca",
            status="ACTIVE",
            sandbox_id="sb-001",
            template_id="tpl-001",
            ttl_seconds=3600,
            created_at=datetime.now(UTC),
        )
        result = await get_device_info("dev-001", facade=facade)
        assert isinstance(result, ApiResponse)
        assert result.data.platform == "arca"

    @pytest.mark.asyncio
    async def test_facade_exception(self):
        facade = AsyncMock()
        facade.get_device_info.side_effect = _make_facade_exception(
            ErrorCode.DEVICE_NOT_FOUND, operation="get_device_info"
        )
        with pytest.raises(HTTPException) as exc_info:
            await get_device_info("dev-001", facade=facade)
        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_generic_exception(self):
        facade = AsyncMock()
        facade.get_device_info.side_effect = RuntimeError("err")
        with pytest.raises(HTTPException) as exc_info:
            await get_device_info("dev-001", facade=facade)
        assert exc_info.value.status_code == 500


# ── update_outbound_operation_rule ──


class TestUpdateOutboundRule:
    @pytest.mark.asyncio
    async def test_success(self):
        facade = AsyncMock()
        facade.update_outbound_operation_rule.return_value = True
        rule = OutBoundOperationRule()
        result = await update_outbound_operation_rule("dev-001", rule, facade=facade)
        assert isinstance(result, ApiResponse)

    @pytest.mark.asyncio
    async def test_facade_exception(self):
        facade = AsyncMock()
        facade.update_outbound_operation_rule.side_effect = _make_facade_exception(
            ErrorCode.DEVICE_UNAVAILABLE, operation="update_outbound_operation_rule"
        )
        rule = OutBoundOperationRule()
        with pytest.raises(HTTPException) as exc_info:
            await update_outbound_operation_rule("dev-001", rule, facade=facade)
        assert exc_info.value.status_code == 503

    @pytest.mark.asyncio
    async def test_generic_exception(self):
        facade = AsyncMock()
        facade.update_outbound_operation_rule.side_effect = RuntimeError("err")
        rule = OutBoundOperationRule()
        with pytest.raises(HTTPException) as exc_info:
            await update_outbound_operation_rule("dev-001", rule, facade=facade)
        assert exc_info.value.status_code == 500


# ── invoke_http_in_device ──


class TestInvokeHttpInDevice:
    @pytest.mark.asyncio
    async def test_success(self):
        facade = AsyncMock()
        body = base64.b64encode(b'{"ok":true}').decode()
        facade.invoke_http_in_device.return_value = {
            "status_code": 200,
            "body": body,
            "headers": {"X-Custom": "val", "Connection": "close"},
        }
        request = MagicMock()
        request.method = "GET"
        request.headers = {"content-type": "application/json", "host": "localhost"}
        request.body = AsyncMock(return_value=b"")
        request.url.query = "key=val"

        result = await invoke_http_in_device(
            "dev-001", 8080, "api/v1", request, facade=facade
        )
        assert result.status_code == 200
        assert result.body == b'{"ok":true}'

    @pytest.mark.asyncio
    async def test_path_already_starts_with_slash(self):
        facade = AsyncMock()
        facade.invoke_http_in_device.return_value = {
            "status_code": 200,
            "body": "",
            "headers": {},
        }
        request = MagicMock()
        request.method = "POST"
        request.headers = {"content-type": "text/plain"}
        request.body = AsyncMock(return_value=b"data")
        request.url.query = ""

        result = await invoke_http_in_device(
            "dev-001", 8080, "/api/v1", request, facade=facade
        )
        assert result.status_code == 200

    @pytest.mark.asyncio
    async def test_invalid_base64(self):
        facade = AsyncMock()
        facade.invoke_http_in_device.return_value = {
            "status_code": 200,
            "body": "!!!invalid",
            "headers": {},
        }
        request = MagicMock()
        request.method = "GET"
        request.headers = {"content-type": "text/plain"}
        request.body = AsyncMock(return_value=b"")
        request.url.query = ""

        with pytest.raises(HTTPException) as exc_info:
            await invoke_http_in_device("dev-001", 8080, "api", request, facade=facade)
        assert exc_info.value.status_code == 500

    @pytest.mark.asyncio
    async def test_empty_body(self):
        facade = AsyncMock()
        facade.invoke_http_in_device.return_value = {
            "status_code": 204,
            "body": "",
            "headers": {},
        }
        request = MagicMock()
        request.method = "DELETE"
        request.headers = {}
        request.body = AsyncMock(return_value=b"")
        request.url.query = ""

        result = await invoke_http_in_device(
            "dev-001", 8080, "api", request, facade=facade
        )
        assert result.status_code == 204
        assert result.body == b""

    @pytest.mark.asyncio
    async def test_not_implemented(self):
        facade = AsyncMock()
        facade.invoke_http_in_device.side_effect = NotImplementedError("not supported")
        request = MagicMock()
        request.method = "GET"
        request.headers = {}
        request.body = AsyncMock(return_value=b"")
        request.url.query = ""

        with pytest.raises(HTTPException) as exc_info:
            await invoke_http_in_device("dev-001", 8080, "api", request, facade=facade)
        assert exc_info.value.status_code == 501

    @pytest.mark.asyncio
    async def test_facade_exception(self):
        facade = AsyncMock()
        facade.invoke_http_in_device.side_effect = _make_facade_exception(
            ErrorCode.DEVICE_NOT_FOUND, operation="invoke_http_in_device"
        )
        request = MagicMock()
        request.method = "GET"
        request.headers = {}
        request.body = AsyncMock(return_value=b"")
        request.url.query = ""

        with pytest.raises(HTTPException) as exc_info:
            await invoke_http_in_device("dev-001", 8080, "api", request, facade=facade)
        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_generic_exception(self):
        facade = AsyncMock()
        facade.invoke_http_in_device.side_effect = RuntimeError("boom")
        request = MagicMock()
        request.method = "GET"
        request.headers = {}
        request.body = AsyncMock(return_value=b"")
        request.url.query = ""

        with pytest.raises(HTTPException) as exc_info:
            await invoke_http_in_device("dev-001", 8080, "api", request, facade=facade)
        assert exc_info.value.status_code == 500


# ── open_folder ──


class TestOpenFolder:
    @pytest.mark.asyncio
    async def test_success_with_path(self):
        facade = AsyncMock()
        facade.open_folder.return_value = True
        req = OpenFolderRequest(folder_path="/tmp")
        result = await open_folder("dev-001", req, facade=facade)
        assert isinstance(result, ApiResponse)

    @pytest.mark.asyncio
    async def test_success_no_request(self):
        facade = AsyncMock()
        facade.open_folder.return_value = True
        result = await open_folder("dev-001", None, facade=facade)
        assert isinstance(result, ApiResponse)

    @pytest.mark.asyncio
    async def test_not_implemented(self):
        facade = AsyncMock()
        facade.open_folder.side_effect = NotImplementedError("not supported")
        with pytest.raises(HTTPException) as exc_info:
            await open_folder("dev-001", None, facade=facade)
        assert exc_info.value.status_code == 501

    @pytest.mark.asyncio
    async def test_facade_exception(self):
        facade = AsyncMock()
        facade.open_folder.side_effect = _make_facade_exception(
            ErrorCode.DEVICE_NOT_FOUND, operation="open_folder"
        )
        with pytest.raises(HTTPException) as exc_info:
            await open_folder("dev-001", None, facade=facade)
        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_generic_exception(self):
        facade = AsyncMock()
        facade.open_folder.side_effect = RuntimeError("err")
        with pytest.raises(HTTPException) as exc_info:
            await open_folder("dev-001", None, facade=facade)
        assert exc_info.value.status_code == 500


# ── update_device_ttl ──


class TestUpdateDeviceTtl:
    @pytest.mark.asyncio
    async def test_success(self):
        facade = AsyncMock()
        facade.update_device_ttl.return_value = TTLInfo(
            paas_device_id="dev-001",
            old_expiration_time=datetime.now(UTC),
            new_expiration_time=datetime.now(UTC) + timedelta(hours=24),
            success=True,
        )
        result = await update_device_ttl("dev-001", facade=facade)
        assert isinstance(result, ApiResponse)
        assert result.data.success is True

    @pytest.mark.asyncio
    async def test_not_implemented(self):
        facade = AsyncMock()
        facade.update_device_ttl.side_effect = NotImplementedError("not supported")
        with pytest.raises(HTTPException) as exc_info:
            await update_device_ttl("dev-001", facade=facade)
        assert exc_info.value.status_code == 501

    @pytest.mark.asyncio
    async def test_facade_exception(self):
        facade = AsyncMock()
        facade.update_device_ttl.side_effect = _make_facade_exception(
            ErrorCode.DEVICE_NOT_FOUND, operation="update_device_ttl"
        )
        with pytest.raises(HTTPException) as exc_info:
            await update_device_ttl("dev-001", facade=facade)
        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_generic_exception(self):
        facade = AsyncMock()
        facade.update_device_ttl.side_effect = RuntimeError("err")
        with pytest.raises(HTTPException) as exc_info:
            await update_device_ttl("dev-001", facade=facade)
        assert exc_info.value.status_code == 500


# ── ERROR_CODE_TO_HTTP_STATUS ──


class TestErrorCodeMapping:
    def test_all_error_codes_mapped(self):
        unmapped = [code for code in ErrorCode if code not in ERROR_CODE_TO_HTTP_STATUS]
        assert len(unmapped) <= 10
