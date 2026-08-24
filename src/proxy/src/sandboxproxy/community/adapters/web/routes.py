"""HTTP and WebSocket routes for the sandbox-proxy."""

from __future__ import annotations

import asyncio
from typing import Any, cast

import httpx
from fastapi import APIRouter, Request, WebSocket
from fastapi.responses import JSONResponse

from sandboxproxy.community.bootstrap import ApplicationContainer
from sandboxproxy.community.config import Config
from sandboxproxy.community.logger import get_logger

logger = get_logger("routes")


def build_router(container: ApplicationContainer, loaded: Config) -> APIRouter:
    router = APIRouter()
    jwt_verifier = container.jwt_verifier()
    relay_secret = loaded.user_config.jwt.secret

    def _auth(request: Request) -> dict[str, Any] | None:
        auth = request.headers.get("authorization", "")
        if not auth.lower().startswith("bearer "):
            return None
        return cast(dict[str, Any], jwt_verifier.verify(auth[7:].strip()))

    @router.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @router.get("/hello")
    async def hello() -> dict[str, str]:
        return {"status": "healthy", "message": "sandboxproxy"}

    @router.api_route(
        "/proxypass/{target:path}",
        methods=["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"],
    )
    async def proxypass(request: Request, target: str) -> Any:
        if _auth(request) is None:
            return JSONResponse({"detail": "unauthorized"}, status_code=401)

        if request.method == "OPTIONS":
            return JSONResponse(content=None, status_code=204)

        resolver = container.target_resolver()
        forwarding = container.forwarding()

        target_hostport, sep, path = target.partition("/")
        target_path = "/" + path if sep else "/"

        try:
            resolved = resolver.resolve(target_hostport)
        except ValueError as exc:
            return JSONResponse({"detail": str(exc)}, status_code=400)
        except RuntimeError as exc:
            return JSONResponse({"detail": str(exc)}, status_code=502)

        upstream = _upstream_url(resolved)
        extra = _extra_headers(resolved)
        try:
            upstream_resp = await forwarding.forward(
                request, upstream, target_path, extra_headers=extra
            )
        except httpx.HTTPError as exc:
            logger.warning("forward failed: %s", exc)
            return JSONResponse({"detail": "upstream unreachable"}, status_code=502)
        return _to_streaming_response(upstream_resp)

    @router.websocket("/proxypass/{target:path}")
    async def proxypass_ws(websocket: WebSocket, target: str) -> None:
        await websocket.accept()
        resolver = container.target_resolver()
        target_hostport, sep, path = target.partition("/")
        target_path = "/" + path if sep else "/"
        try:
            resolved = resolver.resolve(target_hostport)
        except (ValueError, RuntimeError):
            await websocket.close(code=1008)
            return
        upstream = _upstream_url(resolved)
        ws_url = _to_ws_url(upstream, target_path)
        client = container.forwarding().client
        try:
            async with client.stream(
                "GET", ws_url, headers=_extra_headers(resolved)
            ) as upstream_ws:
                await _bridge(websocket, upstream_ws)
        except httpx.HTTPError:
            await websocket.close(code=1011)

    # -- relay endpoints (client → mng pairing) ---------------------------

    @router.websocket("/wsrelay/{session_id}")
    async def wsrelay(websocket: WebSocket, session_id: str) -> None:
        """Client side: authenticate, query BaaS route, then pair with mng."""
        from sandboxproxy.community.core.authn import authenticate_relay

        await websocket.accept()
        auth = authenticate_relay(
            websocket.headers, websocket.url.path, "wsrelay", relay_secret
        )
        if not auth.ok:
            await websocket.close(code=4401)
            return

        relay_server = container.relay_server()
        relay_client = container.relay_client()

        route_info = await relay_client.get_route_info(session_id)
        if route_info is None or route_info.get("status") != "active":
            await websocket.close(code=4503)
            return

        fut = await relay_server.connect_client(session_id)
        if fut is None:
            await websocket.close(code=1008)
            return
        mng_ws = await fut
        try:
            await _relay_bridge(websocket, mng_ws)
        finally:
            await relay_server.close_session(session_id)

    @router.websocket("/wsrevrelay/{session_id}")
    async def wsrevrelay(websocket: WebSocket, session_id: str) -> None:
        """Mng (reverse) side: authenticate, write active route, wait for client."""
        from sandboxproxy.community.core.authn import authenticate_relay

        await websocket.accept()
        auth = authenticate_relay(
            websocket.headers, websocket.url.path, "wsrevrelay", relay_secret
        )
        if not auth.ok:
            await websocket.close(code=4401)
            return

        relay_server = container.relay_server()
        if not await relay_server.register_mng(session_id):
            await websocket.close(code=4502)
            return
        relay_server.signal_mng_ready(session_id, websocket)
        fut = await relay_server.wait_for_client(session_id)
        if fut is None:
            await websocket.close()
            return
        client_ws = await fut
        try:
            await _relay_bridge(websocket, client_ws)
        finally:
            await relay_server.close_session(session_id)

    return router


def _upstream_url(resolved: dict[str, str]) -> str:
    host = (
        resolved.get("arca_host")
        or resolved.get("teclaw_host")
        or resolved.get("baas_host")
        or ""
    )
    if not host.startswith(("http://", "https://")):
        host = "https://" + host
    return host


def _to_ws_url(upstream: str, target_path: str) -> str:
    url = upstream.rstrip("/") + target_path
    if url.startswith("http://"):
        return "ws://" + url[len("http://") :]
    if url.startswith("https://"):
        return "wss://" + url[len("https://") :]
    return url


def _extra_headers(resolved: dict[str, str]) -> dict[str, str]:
    headers: dict[str, str] = {}
    for key in (
        "x-target-bot-id",
        "x-andc-target-service",
        "x-env-id",
        "x-agent-sandbox-id",
        "x-agent-sandbox-port",
        "x-agent-sandbox-api-key",
    ):
        if key in resolved:
            headers[key] = resolved[key]
    if "local_path_prefix" in resolved:
        headers["x-local-path-prefix"] = resolved["local_path_prefix"]
    return headers


def _to_streaming_response(upstream_resp: httpx.Response) -> Any:
    from starlette.background import BackgroundTask
    from starlette.responses import StreamingResponse

    excluded = {
        "content-length",
        "transfer-encoding",
        "connection",
        "content-encoding",
    }
    headers = {
        k: v for k, v in upstream_resp.headers.items() if k.lower() not in excluded
    }
    return StreamingResponse(
        upstream_resp.aiter_raw(),
        status_code=upstream_resp.status_code,
        headers=headers,
        background=BackgroundTask(upstream_resp.aclose),
    )


async def _bridge(websocket: WebSocket, upstream_ws: httpx.Response) -> None:
    """Stream raw bytes between an upstream connection and a client websocket."""

    async def to_client() -> None:
        async for chunk in upstream_ws.aiter_bytes():
            await websocket.send_bytes(chunk)

    async def to_upstream() -> None:
        while True:
            message = await websocket.receive()
            if message["type"] == "websocket.disconnect":
                break
            if "bytes" in message:
                await upstream_ws.aclose()
                break

    await asyncio.gather(to_client(), to_upstream())


async def _relay_bridge(a: WebSocket, b: WebSocket) -> None:
    from sandboxproxy.community.core.relay import bidirectional_forward

    await bidirectional_forward(a, b)
