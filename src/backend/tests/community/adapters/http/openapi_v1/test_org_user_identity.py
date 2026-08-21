"""``GET /openapi/v1/org/user`` — the user-identity read, by behaviour.

The one operation whose answer *is* the end user, so the properties worth
pinning are about where that answer comes from and who gets one at all:

- a caller naming an end user gets **the principal's** identity back — the
  subject id, username and profile attributes the gateway signed, never
  anything the request supplied;
- the tenant in the answer is the tenant the request was scoped by: the
  internal default for a first-party user-only set, the asserted one when a
  machine principal rides along;
- an application acting alone is refused with the same ``401`` an
  unauthenticated caller gets (the sweep in ``test_app_only_refusals.py``
  covers this operation too; the test here pins the byte-identity).

Wiring-level properties — the ``REFUSED`` table entry, the
``refuse_app_only_caller`` declaration, the absent ``user_id`` parameter —
are held by ``test_admission_inventory.py`` and ``test_explicit_user_id.py``.
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
    the dept fields stay null — the unwired/singlebox shape. Mounting the full
    surface rather than the one group means the route answers behind exactly
    the dependencies production mounts it behind.
    """
    from agentclaw.community.adapters.http.app import _unhandled_exception_handler

    app = FastAPI()
    app.add_middleware(AvernetTenantMiddleware)
    app.include_router(build_public_router())
    app.add_exception_handler(Exception, _unhandled_exception_handler)
    return TestClient(app, raise_server_exceptions=False)


def test_a_user_reads_the_identity_the_gateway_signed(client):
    """The full subject comes back, off the principal and nothing else."""
    token = _token(
        user={
            "id": USER,
            "username": "alice@example.com",
            "display_name": "Alice",
            "full_name": "Alice Zhang",
        }
    )

    response = client.get("/openapi/v1/org/user", headers={PRINCIPAL_HEADER: token})

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data == {
        "user_id": USER,
        "username": "alice@example.com",
        "display_name": "Alice",
        "full_name": "Alice Zhang",
        # A user-only set asserts no tenant and scopes to the internal default.
        "tenant": DEFAULT_AVERNET_TENANT,
        # Department is not on the signed principal; the staff-dept reader is
        # ``None`` here (no injector), so the fields answer null — the unwired
        # shape. The wired case is covered in test_staff_dept.py.
        "dept_no": None,
        "dept_name": None,
        "dept_path": None,
    }


def test_absent_profile_attributes_answer_null_not_invented(client):
    """The optional fields are the contract's absence, not a fabrication."""
    token = _token(user={"id": USER, "username": "alice@example.com"})

    response = client.get("/openapi/v1/org/user", headers={PRINCIPAL_HEADER: token})

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["display_name"] is None
    assert data["full_name"] is None


def test_an_app_riding_along_does_not_change_whose_identity_it_is(client):
    """A human request with an App on the wire is still the human's whoami —
    and it lands in the App's asserted tenant, like every request it rides."""
    token = _token(
        user={"id": USER, "username": "alice@example.com"}, include_app=True
    )

    response = client.get("/openapi/v1/org/user", headers={PRINCIPAL_HEADER: token})

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["user_id"] == USER
    assert data["tenant"] == TENANT


def test_an_app_alone_is_refused_like_an_unauthenticated_caller(client):
    """Byte for byte: no oracle for 'right credential, wrong identity type'."""
    refused = client.get(
        "/openapi/v1/org/user",
        headers={PRINCIPAL_HEADER: _token(user=None, include_app=True)},
    )
    unauthenticated = client.get("/openapi/v1/org/user")

    assert refused.status_code == 401, refused.text
    assert unauthenticated.status_code == 401, unauthenticated.text
    assert refused.json()["message"] == unauthenticated.json()["message"]
    assert refused.json()["code"] == unauthenticated.json()["code"]


def test_the_answer_ignores_a_user_id_smuggled_into_the_query(client):
    """The operation takes no ``user_id`` — one supplied anyway changes nothing.

    Not a 422: an undeclared query parameter is ignored on this surface, and
    the identity comes off the signed principal either way.
    """
    token = _token(user={"id": USER, "username": "alice@example.com"})

    response = client.get(
        "/openapi/v1/org/user",
        headers={PRINCIPAL_HEADER: token},
        params={"user_id": "someone-else"},
    )

    assert response.status_code == 200, response.text
    assert response.json()["data"]["user_id"] == USER
