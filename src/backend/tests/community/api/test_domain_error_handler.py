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

    @app.get("/raise-caused/{which}")
    async def raise_caused(which: str):
        try:
            raise RuntimeError("cache backend unreachable")
        except RuntimeError as exc:
            raise _ROUTE_MAP[which](f"boom-{which}") from exc

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


def test_4xx_domain_error_logs_a_warning_with_its_traceback(client, monkeypatch):
    """A refused request used to leave no trace at all, then one line without a
    stack. It now carries the traceback too: the status is what the caller is
    told, and says nothing about what an operator needs to reconstruct it."""
    from agentclaw.community.adapters.http import app as app_mod

    calls: list[tuple] = []
    monkeypatch.setattr(
        app_mod.logger, "warning",
        lambda *a, **k: calls.append((a, k)),
    )
    client.get("/raise/notfound")
    assert any("[DomainError %s]" in str(a[0]) for a, _ in calls), \
        f"expected a 4xx DomainError warning, got: {calls}"
    assert all(k.get("exc_info") is not None for _, k in calls), \
        "a 4xx must carry its raise site"


def test_3xx_domain_error_logs_at_info_not_warning(client, monkeypatch):
    """``LoginRedirectRequired`` is a step in the login flow, not a fault.

    It is logged so that "every error carries a stack" holds without exception,
    but at info — a redirect must not read as a failure to anything watching
    warnings, and this fires on ordinary unauthenticated traffic.
    """
    from agentclaw.community.adapters.http import app as app_mod

    warnings: list[tuple] = []
    infos: list[tuple] = []
    monkeypatch.setattr(
        app_mod.logger, "warning", lambda *a, **k: warnings.append((a, k)),
    )
    monkeypatch.setattr(
        app_mod.logger, "info", lambda *a, **k: infos.append((a, k)),
    )
    client.get("/raise/redirect")

    assert warnings == [], f"302 must not log at warning, got: {warnings}"
    assert infos, "302 must still be recorded"
    assert all(k.get("exc_info") is not None for _, k in infos)


def test_4xx_raised_from_a_cause_logs_that_cause(client, monkeypatch):
    """The status a caller sees and the detail an operator needs are different
    questions.

    ``SkillSetControlPlaneLockUnavailableError`` answers 409 — the mutation
    fence could not be taken, which is a conflict, not an outage. But it is
    raised ``from`` the cache failure underneath, and keying the traceback off
    the status alone dropped that cause on the floor: the log said the fence
    was unavailable and never said why. A 4xx that wraps a cause now carries
    it.
    """
    from agentclaw.community.adapters.http import app as app_mod

    calls: list[tuple] = []
    monkeypatch.setattr(
        app_mod.logger, "warning",
        lambda *a, **k: calls.append((a, k)),
    )
    client.get("/raise-caused/conflict")

    logged = [k["exc_info"] for _, k in calls if k.get("exc_info") is not None]
    assert logged, f"a caused 4xx must log its cause, got: {calls}"
    cause = logged[0].__cause__
    assert isinstance(cause, RuntimeError)
    assert str(cause) == "cache backend unreachable"


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
# Public HTTPException — 5xx keeps the traceback, 4xx does not
# ============================================================

def _public_http_exception_client():
    """Mount the real ``_http_exception_handler`` on public-prefixed routes."""
    from starlette.exceptions import HTTPException as StarletteHTTPException

    from agentclaw.community.adapters.http import app as app_mod
    from agentclaw.community.adapters.http.openapi_v1 import PUBLIC_API_PREFIX

    app = FastAPI()
    app.add_exception_handler(
        StarletteHTTPException, app_mod._http_exception_handler
    )

    @app.get(f"{PUBLIC_API_PREFIX}/boom/{{status}}")
    async def boom(status: int):
        raise StarletteHTTPException(status_code=status, detail=f"boom-{status}")

    return TestClient(app, raise_server_exceptions=False)


@pytest.mark.parametrize(
    "status,level,wants_traceback",
    [
        # Handler-raised: the raise site is the diagnosis. Both of these are live
        # on the public surface (routines 500, resources upload 502) and neither
        # is in ENVELOPE_ERRORS, so they land in this handler.
        (502, "error", True),
        (500, "error", True),
        # 4xx too: the stack is framework internals for Starlette's own routing
        # failures, but it is the raise site for one thrown inside a handler,
        # and the two are indistinguishable from here.
        (404, "warning", True),
    ],
)
def test_public_http_exception_logging(monkeypatch, status, level, wants_traceback):
    from agentclaw.community.adapters.http import app as app_mod
    from agentclaw.community.adapters.http.openapi_v1 import PUBLIC_API_PREFIX

    calls: list[tuple] = []
    monkeypatch.setattr(
        app_mod.logger, level, lambda *a, **k: calls.append((a, k)),
    )
    resp = _public_http_exception_client().get(f"{PUBLIC_API_PREFIX}/boom/{status}")
    assert resp.status_code == status

    assert calls, f"expected a {level} log for {status}"
    args, kwargs = calls[-1]
    assert f"boom-{status}" in args, "the raised detail must survive in the log"
    assert wants_traceback, "every status through this handler carries a traceback"
    assert kwargs.get("exc_info") is not None, \
        "an HTTPException must carry its raise site"


def _internal_http_exception_client():
    """The same handler on an internal ``/api`` route, which delegates its
    response to FastAPI but must still log."""
    from starlette.exceptions import HTTPException as StarletteHTTPException

    from agentclaw.community.adapters.http import app as app_mod

    app = FastAPI()
    app.add_exception_handler(
        StarletteHTTPException, app_mod._http_exception_handler
    )

    @app.get("/api/boom/{status}")
    async def boom(status: int):
        raise StarletteHTTPException(status_code=status, detail=f"boom-{status}")

    return TestClient(app, raise_server_exceptions=False)


@pytest.mark.parametrize("status,level", [(404, "warning"), (500, "error")])
def test_internal_http_exception_is_logged_at_all(monkeypatch, status, level):
    """Internal routes delegate the *response* to FastAPI, whose default handler
    logs nothing — so an ``HTTPException`` raised inside a route left no record
    whatsoever. ``skill_center`` still raises these by hand in a dozen places.

    The response shape is FastAPI's, unchanged; only the log is new.
    """
    from agentclaw.community.adapters.http import app as app_mod

    calls: list[tuple] = []
    monkeypatch.setattr(
        app_mod.logger, level, lambda *a, **k: calls.append((a, k)),
    )
    resp = _internal_http_exception_client().get(f"/api/boom/{status}")

    assert resp.status_code == status
    assert resp.json() == {"detail": f"boom-{status}"}, "wire shape must not change"
    assert calls, f"an internal {status} must not vanish from the log"
    args, kwargs = calls[-1]
    assert f"boom-{status}" in args
    assert kwargs.get("exc_info") is not None


def test_non_5xx_data_proxy_error_is_logged_at_all(monkeypatch):
    """``_data_proxy_error_handler`` logged only 5xx; anything mapped below that
    returned a response with no record it had happened."""
    from agentclaw.community.adapters.http import app as app_mod
    from agentclaw.community.core.aicoding.services.data_proxy_service import (
        DataProxyError,
    )

    class _Downstream(DataProxyError):
        pass

    monkeypatch.setitem(app_mod._DATA_PROXY_ERROR_STATUS_MAP, _Downstream, 400)

    calls: list[tuple] = []
    monkeypatch.setattr(
        app_mod.logger, "warning", lambda *a, **k: calls.append((a, k)),
    )

    app = FastAPI()
    app.add_exception_handler(DataProxyError, app_mod._data_proxy_error_handler)

    @app.get("/api/proxy-boom")
    async def proxy_boom():
        raise _Downstream("upstream refused", op="read")

    resp = TestClient(app, raise_server_exceptions=False).get("/api/proxy-boom")

    assert resp.status_code == 400
    assert calls, "a non-5xx DataProxyError must not vanish from the log"
    assert calls[-1][1].get("exc_info") is not None


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
