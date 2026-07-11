#!/usr/bin/env python3
"""Standalone bridge between BCN protocol v2 and Hermes Dashboard JSON-RPC."""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import os
import secrets
import socket
import tempfile
import time
import uuid
from collections import defaultdict
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from websockets.asyncio.client import connect as ws_connect


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
    uri: str, connector: Callable[..., Any] = ws_connect
) -> Any:
    """Disable environment proxies on websockets 15 without breaking 14.x."""

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
    ) -> None:
        self.url = url
        self.bot_id = bot_id
        self.token = token
        self.credential_store = credential_store
        self.heartbeat_interval = heartbeat_interval
        self.reconnect_delays = tuple(reconnect_delays) or (1.0,)
        self._websocket: Any | None = None
        self._send_lock = asyncio.Lock()
        self._next_id = 0
        self._seq = 0

    async def run(
        self,
        handler: FrameHandler,
        *,
        stop_event: asyncio.Event | None = None,
    ) -> None:
        stop = stop_event or asyncio.Event()
        attempt = 0
        while not stop.is_set():
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
            await websocket.close()

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
    session_ready: asyncio.Future[str]
    task: asyncio.Task[None] | None = None
    aborted: bool = False


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
                session_ready=asyncio.get_running_loop().create_future(),
            )
            self._active[run_id] = active
            active.task = self._spawn(self._handle_send(run_id, params, active))
        elif method == "chat.inject":
            await self.bcs.send_response(request_id, {})
            self._append_observation(params)
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
        try:
            async with self._locks[group]:
                session_id = await self._ensure_session(group, params)
                if not active.session_ready.done():
                    active.session_ready.set_result(session_id)
                prompt, observations = self._build_prompt(group, params)
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
                            await self._send_chat_event(run_id, group, "aborted")
                        elif status == "error":
                            await self._send_chat_event(
                                run_id,
                                group,
                                "error",
                                text=str(payload.get("text") or "Hermes request failed"),
                            )
                        else:
                            await self._send_chat_event(
                                run_id,
                                group,
                                "final",
                                text=str(payload.get("text") or cumulative),
                                usage=payload.get("usage"),
                            )
                    elif event_type == "error":
                        await self._send_chat_event(
                            run_id,
                            group,
                            "error",
                            text=str(payload.get("message") or "Hermes request failed"),
                        )
        except asyncio.CancelledError:
            raise
        except BaseException as exc:
            if not active.session_ready.done():
                active.session_ready.set_exception(exc)
                active.session_ready.exception()
            if not active.aborted:
                _log(
                    logging.WARNING,
                    "bridge_turn_failed",
                    group=group,
                    error_type=type(exc).__name__,
                )
                await self._send_chat_event(
                    run_id, group, "error", text="Hermes is unavailable"
                )
        finally:
            if self._active.get(run_id) is active:
                self._active.pop(run_id, None)

    async def _handle_abort(self, payload: dict[str, Any]) -> None:
        run_id = payload.get("run_id")
        active = self._active.get(run_id) if isinstance(run_id, str) else None
        if active is None:
            return
        active.aborted = True
        try:
            session_id = await active.session_ready
            await self.hermes.interrupt_session(session_id)
        except BaseException as exc:
            _log(
                logging.WARNING,
                "bridge_abort_failed",
                group=active.group,
                error_type=type(exc).__name__,
            )
            return
        if active.task is not None and not active.task.done():
            active.task.cancel()
        await self._send_chat_event(run_id, active.group, "aborted")
        self._active.pop(run_id, None)

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
            result = await self.hermes.resume_session(stored)
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
        observations.append(observation)
        while len(observations) > _OBSERVATION_LIMIT or self._observation_size(
            observations
        ) > _OBSERVATION_BYTES:
            observations.pop(0)
        self._save_state()

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
        group = params.get("bcs_group_id")
        if isinstance(group, str) and group:
            return group
        session_key = params.get("session_key")
        for candidate, state in self._state["groups"].items():
            if isinstance(state, dict) and state.get("session_key") == session_key:
                return candidate
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


class _OwnedDashboard:
    def __init__(self, hermes_home: Path, port: int, token: str) -> None:
        self.hermes_home = hermes_home
        self.port = port
        self.token = token
        self.process: asyncio.subprocess.Process | None = None

    async def start(self) -> asyncio.subprocess.Process:
        env = os.environ.copy()
        env["HERMES_HOME"] = str(self.hermes_home)
        env["HERMES_DASHBOARD_SESSION_TOKEN"] = self.token
        self.process = await asyncio.create_subprocess_exec(
            "hermes",
            "dashboard",
            "--host",
            "127.0.0.1",
            "--port",
            str(self.port),
            "--no-open",
            env=env,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        return self.process

    async def stop(self) -> None:
        process = self.process
        if process is None or process.returncode is not None:
            return
        process.terminate()
        try:
            await asyncio.wait_for(process.wait(), timeout=10)
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()


def _free_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


async def _supervise_dashboard(
    dashboard: _OwnedDashboard, stop: asyncio.Event
) -> None:
    delays = (1, 2, 4, 8, 16, 30)
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
        try:
            await asyncio.wait_for(stop.wait(), timeout=delays[min(attempt, 5)])
            return
        except asyncio.TimeoutError:
            attempt += 1
            await dashboard.start()


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
        port = _free_loopback_port()
        hermes_token = secrets.token_urlsafe(32)
        hermes_url = f"http://127.0.0.1:{port}"
        owned = _OwnedDashboard(home, port, hermes_token)
        await owned.start()

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
    )
    hermes = HermesClient(hermes_url, hermes_token)
    bridge = HermesBcnBridge(
        bcs, hermes, AtomicJsonStore(state_path), workspace=workspace
    )
    try:
        if owned is not None:
            await _wait_for_dashboard(hermes, owned)
            supervisor = asyncio.create_task(
                _supervise_dashboard(owned, stop), name="hermes-dashboard-supervisor"
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


__all__ = [
    "AtomicJsonStore",
    "BcsClient",
    "HermesBcnBridge",
    "HermesClient",
    "HermesEventStream",
    "HermesRpcError",
    "run",
]
