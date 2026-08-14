"""Phase C tests — `api/transport/ws_server.py` plugin dispatch.

Covers the engine-agnostic server's handler translation from wire frames
to plugin calls. The server is exercised directly (not through an HTTP
test client) — every test builds a `EngineWebSocketServer`, stashes a fake
active engine on `EngineManager._instance`, and drives one handler at a
time.

What's NOT covered here:
  - FastAPI WebSocket connection lifecycle / accept + handshake IO
    (that's the same wiring for every inbound connection; the handlers are
    what Phase C actually rewrote).
  - Intent-eval observer side effects — owned by the chat plugin and
    covered in `engines/openclaw/tests/test_intent_eval_observer.py`.
"""
from __future__ import annotations

import json

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from engine.community.core.session.models import SessionResetRequest, SessionResetResult
from engine.community.core.chat.models import ChatAbortRequest, ChatAbortResult
from engine.community.core.engine.context import AuthContext
from engine.community.kernel.frames import (
    ErrorCodes,
    ErrorShape,
    EventFrame,
    RequestFrame,
    ResponseFrame,
)
from engine.community.manager import EngineManager
from engine.community.api.transport.ws_server import (
    EngineWebSocketServer,
    _is_openclaw_session_not_found,
)
from engine.community.api.transport.auth_gate import AuthGateResult
from engine.community.plugin_api.auth_gate.models import VerifyResult
from engine.community.plugins.auth_gate.noop_impl import NoopAuthGateService


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def fake_engine():
    """Stand-in Engine with every plugin slot a MagicMock.

    Wired onto EngineManager._instance so `manager.session` /
    `manager.chat` / `manager.relay` / `on_connection_*` passthroughs
    resolve to this instance.
    """
    engine = MagicMock(name="FakeActiveEngine")
    engine.session = MagicMock(name="FakeSessionService")
    engine.chat = MagicMock(name="FakeChatService")
    engine.relay = MagicMock(name="FakeRelayService")
    engine.on_connection_open = AsyncMock()
    engine.on_connection_close = AsyncMock()

    # Build a bare manager and poke the engine in, bypassing registry/init.
    EngineManager.reset_instance()
    mgr = EngineManager("fake")
    mgr._active_engine = engine
    EngineManager._instance = mgr
    yield engine
    EngineManager.reset_instance()


@pytest.fixture
def server() -> EngineWebSocketServer:
    """Fresh server with one seeded auth entry ("conn-1" → token "tok-a")."""
    s = EngineWebSocketServer()
    s._conn_auth["conn-1"] = AuthContext(token="tok-a")
    return s


class FakeAuthGateService:
    def __init__(self, *, enabled: bool = True, verify_result: VerifyResult | None = None):
        self.enabled = enabled
        self.verify_result = verify_result or VerifyResult(allowed=True)
        self.verify_calls = []

    async def get_switch(self) -> bool:
        return self.enabled

    async def set_switch(self, enabled: bool) -> None:
        self.enabled = bool(enabled)

    async def verify(self, token: str, content: str, session_id: str) -> VerifyResult:
        self.verify_calls.append({
            "token": token,
            "content": content,
            "session_id": session_id,
        })
        return self.verify_result


@pytest.fixture
def auth_gate_service() -> FakeAuthGateService:
    return FakeAuthGateService()


def _req(method: str, params: dict, id: str = "r-1") -> RequestFrame:
    return RequestFrame(id=id, method=method, params=params)


# ─────────────────────────────────────────────────────────────────────────────
# _handle_chat_send
# ─────────────────────────────────────────────────────────────────────────────


class TestHandleChatSend:
    @pytest.mark.asyncio
    async def test_auto_subscribes_session_without_client_subscribe_request(
        self, server, fake_engine, auth_gate_service,
    ):
        websocket = MagicMock()
        stream = AsyncMock()
        subscribe = AsyncMock(return_value=True)
        server._stream_chat_events = stream
        server._subscribe_conn_to_session = subscribe
        auth_gate_service.enabled = False
        params = {"sessionKey": "agent:main:user:165137", "message": "hello"}

        response = await server._handle_chat_send(
            websocket, "conn-1", _req("chat.send", params), params,
            auth_gate_service=auth_gate_service,
        )

        assert response.ok is True
        subscribe.assert_awaited_once_with("conn-1", "agent:main:user:165137")
        stream.assert_called_once()

    @pytest.mark.asyncio
    async def test_subscription_failure_does_not_reject_chat_send(
        self, server, fake_engine, auth_gate_service,
    ):
        websocket = MagicMock()
        stream = AsyncMock()
        server._stream_chat_events = stream
        server._subscribe_conn_to_session = AsyncMock(side_effect=RuntimeError("listener down"))
        auth_gate_service.enabled = False
        params = {"sessionKey": "agent:main:user:165137", "message": "hello"}

        response = await server._handle_chat_send(
            websocket, "conn-1", _req("chat.send", params), params,
            auth_gate_service=auth_gate_service,
        )

        assert response.ok is True
        stream.assert_called_once()

    @pytest.mark.asyncio
    async def test_rejects_missing_iam_token(self, server, fake_engine, auth_gate_service):
        websocket = MagicMock()
        params = {"sessionKey": "agent:main:user:165137", "message": "hello"}

        response = await server._handle_chat_send(
            websocket, "conn-1", _req("chat.send", params), params,
            auth_gate_service=auth_gate_service,
        )

        assert response.ok is False
        assert response.error.code == "ZERO_CHECK_FAILED"
        assert "x-iam-token" in response.error.message

    @pytest.mark.asyncio
    async def test_auth_gate_deny_rejects_chat_send(self, server, fake_engine, auth_gate_service, monkeypatch):
        websocket = MagicMock()
        stream = AsyncMock()
        server._stream_chat_events = stream
        monkeypatch.setattr(
            "engine.community.api.transport.ws_server.verify_chat_send",
            AsyncMock(
                return_value=AuthGateResult(
                    allowed=False,
                    error_message="not allowed",
                )
            ),
        )
        params = {
            "sessionKey": "agent:main:user:165137",
            "message": "hello",
            "x-iam-token": "iam-token",
        }

        response = await server._handle_chat_send(
            websocket, "conn-1", _req("chat.send", params), params,
            auth_gate_service=auth_gate_service,
        )

        assert response.ok is False
        assert response.error.code == "ZERO_CHECK_FAILED"
        assert response.error.message == "not allowed"
        stream.assert_not_called()

    @pytest.mark.asyncio
    async def test_auth_gate_task_id_passes_to_stream(self, server, fake_engine, auth_gate_service, monkeypatch):
        websocket = MagicMock()
        stream = AsyncMock()
        server._stream_chat_events = stream
        monkeypatch.setattr(
            "engine.community.api.transport.ws_server.verify_chat_send",
            AsyncMock(
                return_value=AuthGateResult(
                    allowed=True,
                    idempotency_key="aps_123",
                )
            ),
        )
        params = {
            "sessionKey": "agent:main:user:165137",
            "message": "hello",
            "x-iam-token": "iam-token",
        }

        response = await server._handle_chat_send(
            websocket, "conn-1", _req("chat.send", params), params,
            auth_gate_service=auth_gate_service,
        )

        assert response.ok is True
        assert params["idempotencyKey"] == "aps_123"
        stream.assert_called_once()
        assert stream.call_args.args[-1] == "aps_123"

    @pytest.mark.asyncio
    async def test_open_api_token_still_calls_auth_gate(self, server, fake_engine, auth_gate_service, monkeypatch):
        websocket = MagicMock()
        stream = AsyncMock()
        auth_gate = AsyncMock(return_value=AuthGateResult(allowed=True, idempotency_key="aps_open"))
        server._stream_chat_events = stream
        monkeypatch.setattr("engine.community.api.transport.ws_server.verify_chat_send", auth_gate)
        params = {
            "sessionKey": "agent:main:user:165137",
            "message": "hello",
            "x-iam-token": "OPEN_API:app:security_app",
        }

        response = await server._handle_chat_send(
            websocket, "conn-1", _req("chat.send", params), params,
            auth_gate_service=auth_gate_service,
        )

        assert response.ok is True
        auth_gate.assert_awaited_once_with(
            auth_gate_service=auth_gate_service,
            session_key="agent:main:user:165137",
            message="hello",
            iam_token="OPEN_API:app:security_app",
        )
        assert params["idempotencyKey"] == "aps_open"
        stream.assert_called_once()

    @pytest.mark.asyncio
    async def test_auth_gate_exception_is_fail_open(self, server, fake_engine, auth_gate_service, monkeypatch):
        """When verify_chat_send raises, chat.send proceeds (fail-open)."""
        websocket = MagicMock()
        stream = AsyncMock()
        server._stream_chat_events = stream
        monkeypatch.setattr(
            "engine.community.api.transport.ws_server.verify_chat_send",
            AsyncMock(side_effect=RuntimeError("network unreachable")),
        )
        params = {
            "sessionKey": "agent:main:user:165137",
            "message": "hello",
            "x-iam-token": "iam-token",
        }

        response = await server._handle_chat_send(
            websocket, "conn-1", _req("chat.send", params), params,
            auth_gate_service=auth_gate_service,
        )

        assert response.ok is True
        stream.assert_called_once()

    @pytest.mark.asyncio
    async def test_chat_send_forwards_attachments_to_stream(self, server, fake_engine, auth_gate_service, monkeypatch):
        websocket = MagicMock()
        stream = AsyncMock()
        server._stream_chat_events = stream
        auth_gate_service.enabled = False
        attachments = [
            {
                "type": "file",
                "mimeType": "application/pdf",
                "fileName": "brief.pdf",
                "content": "YmFzZTY0",
            }
        ]
        params = {
            "sessionKey": "agent:main:user:165137",
            "message": "看看这个文件",
            "attachments": attachments,
        }

        response = await server._handle_chat_send(
            websocket, "conn-1", _req("chat.send", params), params,
            auth_gate_service=auth_gate_service,
        )

        assert response.ok is True
        stream.assert_called_once()
        assert stream.call_args.kwargs["attachments"] == attachments

    @pytest.mark.asyncio
    async def test_chat_send_rejects_invalid_attachments(self, server, fake_engine, auth_gate_service, monkeypatch):
        websocket = MagicMock()
        stream = AsyncMock()
        server._stream_chat_events = stream
        auth_gate_service.enabled = False
        params = {
            "sessionKey": "agent:main:user:165137",
            "message": "hello",
            "attachments": {"type": "file"},
        }

        response = await server._handle_chat_send(
            websocket, "conn-1", _req("chat.send", params), params,
            auth_gate_service=auth_gate_service,
        )

        assert response.ok is False
        assert response.error.code == ErrorCodes.INVALID_REQUEST
        assert "attachments" in response.error.message
        stream.assert_not_called()

    @pytest.mark.asyncio
    async def test_auth_gate_empty_task_id_uses_uuid_fallback(self, server, fake_engine, auth_gate_service, monkeypatch):
        websocket = MagicMock()
        stream = AsyncMock()
        server._stream_chat_events = stream
        monkeypatch.setattr(
            "engine.community.api.transport.ws_server.verify_chat_send",
            AsyncMock(return_value=AuthGateResult(allowed=True)),
        )
        params = {
            "sessionKey": "agent:main:user:165137",
            "message": "hello",
            "x-iam-token": "iam-token",
        }

        response = await server._handle_chat_send(
            websocket, "conn-1", _req("chat.send", params), params,
            auth_gate_service=auth_gate_service,
        )

        assert response.ok is True
        assert "idempotencyKey" not in params
        stream.assert_called_once()
        assert stream.call_args.args[-1] is None

    @pytest.mark.asyncio
    async def test_auth_gate_disabled_allows_chat_send_without_token(
        self, server, fake_engine, auth_gate_service, monkeypatch,
    ):
        websocket = MagicMock()
        stream = AsyncMock()
        auth_gate = AsyncMock(return_value=AuthGateResult(allowed=False))
        server._stream_chat_events = stream
        auth_gate_service.enabled = False
        monkeypatch.setattr("engine.community.api.transport.ws_server.verify_chat_send", auth_gate)
        monkeypatch.setattr(
            "engine.community.api.transport.ws_server.uuid.uuid4",
            lambda: SimpleNamespace(hex="uuid-fallback"),
        )
        params = {"sessionKey": "agent:main:user:165137", "message": "hello"}

        response = await server._handle_chat_send(
            websocket, "conn-1", _req("chat.send", params), params,
            auth_gate_service=auth_gate_service,
        )

        assert response.ok is True
        assert params["idempotencyKey"] == "uuid-fallback"
        auth_gate.assert_not_called()
        stream.assert_called_once()
        assert stream.call_args.args[-1] == "uuid-fallback"

    @pytest.mark.asyncio
    async def test_auth_gate_disabled_preserves_client_idempotency_key(
        self, server, fake_engine, auth_gate_service, monkeypatch,
    ):
        websocket = MagicMock()
        stream = AsyncMock()
        server._stream_chat_events = stream
        auth_gate_service.enabled = False
        params = {
            "sessionKey": "agent:main:user:165137",
            "message": "hello",
            "idempotencyKey": "client-run",
        }

        response = await server._handle_chat_send(
            websocket, "conn-1", _req("chat.send", params), params,
            auth_gate_service=auth_gate_service,
        )

        assert response.ok is True
        assert params["idempotencyKey"] == "client-run"
        stream.assert_called_once()
        assert stream.call_args.args[-1] == "client-run"

    @pytest.mark.asyncio
    async def test_community_noop_auth_gate_allows_chat_send_without_token(
        self, server, fake_engine, monkeypatch,
    ):
        websocket = MagicMock()
        stream = AsyncMock()
        auth_gate = AsyncMock(return_value=AuthGateResult(allowed=False))
        server._stream_chat_events = stream
        monkeypatch.setattr("engine.community.api.transport.ws_server.verify_chat_send", auth_gate)
        monkeypatch.setattr(
            "engine.community.api.transport.ws_server.uuid.uuid4",
            lambda: SimpleNamespace(hex="community-noop-run"),
        )
        params = {"sessionKey": "agent:main:user:165137", "message": "hello"}

        response = await server._handle_chat_send(
            websocket, "conn-1", _req("chat.send", params), params,
            auth_gate_service=NoopAuthGateService(),
        )

        assert response.ok is True
        assert params["idempotencyKey"] == "community-noop-run"
        auth_gate.assert_not_called()
        stream.assert_called_once()
        assert stream.call_args.args[-1] == "community-noop-run"


class TestChatSendStream:
    def test_connection_without_session_subscription_has_no_live_listener(self, server):
        server._inject_listener_conns = {("tok-a", 1): {"conn-1"}}

        assert server._connection_has_live_inject_listener("conn-1", "sk-1") is False

    @pytest.mark.asyncio
    async def test_live_listener_prevents_duplicate_inject_stream_event(self, server, fake_engine):
        websocket = MagicMock()
        websocket.send_text = AsyncMock()
        server._session_subscribers = {"sk-1": {"conn-1"}}
        server._inject_listener_conns = {("tok-a", 1): {"conn-1"}}

        async def stream(*_args, **_kwargs):
            yield EventFrame(
                event="chat",
                payload={"runId": "inject-1", "state": "final"},
            )
            yield EventFrame(
                event="chat",
                payload={"runId": "chat-1", "state": "final"},
            )

        fake_engine.chat.stream = stream

        await server._stream_chat_events(
            websocket, "conn-1", "sk-1", "hello", timeout_ms=None,
        )

        websocket.send_text.assert_awaited_once()
        sent = json.loads(websocket.send_text.await_args.args[0])
        assert sent["payload"]["runId"] == "chat-1"


# ─────────────────────────────────────────────────────────────────────────────
# _handle_chat_abort
# ─────────────────────────────────────────────────────────────────────────────


class TestHandleChatAbort:
    @pytest.mark.asyncio
    async def test_rejects_missing_session_key(self, server, fake_engine):
        response, events = await server._handle_chat_abort(
            "conn-1", _req("chat.abort", {}), {},
        )
        assert response.ok is False
        assert response.error.code == ErrorCodes.INVALID_REQUEST
        assert events == []
        # Plugin never called when inputs are rejected locally.
        fake_engine.chat.abort.assert_not_called()

    @pytest.mark.asyncio
    async def test_successful_abort_returns_runids_and_followup_event(
        self, server, fake_engine,
    ):
        fake_engine.chat.abort = AsyncMock(return_value=ChatAbortResult(
            ok=True,
            aborted=True,
            run_id="r-123",
            emit_events=[EventFrame(event="chat", payload={"state": "aborted"})],
        ))

        params = {"sessionKey": "sk-1", "runId": "r-123"}
        response, events = await server._handle_chat_abort(
            "conn-1", _req("chat.abort", params), params,
        )

        assert response.ok is True
        assert response.payload == {
            "ok": True, "aborted": True, "runIds": ["r-123"],
        }
        assert events == [("chat", {"state": "aborted"})]

        # Plugin received the request and the cached AuthContext for conn-1.
        call = fake_engine.chat.abort.await_args
        assert call.args[0] == ChatAbortRequest(session_key="sk-1", run_id="r-123")
        assert call.kwargs["auth"] == AuthContext(token="tok-a")

    @pytest.mark.asyncio
    async def test_not_aborted_returns_empty_runids(self, server, fake_engine):
        fake_engine.chat.abort = AsyncMock(return_value=ChatAbortResult(
            ok=True, aborted=False, run_id="r-xx", emit_events=[],
        ))

        params = {"sessionKey": "sk-1"}
        response, events = await server._handle_chat_abort(
            "conn-1", _req("chat.abort", params), params,
        )

        assert response.ok is True
        assert response.payload["runIds"] == []
        assert events == []

    @pytest.mark.asyncio
    async def test_plugin_failure_surfaces_error_shape(self, server, fake_engine):
        fake_engine.chat.abort = AsyncMock(return_value=ChatAbortResult(
            ok=False,
            error=ErrorShape(code="NOT_FOUND", message="no such run"),
        ))

        params = {"sessionKey": "sk-1"}
        response, events = await server._handle_chat_abort(
            "conn-1", _req("chat.abort", params), params,
        )

        assert response.ok is False
        assert response.error.code == "NOT_FOUND"
        assert response.error.message == "no such run"
        assert events == []


# ─────────────────────────────────────────────────────────────────────────────
# _handle_session_reset
# ─────────────────────────────────────────────────────────────────────────────


class TestHandleSessionReset:
    @pytest.mark.asyncio
    async def test_rejects_missing_session_key(self, server, fake_engine):
        response = await server._handle_session_reset(
            "conn-1", _req("sessions.reset", {}), {},
        )
        assert response.ok is False
        assert response.error.code == ErrorCodes.INVALID_REQUEST
        fake_engine.session.reset.assert_not_called()

    @pytest.mark.asyncio
    async def test_accepts_session_key_or_key(self, server, fake_engine):
        fake_engine.session.reset = AsyncMock(
            return_value=SessionResetResult(ok=True, payload={"cleared": 1}),
        )
        # Legacy callers send `{"key": ...}`; new ones send `{"sessionKey": ...}`.
        params = {"key": "sk-1"}
        response = await server._handle_session_reset(
            "conn-1", _req("sessions.reset", params), params,
        )
        assert response.ok is True
        assert response.payload == {"cleared": 1}

    @pytest.mark.asyncio
    async def test_success_relays_payload(self, server, fake_engine):
        fake_engine.session.reset = AsyncMock(
            return_value=SessionResetResult(ok=True, payload={"cleared": 42}),
        )
        params = {"sessionKey": "sk-1"}
        response = await server._handle_session_reset(
            "conn-1", _req("sessions.reset", params), params,
        )
        assert response.ok is True
        assert response.payload == {"cleared": 42}

        # Plugin saw the request with the right key + AuthContext.
        call = fake_engine.session.reset.await_args
        assert call.args[0] == SessionResetRequest(session_key="sk-1")
        assert call.kwargs["auth"] == AuthContext(token="tok-a")

    @pytest.mark.asyncio
    async def test_failure_maps_to_err_response(self, server, fake_engine):
        fake_engine.session.reset = AsyncMock(
            return_value=SessionResetResult(
                ok=False,
                error_code="NOT_FOUND",
                error_message="no such session",
            ),
        )
        params = {"sessionKey": "sk-x"}
        response = await server._handle_session_reset(
            "conn-1", _req("sessions.reset", params), params,
        )
        assert response.ok is False
        assert response.error.code == "NOT_FOUND"
        assert response.error.message == "no such session"


# ─────────────────────────────────────────────────────────────────────────────
# _forward_request (unknown method → relay)
# ─────────────────────────────────────────────────────────────────────────────


class TestForwardRequest:
    @pytest.mark.asyncio
    async def test_501_when_engine_has_no_relay(self, server, fake_engine):
        fake_engine.relay = None  # engine opted out of passthrough
        response, events = await server._forward_request(
            "conn-1", _req("some.unknown.method", {"x": 1}),
        )
        assert response.ok is False
        assert response.error.code == "METHOD_NOT_SUPPORTED"
        assert "some.unknown.method" in response.error.message
        assert events == []

    @pytest.mark.asyncio
    async def test_relay_response_passed_through(self, server, fake_engine):
        upstream = ResponseFrame(id="r-1", ok=True, payload={"result": "ok"})
        fake_engine.relay.forward_request = AsyncMock(return_value=upstream)

        request = _req("sessions.list", {"limit": 10}, id="r-1")
        response, events = await server._forward_request("conn-1", request)

        # Server passes the ResponseFrame through verbatim.
        assert response is upstream
        assert events == []
        # Request id, method, params, and cached auth all propagate.
        call = fake_engine.relay.forward_request.await_args
        assert call.kwargs == {
            "request_id": "r-1",
            "method": "sessions.list",
            "params": {"limit": 10},
            "auth": AuthContext(token="tok-a"),
            "timeout": 30.0,
        }

    @pytest.mark.asyncio
    async def test_relay_exception_wrapped_as_internal_error(
        self, server, fake_engine,
    ):
        fake_engine.relay.forward_request = AsyncMock(
            side_effect=RuntimeError("upstream blew up"),
        )
        response, events = await server._forward_request(
            "conn-1", _req("sessions.list", {}),
        )
        assert response.ok is False
        assert response.error.code == "INTERNAL_ERROR"
        assert "upstream blew up" in response.error.message
        assert events == []


class TestOpenClawChatInjectAutocreate:
    @staticmethod
    def _set_openclaw_engine():
        EngineManager.get_instance()._engine = "openclaw"

    @pytest.mark.asyncio
    async def test_first_inject_success_does_not_create_session(self, server, fake_engine):
        self._set_openclaw_engine()
        first = ResponseFrame.ok_response("r-1", {"injected": True})
        fake_engine.relay.forward_request = AsyncMock(return_value=first)

        request = _req(
            "chat.inject",
            {
                "sessionKey": "test-openclaw-session-key",
                "message": "hello",
            },
            id="chat-inject-1",
        )
        response, events = await server._forward_chat_inject_with_session_autocreate(
            "conn-1",
            request,
        )

        assert response is first
        assert events == []
        assert fake_engine.relay.forward_request.await_count == 1
        call = fake_engine.relay.forward_request.await_args_list[0]
        assert call.kwargs["method"] == "chat.inject"
        assert call.kwargs["params"] == request.params

    @pytest.mark.asyncio
    async def test_session_not_found_creates_exact_key_then_retries(self, server, fake_engine):
        self._set_openclaw_engine()
        raw_session_key = "test-openclaw-session-key"
        not_found = ResponseFrame.err_response(
            "chat-inject-1",
            ErrorShape(ErrorCodes.INVALID_REQUEST, "session not found"),
        )
        created = ResponseFrame.ok_response("ensure-session-1", {"ok": True})
        retried = ResponseFrame.ok_response("chat-inject-1", {"injected": True})
        fake_engine.relay.forward_request = AsyncMock(
            side_effect=[not_found, created, retried],
        )

        request = _req(
            "chat.inject",
            {
                "sessionKey": raw_session_key,
                "message": "hello",
                "label": "bcs",
            },
            id="chat-inject-1",
        )
        response, events = await server._forward_chat_inject_with_session_autocreate(
            "conn-1",
            request,
        )

        assert response is retried
        assert events == []
        assert fake_engine.relay.forward_request.await_count == 3
        first, create, retry = fake_engine.relay.forward_request.await_args_list
        assert first.kwargs["request_id"] == "chat-inject-1"
        assert first.kwargs["method"] == "chat.inject"
        assert first.kwargs["params"] == request.params
        assert create.kwargs["request_id"].startswith("ensure-session-")
        assert create.kwargs["method"] == "sessions.patch"
        assert create.kwargs["params"] == {"key": raw_session_key}
        assert create.kwargs["auth"] == AuthContext(token="tok-a")
        assert retry.kwargs["request_id"] == "chat-inject-1"
        assert retry.kwargs["method"] == "chat.inject"
        assert retry.kwargs["params"] == request.params

    @pytest.mark.asyncio
    async def test_session_patch_failure_returns_patch_error_without_retry(
        self, server, fake_engine,
    ):
        self._set_openclaw_engine()
        not_found = ResponseFrame.err_response(
            "chat-inject-1",
            ErrorShape(ErrorCodes.INVALID_REQUEST, "session not found"),
        )
        patch_failed = ResponseFrame.err_response(
            "ensure-session-1",
            ErrorShape("PERMISSION_DENIED", "cannot create"),
        )
        fake_engine.relay.forward_request = AsyncMock(
            side_effect=[not_found, patch_failed],
        )

        response, events = await server._forward_chat_inject_with_session_autocreate(
            "conn-1",
            _req("chat.inject", {"sessionKey": "sk-1", "message": "hi"}, id="chat-inject-1"),
        )

        assert response is not patch_failed
        assert response.id == "chat-inject-1"
        assert response.ok is False
        assert response.error.code == "PERMISSION_DENIED"
        assert response.error.message == "cannot create"
        assert events == []
        assert fake_engine.relay.forward_request.await_count == 2

    @pytest.mark.asyncio
    async def test_retry_failure_is_returned(self, server, fake_engine):
        self._set_openclaw_engine()
        not_found = ResponseFrame.err_response(
            "chat-inject-1",
            ErrorShape(ErrorCodes.INVALID_REQUEST, "session not found"),
        )
        created = ResponseFrame.ok_response("ensure-session-1", {"ok": True})
        retry_failed = ResponseFrame.err_response(
            "chat-inject-1",
            ErrorShape("INVALID_REQUEST", "bad inject"),
        )
        fake_engine.relay.forward_request = AsyncMock(
            side_effect=[not_found, created, retry_failed],
        )

        response, events = await server._forward_chat_inject_with_session_autocreate(
            "conn-1",
            _req("chat.inject", {"sessionKey": "sk-1", "message": "hi"}, id="chat-inject-1"),
        )

        assert response is retry_failed
        assert events == []
        assert fake_engine.relay.forward_request.await_count == 3

    @pytest.mark.asyncio
    async def test_other_invalid_request_does_not_create_session(self, server, fake_engine):
        self._set_openclaw_engine()
        invalid = ResponseFrame.err_response(
            "chat-inject-1",
            ErrorShape(ErrorCodes.INVALID_REQUEST, "message required"),
        )
        fake_engine.relay.forward_request = AsyncMock(return_value=invalid)

        response, events = await server._forward_chat_inject_with_session_autocreate(
            "conn-1",
            _req("chat.inject", {"sessionKey": "sk-1", "message": "hi"}, id="chat-inject-1"),
        )

        assert response is invalid
        assert events == []
        assert fake_engine.relay.forward_request.await_count == 1

    @pytest.mark.asyncio
    async def test_non_openclaw_engine_does_not_create_session(self, server, fake_engine):
        EngineManager.get_instance()._engine = "aicoding"
        not_found = ResponseFrame.err_response(
            "chat-inject-1",
            ErrorShape(ErrorCodes.INVALID_REQUEST, "session not found"),
        )
        fake_engine.relay.forward_request = AsyncMock(return_value=not_found)

        response, events = await server._forward_chat_inject_with_session_autocreate(
            "conn-1",
            _req("chat.inject", {"sessionKey": "sk-1", "message": "hi"}, id="chat-inject-1"),
        )

        assert response is not_found
        assert events == []
        assert fake_engine.relay.forward_request.await_count == 1

    @pytest.mark.asyncio
    async def test_session_patch_exception_is_internal_error(self, server, fake_engine):
        self._set_openclaw_engine()
        not_found = ResponseFrame.err_response(
            "chat-inject-1",
            ErrorShape(ErrorCodes.INVALID_REQUEST, "session not found"),
        )
        fake_engine.relay.forward_request = AsyncMock(
            side_effect=[not_found, RuntimeError("patch exploded")],
        )

        response, events = await server._forward_chat_inject_with_session_autocreate(
            "conn-1",
            _req("chat.inject", {"sessionKey": "sk-1", "message": "hi"}, id="chat-inject-1"),
        )

        assert response.ok is False
        assert response.error.code == "INTERNAL_ERROR"
        assert "patch exploded" in response.error.message
        assert events == []


class TestOpenClawSessionNotFoundPredicate:
    def test_matches_invalid_request_session_not_found_case_insensitive(self):
        response = ResponseFrame.err_response(
            "r-1",
            ErrorShape(ErrorCodes.INVALID_REQUEST, "Session Not Found"),
        )
        assert _is_openclaw_session_not_found(response) is True

    def test_rejects_success_and_other_errors(self):
        assert _is_openclaw_session_not_found(
            ResponseFrame.ok_response("r-1", {}),
        ) is False
        assert _is_openclaw_session_not_found(
            ResponseFrame.err_response(
                "r-1",
                ErrorShape(ErrorCodes.INVALID_REQUEST, "message required"),
            ),
        ) is False
        assert _is_openclaw_session_not_found(
            ResponseFrame.err_response(
                "r-1",
                ErrorShape("NOT_FOUND", "session not found"),
            ),
        ) is False


# ─────────────────────────────────────────────────────────────────────────────
# _forward_raw_frame
# ─────────────────────────────────────────────────────────────────────────────


class TestForwardRawFrame:
    @pytest.mark.asyncio
    async def test_drops_when_engine_has_no_relay(self, server, fake_engine):
        fake_engine.relay = None
        websocket = MagicMock()
        websocket.send_text = AsyncMock()

        await server._forward_raw_frame(
            websocket, "conn-1", {"type": "event", "event": "client.ping"},
        )
        # No relay → frame silently dropped, no error event emitted.
        websocket.send_text.assert_not_called()

    @pytest.mark.asyncio
    async def test_delegates_to_relay_with_auth(self, server, fake_engine):
        fake_engine.relay.forward_raw_frame = AsyncMock()
        websocket = MagicMock()

        frame = {"type": "event", "event": "client.hint", "payload": {}}
        await server._forward_raw_frame(websocket, "conn-1", frame)

        fake_engine.relay.forward_raw_frame.assert_awaited_once_with(
            frame, auth=AuthContext(token="tok-a"),
        )

    @pytest.mark.asyncio
    async def test_relay_failure_sends_error_event(self, server, fake_engine):
        fake_engine.relay.forward_raw_frame = AsyncMock(
            side_effect=RuntimeError("gateway down"),
        )
        websocket = MagicMock()
        websocket.send_text = AsyncMock()

        await server._forward_raw_frame(
            websocket, "conn-1", {"type": "event"},
        )
        # An error EventFrame went out.
        websocket.send_text.assert_awaited_once()
        sent = websocket.send_text.await_args.args[0]
        assert "gateway down" in sent


# ─────────────────────────────────────────────────────────────────────────────
# _auth_for
# ─────────────────────────────────────────────────────────────────────────────


class TestAuthFor:
    def test_known_conn_returns_cached_auth(self, server):
        assert server._auth_for("conn-1") == AuthContext(token="tok-a")

    def test_unknown_conn_returns_empty_auth(self, server):
        assert server._auth_for("unknown") == AuthContext()

    def test_none_conn_id_returns_empty_auth(self, server):
        assert server._auth_for(None) == AuthContext()


# ─────────────────────────────────────────────────────────────────────────────
# Dispatch routing (_handle_request)
# ─────────────────────────────────────────────────────────────────────────────


class TestDispatch:
    """_handle_request is the switchboard that picks which handler runs.

    These tests patch individual handlers to verify method → handler routing
    without exercising the handler logic (covered above).
    """

    @pytest.mark.asyncio
    async def test_chat_abort_routes_to_chat_abort_handler(
        self, server, fake_engine, auth_gate_service,
    ):
        expected_response = ResponseFrame.ok_response("r-1", {"accepted": True})
        server._handle_chat_abort = AsyncMock(
            return_value=(expected_response, []),
        )

        req = _req("chat.abort", {"sessionKey": "sk-1"})
        response, events = await server._handle_request(
            websocket=MagicMock(),
            conn_id="conn-1",
            request=req,
            auth_gate_service=auth_gate_service,
        )
        assert response is expected_response
        server._handle_chat_abort.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_sessions_reset_routes_to_session_reset_handler(
        self, server, fake_engine, auth_gate_service,
    ):
        expected_response = ResponseFrame.ok_response("r-1", {})
        server._handle_session_reset = AsyncMock(return_value=expected_response)

        req = _req("sessions.reset", {"sessionKey": "sk-1"})
        response, events = await server._handle_request(
            websocket=MagicMock(),
            conn_id="conn-1",
            request=req,
            auth_gate_service=auth_gate_service,
        )
        assert response is expected_response
        assert events == []

    @pytest.mark.asyncio
    async def test_unknown_method_falls_through_to_forward_request(
        self, server, fake_engine, auth_gate_service,
    ):
        expected_response = ResponseFrame.ok_response("r-1", {})
        server._forward_request = AsyncMock(
            return_value=(expected_response, []),
        )

        # `exec.approval.resolve` used to have a dedicated handler; Phase C
        # routes it through the relay plugin like any other unknown method.
        req = _req("exec.approval.resolve", {"sessionKey": "sk", "action": "ok"})
        response, events = await server._handle_request(
            websocket=MagicMock(),
            conn_id="conn-1",
            request=req,
            auth_gate_service=auth_gate_service,
        )
        assert response is expected_response
        server._forward_request.assert_awaited_once_with("conn-1", req)

    @pytest.mark.asyncio
    async def test_handler_exception_wrapped_as_internal_error(
        self, server, fake_engine, auth_gate_service,
    ):
        server._handle_session_reset = AsyncMock(
            side_effect=RuntimeError("handler boom"),
        )
        req = _req("sessions.reset", {"sessionKey": "sk-1"})
        response, events = await server._handle_request(
            websocket=MagicMock(),
            conn_id="conn-1",
            request=req,
            auth_gate_service=auth_gate_service,
        )
        assert response.ok is False
        assert response.error.code == "INTERNAL_ERROR"
        assert "handler boom" in response.error.message

    @pytest.mark.asyncio
    async def test_chat_inject_filters_params_to_openclaw_schema(
        self, server, fake_engine, auth_gate_service,
    ):
        """openclaw `ChatInjectParamsSchema` 严格 additionalProperties=false，
        dispatch 层必须把 chat.inject 的 params 裁剪成
        {sessionKey, message, label?} 子集再透传。
        """
        expected_response = ResponseFrame.ok_response("r-1", {})
        server._forward_request = AsyncMock()
        server._forward_chat_inject_with_session_autocreate = AsyncMock(
            return_value=(expected_response, []),
        )

        req = _req("chat.inject", {
            "sessionKey": "sk-1",
            "message": "hello",
            "label": "user-pin",
            # 不在 schema 中，必须被过滤
            "idempotencyKey": "should-be-stripped",
            "attachments": [{"data": "..."}],
            "x-iam-token": "should-be-stripped",
            "randomExtra": 42,
        })
        response, events = await server._handle_request(
            websocket=MagicMock(),
            conn_id="conn-1",
            request=req,
            auth_gate_service=auth_gate_service,
        )

        assert response is expected_response
        server._forward_chat_inject_with_session_autocreate.assert_awaited_once()
        server._forward_request.assert_not_awaited()
        forwarded = server._forward_chat_inject_with_session_autocreate.await_args.args[1]
        assert forwarded.method == "chat.inject"
        assert forwarded.params == {
            "sessionKey": "sk-1",
            "message": "hello",
            "label": "user-pin",
        }


class TestChatSubscribeFanout:
    @pytest.mark.asyncio
    async def test_chat_subscribe_records_session_and_binds_openclaw_inject_listener(
        self, server, fake_engine, auth_gate_service,
    ):
        EngineManager.get_instance()._engine = "openclaw"
        client = MagicMock(name="OpenClawClient")
        client.on_event = MagicMock()
        fake_engine.token_pool.get = AsyncMock(return_value=client)

        req = _req("chat.subscribe", {"sessionKey": "sk-1"}, id="sub-1")
        response, events = await server._handle_request(
            websocket=MagicMock(),
            conn_id="conn-1",
            request=req,
            auth_gate_service=auth_gate_service,
        )

        assert response.ok is True
        assert response.payload == {
            "subscribed": True,
            "sessionKey": "sk-1",
            "liveInject": True,
        }
        assert events == []
        assert server._session_subscribers == {"sk-1": {"conn-1"}}
        assert server._conn_sessions == {"conn-1": {"sk-1"}}
        fake_engine.token_pool.get.assert_awaited_once_with("tok-a")
        assert client.on_event.call_args_list[0].args[0] == "chat"
        assert client.on_event.call_args_list[1].args[0] == "agent"

    @pytest.mark.asyncio
    async def test_inject_event_is_broadcast_only_to_subscribed_session(
        self, server, fake_engine, auth_gate_service,
    ):
        EngineManager.get_instance()._engine = "openclaw"
        client = MagicMock(name="OpenClawClient")
        client.on_event = MagicMock()
        fake_engine.token_pool.get = AsyncMock(return_value=client)
        ws1 = MagicMock()
        ws1.send_text = AsyncMock()
        ws2 = MagicMock()
        ws2.send_text = AsyncMock()
        server._connections["conn-1"] = ws1
        server._connections["conn-2"] = ws2
        server._conn_auth["conn-2"] = AuthContext(token="tok-a")

        await server._handle_request(
            websocket=ws1,
            conn_id="conn-1",
            request=_req("chat.subscribe", {"sessionKey": "sk-1"}),
            auth_gate_service=auth_gate_service,
        )
        await server._handle_request(
            websocket=ws2,
            conn_id="conn-2",
            request=_req("chat.subscribe", {"sessionKey": "sk-2"}),
            auth_gate_service=auth_gate_service,
        )
        listener = client.on_event.call_args_list[0].args[1]

        await listener(
            EventFrame(
                event="chat",
                payload={
                    "sessionKey": "sk-1",
                    "runId": "inject-abc",
                    "state": "final",
                    "message": {"content": [{"type": "text", "text": "progress"}]},
                },
            ),
        )

        ws1.send_text.assert_awaited_once()
        sent = json.loads(ws1.send_text.await_args.args[0])
        assert sent["event"] == "chat"
        assert sent["payload"]["runId"] == "inject-abc"
        assert sent["payload"]["sessionKey"] == "sk-1"
        ws2.send_text.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_non_inject_or_missing_session_event_is_not_broadcast(
        self, server, fake_engine, auth_gate_service,
    ):
        EngineManager.get_instance()._engine = "openclaw"
        client = MagicMock(name="OpenClawClient")
        client.on_event = MagicMock()
        fake_engine.token_pool.get = AsyncMock(return_value=client)
        ws = MagicMock()
        ws.send_text = AsyncMock()
        server._connections["conn-1"] = ws

        await server._handle_request(
            websocket=ws,
            conn_id="conn-1",
            request=_req("chat.subscribe", {"sessionKey": "sk-1"}),
            auth_gate_service=auth_gate_service,
        )
        listener = client.on_event.call_args_list[0].args[1]

        await listener(
            EventFrame(
                event="chat",
                payload={"sessionKey": "sk-1", "runId": "aps-abc", "state": "delta"},
            ),
        )
        await listener(
            EventFrame(
                event="chat",
                payload={"runId": "inject-abc", "state": "final"},
            ),
        )

        ws.send_text.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_release_conn_unsubscribes_and_removes_openclaw_listeners(
        self, server, fake_engine, auth_gate_service,
    ):
        EngineManager.get_instance()._engine = "openclaw"
        client = MagicMock(name="OpenClawClient")
        client.on_event = MagicMock()
        client.off_event = MagicMock()
        fake_engine.token_pool.get = AsyncMock(return_value=client)

        await server._handle_request(
            websocket=MagicMock(),
            conn_id="conn-1",
            request=_req("chat.subscribe", {"sessionKey": "sk-1"}),
            auth_gate_service=auth_gate_service,
        )

        await server._release_conn("conn-1")

        assert server._session_subscribers == {}
        assert server._conn_sessions == {}
        assert client.off_event.call_args_list[0].args[0] == "chat"
        assert client.off_event.call_args_list[1].args[0] == "agent"
        fake_engine.on_connection_close.assert_awaited_once_with(AuthContext(token="tok-a"))

    @pytest.mark.asyncio
    async def test_chat_subscribe_missing_session_key_is_invalid_request(
        self, server, fake_engine, auth_gate_service,
    ):
        response, events = await server._handle_request(
            websocket=MagicMock(),
            conn_id="conn-1",
            request=_req("chat.subscribe", {}),
            auth_gate_service=auth_gate_service,
        )

        assert response.ok is False
        assert response.error.code == ErrorCodes.INVALID_REQUEST
        assert events == []

    @pytest.mark.asyncio
    async def test_chat_unsubscribe_removes_one_session_and_returns_ok(
        self, server, fake_engine, auth_gate_service,
    ):
        EngineManager.get_instance()._engine = "openclaw"
        client = MagicMock(name="OpenClawClient")
        client.on_event = MagicMock()
        client.off_event = MagicMock()
        fake_engine.token_pool.get = AsyncMock(return_value=client)

        await server._handle_request(
            websocket=MagicMock(),
            conn_id="conn-1",
            request=_req("chat.subscribe", {"sessionKey": "sk-1"}),
            auth_gate_service=auth_gate_service,
        )
        await server._handle_request(
            websocket=MagicMock(),
            conn_id="conn-1",
            request=_req("chat.subscribe", {"sessionKey": "sk-2"}),
            auth_gate_service=auth_gate_service,
        )

        response, events = await server._handle_request(
            websocket=MagicMock(),
            conn_id="conn-1",
            request=_req("chat.unsubscribe", {"sessionKey": "sk-1"}),
            auth_gate_service=auth_gate_service,
        )

        assert response.ok is True
        assert response.payload == {"unsubscribed": True, "sessionKey": "sk-1"}
        assert events == []
        assert server._session_subscribers == {"sk-2": {"conn-1"}}
        assert server._conn_sessions == {"conn-1": {"sk-2"}}
        client.off_event.assert_not_called()

    @pytest.mark.asyncio
    async def test_chat_unsubscribe_missing_session_key_is_invalid_request(
        self, server, fake_engine, auth_gate_service,
    ):
        response, events = await server._handle_request(
            websocket=MagicMock(),
            conn_id="conn-1",
            request=_req("chat.unsubscribe", {}),
            auth_gate_service=auth_gate_service,
        )

        assert response.ok is False
        assert response.error.code == ErrorCodes.INVALID_REQUEST
        assert events == []

    @pytest.mark.asyncio
    async def test_non_openclaw_subscribe_is_safe_noop(
        self, server, fake_engine, auth_gate_service,
    ):
        EngineManager.get_instance()._engine = "hermes"

        response, events = await server._handle_request(
            websocket=MagicMock(),
            conn_id="conn-1",
            request=_req("chat.subscribe", {"sessionKey": "sk-1"}),
            auth_gate_service=auth_gate_service,
        )

        assert response.ok is True
        assert response.payload == {"subscribed": True, "sessionKey": "sk-1", "liveInject": False}
        assert events == []

    @pytest.mark.asyncio
    async def test_openclaw_listener_bind_failures_are_fail_closed(
        self, server, fake_engine, auth_gate_service,
    ):
        EngineManager.get_instance()._engine = "openclaw"
        fake_engine.token_pool.get = AsyncMock(side_effect=RuntimeError("pool down"))

        response, _events = await server._handle_request(
            websocket=MagicMock(),
            conn_id="conn-1",
            request=_req("chat.subscribe", {"sessionKey": "sk-1"}),
            auth_gate_service=auth_gate_service,
        )

        assert response.ok is True
        assert response.payload["liveInject"] is False

        client = MagicMock(name="OpenClawClient")
        client.on_event = MagicMock(side_effect=RuntimeError("bind down"))
        fake_engine.token_pool.get = AsyncMock(return_value=client)

        response, _events = await server._handle_request(
            websocket=MagicMock(),
            conn_id="conn-1",
            request=_req("chat.subscribe", {"sessionKey": "sk-2"}),
            auth_gate_service=auth_gate_service,
        )

        assert response.ok is True
        assert response.payload["liveInject"] is False

    @pytest.mark.asyncio
    async def test_reuses_existing_openclaw_inject_listener(
        self, server, fake_engine, auth_gate_service,
    ):
        EngineManager.get_instance()._engine = "openclaw"
        client = MagicMock(name="OpenClawClient")
        client.on_event = MagicMock()
        fake_engine.token_pool.get = AsyncMock(return_value=client)
        server._conn_auth["conn-2"] = AuthContext(token="tok-a")

        await server._handle_request(
            websocket=MagicMock(),
            conn_id="conn-1",
            request=_req("chat.subscribe", {"sessionKey": "sk-1"}),
            auth_gate_service=auth_gate_service,
        )
        response, _events = await server._handle_request(
            websocket=MagicMock(),
            conn_id="conn-2",
            request=_req("chat.subscribe", {"sessionKey": "sk-2"}),
            auth_gate_service=auth_gate_service,
        )

        assert response.ok is True
        assert response.payload["liveInject"] is True
        assert client.on_event.call_count == 2

    @pytest.mark.asyncio
    async def test_inject_fanout_drops_stale_or_failing_connections(
        self, server, fake_engine, auth_gate_service,
    ):
        EngineManager.get_instance()._engine = "openclaw"
        client = MagicMock(name="OpenClawClient")
        client.on_event = MagicMock()
        fake_engine.token_pool.get = AsyncMock(return_value=client)
        ws = MagicMock()
        ws.send_text = AsyncMock(side_effect=RuntimeError("closed"))
        server._connections["conn-1"] = ws
        server._conn_auth["conn-2"] = AuthContext(token="tok-a")

        await server._handle_request(
            websocket=ws,
            conn_id="conn-1",
            request=_req("chat.subscribe", {"sessionKey": "sk-1"}),
            auth_gate_service=auth_gate_service,
        )
        await server._handle_request(
            websocket=MagicMock(),
            conn_id="conn-2",
            request=_req("chat.subscribe", {"sessionKey": "sk-1"}),
            auth_gate_service=auth_gate_service,
        )
        listener = client.on_event.call_args_list[0].args[1]

        await listener(
            EventFrame(
                event="chat",
                payload={"sessionKey": "sk-1", "runId": "inject-abc"},
            ),
        )

        assert server._session_subscribers == {}
        assert server._conn_sessions == {}

    @pytest.mark.asyncio
    async def test_unsubscribe_conn_tolerates_missing_subscriber_entry(
        self, server, fake_engine, auth_gate_service,
    ):
        # _conn_sessions references a session already removed from
        # _session_subscribers (race / partial cleanup). _unsubscribe_conn
        # must `continue` past the None entry instead of crashing.
        server._conn_sessions = {"conn-1": {"sk-1"}}
        server._session_subscribers = {}  # sk-1 already gone

        server._unsubscribe_conn("conn-1")

        assert server._conn_sessions == {}
        assert server._session_subscribers == {}

    @pytest.mark.asyncio
    async def test_unsubscribe_session_purges_conn_when_last_session_removed(
        self, server, fake_engine, auth_gate_service,
    ):
        EngineManager.get_instance()._engine = "openclaw"
        client = MagicMock(name="OpenClawClient")
        client.on_event = MagicMock()
        client.off_event = MagicMock()
        fake_engine.token_pool.get = AsyncMock(return_value=client)

        # only one session -> removing it must pop the conn from _conn_sessions
        await server._handle_request(
            websocket=MagicMock(),
            conn_id="conn-1",
            request=_req("chat.subscribe", {"sessionKey": "sk-1"}),
            auth_gate_service=auth_gate_service,
        )
        await server._handle_request(
            websocket=MagicMock(),
            conn_id="conn-1",
            request=_req("chat.unsubscribe", {"sessionKey": "sk-1"}),
            auth_gate_service=auth_gate_service,
        )

        assert server._conn_sessions == {}
        assert server._session_subscribers == {}

    @pytest.mark.asyncio
    async def test_fanout_injected_event_no_subscribers_returns_early(
        self, server, fake_engine, auth_gate_service,
    ):
        EngineManager.get_instance()._engine = "openclaw"
        client = MagicMock(name="OpenClawClient")
        client.on_event = MagicMock()
        fake_engine.token_pool.get = AsyncMock(return_value=client)
        ws = MagicMock()
        ws.send_text = AsyncMock()
        server._connections["conn-1"] = ws

        # subscribe sk-1, bind listener, then drop subscribers so the listener
        # callback hits an empty subscriber set.
        await server._handle_request(
            websocket=ws,
            conn_id="conn-1",
            request=_req("chat.subscribe", {"sessionKey": "sk-1"}),
            auth_gate_service=auth_gate_service,
        )
        listener = client.on_event.call_args_list[0].args[1]
        server._session_subscribers.clear()

        await listener(
            EventFrame(
                event="chat",
                payload={"sessionKey": "sk-1", "runId": "inject-abc"},
            ),
        )

        ws.send_text.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_fanout_injected_event_redacts_materialized_workspace_paths(
        self, server,
    ):
        ws = MagicMock()
        ws.send_text = AsyncMock()
        server._connections["conn-1"] = ws
        server._session_subscribers = {"sk-1": {"conn-1"}}
        server._conn_materialized_redaction_paths["conn-1"] = (
            "/bot/work/.teamclaw/session-files/a.txt",
            "/bot/work",
        )

        await server._fanout_injected_event(
            EventFrame(
                event="agent",
                payload={
                    "sessionKey": "sk-1",
                    "runId": "inject-abc",
                    "cwd": "/bot/work",
                    "command": "sed -n 1p /bot/work/.teamclaw/session-files/a.txt",
                },
            )
        )

        outbound = ws.send_text.await_args.args[0]
        assert "/bot/work" not in outbound
        assert "[materialized-file]" in outbound

    @pytest.mark.asyncio
    async def test_drop_idle_inject_listeners_tolerates_off_event_failure(
        self, server, fake_engine, auth_gate_service,
    ):
        EngineManager.get_instance()._engine = "openclaw"
        client = MagicMock(name="OpenClawClient")
        client.on_event = MagicMock()
        client.off_event = MagicMock(side_effect=RuntimeError("off boom"))
        fake_engine.token_pool.get = AsyncMock(return_value=client)

        await server._handle_request(
            websocket=MagicMock(),
            conn_id="conn-1",
            request=_req("chat.subscribe", {"sessionKey": "sk-1"}),
            auth_gate_service=auth_gate_service,
        )

        # Releasing the conn triggers _drop_idle_inject_listeners, which must
        # swallow the off_event exception and still clear the refs.
        await server._release_conn("conn-1")

        assert server._inject_listener_refs == {}
        assert server._session_subscribers == {}

    @pytest.mark.asyncio
    async def test_drop_idle_inject_listeners_retains_active_listeners_and_drops_idle_ones(
        self, server, fake_engine, auth_gate_service,
    ):
        EngineManager.get_instance()._engine = "openclaw"
        client1 = MagicMock(name="OpenClawClient1")
        client1.on_event = MagicMock()
        client1.off_event = MagicMock()
        client2 = MagicMock(name="OpenClawClient2")
        client2.on_event = MagicMock()
        client2.off_event = MagicMock()

        async def mock_get(token):
            return client1 if token == "tok-a" else client2

        fake_engine.token_pool.get = AsyncMock(side_effect=mock_get)
        server._conn_auth["conn-1"] = AuthContext(token="tok-a")
        server._conn_auth["conn-2"] = AuthContext(token="tok-b")

        await server._handle_request(
            websocket=MagicMock(),
            conn_id="conn-1",
            request=_req("chat.subscribe", {"sessionKey": "sk-1"}),
            auth_gate_service=auth_gate_service,
        )
        await server._handle_request(
            websocket=MagicMock(),
            conn_id="conn-2",
            request=_req("chat.subscribe", {"sessionKey": "sk-2"}),
            auth_gate_service=auth_gate_service,
        )

        await server._release_conn("conn-1")

        assert client1.off_event.call_count == 2
        assert [call.args[0] for call in client1.off_event.call_args_list] == [
            "chat",
            "agent",
        ]
        client2.off_event.assert_not_called()
        assert ("tok-a", id(client1)) not in server._inject_listener_refs
        assert ("tok-a", id(client1)) not in server._inject_listener_conns
        assert ("tok-b", id(client2)) in server._inject_listener_refs
        assert server._inject_listener_conns[("tok-b", id(client2))] == {"conn-2"}
        assert server._session_subscribers == {"sk-2": {"conn-2"}}

    @pytest.mark.asyncio
    async def test_subscribe_cleans_partial_listener_when_on_event_fails(
        self, server, fake_engine, auth_gate_service,
    ):
        EngineManager.get_instance()._engine = "openclaw"
        client = MagicMock(name="OpenClawClient")
        client.on_event = MagicMock(side_effect=[None, RuntimeError("on boom")])
        client.off_event = MagicMock()
        fake_engine.token_pool.get = AsyncMock(return_value=client)
        server._conn_auth["conn-1"] = AuthContext(token="tok-a")

        response, _events = await server._handle_request(
            websocket=MagicMock(),
            conn_id="conn-1",
            request=_req("chat.subscribe", {"sessionKey": "sk-1"}),
            auth_gate_service=auth_gate_service,
        )

        assert response.ok is True
        assert response.payload == {
            "subscribed": True,
            "sessionKey": "sk-1",
            "liveInject": False,
        }
        assert [call.args[0] for call in client.off_event.call_args_list] == [
            "chat",
            "agent",
        ]
        assert ("tok-a", id(client)) not in server._inject_listener_refs
        assert ("tok-a", id(client)) not in server._inject_listener_conns
        assert server._session_subscribers == {"sk-1": {"conn-1"}}
        assert server._conn_sessions == {"conn-1": {"sk-1"}}

    @pytest.mark.asyncio
    async def test_subscribe_ignores_cleanup_error_after_partial_on_event_failure(
        self, server, fake_engine, auth_gate_service,
    ):
        EngineManager.get_instance()._engine = "openclaw"
        client = MagicMock(name="OpenClawClient")
        client.on_event = MagicMock(side_effect=[None, RuntimeError("on boom")])
        client.off_event = MagicMock(side_effect=RuntimeError("off boom"))
        fake_engine.token_pool.get = AsyncMock(return_value=client)
        server._conn_auth["conn-1"] = AuthContext(token="tok-a")

        response, _events = await server._handle_request(
            websocket=MagicMock(),
            conn_id="conn-1",
            request=_req("chat.subscribe", {"sessionKey": "sk-1"}),
            auth_gate_service=auth_gate_service,
        )

        assert response.ok is True
        assert response.payload["liveInject"] is False
        assert client.off_event.call_count == 2
        assert ("tok-a", id(client)) not in server._inject_listener_refs
        assert ("tok-a", id(client)) not in server._inject_listener_conns

    def test_drop_idle_inject_listeners_removes_orphan_conn_mapping_without_ref(
        self, server,
    ):
        server._inject_listener_conns[("tok-a", 123)] = {"conn-1"}
        server._conn_sessions = {}

        server._drop_idle_inject_listeners()

        assert server._inject_listener_conns == {}
        assert server._inject_listener_refs == {}


class _FakeChatServiceWithInject:
    """Stand-in chat service whose class explicitly declares ``inject``.

    Used by chat.inject dispatch tests — the production helper
    ``_chat_plugin_supports_inject`` only recognises ``inject`` defined on a
    class in the MRO (not auto-spawned ``MagicMock`` attributes), so a plain
    ``MagicMock(name="FakeChatService")`` won't trigger the new branch.
    """

    def __init__(self, *, return_value=None, exc=None):
        self._return_value = return_value or {"ok": True, "payload": {}}
        self._exc = exc
        self.calls = []

    async def inject(self, *, session_key, message, label=None, auth=None):
        self.calls.append({
            "session_key": session_key,
            "message": message,
            "label": label,
            "auth": auth,
        })
        if self._exc is not None:
            raise self._exc
        return self._return_value


class TestChatInjectDispatch:
    """覆盖 chat.inject 分支调用 ChatService.inject 的成功 / 失败 / 兜底路径."""

    @pytest.mark.asyncio
    async def test_inject_auto_subscribes_connection_before_dispatch(
        self, server, fake_engine, auth_gate_service,
    ):
        fake_chat = _FakeChatServiceWithInject()
        bind_listener = AsyncMock(return_value=True)
        server._ensure_openclaw_inject_listener = bind_listener

        async def inject(**_kwargs):
            assert server._session_subscribers == {"sk-1": {"conn-1"}}
            assert server._conn_sessions == {"conn-1": {"sk-1"}}
            bind_listener.assert_awaited_once_with("conn-1")
            return {"ok": True, "payload": {"injected": True}}

        fake_chat.inject = inject
        fake_engine.chat = fake_chat

        response, events = await server._handle_request(
            websocket=MagicMock(),
            conn_id="conn-1",
            request=_req("chat.inject", {"sessionKey": "sk-1", "message": "hello"}),
            auth_gate_service=auth_gate_service,
        )

        assert response.ok is True
        assert events == []

    @pytest.mark.asyncio
    async def test_openclaw_inject_auto_binds_live_event_listener(
        self, server, fake_engine, auth_gate_service,
    ):
        EngineManager.get_instance()._engine = "openclaw"
        client = MagicMock(name="OpenClawClient")
        client.on_event = MagicMock()
        fake_engine.token_pool.get = AsyncMock(return_value=client)
        fake_engine.relay.forward_request = AsyncMock(
            return_value=ResponseFrame.ok_response("r-1", {"injected": True}),
        )

        response, events = await server._handle_request(
            websocket=MagicMock(),
            conn_id="conn-1",
            request=_req("chat.inject", {"sessionKey": "sk-1", "message": "hello"}),
            auth_gate_service=auth_gate_service,
        )

        assert response.ok is True
        assert events == []
        assert server._session_subscribers == {"sk-1": {"conn-1"}}
        assert server._conn_sessions == {"conn-1": {"sk-1"}}
        assert [call.args[0] for call in client.on_event.call_args_list] == [
            "chat",
            "agent",
        ]

    @pytest.mark.asyncio
    async def test_invalid_openclaw_inject_keeps_legacy_forwarding_without_subscription(
        self, server, fake_engine, auth_gate_service,
    ):
        EngineManager.get_instance()._engine = "openclaw"
        expected_response = ResponseFrame.ok_response("r-1", {})
        server._forward_chat_inject_with_session_autocreate = AsyncMock(
            return_value=(expected_response, []),
        )

        request = _req("chat.inject", {"message": "hello"})
        response, events = await server._handle_request(
            websocket=MagicMock(),
            conn_id="conn-1",
            request=request,
            auth_gate_service=auth_gate_service,
        )

        assert response is expected_response
        assert events == []
        server._forward_chat_inject_with_session_autocreate.assert_awaited_once_with(
            "conn-1", request,
        )
        assert server._session_subscribers == {}
        assert server._conn_sessions == {}

    @pytest.mark.asyncio
    async def test_inject_success_forwards_to_service(self, server, fake_engine, auth_gate_service):
        fake_chat = _FakeChatServiceWithInject(
            return_value={"ok": True, "payload": {"injected": True}},
        )
        fake_engine.chat = fake_chat

        req = _req("chat.inject", {
            "sessionKey": "sk-1",
            "message": "hello",
            "label": "user-pin",
            # 多余字段需要被裁剪掉, 不传给 service
            "idempotencyKey": "stripped",
        })
        response, events = await server._handle_request(
            websocket=MagicMock(),
            conn_id="conn-1",
            request=req,
            auth_gate_service=auth_gate_service,
        )

        assert response.ok is True
        assert response.payload == {"injected": True}
        assert events == []
        assert len(fake_chat.calls) == 1
        call = fake_chat.calls[0]
        assert call["session_key"] == "sk-1"
        assert call["message"] == "hello"
        assert call["label"] == "user-pin"

    @pytest.mark.asyncio
    async def test_inject_missing_session_key_returns_invalid_request(
        self, server, fake_engine, auth_gate_service,
    ):
        fake_engine.chat = _FakeChatServiceWithInject()

        req = _req("chat.inject", {"message": "hello"})
        response, events = await server._handle_request(
            websocket=MagicMock(),
            conn_id="conn-1",
            request=req,
            auth_gate_service=auth_gate_service,
        )

        assert response.ok is False
        assert response.error is not None
        # 缺 sessionKey: dispatch 层立即拒绝, service 不应被调到
        assert fake_engine.chat.calls == []
        assert server._session_subscribers == {}
        assert server._conn_sessions == {}

    @pytest.mark.asyncio
    async def test_inject_missing_message_returns_invalid_request(
        self, server, fake_engine, auth_gate_service,
    ):
        fake_engine.chat = _FakeChatServiceWithInject()

        req = _req("chat.inject", {"sessionKey": "sk-1"})
        response, events = await server._handle_request(
            websocket=MagicMock(),
            conn_id="conn-1",
            request=req,
            auth_gate_service=auth_gate_service,
        )

        assert response.ok is False
        assert response.error is not None
        assert fake_engine.chat.calls == []

    @pytest.mark.asyncio
    async def test_inject_service_returns_error_dict_surfaces_error_shape(
        self, server, fake_engine, auth_gate_service,
    ):
        fake_engine.chat = _FakeChatServiceWithInject(return_value={
            "ok": False,
            "error": {"code": "METHOD_NOT_SUPPORTED", "message": "no handler"},
        })

        req = _req("chat.inject", {"sessionKey": "sk-1", "message": "hi"})
        response, events = await server._handle_request(
            websocket=MagicMock(),
            conn_id="conn-1",
            request=req,
            auth_gate_service=auth_gate_service,
        )

        assert response.ok is False
        assert response.error.code == "METHOD_NOT_SUPPORTED"
        assert "no handler" in response.error.message
        assert server._session_subscribers == {}
        assert server._conn_sessions == {}

    @pytest.mark.asyncio
    async def test_inject_failure_keeps_existing_auto_subscription(
        self, server, fake_engine, auth_gate_service,
    ):
        fake_engine.chat = _FakeChatServiceWithInject(return_value={
            "ok": False,
            "error": {"code": "METHOD_NOT_SUPPORTED", "message": "no handler"},
        })
        server._session_subscribers = {"sk-1": {"conn-1"}}
        server._conn_sessions = {"conn-1": {"sk-1"}}

        response, _events = await server._handle_request(
            websocket=MagicMock(),
            conn_id="conn-1",
            request=_req("chat.inject", {"sessionKey": "sk-1", "message": "hi"}),
            auth_gate_service=auth_gate_service,
        )

        assert response.ok is False
        assert server._session_subscribers == {"sk-1": {"conn-1"}}
        assert server._conn_sessions == {"conn-1": {"sk-1"}}

    @pytest.mark.asyncio
    async def test_inject_dispatch_exception_removes_new_auto_subscription(
        self, server, fake_engine, auth_gate_service,
    ):
        EngineManager.get_instance()._engine = "openclaw"
        server._forward_chat_inject_with_session_autocreate = AsyncMock(
            side_effect=RuntimeError("relay down"),
        )

        response, _events = await server._handle_request(
            websocket=MagicMock(),
            conn_id="conn-1",
            request=_req("chat.inject", {"sessionKey": "sk-1", "message": "hi"}),
            auth_gate_service=auth_gate_service,
        )

        assert response.ok is False
        assert response.error.code == "INTERNAL_ERROR"
        assert server._session_subscribers == {}
        assert server._conn_sessions == {}

    @pytest.mark.asyncio
    async def test_inject_service_exception_surfaces_internal_error(
        self, server, fake_engine, auth_gate_service,
    ):
        fake_engine.chat = _FakeChatServiceWithInject(exc=RuntimeError("boom"))

        req = _req("chat.inject", {"sessionKey": "sk-1", "message": "hi"})
        response, events = await server._handle_request(
            websocket=MagicMock(),
            conn_id="conn-1",
            request=req,
            auth_gate_service=auth_gate_service,
        )

        assert response.ok is False
        assert response.error.code == "INTERNAL_ERROR"
