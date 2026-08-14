"""ASGI-boundary protection for gateway-appended Caller compatibility tokens."""

from __future__ import annotations

from fastapi import FastAPI
import pytest
from starlette.requests import Request

from agentclaw.community.adapters.http.auth.dependencies import _build_auth_context
from agentclaw.community.adapters.http.middleware import (
    CompatibilityCtokenMiddleware,
    _remove_compatibility_ctoken,
    install_middleware,
)


@pytest.mark.parametrize(
    "path",
    [
        "/api/bots/a-bot/caller-context",
        "/api/bots/a-bot/mcps/a-server/call-type",
        "/api/v1/user-lists/check",
        "/api/v1/user-lists/correct",
    ],
)
def test_removes_ctoken_only_for_opted_in_caller_and_user_list_paths(path):
    scope = {
        "type": "http",
        "path": path,
        "query_string": b"entity_id=member&ctoken=opaque-value&user_list_type=caller_identity",
        "headers": [],
        "scheme": "http",
        "server": ("testserver", 80),
    }

    _remove_compatibility_ctoken(scope)

    assert scope["query_string"] == b"entity_id=member&user_list_type=caller_identity"
    assert "ctoken" not in _build_auth_context(Request(scope)).query_params


def test_does_not_strip_ctoken_from_unrelated_endpoints():
    scope = {
        "type": "http",
        "path": "/api/v1/skillsets",
        "query_string": b"ctoken=opaque-value",
    }

    _remove_compatibility_ctoken(scope)

    assert scope["query_string"] == b"ctoken=opaque-value"


def test_installs_ctoken_sanitizer_as_the_outermost_application_middleware():
    app = FastAPI()

    install_middleware(
        app,
        auth_plugin=object(),
        tracer=_NoopTracer(),
    )

    assert app.user_middleware[0].cls is CompatibilityCtokenMiddleware


class _NoopTracer:
    def install(self, app) -> None:
        del app
