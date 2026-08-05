"""The public-API auth seam: header → verified caller → tenant + owner.

Two seams read one header, and the tests below pin the properties the rest of the
public surface depends on:

- ``require_principal`` produces the verified caller; anything unverifiable is a
  ``401`` whose body is **identical** to the no-credential case;
- ``resolve_avernet_tenant`` binds the caller's tenant for the request, so the
  Track A guard scopes data to the right tenant without any handler's help;
- verification runs **once** per request even though both seams need it;
- **every** public route depends on ``require_principal`` — the property that
  makes the tenant fallback safe.

The app here installs the real ``AvernetTenantMiddleware``, so the tenant
observed inside a handler is the one a production request would get.
"""

from __future__ import annotations

import time
from unittest.mock import patch

import jwt
import pytest
from fastapi import Depends, FastAPI, Request
from fastapi.testclient import TestClient

from agentclaw.community.adapters.http.middleware import AvernetTenantMiddleware
from agentclaw.community.adapters.http.openapi_v1 import build_public_router
from agentclaw.community.adapters.http.openapi_v1.dependencies import (
    PRINCIPAL_HEADER,
    Principal,
    require_principal,
    require_user_and_app_principal,
)
from agentclaw.community.adapters.http.openapi_v1.principal import caller_owner_id
from agentclaw.community.adapters.http.openapi_v1.responses import (
    envelope,
    envelope_errors,
)
from agentclaw.community.utils.avernet_tenant import (
    DEFAULT_AVERNET_TENANT,
    get_current_avernet_tenant,
)
from agentclaw.community.plugin_api.secret_resolver import SecretResolver
from agentclaw.community.utils.gateway_principal_config import (
    init_principal_verifier_config,
    reset_principal_verifier_config_cache,
)

KEY = "seam-test-shared-secret-at-least-32-bytes"
TENANT = "acme-tenant"
SECRET_NAME = "gateway_principal_signing_key"


class _Secret:
    secret_user = "gateway"

    def __init__(self, value: str) -> None:
        self.secret_value = value


def boot_with_key(value: str | None) -> None:
    """Boot the seam with ``value`` as the shared key (``None`` = no such secret).

    The key is a credential, so it is resolved through ``SecretResolver`` at boot
    rather than read off the environment; these tests drive the same entry point
    the composition root uses.
    """

    class _Resolver(SecretResolver):
        def get_secret(self, secret_name: str) -> object | None:
            return None if value is None else _Secret(value)

    init_principal_verifier_config(_Resolver(), SECRET_NAME, strict=False)


@pytest.fixture(autouse=True)
def signing_key():
    """Install the shared key, and drop the process-wide config around it."""
    boot_with_key(KEY)
    yield
    reset_principal_verifier_config_cache()


def mint(
    *,
    tenant: str | None = TENANT,
    user_id: str = "u-1",
    include_app: bool | None = None,
    **overrides,
) -> str:
    """A signed token for a caller, optionally belonging to a tenant.

    ``tenant=None`` mints the **user-only** set: a first-party caller, which
    asserts no tenant and so scopes to the internal default. Any other value
    mints the user alongside an ``app`` principal registered to that tenant,
    because a user principal cannot carry one — see ``gateway_principal/models``.
    """
    now = int(time.time())
    principals: list[dict] = [
        {
            "type": "user",
            "subject": {"id": user_id, "username": "alice@example.com"},
        }
    ]
    if include_app is None:
        include_app = tenant is not None
    if include_app and tenant is not None:
        principals.append(
            {
                "type": "app",
                "tenant": tenant,
                "app": {
                    "app_id": 1,
                    "app_name": "bot-logs-test-app",
                    "owners": user_id,
                    "tenant": tenant,
                    "app_type": "integration",
                },
            }
        )
    claims = {
        "iss": overrides.get("issuer", "gateway"),
        "aud": overrides.get("audience", "backend"),
        "iat": now,
        "exp": now + 60,
        "principals": principals,
    }
    return jwt.encode(claims, overrides.get("key", KEY), algorithm="HS256")


@pytest.fixture
def probe_app():
    """A probe route that reports what the seam produced for the request.

    Wires the two pieces of the real app the seam depends on: the real
    ``AvernetTenantMiddleware``, and **the production catch-all imported from
    ``app.py``** rather than a copy of it (following
    ``test_domain_error_handler.py``'s convention). The import matters — the auth
    seam raises in a *dependency*, so it is that handler, not
    ``@envelope_errors``, that turns the failure into a 401. Mirroring it here
    would let the real wiring be deleted with these tests still green.
    """
    from agentclaw.community.adapters.http.app import _unhandled_exception_handler

    app = FastAPI()
    app.add_middleware(AvernetTenantMiddleware)
    app.add_exception_handler(Exception, _unhandled_exception_handler)

    @app.get("/openapi/v1/bots/_probe")
    @envelope_errors
    async def probe(
        request: Request, principal: Principal = Depends(require_principal)
    ):
        return envelope(
            {
                "owner_id": caller_owner_id(principal),
                "tenant": get_current_avernet_tenant(),
            },
            request,
        )

    @app.get("/openapi/v1/bots/logs/_probe")
    @envelope_errors
    async def bot_logs_probe(
        request: Request,
        principal: Principal = Depends(require_user_and_app_principal),
    ):
        return envelope({"owner_id": caller_owner_id(principal)}, request)

    return app


@pytest.fixture
def client(probe_app):
    # ``raise_server_exceptions=False`` so the catch-all's response is observed
    # instead of the exception being re-raised into the test — a real server
    # returns the response, and that response is what this file is about.
    return TestClient(probe_app, raise_server_exceptions=False)


# ── the happy path ───────────────────────────────────────────────────────────


def test_verified_caller_scopes_owner_and_tenant(client):
    response = client.get(
        "/openapi/v1/bots/_probe", headers={PRINCIPAL_HEADER: mint()}
    )

    assert response.status_code == 200
    assert response.json()["data"] == {"owner_id": "u-1", "tenant": TENANT}


def test_a_first_party_user_scopes_to_the_internal_tenant(client):
    """A caller naming only a user asserts no tenant, so the internal one applies.

    This is the shape the google chain produces, and the shape ``route_security``
    asks for on the whole public surface today: ``user: required`` and nothing
    else. Until an identity that carries a registered tenant is required
    alongside it, every request through this seam lands here.
    """
    response = client.get(
        "/openapi/v1/bots/_probe", headers={PRINCIPAL_HEADER: mint(tenant=None)}
    )

    assert response.status_code == 200
    assert response.json()["data"] == {
        "owner_id": "u-1",
        "tenant": DEFAULT_AVERNET_TENANT,
    }


def test_each_caller_gets_their_own_tenant(client):
    """The tenant is per-request state, never sticky across requests."""
    first = client.get(
        "/openapi/v1/bots/_probe", headers={PRINCIPAL_HEADER: mint(tenant="tenant-a")}
    )
    second = client.get(
        "/openapi/v1/bots/_probe", headers={PRINCIPAL_HEADER: mint(tenant="tenant-b")}
    )

    assert first.json()["data"]["tenant"] == "tenant-a"
    assert second.json()["data"]["tenant"] == "tenant-b"


def test_verification_runs_once_per_request(client):
    """Both seams need the caller; signature work happens a single time."""
    import agentclaw.community.adapters.http.openapi_v1.dependencies as deps

    with patch.object(
        deps, "verify_principal_token", wraps=deps.verify_principal_token
    ) as spy:
        response = client.get(
            "/openapi/v1/bots/_probe", headers={PRINCIPAL_HEADER: mint()}
        )

    assert response.status_code == 200
    assert spy.call_count == 1


def test_bot_logs_requires_user_and_app(client):
    user_only = client.get(
        "/openapi/v1/bots/logs/_probe",
        headers={PRINCIPAL_HEADER: mint(tenant=None)},
    )
    user_and_app = client.get(
        "/openapi/v1/bots/logs/_probe",
        headers={PRINCIPAL_HEADER: mint()},
    )

    assert user_only.status_code == 401
    assert user_and_app.status_code == 200
    assert user_and_app.json()["data"]["owner_id"] == "u-1"


# ── denial, and its uniformity ───────────────────────────────────────────────


def test_no_header_is_unauthorized(client):
    response = client.get("/openapi/v1/bots/_probe")

    assert response.status_code == 401
    assert response.json()["message"] == "Unauthorized"


@pytest.mark.parametrize(
    "token",
    [
        pytest.param("garbage", id="not-a-jwt"),
        pytest.param(None, id="wrong-key"),
    ],
)
def test_unverifiable_token_is_indistinguishable_from_no_credential(client, token):
    """A forger must not learn which part of their token failed."""
    forged = token or mint(key="a-different-secret-that-is-32-bytes-x")

    absent = client.get("/openapi/v1/bots/_probe")
    rejected = client.get("/openapi/v1/bots/_probe", headers={PRINCIPAL_HEADER: forged})

    assert rejected.status_code == absent.status_code == 401
    assert _body_without_request_id(rejected) == _body_without_request_id(absent)


def test_token_for_another_upstream_is_rejected(client):
    response = client.get(
        "/openapi/v1/bots/_probe", headers={PRINCIPAL_HEADER: mint(audience="baas")}
    )

    assert response.status_code == 401


def test_unconfigured_key_denies_everything(client):
    """The pre-auth state is preserved by denying, not by trusting a stub."""
    boot_with_key(None)

    response = client.get(
        "/openapi/v1/bots/_probe", headers={PRINCIPAL_HEADER: mint()}
    )

    assert response.status_code == 401


def test_rejected_caller_never_binds_the_internal_tenant_to_a_handler(client):
    """A denied request must not reach a handler at all — 401 comes first."""
    response = client.get(
        "/openapi/v1/bots/_probe", headers={PRINCIPAL_HEADER: "garbage"}
    )

    assert response.status_code == 401
    assert "data" in response.json()
    assert response.json()["data"] is None


def test_internal_tenant_cannot_be_claimed_over_the_wire(client):
    """A token naming ``teamclaw`` would otherwise read every internal row."""
    response = client.get(
        "/openapi/v1/bots/_probe",
        headers={PRINCIPAL_HEADER: mint(tenant=DEFAULT_AVERNET_TENANT)},
    )

    assert response.status_code == 401


# ── the property that makes the tenant fallback safe ─────────────────────────


def test_public_routes_require_principal():
    """Every route on the public surface must depend on ``require_principal``.

    ``resolve_avernet_tenant`` falls back to the internal tenant when no caller
    verifies, which is only safe because such a request is refused before a
    handler runs. A public route added without the dependency would read the
    internal tenant's data for an unauthenticated caller, so this is checked
    rather than remembered.
    """
    api_routes = _api_routes(build_public_router())
    assert api_routes, "no public routes found — the guard would pass vacuously"

    missing = [
        f"{sorted(route.methods)} {route.path}"
        for route in api_routes
        if not _depends_on_require_principal(route.dependant)
    ]

    assert not missing, f"public routes not gated by require_principal: {missing}"


def test_bot_logs_routes_require_user_and_app_principal():
    bot_logs_routes = [
        route
        for route in _api_routes(build_public_router())
        if route.path.startswith("/openapi/v1/bots/logs")
    ]
    assert bot_logs_routes, "no Bot Logs routes found"

    missing = [
        f"{sorted(route.methods)} {route.path}"
        for route in bot_logs_routes
        if not _depends_on(route.dependant, require_user_and_app_principal)
    ]

    assert not missing, f"Bot Logs routes not gated by User+App: {missing}"


def _api_routes(router) -> list:
    """Every real route under ``router``, flattening included sub-routers.

    ``include_router`` stores a lazy wrapper rather than copying routes, so the
    nesting ``build_public_router`` creates has to be walked rather than read off
    ``router.routes``.
    """
    found = []
    for route in getattr(router, "routes", []):
        if hasattr(route, "dependant"):
            found.append(route)
        elif hasattr(route, "original_router"):
            found.extend(_api_routes(route.original_router))
        else:
            found.extend(_api_routes(route))
    return found


def _depends_on_require_principal(dependant) -> bool:
    """Whether ``require_principal`` appears anywhere in a route's dependency tree."""
    if dependant.call is require_principal:
        return True
    return any(_depends_on_require_principal(sub) for sub in dependant.dependencies)


def _depends_on(dependant, dependency) -> bool:
    if dependant.call is dependency:
        return True
    return any(_depends_on(sub, dependency) for sub in dependant.dependencies)


def _body_without_request_id(response) -> dict:
    body = dict(response.json())
    body.pop("request_id", None)
    return body
