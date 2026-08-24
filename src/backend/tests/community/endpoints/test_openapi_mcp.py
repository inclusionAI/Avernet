"""Declarative happy/error coverage for the public MCP surface.

The six ``/openapi/v1/bots/mcp`` operations sat on ``coverage_baseline.txt`` as
frozen debt for the reason the routines and resources rows carried: the case
runner authenticates with ``x-user-id``, ``require_principal`` accepts only a
gateway-signed token, and the harness had no minter — so a case could assert
nothing but a 401.

That reason has expired. ``test_openapi_session_files.py`` mints a principal
here in the test tree, by pointing ``init_principal_verifier_config`` at a local
signing key and handing the runner a token signed with it. This file does the
same, so the mcp rows come off the baseline and the coverage gate holds them
from here on.

What these cases add over
``tests/community/adapters/http/openapi_v1/test_mcp_endpoints.py`` — which
mounts the router on a bare ``FastAPI()`` with a hand-built injector and
``require_principal`` overridden — is the assembled application: the real DI
graph, the real router mount, real principal verification, and the app-level
envelope/error handlers.

**Two error shapes, because this surface owes two.** The three config and
permission operations are user-scoped (``UserIdDep``), so what each owes is the
``403`` for a ``user_id`` naming somebody else. The three catalogue reads take
no ``user_id`` at all — the marketplace answers identically for every caller in
the tenant — so there is no such refusal to assert, and inventing one would be
a case passing for the wrong reason. Each catalogue read is given the error it
genuinely owns instead: the detail read answers ``404`` for a server code the
catalogue does not know, and the two listings answer ``502`` when the
marketplace itself reports failure.
"""

from __future__ import annotations

import time

import jwt

from agentclaw.community.adapters.http.openapi_v1.dependencies import PRINCIPAL_HEADER
from agentclaw.community.api.mcp_auth_service import MCPAuthServiceProtocol
from agentclaw.community.api.mcp_config_service import MCPConfigServiceProtocol
from agentclaw.community.api.mcp_market_service import MCPMarketServiceProtocol
from agentclaw.community.api.mcp_sync_service import MCPSyncServiceProtocol
from agentclaw.community.utils.gateway_principal_config import (
    init_principal_verifier_config,
)
from tests.community.framework import (
    CaseInput,
    ExpectError,
    ExpectSuccess,
    bind_overrides,
    endpoint_test,
)

_OWNER = "mcp-owner"
_SERVER_CODE = "mcp.weather"
_KEY = "mcp-framework-signing-key-at-least-32-bytes"
_BASE_PATH = "/openapi/v1/bots/mcp"
_SERVER_PATH_PARAMS = {"server_code": _SERVER_CODE}

#: The raw credential the stored row holds. The surface must never echo it —
#: reads answer the masked form below.
_RAW_KEY = "sk-abcdefghijklmnop"
_MASKED_KEY = "sk-a****mnop"


class _Secret:
    secret_user = "test"
    secret_value = _KEY


class _Resolver:
    def get_secret(self, _secret_name: str) -> _Secret:
        return _Secret()


def _principal() -> str:
    now = int(time.time())
    return jwt.encode(
        {
            "iss": "gateway",
            "aud": "backend",
            "iat": now,
            "exp": now + 3600,
            "principals": [
                {
                    "type": "user",
                    "subject": {"id": _OWNER, "username": "mcp@example.test"},
                }
            ],
        },
        _KEY,
        algorithm="HS256",
    )


_HEADERS = {PRINCIPAL_HEADER: _principal()}
#: The three user-scoped operations. The catalogue reads take no ``user_id``.
_QUERY = {"user_id": _OWNER}
_FORBIDDEN_QUERY = {"user_id": "another-user"}


def _server_record() -> dict:
    """One server exactly as MCP Center returns it, ``extInfo`` and all.

    The ``extInfo`` key is deliberate: the detail projection strips it, and a
    record without one could not show that.
    """
    return {
        "serverCode": _SERVER_CODE,
        "name": "Weather",
        "description": "Forecasts and current conditions.",
        "networkTypes": ["INTERNET"],
        "transportProtocol": "SSE",
        "tools": [
            {
                "name": "get",
                "inputSchema": {"properties": {"extInfo": {"hidden": 1}, "q": 2}},
            }
        ],
    }


def _stored_config() -> dict:
    return {
        "api_key": _RAW_KEY,
        "headers": {"authorization": "Bearer x"},
        "endpoint_env": "PROD",
        "transport_protocol": "SSE",
    }


def _seed_verifier(_world) -> None:
    init_principal_verifier_config(_Resolver(), "test-key", strict=False)


def _bind_market(world, *, listings_ok: bool = True, detail: dict | None = None):
    def get_mcp_list(_self, **_kwargs):
        if not listings_ok:
            return {"success": False, "message": "marketplace down"}
        return {"success": True, "data": [_server_record()], "total": 1}

    def get_tenant_list(_self, **_kwargs):
        if not listings_ok:
            return {"success": False, "message": "marketplace down"}
        return {
            "success": True,
            "data": [
                {"code": "t1", "name": "Tenant 1", "categories": [{"name": "cat-a"}]}
            ],
        }

    def get_mcp_detail(_self, *_args, **_kwargs):
        return detail

    bind_overrides(
        world,
        MCPMarketServiceProtocol,
        {
            "get_mcp_list": get_mcp_list,
            "get_tenant_list": get_tenant_list,
            "get_mcp_detail": get_mcp_detail,
        },
    )


def _seed_happy_services(world) -> None:
    _seed_verifier(world)
    _bind_market(world, detail=_server_record())

    def check_mcp_permission_detail(_self, *_args, **_kwargs):
        return {
            "has_permission": True,
            "access_level": "PUBLIC",
            "tool_permissions": {"get": {"code": "AUTHORIZED"}},
        }

    bind_overrides(
        world,
        MCPAuthServiceProtocol,
        {"check_mcp_permission_detail": check_mcp_permission_detail},
    )

    def get_user_unified_config(_self, *_args, **_kwargs):
        return _stored_config()

    def validate_headers_for_mcp(_self, *_args, **_kwargs):
        return {"valid": True, "error": None}

    def update_user_unified_config(_self, **_kwargs):
        # Returns the pre-write row, which the flow keeps for rollback.
        return _stored_config()

    def rollback_unified_config(_self, **_kwargs) -> None:
        return None

    bind_overrides(
        world,
        MCPConfigServiceProtocol,
        {
            "get_user_unified_config": get_user_unified_config,
            "validate_headers_for_mcp": validate_headers_for_mcp,
            "update_user_unified_config": update_user_unified_config,
            "rollback_unified_config": rollback_unified_config,
        },
    )

    async def sync_mcp_detail_to_all_bots(_self, **_kwargs):
        return {"success": True, "sync_results": [], "error": None}

    bind_overrides(
        world,
        MCPSyncServiceProtocol,
        {"sync_mcp_detail_to_all_bots": sync_mcp_detail_to_all_bots},
    )


def _seed_market_unavailable(world) -> None:
    """The marketplace answers, and says it failed — an upstream problem."""
    _seed_verifier(world)
    _bind_market(world, listings_ok=False, detail=_server_record())


def _seed_unknown_server(world) -> None:
    """The catalogue holds no such server code."""
    _seed_verifier(world)
    _bind_market(world, detail=None)


def _never_echoes_the_raw_key(response, _world) -> None:
    """The stored credential must not appear anywhere in the response text.

    ``json_contains`` pins ``api_key`` to the masked form, which is not the
    same promise: a second field echoing the raw value would satisfy it.
    """
    assert _RAW_KEY not in response.text, response.text[:500]


#: The two config operations, which are the only ones that touch a credential.
_CONFIG_ASSERTIONS = (_never_echoes_the_raw_key,)

_CONFIG_BODY = {"api_key": _RAW_KEY, "endpoint_env": "PROD"}

#: ``(method, path, input, status, body_subset)`` — see the local surface's
#: note: a status-only assertion would still hold if the projection dropped
#: every field, so each case pins what its own handler builds.
_DETAIL_PATH = f"{_BASE_PATH}/servers/{{server_code}}"


def _extinfo_never_reaches_the_client(response, _world) -> None:
    """The detail projection strips ``extInfo``; this is what says so.

    ``json_contains`` is a recursive *subset* match, so pinning the keys that
    survive cannot express the absence of one that should not — a fragment of
    ``{"properties": {"q": 2}}`` matches just as well when ``extInfo`` is still
    sitting beside it. Reading the whole response for the key is the only
    assertion that fails when the stripping regresses.
    """
    assert "extInfo" not in response.text, response.text


_DETAIL_ASSERTIONS = (_extinfo_never_reaches_the_client,)


_HAPPY_CASES = (
    (
        "GET",
        f"{_BASE_PATH}/servers",
        CaseInput(headers=_HEADERS),
        200,
        {
            "code": 200000,
            "data": {
                "total": 1,
                "items": [
                    {"server_code": _SERVER_CODE, "network_types": ["INTERNET"]}
                ],
            },
        },
    ),
    (
        "GET",
        f"{_BASE_PATH}/tenants",
        CaseInput(headers=_HEADERS),
        200,
        {"data": [{"code": "t1", "categories": ["cat-a"]}]},
    ),
    (
        "GET",
        f"{_BASE_PATH}/servers/{{server_code}}",
        CaseInput(path_params=_SERVER_PATH_PARAMS, headers=_HEADERS),
        200,
        {
            "data": {
                "server_code": _SERVER_CODE,
                "transport_protocol": "SSE",
                "tools": [{"name": "get"}],
            }
        },
    ),
    (
        "GET",
        f"{_BASE_PATH}/servers/{{server_code}}/permissions",
        CaseInput(
            path_params=_SERVER_PATH_PARAMS, query_params=_QUERY, headers=_HEADERS
        ),
        200,
        {"data": {"has_access": True, "access_level": "PUBLIC"}},
    ),
    (
        "GET",
        f"{_BASE_PATH}/servers/{{server_code}}/config",
        CaseInput(
            path_params=_SERVER_PATH_PARAMS, query_params=_QUERY, headers=_HEADERS
        ),
        200,
        # Masked, never raw — the read must not hand the credential back.
        {"data": {"server_code": _SERVER_CODE, "api_key": _MASKED_KEY,
                  "has_config": True}},
    ),
    (
        "PUT",
        f"{_BASE_PATH}/servers/{{server_code}}/config",
        CaseInput(
            path_params=_SERVER_PATH_PARAMS,
            query_params=_QUERY,
            headers=_HEADERS,
            json_body=_CONFIG_BODY,
        ),
        200,
        # The write answers from a re-read, so it is exactly what a later GET
        # would return — masked as well.
        {"data": {"api_key": _MASKED_KEY, "has_config": True}},
    ),
)


for _method, _path, _input, _status, _contains in _HAPPY_CASES:
    endpoint_test(
        method=_method,
        path=_path,
        scenario="happy",
        input=_input,
        seed=_seed_happy_services,
        expect=ExpectSuccess(status=_status, json_contains=_contains),
        extra_assertions=(
            _CONFIG_ASSERTIONS
            if _path.endswith("/config")
            else _DETAIL_ASSERTIONS
            if _path == _DETAIL_PATH
            else ()
        ),
    )(lambda: None)


# ── The refusal the user-scoped operations owe ──────────────────────────────
# ``user_id`` names someone other than the caller the principal authenticated.
# ``require_user_id`` raises ahead of the handler, so no MCP service is seeded:
# reaching one would itself be the bug.
_USER_SCOPED_CASES = tuple(
    case for case in _HAPPY_CASES if case[2].query_params == _QUERY
)
assert len(_USER_SCOPED_CASES) == 3, "the three config/permission operations"

for _method, _path, _input, _status, _contains in _USER_SCOPED_CASES:
    endpoint_test(
        method=_method,
        path=_path,
        scenario="forbidden_user_scope",
        input=CaseInput(
            path_params=_input.path_params,
            query_params=_FORBIDDEN_QUERY,
            headers=_HEADERS,
            json_body=_input.json_body,
        ),
        seed=_seed_verifier,
        expect=ExpectError(
            status=403, json_contains={"code": 403000, "data": None}
        ),
    )(lambda: None)


# ── The errors the catalogue reads owe ──────────────────────────────────────
# A server code the catalogue does not know. The same 404 answers a server
# hidden by the network-type rule, so a caller cannot tell the two apart.
endpoint_test(
    method="GET",
    path=f"{_BASE_PATH}/servers/{{server_code}}",
    scenario="unknown_server",
    input=CaseInput(path_params={"server_code": "mcp.nope"}, headers=_HEADERS),
    seed=_seed_unknown_server,
    expect=ExpectError(
        status=404, json_contains={"code": 404000, "message": "Not found", "data": None}
    ),
)(lambda: None)


# A marketplace that answers and reports failure is an upstream problem, not an
# empty page — ``list_marketplace_servers`` / ``list_marketplace_tenants`` raise
# ``McpMarketUnavailableError``, which this surface maps to 502.
for _method, _path in (
    ("GET", f"{_BASE_PATH}/servers"),
    ("GET", f"{_BASE_PATH}/tenants"),
):
    endpoint_test(
        method=_method,
        path=_path,
        scenario="marketplace_unavailable",
        input=CaseInput(headers=_HEADERS),
        seed=_seed_market_unavailable,
        expect=ExpectError(
            status=502,
            json_contains={
                "code": 502000,
                "message": "MCP service error",
                "data": None,
            },
        ),
    )(lambda: None)
