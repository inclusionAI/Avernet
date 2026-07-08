"""ClaudeCodePortBase — shared plumbing for the claude_code community port impl.

Provides a lightweight WebSocket client to the vendored claude_code relay
(``ws://127.0.0.1:18900`` by default — the vendored claude_code relay's port,
not OpenClaw's 18789; override via ``CLAUDE_CODE_RELAY_URL`` /
``AICODING_RELAY_URL``), plus ``__init__`` and the per-domain client accessor
used by the mixin files.

This client is intentionally a slim re-implementation of the relay v3 protocol
(connect.challenge → connect → HelloOk; req/res/event frames). It deliberately
does NOT import ``engine.corp.transport.claude_code.*`` (corp保护区, export-excluded) — the
community transport ships in the open-source export tree and depends only on
``engine.community.kernel.frames`` + ``engine.community.openclaw.protocol`` (shared handshake types).
Wire-frame types come from ``engine.community.kernel.frames`` (RequestFrame/ResponseFrame/
EventFrame/ErrorShape) so the impl speaks the same envelope as the relay.

Token routing: the claude_code relay is single-tenant per process (one shared
client), so unlike openclaw there is no token pool here — ``token`` is accepted
on every port method for interface parity and forwarded into relay params as
``token`` when non-None, but client selection is always the single connection.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from collections.abc import AsyncGenerator
from typing import Any, Callable

import websockets

from engine.community.kernel.frames import (
    PROTOCOL_VERSION,
    EventFrame,
    RequestFrame,
    ResponseFrame,
)
from engine.community.openclaw.protocol import ClientInfo, ConnectParams, HelloOk

log = logging.getLogger("claude-code-community-port")

# claude_code_gateway Node relay defaults to PORT 18900 (see
# community/claude_code_gateway/src/server.ts). OpenClaw gateway occupies 18789 — do not
# collide with it. Override via CLAUDE_CODE_RELAY_URL / AICODING_RELAY_URL.
_DEFAULT_RELAY_URL = "ws://127.0.0.1:18900"
# Relay may emit frames up to ~16 MB for large tool_result / inline base64
# payloads (matches the relay's own ws server max_size).
_WS_MAX_SIZE = 16 * 1024 * 1024


def _resolve_relay_url() -> str:
    return (
        os.getenv("CLAUDE_CODE_RELAY_URL")
        or os.getenv("AICODING_RELAY_URL")
        or _DEFAULT_RELAY_URL
    ).strip()


class ClaudeCodeRelayClient:
    """Slim WebSocket client for the vendored claude_code relay (protocol v3).

    Lifecycle: ``await connect()`` → ``send_request`` / ``send_request_with_events``
    → ``await disconnect()``. Re-entrancy: a connected client is reusable across
    requests; chat streaming Registers a per-sessionKey event queue that the
    recv loop drains.
    """

    def __init__(
        self,
        relay_url: str | None = None,
        connection_timeout: float = 10.0,
    ) -> None:
        self._relay_url = relay_url or _resolve_relay_url()
        self._connection_timeout = connection_timeout
        self._ws: Any = None  # websockets.ClientConnection
        self._hello: HelloOk | None = None
        self._connected = False
        self._closing = False
        self._pending: dict[str, asyncio.Future[ResponseFrame]] = {}
        self._event_listeners: dict[str, list[Callable[[EventFrame], None]]] = {}
        self._stream_queues: dict[str, asyncio.Queue[EventFrame]] = {}
        self._seq = 0
        self._id = 0
        self._recv_task: asyncio.Task[None] | None = None

    # ── properties ────────────────────────────────────────────────────────

    @property
    def connected(self) -> bool:
        return self._connected and self._ws is not None

    @property
    def hello(self) -> HelloOk | None:
        return self._hello

    # ── id/seq ────────────────────────────────────────────────────────────

    def _next_id(self) -> str:
        self._seq += 1
        return str(self._seq)

    def _next_seq(self) -> int:
        self._id += 1
        return self._id

    # ── connection ────────────────────────────────────────────────────────

    async def connect(self) -> HelloOk:
        """Connect to the relay and complete the v3 handshake (no device signature)."""
        if self._connected:
            if self._hello:
                return self._hello
            raise RuntimeError("claude_code relay client: already connecting")

        self._closing = False  # reset so a reused/reconnected client tears down cleanly
        log.info("claude_code community client connecting: %s", self._relay_url)
        try:
            # ws:// (localhost relay, the default) needs no TLS. For wss:// we
            # rely on websockets' default TLS verification (system CA store) —
            # do NOT disable certificate checks. Operators with self-signed
            # certs must add the CA to their trust store.
            self._ws = await asyncio.wait_for(
                websockets.connect(self._relay_url, max_size=_WS_MAX_SIZE),
                timeout=self._connection_timeout,
            )
        except asyncio.TimeoutError as exc:
            raise ConnectionError(f"claude_code relay connect timeout: {self._relay_url}") from exc
        except Exception as exc:
            raise ConnectionError(f"claude_code relay connect failed: {self._relay_url}: {exc}") from exc

        # Relay emits a connect.challenge event first.
        try:
            raw = await asyncio.wait_for(self._ws.recv(), timeout=self._connection_timeout)
            challenge = json.loads(raw)
            if not (challenge.get("type") == "event" and challenge.get("event") == "connect.challenge"):
                log.debug("claude_code relay: unexpected first frame %s/%s, proceeding",
                          challenge.get("type"), challenge.get("event"))
        except asyncio.TimeoutError:
            await self._ws.close()
            self._ws = None
            raise ConnectionError("claude_code relay challenge timeout")

        # Send connect request (no device signature, no auth token).
        connect_params = ConnectParams(
            client=ClientInfo(id="claude-code-adapter", version="1.0.0", platform="python", mode="backend"),
            min_protocol=PROTOCOL_VERSION,
            max_protocol=PROTOCOL_VERSION,
        )
        connect_req = RequestFrame(
            id=self._next_id(),
            method="connect",
            params=connect_params.to_dict(),
        )
        await self._ws.send(json.dumps(connect_req.to_dict()))

        raw = await asyncio.wait_for(self._ws.recv(), timeout=self._connection_timeout)
        resp = json.loads(raw)
        if resp.get("type") != "res" or not resp.get("ok"):
            await self._ws.close()
            self._ws = None
            raise ConnectionError(f"claude_code relay connect rejected: {resp.get('error')}")

        self._hello = HelloOk.from_dict(resp.get("payload") or {})
        self._connected = True
        self._recv_task = asyncio.create_task(self._recv_loop(), name="claude-code-relay-recv")
        log.info("claude_code community client connected: %s", self._relay_url)
        return self._hello

    async def disconnect(self) -> None:
        if not self._ws:
            return
        self._closing = True
        try:
            await self._ws.close()
        except Exception:  # noqa: BLE001 — best-effort close
            pass
        self._ws = None
        self._connected = False
        self._hello = None
        self._fail_all_pending(ConnectionError("claude_code relay client disconnected"))
        if self._recv_task and not self._recv_task.done():
            self._recv_task.cancel()
        self._recv_task = None

    def _fail_all_pending(self, exc: Exception) -> None:
        """Reject every in-flight request future + unblock stream consumers.

        Called on disconnect and on recv-loop exit so callers don't hang until
        their own ``wait_for`` timeout when the socket drops.
        """
        for fut in list(self._pending.values()):
            if not fut.done():
                fut.set_exception(exc)
        self._pending.clear()
        for q in list(self._stream_queues.values()):
            try:
                q.put_nowait(
                    EventFrame(event="error",
                               payload={"state": "error", "error": str(exc)}))
            except Exception:  # noqa: BLE001 — best-effort unblock
                pass

    # ── recv loop ─────────────────────────────────────────────────────────

    async def _recv_loop(self) -> None:
        try:
            async for raw in self._ws:
                try:
                    data = json.loads(raw)
                except Exception:  # noqa: BLE001
                    log.warning("claude_code relay: non-JSON frame dropped")
                    continue
                ftype = data.get("type")
                if ftype == "res":
                    await self._dispatch_response(data)
                elif ftype == "event":
                    await self._dispatch_event(data)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            if not self._closing:
                log.warning("claude_code relay recv loop ended: %s", exc)
        finally:
            # Socket closed / loop ended unexpectedly: mark disconnected and
            # unblock waiters. A deliberate disconnect() sets _closing and owns
            # teardown, so skip to avoid double-failing.
            if not self._closing:
                self._connected = False
                self._fail_all_pending(
                    ConnectionError("claude_code relay connection lost"))

    async def _dispatch_response(self, data: dict[str, Any]) -> None:
        rid = data.get("id")
        fut = self._pending.pop(rid, None) if rid is not None else None
        if fut is None or fut.done():
            return
        try:
            resp = ResponseFrame.from_dict(data)
        except Exception as exc:  # noqa: BLE001
            fut.set_exception(exc)
            return
        fut.set_result(resp)

    async def _dispatch_event(self, data: dict[str, Any]) -> None:
        try:
            event = EventFrame.from_dict(data)
        except Exception:  # noqa: BLE001
            return
        # Per-sessionKey stream queue (chat_stream consumes).
        payload = data.get("payload") if isinstance(data.get("payload"), dict) else {}
        skey = payload.get("sessionKey")
        if isinstance(skey, str):
            q = self._stream_queues.get(skey)
            if q is not None:
                q.put_nowait(event)
        # Generic event listeners.
        name = data.get("event", "")
        for listener in list(self._event_listeners.get(name, [])):
            try:
                listener(event)
            except Exception:  # noqa: BLE001
                log.exception("claude_code relay event listener raised")

    # ── event listeners ───────────────────────────────────────────────────

    def on_event(self, event_name: str, listener: Callable[[EventFrame], None]) -> None:
        self._event_listeners.setdefault(event_name, []).append(listener)

    def off_event(self, event_name: str, listener: Callable[[EventFrame], None]) -> None:
        lst = self._event_listeners.get(event_name, [])
        if listener in lst:
            lst.remove(listener)

    # ── requests ──────────────────────────────────────────────────────────

    async def send_request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        timeout: float = 30.0,
    ) -> ResponseFrame:
        """Send a req frame and await the matching res frame."""
        if self._ws is None or not self._connected:
            raise ConnectionError(
                f"claude_code relay client not connected (method={method})")
        rid = self._next_id()
        req = RequestFrame(id=rid, method=method, params=params or {})
        fut: asyncio.Future[ResponseFrame] = asyncio.get_running_loop().create_future()
        self._pending[rid] = fut
        await self._ws.send(json.dumps(req.to_dict()))
        try:
            return await asyncio.wait_for(fut, timeout=timeout)
        finally:
            self._pending.pop(rid, None)

    async def send_request_with_events(
        self,
        method: str,
        params: dict[str, Any] | None,
        event_names: list[str],
        session_key: str | None,
        response_timeout: float = 30.0,
    ) -> tuple[ResponseFrame, list[EventFrame]]:
        """Send a streaming req; collect events for ``session_key`` until the res arrives.

        Registers a per-sessionKey queue + per-event listeners for ``event_names``;
        events are routed to the queue by sessionKey (or by event name for frames
        lacking a sessionKey). Returns (initial response, collected events).
        """
        if session_key:
            q: asyncio.Queue[EventFrame] = asyncio.Queue()
            self._stream_queues[session_key] = q
        collected: list[EventFrame] = []

        def listener(event: EventFrame) -> None:
            collected.append(event)

        for name in event_names:
            self.on_event(name, listener)

        try:
            resp = await self.send_request(method, params, timeout=response_timeout)
            # Drain any queued events for this session delivered before/after res.
            if session_key:
                while not q.empty():
                    collected.append(q.get_nowait())
            return resp, collected
        finally:
            for name in event_names:
                self.off_event(name, listener)
            if session_key:
                self._stream_queues.pop(session_key, None)

    async def send_request_with_id(
        self,
        request_id: str,
        method: str,
        params: dict[str, Any] | None,
        timeout: float = 30.0,
    ) -> ResponseFrame:
        """Send a req frame with a caller-chosen id; await the matching res.

        Used by relay-forwarded RPCs that need a specific request id (the
        caller's frame id) on the wire. Falls back to ``send_request`` shape.
        """
        return await self.send_request(method, params, timeout=timeout)

    async def chat_stream(
        self,
        session_key: str,
        message: str,
        timeout_ms: int | None = None,
        cwd: str | None = None,
        model: str | None = None,
        permission_mode: str | None = None,
        attachments: list[Any] | None = None,
    ) -> "AsyncGenerator[dict[str, Any], None]":
        """Send ``chat.send`` and yield raw relay event payloads until terminal.

        Mirrors ``engine.corp.transport.claude_code.client.gateway_client.ClaudeCodeGatewayClient
        .chat_stream`` (corp, read-only reference): registers a per-sessionKey
        queue, awaits the accepted ``res``, then drains the queue yielding each
        event's ``payload`` dict (with ``_source_event`` set to the top-level
        event name). Terminal states ``final`` / ``error`` / ``aborted`` end
        the generator. Transport errors propagate — the impl/adapter converts
        them to error EventFrames.
        """
        import uuid  # noqa: PLC0415

        if not self.connected or self._ws is None:
            raise ConnectionError("claude_code relay client not connected")
        if session_key in self._stream_queues:
            raise RuntimeError(
                f"Another chat_stream is already active for session {session_key}"
            )

        params: dict[str, Any] = {
            "sessionKey": session_key,
            "message": message,
            "idempotencyKey": uuid.uuid4().hex,
        }
        if timeout_ms is not None:
            params["timeoutMs"] = timeout_ms
        if cwd is not None:
            params["cwd"] = cwd
        if model is not None:
            params["model"] = model
        if permission_mode is not None:
            params["permissionMode"] = permission_mode
        if attachments:
            params["attachments"] = attachments

        q: asyncio.Queue[EventFrame] = asyncio.Queue()
        self._stream_queues[session_key] = q
        rkey = session_key  # capture for finally

        try:
            resp = await self.send_request("chat.send", params, timeout=30.0)
            if not resp.ok:
                raise RuntimeError(
                    f"chat.send rejected: {resp.error.message if resp.error else 'unknown'}"
                )
            stream_timeout = (timeout_ms / 1000.0 + 60.0) if timeout_ms else 300.0
            while True:
                try:
                    event = await asyncio.wait_for(q.get(), timeout=stream_timeout)
                except asyncio.TimeoutError:
                    log.warning(
                        "[chat_stream] idle timeout session=%s", session_key,
                    )
                    break
                payload = event.payload if isinstance(event.payload, dict) else {}
                # Mark the original top-level event name so the impl can restore it
                # (matches the corp gateway client's _source_event injection).
                payload["_source_event"] = event.event
                yield payload
                state = payload.get("state", "")
                if state in ("final", "error", "aborted"):
                    break
        finally:
            self._stream_queues.pop(rkey, None)


class ClaudeCodePortBase:
    """Shared relay plumbing for the claude_code community port impl."""

    def __init__(self, client: ClaudeCodeRelayClient | None = None) -> None:
        self._client = client

    async def _relay(self) -> ClaudeCodeRelayClient:
        """The active relay client. Lazily connects the injected client."""
        if self._client is None:
            self._client = ClaudeCodeRelayClient()
        if not self._client.connected:
            await self._client.connect()
        return self._client
