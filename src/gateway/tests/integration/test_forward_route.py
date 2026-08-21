"""Integration tests for the catch-all forwarding entrypoint.

Wires the real streaming ``HttpxForwarder`` against a stub upstream ASGI app, a
``DomainMap``, and a fake authenticator, then drives it through ``TestClient``.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any, cast

import httpx
import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from gateway.community.adapters.web._forward import (
    _ALL_METHODS,
    _request_body,
    forward_request,
)
from gateway.community.bootstrap._principal_signer import build_principal_signer
from gateway.community.config import ConfigLoader, UserConfig
from gateway.community.core.forwarding import DomainMap
from gateway.community.plugins.forwarder.httpx import HttpxForwarder
from gateway.community.plugins.secret_resolver.community import CommunitySecretResolver
from gateway.community.spi.auth import AuthError
from gateway.community.spi.forwarder import ForwardRequest

Scope = dict[str, Any]
Receive = Callable[[], Awaitable[dict[str, Any]]]
Send = Callable[[dict[str, Any]], Awaitable[None]]


# The gateway signs every forwarded identity, and there is no fallback key, so
# a test that forwards has to provision one exactly as a deployment does: the
# community resolver reads ``{env_prefix}{NAME}_VALUE``.
_TEST_KEY = "integration-test-shared-secret-32b!!"


@pytest.fixture(autouse=True)
def _signing_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AVERNET_SECRET_PRINCIPAL_SIGNING_KEY_VALUE", _TEST_KEY)


def _load_user_config() -> UserConfig:
    return ConfigLoader.load().user_config


def _build_signer():
    uc = _load_user_config()
    return build_principal_signer(
        user_config=uc,
        secret_resolver=CommunitySecretResolver(env_prefix=uc.secret.env_prefix),
    )


async def _stub_upstream(scope: Scope, receive: Receive, send: Send) -> None:
    body = b""
    more = True
    while more:
        msg = await receive()
        body += msg.get("body", b"")
        more = msg.get("more_body", False)

    path, method = scope["path"], scope["method"]
    if path == "/openapi/v1/bots" and method == "GET":
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"set-cookie", b"session=1"),
                    (b"set-cookie", b"csrf=2"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": b'{"code":200000}'})
    elif path == "/openapi/v1/bots/sse" and method == "GET":
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-type", b"text/event-stream")],
            }
        )
        await send(
            {"type": "http.response.body", "body": b"data: 1\n\n", "more_body": True}
        )
        await send({"type": "http.response.body", "body": b"data: 2\n\n"})
    elif path == "/openapi/v1/bots/upload" and method in {"POST", "PUT", "PATCH"}:
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-type", b"application/octet-stream")],
            }
        )
        await send({"type": "http.response.body", "body": body})
    elif path == "/openapi/v1/collaboration/bots/mine" and method == "GET":
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-type", b"application/json")],
            }
        )
        await send({"type": "http.response.body", "body": b'{"source":"bcs"}'})
    elif (
        path == "/openapi/v1/collaboration/groups/group-1/participants/member-1"
        and method == "PATCH"
    ):
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-type", b"application/octet-stream")],
            }
        )
        await send({"type": "http.response.body", "body": body})
    else:
        await send({"type": "http.response.start", "status": 404, "headers": []})
        await send({"type": "http.response.body", "body": b"upstream 404"})


class _FakeAuth:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.fail = False

    async def authenticate(self, method: str, path: str, bundle: object) -> dict:
        self.calls.append((method, path))
        if self.fail:
            raise AuthError("unauthorized")
        return {}


class _FailingRequestStream:
    def __init__(self) -> None:
        self.closed = False

    def __aiter__(self) -> _FailingRequestStream:
        return self

    async def __anext__(self) -> bytes:
        raise RuntimeError("client disconnected")

    async def aclose(self) -> None:
        self.closed = True


class _RequestWithStream:
    def __init__(self, stream: _FailingRequestStream) -> None:
        self._stream = stream

    def stream(self) -> _FailingRequestStream:
        return self._stream


class _EntryFailureForwarder:
    def __init__(self, error: BaseException) -> None:
        self._error = error
        self.request: ForwardRequest | None = None

    @asynccontextmanager
    async def forward(self, request: ForwardRequest) -> AsyncIterator[None]:
        self.request = request
        raise self._error
        yield


def _build(
    upstream_app: Callable[[Scope, Receive, Send], Awaitable[None]] = _stub_upstream,
) -> tuple[FastAPI, _FakeAuth]:
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=upstream_app))  # type: ignore[arg-type]

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        yield
        await client.aclose()

    app = FastAPI(lifespan=lifespan)
    app.state.domain_map = DomainMap.from_config(
        {
            "domains": {
                "bots": {"server": "up"},
                "collaboration": {"server": "bcs"},
            },
            "servers": {
                "up": {"base_url": "http://upstream"},
                "bcs": {"base_url": "http://upstream"},
            },
        },
        variables={},
    )
    app.state.forwarder = HttpxForwarder(client=client)
    auth = _FakeAuth()
    app.state.authenticator = auth
    app.state.principal_signer = _build_signer()
    app.add_api_route("/{full_path:path}", forward_request, methods=_ALL_METHODS)
    return app, auth


def test_unknown_domain_returns_404_without_auth() -> None:
    app, auth = _build()
    with TestClient(app) as client:
        resp = client.get("/openapi/v1/unknown/thing")
    assert resp.status_code == 404
    assert resp.json()["code"] == 404001
    assert auth.calls == []  # domain denied before auth


def test_auth_failure_returns_401_before_forward() -> None:
    app, auth = _build()
    auth.fail = True
    with TestClient(app) as client:
        resp = client.get("/openapi/v1/bots")
    assert resp.status_code == 401
    assert resp.json()["code"] == 401001


async def test_auth_failure_does_not_consume_request_body() -> None:
    body_consumed = False

    async def body() -> AsyncIterator[bytes]:
        nonlocal body_consumed
        body_consumed = True
        yield b"must-not-be-read"

    app, auth = _build()
    auth.fail = True
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://gateway"
    ) as client:
        response = await client.post("/openapi/v1/bots/upload", content=body())

    assert response.status_code == 401
    assert not body_consumed


async def test_request_body_read_failure_closes_source_and_propagates() -> None:
    stream = _FailingRequestStream()
    request = cast(Request, _RequestWithStream(stream))

    with pytest.raises(RuntimeError, match="client disconnected"):
        await _request_body(request)

    assert stream.closed


async def test_forwarder_entry_failure_closes_body_before_returning_502() -> None:
    forwarder = _EntryFailureForwarder(RuntimeError("upstream unavailable"))
    app, _ = _build()
    app.state.forwarder = forwarder
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(
        transport=transport, base_url="http://gateway"
    ) as client:
        response = await client.post(
            "/openapi/v1/bots/upload", content=b"raw-bytes-payload"
        )

    assert response.status_code == 502
    assert response.json()["code"] == 502001
    assert forwarder.request is not None
    assert forwarder.request.body is not None
    with pytest.raises(StopAsyncIteration):
        await anext(forwarder.request.body)


async def test_forwarder_entry_cancellation_closes_body_and_propagates() -> None:
    forwarder = _EntryFailureForwarder(asyncio.CancelledError())
    app, _ = _build()
    app.state.forwarder = forwarder
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(
        transport=transport, base_url="http://gateway"
    ) as client:
        with pytest.raises(asyncio.CancelledError):
            await client.post("/openapi/v1/bots/upload", content=b"raw-bytes-payload")

    assert forwarder.request is not None
    assert forwarder.request.body is not None
    with pytest.raises(StopAsyncIteration):
        await anext(forwarder.request.body)


def test_successful_forward_streams_body_and_preserves_cookies() -> None:
    app, _ = _build()
    with TestClient(app) as client:
        resp = client.get("/openapi/v1/bots")
    assert resp.status_code == 200
    assert resp.json() == {"code": 200000}
    assert resp.headers.get_list("set-cookie") == ["session=1", "csrf=2"]


def test_sse_streaming_forward() -> None:
    app, _ = _build()
    with TestClient(app) as client:
        resp = client.get("/openapi/v1/bots/sse")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "text/event-stream"
    assert resp.text == "data: 1\n\ndata: 2\n\n"


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH"])
def test_upload_body_forwarded_verbatim(method: str) -> None:
    app, _ = _build()
    with TestClient(app) as client:
        resp = client.request(
            method, "/openapi/v1/bots/upload", content=b"raw-bytes-payload"
        )
    assert resp.status_code == 200
    assert resp.content == b"raw-bytes-payload"


async def test_upload_reaches_upstream_before_complete_body_arrives() -> None:
    first_chunk_seen = asyncio.Event()
    release_second_chunk = asyncio.Event()
    seen_body = bytearray()

    async def upstream(scope: Scope, receive: Receive, send: Send) -> None:
        message = await receive()
        seen_body.extend(message.get("body", b""))
        first_chunk_seen.set()
        while message.get("more_body", False):
            message = await receive()
            seen_body.extend(message.get("body", b""))
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": bytes(seen_body)})

    async def client_body() -> AsyncIterator[bytes]:
        yield b"first-"
        await release_second_chunk.wait()
        yield b"second"

    app, _ = _build(upstream)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://gateway"
    ) as client:
        request = asyncio.create_task(
            client.post("/openapi/v1/bots/upload", content=client_body())
        )
        try:
            await asyncio.wait_for(first_chunk_seen.wait(), timeout=1)
        finally:
            release_second_chunk.set()
        response = await request

    assert response.status_code == 200
    assert response.content == b"first-second"


@pytest.mark.parametrize("method", ["GET", "DELETE"])
def test_empty_request_body_is_forwarded_without_chunked_framing(method: str) -> None:
    seen_headers: dict[str, str] = {}

    async def upstream(scope: Scope, receive: Receive, send: Send) -> None:
        for key, value in scope["headers"]:
            seen_headers[key.decode()] = value.decode()
        while True:
            message = await receive()
            assert message.get("body", b"") == b""
            if not message.get("more_body", False):
                break
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    app, _ = _build(upstream)
    with TestClient(app) as client:
        response = client.request(method, "/openapi/v1/bots/empty")

    assert response.status_code == 204
    assert "transfer-encoding" not in seen_headers


def test_collaboration_get_forwards_the_verbatim_path() -> None:
    app, _ = _build()
    with TestClient(app) as client:
        resp = client.get("/openapi/v1/collaboration/bots/mine")
    assert resp.status_code == 200
    assert resp.json() == {"source": "bcs"}


def test_collaboration_patch_forwards_the_body_verbatim() -> None:
    app, _ = _build()
    payload = b'{"role":"member"}'
    with TestClient(app) as client:
        resp = client.patch(
            "/openapi/v1/collaboration/groups/group-1/participants/member-1",
            content=payload,
        )
    assert resp.status_code == 200
    assert resp.content == payload


@pytest.mark.parametrize("method", ["GET", "POST"])
def test_auth_runs_for_known_domain(method: str) -> None:
    app, auth = _build()
    with TestClient(app) as client:
        client.request(method, "/openapi/v1/bots/upload", content=b"x")
    assert (method, "/openapi/v1/bots/upload") in auth.calls


def _test_authenticator(db):
    from gateway.community.bootstrap._authn import build_authenticator
    from gateway.community.config import UserConfig
    from gateway.community.core.access_key import AccessKeyRepository
    from gateway.community.core.app import AppRepository
    from gateway.community.core.bot import BotRepository
    from gateway.community.plugins.authn.access_key_token import AccessKeyTokenStrategy
    from gateway.community.plugins.authn.app_token import AppTokenStrategy
    from gateway.community.plugins.authn.bot_token import BotTokenStrategy
    from gateway.community.plugins.authn.google_token import GoogleUserStrategy

    return build_authenticator(
        strategies={
            "google": GoogleUserStrategy(token_header="x-avernet-google-token"),
            "bot_token": BotTokenStrategy(registry=BotRepository(db)),
            "app_token": AppTokenStrategy(registry=AppRepository(db)),
            "access_key_token": AccessKeyTokenStrategy(
                registry=AccessKeyRepository(db)
            ),
        },
        user_config=UserConfig(),
    )


def test_real_authenticator_admits_google_token_then_forwards() -> None:
    """End-to-end sanity: Authenticator + GoogleUserStrategy (MockTransport) → forward 200."""
    from gateway.community.bootstrap import initialize_database
    from gateway.community.bootstrap._authn import build_authenticator
    from gateway.community.core.authn import IdentityChain
    from gateway.community.plugins.authn.google_token import GoogleUserStrategy
    from gateway.community.plugins.database.sqlite import SqliteDatabasePlugin
    from gateway.community.spi.authn import PrincipalType

    def _userinfo_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"sub": "g-1", "email": "a@example.com"})

    db = initialize_database(SqliteDatabasePlugin())
    authenticator = _test_authenticator(db)
    # Swap the USER chain to a MockTransport google so no real network call.
    authenticator.strategies[PrincipalType.USER] = IdentityChain(
        PrincipalType.USER,
        (
            GoogleUserStrategy(
                token_header="x-avernet-google-token",
                transport=httpx.MockTransport(_userinfo_handler),
            ),
        ),
    )
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=_stub_upstream))  # type: ignore[arg-type]

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        yield
        await client.aclose()

    app = FastAPI(lifespan=lifespan)
    app.state.domain_map = DomainMap.from_config(
        {
            "domains": {"bots": {"server": "up"}},
            "servers": {"up": {"base_url": "http://upstream"}},
        },
        variables={},
    )
    app.state.forwarder = HttpxForwarder(client=client)
    app.state.authenticator = authenticator
    app.state.principal_signer = _build_signer()
    app.add_api_route("/{full_path:path}", forward_request, methods=_ALL_METHODS)

    with TestClient(app) as c:
        resp = c.get(
            "/openapi/v1/bots", headers={"x-avernet-google-token": "google-tok"}
        )
    assert resp.status_code == 200
    assert resp.json() == {"code": 200000}


def test_real_authenticator_rejects_missing_required_identity() -> None:
    """No google token + required user → 401 before forwarding."""
    from gateway.community.bootstrap import initialize_database
    from gateway.community.bootstrap._authn import build_authenticator
    from gateway.community.plugins.database.sqlite import SqliteDatabasePlugin

    db = initialize_database(SqliteDatabasePlugin())
    authenticator = _test_authenticator(db)
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=_stub_upstream))  # type: ignore[arg-type]

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        yield
        await client.aclose()

    app = FastAPI(lifespan=lifespan)
    app.state.domain_map = DomainMap.from_config(
        {
            "domains": {"bots": {"server": "up"}},
            "servers": {"up": {"base_url": "http://upstream"}},
        },
        variables={},
    )
    app.state.forwarder = HttpxForwarder(client=client)
    app.state.authenticator = authenticator
    app.add_api_route("/{full_path:path}", forward_request, methods=_ALL_METHODS)

    with TestClient(app) as c:  # no cookie
        resp = c.get("/openapi/v1/bots")
    assert resp.status_code == 401
    assert resp.json()["code"] == 401001
