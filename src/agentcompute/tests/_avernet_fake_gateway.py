from __future__ import annotations

import json
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

__all__ = ["FakeAvernetGateway"]

_SSE_BODY = (
    b'data: {"choices":[{"delta":{"content":"Integration "}}]}\n\n'
    b'data: {"choices":[{"delta":{"content":"test passed"}}]}\n\n'
    b"data: [DONE]\n\n"
)


class _Handler(BaseHTTPRequestHandler):
    def _read_body(self) -> str:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return ""
        return self.rfile.read(length).decode("utf-8", errors="replace")

    def _record(self, body: str) -> None:
        headers = {k.lower(): v for k, v in self.headers.items()}
        with self.server._lock:
            self.server.recorded_requests.append(
                {
                    "method": self.command,
                    "path": self.path,
                    "headers": headers,
                    "body": body,
                }
            )

    def _respond(self, status: int, content_type: str, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)

    def do_POST(self) -> None:
        body = self._read_body()
        self._record(body)
        if self.path.startswith("/openapi/v1/bots/with-manifest"):
            bot_id = f"bot-fake-{uuid.uuid4().hex[:8]}"
            payload = json.dumps({"code": 0, "data": {"bot_id": bot_id}}).encode("utf-8")
            self._respond(202, "application/json", payload)
        elif self.path.startswith("/openapi/v1/chat/stream"):
            self._respond(200, "text/event-stream", _SSE_BODY)
        else:
            self._respond(404, "text/plain", b"not found")

    def do_GET(self) -> None:
        self._record("")
        if "/with-manifest/status" in self.path:
            payload = json.dumps({"code": 0, "data": {"state": "READY"}}).encode("utf-8")
            self._respond(200, "application/json", payload)
        else:
            self._respond(404, "text/plain", b"not found")

    def do_DELETE(self) -> None:
        self._record("")
        self._respond(204, "application/json", b"")

    def log_message(self, *args: object) -> None:
        pass


class FakeAvernetGateway:
    def __init__(self) -> None:
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self._server.recorded_requests = []
        self._server._lock = threading.Lock()
        self._port = self._server.server_address[1]
        self._thread: threading.Thread | None = None

    @property
    def recorded_requests(self) -> list[dict]:
        return self._server.recorded_requests

    @property
    def lock(self) -> threading.Lock:
        return self._server._lock

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self._port}"

    def start(self) -> str:
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self.base_url

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=2)
