"""Unit tests for the session file-transfer OSS streaming proxy router.

Exercises the end-to-end proxy slice: client PUT/GET round-trips through the
FastAPI route to the DI-injected ``OssStreamingProxy`` bound to an
``httpx.MockTransport`` upstream, with raw path/query byte fidelity, explicit
Host derivation, and the unconfigured-deployment guard (structured 503).
"""

from collections.abc import Callable, Iterator

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient
from starlette.requests import Request

from secbaas.community.adapters.web.routers.file_proxy import (
    file_transfer_proxy_router,
)
from secbaas.community.adapters.web.routers.file_proxy.file_transfer_proxy_router import (
    proxy_oss,
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

EXPECTED_RAW_PATH = b"/root/ten%2Ffile.bin?OSSAccessKeyId=k&Signature=s%2B%2F&Expires=1"

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


# ==========================================================================
# Byte-fidelity edge cases, hop-header discipline, relay and guard matrix
# ==========================================================================


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tail", "query", "expected_raw_path"),
    [
        ("/root/ten%2Ffile.bin", "", b"/root/ten%2Ffile.bin"),
        ("/root/a%20b.bin", "", b"/root/a%20b.bin"),
        ("/root/a+b.bin", "", b"/root/a+b.bin"),
        ("/root/a%2Bb.bin", "", b"/root/a%2Bb.bin"),
        ("/root/%C3%A8.bin", "", b"/root/%C3%A8.bin"),
        (
            "/root/file.bin",
            "uploadId=abc&partNumber=3",
            b"/root/file.bin?uploadId=abc&partNumber=3",
        ),
        ("/root/empty-query.bin", "", b"/root/empty-query.bin"),
    ],
    ids=[
        "pct2F",
        "pct20-space",
        "literal-plus",
        "pct2B",
        "multibyte-pctC3A8",
        "multipart-query",
        "empty-query",
    ],
)
async def test_raw_path_query_byte_fidelity(proxy_env, tail, query, expected_raw_path):
    """Every raw path/query fragment reaches the upstream byte-identical —
    no decode/re-encode step may ever touch it (OSS V1 signature binding)."""
    env = proxy_env(ENDPOINT, BUCKET)
    uri = "/api/v1/file-transfer-proxy" + tail + (("?" + query) if query else "")

    await _request(env, "GET", uri)

    captured = env.request
    assert captured is not None
    assert captured.url.raw_path == expected_raw_path


@pytest.mark.asyncio
async def test_hop_header_discipline(proxy_env):
    """Hop-by-hop inbound headers never reach upstream; the OSS signed header
    set (Content-Type/MD5/x-oss-*) passes through verbatim; Host comes from
    bucket + endpoint host, never from the client's Host."""
    env = proxy_env(ENDPOINT, BUCKET)

    await _request(
        env,
        "GET",
        PROXIED_URI,
        headers={
            "Connection": "close",
            "Keep-Alive": "timeout=5",
            "TE": "trailers",
            "Upgrade": "h2c",
            "Host": "evil.example.com",
            "Proxy-Authorization": "Basic dXNlcjpwYXNz",
            "Content-Type": "application/octet-stream",
            "Content-MD5": "1B2M2Y8AsgTpgAmY7PhCfg==",
            "X-Oss-Meta-Foo": "bar",
            "X-Oss-Access-Key-Id": "AK",
        },
    )

    captured = env.request
    assert captured is not None
    outbound_names = {name.lower() for name in captured.headers.keys()}
    for forbidden in (
        "connection",
        "keep-alive",
        "te",
        "upgrade",
        "proxy-authorization",
    ):
        assert forbidden not in outbound_names
    assert captured.headers["content-type"] == "application/octet-stream"
    assert captured.headers["content-md5"] == "1B2M2Y8AsgTpgAmY7PhCfg=="
    assert captured.headers["x-oss-meta-foo"] == "bar"
    assert captured.headers["x-oss-access-key-id"] == "AK"
    assert captured.headers["host"] == EXPECTED_HOST


@pytest.mark.asyncio
async def test_content_length_relay(proxy_env):
    """A PUT with Content-Length relays the same value plus a byte-exact body."""
    env = proxy_env(ENDPOINT, BUCKET)
    body = b"x" * 100

    await _request(
        env,
        "PUT",
        PROXIED_URI,
        content=body,
        headers={"Content-Type": "application/octet-stream"},
    )

    captured = env.request
    assert captured is not None
    assert captured.headers["content-length"] == "100"
    assert await _streamed_body(env) == body


@pytest.mark.asyncio
async def test_put_without_content_length_still_streams(proxy_env):
    """A PUT without Content-Length (chunked inbound) still streams the body."""

    async def body_stream():
        yield b"first-"
        yield b"second"

    env = proxy_env(ENDPOINT, BUCKET)
    await _request(env, "PUT", PROXIED_URI, content=body_stream())

    captured = env.request
    assert captured is not None
    assert "content-length" not in {name.lower() for name in captured.headers.keys()}
    assert await _streamed_body(env) == b"first-second"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status_code", "body"),
    [(403, b"forbidden by oss"), (500, b"upstream exploded")],
    ids=["403", "500"],
)
async def test_upstream_status_and_body_relay(proxy_env, status_code, body):
    """Upstream 4xx/5xx status and body are relayed without any rewrite."""
    env = proxy_env(ENDPOINT, BUCKET)
    env.response = streamed_response(status_code, body)

    response = await _request(env, "GET", PROXIED_URI)

    assert response.status_code == status_code
    assert response.content == body


@pytest.mark.asyncio
async def test_mid_stream_abort_keeps_200_with_truncated_body(proxy_env):
    """An upstream stream that dies mid-transfer ends the client response
    cleanly at 200 with the truncated body — no 500/503 can surface once the
    status line is committed."""
    env = proxy_env(ENDPOINT, BUCKET)

    async def failing_body():
        yield b"a"
        raise httpx.ReadTimeout("simulated read timeout")

    env.response = httpx.Response(200, content=failing_body())

    response = await _request(env, "GET", PROXIED_URI)

    assert response.status_code == 200
    assert response.content == b"a"


@pytest.mark.asyncio
async def test_method_whitelist_returns_405(proxy_env):
    """Methods outside the GET/PUT whitelist get 405 before any forwarding."""
    env = proxy_env(ENDPOINT, BUCKET)

    response = await _request(env, "POST", PROXIED_URI, content=b"nope")

    assert response.status_code == 405
    allow = response.headers["allow"]
    assert "GET" in allow and "PUT" in allow


@pytest.mark.asyncio
async def test_bad_raw_prefix_returns_404(proxy_env):
    """A raw_path failing the prefix guard gets a structured 404 (direct call
    with a hand-built scope — the prefix strip happens before any assembly)."""
    env = proxy_env(ENDPOINT, BUCKET)
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/wrong/prefix/x",
        "raw_path": b"/wrong/prefix/x",
        "query_string": b"",
        "headers": [(b"host", b"test")],
    }
    request = Request(scope)

    with pytest.raises(HTTPException) as exc_info:
        await proxy_oss(request=request, oss_path="x", proxy=env.proxy)

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail["error_code"] == "FILE_PROXY_BAD_PATH"


def test_timeout_pin_freezes_decision_b(proxy_env):
    """The proxy's client timeout is exactly the pinned per-operation constants
    (10/600/600/10) — never the httpx 5s default that cuts large transfers."""
    env = proxy_env(ENDPOINT, BUCKET)
    assert env.proxy is not None
    assert env.proxy._client.timeout == httpx.Timeout(
        connect=10.0, read=600.0, write=600.0, pool=10.0
    )
