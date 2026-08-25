"""The public API's access log: one line per request, naming tenant and caller.

The gap this closes is a *silence* — a successful ``/openapi/v1`` request used to
produce no log line at all, so the first question asked of a live incident
("which tenant made this call?") had no answer. These tests pin the properties an
operator actually reads the line for:

- a successful request produces exactly one line, and it names the tenant and
  every identity the verified caller carries;
- a request whose principal did not verify still produces a line, saying so
  rather than guessing a tenant;
- an unhandled exception produces a line too, and does not change what the
  request answers;
- the line names the route the request matched, under the same ``request_id``
  the caller was handed in the response envelope — the property that makes
  "a caller quoted this id, which endpoint did they hit?" a single grep;
- the caller's own ``X-Request-ID`` is logged as ``client_request_id``, never as
  ``request_id``, so the two ids cannot be confused for one another;
- the internal ``/api`` surface is untouched;
- a credential in the query string never reaches the log.

The app wires the **real** ``AvernetTenantMiddleware`` in the production order,
so the caller the line names is the cached one the route was scoped by rather
than a second verification done for the log's benefit.
"""

from __future__ import annotations

import logging

import pytest
from fastapi import Depends, FastAPI, Request
from fastapi.testclient import TestClient

from agentclaw.community.adapters.http.middleware import AvernetTenantMiddleware
from agentclaw.community.adapters.http.openapi_v1.access_log import (
    PublicApiAccessLogMiddleware,
    redact_query,
)
from agentclaw.community.adapters.http.openapi_v1.dependencies import (
    PRINCIPAL_HEADER,
    Principal,
    require_principal,
)
from agentclaw.community.adapters.http.openapi_v1.errors import MissingPrincipalError
from agentclaw.community.adapters.http.openapi_v1.responses import (
    envelope,
    envelope_errors,
)

from .test_principal_seam import (  # noqa: F401  (signing_key is an autouse fixture)
    TENANT,
    mint,
    signing_key,
)

pytestmark = pytest.mark.integration


@pytest.fixture
def app() -> FastAPI:
    """Public probe routes behind the real tenant + access-log middleware.

    Added in production order: the tenant middleware first, the access log
    after, so the access log is the *outer* of the two and reads the caller the
    inner one resolved.
    """
    from agentclaw.community.adapters.http.app import (
        _principal_error_handler,
        _unhandled_exception_handler,
    )
    from agentclaw.community.core.gateway_principal import PrincipalVerificationError

    app = FastAPI()
    app.add_middleware(AvernetTenantMiddleware)
    app.add_middleware(PublicApiAccessLogMiddleware)
    # Both handlers, registered exactly as ``app.py`` registers them: the
    # principal errors go to the *inner* ExceptionMiddleware (so a 401 is a
    # response this middleware observes), the catch-all to ServerErrorMiddleware
    # outside it (so an unhandled error is an exception it observes). The two
    # placements are what the status assertions below distinguish.
    app.add_exception_handler(MissingPrincipalError, _principal_error_handler)
    app.add_exception_handler(PrincipalVerificationError, _principal_error_handler)
    app.add_exception_handler(Exception, _unhandled_exception_handler)

    @app.get("/openapi/v1/bots/{bot_id}")
    @envelope_errors
    async def get_bot(
        request: Request,
        bot_id: str,
        principal: Principal = Depends(require_principal),
    ):
        return envelope({"bot_id": bot_id}, request)

    @app.get("/openapi/v1/bots/{bot_id}/boom")
    @envelope_errors
    async def boom(
        request: Request,
        bot_id: str,
        principal: Principal = Depends(require_principal),
    ):
        raise RuntimeError("handler blew up")

    @app.get("/api/bots/internal")
    async def internal():
        return {"ok": True}

    return app


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return TestClient(app, raise_server_exceptions=False)


def access_lines(caplog: pytest.LogCaptureFixture) -> list[str]:
    """The completion lines emitted during the block, in order."""
    return [
        record.getMessage()
        for record in caplog.records
        if record.getMessage().startswith("openapi_access ")
    ]


def field(line: str, key: str) -> str:
    """The value of ``key=`` in a log line (unquoted)."""
    for token in line.split(" "):
        if token.startswith(f"{key}="):
            return token[len(key) + 1 :].strip('"')
    raise AssertionError(f"no {key}= in {line!r}")


def test_successful_request_logs_tenant_and_caller(client, caplog):
    with caplog.at_level(logging.INFO):
        response = client.get(
            "/openapi/v1/bots/bot-7",
            headers={PRINCIPAL_HEADER: mint(user_id="u-42", include_app=True)},
        )

    assert response.status_code == 200
    lines = access_lines(caplog)
    assert len(lines) == 1
    line = lines[0]
    assert field(line, "method") == "GET"
    assert field(line, "path") == "/openapi/v1/bots/bot-7"
    # The template, not the instance — a thousand bot ids aggregate to one route.
    assert field(line, "route") == "/openapi/v1/bots/{bot_id}"
    assert field(line, "status") == "200"
    assert field(line, "tenant") == TENANT
    assert field(line, "caller") == "user:u-42+app:1"
    assert float(field(line, "duration_ms")) >= 0


def test_caller_names_every_identity_in_the_set(client, caplog):
    """A user+app route is answering for two identities; both are named."""
    with caplog.at_level(logging.INFO):
        client.get(
            "/openapi/v1/bots/bot-7",
            headers={PRINCIPAL_HEADER: mint(user_id="u-42", include_app=True)},
        )

    assert field(access_lines(caplog)[0], "caller") == "user:u-42+app:1"


def test_unverified_caller_logs_absent_tenant(client, caplog):
    """A 401 is logged, and the line does not invent a tenant for it."""
    with caplog.at_level(logging.INFO):
        response = client.get("/openapi/v1/bots/bot-7")

    assert response.status_code == 401
    line = access_lines(caplog)[0]
    assert field(line, "status") == "401"
    assert field(line, "tenant") == "-"
    assert field(line, "caller") == "-"


def test_unhandled_exception_is_logged_and_still_raised(client, caplog):
    """The line records the failure; the response is still the handler's 500."""
    with caplog.at_level(logging.INFO):
        response = client.get(
            "/openapi/v1/bots/bot-7/boom", headers={PRINCIPAL_HEADER: mint()}
        )

    assert response.status_code == 500
    line = access_lines(caplog)[0]
    assert field(line, "error") == "RuntimeError"
    # No response status passed through this layer — the outer handler made one.
    assert field(line, "status") == "-"
    assert field(line, "tenant") == TENANT


@pytest.fixture
def traced_app() -> FastAPI:
    """The same probe routes, with the trace-id stack wired in production order.

    The default ``app`` fixture omits the tracer, so ``request.state.trace_id``
    is never set there and every id below would be empty. This one adds the
    pieces the real ``install_middleware`` adds, in the order it adds them:
    ``TraceIdMappingMiddleware`` reads the tracer and stashes the id, the
    tracer's own middleware is installed after it (so it runs *outside*, minting
    the id first), and the access log is added last of all so it is outside both
    — which is exactly why it can read the id on the way out.

    ``CommunityTracer`` is the real community binding, not a stub: the id this
    test correlates on is the one a community deployment actually mints.
    """
    from agentclaw.community.adapters.http.app import (
        _principal_error_handler,
        _unhandled_exception_handler,
    )
    from agentclaw.community.adapters.http.middleware import TraceIdMappingMiddleware
    from agentclaw.community.core.gateway_principal import PrincipalVerificationError
    from agentclaw.community.plugins.community.tracer import CommunityTracer

    app = FastAPI()
    tracer = CommunityTracer()
    app.add_middleware(AvernetTenantMiddleware)
    app.add_middleware(TraceIdMappingMiddleware, tracer=tracer)
    tracer.install(app)
    app.add_middleware(PublicApiAccessLogMiddleware)
    # The same handlers, in the same places, as the ``app`` fixture above — a
    # 401 has to be a *response* here, not an escaping exception, or the error
    # envelope whose ``request_id`` the last test correlates on is never built.
    app.add_exception_handler(MissingPrincipalError, _principal_error_handler)
    app.add_exception_handler(PrincipalVerificationError, _principal_error_handler)
    app.add_exception_handler(Exception, _unhandled_exception_handler)

    @app.get("/openapi/v1/bots/{bot_id}")
    @envelope_errors
    async def get_bot(
        request: Request,
        bot_id: str,
        principal: Principal = Depends(require_principal),
    ):
        return envelope({"bot_id": bot_id}, request)

    return app


@pytest.fixture
def traced_client(traced_app: FastAPI) -> TestClient:
    return TestClient(traced_app, raise_server_exceptions=False)


def test_request_id_in_the_line_is_the_one_the_caller_was_given(
    traced_client, caplog
):
    """The whole point: an id from a response envelope finds its endpoint.

    A caller quotes the ``request_id`` their response carried. That one string
    has to appear on the access line, under that name, next to the route it
    matched — otherwise answering "which endpoint was this?" means knowing that
    the envelope's ``request_id`` is really the trace id and searching for a
    field with a different name, which is knowledge nobody reading a bug report
    has.
    """
    with caplog.at_level(logging.INFO):
        response = traced_client.get(
            "/openapi/v1/bots/bot-7", headers={PRINCIPAL_HEADER: mint()}
        )

    quoted = response.json()["request_id"]
    assert quoted  # the tracer is wired; an empty id would make this vacuous

    line = access_lines(caplog)[0]
    assert field(line, "request_id") == quoted
    assert field(line, "route") == "/openapi/v1/bots/{bot_id}"
    assert field(line, "method") == "GET"


def test_the_three_names_for_the_id_agree(traced_client, caplog):
    """Envelope body, ``X-Trace-ID`` header, and both log fields: one value.

    A caller may quote any of the three, and each is produced by a different
    piece of code — the envelope builder, the trace middleware's response
    header, and this log line. Pinned together because a drift between any two
    of them is invisible until an incident, when the id someone quotes matches
    nothing.
    """
    with caplog.at_level(logging.INFO):
        response = traced_client.get(
            "/openapi/v1/bots/bot-7", headers={PRINCIPAL_HEADER: mint()}
        )

    line = access_lines(caplog)[0]
    assert (
        response.json()["request_id"]
        == response.headers["X-Trace-ID"]
        == field(line, "request_id")
        == field(line, "trace_id")
    )


def test_client_request_id_is_the_callers_header_and_not_the_trace_id(
    traced_client, caplog
):
    """The caller's ``X-Request-ID`` is logged, and kept distinct.

    Both ids are worth having — ours answers "which endpoint", theirs is what a
    client quotes from its *own* logs — but they are different ids, and the
    regression this guards is the one where the caller's value occupies the
    ``request_id`` field: a search for an envelope's id would then either miss
    the line or, if a client ever sent that string, hit the wrong request.
    """
    with caplog.at_level(logging.INFO):
        response = traced_client.get(
            "/openapi/v1/bots/bot-7",
            headers={PRINCIPAL_HEADER: mint(), "X-Request-ID": "caller-abc-123"},
        )

    line = access_lines(caplog)[0]
    assert field(line, "client_request_id") == "caller-abc-123"
    assert field(line, "request_id") == response.json()["request_id"]
    assert field(line, "request_id") != "caller-abc-123"


def test_a_failed_request_is_searchable_by_its_error_envelopes_request_id(
    traced_client, caplog
):
    """An error envelope's ``request_id`` finds its line too.

    The failing call is the one anyone actually quotes an id from, and an error
    body is built by a different path (``_error_response``) than a success. If
    only the success path stamped the id the correlation would work for exactly
    the requests nobody needs to look up.
    """
    with caplog.at_level(logging.INFO):
        response = traced_client.get("/openapi/v1/bots/bot-7")  # no principal

    assert response.status_code == 401
    line = access_lines(caplog)[0]
    assert field(line, "request_id") == response.json()["request_id"]
    assert field(line, "route") == "/openapi/v1/bots/{bot_id}"
    assert field(line, "status") == "401"


def test_internal_api_is_not_logged(client, caplog):
    with caplog.at_level(logging.INFO):
        assert client.get("/api/bots/internal").status_code == 200

    assert access_lines(caplog) == []


def test_query_is_logged_with_credentials_redacted(client, caplog):
    with caplog.at_level(logging.INFO):
        client.get(
            "/openapi/v1/bots/bot-7?page=2&access_token=super-secret",
            headers={PRINCIPAL_HEADER: mint()},
        )

    query = field(access_lines(caplog)[0], "query")
    assert "super-secret" not in query
    assert "page=2" in query
    assert "access_token=<redacted>" in query


def test_caller_controlled_fields_are_capped(client, caplog):
    """Every field a caller controls is bounded, including on an unauthenticated
    request — this line is written for a 401 too, so the cap cannot depend on
    having verified anyone first.
    """
    from agentclaw.community.adapters.http.openapi_v1.access_log import (
        _MAX_CLIENT_REQUEST_ID,
        _MAX_UA,
    )

    with caplog.at_level(logging.INFO):
        response = client.get(
            "/openapi/v1/bots/bot-7",
            headers={"x-request-id": "R" * 8000, "user-agent": "U" * 8000},
        )

    assert response.status_code == 401  # no principal: the line still gets written
    line = access_lines(caplog)[0]
    assert len(field(line, "client_request_id")) == _MAX_CLIENT_REQUEST_ID
    assert len(field(line, "ua")) == _MAX_UA


def test_a_broken_log_line_does_not_break_the_request(client, caplog, monkeypatch):
    """The access log runs after the response is sent; it must never fail it.

    An exception escaping here would reach ``ServerErrorMiddleware`` with the
    response already started, truncating a request that actually succeeded.
    """
    import agentclaw.community.adapters.http.openapi_v1.access_log as module

    def _boom(scope):
        raise RuntimeError("projection blew up")

    monkeypatch.setattr(module, "_identity_fields", _boom)

    with caplog.at_level(logging.INFO):
        response = client.get(
            "/openapi/v1/bots/bot-7", headers={PRINCIPAL_HEADER: mint()}
        )

    assert response.status_code == 200
    assert response.json()["data"] == {"bot_id": "bot-7"}
    assert access_lines(caplog) == []
    assert any(
        record.getMessage().startswith("openapi_access_failed")
        for record in caplog.records
    )


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("page=2", "page=2"),
        ("token=abc", "token=<redacted>"),
        ("a=1&api_key=abc&b=2", "a=1&api_key=<redacted>&b=2"),
        ("X-Proxypass-Token=abc", "X-Proxypass-Token=<redacted>"),
        # A parameter that merely *contains* an innocent word is left alone —
        # redacting everything would make the field useless for debugging.
        ("monkey=1", "monkey=1"),
    ],
)
def test_redact_query(raw: str, expected: str):
    assert redact_query(raw) == expected
