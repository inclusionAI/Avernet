"""Verify the DomainError -> JSONResponse handler.

The handler must reproduce, bit-for-bit, the wire format that the
previous ``fastapi.HTTPException(status_code=X, detail="msg")`` calls
produced — otherwise the frontend / clients see a behavior change.

We build a minimal FastAPI app with the same handler registration
used in ``agentclaw.community.adapters.http.app``, mount stub routes that raise each
DomainError subclass, and assert the response status + JSON body
match the legacy contract.
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from agentclaw.community.core.errors import (
    Conflict,
    DomainError,
    Forbidden,
    InternalError,
    LoginRedirectRequired,
    NotFound,
    Unauthorized,
    ValidationError,
)


def _build_app() -> FastAPI:
    # Mirror the prod handler wiring exactly — import the map and both
    # handlers from api.app so test and prod stay in sync.
    from agentclaw.community.adapters.http.app import (
        _DOMAIN_ERROR_STATUS_MAP,
        _domain_error_handler,
        _unhandled_exception_handler,
    )

    app = FastAPI()
    app.add_exception_handler(DomainError, _domain_error_handler)
    app.add_exception_handler(Exception, _unhandled_exception_handler)
    # Suppress reference warning on imported map; tests below assert via it.
    _ = _DOMAIN_ERROR_STATUS_MAP

    @app.get("/raise/{which}")
    async def raiser(which: str):
        cls = _ROUTE_MAP[which]
        raise cls(f"boom-{which}")

    @app.get("/raise-unhandled")
    async def raise_unhandled():
        raise RuntimeError("kaboom-runtime")

    return app


_ROUTE_MAP = {
    "validation": ValidationError,
    "unauthorized": Unauthorized,
    "redirect": LoginRedirectRequired,
    "forbidden": Forbidden,
    "notfound": NotFound,
    "conflict": Conflict,
    "internal": InternalError,
}


@pytest.fixture(scope="module")
def client():
    # raise_server_exceptions=False so the catch-all Exception handler
    # is actually exercised (otherwise TestClient re-raises and we never
    # see the JSONResponse it produced).
    return TestClient(
        _build_app(), follow_redirects=False, raise_server_exceptions=False
    )


@pytest.mark.parametrize(
    "which,expected_status",
    [
        ("validation", 400),
        ("unauthorized", 401),
        ("redirect", 302),
        ("forbidden", 403),
        ("notfound", 404),
        ("conflict", 409),
        ("internal", 500),
    ],
)
def test_handler_matches_legacy_httpexception_wire_format(client, which, expected_status):
    resp = client.get(f"/raise/{which}")
    assert resp.status_code == expected_status
    assert resp.json() == {"detail": f"boom-{which}"}


def test_redirect_response_has_no_location_header(client):
    """Preserves today's slightly-broken 302 behavior — bare status, JSON body,
    NO Location header. Cleaning this up to a real redirect is a separate
    follow-up that requires a frontend change too.
    """
    resp = client.get("/raise/redirect")
    assert resp.status_code == 302
    assert "location" not in {k.lower() for k in resp.headers.keys()}
    assert resp.json() == {"detail": "boom-redirect"}


# ============================================================
# Unhandled exception path — uniform JSON {"detail": ...} shape
# ============================================================

def test_unhandled_exception_returns_json_500(client):
    """Any uncaught non-DomainError exception is wrapped to JSON 500
    instead of Starlette's default plain-text 'Internal Server Error'.
    """
    resp = client.get("/raise-unhandled")
    assert resp.status_code == 500
    assert resp.headers["content-type"].startswith("application/json")
    assert resp.json() == {"detail": "Internal Server Error"}


def test_unhandled_exception_logs_traceback(client, monkeypatch):
    """The catch-all calls logger.exception with the unhandled error."""
    from agentclaw.community.adapters.http import app as app_mod

    calls: list[tuple] = []
    monkeypatch.setattr(
        app_mod.logger, "exception",
        lambda *a, **k: calls.append((a, k)),
    )
    client.get("/raise-unhandled")
    assert any("Unhandled exception" in str(a[0]) for a, _ in calls), \
        f"expected 'Unhandled exception' log, got: {calls}"


def test_5xx_domain_error_logs_traceback_but_4xx_does_not(client, monkeypatch):
    """InternalError (500) calls logger.exception; ValidationError (400) does not."""
    from agentclaw.community.adapters.http import app as app_mod

    calls: list[tuple] = []
    monkeypatch.setattr(
        app_mod.logger, "exception",
        lambda *a, **k: calls.append((a, k)),
    )
    client.get("/raise/internal")
    client.get("/raise/validation")
    fmt_strings = [a[0] for a, _ in calls]
    assert any("DomainError 5xx" in s for s in fmt_strings), \
        "InternalError should have triggered logger.exception"
    # Only the InternalError call should be present; ValidationError must not log.
    assert sum(1 for s in fmt_strings if "DomainError 5xx" in s) == 1


def test_4xx_domain_error_logs_one_compact_warning(client, monkeypatch):
    """A refused request used to leave no trace at all. It now logs one line —
    without a traceback, so a per-401 stack does not bury the real 5xx ones."""
    from agentclaw.community.adapters.http import app as app_mod

    calls: list[tuple] = []
    monkeypatch.setattr(
        app_mod.logger, "warning",
        lambda *a, **k: calls.append((a, k)),
    )
    client.get("/raise/notfound")
    assert any("[DomainError %s]" in str(a[0]) for a, _ in calls), \
        f"expected a 4xx DomainError warning, got: {calls}"
    assert all("exc_info" not in k for _, k in calls), \
        "4xx must not carry a traceback"

    # 3xx stays silent: LoginRedirectRequired is a step in the login flow, not
    # a failure anyone debugs.
    calls.clear()
    client.get("/raise/redirect")
    assert calls == [], f"302 must not log, got: {calls}"


def test_handler_logs_the_params_stashed_by_the_public_decorator(monkeypatch):
    """The arguments captured inside the route survive to the app-level handler.

    ``@envelope_errors`` re-raises anything it does not map; by the time this
    handler answers, the frame that knew the arguments is gone, so the decorator
    leaves them on the request scope for exactly this reason.
    """
    from agentclaw.community.adapters.http import app as app_mod
    from agentclaw.community.adapters.http.error_logging import remember_call_params

    calls: list[tuple] = []
    monkeypatch.setattr(
        app_mod.logger, "exception",
        lambda *a, **k: calls.append((a, k)),
    )

    app = FastAPI()
    app.add_exception_handler(DomainError, app_mod._domain_error_handler)

    @app.get("/boom")
    async def boom(request: Request):
        remember_call_params(request, {"bot_id": "b-77"})
        raise InternalError("kaboom")

    client = TestClient(app, raise_server_exceptions=False)
    assert client.get("/boom").status_code == 500
    rendered = [str(a) for a, _ in calls]
    assert any("b-77" in line for line in rendered), \
        f"expected the stashed params in the log args, got: {rendered}"


# ============================================================
# Trace-ID propagation — every response carries X-Trace-ID
# ============================================================

class _FakeTracer:
    """Test double for ``TracerPlugin``: ``current_trace_id`` returns a fixed
    value (a present id, or ``None`` for the no-tracer case). ``install`` is a
    no-op — the test reads the id directly, so no real backend is needed."""

    def __init__(self, trace_id: str | None):
        self._trace_id = trace_id

    def install(self, app) -> None:  # no backend to attach
        pass

    def current_trace_id(self) -> str | None:
        return self._trace_id


def _build_app_with_trace_middleware(tracer):
    """Builds a mini app that mounts TraceIdMappingMiddleware (fed the given
    tracer) + both error handlers, so we can prove X-Trace-ID lands on
    success, 4xx, 5xx (DomainError), and unhandled-exception responses
    uniformly.
    """
    from fastapi import FastAPI
    from agentclaw.community.adapters.http.app import (
        _DOMAIN_ERROR_STATUS_MAP,
        _domain_error_handler,
        _unhandled_exception_handler,
    )
    from agentclaw.community.adapters.http.middleware import TraceIdMappingMiddleware

    app = FastAPI()
    app.add_middleware(TraceIdMappingMiddleware, tracer=tracer)
    app.add_exception_handler(DomainError, _domain_error_handler)
    app.add_exception_handler(Exception, _unhandled_exception_handler)
    _ = _DOMAIN_ERROR_STATUS_MAP

    @app.get("/ok")
    async def ok():
        return {"ok": True}

    @app.get("/bad")
    async def bad():
        raise ValidationError("nope")

    @app.get("/oops")
    async def oops():
        raise InternalError("boom")

    @app.get("/kaboom")
    async def kaboom():
        raise RuntimeError("kaboom")

    return TestClient(app, follow_redirects=False, raise_server_exceptions=False)


@pytest.mark.parametrize("path,expected_status", [
    ("/ok",      200),
    ("/bad",     400),
    ("/oops",    500),
    ("/kaboom",  500),
])
def test_x_trace_id_present_on_every_response_when_tracer_active(
    path, expected_status,
):
    """With a tracer that yields an id (prod), every response — including 5xx
    — carries X-Trace-ID. Pre-refactor, unhandled exceptions bypassed the
    middleware and lost the header; the catch-all handler fixes that.
    """
    client = _build_app_with_trace_middleware(_FakeTracer("trace-abc"))
    resp = client.get(path)
    assert resp.status_code == expected_status
    assert resp.headers.get("X-Trace-ID") == "trace-abc", \
        f"X-Trace-ID missing/wrong on {path} (status {expected_status})"


def test_x_trace_id_absent_when_no_tracer_active():
    """Local-mode parity: a tracer yielding no id ⇒ no X-Trace-ID header.
    Matches the pre-seam behavior exactly — we do not mint server-side IDs.
    """
    client = _build_app_with_trace_middleware(_FakeTracer(None))
    resp = client.get("/ok")
    assert "x-trace-id" not in {k.lower() for k in resp.headers.keys()}


def test_x_trace_id_does_not_echo_incoming_request_id():
    """X-Trace-ID is the server's trace id, never a verbatim echo of the
    frontend's X-Request-ID. With no id active, no header is emitted at all —
    the request_id is logged, not echoed.
    """
    client = _build_app_with_trace_middleware(_FakeTracer(None))
    resp = client.get("/ok", headers={"X-Request-ID": "client-abc-123"})
    assert resp.headers.get("X-Trace-ID") is None
