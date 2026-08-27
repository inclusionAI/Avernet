"""Endpoint-level coverage for ``GET /api/v1/org/user?user_id=``.

A sibling of ``GET /openapi/v1/org/user`` at a separate prefix with its own
access seam: ``require_org_user_caller`` wraps the cached, signature-verified
``resolve_caller`` and raises ``MissingPrincipalError`` when there is no
verified caller. ``user_id`` is a REQUIRED directory filter; the answer comes
from the staff directory, the tenant from the verified caller. No reader
wired (singlebox/community) ⇒ null fields + ``200``; directory unreachable
(``DeptLookupError``) ⇒ ``5xx``; no or invalid principal ⇒ ``401``.

Unlike the openapi_v1 sibling this route is a plain ``APIRouter``
(``route_class=PublicAPIRoute`` is *not* used — that would force ADMISSION-table
rows and the end-user/app-only adjudication the decoupled surface deliberately
avoids), so a dependency-raised ``MissingPrincipalError`` is not folded into the
public Envelope at the route layer. It surfaces to the app-level
``_principal_error_handler``, which (per its own docstring) routes an internal
``/api`` path reaching the verifier directly to a ``401`` carrying the
``{"detail": "Unauthorized"}`` shape internal callers parse — *not* the
Envelope. Success and the ``DeptLookupError`` ``5xx`` are still the Envelope,
because those are produced in the handler body under ``@envelope_errors``.
"""

from __future__ import annotations

import time

import jwt
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agentclaw.community.adapters.http.middleware import AvernetTenantMiddleware
from agentclaw.community.adapters.http.openapi_v1.dependencies import PRINCIPAL_HEADER
from agentclaw.community.adapters.http.openapi_v1.errors import (
    DeptLookupError,
    MissingPrincipalError,
)
from agentclaw.community.plugin_api.staff_dept import (
    StaffDeptPlugin,
    UserIdentityInfo,
)
from agentclaw.community.utils.avernet_tenant import DEFAULT_AVERNET_TENANT
from agentclaw.community.utils.gateway_principal_config import (
    init_principal_verifier_config,
    reset_principal_verifier_config_cache,
)

# Mounts the route under test (it is a standalone router, not built by
# ``build_public_router``) — imported last so the docstring/read order is stable.
from agentclaw.community.adapters.http.org.router import router as org_user_router  # noqa: E402

KEY = "org-user-api-test-shared-secret-32-bytes-min"
USER = "caller-u-1"
LOOKED_UP = "302992"


class _Secret:
    secret_user = "gateway"

    def __init__(self, value: str) -> None:
        self.secret_value = value


@pytest.fixture(autouse=True)
def signing_key():
    """Boot the verifier with the shared key both cases are judged against.

    The refusal case needs it as much as the happy one: without a booted
    verifier a 401 could come from an unconfigured seam rather than from the
    missing/invalid principal.
    """

    class _Resolver:
        def get_secret(self, _secret_name: str) -> _Secret:
            return _Secret(KEY)

    init_principal_verifier_config(_Resolver(), "org_user_signing_key", strict=False)
    yield
    reset_principal_verifier_config_cache()


def _user() -> dict:
    """The user principal's subject — ``username`` is REQUIRED by the verifier."""
    return {"id": USER, "username": "caller@example.com"}


def _token(user: dict | None) -> str:
    now = int(time.time())
    principals: list[dict] = []
    if user is not None:
        principals.append({"type": "user", "subject": user})
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


def _auth(user: dict | None = None) -> dict[str, str]:
    return {PRINCIPAL_HEADER: _token(user=user)}


def _make_app(staff_dept: StaffDeptPlugin | None = None) -> FastAPI:
    # Mirror the prod app's exception handlers for the exceptions this route can
    # surface: ``_principal_error_handler`` maps a dependency-raised
    # ``MissingPrincipalError`` to the internal-``/api`` 401 (``{"detail": ...}``,
    # not the Envelope); ``_unhandled_exception_handler`` is the catch-all.
    from agentclaw.community.adapters.http.app import (
        _principal_error_handler,
        _unhandled_exception_handler,
    )

    app = FastAPI()
    app.add_middleware(AvernetTenantMiddleware)
    app.include_router(org_user_router)
    app.add_exception_handler(MissingPrincipalError, _principal_error_handler)
    app.add_exception_handler(Exception, _unhandled_exception_handler)
    if staff_dept is not None:
        # The handler resolves the reader off ``app.state.injector``; a stub
        # injector binding ``StaffDeptPlugin`` to ``staff_dept`` is enough.
        app.state.injector = _StubInjector(staff_dept)
    return app


class _StubInjector:
    """Duck injector: ``get(StaffDeptPlugin)`` returns the bound reader."""

    def __init__(self, staff_dept: StaffDeptPlugin) -> None:
        self._staff_dept = staff_dept

    def get(self, interface, scope=None):  # noqa: ARG002
        if interface is StaffDeptPlugin:
            return self._staff_dept
        raise LookupError(interface)


class _Reader:
    """A ``StaffDeptPlugin`` stub returning a fixed record, by work number.

    Only ``get_user_by_work_no`` is exercised by the handler, so the other
    Protocol methods are not implemented — duck typing, not conformance.
    """

    def __init__(
        self,
        info_by_work_no: dict[str, UserIdentityInfo] | None = None,
        raise_on: set[str] | None = None,
    ) -> None:
        self._by = info_by_work_no or {}
        self._raise_on = raise_on or set()

    def get_user_by_work_no(self, *, work_no: str) -> UserIdentityInfo:
        if work_no in self._raise_on:
            raise DeptLookupError("directory down")
        return self._by.get(work_no, UserIdentityInfo(work_no=work_no))


def test_missing_user_id_is_a_validation_failure():
    """``?user_id=`` is required — its absence is a 422, never a directory read."""
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.get("/api/v1/org/user", headers=_auth(_user()))
    assert resp.status_code == 422, resp.text


def test_no_principal_is_the_surface_uniform_refusal():
    """No ``X-Avernet-Principal`` — no answer.

    A decoupled ``/api`` route reaching the verifier directly is answered with
    the internal ``401`` ``{"detail": "Unauthorized"}`` shape (the app-level
    ``_principal_error_handler``), not the public Envelope.
    """
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.get("/api/v1/org/user", params={"user_id": LOOKED_UP})
    assert resp.status_code == 401, resp.text
    assert resp.json()["detail"] == "Unauthorized"


def test_unwired_lookup_returns_null_fields_200():
    """No reader bound ⇒ identity+dept null; ``user_id`` echoes, tenant from caller."""
    client = TestClient(_make_app(), raise_server_exceptions=False)
    resp = client.get(
        "/api/v1/org/user",
        headers=_auth(_user()),
        params={"user_id": LOOKED_UP},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["user_id"] == LOOKED_UP
    assert data["username"] is None
    assert data["tenant"] == DEFAULT_AVERNET_TENANT
    assert data["dept_no"] is None
    assert data["dept_name"] is None
    assert data["dept_path"] is None


def test_wired_reader_returns_lookup_result_200():
    """With a reader, the directory's identity+dept are returned for the id."""
    info = UserIdentityInfo(
        work_no=LOOKED_UP,
        username="alice@example.com",
        display_name="Alice",
        full_name="Alice Zhang",
        dept_no="D-1",
        dept_name="Platform",
        dept_path="Ant/Platform",
    )
    reader = _Reader(info_by_work_no={LOOKED_UP: info})
    client = TestClient(_make_app(staff_dept=reader), raise_server_exceptions=False)
    resp = client.get(
        "/api/v1/org/user",
        headers=_auth(_user()),
        params={"user_id": LOOKED_UP},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["user_id"] == LOOKED_UP
    assert data["username"] == "alice@example.com"
    assert data["display_name"] == "Alice"
    assert data["full_name"] == "Alice Zhang"
    assert data["dept_no"] == "D-1"
    assert data["dept_name"] == "Platform"
    assert data["dept_path"] == "Ant/Platform"


def test_directory_unreachable_surfaces_5xx():
    """``DeptLookupError`` is not caught — infra failure, distinct from no-record 200."""
    reader = _Reader(raise_on={LOOKED_UP})
    client = TestClient(_make_app(staff_dept=reader), raise_server_exceptions=False)
    resp = client.get(
        "/api/v1/org/user",
        headers=_auth(_user()),
        params={"user_id": LOOKED_UP},
    )
    assert resp.status_code >= 500, resp.text
