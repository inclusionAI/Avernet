"""Endpoint tests for device config switch endpoints.

Covers:
- POST /api/v1/config/device/template-type-provider-map
- POST /api/v1/config/device/personal-bot-baas-disable
"""
from __future__ import annotations

from tests.community.factories.access import make_staff_user
from tests.community.framework import (
    CaseInput,
    ExpectError,
    ExpectSuccess,
    endpoint_test,
)


_OPERATOR = "u_smoke"


def _seed_operator(world):
    make_staff_user(world, user_id=_OPERATOR)


# =============================================================================
# POST /api/v1/config/device/template-type-provider-map
# =============================================================================


@endpoint_test(
    method="POST",
    path="/api/v1/config/device/template-type-provider-map",
    scenario="ok_set_mapping",
    seed=_seed_operator,
    input=CaseInput(
        json_body={"mapping": {"personalCoding": "baas"}},
        headers={"x-user-id": _OPERATOR},
    ),
    expect=ExpectSuccess(
        status=200,
        json_contains={"success": True},
    ),
)
def test_set_template_type_provider_map_ok():
    """Operator sets template_type → provider mapping."""


@endpoint_test(
    method="POST",
    path="/api/v1/config/device/template-type-provider-map",
    scenario="err_invalid_provider",
    seed=_seed_operator,
    input=CaseInput(
        json_body={"mapping": {"personalCoding": "invalid_provider_xyz"}},
        headers={"x-user-id": _OPERATOR},
    ),
    expect=ExpectError(
        status=200,
        json_contains={"success": False, "error_code": 40001},
    ),
)
def test_set_template_type_provider_map_err():
    """Invalid provider name returns error."""


# =============================================================================
# POST /api/v1/config/device/personal-bot-baas-disable
# =============================================================================


@endpoint_test(
    method="POST",
    path="/api/v1/config/device/personal-bot-baas-disable",
    scenario="ok_disable",
    seed=_seed_operator,
    input=CaseInput(
        json_body={"disabled": True},
        headers={"x-user-id": _OPERATOR},
    ),
    expect=ExpectSuccess(
        status=200,
        json_contains={"success": True},
    ),
)
def test_set_personal_bot_baas_disable_ok():
    """Operator disables personal bot BaaS routing."""


@endpoint_test(
    method="POST",
    path="/api/v1/config/device/personal-bot-baas-disable",
    scenario="err_missing_body",
    seed=_seed_operator,
    input=CaseInput(
        json_body={},
        headers={"x-user-id": _OPERATOR},
    ),
    expect=ExpectError(status=422),
)
def test_set_personal_bot_baas_disable_err():
    """Missing required 'disabled' field returns validation error."""
