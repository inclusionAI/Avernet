"""Endpoint coverage for the device provider-inventory route."""

from __future__ import annotations

from agentclaw.community.plugin_api.auth import AuthPlugin
from tests.community.framework import CaseInput, ExpectError, ExpectSuccess, endpoint_test

_OPERATOR = "provider_inventory_operator"


def _deny_operator(world) -> None:
    """Have the auth plugin refuse operator rights for the caller.

    The route is operator-only because it scans global bindings; the
    denial is the error path worth pinning. ``is_operator_allowed`` is
    the plugin's own policy decision, driven here through its DI seam so
    the real ``require_operator`` dependency and error handler still run.
    """
    world.get(AuthPlugin).set_response("is_operator_allowed", False)


@endpoint_test(
    method="GET",
    path="/api/v1/devices/provider-inventory",
    scenario="happy",
    input=CaseInput(
        headers={"x-user-id": _OPERATOR},
        query_params={
            "entity_type": "staff",
            "env": "dev",
            "status": "ACTIVE",
            "page_size": 10,
            "max_pages": 1,
        },
    ),
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "success": True,
            "data": {
                "filters": {
                    "entity_id": None,
                    "entity_type": "staff",
                    "env": "dev",
                    "status": "ACTIVE",
                },
                "truncated": False,
            },
        },
    ),
)
def provider_inventory_happy():
    """Provider inventory returns the bounded aggregate envelope."""


@endpoint_test(
    method="GET",
    path="/api/v1/devices/provider-inventory",
    scenario="forbidden_non_operator",
    input=CaseInput(headers={"x-user-id": _OPERATOR}),
    seed=_deny_operator,
    expect=ExpectError(
        status=403,
        json_contains={"detail": "权限不足：您没有操作员权限"},
    ),
)
def provider_inventory_forbidden_non_operator():
    """A caller outside the operator allowlist never reaches the global scan."""
