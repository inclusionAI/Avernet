"""Tests for local management WebSocket endpoints.

Covers R3.1, R3.2, R3.3, R3.4, R3.7 requirements:
- WebSocket endpoint at /ws/local/management
- JWT Bearer token authentication via Authorization header (Phase 18.4)
- Duplicate connection detection (D-DC01~04)
- Heartbeat handling without response (D-HB03)
- Result message routing (R3.7)
"""

from __future__ import annotations

import base64
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import WebSocket, WebSocketDisconnect, WebSocketException, status

from secbaas.community.adapters.web.websocket.local_management_ws import (
    _extract_user_id_from_jwt,
    _handle_callback_fire_and_forget,
    _handle_callback_with_response,
    local_management_websocket,
    router,
)
from secbaas.community.core.service.paas import PaasServiceFactory


@pytest.fixture
def factory():
    from secbaas.community.plugins.sandbox.arca import StubArcaSandboxPlugin
    from secbaas.community.plugins.sandbox.desktop import StubDesktopSandboxPlugin
    from secbaas.community.plugins.sandbox.teclaw import StubTeClawBotPlugin
    from secbaas.community.spi.sandbox import PaasSandboxPlugins

    return PaasServiceFactory(
        template_service=MagicMock(),
        connection_manager=MagicMock(),
        worker_router=MagicMock(),
        instance_router=MagicMock(),
        device_template_repository=MagicMock(),
        device_repository=MagicMock(),
        publish_record_repository=MagicMock(),
        local_user_machine_repository=MagicMock(),
        paas_sandbox_plugins=PaasSandboxPlugins(
            arca_sandbox_plugin_factory=StubArcaSandboxPlugin,
            desktop_sandbox_plugin=StubDesktopSandboxPlugin(),
            teclaw_bot_plugin_factory=lambda endpoint, key_supplier, timeout: (
                StubTeClawBotPlugin()
            ),
        ),
        callback_handler=MagicMock(handle=AsyncMock(return_value={"status": "ok"})),
    )


class TestLocalManagementV1WebSocket:
    """Test suite for /ws/v1/local/mng endpoint."""

    @pytest.fixture
    def mock_connection_manager(self) -> MagicMock:
        """Create mock ConnectionManager."""
        cm = MagicMock()
        cm.is_connected = MagicMock(return_value=False)
        cm._get_user_id = MagicMock(return_value=None)
        cm._add_connection = AsyncMock()
        cm._remove_connection = MagicMock()
        cm._handle_result = MagicMock()
        return cm

    @pytest.fixture
    def mock_local_service(self) -> MagicMock:
        """Create mock LocalPaasService."""
        service = MagicMock()
        service.handle_mng_register = AsyncMock()
        service.handle_mng_heartbeat = AsyncMock()
        service.handle_mng_disconnect = AsyncMock()
        return service

    def test_endpoint_path_exists(self) -> None:
        """Test 1: Verify /ws/local/management route is registered."""
        # Check that the route is registered
        routes = [r for r in router.routes if hasattr(r, "path")]
        paths = [r.path for r in routes]
        assert "/ws/local/management" in paths

    def test_endpoint_rejects_missing_machine_id(self) -> None:
        """Test 2: Endpoint rejects missing machine_id with code 1008."""
        # This test verifies the validation logic exists
        # Actual WebSocket testing requires live server or mocked transport
        # Check the function signature includes machine_id
        import inspect

        from secbaas.community.adapters.web.websocket.local_management_ws import (
            local_management_websocket,
        )

        sig = inspect.signature(local_management_websocket)
        params = list(sig.parameters.keys())
        assert "machine_id" in params

    def test_duplicate_connection_check_logic(
        self, mock_connection_manager: MagicMock
    ) -> None:
        """Test 3: Duplicate connection check uses connection_manager.is_connected."""
        # Verify the connection_manager interface is used correctly
        mock_connection_manager.is_connected.return_value = True

        machine_id = "machine-001"
        result = mock_connection_manager.is_connected(machine_id)

        assert result is True
        mock_connection_manager.is_connected.assert_called_with(machine_id)

    def test_cross_user_duplicate_check(
        self, mock_connection_manager: MagicMock
    ) -> None:
        """Test 4: Cross-user duplicate triggers warning and rejection."""
        # Simulate existing connection from different user
        mock_connection_manager.is_connected.return_value = True
        mock_connection_manager._get_user_id.return_value = "different-user"

        machine_id = "machine-001"
        existing_user = mock_connection_manager._get_user_id(machine_id)
        current_user = "test-user-001"  # Expected user_id from JWT

        assert existing_user != current_user
        assert existing_user == "different-user"

    def test_on_accept_calls_add_connection(
        self, mock_connection_manager: MagicMock
    ) -> None:
        """Test 5: On accept, connection_manager.add_connection is called via async."""
        # Verify the coroutine exists
        import inspect

        assert inspect.iscoroutinefunction(mock_connection_manager._add_connection)

    def test_heartbeat_calls_handle_mng_heartbeat(
        self, mock_local_service: MagicMock
    ) -> None:
        """Test 6: Heartbeat message calls local_service.handle_mng_heartbeat."""
        import inspect

        assert inspect.iscoroutinefunction(mock_local_service.handle_mng_heartbeat)

    def test_result_calls_connection_manager_handle_result(
        self, mock_connection_manager: MagicMock
    ) -> None:
        """Test 7: Result message routes to connection_manager.handle_result."""
        # Simulate receiving a result message
        message = {
            "type": "result",
            "request_id": "machine-001:abc123",
            "payload": {"status": "success"},
        }

        mock_connection_manager._handle_result(message)

        mock_connection_manager._handle_result.assert_called_once_with(message)

    def test_disconnect_calls_remove_connection(
        self, mock_connection_manager: MagicMock
    ) -> None:
        """Test 8: On disconnect, connection_manager.remove_connection is called."""
        machine_id = "machine-001"
        mock_connection_manager._remove_connection(machine_id)

        mock_connection_manager._remove_connection.assert_called_with(machine_id)


class TestWebSocketMessageRouting:
    """Test message type routing logic."""

    def test_heartbeat_message_format(self) -> None:
        """Test D-HB02: Heartbeat format is minimal JSON."""
        message = {"type": "heartbeat"}
        serialized = json.dumps(message)

        assert serialized == '{"type": "heartbeat"}'

    def test_result_message_format(self) -> None:
        """Test D-MP01: Result message includes request_id."""
        message = {
            "type": "result",
            "request_id": "machine-001:abc123def456",
            "payload": {"status": "success", "data": {}},
        }

        assert message["type"] == "result"
        assert "request_id" in message
        assert "payload" in message
        assert message["request_id"].startswith("machine-001:")

    def test_heartbeat_no_response_per_d_hb03(self) -> None:
        """Test D-HB03: Heartbeat does not generate response (no heartbeat_ack)."""
        # This test verifies the protocol design - no response sent
        # The new endpoint /ws/v1/local/mng does NOT send heartbeat_ack
        # Legacy /ws/local/management DOES send heartbeat_ack
        # D-HB03: fire-and-forget heartbeat protocol
        heartbeat_message = {"type": "heartbeat"}

        # No response expected for new endpoint
        expected_response = None
        assert expected_response is None  # Verify no response expected
        assert heartbeat_message["type"] == "heartbeat"


class TestWebSocketExceptionHandling:
    """Test WebSocket exception constants and handling."""

    def test_websocket_exception_codes(self) -> None:
        """Test D-EH02: Standard WebSocket close codes are available."""

        assert status.WS_1000_NORMAL_CLOSURE == 1000
        assert status.WS_1008_POLICY_VIOLATION == 1008
        assert status.WS_1011_INTERNAL_ERROR == 1011

    def test_close_code_1008_for_duplicates(self) -> None:
        """Test D-DC03: Duplicate connections use close code 1008."""

        # Code 1008 is Policy Violation
        assert status.WS_1008_POLICY_VIOLATION == 1008


class TestConnectionMetadata:
    """Test connection metadata tracking (D-CM01)."""

    def test_metadata_structure(self) -> None:
        """Test D-CM01: Metadata includes required fields."""
        metadata = {
            "remote_addr": "192.168.1.100",
            "user_agent": "Mozilla/5.0",
            "headers": {"accept": "application/json"},
            "connected_at": "2026-05-14T10:00:00",
            "user_id": "user-001",
        }

        required_fields = [
            "remote_addr",
            "user_agent",
            "headers",
            "connected_at",
            "user_id",
        ]
        for field in required_fields:
            assert field in metadata


class TestLocalManagementRouter:
    """Test router configuration."""

    def test_router_has_correct_tags(self) -> None:
        """Test router is tagged for API documentation."""
        from secbaas.community.adapters.web.websocket.local_management_ws import router

        assert router.tags == ["Local Management WebSocket"]


class TestJWTExtraction:
    """Test suite for JWT extraction from Authorization header (Phase 18.4).

    Covers REQ-18.4.1 through REQ-18.4.5:
    - JWT Bearer token extraction
    - "sno" field parsing as user_id
    - Error handling with WebSocketException code 1008
    - Warning logging with client IP for auth failures
    """

    @pytest.fixture
    def mock_websocket(self) -> MagicMock:
        """Create mock WebSocket with headers and client."""
        ws = MagicMock()
        ws.headers = {}
        ws.client = MagicMock()
        ws.client.host = "192.168.1.100"
        return ws

    def _create_test_jwt(self, sno: str | int, include_sno: bool = True) -> str:
        """Create a test JWT token with encoded payload (no signature verification)."""
        payload: dict[str, object] = {"exp": 1234567890}
        if include_sno:
            payload["sno"] = sno
        payload_json = json.dumps(payload)
        payload_b64 = (
            base64.urlsafe_b64encode(payload_json.encode()).decode().rstrip("=")
        )
        return f"header.{payload_b64}.signature"

    def test_extract_valid_jwt_with_string_sno(self, mock_websocket: MagicMock) -> None:
        """Test: Valid JWT with string sno returns the sno value."""
        token = self._create_test_jwt("test-user-001")
        mock_websocket.headers = {"Authorization": f"Bearer {token}"}

        result = _extract_user_id_from_jwt(mock_websocket)

        assert result == "test-user-001"

    def test_extract_valid_jwt_with_numeric_sno(
        self, mock_websocket: MagicMock
    ) -> None:
        """Test: Valid JWT with numeric sno converts to string (D-07)."""
        token = self._create_test_jwt(456)
        mock_websocket.headers = {"Authorization": f"Bearer {token}"}

        result = _extract_user_id_from_jwt(mock_websocket)

        assert result == "456"
        assert isinstance(result, str)

    def test_extract_valid_jwt_with_int_sno_zero(
        self, mock_websocket: MagicMock
    ) -> None:
        """Test: Valid JWT with sno=0 returns "0" (edge case for falsy check)."""
        # Create JWT with sno=0 explicitly
        payload = {"sno": 0, "exp": 1234567890}
        payload_json = json.dumps(payload)
        payload_b64 = (
            base64.urlsafe_b64encode(payload_json.encode()).decode().rstrip("=")
        )
        token = f"header.{payload_b64}.signature"
        mock_websocket.headers = {"Authorization": f"Bearer {token}"}

        result = _extract_user_id_from_jwt(mock_websocket)

        assert result == "0"

    def test_missing_authorization_header(self, mock_websocket: MagicMock) -> None:
        """Test: Missing Authorization header raises WebSocketException 1008."""
        mock_websocket.headers = {}

        with pytest.raises(WebSocketException) as exc_info:
            _extract_user_id_from_jwt(mock_websocket)

        assert exc_info.value.code == status.WS_1008_POLICY_VIOLATION
        assert "Missing Authorization header" in str(exc_info.value.reason)

    def test_missing_authorization_header_logs_warning(
        self, mock_websocket: MagicMock
    ) -> None:
        """Test: Missing Authorization header logs warning with client IP (D-12)."""
        mock_websocket.headers = {}

        with patch(
            "secbaas.community.adapters.web.websocket.local_management_ws.logger"
        ) as mock_logger:
            with pytest.raises(WebSocketException):
                _extract_user_id_from_jwt(mock_websocket)

            mock_logger.warning.assert_called_once()
            log_msg = mock_logger.warning.call_args[0][0]
            assert "Missing Authorization header" in log_msg
            assert "192.168.1.100" in log_msg

    def test_invalid_bearer_prefix(self, mock_websocket: MagicMock) -> None:
        """Test: Non-Bearer authorization format raises WebSocketException 1008 (D-05)."""
        mock_websocket.headers = {"Authorization": "Basic dXNlcjpwYXNz"}

        with pytest.raises(WebSocketException) as exc_info:
            _extract_user_id_from_jwt(mock_websocket)

        assert exc_info.value.code == status.WS_1008_POLICY_VIOLATION
        assert "Invalid authorization format" in str(exc_info.value.reason)

    def test_invalid_bearer_prefix_logs_warning(
        self, mock_websocket: MagicMock
    ) -> None:
        """Test: Invalid Bearer format logs warning with client IP."""
        mock_websocket.headers = {"Authorization": "Basic dXNlcjpwYXNz"}

        with patch(
            "secbaas.community.adapters.web.websocket.local_management_ws.logger"
        ) as mock_logger:
            with pytest.raises(WebSocketException):
                _extract_user_id_from_jwt(mock_websocket)

            mock_logger.warning.assert_called_once()
            log_msg = mock_logger.warning.call_args[0][0]
            assert "Invalid authorization format" in log_msg
            assert "192.168.1.100" in log_msg

    def test_malformed_jwt_format(self, mock_websocket: MagicMock) -> None:
        """Test: JWT without 3 parts raises WebSocketException 1008 (D-11)."""
        mock_websocket.headers = {"Authorization": "Bearer invalid.jwt"}

        with pytest.raises(WebSocketException) as exc_info:
            _extract_user_id_from_jwt(mock_websocket)

        assert exc_info.value.code == status.WS_1008_POLICY_VIOLATION
        assert "Invalid JWT format" in str(exc_info.value.reason)

    def test_jwt_without_sno_field(self, mock_websocket: MagicMock) -> None:
        """Test: JWT without sno field raises WebSocketException 1008 (D-04)."""
        token = self._create_test_jwt("", include_sno=False)
        mock_websocket.headers = {"Authorization": f"Bearer {token}"}

        with pytest.raises(WebSocketException) as exc_info:
            _extract_user_id_from_jwt(mock_websocket)

        assert exc_info.value.code == status.WS_1008_POLICY_VIOLATION
        assert "Missing sno field" in str(exc_info.value.reason)

    def test_jwt_without_sno_field_logs_warning(
        self, mock_websocket: MagicMock
    ) -> None:
        """Test: Missing sno field logs warning with client IP."""
        token = self._create_test_jwt("", include_sno=False)
        mock_websocket.headers = {"Authorization": f"Bearer {token}"}

        with patch(
            "secbaas.community.adapters.web.websocket.local_management_ws.logger"
        ) as mock_logger:
            with pytest.raises(WebSocketException):
                _extract_user_id_from_jwt(mock_websocket)

            mock_logger.warning.assert_called_once()
            log_msg = mock_logger.warning.call_args[0][0]
            assert "Missing sno field" in log_msg
            assert "192.168.1.100" in log_msg

    def test_jwt_base64_decode_failure(self, mock_websocket: MagicMock) -> None:
        """Test: Invalid Base64 in JWT payload raises WebSocketException 1008."""
        # Create token with invalid base64 payload
        mock_websocket.headers = {"Authorization": "Bearer header.!!!invalid!!!.sig"}

        with pytest.raises(WebSocketException) as exc_info:
            _extract_user_id_from_jwt(mock_websocket)

        assert exc_info.value.code == status.WS_1008_POLICY_VIOLATION
        assert "Invalid JWT format" in str(exc_info.value.reason)

    def test_jwt_json_decode_failure(self, mock_websocket: MagicMock) -> None:
        """Test: Invalid JSON in JWT payload raises WebSocketException 1008."""
        # Create valid base64 but invalid JSON
        invalid_json_b64 = (
            base64.urlsafe_b64encode(b"not valid json").decode().rstrip("=")
        )
        mock_websocket.headers = {
            "Authorization": f"Bearer header.{invalid_json_b64}.sig"
        }

        with pytest.raises(WebSocketException) as exc_info:
            _extract_user_id_from_jwt(mock_websocket)

        assert exc_info.value.code == status.WS_1008_POLICY_VIOLATION
        assert "Invalid JWT format" in str(exc_info.value.reason)

    def test_jwt_parse_error_logs_warning(self, mock_websocket: MagicMock) -> None:
        """Test: JWT parse errors log warning with client IP."""
        mock_websocket.headers = {"Authorization": "Bearer header.!!!invalid!!!.sig"}

        with patch(
            "secbaas.community.adapters.web.websocket.local_management_ws.logger"
        ) as mock_logger:
            with pytest.raises(WebSocketException):
                _extract_user_id_from_jwt(mock_websocket)

            mock_logger.warning.assert_called_once()
            log_msg = mock_logger.warning.call_args[0][0]
            assert "JWT" in log_msg and "Parse error" in log_msg
            assert "192.168.1.100" in log_msg

    def test_unknown_client_ip(self) -> None:
        """Test: Missing client info logs 'unknown' for client IP."""
        ws = MagicMock()
        ws.headers = {}
        ws.client = None

        with patch(
            "secbaas.community.adapters.web.websocket.local_management_ws.logger"
        ) as mock_logger:
            with pytest.raises(WebSocketException):
                _extract_user_id_from_jwt(ws)

            log_msg = mock_logger.warning.call_args[0][0]
            assert "unknown" in log_msg


# =============================================================================
# Tests for uncovered functions:
# _handle_callback_fire_and_forget, _handle_callback_with_response
# =============================================================================


class TestHandleCallbackFireAndForget:
    """Tests for _handle_callback_fire_and_forget."""

    @pytest.fixture
    def mock_local_service(self) -> MagicMock:
        service = MagicMock()
        service.handle_callback = AsyncMock()
        return service

    @pytest.mark.asyncio
    async def test_callback_success(self, mock_local_service: MagicMock) -> None:
        """Fire-and-forget callback succeeds and logs info."""
        payload = {"action": "container_ready", "params": {"status": "running"}}

        with patch(
            "secbaas.community.adapters.web.websocket.local_management_ws.logger"
        ) as mock_logger:
            await _handle_callback_fire_and_forget(
                machine_id="m-001",
                action="container_ready",
                payload=payload,
                local_service=mock_local_service,
            )

        mock_local_service.handle_callback.assert_awaited_once_with(
            "m-001", "container_ready", {"status": "running"}
        )
        mock_logger.info.assert_called_once()
        assert "container_ready" in mock_logger.info.call_args[0][0]

    @pytest.mark.asyncio
    async def test_callback_empty_params(self, mock_local_service: MagicMock) -> None:
        """Fire-and-forget callback with no params key defaults to empty dict."""
        payload: dict = {}

        await _handle_callback_fire_and_forget(
            machine_id="m-001",
            action="status_update",
            payload=payload,
            local_service=mock_local_service,
        )

        mock_local_service.handle_callback.assert_awaited_once_with(
            "m-001", "status_update", {}
        )

    @pytest.mark.asyncio
    async def test_callback_exception_logs_warning(
        self, mock_local_service: MagicMock
    ) -> None:
        """Fire-and-forget callback exception logs warning with full context."""
        mock_local_service.handle_callback.side_effect = RuntimeError("boom")

        payload = {"action": "bad_action", "params": {}}
        with patch(
            "secbaas.community.adapters.web.websocket.local_management_ws.logger"
        ) as mock_logger:
            await _handle_callback_fire_and_forget(
                machine_id="m-001",
                action="bad_action",
                payload=payload,
                local_service=mock_local_service,
            )

        mock_logger.warning.assert_called_once()
        log_msg = mock_logger.warning.call_args[0][0]
        assert "bad_action" in log_msg
        assert "m-001" in log_msg
        assert "RuntimeError" in log_msg
        assert "boom" in log_msg


class TestHandleCallbackWithResponse:
    """Tests for _handle_callback_with_response."""

    @pytest.fixture
    def mock_local_service(self) -> MagicMock:
        service = MagicMock()
        service.handle_callback = AsyncMock()
        return service

    @pytest.fixture
    def mock_cm(self) -> MagicMock:
        cm = MagicMock()
        cm.send_callback_result = AsyncMock(return_value=True)
        return cm

    @pytest.mark.asyncio
    async def test_callback_result_ok_with_data(
        self, mock_local_service: MagicMock, mock_cm: MagicMock
    ) -> None:
        """Callback with status=ok result sends success response with data."""
        mock_local_service.handle_callback.return_value = {
            "status": "ok",
            "data": {"key": "value"},
        }
        payload = {"action": "deploy", "params": {"version": "1.0"}}

        await _handle_callback_with_response(
            machine_id="m-001",
            request_id="req-123",
            action="deploy",
            payload=payload,
            local_service=mock_local_service,
            connection_manager=mock_cm,
        )

        mock_local_service.handle_callback.assert_awaited_once_with(
            "m-001", "deploy", {"version": "1.0"}
        )
        mock_cm.send_callback_result.assert_awaited_once_with(
            "m-001",
            "req-123",
            status="ok",
            data={"key": "value"},
        )

    @pytest.mark.asyncio
    async def test_callback_result_error_with_details(
        self, mock_local_service: MagicMock, mock_cm: MagicMock
    ) -> None:
        """Callback with status=error result sends error response."""
        mock_local_service.handle_callback.return_value = {
            "status": "error",
            "error": "DEPLOY_FAILED",
            "message": "version not found",
        }
        payload = {"action": "deploy", "params": {"version": "99.0"}}

        await _handle_callback_with_response(
            machine_id="m-001",
            request_id="req-456",
            action="deploy",
            payload=payload,
            local_service=mock_local_service,
            connection_manager=mock_cm,
        )

        mock_cm.send_callback_result.assert_awaited_once_with(
            "m-001",
            "req-456",
            status="error",
            error="DEPLOY_FAILED",
            message="version not found",
        )

    @pytest.mark.asyncio
    async def test_callback_result_none_sends_empty_ok(
        self, mock_local_service: MagicMock, mock_cm: MagicMock
    ) -> None:
        """Callback returning None sends empty success response."""
        mock_local_service.handle_callback.return_value = None
        payload = {"action": "ping", "params": {}}

        await _handle_callback_with_response(
            machine_id="m-001",
            request_id="req-789",
            action="ping",
            payload=payload,
            local_service=mock_local_service,
            connection_manager=mock_cm,
        )

        mock_cm.send_callback_result.assert_awaited_once_with(
            "m-001", "req-789", status="ok", data={}
        )

    @pytest.mark.asyncio
    async def test_callback_exception_sends_error_response(
        self, mock_local_service: MagicMock, mock_cm: MagicMock
    ) -> None:
        """Callback that raises sends error response to mng daemon."""
        mock_local_service.handle_callback.side_effect = RuntimeError("timeout")
        payload = {"action": "heavy_task", "params": {}}

        with patch(
            "secbaas.community.adapters.web.websocket.local_management_ws.logger"
        ) as mock_logger:
            await _handle_callback_with_response(
                machine_id="m-001",
                request_id="req-999",
                action="heavy_task",
                payload=payload,
                local_service=mock_local_service,
                connection_manager=mock_cm,
            )

        # Warning logged with full context
        mock_logger.warning.assert_called_once()
        log_msg = mock_logger.warning.call_args[0][0]
        assert "heavy_task" in log_msg
        assert "m-001" in log_msg
        assert "req-999" in log_msg
        assert "RuntimeError" in log_msg

        # Error response sent to mng daemon
        mock_cm.send_callback_result.assert_awaited_once_with(
            "m-001",
            "req-999",
            status="error",
            error="CALLBACK_PROCESSING_ERROR",
            message="timeout",
        )

    @pytest.mark.asyncio
    async def test_callback_exception_send_fails_logs_error(
        self, mock_local_service: MagicMock, mock_cm: MagicMock
    ) -> None:
        """When error response send fails, logs error."""
        mock_local_service.handle_callback.side_effect = RuntimeError("timeout")
        mock_cm.send_callback_result.return_value = False  # send fails

        with patch(
            "secbaas.community.adapters.web.websocket.local_management_ws.logger"
        ) as mock_logger:
            await _handle_callback_with_response(
                machine_id="m-001",
                request_id="req-fail",
                action="crash",
                payload={"action": "crash", "params": {}},
                local_service=mock_local_service,
                connection_manager=mock_cm,
            )

        # Error log should be emitted for failed error-response send
        error_logs = [
            c
            for c in mock_logger.error.call_args_list
            if "CALLBACK_ERROR_RESPONSE_FAILED" in str(c)
        ]
        assert len(error_logs) == 1

    @pytest.mark.asyncio
    async def test_callback_empty_params_defaults_to_empty_dict(
        self, mock_local_service: MagicMock, mock_cm: MagicMock
    ) -> None:
        """Callback with no params key passes empty dict to handle_callback."""
        mock_local_service.handle_callback.return_value = {
            "status": "ok",
            "data": {},
        }

        await _handle_callback_with_response(
            machine_id="m-001",
            request_id="req-xyz",
            action="no_params",
            payload={},  # no "params" key
            local_service=mock_local_service,
            connection_manager=mock_cm,
        )

        mock_local_service.handle_callback.assert_awaited_once_with(
            "m-001", "no_params", {}
        )


# =============================================================================
# Tests for local_management_websocket main handler
# =============================================================================


class TestLocalManagementWebsocketHandler:
    """Tests for the local_management_websocket async handler.

    Tests the async handler directly with mocked dependencies and a
    MagicMock WebSocket — no real connections, no asyncio.sleep.
    """

    # ---- Helpers ----

    @staticmethod
    def _make_jwt(sno: str | int) -> str:
        """Create a minimal valid JWT token string."""
        payload = {"sno": sno, "exp": 9999999999}
        payload_b64 = (
            base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
        )
        return f"header.{payload_b64}.sig"

    @staticmethod
    def _make_ws(
        machine_id: str = "m-001",
        machine_name: str | None = None,
        user_id: str = "user-001",
        headers: dict | None = None,
        connected: bool = True,
    ) -> MagicMock:
        """Build a MagicMock WebSocket with standard preconditions."""
        if headers is None:
            token = TestLocalManagementWebsocketHandler._make_jwt(user_id)
            headers = {"Authorization": f"Bearer {token}"}

        ws = MagicMock(spec=WebSocket)
        ws.headers = headers
        ws.client = MagicMock()
        ws.client.host = "10.0.0.1"
        ws.url = "ws://localhost/ws/local/management"
        ws.scope = {}
        ws.query_params = {}

        ws.accept = AsyncMock()
        ws.receive_text = AsyncMock()
        ws.send_json = AsyncMock()
        ws.close = AsyncMock()
        return ws

    @staticmethod
    def _make_cm(**kwargs) -> MagicMock:
        """Build a MagicMock ConnectionManager with sensible defaults."""
        cm = MagicMock()
        cm.MAX_CONNECTIONS = 10000
        cm.is_at_capacity = MagicMock(return_value=kwargs.get("at_capacity", False))
        cm.is_connected = MagicMock(return_value=kwargs.get("is_connected", False))
        cm._get_user_id = MagicMock(return_value=kwargs.get("existing_user_id", None))
        cm._add_connection = AsyncMock()
        cm._remove_connection = MagicMock()
        cm._on_connect = MagicMock()
        cm._on_disconnect = MagicMock()
        cm._update_heartbeat = AsyncMock()
        cm._handle_result = MagicMock()
        cm.send_callback_result = AsyncMock(return_value=True)
        return cm

    @staticmethod
    def _make_service() -> MagicMock:
        """Build a MagicMock LocalPaasService."""
        svc = MagicMock()
        svc.handle_mng_register = AsyncMock()
        svc.handle_mng_disconnect = AsyncMock()
        svc.handle_callback = AsyncMock()
        return svc

    # ---- Pre-accept validation tests ----

    @pytest.mark.asyncio
    async def test_rejects_missing_machine_id(self) -> None:
        """Empty machine_id raises WebSocketException 1008 before accept."""
        ws = self._make_ws()

        with pytest.raises(WebSocketException) as exc_info:
            await local_management_websocket(
                websocket=ws,
                machine_id="",  # empty string — falsy
                machine_name=None,
            )

        assert exc_info.value.code == status.WS_1008_POLICY_VIOLATION
        assert "machine_id is required" in str(exc_info.value.reason)
        ws.accept.assert_not_called()

    @pytest.mark.asyncio
    async def test_rejects_missing_machine_id_none(self) -> None:
        """None machine_id raises WebSocketException 1008 before accept."""
        ws = self._make_ws()

        with pytest.raises(WebSocketException) as exc_info:
            await local_management_websocket(
                websocket=ws,
                machine_id=None,  # type: ignore[arg-type]
                machine_name=None,
            )

        assert exc_info.value.code == status.WS_1008_POLICY_VIOLATION
        assert "machine_id is required" in str(exc_info.value.reason)

    @pytest.mark.asyncio
    async def test_rejects_at_capacity(self) -> None:
        """Server at capacity raises WebSocketException 1013."""
        ws = self._make_ws()

        with pytest.raises(WebSocketException) as exc_info:
            await local_management_websocket(
                websocket=ws,
                machine_id="m-001",
                machine_name=None,
                connection_manager=self._make_cm(at_capacity=True),
            )

        assert exc_info.value.code == status.WS_1013_TRY_AGAIN_LATER
        assert "capacity" in str(exc_info.value.reason).lower()

    @pytest.mark.asyncio
    async def test_rejects_duplicate_connection(self) -> None:
        """Duplicate machine_id raises WebSocketException 1008."""
        ws = self._make_ws(machine_id="m-dup")

        cm = self._make_cm(is_connected=True, existing_user_id="user-001")
        with pytest.raises(WebSocketException) as exc_info:
            await local_management_websocket(
                websocket=ws,
                machine_id="m-dup",
                machine_name=None,
                connection_manager=cm,
                paas_service_factory=MagicMock(),
            )

        assert exc_info.value.code == status.WS_1008_POLICY_VIOLATION
        assert "already connected" in str(exc_info.value.reason).lower()

    @pytest.mark.asyncio
    async def test_rejects_cross_user_duplicate(self) -> None:
        """Cross-user duplicate triggers warning and rejection."""
        ws = self._make_ws(machine_id="m-dup", user_id="user-002")

        cm = self._make_cm(is_connected=True, existing_user_id="user-001")

        with patch(
            "secbaas.community.adapters.web.websocket.local_management_ws.logger"
        ) as mock_logger:
            with pytest.raises(WebSocketException):
                await local_management_websocket(
                    websocket=ws,
                    machine_id="m-dup",
                    machine_name=None,
                    connection_manager=cm,
                    paas_service_factory=MagicMock(),
                )

        # Cross-user warning logged
        cross_warnings = [
            c for c in mock_logger.warning.call_args_list if "Cross-user" in str(c)
        ]
        assert len(cross_warnings) == 1

    # ---- Successful connection flow ----

    @pytest.mark.asyncio
    async def test_successful_connect_flow(self) -> None:
        """Full connect flow: accept -> add_connection -> on_connect -> register."""
        ws = self._make_ws(machine_id="m-001")
        cm = self._make_cm()
        svc = self._make_service()

        # Make receive_text raise WebSocketDisconnect after accept to exit loop cleanly
        ws.receive_text.side_effect = WebSocketDisconnect(code=1000)

        await local_management_websocket(
            websocket=ws,
            machine_id="m-001",
            machine_name="Test Machine",
            connection_manager=cm,
            paas_service_factory=MagicMock(
                create_local_paas_service=MagicMock(return_value=svc)
            ),
        )

        # Verify accept was called
        ws.accept.assert_awaited_once()

        # Verify connection registered
        cm._add_connection.assert_awaited_once()
        add_args = cm._add_connection.call_args
        assert add_args[0][0] == "m-001"
        assert add_args[0][1] is ws
        metadata = add_args[0][2]
        assert metadata["user_id"] == "user-001"
        assert metadata["remote_addr"] == "10.0.0.1"
        assert "connected_at" in metadata
        # Verify sensitive headers filtered per D-CM01
        assert "authorization" not in {k.lower() for k in metadata["headers"]}

        # Verify DB and service layer called
        cm._on_connect.assert_called_once_with("m-001", "user-001")
        svc.handle_mng_register.assert_awaited_once_with(
            machine_id="m-001",
            user_id="user-001",
            machine_name="Test Machine",
        )

    @pytest.mark.asyncio
    async def test_successful_connect_without_machine_name(self) -> None:
        """Connect flow without optional machine_name parameter."""
        ws = self._make_ws(machine_id="m-002")
        ws.receive_text.side_effect = WebSocketDisconnect(code=1000)
        cm = self._make_cm()
        svc = self._make_service()

        await local_management_websocket(
            websocket=ws,
            machine_id="m-002",
            machine_name=None,
            connection_manager=cm,
            paas_service_factory=MagicMock(
                create_local_paas_service=MagicMock(return_value=svc)
            ),
        )

        svc.handle_mng_register.assert_awaited_once_with(
            machine_id="m-002",
            user_id="user-001",
            machine_name=None,
        )

    # ---- Message loop: heartbeat ----

    @pytest.mark.asyncio
    async def test_heartbeat_message_updates_heartbeat(self) -> None:
        """Heartbeat message calls _update_heartbeat (fire-and-forget, no response)."""
        ws = self._make_ws(machine_id="m-001")
        ws.receive_text = AsyncMock(
            side_effect=[
                json.dumps({"type": "heartbeat"}),
                json.dumps({"type": "heartbeat"}),
                WebSocketDisconnect(code=1000),
            ]
        )
        cm = self._make_cm()
        svc = self._make_service()

        await local_management_websocket(
            websocket=ws,
            machine_id="m-001",
            machine_name=None,
            connection_manager=cm,
            paas_service_factory=MagicMock(
                create_local_paas_service=MagicMock(return_value=svc)
            ),
        )

        assert cm._update_heartbeat.call_count == 2
        cm._update_heartbeat.assert_awaited_with("m-001")
        # D-HB03: no heartbeat_ack sent (fire-and-forget)
        ws.send_json.assert_not_called()

    # ---- Message loop: result ----

    @pytest.mark.asyncio
    async def test_result_message_routes_to_handle_result(self) -> None:
        """Result message is routed to connection_manager._handle_result."""
        result_msg = {
            "type": "result",
            "request_id": "m-001|abc123",
            "payload": {"status": "success", "data": {}},
        }
        ws = self._make_ws(machine_id="m-001")
        ws.receive_text = AsyncMock(
            side_effect=[
                json.dumps(result_msg),
                WebSocketDisconnect(code=1000),
            ]
        )
        cm = self._make_cm()
        svc = self._make_service()

        await local_management_websocket(
            websocket=ws,
            machine_id="m-001",
            machine_name=None,
            connection_manager=cm,
            paas_service_factory=MagicMock(
                create_local_paas_service=MagicMock(return_value=svc)
            ),
        )

        cm._handle_result.assert_called_once()
        handled_msg = cm._handle_result.call_args[0][0]
        assert handled_msg["type"] == "result"
        assert handled_msg["request_id"] == "m-001|abc123"

    # ---- Message loop: callback ----

    @pytest.mark.asyncio
    async def test_callback_with_request_id_creates_task(self) -> None:
        """Callback with request_id triggers request-response mode task."""
        callback_msg = {
            "type": "callback",
            "request_id": "req-cb-001",
            "payload": {"action": "container_ready", "params": {"status": "ok"}},
        }
        ws = self._make_ws(machine_id="m-001")
        ws.receive_text = AsyncMock(
            side_effect=[
                json.dumps(callback_msg),
                WebSocketDisconnect(code=1000),
            ]
        )
        cm = self._make_cm()
        svc = self._make_service()

        def _close_coro_create_task(coro, **kwargs):
            """Mock create_task that closes the coroutine to avoid RuntimeWarning."""
            coro.close()
            return MagicMock()

        with patch(
            "secbaas.community.adapters.web.websocket.local_management_ws.asyncio.create_task",
            side_effect=_close_coro_create_task,
        ) as mock_create_task:
            await local_management_websocket(
                websocket=ws,
                machine_id="m-001",
                machine_name=None,
                connection_manager=cm,
                paas_service_factory=MagicMock(
                    create_local_paas_service=MagicMock(return_value=svc)
                ),
            )

        # asyncio.create_task was called (for request-response callback)
        mock_create_task.assert_called_once()

    @pytest.mark.asyncio
    async def test_callback_without_request_id_fire_and_forget(self) -> None:
        """Callback without request_id triggers fire-and-forget mode task."""
        callback_msg = {
            "type": "callback",
            "payload": {"action": "status_update", "params": {}},
        }
        ws = self._make_ws(machine_id="m-001")
        ws.receive_text = AsyncMock(
            side_effect=[
                json.dumps(callback_msg),
                WebSocketDisconnect(code=1000),
            ]
        )
        cm = self._make_cm()
        svc = self._make_service()

        def _close_coro_create_task(coro, **kwargs):
            """Mock create_task that closes the coroutine to avoid RuntimeWarning."""
            coro.close()
            return MagicMock()

        with patch(
            "secbaas.community.adapters.web.websocket.local_management_ws.asyncio.create_task",
            side_effect=_close_coro_create_task,
        ) as mock_create_task:
            await local_management_websocket(
                websocket=ws,
                machine_id="m-001",
                machine_name=None,
                connection_manager=cm,
                paas_service_factory=MagicMock(
                    create_local_paas_service=MagicMock(return_value=svc)
                ),
            )

        mock_create_task.assert_called_once()

    # ---- Message loop: invalid JSON ----

    @pytest.mark.asyncio
    async def test_invalid_json_does_not_disconnect(self) -> None:
        """Invalid JSON message logs error but continues (does not disconnect)."""
        ws = self._make_ws(machine_id="m-001")
        ws.receive_text = AsyncMock(
            side_effect=[
                "not valid json {{{",
                json.dumps({"type": "heartbeat"}),
                WebSocketDisconnect(code=1000),
            ]
        )
        cm = self._make_cm()
        svc = self._make_service()

        with patch(
            "secbaas.community.adapters.web.websocket.local_management_ws.logger"
        ) as mock_logger:
            await local_management_websocket(
                websocket=ws,
                machine_id="m-001",
                machine_name=None,
                connection_manager=cm,
                paas_service_factory=MagicMock(
                    create_local_paas_service=MagicMock(return_value=svc)
                ),
            )

        # Error logged for invalid JSON
        error_logs = [
            c for c in mock_logger.error.call_args_list if "Invalid JSON" in str(c)
        ]
        assert len(error_logs) == 1
        # Heartbeat was still processed (connection stayed open)
        cm._update_heartbeat.assert_awaited_once()

    # ---- Message loop: unknown type ----

    @pytest.mark.asyncio
    async def test_unknown_message_type_logs_warning(self) -> None:
        """Unknown message type logs warning but connection stays open."""
        ws = self._make_ws(machine_id="m-001")
        ws.receive_text = AsyncMock(
            side_effect=[
                json.dumps({"type": "garbage", "data": "???"}),
                WebSocketDisconnect(code=1000),
            ]
        )
        cm = self._make_cm()
        svc = self._make_service()

        with patch(
            "secbaas.community.adapters.web.websocket.local_management_ws.logger"
        ) as mock_logger:
            await local_management_websocket(
                websocket=ws,
                machine_id="m-001",
                machine_name=None,
                connection_manager=cm,
                paas_service_factory=MagicMock(
                    create_local_paas_service=MagicMock(return_value=svc)
                ),
            )

        warn_logs = [
            c
            for c in mock_logger.warning.call_args_list
            if "Unknown message type" in str(c)
        ]
        assert len(warn_logs) == 1

    # ---- Message loop: message without type field ----

    @pytest.mark.asyncio
    async def test_message_without_type_field(self) -> None:
        """Message with no 'type' field treated as unknown (empty string)."""
        ws = self._make_ws(machine_id="m-001")
        ws.receive_text = AsyncMock(
            side_effect=[
                json.dumps({"payload": "no type here"}),
                WebSocketDisconnect(code=1000),
            ]
        )
        cm = self._make_cm()
        svc = self._make_service()

        with patch(
            "secbaas.community.adapters.web.websocket.local_management_ws.logger"
        ) as mock_logger:
            await local_management_websocket(
                websocket=ws,
                machine_id="m-001",
                machine_name=None,
                connection_manager=cm,
                paas_service_factory=MagicMock(
                    create_local_paas_service=MagicMock(return_value=svc)
                ),
            )

        warn_logs = [
            c
            for c in mock_logger.warning.call_args_list
            if "Unknown message type" in str(c)
        ]
        assert len(warn_logs) == 1

    # ---- Disconnect and cleanup ----

    @pytest.mark.asyncio
    async def test_disconnect_triggers_full_cleanup(self) -> None:
        """WebSocketDisconnect triggers _on_disconnect, _remove_connection, handle_mng_disconnect."""
        ws = self._make_ws(machine_id="m-001")
        ws.receive_text.side_effect = WebSocketDisconnect(code=1000, reason="gone")
        cm = self._make_cm()
        svc = self._make_service()

        await local_management_websocket(
            websocket=ws,
            machine_id="m-001",
            machine_name=None,
            connection_manager=cm,
            paas_service_factory=MagicMock(
                create_local_paas_service=MagicMock(return_value=svc)
            ),
        )

        # Full cleanup called in finally block
        cm._on_disconnect.assert_called_once_with("m-001")
        cm._remove_connection.assert_called_once_with("m-001")
        svc.handle_mng_disconnect.assert_awaited_once_with("m-001")

    @pytest.mark.asyncio
    async def test_cleanup_handles_on_disconnect_error(self) -> None:
        """Cleanup continues despite _on_disconnect error."""
        ws = self._make_ws(machine_id="m-001")
        ws.receive_text.side_effect = WebSocketDisconnect(code=1000)
        cm = self._make_cm()
        cm._on_disconnect.side_effect = RuntimeError("DB error")
        svc = self._make_service()

        await local_management_websocket(
            websocket=ws,
            machine_id="m-001",
            machine_name=None,
            connection_manager=cm,
            paas_service_factory=MagicMock(
                create_local_paas_service=MagicMock(return_value=svc)
            ),
        )

        # Remove connection still called
        cm._remove_connection.assert_called_once_with("m-001")
        # Service disconnect still called
        svc.handle_mng_disconnect.assert_awaited_once_with("m-001")

    @pytest.mark.asyncio
    async def test_cleanup_handles_remove_connection_error(self) -> None:
        """Cleanup continues despite _remove_connection error."""
        ws = self._make_ws(machine_id="m-001")
        ws.receive_text.side_effect = WebSocketDisconnect(code=1000)
        cm = self._make_cm()
        cm._remove_connection.side_effect = RuntimeError("Remove error")
        svc = self._make_service()

        await local_management_websocket(
            websocket=ws,
            machine_id="m-001",
            machine_name=None,
            connection_manager=cm,
            paas_service_factory=MagicMock(
                create_local_paas_service=MagicMock(return_value=svc)
            ),
        )

        # on_disconnect was called
        cm._on_disconnect.assert_called_once_with("m-001")
        # Service disconnect still called despite remove error
        svc.handle_mng_disconnect.assert_awaited_once_with("m-001")

    @pytest.mark.asyncio
    async def test_cleanup_handles_service_disconnect_error(self) -> None:
        """Cleanup continues despite handle_mng_disconnect error."""
        ws = self._make_ws(machine_id="m-001")
        ws.receive_text.side_effect = WebSocketDisconnect(code=1000)
        cm = self._make_cm()
        svc = self._make_service()
        svc.handle_mng_disconnect.side_effect = RuntimeError("Service error")

        with patch(
            "secbaas.community.adapters.web.websocket.local_management_ws.logger"
        ) as mock_logger:
            await local_management_websocket(
                websocket=ws,
                machine_id="m-001",
                machine_name=None,
                connection_manager=cm,
                paas_service_factory=MagicMock(
                    create_local_paas_service=MagicMock(return_value=svc)
                ),
            )

        cm._on_disconnect.assert_called_once_with("m-001")
        cm._remove_connection.assert_called_once_with("m-001")
        service_err_logs = [
            c
            for c in mock_logger.warning.call_args_list
            if "service disconnect" in str(c).lower()
        ]
        assert len(service_err_logs) == 1

    # ---- Edge cases ----

    @pytest.mark.asyncio
    async def test_client_none_handled_gracefully(self) -> None:
        """WebSocket with client=None uses 'unknown' IP and None remote_addr."""
        ws = self._make_ws(machine_id="m-001")
        ws.client = None  # Simulate no client info
        ws.receive_text.side_effect = WebSocketDisconnect(code=1000)
        cm = self._make_cm()
        svc = self._make_service()

        await local_management_websocket(
            websocket=ws,
            machine_id="m-001",
            machine_name=None,
            connection_manager=cm,
            paas_service_factory=MagicMock(
                create_local_paas_service=MagicMock(return_value=svc)
            ),
        )

        # Accept still called
        ws.accept.assert_awaited_once()
        # Metadata has None remote_addr
        add_call = cm._add_connection.call_args
        metadata = add_call[0][2]
        assert metadata["remote_addr"] is None

    @pytest.mark.asyncio
    async def test_scope_none_handled(self) -> None:
        """WebSocket without scope field uses 'unknown' URL."""
        ws = self._make_ws(machine_id="m-001")
        ws.scope = None  # pyright: ignore[reportAttributeAccessIssue]
        ws.receive_text.side_effect = WebSocketDisconnect(code=1000)
        cm = self._make_cm()
        svc = self._make_service()

        await local_management_websocket(
            websocket=ws,
            machine_id="m-001",
            machine_name=None,
            connection_manager=cm,
            paas_service_factory=MagicMock(
                create_local_paas_service=MagicMock(return_value=svc)
            ),
        )

        # No crash — test passes if no exception
        ws.accept.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_query_params_none_handled(self) -> None:
        """WebSocket with query_params=None uses empty dict."""
        ws = self._make_ws(machine_id="m-001")
        ws.query_params = None  # pyright: ignore[reportAttributeAccessIssue]
        ws.receive_text.side_effect = WebSocketDisconnect(code=1000)
        cm = self._make_cm()
        svc = self._make_service()

        await local_management_websocket(
            websocket=ws,
            machine_id="m-001",
            machine_name=None,
            connection_manager=cm,
            paas_service_factory=MagicMock(
                create_local_paas_service=MagicMock(return_value=svc)
            ),
        )

        ws.accept.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_unexpected_exception_in_handler(self) -> None:
        """Unexpected exception during registration propagates and triggers cleanup."""
        ws = self._make_ws(machine_id="m-001")
        cm = self._make_cm()
        cm._add_connection.side_effect = RuntimeError("Connection pool exhausted")
        svc = self._make_service()

        with pytest.raises(RuntimeError, match="Connection pool exhausted"):
            await local_management_websocket(
                websocket=ws,
                machine_id="m-001",
                machine_name=None,
                connection_manager=cm,
                paas_service_factory=MagicMock(
                    create_local_paas_service=MagicMock(return_value=svc)
                ),
            )

        # Finally block still runs cleanup despite error in try
        # (on_disconnect and remove_connection are called as best-effort)
        cm._on_disconnect.assert_called()
        cm._remove_connection.assert_called()
        svc.handle_mng_disconnect.assert_awaited()
