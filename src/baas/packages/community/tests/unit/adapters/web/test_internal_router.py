"""Tests for internal_router.

Tests the POST /internal/v1/forward endpoint for cross-instance forwarding.

After the cross-process fix, ``internal_forward`` no longer queries
``ConnectionManager`` directly — it delegates to
``LocalPaasService.dispatch_to_local_connection`` which encapsulates the full
same-instance decision tree (this-process → UDS-forward → not-connected).
The ``local_paas_service`` dependency is now injected via FastAPI ``Depends``
as a parameter, so tests pass the mock service directly — no patching needed.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from secbaas.adapters.web.routers.internal.internal_router import (
    ForwardRequest,
    internal_forward,
)


def _make_mock_service(dispatch_async_mock):
    """Build a mock ``LocalPaasService`` with the given dispatch coroutine."""
    mock_service = MagicMock()
    mock_service.dispatch_to_local_connection = dispatch_async_mock
    return mock_service


class TestForwardRequest:
    """Tests for ForwardRequest model."""

    def test_model_creation(self) -> None:
        """Test ForwardRequest can be created."""
        request = ForwardRequest(
            action="execute_command",
            machine_id="machine-1",
            params={"cmd": "ls"},
            request_id="req-123",
        )

        assert request.action == "execute_command"
        assert request.machine_id == "machine-1"
        assert request.params == {"cmd": "ls"}
        assert request.request_id == "req-123"


class TestInternalForward:
    """Tests for internal_forward endpoint."""

    @pytest.fixture
    def mock_http_request(self) -> MagicMock:
        """Create a mock HTTP request."""
        mock = MagicMock()
        mock.headers = {}
        return mock

    @pytest.fixture
    def forward_request(self) -> ForwardRequest:
        """Create a sample forward request."""
        return ForwardRequest(
            action="execute_command",
            machine_id="machine-1",
            params={"cmd": "ls"},
            request_id="req-123",
        )

    @pytest.mark.asyncio
    async def test_forward_success(
        self,
        forward_request: ForwardRequest,
        mock_http_request: MagicMock,
    ) -> None:
        """Same-process success: dispatcher returns mng payload verbatim."""
        dispatch = AsyncMock(return_value={"output": "hello"})
        mock_service = _make_mock_service(dispatch)

        result = await internal_forward(
            forward_request, mock_http_request, local_paas_service=mock_service
        )

        assert result == {"output": "hello"}
        # request_id is propagated both as the dispatcher argument and inside
        # the command dict (used by the cross-process branch to seed the UDS
        # envelope id; harmless for same-process dispatch).
        dispatch.assert_awaited_once()
        call_kwargs = dispatch.call_args.kwargs
        assert call_kwargs["machine_id"] == "machine-1"
        assert call_kwargs["request_id"] == "req-123"
        assert call_kwargs["command"]["action"] == "execute_command"
        assert call_kwargs["command"]["params"] == {"cmd": "ls"}
        assert call_kwargs["command"]["request_id"] == "req-123"

    @pytest.mark.asyncio
    async def test_forward_missing_machine_id(
        self,
        mock_http_request: MagicMock,
    ) -> None:
        """Test forward with empty machine_id raises 400."""
        request = ForwardRequest(
            action="execute_command",
            machine_id="",  # Empty machine_id should fail
            params={"cmd": "ls"},
            request_id="req-123",
        )
        dispatch = AsyncMock()
        mock_service = _make_mock_service(dispatch)

        with pytest.raises(HTTPException) as exc_info:
            await internal_forward(
                request, mock_http_request, local_paas_service=mock_service
            )

        assert exc_info.value.status_code == 400
        assert "machine_id" in str(exc_info.value.detail)
        dispatch.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_forward_machine_not_connected(
        self,
        forward_request: ForwardRequest,
        mock_http_request: MagicMock,
    ) -> None:
        """Dispatcher returns MACHINE_NOT_CONNECTED envelope → passthrough."""
        dispatch = AsyncMock(
            return_value={
                "status": "error",
                "error": "MACHINE_NOT_CONNECTED",
                "message": "Machine machine-1 WebSocket not connected",
                "data": {"machine_id": "machine-1"},
            }
        )
        mock_service = _make_mock_service(dispatch)

        result = await internal_forward(
            forward_request, mock_http_request, local_paas_service=mock_service
        )

        assert result["status"] == "error"
        assert result["error"] == "MACHINE_NOT_CONNECTED"
        assert "machine-1" in result["message"]
        assert "data" in result

    @pytest.mark.asyncio
    async def test_forward_cross_process_uds_path(
        self,
        forward_request: ForwardRequest,
        mock_http_request: MagicMock,
    ) -> None:
        """The fix's central regression test: a forward landing on a worker
        that doesn't hold the WS connection must be UDS-forwarded to the
        sibling worker that does — and the response returned verbatim.

        Before the fix, ``internal_forward`` checked only this process's
        ``ConnectionManager`` and returned ``MACHINE_NOT_CONNECTED`` even when
        a sibling worker on the same instance held the WS. Now the dispatcher
        owns the decision; this test asserts the success envelope passes
        through end-to-end.
        """
        dispatch = AsyncMock(
            return_value={"status": "success", "data": {"out": "from-sibling"}}
        )
        mock_service = _make_mock_service(dispatch)

        result = await internal_forward(
            forward_request, mock_http_request, local_paas_service=mock_service
        )

        assert result == {"status": "success", "data": {"out": "from-sibling"}}

    @pytest.mark.asyncio
    async def test_forward_uds_envelope_error_passthrough(
        self,
        forward_request: ForwardRequest,
        mock_http_request: MagicMock,
    ) -> None:
        """UDS envelope errors (WORKER_OFFLINE) returned as-is to the source instance."""
        dispatch = AsyncMock(
            return_value={
                "status": "error",
                "error": "WORKER_OFFLINE",
                "message": "sibling worker offline",
                "data": {
                    "machine_id": "machine-1",
                    "target_worker_pid": 99999,
                    "socket_path": "/tmp/sibling.sock",
                },
            }
        )
        mock_service = _make_mock_service(dispatch)

        result = await internal_forward(
            forward_request, mock_http_request, local_paas_service=mock_service
        )

        assert result["status"] == "error"
        assert result["error"] == "WORKER_OFFLINE"
        # Sender-side routing diagnostics from the dispatcher are passed
        # through so the source instance's logs can show which worker died.
        assert result["data"]["target_worker_pid"] == 99999
        assert result["data"]["socket_path"] == "/tmp/sibling.sock"

    @pytest.mark.asyncio
    async def test_forward_timeout(
        self,
        forward_request: ForwardRequest,
        mock_http_request: MagicMock,
    ) -> None:
        """Same-process TimeoutError from dispatcher → COMMAND_TIMEOUT envelope."""
        dispatch = AsyncMock(side_effect=TimeoutError())
        mock_service = _make_mock_service(dispatch)

        result = await internal_forward(
            forward_request, mock_http_request, local_paas_service=mock_service
        )

        assert result["status"] == "error"
        assert result["error"] == "COMMAND_TIMEOUT"
        assert "data" in result and "machine_id" in result["data"]

    @pytest.mark.asyncio
    async def test_forward_connection_error(
        self,
        forward_request: ForwardRequest,
        mock_http_request: MagicMock,
    ) -> None:
        """Same-process ConnectionError from dispatcher → CONNECTION_LOST envelope."""
        dispatch = AsyncMock(side_effect=ConnectionError("Connection lost"))
        mock_service = _make_mock_service(dispatch)

        result = await internal_forward(
            forward_request, mock_http_request, local_paas_service=mock_service
        )

        assert result["status"] == "error"
        assert result["error"] == "CONNECTION_LOST"
        assert "data" in result

    @pytest.mark.asyncio
    async def test_forward_unexpected_error(
        self,
        forward_request: ForwardRequest,
        mock_http_request: MagicMock,
    ) -> None:
        """Any other exception from dispatcher → INTERNAL_ERROR envelope."""
        dispatch = AsyncMock(side_effect=RuntimeError("Something went wrong"))
        mock_service = _make_mock_service(dispatch)

        result = await internal_forward(
            forward_request, mock_http_request, local_paas_service=mock_service
        )

        assert result["status"] == "error"
        assert result["error"] == "INTERNAL_ERROR"
        assert "data" in result

    @pytest.mark.asyncio
    async def test_forward_x_request_id_header(
        self,
        forward_request: ForwardRequest,
        mock_http_request: MagicMock,
    ) -> None:
        """Test forward with X-Request-ID header (logging branch coverage)."""
        mock_http_request.headers = {"X-Request-ID": "trace-abc-123"}
        dispatch = AsyncMock(return_value={"output": "test"})
        mock_service = _make_mock_service(dispatch)

        result = await internal_forward(
            forward_request, mock_http_request, local_paas_service=mock_service
        )

        assert result == {"output": "test"}

    @pytest.mark.asyncio
    async def test_forward_request_id_propagated(
        self,
        forward_request: ForwardRequest,
        mock_http_request: MagicMock,
    ) -> None:
        """Caller's request_id reaches the dispatcher and lands in command['request_id']."""
        dispatch = AsyncMock(return_value={"output": "hello"})
        mock_service = _make_mock_service(dispatch)

        await internal_forward(
            forward_request, mock_http_request, local_paas_service=mock_service
        )

        call_kwargs = dispatch.call_args.kwargs
        assert call_kwargs["request_id"] == "req-123"
        assert call_kwargs["command"]["request_id"] == "req-123"
