"""Endpoint-framework coverage for digital employee HTTP adapters."""

from __future__ import annotations

import time

import jwt

from agentclaw.community.adapters.http.openapi_v1.dependencies import PRINCIPAL_HEADER
from agentclaw.community.api.digital_employee_service import (
    DigitalEmployeeCatalogProtocol,
    DigitalEmployeeServiceProtocol,
)
from agentclaw.community.api.execution_identity_service import (
    ExecutionIdentityServiceProtocol,
)
from agentclaw.community.utils.gateway_principal_config import (
    init_principal_verifier_config,
)
from tests.community.factories.bot_collaborator import make_bot
from tests.community.framework import (
    CaseInput,
    ExpectError,
    ExpectSuccess,
    endpoint_test,
)
from tests.community.framework.di_seams import bind_method

_KEY = "digital-employee-endpoint-signing-key-32-bytes"
_OPENAPI_BASE = "/openapi/v1/bots/metadata/digital-employees"
_OWNER = "digital-employee-owner"
_BOT_ID = "digital-employee-service-bot"


class _Secret:
    secret_user = "test"
    secret_value = _KEY


class _Resolver:
    def get_secret(self, _secret_name: str) -> _Secret:
        return _Secret()


def _principal_headers() -> dict[str, str]:
    now = int(time.time())
    encoded = jwt.encode(
        {
            "iss": "gateway",
            "aud": "backend",
            "iat": now,
            "exp": now + 3600,
            "principals": [
                {
                    "type": "app",
                    "tenant": "digital-employee-endpoint-test",
                    "app": {
                        "app_id": 17,
                        "app_name": "digital-employee-platform",
                        "owners": "platform",
                        "tenant": "digital-employee-endpoint-test",
                        "app_type": "integration",
                    },
                }
            ],
        },
        _KEY,
        algorithm="HS256",
    )
    return {PRINCIPAL_HEADER: encoded}


def _boot_verifier(_world) -> None:
    init_principal_verifier_config(_Resolver(), "test-key", strict=False)


def _seed_catalog_list(world) -> None:
    _boot_verifier(world)

    def _list_bindable(_self, creator_no: str) -> list[dict]:
        return [
            {
                "agentId": "17",
                "platform": "digital-employee-endpoint-test",
                "agentName": "Service Bot",
                "description": "Endpoint coverage Bot",
                "creatorNo": creator_no,
                "aiWorkNo": None,
            }
        ]

    bind_method(
        world,
        DigitalEmployeeServiceProtocol,
        "list_bindable",
        _list_bindable,
    )


def _seed_catalog_detail(world) -> None:
    _boot_verifier(world)

    async def _detail(_self, agent_id: int, version_status: str) -> dict:
        return {
            "agentId": str(agent_id),
            "platform": "digital-employee-endpoint-test",
            "agentName": "Service Bot",
            "description": "Endpoint coverage Bot",
            "creatorNo": _OWNER,
            "aiWorkNo": None,
            "agentCode": "agent-17",
            "agentPlatform": "digital-employee-endpoint-test",
            "agentFramework": "openclaw",
            "modelList": [],
            "mcpDetailList": [],
            "skillList": [],
            "cliLis": [],
            "queriedVersionStatus": version_status,
        }

    bind_method(world, DigitalEmployeeCatalogProtocol, "detail", _detail)


def _seed_execution_identity(world) -> None:
    def _change(_self, **_kwargs) -> dict:
        return {
            "status": "ACTIVE",
            "identity_type": "DIGITAL_EMPLOYEE",
            "execution_workno": "AI00000124",
            "token_injected": True,
        }

    bind_method(
        world,
        ExecutionIdentityServiceProtocol,
        "change_execution_identity",
        _change,
    )


def _seed_permission_query(world) -> None:
    make_bot(
        world,
        bot_id=_BOT_ID,
        owner_id=_OWNER,
        bot_type="service",
        status="ACTIVE",
        active_engine="openclaw",
    )

    def _query(_self, bot_pk: int, mcp_codes: list[str]) -> dict:
        return {
            "bot_pk": bot_pk,
            "items": [{"mcpCode": code, "status": "APPROVED"} for code in mcp_codes],
        }

    bind_method(
        world,
        DigitalEmployeeServiceProtocol,
        "query_permissions",
        _query,
    )


@endpoint_test(
    method="GET",
    path="/api/digital-employee/settings",
    scenario="returns_disabled_public_defaults",
    input=CaseInput(headers={"x-user-id": _OWNER}),
    expect=ExpectSuccess(
        status=200,
        json_contains={"success": True, "data": {"enabled": False, "site_url": ""}},
    ),
)
def digital_employee_settings_ok():
    """The framework owns invocation."""


@endpoint_test(
    method="GET",
    path="/api/digital-employee/settings",
    scenario="requires_authenticated_user",
    expect=ExpectError(status=401),
)
def digital_employee_settings_error():
    """The framework owns invocation."""


@endpoint_test(
    method="GET",
    path=_OPENAPI_BASE,
    scenario="returns_platform_catalog",
    seed=_seed_catalog_list,
    input=CaseInput(
        headers=_principal_headers(),
        query_params={"creatorNo": _OWNER},
    ),
    expect=ExpectSuccess(
        status=200,
        json_contains={"success": True, "data": [{"agentId": "17"}]},
    ),
)
def digital_employee_catalog_ok():
    """The framework owns invocation."""


@endpoint_test(
    method="GET",
    path=_OPENAPI_BASE,
    scenario="requires_openapi_principal",
    input=CaseInput(query_params={"creatorNo": _OWNER}),
    expect=ExpectError(status=401),
)
def digital_employee_catalog_error():
    """The framework owns invocation."""


@endpoint_test(
    method="GET",
    path=f"{_OPENAPI_BASE}/{{agent_id}}",
    scenario="returns_selected_version",
    seed=_seed_catalog_detail,
    input=CaseInput(
        path_params={"agent_id": 17},
        headers=_principal_headers(),
        query_params={"versionStatus": "published"},
    ),
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "success": True,
            "data": {"agentId": "17", "queriedVersionStatus": "published"},
        },
    ),
)
def digital_employee_detail_ok():
    """The framework owns invocation."""


@endpoint_test(
    method="GET",
    path=f"{_OPENAPI_BASE}/{{agent_id}}",
    scenario="rejects_invalid_metadata_id",
    seed=_boot_verifier,
    input=CaseInput(
        path_params={"agent_id": 0},
        headers=_principal_headers(),
    ),
    expect=ExpectError(status=422),
)
def digital_employee_detail_error():
    """The framework owns invocation."""


@endpoint_test(
    method="POST",
    path="/api/bots/{bot_id}/admin/execution-identity",
    scenario="reissues_and_injects_credentials",
    seed=_seed_execution_identity,
    input=CaseInput(
        path_params={"bot_id": _BOT_ID},
        headers={"x-user-id": "operator"},
        json_body={
            "owner_id": _OWNER,
            "execution_workno": "AI00000124",
            "identity_type": "DIGITAL_EMPLOYEE",
            "action": "reissue",
        },
    ),
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "success": True,
            "data": {"status": "ACTIVE", "token_injected": True},
        },
    ),
)
def execution_identity_change_ok():
    """The framework owns invocation."""


@endpoint_test(
    method="POST",
    path="/api/bots/{bot_id}/admin/execution-identity",
    scenario="rejects_invalid_action",
    input=CaseInput(
        path_params={"bot_id": _BOT_ID},
        headers={"x-user-id": "operator"},
        json_body={"owner_id": _OWNER, "action": "unsupported"},
    ),
    expect=ExpectError(status=422),
)
def execution_identity_change_error():
    """The framework owns invocation."""


@endpoint_test(
    method="POST",
    path="/api/digital-employee/bots/{bot_id}/mcp-permissions/query",
    scenario="returns_permission_status",
    seed=_seed_permission_query,
    input=CaseInput(
        path_params={"bot_id": _BOT_ID},
        headers={"x-user-id": _OWNER},
        json_body={"owner_id": _OWNER, "mcp_codes": ["private-mcp"]},
    ),
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "success": True,
            "data": {"items": [{"mcpCode": "private-mcp", "status": "APPROVED"}]},
        },
    ),
)
def digital_employee_permissions_ok():
    """The framework owns invocation."""


@endpoint_test(
    method="POST",
    path="/api/digital-employee/bots/{bot_id}/mcp-permissions/query",
    scenario="rejects_empty_mcp_list",
    input=CaseInput(
        path_params={"bot_id": _BOT_ID},
        headers={"x-user-id": _OWNER},
        json_body={"owner_id": _OWNER, "mcp_codes": []},
    ),
    expect=ExpectError(status=422),
)
def digital_employee_permissions_error():
    """The framework owns invocation."""
