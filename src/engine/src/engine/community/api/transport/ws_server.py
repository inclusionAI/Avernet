"""
Engine-agnostic WebSocket server.

Accepts inbound WebSocket connections from the frontend, handshakes, and
dispatches frames through the plugin surface on `EngineManager`:

  - `chat.send`        → `manager.chat.stream(...)` (streams EventFrames back)
  - `chat.abort`       → `manager.chat.abort(...)`
  - `sessions.reset`   → `manager.session.reset(...)`
  - every other `req`  → `manager.relay.forward_request(...)` (501 if no relay)
  - non-`req` frames   → `manager.relay.forward_raw_frame(...)` (dropped if
                         no relay)

Connection lifecycle notifies the active engine via
`EngineManager.on_connection_open(auth) / on_connection_close(auth)` so
engine-owned bookkeeping (OpenClaw's token pool, future engines' tenant
tracking) stays behind the plugin boundary.

The server remains event-shape-agnostic: `_stream_chat_events` stamps
seq/ts on whatever `ChatService.stream` yields and relays frames verbatim.
Per-event side effects (Langfuse emission, etc.) live inside the plugin.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import time
import uuid
from typing import Any, Callable, Dict, Optional

from fastapi import WebSocket, WebSocketDisconnect

from engine.community.config import load_max_connections, load_mcp_token_settings
from engine.community.plugin_api.auth_gate.protocol import AuthGateService
from engine.community.core.chat.models import ChatAbortRequest, ChatRequest
from engine.community.core.engine.context import AuthContext
from engine.community.core.resource_references.service import ResourceReferenceService
from engine.community.manager import EngineManager
from engine.community.plugin_api.workspace_root import workspace_root_strict
from engine.community.openclaw.protocol import (
    PROTOCOL_VERSION,
    ConnectParams,
    ErrorCodes,
    ErrorShape,
    EventFrame,
    Features,
    HelloAuth,
    HelloOk,
    Policy,
    RequestFrame,
    ResponseFrame,
    ServerInfo,
)
from engine.community.shared import (
    extract_mcp_token,
    get_connection_limiter,
    get_owner_id,
    persist_mcp_token,
)

# The wire protocol (RequestFrame / ResponseFrame / EventFrame / ConnectParams
# / HelloOk, etc.) is OpenClaw-native but adopted as the standard WS envelope
# for the whole engine module. Future engines speak the same shape on this
# socket; internal translation is a plugin concern. See decision #10 in memory.
# MCP-token extraction is still OpenClaw-specific; when a second engine with
# a different auth scheme arrives, lift token extraction onto the Engine
# Protocol (`Engine.extract_auth(params, headers) -> AuthContext | None`).

from engine.community.core.session.models import SessionResetRequest  # noqa: E402
from engine.community.api.transport.auth_gate import verify_chat_send  # noqa: E402


log = logging.getLogger("engine-ws-server")
_DEBUG = os.getenv("OPENCLAW_DEBUG_EVENTS", "").lower() in {"1", "true", "yes", "on"}
_REDACTED_MATERIALIZED_FILE = "[materialized-file]"
_SESSION_FILES_PATH_MARKER = "/.teamclaw/session-files/"


def _redact_materialized_paths(value: Any, paths: tuple[str, ...]) -> Any:
    """Prevent internally resolved workspace paths from reaching WS clients."""
    if not paths:
        return value
    if isinstance(value, str):
        redacted = value
        for path in paths:
            redacted = redacted.replace(path, _REDACTED_MATERIALIZED_FILE)
        return redacted
    if isinstance(value, dict):
        return {key: _redact_materialized_paths(item, paths) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact_materialized_paths(item, paths) for item in value]
    if isinstance(value, tuple):
        return tuple(_redact_materialized_paths(item, paths) for item in value)
    return value


def _materialized_path_redaction_targets(paths: tuple[str, ...]) -> tuple[str, ...]:
    """Include each controlled session-files workspace root in WS redaction."""
    targets = set(paths)
    for path in paths:
        workspace_root, marker, _ = path.partition(_SESSION_FILES_PATH_MARKER)
        if marker and workspace_root:
            targets.add(workspace_root)
    return tuple(sorted(targets, key=len, reverse=True))


def _chat_plugin_supports_inject(chat_plugin: Any) -> bool:
    """判定 chat plugin 是否显式实现了 ``inject`` 方法.

    使用 MRO 的 ``__dict__`` 显式查找, 而不是 ``hasattr``: MagicMock 实例对任意
    属性返回 True, 直接 hasattr 会让 openclaw 路径 (无 inject) 也走新分支并失败.
    只要类层级里某一层显式声明 ``inject``, 就视为支持.
    """
    for ancestor in type(chat_plugin).__mro__:
        if "inject" in ancestor.__dict__:
            return True
    return False


def _is_openclaw_session_not_found(response: ResponseFrame) -> bool:
    if response.ok or response.error is None:
        return False
    message = response.error.message or ""
    return (
        response.error.code == ErrorCodes.INVALID_REQUEST
        and "session not found" in message.lower()
    )


def _summarize_attachments(attachments: Any) -> dict[str, Any]:
    if attachments is None:
        return {"present": False, "count": 0}
    if not isinstance(attachments, list):
        return {"present": True, "valid": False, "type": type(attachments).__name__}

    items: list[dict[str, Any]] = []
    for index, item in enumerate(attachments):
        if not isinstance(item, dict):
            items.append({"index": index, "valid": False, "type": type(item).__name__})
            continue

        content = item.get("content")
        source = item.get("source")
        source_data = source.get("data") if isinstance(source, dict) else None
        items.append(
            {
                "index": index,
                "valid": True,
                "type": item.get("type"),
                "mimeType": item.get("mimeType") or item.get("media_type"),
                "fileName": item.get("fileName") or item.get("filename"),
                "contentLength": len(content) if isinstance(content, str) else None,
                "sourceType": source.get("type") if isinstance(source, dict) else None,
                "sourceMediaType": source.get("media_type") if isinstance(source, dict) else None,
                "sourceDataLength": len(source_data) if isinstance(source_data, str) else None,
            }
        )
    return {"present": True, "valid": True, "count": len(attachments), "items": items}


class EngineWebSocketServer:
    """Engine-agnostic inbound WebSocket server.

    Per-connection state:
      - `_connections[conn_id]`: the WebSocket object (used by `broadcast_*`)
      - `_conn_auth[conn_id]`:   the AuthContext derived on handshake, reused
                                 for every plugin dispatch on this connection
                                 and handed back to the engine on disconnect.

    State is keyed by conn_id (not by token) so multiple connections with the
    same token remain distinct for message routing; the engine's own
    bookkeeping (e.g. OpenClaw's refcount) collapses them if it wants to.
    """

    def __init__(
        self,
        *,
        resource_reference_service: ResourceReferenceService | None = None,
    ) -> None:
        self._mcp_token_settings = load_mcp_token_settings()
        self._resource_reference_service = (
            resource_reference_service or ResourceReferenceService()
        )
        self._connections: Dict[str, WebSocket] = {}
        self._conn_auth: Dict[str, AuthContext] = {}
        self._session_subscribers: Dict[str, set[str]] = {}
        self._conn_sessions: Dict[str, set[str]] = {}
        self._session_materialized_redaction_paths: Dict[str, tuple[str, ...]] = {}
        self._inject_listener_refs: Dict[
            tuple[str | None, int], tuple[Any, Callable[[EventFrame], Any]]
        ] = {}
        self._inject_listener_conns: Dict[tuple[str | None, int], set[str]] = {}
        self._seq = 0
        self._version = "1.0.0"

    def _next_seq(self) -> int:
        self._seq += 1
        return self._seq

    def _auth_for(self, conn_id: Optional[str]) -> AuthContext:
        """Return the AuthContext cached for this connection.

        Unknown / missing → `AuthContext(token=None)`, which plugins map to
        their default (non-tenant-scoped) upstream.
        """
        return self._conn_auth.get(conn_id or "", AuthContext())

    # ── Top-level connection handler ───────────────────────────────────────

    async def handle_connection(
        self,
        websocket: WebSocket,
        *,
        auth_gate_service: AuthGateService,
    ) -> None:
        """Accept, handshake, and run the message loop for one client."""
        await websocket.accept()
        conn_id = uuid.uuid4().hex

        # 限流检查
        max_conn = load_max_connections()
        limiter = get_connection_limiter()
        if not await limiter.try_acquire(max_conn):
            log.warning(f"Connection rejected: limit reached (max={max_conn})")
            error = ResponseFrame.err_response(
                "0",
                ErrorShape(
                    code=ErrorCodes.UNAVAILABLE,
                    message="连接数已达上限，请稍后重试",
                    retryable=True,
                    retry_after_ms=5000,
                ),
            )
            await websocket.send_text(error.to_json())
            await websocket.close()
            return

        log.info(f"New WebSocket connection: {conn_id}")

        try:
            hello_ok = await self._handle_handshake(websocket, conn_id)
            if hello_ok is None:
                return

            self._connections[conn_id] = websocket
            await self._message_loop(websocket, conn_id, auth_gate_service=auth_gate_service)

        except WebSocketDisconnect:
            log.info(f"WebSocket disconnected: {conn_id}")
        except Exception as e:
            log.exception(f"WebSocket error: {conn_id}, {e}")
        finally:
            self._connections.pop(conn_id, None)
            await self._release_conn(conn_id)
            await limiter.release()
            log.info(f"WebSocket connection closed: {conn_id}")

    async def _release_conn(self, conn_id: str) -> None:
        """Tell the active engine this connection is gone and drop session subscriptions."""
        self._unsubscribe_conn(conn_id)
        auth = self._conn_auth.pop(conn_id, None)
        if auth is None:
            return
        try:
            await EngineManager.get_instance().on_connection_close(auth)
        except Exception as e:
            log.warning(f"on_connection_close failed for conn {conn_id}: {e}")

    # ── Handshake ──────────────────────────────────────────────────────────

    async def _handle_handshake(
        self, websocket: WebSocket, conn_id: str,
    ) -> Optional[HelloOk]:
        """Handle the initial `connect` frame and return the HelloOk payload.

        Extracts the MCP token (if any) into an AuthContext, stashes it for
        this connection, and notifies the active engine so it can register
        any per-connection resources.
        """
        try:
            raw = await asyncio.wait_for(websocket.receive_text(), timeout=10.0)
            data = json.loads(raw)

            if data.get("type") != "req" or data.get("method") != "connect":
                error = ResponseFrame.err_response(
                    data.get("id", "0"),
                    ErrorShape(ErrorCodes.INVALID_REQUEST, "First message must be 'connect'"),
                )
                await websocket.send_text(error.to_json())
                await websocket.close()
                return None

            request = RequestFrame.from_dict(data)
            request_params = request.params or {}
            params = ConnectParams.from_dict(request_params)

            # Extract auth identity from the handshake params. Today the only
            # scheme is the MCP token header; when a second engine lands with
            # a different scheme, this extraction moves onto an Engine hook.
            token = extract_mcp_token(
                request_params,
                self._mcp_token_settings.header_name,
            )
            auth = AuthContext(token=token) if token else AuthContext()
            self._conn_auth[conn_id] = auth

            if token:
                # Optional owner-scoped persistence (OpenClaw MCP-specific
                # convenience; kept here while it's the only auth scheme).
                user_id = request_params.get("user_id")
                owner_id = get_owner_id()
                if user_id and owner_id and user_id == owner_id:
                    try:
                        persist_mcp_token(token, self._mcp_token_settings)
                        log.info(f"[connect] token persisted for owner: user_id={user_id}")
                    except Exception as e:
                        log.warning(f"[connect] Persist MCP token failed: {e}")

            # Notify the engine — OpenClawEngine forwards to its token pool.
            try:
                await EngineManager.get_instance().on_connection_open(auth)
            except Exception as e:
                log.warning(f"on_connection_open failed for conn {conn_id}: {e}")

            if params.min_protocol > PROTOCOL_VERSION or params.max_protocol < PROTOCOL_VERSION:
                error = ResponseFrame.err_response(
                    request.id,
                    ErrorShape(
                        ErrorCodes.INVALID_REQUEST,
                        f"Protocol mismatch: server={PROTOCOL_VERSION}, client={params.min_protocol}-{params.max_protocol}",
                    ),
                )
                await websocket.send_text(error.to_json())
                await websocket.close()
                return None

            hello_ok = HelloOk(
                protocol=PROTOCOL_VERSION,
                server=ServerInfo(
                    version=self._version,
                    conn_id=conn_id,
                    host="openclaw-enterprise",
                ),
                features=Features(
                    methods=[
                        "chat.send",
                        "chat.abort",
                        "chat.history",
                        "sessions.list",
                        "sessions.patch",
                        "sessions.delete",
                        "sessions.reset",
                        "exec.approval.resolve",
                        "exec.approvals.get",
                        "exec.approvals.set",
                    ],
                    events=["tick", "chat", "agent", "approval.requested", "approval.resolved"],
                ),
                policy=Policy(),
                auth=HelloAuth(
                    device_token="",
                    role=params.role or "operator",
                    scopes=["operator.admin", "operator.read", "operator.write"],
                    issued_at_ms=int(time.time() * 1000),
                ),
            )

            response = ResponseFrame.ok_response(request.id, hello_ok.to_dict())
            await websocket.send_text(response.to_json())

            log.info(f"Handshake complete: {conn_id}, client={params.client.id}")
            return hello_ok

        except asyncio.TimeoutError:
            log.warning(f"Handshake timeout: {conn_id}")
            await websocket.close()
            return None
        except json.JSONDecodeError as e:
            log.warning(f"Invalid JSON in handshake: {conn_id}, {e}")
            await websocket.close()
            return None
        except Exception as e:
            log.exception(f"Handshake error: {conn_id}, {e}")
            await websocket.close()
            return None

    # ── Message loop ───────────────────────────────────────────────────────

    async def _message_loop(
        self,
        websocket: WebSocket,
        conn_id: str,
        *,
        auth_gate_service: AuthGateService,
    ) -> None:
        """Run the request/event loop until the client disconnects."""
        tick_task = asyncio.create_task(self._tick_loop(websocket, conn_id))

        try:
            while True:
                raw = await websocket.receive_text()
                try:
                    data = json.loads(raw)
                    frame_type = data.get("type")

                    if frame_type == "req":
                        request = RequestFrame.from_dict(data)
                        response, followup_events = await self._handle_request(
                            websocket, conn_id, request, auth_gate_service=auth_gate_service,
                        )
                        await websocket.send_text(response.to_json())
                        for event_name, event_payload in followup_events:
                            await self._send_event(websocket, event_name, event_payload)
                    else:
                        await self._forward_raw_frame(websocket, conn_id, data)

                except json.JSONDecodeError as e:
                    log.warning(f"Invalid JSON: {conn_id}, {e}")
                    event = EventFrame(
                        event="error",
                        payload={"message": "Invalid JSON"},
                        seq=self._next_seq(),
                    )
                    await websocket.send_text(event.to_json())

        except WebSocketDisconnect:
            raise
        except Exception as e:
            log.exception(f"Message loop error: {conn_id}, {e}")
        finally:
            tick_task.cancel()
            try:
                await tick_task
            except asyncio.CancelledError:
                pass

    async def _tick_loop(self, websocket: WebSocket, conn_id: str) -> None:
        """Send heartbeat ticks every 30s."""
        try:
            while True:
                await asyncio.sleep(30)
                event = EventFrame(
                    event="tick",
                    payload={"ts": int(time.time() * 1000)},
                    seq=self._next_seq(),
                )
                await websocket.send_text(event.to_json())
        except asyncio.CancelledError:
            pass
        except Exception as e:
            log.debug(f"Tick loop ended: {conn_id}, {e}")

    # ── Request dispatch ───────────────────────────────────────────────────

    async def _handle_request(
        self,
        websocket: WebSocket,
        conn_id: str,
        request: RequestFrame,
        *,
        auth_gate_service: AuthGateService,
    ) -> tuple[ResponseFrame, list[tuple[str, Dict[str, Any]]]]:
        """Dispatch a `req` frame to the appropriate plugin.

        Known methods (chat.send / chat.abort / sessions.reset) route to
        their dedicated plugin. Everything else falls through to
        `_forward_request` which goes via `RelayService`.
        """
        method = request.method
        params = request.params or {}

        log.debug(f"Handling request: {conn_id}, method={method}, id={request.id}")

        try:
            if method == "chat.send":
                response = await self._handle_chat_send(
                    websocket, conn_id, request, params, auth_gate_service=auth_gate_service,
                )
                return response, []
            elif method == "chat.abort":
                return await self._handle_chat_abort(conn_id, request, params)
            elif method == "chat.subscribe":
                return await self._handle_chat_subscribe(conn_id, request, params)
            elif method == "chat.unsubscribe":
                return await self._handle_chat_unsubscribe(conn_id, request, params)
            elif method == "sessions.reset":
                response = await self._handle_session_reset(conn_id, request, params)
                return response, []
            elif method == "chat.inject":
                # OpenClaw `ChatInjectParamsSchema` 严格 additionalProperties=false，
                # 会拒绝任何额外字段。前端可能透传 idempotencyKey/attachments/
                # x-iam-token 之类的 chat.send 字段，必须裁剪到 schema 接受的子集。
                allowed = {"sessionKey", "message", "label"}
                request.params = {
                    k: v for k, v in params.items() if k in allowed and v is not None
                }
                session_key = request.params.get("sessionKey")
                auto_subscription_added = False
                if session_key and request.params.get("message"):
                    # An inject event may arrive before its RPC response. Subscribe before
                    # dispatching so the originating connection cannot miss that event.
                    auto_subscription_added = conn_id not in self._session_subscribers.get(
                        session_key, set()
                    )
                    await self._subscribe_conn_to_session(conn_id, session_key)
                # 优先调用 active engine 的 chat plugin.inject (claude_code 走 RPC 透传到 relay,
                # 同时给业务层提供统一入口); 不实现 inject 的 engine (openclaw 走 gateway
                # 原生 chat.inject) 仍走 _forward_request 透传分支.
                # 判定: 类的 MRO 中显式定义了 inject 才认为支持. 用 type(...).__mro__ 配
                # __dict__ 检查, 避免 MagicMock 自动属性误触发新分支.
                # 排查日志关键字: [ws_server chat.inject]
                chat_plugin = EngineManager.get_instance().chat
                try:
                    if chat_plugin is not None and _chat_plugin_supports_inject(chat_plugin):
                        result = await self._handle_chat_inject(
                            conn_id, request, request.params
                        )
                    else:
                        result = await self._forward_chat_inject_with_session_autocreate(
                            conn_id, request
                        )
                except Exception:
                    if auto_subscription_added:
                        # Preserve an earlier successful inject subscription for this session.
                        self._unsubscribe_conn_from_session(conn_id, session_key)
                        self._drop_idle_inject_listeners()
                    raise

                if auto_subscription_added and not result[0].ok:
                    # Failed injects must not retain subscriptions created only for this request.
                    self._unsubscribe_conn_from_session(conn_id, session_key)
                    self._drop_idle_inject_listeners()
                return result
            else:
                return await self._forward_request(conn_id, request)
        except Exception as e:
            log.exception(f"Request handler error: {method}, {e}")
            return ResponseFrame.err_response(
                request.id,
                ErrorShape("INTERNAL_ERROR", str(e)),
            ), []

    async def _forward_raw_frame(
        self, websocket: WebSocket, conn_id: str, frame: Dict[str, Any],
    ) -> None:
        """Forward a non-req frame through the active engine's relay plugin.

        Engines without a relay plugin drop the frame with a warning — there
        is no generic fallback for client-originated events.
        """
        relay = EngineManager.get_instance().relay
        if relay is None:
            log.warning(
                f"Dropping raw frame (no relay plugin on active engine): "
                f"type={frame.get('type')}"
            )
            return
        try:
            await relay.forward_raw_frame(frame, auth=self._auth_for(conn_id))
        except Exception as e:
            log.exception(f"Forward raw frame error: {e}")
            await self._send_event(websocket, "error", {"message": str(e)})

    async def _forward_request(
        self, conn_id: str, request: RequestFrame,
    ) -> tuple[ResponseFrame, list[tuple[str, Dict[str, Any]]]]:
        """Forward an unknown method through the active engine's relay plugin.

        Engines without a relay plugin 501 on unknown methods — the generic
        server has no way to service them.
        """
        relay = EngineManager.get_instance().relay
        if relay is None:
            return ResponseFrame.err_response(
                request.id,
                ErrorShape(
                    "METHOD_NOT_SUPPORTED",
                    f"Method {request.method!r} not supported by active engine",
                ),
            ), []
        try:
            response = await relay.forward_request(
                request_id=request.id,
                method=request.method,
                params=request.params,
                auth=self._auth_for(conn_id),
                timeout=30.0,
            )
            return response, []
        except Exception as e:
            log.exception(f"Forward request error: {request.method}, {e}")
            return ResponseFrame.err_response(
                request.id,
                ErrorShape("INTERNAL_ERROR", str(e)),
            ), []

    async def _forward_chat_inject_with_session_autocreate(
        self, conn_id: str, request: RequestFrame,
    ) -> tuple[ResponseFrame, list[tuple[str, Dict[str, Any]]]]:
        """Forward OpenClaw chat.inject and create the session on demand.

        OpenClaw rejects chat.inject when the target session is missing. In that
        narrow case, create/update the exact key via sessions.patch and retry
        the original inject once. The session key is deliberately passed through
        unchanged; OpenClaw owns any internal key normalization.
        """
        manager = EngineManager.get_instance()
        response, events = await self._forward_request(conn_id, request)
        if manager.engine != "openclaw" or not _is_openclaw_session_not_found(response):
            return response, events

        params = request.params or {}
        session_key = params.get("sessionKey")
        if not session_key:
            return response, events

        relay = manager.relay
        if relay is None:
            return response, events

        log.info(
            "[ws_server chat.inject] session missing, creating via sessions.patch: sessionKey=%s",
            session_key,
        )
        try:
            create_response = await relay.forward_request(
                request_id=f"ensure-session-{uuid.uuid4().hex}",
                method="sessions.patch",
                params={"key": session_key},
                auth=self._auth_for(conn_id),
                timeout=30.0,
            )
        except Exception as e:
            log.exception(
                "[ws_server chat.inject] sessions.patch failed: sessionKey=%s error=%s",
                session_key,
                e,
            )
            return ResponseFrame.err_response(
                request.id,
                ErrorShape("INTERNAL_ERROR", str(e)),
            ), []

        if not create_response.ok:
            error = create_response.error or ErrorShape(
                "UNKNOWN",
                "sessions.patch failed",
            )
            log.warning(
                "[ws_server chat.inject] sessions.patch rejected: sessionKey=%s code=%s message=%s",
                session_key,
                error.code,
                error.message,
            )
            return ResponseFrame.err_response(request.id, error), []

        log.info(
            "[ws_server chat.inject] sessions.patch ok, retrying chat.inject: sessionKey=%s",
            session_key,
        )
        return await self._forward_request(conn_id, request)

    async def _send_event(
        self,
        websocket: WebSocket,
        event_name: str,
        payload: Dict[str, Any],
        *,
        materialized_paths: tuple[str, ...] = (),
    ) -> None:
        """Stamp seq/ts on an outgoing event and ship it."""
        outbound_payload = _redact_materialized_paths(payload, materialized_paths)
        if not isinstance(outbound_payload, dict):
            outbound_payload = {}
        # 先检查键是否存在，避免 _next_seq() 被无条件调用导致序列号跳号
        if "seq" not in outbound_payload:
            outbound_payload["seq"] = self._next_seq()
        if "ts" not in outbound_payload:
            outbound_payload["ts"] = int(time.time() * 1000)
        event = EventFrame(
            event=event_name,
            payload=outbound_payload,
            seq=outbound_payload["seq"],
        )
        await websocket.send_text(event.to_json())


    # ── chat.subscribe / injected event fanout ───────────────────────────────

    async def _handle_chat_subscribe(
        self,
        conn_id: str,
        request: RequestFrame,
        params: Dict[str, Any],
    ) -> tuple[ResponseFrame, list[tuple[str, Dict[str, Any]]]]:
        session_key = params.get("sessionKey")
        if not session_key:
            return ResponseFrame.err_response(
                request.id,
                ErrorShape(ErrorCodes.INVALID_REQUEST, "Missing sessionKey"),
            ), []

        live_inject = await self._subscribe_conn_to_session(conn_id, session_key)
        return ResponseFrame.ok_response(
            request.id,
            {
                "subscribed": True,
                "sessionKey": session_key,
                "liveInject": live_inject,
            },
        ), []

    async def _handle_chat_unsubscribe(
        self,
        conn_id: str,
        request: RequestFrame,
        params: Dict[str, Any],
    ) -> tuple[ResponseFrame, list[tuple[str, Dict[str, Any]]]]:
        session_key = params.get("sessionKey")
        if not session_key:
            return ResponseFrame.err_response(
                request.id,
                ErrorShape(ErrorCodes.INVALID_REQUEST, "Missing sessionKey"),
            ), []

        self._unsubscribe_conn_from_session(conn_id, session_key)
        self._drop_idle_inject_listeners()
        return ResponseFrame.ok_response(
            request.id,
            {"unsubscribed": True, "sessionKey": session_key},
        ), []

    async def _subscribe_conn_to_session(self, conn_id: str, session_key: str) -> bool:
        """Register a connection for one session and bind its live inject listener."""
        self._session_subscribers.setdefault(session_key, set()).add(conn_id)
        self._conn_sessions.setdefault(conn_id, set()).add(session_key)
        return await self._ensure_openclaw_inject_listener(conn_id)

    def _unsubscribe_conn(self, conn_id: str) -> None:
        for session_key in list(self._conn_sessions.pop(conn_id, set())):
            subscribers = self._session_subscribers.get(session_key)
            if subscribers is None:
                continue
            subscribers.discard(conn_id)
            if not subscribers:
                self._session_subscribers.pop(session_key, None)
                self._session_materialized_redaction_paths.pop(session_key, None)
        self._drop_idle_inject_listeners()

    def _unsubscribe_conn_from_session(self, conn_id: str, session_key: str) -> None:
        sessions = self._conn_sessions.get(conn_id)
        if sessions is not None:
            sessions.discard(session_key)
            if not sessions:
                self._conn_sessions.pop(conn_id, None)
        subscribers = self._session_subscribers.get(session_key)
        if subscribers is not None:
            subscribers.discard(conn_id)
            if not subscribers:
                self._session_subscribers.pop(session_key, None)
                self._session_materialized_redaction_paths.pop(session_key, None)

    async def _ensure_openclaw_inject_listener(self, conn_id: str) -> bool:
        manager = EngineManager.get_instance()
        if manager.engine != "openclaw":
            return False

        auth = self._auth_for(conn_id)
        token = auth.token
        try:
            pool = getattr(manager._require_engine(), "token_pool")
            client = await pool.get(token)
        except Exception as e:
            log.warning("chat.subscribe: failed to bind openclaw inject listener: %s", e)
            return False

        key = (token, id(client))
        self._inject_listener_conns.setdefault(key, set()).add(conn_id)
        if key in self._inject_listener_refs:
            return True

        async def on_injected_event(event: EventFrame) -> None:
            await self._fanout_injected_event(event)

        try:
            client.on_event("chat", on_injected_event)
            client.on_event("agent", on_injected_event)
        except Exception as e:
            log.warning("chat.subscribe: client.on_event failed: %s", e)
            for event_name in ("chat", "agent"):
                try:
                    client.off_event(event_name, on_injected_event)
                except Exception:
                    pass
            conns = self._inject_listener_conns.get(key)
            if conns is not None:
                conns.discard(conn_id)
                if not conns:
                    self._inject_listener_conns.pop(key, None)
            return False

        self._inject_listener_refs[key] = (client, on_injected_event)
        return True

    def _connection_has_live_inject_listener(self, conn_id: str, session_key: str) -> bool:
        if conn_id not in self._session_subscribers.get(session_key, set()):
            return False
        return any(conn_id in conns for conns in self._inject_listener_conns.values())

    async def _fanout_injected_event(self, event: EventFrame) -> None:
        payload = event.payload if isinstance(event.payload, dict) else {}
        session_key = payload.get("sessionKey")
        run_id = payload.get("runId")
        if not session_key:
            return
        if not (isinstance(run_id, str) and run_id.startswith("inject-")):
            return

        subscribers = list(self._session_subscribers.get(session_key) or [])
        if not subscribers:
            return

        send_payload = _redact_materialized_paths(
            payload,
            self._session_materialized_redaction_paths.get(session_key, ()),
        )
        if "seq" not in send_payload:
            send_payload["seq"] = self._next_seq()
        if "ts" not in send_payload:
            send_payload["ts"] = int(time.time() * 1000)
        frame = EventFrame(
            event=event.event,
            payload=send_payload,
            seq=send_payload["seq"],
        )
        raw = frame.to_json()
        stale: list[str] = []
        for subscriber_conn_id in subscribers:
            websocket = self._connections.get(subscriber_conn_id)
            if websocket is None:
                stale.append(subscriber_conn_id)
                continue
            try:
                await websocket.send_text(raw)
            except Exception as e:
                log.debug(
                    "chat.subscribe: fanout failed conn=%s: %s",
                    subscriber_conn_id,
                    e,
                )
                stale.append(subscriber_conn_id)

        for stale_conn_id in stale:
            self._unsubscribe_conn(stale_conn_id)

    def _drop_idle_inject_listeners(self) -> None:
        active_conns = set(self._conn_sessions.keys())
        for key, conns in list(self._inject_listener_conns.items()):
            conns.intersection_update(active_conns)
            if conns:
                continue

            self._inject_listener_conns.pop(key, None)
            ref = self._inject_listener_refs.pop(key, None)
            if ref is None:
                continue
            client, listener = ref
            try:
                client.off_event("chat", listener)
                client.off_event("agent", listener)
            except Exception as e:
                log.debug("chat.subscribe: off_event failed: %s", e)

    # ── chat.send ──────────────────────────────────────────────────────────

    async def _handle_chat_send(
        self,
        websocket: WebSocket,
        conn_id: str,
        request: RequestFrame,
        params: Dict[str, Any],
        *,
        auth_gate_service: AuthGateService,
    ) -> ResponseFrame:
        """Accept chat.send and spawn a background streaming task."""
        session_key = params.get("sessionKey")
        message = params.get("message")
        iam_token = params.get("x-iam-token")
        attachments = params.get("attachments")
        resource_references = params.get("resourceReferences")
        prompt_file_refs = params.get("promptFileRefs")

        if not session_key or not message:
            return ResponseFrame.err_response(
                request.id,
                ErrorShape(ErrorCodes.INVALID_REQUEST, "Missing sessionKey or message"),
            )
        # COSEC: session keys are identifiers; use a non-reversible digest in logs.
        session_key_hash = hashlib.sha256(session_key.encode("utf-8")).hexdigest()[:16]
        if attachments is not None:
            if not isinstance(attachments, list) or not all(isinstance(item, dict) for item in attachments):
                log.warning(
                    "[attachments][ws_received_invalid] requestId=%s session_key_hash=%s summary=%s",
                    request.id,
                    session_key_hash,
                    _summarize_attachments(attachments),
                )
                return ResponseFrame.err_response(
                    request.id,
                    ErrorShape(ErrorCodes.INVALID_REQUEST, "attachments must be an array of objects"),
                )
            log.info(
                "[attachments][ws_received] requestId=%s session_key_hash=%s summary=%s",
                request.id,
                session_key_hash,
                _summarize_attachments(attachments),
            )
        for field_name, value in (
            ("resourceReferences", resource_references),
            ("promptFileRefs", prompt_file_refs),
        ):
            if value is not None and (
                not isinstance(value, list)
                or not all(isinstance(item, dict) for item in value)
            ):
                return ResponseFrame.err_response(
                    request.id,
                    ErrorShape(
                        ErrorCodes.INVALID_REQUEST,
                        f"{field_name} must be an array of objects",
                    ),
                )

        auth_gate_enabled = await auth_gate_service.get_switch()
        if auth_gate_enabled:
            if not iam_token:
                return ResponseFrame.err_response(
                    request.id,
                    ErrorShape("ZERO_CHECK_FAILED", "Missing x-iam-token"),
                )

            auth_gate_result = None
            try:
                auth_gate_result = await verify_chat_send(
                    auth_gate_service=auth_gate_service,
                    session_key=session_key,
                    message=message,
                    iam_token=iam_token,
                )
            except Exception as e:
                log.warning("auth_gate exception, allowing chat.send: %s", e)

            if auth_gate_result and not auth_gate_result.allowed:
                return ResponseFrame.err_response(
                    request.id,
                    ErrorShape(
                        "ZERO_CHECK_FAILED",
                        auth_gate_result.error_message or "zero_check failed",
                    ),
                )

            if auth_gate_result and auth_gate_result.idempotency_key:
                params["idempotencyKey"] = auth_gate_result.idempotency_key
        else:
            params.setdefault("idempotencyKey", uuid.uuid4().hex)

        # The browser only sends chat.send. Bind this connection before starting
        # the stream so OpenClaw-originated inject events can reach the session.
        try:
            await self._subscribe_conn_to_session(conn_id, session_key)
        except Exception as e:
            # Listener binding is best-effort and must not reject a valid chat.send.
            log.warning("chat.send: failed to bind inject listener: %s", e)

        # ack first, then stream events in the background
        response = ResponseFrame.ok_response(request.id, {"accepted": True})

        asyncio.create_task(
            self._stream_chat_events(
                websocket,
                conn_id,
                session_key,
                message,
                params.get("timeoutMs"),
                params.get("idempotencyKey"),
                attachments=attachments,
                resource_references=resource_references,
                prompt_file_refs=prompt_file_refs,
            )
        )

        return response

    async def _stream_chat_events(
        self,
        websocket: WebSocket,
        conn_id: str,
        session_key: str,
        message: str,
        timeout_ms: Optional[int],
        idempotency_key: Optional[str] = None,
        attachments: Optional[list[dict[str, Any]]] = None,
        resource_references: Optional[list[dict[str, Any]]] = None,
        prompt_file_refs: Optional[list[dict[str, Any]]] = None,
    ) -> None:
        """Relay `ChatService.stream` events back to the client verbatim.

        Per-event side effects (Langfuse emission, intent-eval, etc.) live
        inside the plugin — the server is event-shape-agnostic on the chat
        hot path.
        """
        # COSEC: session keys are identifiers; use a non-reversible digest in logs.
        session_key_hash = hashlib.sha256(session_key.encode("utf-8")).hexdigest()[:16]
        log.info(
            "[stream] Starting chat stream: conn=%s session_key_hash=%s",
            conn_id,
            session_key_hash,
        )

        chat_plugin = EngineManager.get_instance().chat
        extra_params: dict[str, Any] = {}
        if idempotency_key:
            extra_params["idempotencyKey"] = idempotency_key
        if attachments is not None:
            extra_params["attachments"] = attachments
            log.info(
                "[attachments][chat_request_extra] conn=%s session_key_hash=%s summary=%s",
                conn_id,
                session_key_hash,
                _summarize_attachments(attachments),
            )
        if resource_references is not None:
            extra_params["resourceReferences"] = resource_references
        if prompt_file_refs is not None:
            extra_params["promptFileRefs"] = prompt_file_refs
        chat_request = ChatRequest(
            # userId / agentId are encoded inside session_key for OpenClaw;
            # the plugin forwards session_key verbatim. Empty strings match
            # the legacy relay (which also sent user_id="" to intent_eval).
            userId="",
            agentId="",
            query=message,
            sessionId=session_key,
            aliveTime=timeout_ms,
            extraParams=extra_params or None,
        )
        auth = self._auth_for(conn_id)
        materialized_paths: tuple[str, ...] = ()

        try:
            if (
                resource_references is not None
                or prompt_file_refs is not None
                or "<file-ref" in message
            ):
                # Reference validation includes controlled workspace file hashing.
                # Keep that I/O off the WebSocket event loop.
                resolved = await asyncio.to_thread(
                    self._resource_reference_service.rewrite,
                    prompt=message,
                    session_key=session_key,
                    resource_references=resource_references,
                    prompt_file_refs=prompt_file_refs,
                )
                chat_request.query = resolved.prompt
                merged_extra = dict(chat_request.extraParams or {})
                merged_extra["materializedFiles"] = resolved.materialized_files
                chat_request.extraParams = merged_extra
                materialized_paths = tuple(
                    path
                    for item in resolved.materialized_files
                    if isinstance(item, dict)
                    and isinstance(
                        path := item.get("canonical_bot_absolute_path"), str
                    )
                    and path
                )
                workspace_root = workspace_root_strict()
                if workspace_root is not None:
                    materialized_paths = (*materialized_paths, str(workspace_root))
                materialized_paths = _materialized_path_redaction_targets(
                    materialized_paths
                )
                self._session_materialized_redaction_paths[session_key] = (
                    materialized_paths
                )
                log.info(
                    "engine.resource_reference.validate session_key_hash=%s reference_count=%s ok=true",
                    session_key_hash,
                    len(resolved.materialized_files),
                )
            event_count = 0
            log.info(
                "[stream] Starting to iterate chat_plugin.stream() session_key_hash=%s",
                session_key_hash,
            )
            async for event_frame in chat_plugin.stream(chat_request, auth=auth):
                event_count += 1
                event_name = event_frame.event
                event_data = event_frame.payload
                state = event_data.get("state", "")
                run_id = event_data.get("runId", "")

                # seq/ts stamping happens inside _send_event (guarded so
                # pre-populated payloads are preserved).
                event_data.setdefault("sessionKey", session_key)

                # The live listener already fans this frame out to the current
                # connection. Avoid sending the same inject event a second time
                # through the foreground chat stream.
                if (
                    isinstance(run_id, str)
                    and run_id.startswith("inject-")
                    and self._connection_has_live_inject_listener(conn_id, session_key)
                ):
                    continue

                await self._send_event(
                    websocket,
                    event_name,
                    event_data,
                    materialized_paths=materialized_paths,
                )

                if state in ("final", "error", "aborted"):
                    if isinstance(run_id, str) and run_id.startswith("inject-"):
                        continue
                    log.info(
                        "[stream] chat stream ended: conn=%s session_key_hash=%s reason=%s total_events=%s",
                        conn_id,
                        session_key_hash,
                        state,
                        event_count,
                    )
                    break

        except WebSocketDisconnect:
            log.info(f"Client disconnected during chat stream: {conn_id}")
        except Exception as e:
            log.exception(f"Chat stream error: {conn_id}, {e}")
            try:
                await self._send_event(websocket, "chat", {
                    "sessionKey": session_key,
                    "state": "error",
                    "errorMessage": str(e),
                })
            except Exception:
                pass

    # ── chat.abort ─────────────────────────────────────────────────────────

    async def _handle_chat_abort(
        self, conn_id: str, request: RequestFrame, params: Dict[str, Any],
    ) -> tuple[ResponseFrame, list[tuple[str, Dict[str, Any]]]]:
        """Dispatch chat.abort through `ChatService.abort`.

        The plugin returns a structured result describing the outcome plus
        any follow-up `EventFrame`s the server should emit after the ack.
        """
        session_key = params.get("sessionKey")
        run_id = params.get("runId")

        if not session_key:
            return ResponseFrame.err_response(
                request.id,
                ErrorShape(ErrorCodes.INVALID_REQUEST, "Missing sessionKey"),
            ), []

        chat = EngineManager.get_instance().chat
        result = await chat.abort(
            ChatAbortRequest(session_key=session_key, run_id=run_id),
            auth=self._auth_for(conn_id),
        )

        if not result.ok:
            return ResponseFrame.err_response(
                request.id,
                result.error or ErrorShape("UNKNOWN", "Unknown error"),
            ), []

        payload = {
            "ok": True,
            "aborted": result.aborted,
            "runIds": [result.run_id] if result.aborted and result.run_id else [],
        }
        followup_events = [(ev.event, ev.payload) for ev in result.emit_events]
        return ResponseFrame.ok_response(request.id, payload), followup_events

    # ── chat.inject ────────────────────────────────────────────────────────

    async def _handle_chat_inject(
        self, conn_id: str, request: RequestFrame, params: Dict[str, Any],
    ) -> tuple[ResponseFrame, list[tuple[str, Dict[str, Any]]]]:
        """Dispatch chat.inject through ``ChatService.inject`` when the active
        engine implements it (currently claude_code). engines that don't
        implement inject are filtered out in the caller and reach the legacy
        ``_forward_request`` passthrough path (openclaw native handler).

        Returns a dict ``{ok: True, payload}`` on success or ``{ok: False, error}``.
        排查日志关键字: ``[ws_server _handle_chat_inject]``
        """
        session_key = params.get("sessionKey")
        message = params.get("message")
        label = params.get("label")

        if not session_key:
            return ResponseFrame.err_response(
                request.id,
                ErrorShape(ErrorCodes.INVALID_REQUEST, "Missing sessionKey"),
            ), []
        if not message:
            return ResponseFrame.err_response(
                request.id,
                ErrorShape(ErrorCodes.INVALID_REQUEST, "Missing message"),
            ), []

        chat = EngineManager.get_instance().chat
        try:
            result = await chat.inject(
                session_key=session_key,
                message=message,
                label=label,
                auth=self._auth_for(conn_id),
            )
        except Exception as e:
            log.exception(
                "[ws_server _handle_chat_inject] error session=%s: %s",
                session_key, e,
            )
            return ResponseFrame.err_response(
                request.id,
                ErrorShape("INTERNAL_ERROR", str(e)),
            ), []

        if not result.get("ok"):
            err = result.get("error") or {}
            return ResponseFrame.err_response(
                request.id,
                ErrorShape(
                    err.get("code", "UNKNOWN"),
                    err.get("message", "Unknown error"),
                ),
            ), []
        return ResponseFrame.ok_response(request.id, result.get("payload") or {}), []

    # ── sessions.reset ─────────────────────────────────────────────────────

    async def _handle_session_reset(
        self, conn_id: str, request: RequestFrame, params: Dict[str, Any],
    ) -> ResponseFrame:
        """Dispatch sessions.reset through `SessionService.reset`."""
        session_key = params.get("sessionKey") or params.get("key")
        if not session_key:
            return ResponseFrame.err_response(
                request.id,
                ErrorShape(ErrorCodes.INVALID_REQUEST, "Missing sessionKey"),
            )

        session = EngineManager.get_instance().session
        result = await session.reset(
            SessionResetRequest(session_key=session_key),
            auth=self._auth_for(conn_id),
        )

        if not result.ok:
            return ResponseFrame.err_response(
                request.id,
                ErrorShape(
                    result.error_code or "UNKNOWN",
                    result.error_message or "Unknown error",
                ),
            )

        return ResponseFrame.ok_response(request.id, result.payload)

    # ── Broadcast ──────────────────────────────────────────────────────────

    async def broadcast_event(self, event: str, payload: Dict[str, Any]) -> None:
        """广播事件到所有连接的客户端"""
        event_frame = EventFrame(
            event=event,
            payload=payload,
            seq=self._next_seq(),
        )
        message = event_frame.to_json()

        disconnected = []
        for conn_id, websocket in self._connections.items():
            try:
                await websocket.send_text(message)
            except Exception as e:
                log.debug(f"Failed to broadcast to {conn_id}: {e}")
                disconnected.append(conn_id)

        for conn_id in disconnected:
            self._connections.pop(conn_id, None)

    async def send_evaluation_report(
        self,
        session_id: str,
        report_data: Dict[str, Any],
    ) -> bool:
        """
        发送评测报告到前端

        通过广播方式发送，客户端根据 sessionKey 过滤

        Args:
            session_id: 会话 ID (sessionKey)
            report_data: 评测报告数据

        Returns:
            是否发送成功
        """
        log.info(f"[send_evaluation_report] Called with session_id={session_id}, connections={len(self._connections)}")
        log.debug(f"[send_evaluation_report] Connection IDs: {list(self._connections.keys())}")
        log.debug(f"[send_evaluation_report] Report data keys: {list(report_data.keys())}")

        if not self._connections:
            log.warning(f"No active connections to send evaluation report for session: {session_id}")
            return False

        # 格式化评测报告为文本
        report = report_data.get("report", {})
        validation = report_data.get("validation", {})
        feedback_actions = report_data.get("feedback_actions", [])

        # 构建评测报告文本
        text_lines = ["📊 评测报告", ""]
        text_lines.append(f"**意图类型**: {report.get('intent_type', 'N/A')}")
        text_lines.append(f"**用户消息**: {report.get('user_message', 'N/A')[:100]}...")
        text_lines.append("")

        # 验证结果
        l0 = validation.get("l0", {})
        text_lines.append(f"**L0 安全检查**: {'✅ 通过' if l0.get('passed') else '❌ 未通过'}")

        l1 = validation.get("l1")
        if l1:
            text_lines.append(f"**L1 验收标准**: {'✅ 通过' if l1.get('passed') else '❌ 未通过'} (置信度: {l1.get('confidence', 0):.0%})")

        l2 = validation.get("l2")
        if l2 and l2.get("triggered"):
            text_lines.append(f"**L2 深度验证**: {'✅ 通过' if l2.get('passed') else '❌ 未通过'}")

        text_lines.append("")
        text_lines.append(f"**总体评分**: {validation.get('overall_confidence', 0):.0%}")
        text_lines.append(f"**状态**: {validation.get('status', 'N/A')}")

        # 反馈选项
        if feedback_actions:
            text_lines.append("")
            text_lines.append("**反馈选项**:")
            for action in feedback_actions:
                text_lines.append(f"  - {action.get('label', '')} ({action.get('action_type', '')})")

        evaluation_text = "\n".join(text_lines)

        seq = self._next_seq()
        ts = int(time.time() * 1000)

        # 发送为普通的 assistant 消息，这样 SDK 可以直接渲染
        # 模仿 OpenClaw Gateway 的响应格式
        event_data = {
            "type": "event",
            "event": "assistant",  # 使用 assistant 事件类型
            "payload": {
                "text": evaluation_text,
                "sessionKey": session_id,
            },
            "seq": seq,
            "ts": ts,
        }
        message = json.dumps(event_data)

        log.info(f"[send_evaluation_report] Sending as assistant message to {len(self._connections)} connections, session: {session_id}")
        log.debug(f"[send_evaluation_report] Message preview: {message[:500]}...")

        sent_count = 0
        disconnected = []
        for conn_id, websocket in self._connections.items():
            try:
                log.info(f"[send_evaluation_report] Sending to connection: {conn_id}")
                await websocket.send_text(message)
                log.info(f"[send_evaluation_report] Successfully sent to connection: {conn_id}")
                sent_count += 1
            except Exception as e:
                log.warning(f"[send_evaluation_report] Failed to send to {conn_id}: {e}")
                disconnected.append(conn_id)

        # 清理断开的连接
        for conn_id in disconnected:
            self._connections.pop(conn_id, None)

        log.info(f"Evaluation report sent to {sent_count} connections")
        return sent_count > 0


# ── Module-level singleton ──────────────────────────────────────────────────

_server: Optional[EngineWebSocketServer] = None


def get_server() -> EngineWebSocketServer:
    """Return the process-wide `EngineWebSocketServer` singleton."""
    global _server
    if _server is None:
        _server = EngineWebSocketServer()
    return _server


def reset_server() -> None:
    """Drop the singleton — called during engine switch/restart."""
    global _server
    _server = None


# ── Extra reset callbacks (corp → community, one-way) ────────────────────────
# The community runtime imports zero corp code; corp registers its own WS-server
# reset (e.g. the AiCoding server) here at bootstrap. `EngineManager._reset_server`
# runs these across engine switch/restart without naming any corp module.
_server_reset_callbacks: list[Callable[[], None]] = []


def register_server_reset(callback: Callable[[], None]) -> None:
    """Register an extra WS-server reset callback (idempotent by identity)."""
    if callback not in _server_reset_callbacks:
        _server_reset_callbacks.append(callback)


def run_server_resets() -> None:
    """Run all registered reset callbacks, isolating individual failures."""
    for cb in _server_reset_callbacks:
        try:
            cb()
        except Exception as e:  # noqa: BLE001 — reset must be best-effort
            log.warning(f"Registered server reset failed: {e}")


__all__ = ["EngineWebSocketServer", "get_server", "reset_server"]
