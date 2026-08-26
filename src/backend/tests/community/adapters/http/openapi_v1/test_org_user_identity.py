"""``GET /openapi/v1/org/user`` — the directory-identity read, by behaviour.

A REQUIRED ``?user_id=`` names the work number whose identity+department to
return; the answer comes from the staff directory (the gateway signs only the
caller, so another user's identity is read off HR, not the principal). A human
caller may name any user — there is no self-only 403; an app-only caller is
refused (byte-identical 401 to an unauthenticated caller, pinned below).

Wiring-level properties — the ``REFUSED`` table entry, the
``refuse_app_only_caller`` declaration, the REQUIRED ``user_id`` parameter and
its "opposite contract" carve-out — are held by ``test_admission_inventory.py``
and ``test_explicit_user_id.py``.
"""

from __future__ import annotations

import time

import jwt
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agentclaw.community.adapters.http.middleware import AvernetTenantMiddleware
from agentclaw.community.adapters.http.openapi_v1 import build_public_router
from agentclaw.community.adapters.http.openapi_v1.dependencies import PRINCIPAL_HEADER
from agentclaw.community.plugin_api.secret_resolver import SecretResolver
from agentclaw.community.utils.avernet_tenant import DEFAULT_AVERNET_TENANT
from agentclaw.community.utils.gateway_principal_config import (
    init_principal_verifier_config,
    reset_principal_verifier_config_cache,
)

KEY = "caller-test-shared-secret-at-least-32-bytes"
TENANT = "acme-tenant"
USER = "u-1"


class _Secret:
    secret_user = "gateway"

    def __init__(self, value: str) -> None:
        self.secret_value = value


@pytest.fixture(autouse=True)
def signing_key():
    class _Resolver(SecretResolver):
        def get_secret(self, secret_name: str) -> object | None:
            return _Secret(KEY)

    init_principal_verifier_config(
        _Resolver(), "gateway_principal_signing_key", strict=False
    )
    yield
    reset_principal_verifier_config_cache()


def _token(
    *,
    user: dict | None,
    include_app: bool = False,
) -> str:
    """A signed token; ``user`` is the ``user`` principal's subject, verbatim."""
    now = int(time.time())
    principals: list[dict] = []
    if user is not None:
        principals.append({"type": "user", "subject": user})
    if include_app:
        principals.append(
            {
                "type": "app",
                "tenant": TENANT,
                "app": {
                    "app_id": 42,
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

    No injector is attached, so the staff-dept reader resolves to ``None`` and
    the looked-up identity+dept fields stay null — the unwired/singlebox shape.
    Mounting the full surface rather than the one group means the route answers
    behind exactly the dependencies production mounts it behind.
    """
    from agentclaw.community.adapters.http.app import _unhandled_exception_handler

    app = FastAPI()
    app.add_middleware(AvernetTenantMiddleware)
    app.include_router(build_public_router())
    app.add_exception_handler(Exception, _unhandled_exception_handler)
    return TestClient(app, raise_server_exceptions=False)


def test_a_missing_user_id_is_a_validation_failure(client):
    """``?user_id=`` is required — its absence is a 422, never a whoami.

    The endpoint is a directory lookup keyed on the passed id; there is no
    absent-param fall-back to the verified principal's own identity.
    """
    token = _token(user={"id": USER, "username": "alice@example.com"})

    response = client.get("/openapi/v1/org/user", headers={PRINCIPAL_HEADER: token})

    assert response.status_code == 422, response.text


def test_a_user_id_param_drives_a_directory_lookup_of_that_user(client):
    """The passed ``user_id`` is authoritative — not ignored, and not a 403.

    The relaxation: a human caller may name any user. Unwired (no injector), the
    reader resolves to None, so the looked-up user's identity+dept answer null —
    but ``user_id`` echoes the requested id and the call is 200, never 403.
    """
    token = _token(user={"id": USER, "username": "alice@example.com"})

    response = client.get(
        "/openapi/v1/org/user",
        headers={PRINCIPAL_HEADER: token},
        params={"user_id": "someone-else"},
    )

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["user_id"] == "someone-else"
    assert data["username"] is None
    assert data["tenant"] == DEFAULT_AVERNET_TENANT
    assert data["dept_no"] is None
    assert data["dept_name"] is None
    assert data["dept_path"] is None


def test_with_user_id_equal_self_returns_200_not_403(client):
    """Naming yourself is accepted — the param is a directory filter, not a
    self-confirm seam, so there is no mismatch-403 path on this operation."""
    token = _token(user={"id": USER, "username": "alice@example.com"})

    response = client.get(
        "/openapi/v1/org/user",
        headers={PRINCIPAL_HEADER: token},
        params={"user_id": USER},
    )

    assert response.status_code == 200, response.text
    assert response.json()["data"]["user_id"] == USER


def test_an_app_alone_is_refused_like_an_unauthenticated_caller(client):
    """Byte for byte: no oracle for 'right credential, wrong identity type'.

    ``?user_id=`` is passed so the only thing refusing the call is the
    principal — the param is required, but auth outranks it: an unauthenticated
    or app-only caller is 401 either way.
    """
    refused = client.get(
        "/openapi/v1/org/user",
        headers={PRINCIPAL_HEADER: _token(user=None, include_app=True)},
        params={"user_id": USER},
    )
    unauthenticated = client.get("/openapi/v1/org/user", params={"user_id": USER})

    assert refused.status_code == 401, refused.text
    assert unauthenticated.status_code == 401, unauthenticated.text
    assert refused.json()["message"] == unauthenticated.json()["message"]
    assert refused.json()["code"] == unauthenticated.json()["code"]
