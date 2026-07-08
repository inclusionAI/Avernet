"""Endpoint coverage for the device provider-inventory route."""

from __future__ import annotations

from unittest.mock import patch

from agentclaw.community.api.device_service import DeviceServiceProtocol
from tests.community.framework import CaseInput, ExpectError, ExpectSuccess, endpoint_test

_OPERATOR = "provider_inventory_operator"


def _raise_provider_inventory_error(world) -> None:
    service = world.get(DeviceServiceProtocol)
    patch.object(
        service, "get_provider_inventory", side_effect=RuntimeError("inventory boom")
    ).start()


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
    scenario="service_error",
    input=CaseInput(headers={"x-user-id": _OPERATOR}),
    seed=_raise_provider_inventory_error,
    expect=ExpectError(
        status=200,
        json_contains={
            "success": False,
            "error_code": 50000,
            "message": "Failed to get provider inventory: inventory boom",
        },
    ),
)
def provider_inventory_service_error():
    """Provider inventory service failures stay in the ApiResponse envelope."""
