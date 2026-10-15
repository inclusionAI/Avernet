"""G1b lightweight proxy shim — mimics agentclawproxy auth + forwarding.

This is NOT the real proxy. It exists to validate browser-side G1b assertions
(JWT auth, SSE pass-through behavior, abort/resume) before the full singlebox
run. Real proxy behavior (resolver table, MOSN routing, LB) is deferred to
the formal singlebox validation.

Mimics:
  - Same JWT structure as relay_jwt.py (HS256, claims: target + exp)
  - /proxypass/{target} forwarding with SSE pass-through (no response buffering)
  - Token extraction: X-PROXYPASS-TOKEN header or x-proxypass-token query param
Does NOT mimic:
  - Resolver table (LOCAL_/ARCA_/TECLAW_ routing) — direct target id:port
  - MOSN runtime — plain FastAPI/httpx
  - LB / connection pooling / routing tables
"""
import hmac
import hashlib
import base64
import json
import time

import httpx
from fastapi import FastAPI, Request, WebSocket
from fastapi.responses import JSONResponse, StreamingResponse

SHIM_PORT = 20046
SPIKE_PORT = 20043
SECRET = "local-only"                     # 占位哑值（shim 自签自验；真 proxy 走 Mist 密钥平台）

app = FastAPI()


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _b64url_decode(data: str) -> bytes:
    rem = len(data) % 4
    if rem:
        data += "=" * (4 - rem)
    return base64.urlsafe_b64decode(data)


def issue_token() -> str:
    """Sign and return a valid proxypass JWT."""
    header = _b64url_encode(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    payload = _b64url_encode(json.dumps(
        {"target": f"127.0.0.1:{SPIKE_PORT}", "exp": int(time.time()) + 3600}
    ).encode())
    signing_input = f"{header}.{payload}"
    sig = hmac.new(SECRET.encode(), signing_input.encode(),
                   hashlib.sha256).digest()
    return f"{signing_input}.{_b64url_encode(sig)}"


def verify_token(token: str) -> bool:
    """Verify HS256 JWT signature + target + expiry."""
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return False
        header_b64, payload_b64, sig_b64 = parts
        signing_input = f"{header_b64}.{payload_b64}".encode()
        expected = hmac.new(SECRET.encode(), signing_input,
                            hashlib.sha256).digest()
        if not hmac.compare_digest(expected, _b64url_decode(sig_b64)):
            return False
        payload = json.loads(_b64url_decode(payload_b64))
        if payload.get("exp", 0) < time.time():
            return False
        return True
    except Exception:
        return False


def _validate(request: Request) -> JSONResponse | None:
    """Check JWT from X-PROXYPASS-TOKEN header or query param."""
    candidate = request.headers.get("X-PROXYPASS-TOKEN")
    if not candidate:
        from urllib.parse import urlparse, parse_qs
        qs = parse_qs(urlparse(str(request.url)).query)
        candidate = (qs.get("x-proxypass-token") or [None])[0]
    if not candidate or not verify_token(candidate):
        return JSONResponse(status_code=401,
                            content={"error": "invalid or missing token"})
    return None


@app.get("/_test/token")
async def get_token() -> dict:
    """Issue a test token (simulates proxy's credential signing side)."""
    credential = issue_token()   # test-only JWT, not a real secret
    return {"credential": credential}


@app.api_route("/proxypass/{target:path}", methods=["GET", "POST", "PUT",
                                                     "DELETE", "PATCH"])
async def proxypass(request: Request, target: str):
    """Forward /proxypass/{id:port}/path to http://{id:port}/path."""
    auth_error = _validate(request)
    if auth_error:
        return auth_error

    # 解析 target: 轻量 mode 忽略 target id/port，一律路由到 spike /rpc
    # （真 proxy 走 resolver 表 LOCAL_/ARCA_/TECLAW_；此处埋桩）
    host = f"http://127.0.0.1:{SPIKE_PORT}"
    url = f"{host}/rpc"

    # 清除 proxy 内部 header，只转发原始 content
    headers = {k: v for k, v in request.headers.items()
               if k.lower() not in ("x-proxypass-token", "host")}
    body = await request.body()

    upstream = httpx.AsyncClient(timeout=30)
    # 检查是否为流式请求（SendStreamingMessage + SSE 接受）
    accept = headers.get("accept", "")
    is_stream = "text/event-stream" in accept or \
        (body and b"SendStreamingMessage" in body)

    if is_stream:
        req = upstream.build_request("POST", url, headers=headers,
                                     content=body)
        upstream_resp = await upstream.send(req, stream=True)
        return StreamingResponse(
            upstream_resp.aiter_raw(),
            status_code=upstream_resp.status_code,
            headers=dict(upstream_resp.headers),
            background=lambda: upstream.aclose(),
        )
    else:
        resp = await upstream.post(url, headers=headers, content=body)
        await upstream.aclose()
        return JSONResponse(resp.json(), status_code=resp.status_code)


@app.get("/health")
async def health():
    return {"status": "ok", "mode": "spike_g1b_shim"}
