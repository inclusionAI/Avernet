"""Unit tests for the session file-transfer OSS streaming proxy router.

Exercises the end-to-end proxy slice: client PUT/GET round-trips through the
FastAPI route to the DI-injected ``OssStreamingProxy`` bound to an
``httpx.MockTransport`` upstream, with raw path/query byte fidelity, explicit
Host derivation, and the unconfigured-deployment guard (structured 503).
"""

from collections.abc import Callable, Iterator

import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from secbaas.community.adapters.web.routers.file_proxy import (
    file_transfer_proxy_router,
)
from secbaas.community.plugins.file_transfer._http_proxy import OssStreamingProxy
from tests.unit.adapters.web.conftest import iter_api_routes

app = FastAPI()
app.include_router(file_transfer_proxy_router)

# Path/query exactly as projected by the phase-88 aliyun projector: the OSS
# object key carries a percent-encoded "%2F" and the presigned query carries
# percent-encoded "%2B" — both must reach the upstream byte-identical.
PROXIED_URI = (
    "/api/v1/file-transfer-proxy/root/ten%2Ffile.bin"
    "?OSSAccessKeyId=k&Signature=s%2B%2F&Expires=1"
)

EXPECTED_RAW_PATH = (
    b"/root/ten%2Ffile.bin?OSSAccessKeyId=k&Signature=s%2B%2F&Expires=1"
)

ENDPOINT = "https://oss-cn-hangzhou.aliyuncs.com"
BUCKET = "my-bucket"
EXPECTED_HOST = "my-bucket.oss-cn-hangzhou.aliyuncs.com"


def streamed_response(status_code: int, content: bytes) -> httpx.Response:
    """Build an upstream response shaped as a live async stream.

    httpx materializes plain-byte responses eagerly and marks their stream
    consumed, which maps to a fully buffered ``send()`` — not the
    ``stream=True`` shape the proxy drives.  Wrapping the body in an async
    generator reproduces a live upstream stream for ``aiter_raw()``.
    """

    async def body():
        yield content

    return httpx.Response(status_code, content=body())


class OutboundCapture:
    """Mutable per-test holder for the upstream request/response state."""

    def __init__(self) -> None:
        self.request: httpx.Request | None = None
        self.response: httpx.Response = streamed_response(200, b"")
        self.proxy: OssStreamingProxy | None = None


def _override_proxy(instance: OssStreamingProxy) -> dict:
    """Override every route's ``proxy`` Provide dependency with ``instance``."""
    old_overrides = dict(app.dependency_overrides)
    for route in iter_api_routes(app):
        for dep in route.dependant.dependencies:
            if dep.name == "proxy":
                app.dependency_overrides[dep.call] = lambda: instance
    return old_overrides


@pytest.fixture
def proxy_env() -> Iterator[Callable[[str, str], OutboundCapture]]:
    """Yield a builder producing a capture env wired with a custom proxy."""
    restores: list[dict] = []

    def build(endpoint: str, bucket_name: str) -> OutboundCapture:
        capture = OutboundCapture()

        async def handler(request: httpx.Request) -> httpx.Response:
            capture.request = request
            return capture.response

        proxy = OssStreamingProxy(
            endpoint=endpoint,
            bucket_name=bucket_name,
            transport=httpx.MockTransport(handler),
        )
        capture.proxy = proxy
        restores.append(_override_proxy(proxy))
        return capture

    yield build
    for old_overrides in reversed(restores):
        app.dependency_overrides = old_overrides


async def _request(
    capture: OutboundCapture, method: str, uri: str, **kwargs
) -> httpx.Response:
    """Drive one client request through the ASGI app; return the response."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test/") as client:
        return await client.request(method, uri, **kwargs)


async def _streamed_body(capture: OutboundCapture) -> bytes:
    """Drain and return the bytes the upstream saw as the request body."""
    assert capture.request is not None
    return b"".join([chunk async for chunk in capture.request.stream])


# ==========================================================================
# Tracer tests — happy-path byte fidelity, guard, and DI shape
# ==========================================================================


@pytest.mark.asyncio
async def test_put_upload_round_trip_keeps_raw_path_query_and_body(proxy_env):
    """A PUT upload streams byte-faithfully to the configured OSS upstream."""
    env = proxy_env(ENDPOINT, BUCKET)
    env.response = streamed_response(200, b"ok")
    body = b"\x00\x01upload-payload\xff\xfe"

    response = await _request(
        env,
        "PUT",
        PROXIED_URI,
        content=body,
        headers={"Content-Type": "application/octet-stream"},
    )

    assert response.status_code == 200
    captured = env.request
    assert captured is not None
    assert captured.method == "PUT"
    assert captured.url.raw_path == EXPECTED_RAW_PATH
    assert captured.headers["host"] == EXPECTED_HOST
    assert captured.headers["content-type"] == "application/octet-stream"
    assert await _streamed_body(env) == body


@pytest.mark.asyncio
async def test_get_download_round_trip_relays_status_and_body(proxy_env):
    """A GET download returns the upstream status and body untouched."""
    env = proxy_env(ENDPOINT, BUCKET)
    env.response = streamed_response(200, b"chunk1")

    response = await _request(env, "GET", PROXIED_URI)

    assert response.status_code == 200
    assert response.content == b"chunk1"


@pytest.mark.asyncio
async def test_unconfigured_endpoint_returns_structured_503(proxy_env):
    """An unconfigured proxy raises SessionFileTransferProxyUnavailableError,
    mapped by the router to a structured HTTP 503 — never an open relay."""
    env = proxy_env("", "x")

    response = await _request(env, "GET", PROXIED_URI)

    assert response.status_code == 503
    detail = response.json()["detail"]
    assert detail["error_code"] == "SESSION_FILE_TRANSFER_PROXY_UNAVAILABLE"
    assert detail["reason"]


@pytest.mark.asyncio
async def test_di_shape_injects_override_through_provide_cell(proxy_env):
    """Overriding the ``proxy`` dependency via iter_api_routes injects the
    test instance — proving the Provide cell resolves through the container."""
    env = proxy_env(ENDPOINT, BUCKET)
    env.response = streamed_response(200, b"chunk1")

    route = next(iter_api_routes(app))
    assert [dep.name for dep in route.dependant.dependencies] == ["proxy"]

    response = await _request(env, "GET", PROXIED_URI)

    assert response.status_code == 200
    assert response.content == b"chunk1"
    assert env.request is not None
    assert env.request.headers["host"] == EXPECTED_HOST