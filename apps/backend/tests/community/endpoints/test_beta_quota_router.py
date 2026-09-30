"""Endpoint coverage for beta invite quota APIs."""
from __future__ import annotations

from agentclaw.community.core.common_config import CommonConfigService
from agentclaw.community.core.common_config.beta_quota_service import (
    BUSINESS_CODE,
    PARAM_CODE,
)
from agentclaw.community.utils import env_utils
from tests.community.framework import CaseInput, ExpectError, ExpectSuccess, endpoint_test


_USER_HEADER = {"x-user-id": "u_beta_quota_test"}


def _seed_quota(
    world, *, total: int, remaining: int, enable: str = "1"
) -> None:
    """Seed the beta quota config row via the real config service.

    The router resolves the env with ``env_utils.get_current_env()``; seed the
    row under that exact env so ``BetaQuotaService`` reads it back.
    """
    service = world.get(CommonConfigService)
    service.upsert_config(
        business_code=BUSINESS_CODE,
        business_name="内测准入",
        param_code=PARAM_CODE,
        param_name="内测名额",
        param_value={"total": total, "remaining": remaining},
        enable=enable,
        ext_info={"source": "endpoint-test"},
        env=env_utils.get_current_env(),
    )


def _seed_get_quota(world) -> None:
    _seed_quota(world, total=100, remaining=42)


def _seed_adjust_quota(world) -> None:
    _seed_quota(world, total=100, remaining=10)


def _seed_adjust_insufficient(world) -> None:
    _seed_quota(world, total=100, remaining=0)


def _seed_get_quota_disabled(world) -> None:
    _seed_quota(world, total=100, remaining=42, enable="0")


def _seed_adjust_quota_disabled(world) -> None:
    _seed_quota(world, total=100, remaining=10, enable="0")


def _assert_adjust_persisted(response, world) -> None:
    service = world.get(CommonConfigService)
    data = service.get_config(
        business_code=BUSINESS_CODE,
        param_code=PARAM_CODE,
        env=env_utils.get_current_env(),
    )
    assert data is not None
    assert data["param_value"]["remaining"] == 9


@endpoint_test(
    method="GET",
    path="/api/v1/beta/quota",
    scenario="happy",
    input=CaseInput(headers=_USER_HEADER),
    seed=_seed_get_quota,
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "success": True,
            "data": {"total": 100, "remaining": 42},
        },
    ),
)
def get_beta_quota_happy():
    """Existing config returns total + remaining."""


@endpoint_test(
    method="GET",
    path="/api/v1/beta/quota",
    scenario="error",
    input=CaseInput(headers=_USER_HEADER),
    expect=ExpectError(
        status=200,
        json_contains={"success": False, "error_code": 40401},
    ),
)
def get_beta_quota_error():
    """Missing config returns the not-found error envelope."""


@endpoint_test(
    method="POST",
    path="/api/v1/beta/quota/adjust",
    scenario="happy",
    input=CaseInput(json_body={"delta": -1}, headers=_USER_HEADER),
    seed=_seed_adjust_quota,
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "success": True,
            "data": {"total": 100, "remaining": 9},
        },
    ),
    extra_assertions=(_assert_adjust_persisted,),
)
def adjust_beta_quota_happy():
    """A delta adjustment returns and persists the new remaining count."""


@endpoint_test(
    method="POST",
    path="/api/v1/beta/quota/adjust",
    scenario="error",
    input=CaseInput(json_body={"delta": -1}, headers=_USER_HEADER),
    seed=_seed_adjust_insufficient,
    expect=ExpectError(
        status=200,
        json_contains={"success": False, "error_code": 40001},
    ),
)
def adjust_beta_quota_error():
    """Driving remaining below zero returns the insufficient-quota envelope."""


@endpoint_test(
    method="GET",
    path="/api/v1/beta/quota",
    scenario="disabled",
    input=CaseInput(headers=_USER_HEADER),
    seed=_seed_get_quota_disabled,
    expect=ExpectError(
        status=200,
        json_contains={"success": False, "error_code": 40401},
    ),
)
def get_beta_quota_disabled():
    """Disabled config (enable=0) returns the not-found error envelope."""


@endpoint_test(
    method="POST",
    path="/api/v1/beta/quota/adjust",
    scenario="disabled",
    input=CaseInput(json_body={"delta": -1}, headers=_USER_HEADER),
    seed=_seed_adjust_quota_disabled,
    expect=ExpectError(
        status=200,
        json_contains={"success": False, "error_code": 40001},
    ),
)
def adjust_beta_quota_disabled():
    """Disabled config (enable=0) blocks adjustment via the error envelope."""
