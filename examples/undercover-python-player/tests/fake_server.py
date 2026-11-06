"""测试用 fake BCS：真实 WebSocket 握手 + 仅记录 onboard/visibility 的 HTTP 服务。"""

from __future__ import annotations

import asyncio
import json
import threading
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from websockets.asyncio.server import serve


class FakeBcsServer:
    def __init__(self, *, is_new: bool = True) -> None:
        self.is_new = is_new
        self.frames: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self.http_requests: list[dict[str, Any]] = []
        self.websocket: Any = None
        self.connected = asyncio.Event()
        self._server: Any = None
        self._http: ThreadingHTTPServer | None = None
        self._http_thread: threading.Thread | None = None
        self.ws_port = 0
        self.http_port = 0

    async def start(self) -> FakeBcsServer:
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def _record(self) -> None:
                length = int(self.headers.get("Content-Length", "0") or 0)
                raw = self.rfile.read(length) if length else b""
                owner.http_requests.append(
                    {
                        "method": self.command,
                        "path": self.path,
                        "body": json.loads(raw) if raw else None,
                        "authorization": self.headers.get("Authorization"),
                    }
                )
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b"{}")

            def do_POST(self) -> None:
                self._record()

            def do_PUT(self) -> None:
                self._record()

            def log_message(self, *_args: object) -> None:
                return

        self._http = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._http_thread = threading.Thread(target=self._http.serve_forever, daemon=True)
        self._http_thread.start()
        self.http_port = self._http.server_address[1]
        self._server = await serve(self._ws_handler, "127.0.0.1", 0)
        self.ws_port = self._server.sockets[0].getsockname()[1]
        return self

    async def close(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        if self._http is not None:
            self._http.shutdown()
            self._http.server_close()
            self._http = None
        if self._http_thread is not None:
            self._http_thread.join(timeout=2)
            self._http_thread = None

    async def _ws_handler(self, websocket: Any) -> None:
        self.websocket = websocket
        self.connected.set()
        async for raw in websocket:
            frame = json.loads(raw)
            await self.frames.put(frame)
            if frame.get("type") == "req" and frame.get("method") == "bot.connect":
                await websocket.send(json.dumps(self._connect_response(frame), ensure_ascii=False))

    def _connect_response(self, frame: dict[str, Any]) -> dict[str, Any]:
        return {
            "type": "res",
            "id": frame.get("id"),
            "ok": True,
            "payload": {
                "is_new": self.is_new,
                "token": "tok-fake",
                "bot_uuid": "bot-fake",
                "protocol_version": 3,
                "min_supported_version": 1,
                "capabilities": {
                    "unified_run_events": True,
                    "tool_result_task_intent": False,
                    "canonical_session_id": True,
                },
                "env": {"BCN_BOT_UUID": "bot-fake", "BCN_BOT_TOKEN": "tok-fake"},
            },
        }

    async def send(self, frame: dict[str, Any]) -> None:
        if self.websocket is None:
            raise RuntimeError("fake BCS has no websocket")
        await self.websocket.send(json.dumps(frame, ensure_ascii=False))

    async def next_frame(self, timeout: float = 5.0) -> dict[str, Any]:
        return await asyncio.wait_for(self.frames.get(), timeout=timeout)

    async def wait_for(
        self,
        predicate: Callable[[dict[str, Any]], bool],
        *,
        timeout: float = 5.0,
    ) -> dict[str, Any]:
        async def _wait() -> dict[str, Any]:
            while True:
                frame = await self.frames.get()
                if predicate(frame):
                    return frame

        return await asyncio.wait_for(_wait(), timeout=timeout)

    async def wait_for_final(self, run_id: str, *, timeout: float = 5.0) -> dict[str, Any]:
        return await self.wait_for(
            lambda frame: (
                frame.get("type") == "event"
                and frame.get("event") == "chat"
                and isinstance(frame.get("payload"), dict)
                and frame["payload"].get("runId") == run_id
                and frame["payload"].get("state") == "final"
            ),
            timeout=timeout,
        )

    async def wait_for_response(self, frame_id: str, *, timeout: float = 5.0) -> dict[str, Any]:
        return await self.wait_for(
            lambda frame: frame.get("type") == "res" and frame.get("id") == frame_id,
            timeout=timeout,
        )
