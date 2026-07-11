#!/usr/bin/env python3
"""Standalone bridge between BCN protocol v2 and Hermes Dashboard JSON-RPC."""

from __future__ import annotations

import argparse
import asyncio
import fcntl
import inspect
import json
import logging
import os
import secrets
import shlex
import signal
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import uuid
from collections import defaultdict
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen

try:
    from websockets.asyncio.client import connect as ws_connect
except ModuleNotFoundError:
    ws_connect = None


LOGGER = logging.getLogger("hermes_bcn")
_SECRET_KEYS = frozenset(
    {
        "api_key",
        "authorization",
        "bot_token",
        "dashboard_token",
        "human_token",
        "token",
    }
)
_TERMINAL_HERMES_EVENTS = frozenset({"message.complete", "error"})
_OBSERVATION_LIMIT = 256
_OBSERVATION_BYTES = 64 * 1024
_DEFAULT_BCS_ENDPOINT = "http://127.0.0.1:21000"
_DEFAULT_BCS_WS_URL = "ws://127.0.0.1:21000/ws/bot"
_CONNECTOR_VERSION = "1"


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): "[REDACTED]" if str(key).lower() in _SECRET_KEYS else _redact(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_redact(item) for item in value)
    return value


def _log(level: int, event: str, **fields: Any) -> None:
    record = {"event": event, **_redact(fields)}
    LOGGER.log(level, "%s", json.dumps(record, ensure_ascii=True, sort_keys=True))


async def _open_websocket(
    uri: str, connector: Callable[..., Any] | None = None
) -> Any:
    """Disable environment proxies on websockets 15 without breaking 14.x."""

    connector = connector or ws_connect
    if connector is None:
        raise RuntimeError("websockets>=14,<16 is required to run the connector")
    try:
        supports_proxy = "proxy" in inspect.signature(connector).parameters
    except (TypeError, ValueError):
        supports_proxy = False
    kwargs = {"proxy": None} if supports_proxy else {}
    return await connector(uri, **kwargs)


class AtomicJsonStore:
    """Small JSON store using same-directory atomic replacement and mode 0600."""

    def __init__(self, path: str | os.PathLike[str]) -> None:
        self.path = Path(path).expanduser()

    def load(self, default: Any | None = None) -> Any:
        if not self.path.exists():
            return {} if default is None else default
        try:
            with self.path.open("r", encoding="utf-8") as handle:
                return json.load(handle)
        except (OSError, json.JSONDecodeError) as exc:
            _log(
                logging.WARNING,
                "json_store_load_failed",
                path=str(self.path),
                error_type=type(exc).__name__,
            )
            return {} if default is None else default

    def save(self, value: Any) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd, raw_path = tempfile.mkstemp(
            prefix=f".{self.path.name}.", suffix=".tmp", dir=self.path.parent
        )
        temp_path = Path(raw_path)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                fd = -1
                json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, self.path)
            self.path.chmod(0o600)
        finally:
            if fd >= 0:
                os.close(fd)
            try:
                temp_path.unlink()
            except FileNotFoundError:
                pass


class HermesRpcError(RuntimeError):
    def __init__(self, code: int | str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class _StreamFailure:
    error: BaseException


class HermesEventStream(AsyncIterator[dict[str, Any]]):
    def __init__(
        self,
        client: "HermesClient",
        session_id: str,
        queue: asyncio.Queue[Any],
    ) -> None:
        self._client = client
        self._session_id = session_id
        self._queue = queue
        self._closed = False

    def __aiter__(self) -> "HermesEventStream":
        return self

    async def __anext__(self) -> dict[str, Any]:
        if self._closed:
            raise StopAsyncIteration
        item = await self._queue.get()
        if isinstance(item, _StreamFailure):
            self.close()
            raise item.error
        if not isinstance(item, dict):
            return await self.__anext__()
        if item.get("type") in _TERMINAL_HERMES_EVENTS:
            self.close()
        return item

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            self._client._remove_stream(self._session_id, self._queue)


class HermesClient:
    """Multiplexed JSON-RPC client for a Hermes Dashboard WebSocket."""

    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        request_timeout: float = 120.0,
    ) -> None:
        self._base_url = base_url
        self._token = token
        self._request_timeout = request_timeout
        self._websocket: Any | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self._connect_lock = asyncio.Lock()
        self._send_lock = asyncio.Lock()
        self._pending: dict[str, asyncio.Future[Any]] = {}
        self._streams: dict[str, set[asyncio.Queue[Any]]] = defaultdict(set)
        self._next_request_id = 0
        self._closing = False
        self._generation = 0

    @property
    def connected(self) -> bool:
        return (
            self._websocket is not None
            and self._reader_task is not None
            and not self._reader_task.done()
        )

    @property
    def generation(self) -> int:
        return self._generation

    def _websocket_url(self) -> str:
        if not self._token:
            raise ValueError("Hermes Dashboard token is required")
        parts = urlsplit(self._base_url)
        schemes = {"http": "ws", "https": "wss", "ws": "ws", "wss": "wss"}
        if parts.scheme not in schemes:
            raise ValueError("Hermes URL must use http, https, ws, or wss")
        path = parts.path.rstrip("/")
        if not path.endswith("/api/ws"):
            path = f"{path}/api/ws" if path else "/api/ws"
        query = dict(parse_qsl(parts.query, keep_blank_values=True))
        query["token"] = self._token
        return urlunsplit(
            (schemes[parts.scheme], parts.netloc, path, urlencode(query), "")
        )

    async def connect(self) -> None:
        if self.connected:
            return
        async with self._connect_lock:
            if self.connected:
                return
            old_websocket = self._websocket
            if old_websocket is not None:
                await old_websocket.close()
            self._closing = False
            websocket = await _open_websocket(self._websocket_url())
            self._websocket = websocket
            self._generation += 1
            self._reader_task = asyncio.create_task(
                self._reader_loop(websocket), name="hermes-bcn-hermes-reader"
            )

    async def close(self) -> None:
        self._closing = True
        websocket = self._websocket
        reader = self._reader_task
        if websocket is not None:
            await websocket.close()
        if reader is not None and reader is not asyncio.current_task():
            await asyncio.gather(reader, return_exceptions=True)
        self._websocket = None
        self._reader_task = None
        self._fail_waiters(ConnectionError("Hermes gateway closed"))

    async def request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        await self.connect()
        websocket = self._websocket
        if websocket is None:
            raise ConnectionError("Hermes gateway is not connected")
        self._next_request_id += 1
        request_id = f"avernet-{self._next_request_id}"
        future = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        frame = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": params or {},
        }
        try:
            async with self._send_lock:
                await websocket.send(json.dumps(frame, ensure_ascii=False))
            result = await asyncio.wait_for(
                future,
                timeout=self._request_timeout if timeout is None else timeout,
            )
        finally:
            self._pending.pop(request_id, None)
        if result is None:
            return {}
        if not isinstance(result, dict):
            raise HermesRpcError("INVALID_RESULT", "Hermes returned a non-object result")
        return result

    async def create_session(self, *, cwd: str | None = None) -> dict[str, Any]:
        params = {"source": "avernet-bcn"}
        if cwd:
            params["cwd"] = cwd
        return await self.request("session.create", params)

    async def resume_session(self, stored_session_id: str) -> dict[str, Any]:
        return await self.request(
            "session.resume",
            {"session_id": stored_session_id, "source": "avernet-bcn"},
        )

    async def session_history(self, session_id: str) -> dict[str, Any]:
        return await self.request("session.history", {"session_id": session_id})

    async def interrupt_session(self, session_id: str) -> dict[str, Any]:
        return await self.request("session.interrupt", {"session_id": session_id})

    async def submit_prompt(self, session_id: str, text: str) -> HermesEventStream:
        await self.connect()
        queue: asyncio.Queue[Any] = asyncio.Queue()
        self._streams[session_id].add(queue)
        stream = HermesEventStream(self, session_id, queue)
        try:
            await self.request(
                "prompt.submit", {"session_id": session_id, "text": text}
            )
        except BaseException:
            stream.close()
            raise
        return stream

    async def _reader_loop(self, websocket: Any) -> None:
        failure: BaseException | None = None
        try:
            async for raw in websocket:
                try:
                    message = json.loads(raw)
                except (TypeError, json.JSONDecodeError) as exc:
                    _log(
                        logging.WARNING,
                        "hermes_malformed_frame",
                        size=len(raw) if isinstance(raw, str) else None,
                        error_type=type(exc).__name__,
                    )
                    continue
                if isinstance(message, dict):
                    self._dispatch(message)
        except asyncio.CancelledError:
            raise
        except BaseException as exc:
            failure = exc
        finally:
            if self._websocket is websocket:
                self._websocket = None
                self._reader_task = None
                if not self._closing:
                    self._fail_waiters(
                        failure or ConnectionError("Hermes gateway disconnected")
                    )

    def _dispatch(self, message: dict[str, Any]) -> None:
        request_id = message.get("id")
        if request_id in self._pending:
            future = self._pending[request_id]
            if future.done():
                return
            error = message.get("error")
            if isinstance(error, dict):
                future.set_exception(
                    HermesRpcError(
                        error.get("code", "UNKNOWN"),
                        str(error.get("message") or "Hermes request failed"),
                    )
                )
            else:
                future.set_result(message.get("result"))
            return
        if message.get("method") != "event":
            return
        params = message.get("params")
        if not isinstance(params, dict):
            return
        session_id = params.get("session_id")
        if not isinstance(session_id, str):
            return
        event = {
            "type": params.get("type"),
            "session_id": session_id,
            "payload": params.get("payload") if isinstance(params.get("payload"), dict) else {},
        }
        for queue in tuple(self._streams.get(session_id, ())):
            queue.put_nowait(event)

    def _remove_stream(self, session_id: str, queue: asyncio.Queue[Any]) -> None:
        queues = self._streams.get(session_id)
        if queues is None:
            return
        queues.discard(queue)
        if not queues:
            self._streams.pop(session_id, None)

    def _fail_waiters(self, error: BaseException) -> None:
        for future in tuple(self._pending.values()):
            if not future.done():
                future.set_exception(error)
        failure = _StreamFailure(error)
        for queues in tuple(self._streams.values()):
            for queue in tuple(queues):
                queue.put_nowait(failure)


FrameHandler = Callable[[dict[str, Any]], Awaitable[None] | None]


class BcsClient:
    """BCN protocol-v2 transport with token rotation and bounded reconnect."""

    def __init__(
        self,
        *,
        url: str,
        bot_id: str,
        token: str,
        credential_store: AtomicJsonStore,
        heartbeat_interval: float = 60.0,
        reconnect_delays: Iterable[float] = (1, 2, 4, 8, 16, 30),
        connection_state_callback: Callable[[bool], None] | None = None,
    ) -> None:
        self.url = url
        self.bot_id = bot_id
        self.token = token
        self.credential_store = credential_store
        self.heartbeat_interval = heartbeat_interval
        self.reconnect_delays = tuple(reconnect_delays) or (1.0,)
        self.connection_state_callback = connection_state_callback
        self._websocket: Any | None = None
        self._send_lock = asyncio.Lock()
        self._next_id = 0
        self._seq = 0
        self._connection_generation = 0

    async def run(
        self,
        handler: FrameHandler,
        *,
        stop_event: asyncio.Event | None = None,
    ) -> None:
        stop = stop_event or asyncio.Event()
        attempt = 0
        while not stop.is_set():
            connection_generation = self._connection_generation
            serve_task = asyncio.create_task(self._serve_once(handler))
            stop_task = asyncio.create_task(stop.wait())
            done, _ = await asyncio.wait(
                {serve_task, stop_task}, return_when=asyncio.FIRST_COMPLETED
            )
            if stop_task in done:
                await self.close()
                await asyncio.gather(serve_task, return_exceptions=True)
                return
            stop_task.cancel()
            await asyncio.gather(stop_task, return_exceptions=True)
            error = serve_task.exception()
            if error is not None:
                _log(
                    logging.WARNING,
                    "bcs_connection_lost",
                    error_type=type(error).__name__,
                )
            if stop.is_set():
                return
            if self._connection_generation != connection_generation:
                attempt = 0
            delay = self.reconnect_delays[min(attempt, len(self.reconnect_delays) - 1)]
            attempt += 1
            try:
                await asyncio.wait_for(stop.wait(), timeout=delay)
            except asyncio.TimeoutError:
                pass

    async def close(self) -> None:
        websocket = self._websocket
        if websocket is not None:
            await websocket.close()
        self._websocket = None

    async def send_response(
        self,
        request_id: str,
        payload: dict[str, Any] | None = None,
        *,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> None:
        if error_code is None:
            frame = {
                "type": "res",
                "id": request_id,
                "ok": True,
                "payload": payload or {},
            }
        else:
            frame = {
                "type": "res",
                "id": request_id,
                "ok": False,
                "error": {
                    "code": error_code,
                    "message": error_message or error_code,
                    "retryable": False,
                    "retry_after_ms": None,
                },
            }
        await self._send(frame)

    async def send_event(self, event: str, payload: dict[str, Any]) -> None:
        self._seq += 1
        await self._send(
            {"type": "event", "event": event, "payload": payload, "seq": self._seq}
        )

    async def _serve_once(self, handler: FrameHandler) -> None:
        websocket = await _open_websocket(self.url)
        self._websocket = websocket
        heartbeat: asyncio.Task[None] | None = None
        connected = False
        try:
            connect_id = self._new_id("connect")
            await self._send(
                {
                    "type": "req",
                    "id": connect_id,
                    "method": "bot.connect",
                    "params": {
                        "bot_id": self.bot_id,
                        "token": self.token,
                        "protocol_version": 2,
                    },
                }
            )
            raw = await websocket.recv()
            response = json.loads(raw)
            if (
                not isinstance(response, dict)
                or response.get("type") != "res"
                or response.get("id") != connect_id
                or not response.get("ok")
            ):
                raise ConnectionError("BCN rejected bot.connect")
            payload = response.get("payload")
            if not isinstance(payload, dict) or payload.get("protocol_version") != 2:
                raise ConnectionError("BCN did not negotiate protocol version 2")
            self._persist_identity(payload)
            self._connection_generation += 1
            connected = True
            if self.connection_state_callback is not None:
                self.connection_state_callback(True)
            heartbeat = asyncio.create_task(
                self._heartbeat_loop(), name="hermes-bcn-heartbeat"
            )
            async for raw in websocket:
                try:
                    frame = json.loads(raw)
                except (TypeError, json.JSONDecodeError) as exc:
                    _log(
                        logging.WARNING,
                        "bcs_malformed_frame",
                        size=len(raw) if isinstance(raw, str) else None,
                        error_type=type(exc).__name__,
                    )
                    continue
                if not isinstance(frame, dict):
                    _log(logging.WARNING, "bcs_non_object_frame")
                    continue
                if frame.get("type") == "res":
                    continue
                result = handler(frame)
                if inspect.isawaitable(result):
                    await result
        finally:
            if heartbeat is not None:
                heartbeat.cancel()
                await asyncio.gather(heartbeat, return_exceptions=True)
            if self._websocket is websocket:
                self._websocket = None
            try:
                await websocket.close()
            finally:
                if connected and self.connection_state_callback is not None:
                    self.connection_state_callback(False)

    async def _heartbeat_loop(self) -> None:
        while True:
            await asyncio.sleep(self.heartbeat_interval)
            await self._send(
                {
                    "type": "req",
                    "id": self._new_id("status"),
                    "method": "bot.status",
                    "params": {},
                }
            )

    def _persist_identity(self, payload: dict[str, Any]) -> None:
        replacement = payload.get("token")
        bot_uuid = payload.get("bot_uuid")
        if isinstance(replacement, str) and replacement:
            self.token = replacement
        if isinstance(bot_uuid, str) and bot_uuid:
            self.bot_id = bot_uuid
        current = self.credential_store.load({})
        if not isinstance(current, dict):
            current = {}
        current.update({"bot_uuid": self.bot_id, "bot_token": self.token})
        self.credential_store.save(current)

    async def _send(self, frame: dict[str, Any]) -> None:
        websocket = self._websocket
        if websocket is None:
            raise ConnectionError("BCN is not connected")
        async with self._send_lock:
            await websocket.send(json.dumps(frame, ensure_ascii=False))

    def _new_id(self, prefix: str) -> str:
        self._next_id += 1
        return f"{prefix}-{self._next_id}"


@dataclass
class _ActiveRun:
    group: str
    session_key: str
    session_ready: asyncio.Future[str]
    task: asyncio.Task[None] | None = None
    aborted: bool = False
    lock_acquired: bool = False
    prompt_submitted: bool = False
    terminal_sent: bool = False


class HermesBcnBridge:
    """Stateful protocol translation with one in-flight turn per BCN group."""

    def __init__(
        self,
        bcs: BcsClient,
        hermes: HermesClient,
        state_store: AtomicJsonStore,
        *,
        workspace: str | None = None,
    ) -> None:
        self.bcs = bcs
        self.hermes = hermes
        self.state_store = state_store
        self.workspace = workspace
        loaded = state_store.load({"groups": {}})
        if not isinstance(loaded, dict):
            loaded = {"groups": {}}
        groups = loaded.get("groups")
        if not isinstance(groups, dict):
            groups = {}
        self._state: dict[str, Any] = {**loaded, "groups": groups}
        self._locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._live_sessions: dict[str, tuple[str, int]] = {}
        self._active: dict[str, _ActiveRun] = {}
        self._tasks: set[asyncio.Task[None]] = set()

    async def handle_frame(self, frame: dict[str, Any]) -> None:
        if frame.get("type") == "event" and frame.get("event") == "chat.abort":
            self._spawn(self._handle_abort(frame.get("payload") or {}))
            return
        if frame.get("type") != "req":
            return
        request_id = frame.get("id")
        method = frame.get("method")
        params = frame.get("params")
        if not isinstance(request_id, str) or not isinstance(params, dict):
            return
        if method == "chat.send":
            run_id = f"run-{uuid.uuid4().hex}"
            await self.bcs.send_response(request_id, {"run_id": run_id})
            active = _ActiveRun(
                group=self._group_key(params),
                session_key=str(params.get("session_key") or ""),
                session_ready=asyncio.get_running_loop().create_future(),
            )
            self._active[run_id] = active
            active.task = self._spawn(self._handle_send(run_id, params, active))
        elif method == "chat.inject":
            try:
                self._append_observation(params)
            except Exception as exc:
                _log(
                    logging.WARNING,
                    "bridge_inject_persist_failed",
                    group=self._group_key(params),
                    error_type=type(exc).__name__,
                )
                await self.bcs.send_response(
                    request_id,
                    error_code="persistence_failed",
                    error_message="Observation could not be persisted",
                )
            else:
                await self.bcs.send_response(request_id, {})
        elif method == "chat.history":
            self._spawn(self._handle_history(request_id, params))
        elif method == "chat.abort":
            await self.bcs.send_response(request_id, {})
            self._spawn(self._handle_abort(params))
        else:
            await self.bcs.send_response(
                request_id,
                error_code="unknown_method",
                error_message=f"Unknown method: {method}",
            )

    async def _handle_send(
        self, run_id: str, params: dict[str, Any], active: _ActiveRun
    ) -> None:
        group = active.group
        stream: AsyncIterator[dict[str, Any]] | None = None
        try:
            if active.aborted:
                await self._send_terminal_chat_event(active, run_id, "aborted")
                return
            async with self._locks[group]:
                active.lock_acquired = True
                if active.aborted:
                    await self._send_terminal_chat_event(active, run_id, "aborted")
                    return
                session_id = await self._ensure_session(group, params)
                if not active.session_ready.done():
                    active.session_ready.set_result(session_id)
                if active.aborted:
                    await self._send_terminal_chat_event(active, run_id, "aborted")
                    return
                prompt, observations = self._build_prompt(group, params)
                active.prompt_submitted = True
                stream = await self.hermes.submit_prompt(session_id, prompt)
                self._clear_observations(group, observations)
                cumulative = ""
                async for event in stream:
                    if active.aborted:
                        return
                    event_type = event.get("type")
                    payload = event.get("payload") or {}
                    if event_type == "message.delta":
                        cumulative += str(payload.get("text") or "")
                        await self._send_chat_event(
                            run_id, group, "delta", text=cumulative
                        )
                    elif event_type == "message.complete":
                        status = str(payload.get("status") or "complete")
                        if status == "interrupted":
                            await self._send_terminal_chat_event(
                                active, run_id, "aborted"
                            )
                        elif status == "error":
                            await self._send_terminal_chat_event(
                                active,
                                run_id,
                                "error",
                                text=str(payload.get("text") or "Hermes request failed"),
                            )
                        else:
                            await self._send_terminal_chat_event(
                                active,
                                run_id,
                                "final",
                                text=str(payload.get("text") or cumulative),
                                usage=payload.get("usage"),
                            )
                    elif event_type == "error":
                        await self._send_terminal_chat_event(
                            active,
                            run_id,
                            "error",
                            text=str(payload.get("message") or "Hermes request failed"),
                        )
                if not active.aborted:
                    await self._send_terminal_chat_event(
                        active, run_id, "error", text="Hermes stream ended unexpectedly"
                    )
        except asyncio.CancelledError:
            raise
        except BaseException as exc:
            if not active.session_ready.done():
                active.session_ready.set_exception(exc)
                active.session_ready.exception()
            _log(
                logging.WARNING,
                "bridge_turn_failed",
                group=group,
                error_type=type(exc).__name__,
            )
            await self._send_terminal_chat_event(
                active, run_id, "error", text="Hermes is unavailable"
            )
        finally:
            close_stream = getattr(stream, "close", None)
            if callable(close_stream):
                close_stream()
            if self._active.get(run_id) is active:
                self._active.pop(run_id, None)

    async def _handle_abort(self, payload: dict[str, Any]) -> None:
        found = self._find_active_run(payload)
        if found is None:
            return
        run_id, active = found
        active.aborted = True
        if not active.lock_acquired:
            await self._send_terminal_chat_event(active, run_id, "aborted")
            if active.task is not None and not active.task.done():
                active.task.cancel()
            return
        try:
            session_id = await active.session_ready
            if active.prompt_submitted:
                await self.hermes.interrupt_session(session_id)
        except asyncio.CancelledError:
            raise
        except BaseException as exc:
            _log(
                logging.WARNING,
                "bridge_abort_failed",
                group=active.group,
                error_type=type(exc).__name__,
            )
            await self._send_terminal_chat_event(
                active, run_id, "error", text="Hermes interrupt failed"
            )
            if active.task is not None and not active.task.done():
                active.task.cancel()
            return
        await self._send_terminal_chat_event(active, run_id, "aborted")
        if active.task is not None and not active.task.done():
            active.task.cancel()
        if self._active.get(run_id) is active:
            self._active.pop(run_id, None)

    def _find_active_run(
        self, payload: dict[str, Any]
    ) -> tuple[str, _ActiveRun] | None:
        run_id = payload.get("run_id")
        if isinstance(run_id, str):
            active = self._active.get(run_id)
            if active is not None and not active.terminal_sent:
                return run_id, active

        candidates = tuple(reversed(self._active.items()))
        session_key = payload.get("session_key")
        if isinstance(session_key, str) and session_key:
            for candidate_run_id, active in candidates:
                if active.session_key == session_key and not active.terminal_sent:
                    return candidate_run_id, active

        group = payload.get("bcs_group_id")
        if isinstance(group, str) and group:
            for candidate_run_id, active in candidates:
                if active.group == group and not active.terminal_sent:
                    return candidate_run_id, active
        return None

    async def _handle_history(
        self, request_id: str, params: dict[str, Any]
    ) -> None:
        group = self._find_group(params)
        session_key = str(params.get("session_key") or "")
        if group is None:
            await self.bcs.send_response(
                request_id,
                {"session_key": session_key, "session_id": "", "messages": []},
            )
            return
        try:
            async with self._locks[group]:
                session_id = await self._ensure_session(group, params)
                result = await self.hermes.session_history(session_id)
            raw_messages = result.get("messages")
            messages = []
            if isinstance(raw_messages, list):
                for item in raw_messages:
                    normalized = self._normalize_history_message(item)
                    if normalized is not None:
                        messages.append(normalized)
            before = params.get("before")
            if isinstance(before, int):
                messages = [
                    message
                    for message in messages
                    if isinstance(message.get("timestamp"), (int, float))
                    and message["timestamp"] < before
                ]
            limit = params.get("limit")
            if isinstance(limit, int) and limit >= 0:
                messages = messages[-limit:] if limit else []
            await self.bcs.send_response(
                request_id,
                {
                    "session_key": session_key,
                    "session_id": group,
                    "messages": messages,
                },
            )
        except BaseException as exc:
            _log(
                logging.WARNING,
                "bridge_history_failed",
                group=group,
                error_type=type(exc).__name__,
            )
            await self.bcs.send_response(
                request_id,
                error_code="unavailable",
                error_message="Hermes history is unavailable",
            )

    async def _ensure_session(
        self, group: str, params: dict[str, Any]
    ) -> str:
        await self.hermes.connect()
        current = self._live_sessions.get(group)
        if current is not None and current[1] == self.hermes.generation:
            return current[0]
        group_state = self._group_state(group, params)
        stored = group_state.get("stored_session_id")
        if isinstance(stored, str) and stored:
            try:
                result = await self.hermes.resume_session(stored)
            except HermesRpcError as exc:
                if str(exc.code) != "4007":
                    raise
                group_state.pop("stored_session_id", None)
                self._live_sessions.pop(group, None)
                self._save_state()
                result = await self.hermes.create_session(cwd=self.workspace)
                stored = result.get("stored_session_id") or result.get("session_key")
                if not isinstance(stored, str) or not stored:
                    raise HermesRpcError(
                        "INVALID_RESULT", "Hermes did not return a stored session id"
                    )
                group_state["stored_session_id"] = stored
                self._save_state()
        else:
            result = await self.hermes.create_session(cwd=self.workspace)
            stored = result.get("stored_session_id") or result.get("session_key")
            if not isinstance(stored, str) or not stored:
                raise HermesRpcError(
                    "INVALID_RESULT", "Hermes did not return a stored session id"
                )
            group_state["stored_session_id"] = stored
            self._save_state()
        session_id = result.get("session_id")
        if not isinstance(session_id, str) or not session_id:
            raise HermesRpcError("INVALID_RESULT", "Hermes did not return a session id")
        self._live_sessions[group] = (session_id, self.hermes.generation)
        return session_id

    def _append_observation(self, params: dict[str, Any]) -> None:
        group = self._group_key(params)
        group_state = self._group_state(group, params)
        sender = str((params.get("channel") or {}).get("user_id") or "user")
        text = self._message_text(params.get("message"))
        observation = {"sender": sender, "text": text}
        observations = group_state["observations"]
        previous = list(observations)
        observations.append(observation)
        while len(observations) > _OBSERVATION_LIMIT or self._observation_size(
            observations
        ) > _OBSERVATION_BYTES:
            observations.pop(0)
        try:
            self._save_state()
        except Exception:
            observations[:] = previous
            raise

    def _build_prompt(
        self, group: str, params: dict[str, Any]
    ) -> tuple[str, list[dict[str, str]]]:
        observations = list(self._group_state(group, params)["observations"])
        text = self._message_text(params.get("message"))
        if not observations:
            return text, observations
        lines = ["Silent observations:"]
        lines.extend(
            f"- {item.get('sender', 'user')}: {item.get('text', '')}"
            for item in observations
        )
        observation_text = "\n".join(lines)
        return f"{observation_text}\n\n{text}", observations

    def _clear_observations(
        self, group: str, submitted: list[dict[str, str]]
    ) -> None:
        observations = self._group_state(group)["observations"]
        count = 0
        for current, expected in zip(observations, submitted):
            if current != expected:
                break
            count += 1
        if count:
            del observations[:count]
            self._save_state()

    async def _send_chat_event(
        self,
        run_id: str,
        group: str,
        state: str,
        *,
        text: str | None = None,
        usage: Any = None,
    ) -> None:
        payload: dict[str, Any] = {
            "run_id": run_id,
            "bcs_group_id": group,
            "state": state,
        }
        if text is not None:
            payload["message"] = {
                "role": "assistant",
                "content": [{"type": "text", "text": text}],
                "timestamp": int(time.time() * 1000),
            }
        if isinstance(usage, dict):
            payload["usage"] = usage
        if state in {"final", "aborted"}:
            payload["stop_reason"] = "complete" if state == "final" else "aborted"
        await self.bcs.send_event("chat.event", payload)

    async def _send_terminal_chat_event(
        self,
        active: _ActiveRun,
        run_id: str,
        state: str,
        *,
        text: str | None = None,
        usage: Any = None,
    ) -> bool:
        if active.terminal_sent:
            return False
        active.terminal_sent = True
        await self._send_chat_event(
            run_id, active.group, state, text=text, usage=usage
        )
        return True

    def _group_state(
        self, group: str, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        groups = self._state["groups"]
        state = groups.setdefault(group, {})
        if not isinstance(state.get("observations"), list):
            state["observations"] = []
        if params is not None and params.get("session_key"):
            state["session_key"] = str(params["session_key"])
        return state

    def _find_group(self, params: dict[str, Any]) -> str | None:
        session_key = params.get("session_key")
        if isinstance(session_key, str) and session_key:
            for candidate, state in self._state["groups"].items():
                if isinstance(state, dict) and state.get("session_key") == session_key:
                    return candidate
        group = params.get("bcs_group_id")
        if isinstance(group, str) and group:
            return group
        return None

    @staticmethod
    def _group_key(params: dict[str, Any]) -> str:
        value = params.get("bcs_group_id") or params.get("session_key")
        return str(value or "default")

    @classmethod
    def _message_text(cls, message: Any) -> str:
        if not isinstance(message, dict):
            return ""
        content = message.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            return "".join(
                str(item.get("text") or "")
                for item in content
                if isinstance(item, dict) and item.get("type") == "text"
            )
        return ""

    @classmethod
    def _normalize_history_message(cls, item: Any) -> dict[str, Any] | None:
        if not isinstance(item, dict) or item.get("role") not in {"user", "assistant"}:
            return None
        text = item.get("text")
        if not isinstance(text, str):
            text = cls._message_text(item)
        result = {"role": item["role"], "content": text}
        if isinstance(item.get("timestamp"), (int, float)):
            result["timestamp"] = item["timestamp"]
        return result

    @staticmethod
    def _observation_size(observations: list[dict[str, str]]) -> int:
        return len(
            json.dumps(observations, ensure_ascii=False, separators=(",", ":")).encode(
                "utf-8"
            )
        )

    def _save_state(self) -> None:
        self.state_store.save(self._state)

    def _spawn(self, awaitable: Awaitable[None]) -> asyncio.Task[None]:
        task = asyncio.create_task(awaitable)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task


@dataclass(frozen=True)
class ConnectorPaths:
    session: Path
    state: Path
    pid: Path
    dashboard_pid: Path
    health: Path
    lock: Path
    run_lock: Path
    log: Path


def resolve_hermes_home(
    *,
    profile: str | None = None,
    hermes_home: str | os.PathLike[str] | None = None,
) -> Path:
    if profile and hermes_home:
        raise ValueError("use either --profile or --hermes-home, not both")
    if hermes_home:
        return Path(hermes_home).expanduser().absolute()
    if profile:
        if profile in {".", ".."} or Path(profile).name != profile:
            raise ValueError("profile must be a single directory name")
        return (Path.home() / ".hermes" / "profiles" / profile).absolute()
    configured = os.environ.get("HERMES_HOME")
    return Path(configured or Path.home() / ".hermes").expanduser().absolute()


def connector_paths(hermes_home: str | os.PathLike[str]) -> ConnectorPaths:
    state_dir = Path(hermes_home).expanduser().resolve() / "bcn"
    return ConnectorPaths(
        session=state_dir / "session.json",
        state=state_dir / "groups.json",
        pid=state_dir / "connector.pid",
        dashboard_pid=state_dir / "dashboard.pid",
        health=state_dir / "health.json",
        lock=state_dir / "lifecycle.lock",
        run_lock=state_dir / "run.lock",
        log=state_dir / "connector.log",
    )


class _ConnectorHealth:
    def __init__(self, path: Path, pid: int, start_marker: str) -> None:
        self.path = path
        self.pid = pid
        self.start_marker = start_marker
        self.dashboard_ready = False
        self.bcs_ready = False

    def initialize(self) -> None:
        self._save()

    def set_dashboard_ready(self, ready: bool) -> None:
        self.dashboard_ready = ready
        self._save()

    def set_bcs_ready(self, ready: bool) -> None:
        self.bcs_ready = ready
        self._save()

    def clear(self) -> None:
        current = AtomicJsonStore(self.path).load({})
        if (
            isinstance(current, dict)
            and current.get("pid") == self.pid
            and current.get("start_marker") == self.start_marker
        ):
            self.path.unlink(missing_ok=True)

    def _save(self) -> None:
        AtomicJsonStore(self.path).save(
            {
                "pid": self.pid,
                "start_marker": self.start_marker,
                "dashboard_ready": self.dashboard_ready,
                "bcs_ready": self.bcs_ready,
                "ready": self.dashboard_ready and self.bcs_ready,
                "updated_at": time.time(),
            }
        )


def _post_registration(endpoint: str, human_token: str, bot_name: str) -> dict[str, Any]:
    query = urlencode({"token": human_token, "bot-name": bot_name})
    url = f"{endpoint.rstrip('/')}/register?{query}"
    request = Request(url, data=b"", method="POST")
    try:
        with urlopen(request, timeout=30) as response:
            payload = json.load(response)
    except HTTPError as exc:
        raise RuntimeError(f"registration failed (HTTP {exc.code})") from None
    except (URLError, OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"registration failed ({type(exc).__name__})") from None
    if not isinstance(payload, dict):
        raise ValueError("registration response must be a JSON object")
    return payload


def _valid_credentials(session: Any) -> bool:
    return isinstance(session, dict) and all(
        isinstance(session.get(key), str) and bool(session[key])
        for key in ("bot_uuid", "bot_token", "bcs_url")
    )


def register_bot(
    *,
    human_token: str,
    bot_name: str,
    bcs_endpoint: str,
    bcs_url: str,
    hermes_home: str | os.PathLike[str],
    workspace: str | None = None,
    replace: bool = False,
) -> dict[str, Any]:
    home = Path(hermes_home).expanduser().resolve()
    paths = connector_paths(home)
    store = AtomicJsonStore(paths.session)
    existing = store.load({})
    if _valid_credentials(existing) and not replace:
        return existing
    if not human_token:
        raise ValueError("human token cannot be empty")

    response = _post_registration(bcs_endpoint, human_token, bot_name)
    for key in ("bot_uuid", "bot_token"):
        if not isinstance(response.get(key), str) or not response[key]:
            raise ValueError(f"registration response missing {key}")

    session = {
        "bcs_url": bcs_url,
        "bot_uuid": response["bot_uuid"],
        "bot_token": response["bot_token"],
        "bot_name": bot_name,
        "hermes_home": str(home),
        "workspace": workspace or str(home),
        "dashboard_port": _free_loopback_port(),
        "dashboard_token": secrets.token_urlsafe(32),
        "connector_version": _CONNECTOR_VERSION,
    }
    if _valid_credentials(existing) and replace:
        with _lifecycle_lock(paths):
            _stop_connector_locked(paths, timeout=15.0, require_running_stop=True)
            store.save(session)
    else:
        store.save(session)
    return session


def _loopback_port_available(port: int) -> bool:
    if not isinstance(port, int) or not 0 < port < 65536:
        return False
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def ensure_dashboard_settings(
    session: dict[str, Any], hermes_home: str | os.PathLike[str]
) -> tuple[int, str]:
    port = session.get("dashboard_port")
    token = session.get("dashboard_token")
    changed = False
    if not isinstance(port, int) or not _loopback_port_available(port):
        port = _free_loopback_port()
        session["dashboard_port"] = port
        changed = True
    if not isinstance(token, str) or not token:
        token = secrets.token_urlsafe(32)
        session["dashboard_token"] = token
        changed = True
    if changed:
        AtomicJsonStore(connector_paths(hermes_home).session).save(session)
    return port, token


def _read_pid_record(path: Path) -> dict[str, Any] | None:
    value = AtomicJsonStore(path).load(None)
    if not isinstance(value, dict) or not isinstance(value.get("pid"), int):
        return None
    return value


def _process_start_marker(pid: int) -> str | None:
    try:
        result = subprocess.run(
            ["ps", "-p", str(pid), "-o", "lstart="],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        return None
    marker = result.stdout.strip()
    return marker if result.returncode == 0 and marker else None


def _process_argv(pid: int) -> tuple[str, ...] | None:
    try:
        data = (Path("/proc") / str(pid) / "cmdline").read_bytes()
    except OSError:
        data = b""
    if data:
        return tuple(
            part.decode(errors="surrogateescape") for part in data.split(b"\0") if part
        )
    try:
        result = subprocess.run(
            ["ps", "-ww", "-p", str(pid), "-o", "command="],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        return None
    if result.returncode != 0 or not result.stdout.strip():
        return None
    try:
        return tuple(shlex.split(result.stdout.strip()))
    except ValueError:
        return None


def _wait_for_process_start_marker(pid: int, timeout: float = 1.0) -> str:
    deadline = time.monotonic() + timeout
    while True:
        marker = _process_start_marker(pid)
        if marker is not None:
            return marker
        if time.monotonic() >= deadline:
            raise RuntimeError(f"could not identify started process {pid}")
        time.sleep(0.01)


def _wait_for_process_exit(pid: int, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except OSError:
            return True
        time.sleep(0.1)
    return False


@contextmanager
def _lifecycle_lock(paths: ConnectorPaths) -> Iterable[None]:
    paths.lock.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with paths.lock.open("a", encoding="utf-8") as handle:
        paths.lock.chmod(0o600)
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


@contextmanager
def _run_ownership_lock(paths: ConnectorPaths) -> Iterable[None]:
    paths.run_lock.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with paths.run_lock.open("a", encoding="utf-8") as handle:
        paths.run_lock.chmod(0o600)
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("connector run already owns this Hermes profile") from exc
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _connector_process_matches(
    record: dict[str, Any], *, expected_home: Path | None = None
) -> bool:
    pid = record.get("pid")
    home = record.get("hermes_home")
    script = record.get("script")
    start_marker = record.get("start_marker")
    if (
        not isinstance(pid, int)
        or pid <= 0
        or not isinstance(home, str)
        or not isinstance(script, str)
        or not Path(script).expanduser().is_absolute()
        or not isinstance(start_marker, str)
        or not start_marker
    ):
        return False
    if (
        expected_home is not None
        and Path(home).expanduser().resolve() != expected_home.resolve()
    ):
        return False
    if _process_start_marker(pid) != start_marker:
        return False
    argv = _process_argv(pid)
    if argv is None:
        return False
    script_path = Path(script).expanduser().resolve()
    expected = ("run", "--hermes-home", home)
    if len(argv) == 4 and Path(argv[0]).expanduser().resolve() == script_path:
        return argv[1:] == expected
    if len(argv) == 5 and Path(argv[1]).expanduser().resolve() == script_path:
        return argv[2:] == expected
    return False


def _dashboard_process_matches(record: dict[str, Any]) -> bool:
    pid = record.get("pid")
    port = record.get("port")
    start_marker = record.get("start_marker")
    expected_argv = record.get("argv")
    if (
        not isinstance(pid, int)
        or pid <= 0
        or not isinstance(record.get("hermes_home"), str)
        or not isinstance(port, int)
        or not isinstance(start_marker, str)
        or not isinstance(expected_argv, list)
        or not all(isinstance(arg, str) for arg in expected_argv)
    ):
        return False
    expected = tuple(expected_argv)
    if len(expected) != 8 or expected[1:] != (
        "dashboard",
        "--isolated",
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        "--no-open",
    ):
        return False
    if _process_start_marker(pid) != start_marker:
        return False
    actual = _process_argv(pid)
    if actual == expected:
        return True
    return (
        actual is not None
        and len(actual) == len(expected) + 1
        and Path(actual[1]).expanduser().resolve()
        == Path(expected[0]).expanduser().resolve()
        and actual[2:] == expected[1:]
    )


def _recover_orphan_dashboard(path: Path, *, timeout: float = 10.0) -> bool:
    record = _read_pid_record(path)
    if record is None:
        path.unlink(missing_ok=True)
        return False
    home = record.get("hermes_home")
    if not isinstance(home, str) or Path(home).resolve() != path.parent.parent.resolve():
        path.unlink(missing_ok=True)
        return False
    if not _dashboard_process_matches(record):
        path.unlink(missing_ok=True)
        return False
    if not _dashboard_process_matches(record):
        path.unlink(missing_ok=True)
        return False
    pid = int(record["pid"])
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        path.unlink(missing_ok=True)
        return True
    if not _wait_for_process_exit(pid, timeout):
        raise TimeoutError("orphan Hermes Dashboard did not stop after SIGTERM")
    path.unlink(missing_ok=True)
    return True


def connector_status(hermes_home: str | os.PathLike[str]) -> tuple[str, int]:
    paths = connector_paths(hermes_home)
    record = _read_pid_record(paths.pid)
    if record is None:
        return ("stale", 2) if paths.pid.exists() else ("stopped", 1)
    if _connector_process_matches(record, expected_home=paths.pid.parent.parent):
        return "running", 0
    return "stale", 2


def _connector_health_ready(paths: ConnectorPaths, record: dict[str, Any]) -> bool:
    health = AtomicJsonStore(paths.health).load({})
    return (
        isinstance(health, dict)
        and health.get("pid") == record.get("pid")
        and health.get("start_marker") == record.get("start_marker")
        and health.get("dashboard_ready") is True
        and health.get("bcs_ready") is True
        and health.get("ready") is True
    )


def _wait_for_connector_ready(
    paths: ConnectorPaths,
    record: dict[str, Any],
    *,
    process: subprocess.Popen[Any] | None,
    timeout: float,
) -> None:
    deadline = time.monotonic() + max(0.0, timeout)
    while True:
        current = _read_pid_record(paths.pid)
        if (
            current is not None
            and current.get("pid") == record.get("pid")
            and current.get("start_marker") == record.get("start_marker")
            and _connector_health_ready(paths, current)
        ):
            if not _connector_process_matches(
                current, expected_home=paths.pid.parent.parent
            ):
                raise RuntimeError(f"connector identity check failed; see {paths.log}")
            return
        if process is not None and process.poll() is not None:
            raise RuntimeError(f"connector exited before becoming ready; see {paths.log}")
        if time.monotonic() >= deadline:
            raise RuntimeError(f"connector did not become ready; see {paths.log}")
        time.sleep(0.05)


def _stop_connector_locked(
    paths: ConnectorPaths,
    *,
    timeout: float,
    require_running_stop: bool = False,
    owned_process: subprocess.Popen[Any] | None = None,
) -> bool:
    record = _read_pid_record(paths.pid)
    connector_stopped = False
    expected_home = paths.pid.parent.parent
    if record is not None and _connector_process_matches(
        record, expected_home=expected_home
    ):
        if not _connector_process_matches(record, expected_home=expected_home):
            if require_running_stop:
                raise RuntimeError("connector identity changed before credential replacement")
        else:
            pid = int(record["pid"])
            try:
                os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                connector_stopped = True
            else:
                wait = getattr(owned_process, "wait", None)
                if (
                    owned_process is not None
                    and owned_process.pid == pid
                    and callable(wait)
                ):
                    try:
                        wait(timeout=timeout)
                    except subprocess.TimeoutExpired:
                        raise TimeoutError(
                            "connector did not stop after SIGTERM"
                        ) from None
                elif not _wait_for_process_exit(pid, timeout):
                    raise TimeoutError("connector did not stop after SIGTERM")
                connector_stopped = True
    paths.pid.unlink(missing_ok=True)
    paths.health.unlink(missing_ok=True)
    dashboard_stopped = _recover_orphan_dashboard(paths.dashboard_pid)
    return connector_stopped or dashboard_stopped


def start_connector(
    hermes_home: str | os.PathLike[str], *, health_wait: float = 30.0
) -> int:
    home = Path(hermes_home).expanduser().resolve()
    paths = connector_paths(home)
    with _lifecycle_lock(paths):
        record = _read_pid_record(paths.pid)
        if record is not None and _connector_process_matches(
            record, expected_home=home
        ):
            try:
                _wait_for_connector_ready(
                    paths, record, process=None, timeout=health_wait
                )
            except Exception:
                _stop_connector_locked(paths, timeout=15.0)
                raise
            return int(record["pid"])
        if record is not None or paths.pid.exists():
            paths.pid.unlink(missing_ok=True)
        paths.health.unlink(missing_ok=True)
        _recover_orphan_dashboard(paths.dashboard_pid)
        if not _valid_credentials(AtomicJsonStore(paths.session).load({})):
            raise RuntimeError(f"missing valid connector session: {paths.session}")

        paths.log.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with paths.log.open("a", encoding="utf-8") as log_handle:
            paths.log.chmod(0o600)
            process = subprocess.Popen(
                [
                    sys.executable,
                    str(Path(__file__).resolve()),
                    "run",
                    "--hermes-home",
                    str(home),
                ],
                stdin=subprocess.DEVNULL,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                start_new_session=True,
                close_fds=True,
            )
        try:
            start_marker = _wait_for_process_start_marker(process.pid)
        except Exception:
            process.terminate()
            raise
        record = {
            "pid": process.pid,
            "hermes_home": str(home),
            "script": str(Path(__file__).resolve()),
            "start_marker": start_marker,
        }
        AtomicJsonStore(paths.pid).save(record)
        try:
            _wait_for_connector_ready(
                paths, record, process=process, timeout=health_wait
            )
        except Exception:
            _stop_connector_locked(paths, timeout=15.0, owned_process=process)
            raise
        return process.pid


def stop_connector(
    hermes_home: str | os.PathLike[str], *, timeout: float = 15.0
) -> bool:
    paths = connector_paths(hermes_home)
    with _lifecycle_lock(paths):
        return _stop_connector_locked(paths, timeout=timeout)


class _OwnedDashboard:
    def __init__(self, hermes_home: Path, port: int, token: str) -> None:
        self.hermes_home = hermes_home
        self.port = port
        self.token = token
        self.process: asyncio.subprocess.Process | None = None
        self.record_path = connector_paths(hermes_home).dashboard_pid

    async def start(self) -> asyncio.subprocess.Process:
        env = os.environ.copy()
        env["HERMES_HOME"] = str(self.hermes_home)
        env["HERMES_DASHBOARD_SESSION_TOKEN"] = self.token
        executable = shutil.which("hermes") or "hermes"
        argv = (
            executable,
            "dashboard",
            "--isolated",
            "--host",
            "127.0.0.1",
            "--port",
            str(self.port),
            "--no-open",
        )
        self.process = await asyncio.create_subprocess_exec(
            *argv,
            env=env,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            start_marker = _wait_for_process_start_marker(self.process.pid)
        except Exception:
            self.process.terminate()
            raise
        AtomicJsonStore(self.record_path).save(
            {
                "pid": self.process.pid,
                "hermes_home": str(self.hermes_home.resolve()),
                "port": self.port,
                "start_marker": start_marker,
                "argv": list(argv),
            }
        )
        return self.process

    async def stop(self) -> None:
        process = self.process
        if process is None:
            return
        record = _read_pid_record(self.record_path)
        if process.returncode is not None:
            if record is not None and record.get("pid") == process.pid:
                self.record_path.unlink(missing_ok=True)
            return
        if (
            record is None
            or record.get("pid") != process.pid
            or not _dashboard_process_matches(record)
            or not _dashboard_process_matches(record)
        ):
            if record is not None and record.get("pid") == process.pid:
                self.record_path.unlink(missing_ok=True)
            return
        os.kill(process.pid, signal.SIGTERM)
        try:
            await asyncio.wait_for(process.wait(), timeout=10)
        except asyncio.TimeoutError:
            record = _read_pid_record(self.record_path)
            if record is not None and _dashboard_process_matches(record):
                os.kill(process.pid, signal.SIGKILL)
            await process.wait()
        finally:
            record = _read_pid_record(self.record_path)
            if record is not None and record.get("pid") == process.pid:
                self.record_path.unlink(missing_ok=True)


def _free_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


async def _supervise_dashboard(
    dashboard: _OwnedDashboard,
    stop: asyncio.Event,
    *,
    readiness_probe: Callable[[], Awaitable[None]] | None = None,
    readiness_callback: Callable[[bool], None] | None = None,
    reconnect_delays: Iterable[float] = (1, 2, 4, 8, 16, 30),
) -> None:
    delays = tuple(reconnect_delays) or (1.0,)
    attempt = 0
    while not stop.is_set():
        process = dashboard.process or await dashboard.start()
        process_wait = asyncio.create_task(process.wait())
        stop_wait = asyncio.create_task(stop.wait())
        done, _ = await asyncio.wait(
            {process_wait, stop_wait}, return_when=asyncio.FIRST_COMPLETED
        )
        if stop_wait in done:
            process_wait.cancel()
            await asyncio.gather(process_wait, return_exceptions=True)
            return
        stop_wait.cancel()
        await asyncio.gather(stop_wait, return_exceptions=True)
        _log(logging.WARNING, "owned_dashboard_exited", returncode=process.returncode)
        if readiness_callback is not None:
            readiness_callback(False)
        try:
            await asyncio.wait_for(
                stop.wait(), timeout=delays[min(attempt, len(delays) - 1)]
            )
            return
        except asyncio.TimeoutError:
            attempt += 1
        try:
            await dashboard.start()
            if readiness_probe is not None:
                await readiness_probe()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            _log(
                logging.WARNING,
                "owned_dashboard_restart_failed",
                error_type=type(exc).__name__,
            )
            continue
        if readiness_callback is not None:
            readiness_callback(True)
        attempt = 0


async def _wait_for_dashboard(
    client: HermesClient,
    dashboard: _OwnedDashboard,
    *,
    timeout: float = 30.0,
) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        process = dashboard.process
        if process is not None and process.returncode is not None:
            raise RuntimeError("Hermes Dashboard exited during startup")
        try:
            await client.connect()
            return
        except (OSError, ConnectionError):
            if asyncio.get_running_loop().time() >= deadline:
                raise TimeoutError("Hermes Dashboard did not become ready")
            await asyncio.sleep(0.1)


async def run(
    *,
    bcs_url: str,
    bot_id: str,
    bot_token: str,
    state_path: str | os.PathLike[str],
    credential_path: str | os.PathLike[str],
    hermes_url: str | None = None,
    hermes_token: str | None = None,
    hermes_home: str | os.PathLike[str] | None = None,
    workspace: str | None = None,
    stop_event: asyncio.Event | None = None,
    owned_dashboard_port: int | None = None,
    owned_dashboard_token: str | None = None,
    dashboard_state_callback: Callable[[bool], None] | None = None,
    bcs_state_callback: Callable[[bool], None] | None = None,
) -> None:
    """Run the bridge against an existing or connector-owned Dashboard."""

    if (hermes_url is None) != (hermes_token is None):
        raise ValueError("hermes_url and hermes_token must be provided together")
    stop = stop_event or asyncio.Event()
    owned: _OwnedDashboard | None = None
    supervisor: asyncio.Task[None] | None = None
    if hermes_url is None:
        home = Path(
            hermes_home or os.environ.get("HERMES_HOME") or Path.home() / ".hermes"
        ).expanduser()
        port = owned_dashboard_port or _free_loopback_port()
        hermes_token = owned_dashboard_token or secrets.token_urlsafe(32)
        hermes_url = f"http://127.0.0.1:{port}"
        owned = _OwnedDashboard(home, port, hermes_token)

    assert hermes_url is not None and hermes_token is not None
    credentials = AtomicJsonStore(credential_path)
    current = credentials.load({})
    if not isinstance(current, dict):
        current = {}
    current.update({"bot_uuid": bot_id, "bot_token": bot_token, "bcs_url": bcs_url})
    credentials.save(current)
    bcs = BcsClient(
        url=bcs_url,
        bot_id=bot_id,
        token=bot_token,
        credential_store=credentials,
        connection_state_callback=bcs_state_callback,
    )
    hermes = HermesClient(hermes_url, hermes_token)
    bridge = HermesBcnBridge(
        bcs, hermes, AtomicJsonStore(state_path), workspace=workspace
    )
    try:
        if owned is not None:
            await owned.start()
            await _wait_for_dashboard(hermes, owned)
            if dashboard_state_callback is not None:
                dashboard_state_callback(True)

            async def restore_dashboard() -> None:
                await hermes.close()
                await _wait_for_dashboard(hermes, owned)

            supervisor = asyncio.create_task(
                _supervise_dashboard(
                    owned,
                    stop,
                    readiness_probe=restore_dashboard,
                    readiness_callback=dashboard_state_callback,
                ),
                name="hermes-dashboard-supervisor",
            )
        await bcs.run(bridge.handle_frame, stop_event=stop)
    finally:
        stop.set()
        await bcs.close()
        await hermes.close()
        if supervisor is not None:
            supervisor.cancel()
            await asyncio.gather(supervisor, return_exceptions=True)
        if owned is not None:
            await owned.stop()
        if dashboard_state_callback is not None:
            dashboard_state_callback(False)


async def _run_saved_session(hermes_home: Path) -> None:
    paths = connector_paths(hermes_home)
    session = AtomicJsonStore(paths.session).load({})
    if not _valid_credentials(session):
        raise RuntimeError(f"missing valid connector session: {paths.session}")
    port, dashboard_token = ensure_dashboard_settings(session, hermes_home)
    health = _ConnectorHealth(
        paths.health,
        os.getpid(),
        _wait_for_process_start_marker(os.getpid()),
    )
    health.initialize()
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(signum, stop_event.set)
        except NotImplementedError:
            signal.signal(signum, lambda *_: loop.call_soon_threadsafe(stop_event.set))
    try:
        await run(
            bcs_url=session["bcs_url"],
            bot_id=session["bot_uuid"],
            bot_token=session["bot_token"],
            state_path=paths.state,
            credential_path=paths.session,
            hermes_home=hermes_home,
            workspace=session.get("workspace"),
            stop_event=stop_event,
            owned_dashboard_port=port,
            owned_dashboard_token=dashboard_token,
            dashboard_state_callback=health.set_dashboard_ready,
            bcs_state_callback=health.set_bcs_ready,
        )
    finally:
        health.clear()
        record = _read_pid_record(paths.pid)
        if record is not None and record.get("pid") == os.getpid():
            paths.pid.unlink(missing_ok=True)


def _add_home_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--profile", help="Hermes profile name")
    parser.add_argument("--hermes-home", help="explicit Hermes home directory")


def _argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    register_parser = commands.add_parser(
        "register", help="register and save BCS credentials"
    )
    _add_home_arguments(register_parser)
    register_parser.add_argument(
        "--human-token-stdin",
        action="store_true",
        required=True,
        help="read the human token from standard input",
    )
    register_parser.add_argument("--bot-name", required=True)
    register_parser.add_argument("--bcs-endpoint", default=_DEFAULT_BCS_ENDPOINT)
    register_parser.add_argument("--bcs-url", default=_DEFAULT_BCS_WS_URL)
    register_parser.add_argument("--workspace")
    register_parser.add_argument("--replace", action="store_true")

    for name in ("run", "stop", "status"):
        command_parser = commands.add_parser(name)
        _add_home_arguments(command_parser)
    start_parser = commands.add_parser("start")
    _add_home_arguments(start_parser)
    start_parser.add_argument(
        "--health-wait",
        type=float,
        default=30.0,
        help="seconds to wait for Dashboard RPC and BCN handshake readiness",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _argument_parser().parse_args(argv)
    try:
        home = resolve_hermes_home(profile=args.profile, hermes_home=args.hermes_home)
        if args.command == "register":
            human_token = sys.stdin.read().rstrip("\r\n")
            session = register_bot(
                human_token=human_token,
                bot_name=args.bot_name,
                bcs_endpoint=args.bcs_endpoint,
                bcs_url=args.bcs_url,
                hermes_home=home,
                workspace=args.workspace,
                replace=args.replace,
            )
            print(f"registered {session['bot_uuid']}")
            return 0
        if args.command == "run":
            paths = connector_paths(home)
            with _run_ownership_lock(paths):
                record = _read_pid_record(paths.pid)
                if (
                    record is not None
                    and record.get("pid") != os.getpid()
                    and _connector_process_matches(record, expected_home=home)
                ):
                    raise RuntimeError(f"connector already running (pid {record['pid']})")
                AtomicJsonStore(paths.pid).save(
                    {
                        "pid": os.getpid(),
                        "hermes_home": str(home),
                        "script": str(Path(__file__).resolve()),
                        "start_marker": _wait_for_process_start_marker(os.getpid()),
                    }
                )
                try:
                    asyncio.run(_run_saved_session(home))
                finally:
                    record = _read_pid_record(paths.pid)
                    if record is not None and record.get("pid") == os.getpid():
                        paths.pid.unlink(missing_ok=True)
                    paths.health.unlink(missing_ok=True)
            return 0
        if args.command == "start":
            print(
                f"running (pid {start_connector(home, health_wait=args.health_wait)})"
            )
            return 0
        if args.command == "stop":
            print("stopped" if stop_connector(home) else "not running")
            return 0
        state, code = connector_status(home)
        print(state)
        return code
    except (OSError, RuntimeError, ValueError, TimeoutError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


__all__ = [
    "AtomicJsonStore",
    "BcsClient",
    "HermesBcnBridge",
    "HermesClient",
    "HermesEventStream",
    "HermesRpcError",
    "connector_paths",
    "connector_status",
    "ensure_dashboard_settings",
    "main",
    "register_bot",
    "resolve_hermes_home",
    "run",
    "start_connector",
    "stop_connector",
]


if __name__ == "__main__":
    raise SystemExit(main())
