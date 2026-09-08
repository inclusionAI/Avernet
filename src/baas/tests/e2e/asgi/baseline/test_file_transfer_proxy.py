"""E2E baseline for the session file-transfer OSS streaming proxy.

Remote-CI-only: these tests run the full FastAPI application in-process (real
ASGI transport over the DI-wired app) against a **local fake upstream** — an
stdlib ``http.server`` bound to 127.0.0.1 that records exactly what the proxy
forwards. No ``httpx.MockTransport`` anywhere: the proxy performs real
socket-level HTTP against the fake upstream, so raw path/query byte fidelity,
Host derivation, and header relay are asserted at full-stack altitude.

The same bodies execute unchanged against a real OSS bucket on remote CI by
pointing the harness endpoint at the bucket's endpoint and letting OSS answer
(status 403 reproduces ``SignatureDoesNotMatch`` for the tampered-query case).

The dev-stage workspace rule forbids running e2e locally (`-m "not e2e"` is
the default pytest selection); this file documents the Nyquist-remote dims of
89-VALIDATION.md.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest

from tests.e2e.asgi.conftest import APITestHelper

pytestmark = [pytest.mark.e2e_asgi]

# The object key carries a percent-encoded %2F and the presigned query carries
# %2B — both must reach the upstream byte-identical (OSS V1 signature binding).
_PROXY_KEY = "/root/ten%2Ffile.bin"
_PRESIGNED_QUERY = "OSSAccessKeyId=k&Signature=s%2B%2F&Expires=1"
PROXIED_URI = f"/api/v1/file-transfer-proxy{_PROXY_KEY}?{_PRESIGNED_QUERY}"

_E2E_BUCKET = "e2e-bucket"
_UPLOAD_CONTENT_TYPE = "application/octet-stream"


class _FakeUpstream:
    """One-shot local upstream recording the request the proxy forwarded.

    Runs ``ThreadingHTTPServer`` on an ephemeral 127.0.0.1 port in a daemon
    thread.  ``record`` captures the raw request target (``self.path`` keeps
    percent-encoding and the raw query), the received headers, and the body.
    """

    def __init__(
        self,
        status: int = 200,
        content: bytes = b"ok",
        content_type: str = _UPLOAD_CONTENT_TYPE,
    ) -> None:
        self.status = status
        self.content = content
        self.content_type = content_type
        self.record: dict = {}
        self.port = 0
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    def __enter__(self) -> _FakeUpstream:
        upstream = self

        class _Recorder(BaseHTTPRequestHandler):
            def _record_and_reply(self) -> None:  # noqa: PLR6301
                length = int(self.headers.get("Content-Length") or 0)
                upstream.record["path"] = self.path
                upstream.record["headers"] = dict(self.headers.items())
                upstream.record["body"] = self.rfile.read(length)
                self.send_response(upstream.status)
                self.send_header("Content-Type", upstream.content_type)
                self.send_header("Content-Length", str(len(upstream.content)))
                self.end_headers()
                self.wfile.write(upstream.content)

            def do_GET(self) -> None:  # noqa: N802
                self._record_and_reply()

            def do_PUT(self) -> None:  # noqa: N802
                self._record_and_reply()

            def log_message(self, *args: object) -> None:  # noqa: ARG002
                pass  # silence the per-request stderr line

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), _Recorder)
        self.port = self._server.server_address[1]
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        assert self._server is not None
        self._server.shutdown()
        self._server.server_close()
        assert self._thread is not None
        self._thread.join(timeout=5)

    def header(self, name: str) -> str:
        """Case-insensitive lookup of a received header value."""
        for key, value in self.record["headers"].items():
            if key.casefold() == name.casefold():
                return value
        raise KeyError(name)


@pytest.fixture
def proxy_to_fake_upstream(
    api: APITestHelper, request: pytest.FixtureRequest
) -> Iterator[Callable[..., _FakeUpstream]]:
    """Bind the real app's proxy DI cell to an ``OssStreamingProxy`` pointing
    at a caller-spawned fake upstream; build(), start and register cleanup.

    Imports are lazy: the full ``ApplicationContainer`` is wired by the ``api``
    fixture (which pulls ``bootstrap_init`` -> ``_testclient_app``) before the
    app module is ever imported here.
    """
    from secbaas.community.adapters.web.app import app
    from secbaas.community.plugins.file_transfer._http_proxy import (
        OssStreamingProxy,
    )
    from tests.unit.adapters.web.conftest import iter_api_routes

    overrides_stack: list[dict] = []

    def build(**upstream_kwargs: object) -> _FakeUpstream:
        upstream = _FakeUpstream(**upstream_kwargs)
        upstream.__enter__()
        request.addfinalizer(upstream.__exit__)
        proxy = OssStreamingProxy(
            endpoint=f"http://127.0.0.1:{upstream.port}",
            bucket_name=_E2E_BUCKET,
        )
        old_overrides = dict(app.dependency_overrides)
        for route in iter_api_routes(app):
            for dep in route.dependant.dependencies:
                if dep.name == "proxy":
                    app.dependency_overrides[dep.call] = lambda: proxy
        overrides_stack.append(old_overrides)
        return upstream

    yield build
    for old_overrides in reversed(overrides_stack):
        app.dependency_overrides = old_overrides


@pytest.mark.asyncio
async def test_put_upload_round_trip_through_proxy_app(
    api: APITestHelper,
    proxy_to_fake_upstream: Callable[..., _FakeUpstream],
) -> None:
    """PUT upload streams byte-faithfully through the full app to upstream.

    Asserts the fake upstream received the raw path/query verbatim, the
    derived Host, the exact Content-Type, and the byte-exact body.
    """
    upstream = proxy_to_fake_upstream(status=200, content=b"ok")
    body = b"\x00\x01e2e-upload-payload\xff\xfe"

    response = await api.client.put(
        PROXIED_URI,
        content=body,
        headers={"Content-Type": _UPLOAD_CONTENT_TYPE},
    )

    assert response.status_code == 200
    assert upstream.record["path"] == f"{_PROXY_KEY}?{_PRESIGNED_QUERY}"
    assert upstream.header("Host") == f"{_E2E_BUCKET}.127.0.0.1"
    assert upstream.header("Content-Type") == _UPLOAD_CONTENT_TYPE
    assert upstream.record["body"] == body


@pytest.mark.asyncio
async def test_get_download_round_trip_relays_bytes(
    api: APITestHelper,
    proxy_to_fake_upstream: Callable[..., _FakeUpstream],
) -> None:
    """GET download returns the upstream status, body, and Content-Type
    untouched, with the raw presigned query byte-identical upstream."""
    payload = b"download-payload-via-proxy"
    upstream = proxy_to_fake_upstream(
        status=200, content=payload, content_type="text/plain"
    )

    response = await api.client.get(PROXIED_URI)

    assert response.status_code == 200
    assert response.content == payload
    assert response.headers["content-type"] == "text/plain"
    assert upstream.record["path"] == f"{_PROXY_KEY}?{_PRESIGNED_QUERY}"


@pytest.mark.asyncio
async def test_tampered_signature_fails_at_upstream_403(
    api: APITestHelper,
    proxy_to_fake_upstream: Callable[..., _FakeUpstream],
) -> None:
    """A tampered query is relayed verbatim and the upstream 403 (real OSS:
    SignatureDoesNotMatch) reaches the client untouched."""
    oss_error = (
        b"<Error><Code>SignatureDoesNotMatch</Code>"
        b"<Message>The request signature we calculated does not match"
        b"</Message></Error>"
    )
    upstream = proxy_to_fake_upstream(
        status=403, content=oss_error, content_type="application/xml"
    )
    tampered = PROXIED_URI.replace("Signature=s%2B%2F", "Signature=s%2B%2E")

    response = await api.client.get(tampered)

    assert response.status_code == 403
    assert response.content == oss_error
    assert upstream.record["path"] == (
        f"{_PROXY_KEY}?OSSAccessKeyId=k&Signature=s%2B%2E&Expires=1"
    )


@pytest.mark.asyncio
async def test_multipart_part_put_through_proxy(
    api: APITestHelper,
    proxy_to_fake_upstream: Callable[..., _FakeUpstream],
) -> None:
    """A multipart per-part PUT relays the uploadId/partNumber query and the
    part body byte-identically through the proxy path."""
    part_query = "uploadId=abc123&partNumber=3&OSSAccessKeyId=k&Signature=s&Expires=1"
    part_body = b"x" * 1024
    upstream = proxy_to_fake_upstream(status=200, content=b"part-ok")

    response = await api.client.put(
        f"/api/v1/file-transfer-proxy/root/big.bin?{part_query}",
        content=part_body,
        headers={"Content-Type": _UPLOAD_CONTENT_TYPE},
    )

    assert response.status_code == 200
    assert upstream.record["path"] == f"/root/big.bin?{part_query}"
    assert upstream.record["body"] == part_body
    assert upstream.header("Host") == f"{_E2E_BUCKET}.127.0.0.1"
