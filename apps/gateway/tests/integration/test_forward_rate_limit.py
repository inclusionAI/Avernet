"""Integration tests for rate-limit admission in the forward path.

Wire the real ``forward_request`` route with a fake authenticator, a counting
forwarder, and an :class:`AdmissionController` backed by a fake limiter, then
drive it through ``TestClient``. These assert the HTTP mapping:

- a confirmed rejection → 429 (+ Retry-After), upstream never called;
- a storage failure/timeout → forwarded (fail open);
- an allowed request → upstream called exactly once;
- an auth failure → no quota consumed;
- a caller with no eligible application identity → unchanged behaviour;
- a streaming response → still streamed.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from gateway.community.adapters.web._forward import _ALL_METHODS, forward_request
from gateway.community.bootstrap._principal_signer import build_principal_signer
from gateway.community.config import ConfigLoader
from gateway.community.core.forwarding import DomainMap
from gateway.community.core.rate_limiting import AdmissionController, PolicyResolver
from gateway.community.plugins.secret_resolver.community import CommunitySecretResolver
from gateway.community.spi.auth import AuthError
from gateway.community.spi.authn import AppPrincipal, PrincipalType, ThirdPartyApp
from gateway.community.spi.forwarder import ForwardRequest
from gateway.community.spi.rate_limiter import (
    Decision,
    RateLimiterUnavailableError,
    RateLimitPolicy,
)

_TEST_KEY = "integration-test-shared-secret-32b!!"


@pytest.fixture(autouse=True)
def _signing_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("principal_signing_key", _TEST_KEY)


def _build_signer():
    uc = ConfigLoader.load().user_config
    return build_principal_signer(
        user_config=uc, secret_resolver=CommunitySecretResolver()
    )


def _app_identities(tenant: str = "t1", app_id: int = 1) -> dict:
    app = ThirdPartyApp(app_id=app_id, app_name="demo", owners="dev", tenant=tenant)
    return {PrincipalType.APP: AppPrincipal(tenant=tenant, app=app)}


class _FakeAuth:
    def __init__(self, identities: dict) -> None:
        self._identities = identities
        self.fail = False
        self.calls: list[tuple[str, str]] = []

    async def authenticate(self, method: str, path: str, bundle: object) -> dict:
        self.calls.append((method, path))
        if self.fail:
            raise AuthError("unauthorized")
        return self._identities


class _CountingForwarder:
    """Records how many times the upstream was contacted, and streams a reply."""

    class _Upstream:
        def __init__(self, chunks: list[bytes]) -> None:
            self.status_code = 200
            self.headers = [("content-type", "text/event-stream")]
            self._chunks = list(chunks)

        @property
        def body(self) -> AsyncIterator[bytes]:
            return self._iter()

        async def _iter(self) -> AsyncIterator[bytes]:
            for c in self._chunks:
                yield c

    def __init__(self, chunks: list[bytes] | None = None) -> None:
        self.count = 0
        self._chunks = chunks or [b"ok"]

    @asynccontextmanager
    async def forward(self, request: ForwardRequest) -> AsyncIterator[object]:
        self.count += 1
        yield self._Upstream(self._chunks)


class _FakeLimiter:
    def __init__(
        self, *, decision: Decision | None = None, error: Exception | None = None
    ):
        self._decision = decision
        self._error = error
        self.calls = 0

    async def try_acquire(self, subject, policy):
        self.calls += 1
        if self._error is not None:
            raise self._error
        assert self._decision is not None
        return self._decision

    async def close(self) -> None:
        return None


def _admission(limiter) -> AdmissionController:
    return AdmissionController(
        limiter=limiter,
        policy_resolver=PolicyResolver(
            default=RateLimitPolicy(rate=5.0, burst=10), overrides={}
        ),
        namespace="ns",
    )


def _build(
    *, identities: dict, limiter: _FakeLimiter, forwarder: _CountingForwarder
) -> tuple[FastAPI, _FakeAuth]:
    app = FastAPI()
    app.state.domain_map = DomainMap.from_config(
        {
            "domains": {"bots": {"server": "up"}},
            "servers": {"up": {"base_url": "http://upstream"}},
        },
        variables={},
    )
    app.state.forwarder = forwarder
    auth = _FakeAuth(identities)
    app.state.authenticator = auth
    app.state.principal_signer = _build_signer()
    app.state.admission = _admission(limiter)
    app.add_api_route("/{full_path:path}", forward_request, methods=_ALL_METHODS)
    return app, auth


def test_confirmed_rejection_returns_429_and_does_not_call_upstream() -> None:
    limiter = _FakeLimiter(
        decision=Decision.reject(retry_after_seconds=0.3, remaining_tokens=0.0)
    )
    forwarder = _CountingForwarder()
    app, _ = _build(identities=_app_identities(), limiter=limiter, forwarder=forwarder)
    with TestClient(app) as client:
        resp = client.get("/openapi/v1/bots")
    assert resp.status_code == 429
    assert resp.json()["code"] == 429001
    assert resp.json()["request_id"]  # envelope format preserved
    # Retry-After rounded up, never 0 (0.3s → 1).
    assert resp.headers["Retry-After"] == "1"
    assert forwarder.count == 0  # upstream never contacted
    assert limiter.calls == 1


def test_allowed_request_calls_upstream_once() -> None:
    limiter = _FakeLimiter(decision=Decision.allow(remaining_tokens=9))
    forwarder = _CountingForwarder()
    app, _ = _build(identities=_app_identities(), limiter=limiter, forwarder=forwarder)
    with TestClient(app) as client:
        resp = client.get("/openapi/v1/bots")
    assert resp.status_code == 200
    assert forwarder.count == 1
    assert limiter.calls == 1


def test_storage_failure_fails_open_and_forwards() -> None:
    limiter = _FakeLimiter(
        error=RateLimiterUnavailableError("redis down", ambiguous=True)
    )
    forwarder = _CountingForwarder()
    app, _ = _build(identities=_app_identities(), limiter=limiter, forwarder=forwarder)
    with TestClient(app) as client:
        resp = client.get("/openapi/v1/bots")
    assert resp.status_code == 200  # not 429, not 503
    assert forwarder.count == 1
    assert limiter.calls == 1


def test_auth_failure_does_not_consume_quota() -> None:
    limiter = _FakeLimiter(decision=Decision.allow(remaining_tokens=9))
    forwarder = _CountingForwarder()
    app, auth = _build(
        identities=_app_identities(), limiter=limiter, forwarder=forwarder
    )
    auth.fail = True
    with TestClient(app) as client:
        resp = client.get("/openapi/v1/bots")
    assert resp.status_code == 401
    assert limiter.calls == 0  # admission never reached
    assert forwarder.count == 0


def test_no_eligible_application_identity_preserves_behaviour() -> None:
    # Empty identity set (e.g. a first-party call): limiter is never consulted
    # and the request forwards exactly as before.
    limiter = _FakeLimiter(decision=Decision.allow(remaining_tokens=9))
    forwarder = _CountingForwarder()
    app, _ = _build(identities={}, limiter=limiter, forwarder=forwarder)
    with TestClient(app) as client:
        resp = client.get("/openapi/v1/bots")
    assert resp.status_code == 200
    assert limiter.calls == 0
    assert forwarder.count == 1


def test_streaming_response_remains_streaming() -> None:
    limiter = _FakeLimiter(decision=Decision.allow(remaining_tokens=9))
    forwarder = _CountingForwarder(chunks=[b"data: 1\n\n", b"data: 2\n\n"])
    app, _ = _build(identities=_app_identities(), limiter=limiter, forwarder=forwarder)
    with TestClient(app) as client:
        with client.stream("GET", "/openapi/v1/bots") as resp:
            assert resp.status_code == 200
            assert resp.headers["content-type"] == "text/event-stream"
            body = b"".join(resp.iter_bytes())
    assert body == b"data: 1\n\ndata: 2\n\n"
    assert forwarder.count == 1


def test_admission_disabled_leaves_forward_path_untouched() -> None:
    # No admission on app.state → no limiting, request forwards.
    forwarder = _CountingForwarder()
    app, _ = _build(
        identities=_app_identities(),
        limiter=_FakeLimiter(decision=Decision.allow(remaining_tokens=9)),
        forwarder=forwarder,
    )
    app.state.admission = None
    with TestClient(app) as client:
        resp = client.get("/openapi/v1/bots")
    assert resp.status_code == 200
    assert forwarder.count == 1
