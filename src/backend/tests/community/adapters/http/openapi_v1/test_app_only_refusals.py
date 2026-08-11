"""What an application acting alone is refused, and how indistinguishably.

Two properties, both of which fail silently if they break:

- **Every refused operation is refused**, enumerated from ``ADMISSION`` rather
  than sampled — so an operation that gains a mode by accident is caught here as
  well as in the inventory test, this time by its behaviour rather than its
  wiring.
- **Refusals are indistinguishable.** A caller that could tell "not granted"
  from "no such bot", or "wrong identity type" from "bad credential", has an
  oracle. Compared byte for byte, because "similar" is not the promise.

The whole surface is mounted here — not one group's router — so these run
against the operation set an integrator actually meets.
"""

from __future__ import annotations

import time

import jwt
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agentclaw.community.adapters.http.middleware import AvernetTenantMiddleware
from agentclaw.community.adapters.http.openapi_v1 import build_public_router
from agentclaw.community.adapters.http.openapi_v1.admission import (
    ADMISSION,
    AdmissionMode,
)
from agentclaw.community.adapters.http.openapi_v1.dependencies import PRINCIPAL_HEADER
from agentclaw.community.plugin_api.secret_resolver import SecretResolver
from agentclaw.community.utils.gateway_principal_config import (
    init_principal_verifier_config,
    reset_principal_verifier_config_cache,
)

KEY = "refusal-test-shared-secret-at-least-32-bytes"
TENANT = "acme-tenant"
USER = "u-1"
APP_ID = 42


class _Secret:
    secret_user = "gateway"

    def __init__(self, value: str) -> None:
        self.secret_value = value


@pytest.fixture(autouse=True)
def signing_key():
    class _Resolver(SecretResolver):
        def get_secret(self, secret_name: str) -> object | None:
            return _Secret(KEY)

    init_principal_verifier_config(_Resolver(), "gateway_principal_signing_key",
                                   strict=False)
    yield
    reset_principal_verifier_config_cache()


def _token(*, with_user: bool) -> str:
    now = int(time.time())
    principals: list[dict] = []
    if with_user:
        principals.append(
            {"type": "user", "subject": {"id": USER, "username": "alice@example.com"}}
        )
    principals.append(
        {
            "type": "app",
            "tenant": TENANT,
            "app": {
                "app_id": APP_ID,
                "app_name": "partner",
                "owners": "platform-team",
                "tenant": TENANT,
                "app_type": "integration",
            },
        }
    )
    return jwt.encode(
        {
            "iss": "gateway",
            "aud": "backend",
            "iat": now,
            "exp": now + 60,
            "principals": principals,
        },
        KEY,
        algorithm="HS256",
    )


@pytest.fixture
def client():
    """The whole public surface, with no services bound.

    Nothing needs binding: every request in this file is refused *before* a
    handler runs. If one ever reaches a handler it will fail loudly on a missing
    dependency rather than quietly returning something — which is the failure
    mode worth having.
    """
    from agentclaw.community.adapters.http.app import _unhandled_exception_handler

    app = FastAPI()
    app.add_middleware(AvernetTenantMiddleware)
    app.include_router(build_public_router())
    app.add_exception_handler(Exception, _unhandled_exception_handler)
    return TestClient(app, raise_server_exceptions=False)


def _refused_operations():
    """Every operation the table says must refuse a machine caller.

    Enumerated, never sampled: a sampled list stops covering an operation the
    moment someone adds one, and says nothing about it.
    """
    return sorted(
        (method, path)
        for (method, path), mode in ADMISSION.items()
        if mode is AdmissionMode.REFUSED and method != "WEBSOCKET"
    )


def _concrete(path: str) -> str:
    """A requestable URL for a templated path.

    The values are arbitrary — every one of these operations refuses before it
    could look at them.
    """
    out = []
    for part in path.split("/"):
        if part.startswith("{"):
            out.append("x")
        else:
            out.append(part)
    return "/".join(out)


@pytest.mark.parametrize(
    ("method", "path"), _refused_operations(), ids=lambda v: str(v)
)
def test_every_refused_operation_refuses_an_app_only_caller(client, method, path):
    """All fourteen, by behaviour rather than by wiring.

    ``401`` specifically: the surface's answer for "no caller we can act for",
    the same one an unauthenticated request gets. Not ``403`` — that would say
    the credential was fine and the identity type wrong, which tells a prober
    exactly what to change.
    """
    response = client.request(
        method,
        _concrete(path),
        headers={PRINCIPAL_HEADER: _token(with_user=False)},
        params={"user_id": USER},
    )

    assert response.status_code == 401, response.text


def test_the_socket_plane_refuses_an_app_only_caller_too(client):
    """The one refused operation that cannot answer with a status code.

    A handshake has no body and no status to carry a ``401``, so it is refused
    with close code ``1008`` instead — the same rule, in the only shape the
    plane has. Excluded from the sweep above because it cannot be driven with
    ``client.request``, which is exactly why it needs its own test rather than
    being quietly dropped.
    """
    from starlette.websockets import WebSocketDisconnect

    with pytest.raises(WebSocketDisconnect) as refused:
        with client.websocket_connect(
            f"/openapi/v1/bots/loadtest/ws/echo?user_id={USER}",
            headers={PRINCIPAL_HEADER: _token(with_user=False)},
        ):
            pass

    assert refused.value.code == 1008


def test_the_refusal_is_identical_to_having_no_credential_at_all(client):
    """Byte for byte, so the caller cannot tell which half it failed."""
    refused = client.get(
        "/openapi/v1/bots/logs/traces",
        headers={PRINCIPAL_HEADER: _token(with_user=False)},
        params={"user_id": USER},
    )
    absent = client.get("/openapi/v1/bots/logs/traces", params={"user_id": USER})

    assert refused.status_code == absent.status_code == 401
    assert _without_request_id(refused) == _without_request_id(absent)


def test_a_caller_naming_a_user_still_reaches_the_refused_operations(client):
    """The refusals are about the *caller shape*, not about the operation.

    Bot logs and the authorization group are perfectly ordinary for a human;
    turning them off for everyone would be a much larger change than the one
    being made. A ``401`` here would mean this feature had broken them.
    """
    response = client.get(
        "/openapi/v1/bots/logs/traces",
        headers={PRINCIPAL_HEADER: _token(with_user=True)},
        params={"user_id": USER},
    )

    assert response.status_code != 401, response.text


def _without_request_id(response) -> dict:
    body = dict(response.json())
    body.pop("request_id", None)
    return body


# ── credential shapes this surface never admits ──────────────────────────────


def _token_with(principals: list[dict]) -> str:
    now = int(time.time())
    return jwt.encode(
        {
            "iss": "gateway",
            "aud": "backend",
            "iat": now,
            "exp": now + 60,
            "principals": principals,
        },
        KEY,
        algorithm="HS256",
    )


@pytest.mark.parametrize(
    ("label", "principal"),
    [
        (
            "access_key",
            {
                "type": "access_key",
                "tenant": TENANT,
                "access_key": {
                    "access_key_id": "ak-1",
                    "access_key_token": "secret",
                    "tenant": TENANT,
                },
            },
        ),
        (
            "bot",
            {
                "type": "bot",
                "tenant": TENANT,
                "bot": {
                    "bot_id": "b-1",
                    "owner_id": USER,
                    "session_token": "secret",
                    "tenant": TENANT,
                },
            },
        ),
    ],
)
def test_access_key_and_bot_callers_are_refused_even_on_admitted_operations(
    client, label, principal
):
    """Widening admitted an *application*, and nothing else.

    Both of these are refused during verification, before any route is
    consulted, so the admission table never even gets asked. Asserted on an
    operation that *does* admit a machine caller, because that is where a
    mistaken widening would show.
    """
    response = client.get(
        "/openapi/v1/bots/ceiling",
        headers={PRINCIPAL_HEADER: _token_with([principal])},
        params={"user_id": USER},
    )

    assert response.status_code == 401, response.text
