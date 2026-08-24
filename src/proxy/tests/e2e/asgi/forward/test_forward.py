"""E2E test — proxypass forwards a real request to a live local upstream."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import threading
import time

import httpx
import pytest
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

_SECRET = "e2e-forward-secret"


def _sign(secret: str, payload: dict) -> str:
    def _b64(data: bytes) -> str:
        return base64.urlsafe_b64encode(data).rstrip(b"=").decode()

    header_b64 = _b64(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    payload_b64 = _b64(json.dumps(payload).encode())
    sig = hmac.new(
        secret.encode(), f"{header_b64}.{payload_b64}".encode(), hashlib.sha256
    ).digest()
    return f"{header_b64}.{payload_b64}.{_b64(sig)}"


def _upstream_app():
    app = FastAPI()

    @app.api_route("/{path:path}", methods=["GET", "POST", "PUT"])
    async def echo(request: Request, path: str):
        return JSONResponse(
            {
                "path": "/" + path,
                "method": request.method,
                "query": dict(request.query_params),
            }
        )

    return app


class _UpstreamServer:
    def __init__(self, port: int):
        self.port = port
        self.thread: threading.Thread | None = None

    def start(self) -> None:
        self.thread = threading.Thread(
            target=uvicorn.run,
            kwargs={
                "app": _upstream_app(),
                "host": "127.0.0.1",
                "port": self.port,
                "log_level": "error",
            },
            daemon=True,
        )
        self.thread.start()
        for _ in range(50):
            try:
                httpx.get(f"http://127.0.0.1:{self.port}/health")
                return
            except httpx.HTTPError:
                time.sleep(0.1)
        raise RuntimeError("upstream did not start")


@pytest.fixture
def upstream_port():
    return 18765


@pytest.mark.e2e
class TestProxypassForward:
    def test_forward_to_live_upstream(self, upstream_port: int) -> None:
        import tempfile
        from pathlib import Path

        secret = _SECRET
        os.environ["SANDBOXPROXY_JWT_SECRET"] = secret

        upstream = _UpstreamServer(upstream_port)
        upstream.start()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                cfg = Path(tmp) / "application.yaml"
                cfg.write_text(
                    "app_name: sandboxproxy\n"
                    "user_config:\n"
                    "  plugins:\n"
                    "    resolver: prefix\n"
                    "    relay_client: stub\n"
                    "  jwt:\n"
                    f"    secret: {secret}\n"
                    "  aliyun_ack_cluster:\n"
                    f"    api_server: http://127.0.0.1:{upstream_port}\n"
                )
                os.environ["SANDBOXPROXY_CONFIG_PATH"] = str(cfg)

                from sandboxproxy.community.adapters.web import build_app
                from sandboxproxy.community.api.identity import (
                    resolve_instance_id,
                )
                from sandboxproxy.community.bootstrap import (
                    ApplicationContainer,
                    initialize_services,
                )
                from sandboxproxy.community.config import ConfigLoader

                loaded = ConfigLoader.load()
                container = ApplicationContainer()
                container.config.from_dict(
                    {
                        "user_config": loaded.user_config.model_dump(),
                        "plugins": {
                            "resolver": "prefix",
                            "relay_client": "stub",
                        },
                        "instance": resolve_instance_id(),
                    }
                )
                initialize_services(container)
                app = build_app(container, loaded)

                from starlette.testclient import TestClient

                with TestClient(app) as client:
                    token = _sign(secret, {"sub": "u1", "exp": time.time() + 3600})
                    resp = client.get(
                        "/proxypass/ARCA_12345:8080/echo?x=1",
                        headers={"Authorization": f"Bearer {token}"},
                    )
                    assert resp.status_code == 200
                    body = resp.json()
                    assert body["path"] == "/echo"
                    assert body["method"] == "GET"
        finally:
            os.environ.pop("SANDBOXPROXY_CONFIG_PATH", None)
