"""The gateway answers browser CORS at its own edge.

A cross-origin browser call reaches the gateway before it reaches anything else,
and its preflight carries no credential — so the questions here are whether the
preflight is answered *without* routing or authenticating, and whether the
allow-list that answers it is the configured one.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

# The response type ``TestClient`` returns — starlette's test transport is built
# on httpx2, so a bare ``httpx.Response`` annotation would not describe it.
from httpx2 import Response

from gateway.community.adapters.web._cors import EXPOSED_HEADERS, install_cors
from gateway.community.adapters.web.app import create_app
from gateway.community.config import CorsConfig, UserConfig

_ALLOWED = "https://frontend.example.com"
_FOREIGN = "https://not-the-frontend.example.com"


def _app(cors: CorsConfig) -> tuple[FastAPI, list[str]]:
    """An app whose only route records that it ran, behind the edge middleware."""
    app = FastAPI()
    reached: list[str] = []

    @app.api_route("/openapi/v1/bots/all", methods=["GET", "OPTIONS", "POST"])
    async def _route() -> dict[str, bool]:
        reached.append("route")
        return {"ok": True}

    install_cors(app, cors)
    return app, reached


def _preflight(client: TestClient, origin: str, method: str = "GET") -> Response:
    return client.options(
        "/openapi/v1/bots/all",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": method,
            "Access-Control-Request-Headers": "authorization,content-type",
        },
    )


def test_preflight_is_answered_at_the_edge_without_reaching_the_route() -> None:
    app, reached = _app(CorsConfig(allow_origins=[_ALLOWED], allow_origin_regex=[]))
    resp = _preflight(TestClient(app), _ALLOWED)

    assert resp.status_code == 200
    assert resp.headers["access-control-allow-origin"] == _ALLOWED
    assert resp.headers["access-control-allow-credentials"] == "true"
    # The preflight carries no credential, so it must never reach the forward
    # route — where domain resolution and authentication would refuse it.
    assert reached == []


def test_preflight_mirrors_the_requested_headers_and_method() -> None:
    app, _ = _app(CorsConfig(allow_origins=[_ALLOWED], allow_origin_regex=[]))
    resp = _preflight(TestClient(app), _ALLOWED, method="POST")

    assert resp.status_code == 200
    allowed_headers = resp.headers["access-control-allow-headers"]
    assert "authorization" in allowed_headers
    assert "content-type" in allowed_headers
    assert "POST" in resp.headers["access-control-allow-methods"]


def test_simple_response_carries_the_origin_and_the_exposed_headers() -> None:
    app, reached = _app(CorsConfig(allow_origins=[_ALLOWED], allow_origin_regex=[]))
    resp = TestClient(app).get("/openapi/v1/bots/all", headers={"Origin": _ALLOWED})

    assert reached == ["route"]
    assert resp.headers["access-control-allow-origin"] == _ALLOWED
    exposed = resp.headers["access-control-expose-headers"]
    assert all(header in exposed for header in EXPOSED_HEADERS)


def test_unlisted_origin_gets_no_allow_origin_header() -> None:
    app, _ = _app(CorsConfig(allow_origins=[_ALLOWED], allow_origin_regex=[]))
    client = TestClient(app)

    assert "access-control-allow-origin" not in _preflight(client, _FOREIGN).headers
    simple = client.get("/openapi/v1/bots/all", headers={"Origin": _FOREIGN})
    assert "access-control-allow-origin" not in simple.headers


@pytest.mark.parametrize(
    ("origin", "allowed"),
    [
        ("https://team.preview.example.com", True),
        ("https://other.internal.example.com", True),
        # fullmatch, not a prefix match: a host that merely *starts* with an
        # allowed one is a different origin and must not be admitted.
        ("https://team.preview.example.com.evil.test", False),
        ("https://team.preview.example.org", False),
    ],
)
def test_each_configured_regex_is_matched_whole(origin: str, allowed: bool) -> None:
    app, _ = _app(
        CorsConfig(
            allow_origins=[],
            allow_origin_regex=[
                r"https://[a-z0-9-]+\.preview\.example\.com",
                r"https://[a-z0-9-]+\.internal\.example\.com",
            ],
        )
    )
    headers = _preflight(TestClient(app), origin).headers

    assert ("access-control-allow-origin" in headers) is allowed


def test_neutral_default_admits_a_localhost_ui() -> None:
    app, _ = _app(CorsConfig())
    headers = _preflight(TestClient(app), "http://localhost:8000").headers

    assert headers["access-control-allow-origin"] == "http://localhost:8000"


def test_the_served_app_answers_a_preflight_from_the_shipped_allow_list() -> None:
    """The wiring, not just the middleware: the app built from configs/ answers.

    ``create_app`` is where the allow-list is read and the middleware attached,
    so a preflight reaching the real app is what proves an operator's ``cors``
    block is in force — and that it is answered without a domain, a route or a
    credential.
    """
    resp = _preflight(TestClient(create_app()), "http://localhost:8000")

    assert resp.status_code == 200
    assert resp.headers["access-control-allow-origin"] == "http://localhost:8000"


def test_a_pattern_may_open_with_a_global_inline_flag() -> None:
    """Each configured regex is compiled on its own, so its own flags survive.

    ``(?i)`` is legal only at the very start of an expression; combining the
    configured patterns into one alternation would move it and raise
    ``re.error: global flags not at the start`` while Starlette builds the
    middleware stack — the gateway would refuse to serve rather than refuse an
    origin.
    """
    app, _ = _app(
        CorsConfig(
            allow_origins=[],
            allow_origin_regex=[
                r"(?i)https://frontend\.example\.com",
                r"https://[a-z0-9-]+\.preview\.example\.com",
            ],
        )
    )
    client = TestClient(app)

    assert (
        _preflight(client, "https://FRONTEND.example.com").headers[
            "access-control-allow-origin"
        ]
        == "https://FRONTEND.example.com"
    )
    # The second pattern keeps its own (case-sensitive) semantics.
    assert (
        "access-control-allow-origin"
        not in _preflight(client, "https://TEAM.preview.example.com").headers
    )


def test_an_escaped_exception_still_answers_with_the_origin() -> None:
    """``ServerErrorMiddleware`` writes its 500 outside every added middleware.

    Without the handler ``install_cors`` registers, a browser sees that 500 only
    as a CORS failure and cannot tell the request reached the gateway at all.
    """
    app = FastAPI()

    @app.get("/openapi/v1/bots/boom")
    async def _boom() -> None:
        raise RuntimeError("upstream exploded")

    install_cors(app, CorsConfig(allow_origins=[_ALLOWED], allow_origin_regex=[]))
    client = TestClient(app, raise_server_exceptions=False)

    resp = client.get("/openapi/v1/bots/boom", headers={"Origin": _ALLOWED})
    assert resp.status_code == 500
    assert resp.headers["access-control-allow-origin"] == _ALLOWED
    assert resp.headers["access-control-allow-credentials"] == "true"

    # An origin the edge does not admit learns nothing from the failure either.
    foreign = client.get("/openapi/v1/bots/boom", headers={"Origin": _FOREIGN})
    assert foreign.status_code == 500
    assert "access-control-allow-origin" not in foreign.headers


def test_wildcard_origin_is_refused_at_config_load() -> None:
    """``"*"`` must not boot: with credentials on, Starlette echoes every origin."""
    with pytest.raises(ValueError, match=r"must not contain"):
        CorsConfig(allow_origins=["*"])

    # And the refusal reaches a real config load, not just direct construction.
    with pytest.raises(ValueError, match=r"must not contain"):
        UserConfig.model_validate({"cors": {"allow_origins": ["*"]}})
