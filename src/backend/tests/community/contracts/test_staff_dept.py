"""Rule 25 conformance — StaffDeptPlugin.

Consumer under test: ``GET /openapi/v1/org/user`` (``adapters/http/openapi_v1/
org/router.py``). The whoami resolves ``StaffDeptPlugin`` off
``request.app.state.injector`` and threads the returned :class:`StaffDeptInfo`
into the response dept fields. The local impl returns an all-``None`` info ("no
dept") so singlebox/community whoamis stay available with null dept.

Three shapes are pinned, the failure-mode contract the most important:

- local/community: all-``None`` (no dept) → 200 + null dept, identity present;
- a wired real impl that returns dept values → they propagate;
- a wired impl that raises :class:`DeptLookupError` (directory down) → 5xx,
  identity **not** returned — "infra broken" kept distinct from "no dept".

Plugin-hit assertion: the local impl's call is recorded by ``MockSeam`` (only
the wired reader is ever consulted), and the all-``None`` shape is producible
only by the local/community impls.
"""
from __future__ import annotations

import time

import jwt
from fastapi import FastAPI
from fastapi.testclient import TestClient
from fastapi_injector import attach_injector
from injector import Injector, Module, singleton

from agentclaw.community.adapters.http.app import _unhandled_exception_handler
from agentclaw.community.adapters.http.middleware import AvernetTenantMiddleware
from agentclaw.community.adapters.http.openapi_v1 import build_public_router
from agentclaw.community.adapters.http.openapi_v1.dependencies import PRINCIPAL_HEADER
from agentclaw.community.adapters.http.openapi_v1.errors import DeptLookupError
from agentclaw.community.plugin_api.staff_dept import (
    DeptSearchItem,
    StaffDeptInfo,
    StaffDeptPlugin,
)
from agentclaw.community.utils.avernet_tenant import DEFAULT_AVERNET_TENANT
from agentclaw.community.utils.gateway_principal_config import (
    init_principal_verifier_config,
    reset_principal_verifier_config_cache,
)

_KEY = "staff-dept-contract-signing-key-at-least-32-bytes"
_USER = {"id": "u-staff-1", "username": "bob@example.com"}


class _Secret:
    secret_user = "gateway"
    secret_value = _KEY


def _token() -> str:
    now = int(time.time())
    return jwt.encode(
        {
            "iss": "gateway",
            "aud": "backend",
            "iat": now,
            "exp": now + 60,
            "principals": [{"type": "user", "subject": _USER}],
        },
        _KEY,
        algorithm="HS256",
    )


def _http_client(app: FastAPI) -> TestClient:
    # ``raise_server_exceptions=False`` so the 5xx from ``DeptLookupError`` is
    # captured as a response rather than re-raised into the test.
    return TestClient(app, raise_server_exceptions=False)


def _app_bound_to(staff_dept: StaffDeptPlugin) -> FastAPI:
    """A public-surface app whose ``StaffDeptPlugin`` is ``staff_dept``.

    Pattern A: a bare ``Injector([_M])`` attaches the binding the resolver reads
    off ``app.state.injector``, with no other services. The principal is signed
    (JWT) so ``require_principal`` verifies it.
    """

    class _M(Module):
        def configure(self, binder):
            binder.bind(StaffDeptPlugin, to=staff_dept, scope=singleton)

    app = FastAPI()
    app.add_middleware(AvernetTenantMiddleware)
    app.include_router(build_public_router())
    app.add_exception_handler(Exception, _unhandled_exception_handler)
    attach_injector(app, Injector([_M()]))
    return app


def test_local_whoami_is_present_with_null_dept(app_with_testing_modules) -> None:
    """Local impl: all-``None`` dept → 200, identity still returned, plugin hit."""
    init_principal_verifier_config(
        _SecretResolver(), "gateway_principal_signing_key", strict=False
    )
    try:
        client = _http_client(app_with_testing_modules)
        resp = client.get("/openapi/v1/org/user", headers={PRINCIPAL_HEADER: _token()})
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        assert data["user_id"] == _USER["id"]
        assert data["username"] == _USER["username"]
        assert data["tenant"] == DEFAULT_AVERNET_TENANT
        # local/community impl returns "no dept" — null, but present, not a failure.
        assert data["dept_no"] is None
        assert data["dept_name"] is None
        assert data["dept_path"] is None

        # Plugin-hit: the local ``MockSeam`` impl recorded the lookup. Resolve it
        # off the attached injector and assert the call was made for our workNo.
        injector = getattr(app_with_testing_modules.state, "injector", None)
        assert injector is not None
        local = injector.get(StaffDeptPlugin)
        assert any(
            getattr(c, "kwargs", {}).get("work_no") == _USER["id"]
            for c in local.calls_to("get_dept_by_work_no")
        )
    finally:
        reset_principal_verifier_config_cache()


def test_community_staff_dept_reports_no_dept(community_world) -> None:
    """The community column wires a real ``NoStaffDept`` → all-``None``."""
    plugin = community_world.get(StaffDeptPlugin)
    info = plugin.get_dept_by_work_no(work_no="anyone")
    assert isinstance(info, StaffDeptInfo)
    assert info.dept_no is None
    assert info.dept_name is None
    assert info.dept_path is None


def test_wired_real_dept_propagates() -> None:
    """A wired impl that returns dept values — they reach the whoami response."""

    class _Real:
        def get_dept_by_work_no(self, *, work_no: str) -> StaffDeptInfo:
            return StaffDeptInfo(
                dept_no="D-7",
                dept_name="Platform Engineering",
                dept_path="Ant Group/Platform",
            )

    init_principal_verifier_config(
        _SecretResolver(), "gateway_principal_signing_key", strict=False
    )
    try:
        client = _http_client(_app_bound_to(_Real()))
        resp = client.get("/openapi/v1/org/user", headers={PRINCIPAL_HEADER: _token()})
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        assert data["dept_no"] == "D-7"
        assert data["dept_name"] == "Platform Engineering"
        assert data["dept_path"] == "Ant Group/Platform"
    finally:
        reset_principal_verifier_config_cache()


def test_directory_down_surfaces_5xx() -> None:
    """Infra failure (``DeptLookupError``) → 5xx, identity NOT returned.

    The failure mode the contract most needs pinned: "directory down" must stay
    distinct from "no dept" (200 + null) and from auth failure (401).
    """

    class _Down:
        def get_dept_by_work_no(self, *, work_no: str) -> StaffDeptInfo:
            raise DeptLookupError("master-data service unreachable")

    init_principal_verifier_config(
        _SecretResolver(), "gateway_principal_signing_key", strict=False
    )
    try:
        client = _http_client(_app_bound_to(_Down()))
        resp = client.get("/openapi/v1/org/user", headers={PRINCIPAL_HEADER: _token()})
        assert resp.status_code == 502, resp.text
        # Fixed message, no identity leaked, no dept values invented.
        body = resp.json()
        assert body.get("data") is None
        assert "unavailable" in body.get("message", "").lower()
    finally:
        reset_principal_verifier_config_cache()


class _SecretResolver:
    """Minimal resolver returning the JWT signing key for the verifier."""

    def get_secret(self, name: str):
        return _Secret()


# ── Department directory search (GET /openapi/v1/org/dept) ───────────────────


def test_local_dept_search_returns_empty_list(app_with_testing_modules) -> None:
    """Local impl: ``[]`` → 200, plugin hit recorded."""
    init_principal_verifier_config(
        _SecretResolver(), "gateway_principal_signing_key", strict=False
    )
    try:
        client = _http_client(app_with_testing_modules)
        resp = client.get(
            "/openapi/v1/org/dept",
            headers={PRINCIPAL_HEADER: _token()},
            params={"keyword": "platform"},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["data"] == []

        injector = getattr(app_with_testing_modules.state, "injector", None)
        assert injector is not None
        local = injector.get(StaffDeptPlugin)
        assert any(
            getattr(c, "kwargs", {}).get("keyword") == "platform"
            for c in local.calls_to("search_depts")
        )
    finally:
        reset_principal_verifier_config_cache()


def test_wired_dept_search_returns_the_list() -> None:
    """A wired impl returns a list whose items carry the three dept fields."""

    class _Real:
        def get_dept_by_work_no(self, *, work_no: str) -> StaffDeptInfo:
            return StaffDeptInfo()

        def search_depts(self, *, keyword: str) -> list[DeptSearchItem]:
            return [
                DeptSearchItem(
                    dept_no="D-7", dept_name="Platform Engineering",
                    dept_path="Ant Group/Platform",
                ),
                DeptSearchItem(
                    dept_no="D-8", dept_name="Platform Reliability",
                    dept_path="Ant Group/Platform/Reliability",
                ),
            ]

    init_principal_verifier_config(
        _SecretResolver(), "gateway_principal_signing_key", strict=False
    )
    try:
        client = _http_client(_app_bound_to(_Real()))
        resp = client.get(
            "/openapi/v1/org/dept",
            headers={PRINCIPAL_HEADER: _token()},
            params={"keyword": "platform"},
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        assert isinstance(data, list)
        assert len(data) == 2
        assert data[0] == {
            "dept_no": "D-7",
            "dept_name": "Platform Engineering",
            "dept_path": "Ant Group/Platform",
        }
    finally:
        reset_principal_verifier_config_cache()


def test_dept_search_directory_down_surfaces_5xx() -> None:
    """Infra failure during ``search_depts`` → 5xx, no list returned."""

    class _Down:
        def get_dept_by_work_no(self, *, work_no: str) -> StaffDeptInfo:
            return StaffDeptInfo()

        def search_depts(self, *, keyword: str) -> list[DeptSearchItem]:
            raise DeptLookupError("master-data service unreachable")

    init_principal_verifier_config(
        _SecretResolver(), "gateway_principal_signing_key", strict=False
    )
    try:
        client = _http_client(_app_bound_to(_Down()))
        resp = client.get(
            "/openapi/v1/org/dept",
            headers={PRINCIPAL_HEADER: _token()},
            params={"keyword": "platform"},
        )
        assert resp.status_code == 502, resp.text
        body = resp.json()
        assert body.get("data") is None
        assert "unavailable" in body.get("message", "").lower()
    finally:
        reset_principal_verifier_config_cache()



def test_community_profile_lookup_returns_requested_work_no(community_world) -> None:
    info = community_world.get(StaffDeptPlugin).get_profile_by_work_no(
        work_no="work-42"
    )
    assert info.work_no == "work-42"
    assert info.nick_name is None


def test_local_profile_lookup_is_recorded_and_returns_null_name(
    app_with_testing_modules,
) -> None:
    injector = getattr(app_with_testing_modules.state, "injector", None)
    assert injector is not None
    local = injector.get(StaffDeptPlugin)
    info = local.get_profile_by_work_no(work_no="work-42")
    assert info.work_no == "work-42"
    assert info.nick_name is None
    assert any(
        getattr(call, "kwargs", {}).get("work_no") == "work-42"
        for call in local.calls_to("get_profile_by_work_no")
    )


def test_community_directory_lookup_reports_null(community_world) -> None:
    """The community column wires ``NoStaffDept`` ⇒ all-``None`` identity+dept."""
    from agentclaw.community.plugin_api.staff_dept import UserIdentityInfo

    info = community_world.get(StaffDeptPlugin).get_user_by_work_no(
        work_no="anyone"
    )
    assert isinstance(info, UserIdentityInfo)
    assert info.work_no == "anyone"
    assert info.username is None
    assert info.display_name is None
    assert info.full_name is None
    assert info.dept_no is None
    assert info.dept_name is None
    assert info.dept_path is None
