"""a2a test support errors; focused regression tests and fixtures."""

from __future__ import annotations

import json
import socket
import threading
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse


def make_stringified_error_app(body: Any, status_code: int = 200) -> FastAPI:
    """错误面速写：body 原样 JSON 化返回——含 JSON 裸串与非对象 error 字段。

    2026-10-29 预发实证：代理层曾回 JSON 裸串错误体，E1 二分把它当
    JSON-RPC 错误对象 .get，炸成 AttributeError 并盖住真实错误。
    """
    app = FastAPI()

    @app.get("/.well-known/agent-card.json")
    def card() -> dict:
        return {
            "supportedInterfaces": [
                {"protocolBinding": "JSONRPC", "protocolVersion": "1.0"}
            ]
        }

    @app.post("/rpc")
    async def rpc(request: Request) -> Any:
        await request.json()
        return JSONResponse(body, status_code=status_code)

    return app


def _make_broken_body_server():
    """TCP 裸服务器：GET card 正常 200；POST /rpc 只回 502 响应头
    （声明 Content-Length: 100）后立刻关连接——错误体永远到不了。

    触发的具体传输异常形态不固定（RemoteProtocolError 或空串
    ReadError），断言只钉结构化返回与资源关闭，不断言异常文本形态。
    """

    class _BrokenBodyServer:
        def __init__(self) -> None:
            self._sock = socket.socket()
            self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self._sock.bind(("127.0.0.1", 0))
            self._sock.listen(8)
            self._thread = threading.Thread(target=self._serve, daemon=True)
            self._thread.start()

        def _serve(self) -> None:
            while True:
                try:
                    conn, _ = self._sock.accept()
                except OSError:
                    return
                with conn:
                    try:
                        head = conn.recv(65536)
                    except OSError:
                        continue
                    if head.startswith(b"GET"):
                        body = json.dumps(
                            {
                                "supportedInterfaces": [
                                    {
                                        "protocolBinding": "JSONRPC",
                                        "protocolVersion": "1.0",
                                    }
                                ]
                            }
                        ).encode()
                        conn.sendall(
                            b"HTTP/1.1 200 OK\r\n"
                            b"Content-Type: application/json\r\n"
                            b"Content-Length: "
                            + str(len(body)).encode()
                            + b"\r\n\r\n"
                            + body
                        )
                    else:
                        conn.sendall(
                            b"HTTP/1.1 502 Bad Gateway\r\n"
                            b"Content-Type: application/json\r\n"
                            b"Content-Length: 100\r\n\r\n"
                        )
                        conn.close()  # 体永不到达 → aread 炸

        def stop(self) -> None:
            self._sock.close()

        @property
        def base(self) -> str:
            return f"http://127.0.0.1:{self._sock.getsockname()[1]}"

    return _BrokenBodyServer()
