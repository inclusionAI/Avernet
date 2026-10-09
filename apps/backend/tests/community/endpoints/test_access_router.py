"""Tests for selected access router endpoints.

PUT /api/v1/access/bots-ceiling

Covered branches:
- happy path (operator user) → success=True with echoed entityId/ceiling
- non-operator caller → ``require_operator`` raises ``Forbidden`` → 403
- quota happy path with no seed rows → zero quota envelope
- quota config parse errors through the real service → success=False, error_code=500

In local mode ``AuthPlugin.is_operator_allowed`` returns True for every
user, so any seeded staff user satisfies ``require_operator`` — mirroring
the operator-flow setup in ``test_bot_admin_router.py``. The denial case
drives that same policy decision through the plugin's DI seam
(``set_response``), so the real ``require_operator`` dependency runs and
the real error handler renders the 403.
"""
from __future__ import annotations

from tests.community.factories.access import make_staff_user
from agentclaw.community.api.space_service import SpaceServiceProtocol
from tests.community.framework import (
    CaseInput,
    ExpectError,
    ExpectSuccess,
    endpoint_test,
)


# ============================================================================
# Seed helpers
# ============================================================================


def _seed_operator(world):
    """Seed an operator (caller) and the target user whose ceiling is set."""
    make_staff_user(world, user_id="u_operator")
    make_staff_user(world, user_id="u_target")


def _seed_non_operator(world):
    """Seed the caller, then have the auth plugin refuse operator rights.

    ``is_operator_allowed`` is the policy the local plugin answers ``True``
    to for everyone; driving it through the plugin's DI seam is the only
    way to reach the denial branch without a fake ``require_operator``.
    Everything downstream — the dependency, the raised ``Forbidden``, the
    error handler — is the production path.
    """
    from agentclaw.community.plugin_api.auth import AuthPlugin

    make_staff_user(world, user_id="u_operator")
    make_staff_user(world, user_id="u_target")
    world.get(AuthPlugin).set_response("is_operator_allowed", False)


def _seed_operator_and_team(world):
    _seed_operator(world)
    world.get(SpaceServiceProtocol).create_team(
        name="Quota Team", creator_id="u_operator", create_sc_team=False
    )


def _seed_non_operator_and_team(world):
    _seed_operator_and_team(world)
    from agentclaw.community.plugin_api.auth import AuthPlugin

    world.get(AuthPlugin).set_response("is_operator_allowed", False)


def _seed_team_with_override(world):
    _seed_operator_and_team(world)
    from agentclaw.community.core.access.services.policy_service import PolicyService

    world.get(PolicyService).set_bots_ceiling(
        entity_type="space", entity_id="1", ceiling=33
    )


def _seed_non_operator_team_with_override(world):
    _seed_team_with_override(world)
    from agentclaw.community.plugin_api.auth import AuthPlugin

    world.get(AuthPlugin).set_response("is_operator_allowed", False)


def _seed_quota_invalid_daily_config(world):
    """Seed invalid daily quota config so the real service raises."""
    _seed_system_config(
        world,
        config_key="daily_container_quota",
        config_value="not-a-number",
    )


def _seed_quota_total_limit_error(world):
    """Seed invalid total limit config so the real service raises."""
    _seed_system_config(
        world,
        config_key="daily_container_quota",
        config_value="10",
    )
    _seed_system_config(
        world,
        config_key="total_container_limit",
        config_value="not-a-number",
    )


def _seed_system_config(world, *, config_key: str, config_value: str):
    """Seed access quota config via the real system config service."""
    from agentclaw.community.core.system_config import SystemConfigService
    from agentclaw.community.utils import env_utils

    env = env_utils.get_current_env()
    service = world.get(SystemConfigService)
    service.create_category(
        category="system",
        category_name="系统配置",
        description="access quota endpoint test",
        env=env,
        operator="endpoint-test",
    )
    service.set_config(
        category="system",
        config_key=config_key,
        config_value=config_value,
        env=env,
        description="access quota endpoint test",
        operator="endpoint-test",
    )


# ============================================================================
# Extra assertions
# ============================================================================


def _assert_ceiling_echoed(response, world):
    """Assert the response echoes the entity id and ceiling that were set."""
    data = response.json()["data"]
    assert data.get("entityId") == "u_target", f"Expected entityId=u_target, got {data}"
    assert data.get("ceiling") == 10, f"Expected ceiling=10, got {data}"


def _assert_ceiling_persisted(response, world):
    """Assert the value was actually written through the real PolicyService."""
    from agentclaw.community.core.access.services.policy_service import PolicyService

    svc = world.get(PolicyService)
    assert svc.get_bots_ceiling(entity_id="u_target") == 10


def _assert_ceiling_not_written(response, world):
    """A rejected caller must not have moved the target's ceiling."""
    from agentclaw.community.core.access.services.policy_service import PolicyService

    svc = world.get(PolicyService)
    assert svc.get_bots_ceiling(entity_id="u_target", default=5) == 5


def _assert_space_ceiling_persisted(response, world):
    from agentclaw.community.core.access.services.policy_service import PolicyService

    assert response.json()["data"] == {"spaceId": 1, "ceiling": 25}
    assert (
        world.get(PolicyService).get_bots_ceiling(
            entity_type="space", entity_id="1", default=20
        )
        == 25
    )


def _assert_space_ceiling_not_written(response, world):
    from agentclaw.community.core.access.services.policy_service import PolicyService

    assert (
        world.get(PolicyService).get_bots_ceiling(
            entity_type="space", entity_id="1", default=20
        )
        == 20
    )


def _assert_space_ceiling_reset(response, world):
    from agentclaw.community.core.access.services.policy_service import PolicyService

    assert response.json()["data"] == {"spaceId": 1, "ceiling": 20}
    assert (
        world.get(PolicyService).get_bots_ceiling(
            entity_type="space", entity_id="1", default=20
        )
        == 20
    )


def _assert_space_ceiling_override_preserved(response, world):
    from agentclaw.community.core.access.services.policy_service import PolicyService

    assert (
        world.get(PolicyService).get_bots_ceiling(
            entity_type="space", entity_id="1", default=20
        )
        == 33
    )


# ============================================================================
# GET /api/v1/access/quota
# ============================================================================


@endpoint_test(
    method="GET",
    path="/api/v1/access/quota",
    scenario="happy_no_seed_defaults",
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "success": True,
            "error_code": 200,
            "data": {
                "quota": 0,
                "totalLimit": 0,
                "activeCount": 0,
                "effectiveQuota": 0,
                "updateTime": "",
            },
        },
    ),
)
def get_quota_happy_no_seed_defaults():
    """Quota endpoint returns deterministic zero defaults without config rows."""


@endpoint_test(
    method="GET",
    path="/api/v1/access/quota",
    scenario="invalid_daily_quota_config",
    seed=_seed_quota_invalid_daily_config,
    expect=ExpectError(
        status=200,
        json_contains={"success": False, "error_code": 500},
    ),
)
def get_quota_invalid_daily_quota_config():
    """Quota endpoint returns an error envelope for invalid daily quota config."""


@endpoint_test(
    method="GET",
    path="/api/v1/access/quota",
    scenario="invalid_total_limit_config",
    seed=_seed_quota_total_limit_error,
    expect=ExpectError(
        status=200,
        json_contains={"success": False, "error_code": 500},
    ),
)
def get_quota_invalid_total_limit_config():
    """Quota endpoint returns an error envelope for invalid total limit config."""


# ============================================================================
# PUT /api/v1/access/bots-ceiling
# ============================================================================


@endpoint_test(
    method="PUT",
    path="/api/v1/access/bots-ceiling",
    scenario="ok_set_ceiling",
    seed=_seed_operator,
    input=CaseInput(
        json_body={"entity_id": "u_target", "ceiling": 10},
        headers={"x-user-id": "u_operator"},
    ),
    expect=ExpectSuccess(
        status=200,
        json_contains={"success": True},
    ),
    extra_assertions=(_assert_ceiling_echoed, _assert_ceiling_persisted),
)
def set_bots_ceiling_ok():
    """Operator sets a target user's BOT ceiling; value is echoed and persisted."""


@endpoint_test(
    method="PUT",
    path="/api/v1/access/bots-ceiling",
    scenario="forbidden_non_operator",
    seed=_seed_non_operator,
    input=CaseInput(
        json_body={"entity_id": "u_target", "ceiling": 10},
        headers={"x-user-id": "u_operator"},
    ),
    expect=ExpectError(
        status=403,
        json_contains={"detail": "权限不足：您没有操作员权限"},
    ),
    extra_assertions=(_assert_ceiling_not_written,),
)
def set_bots_ceiling_forbidden_non_operator():
    """A caller outside the operator allowlist is rejected before any write."""


# ============================================================================
# PUT / DELETE /api/v1/access/spaces/{space_id}/bots-ceiling
# ============================================================================


@endpoint_test(
    method="PUT",
    path="/api/v1/access/spaces/{space_id}/bots-ceiling",
    scenario="operator_sets_team_space_ceiling",
    seed=_seed_operator_and_team,
    input=CaseInput(
        path_params={"space_id": 1},
        json_body={"ceiling": 25},
        headers={"x-user-id": "u_operator"},
    ),
    expect=ExpectSuccess(status=200, json_contains={"success": True}),
    extra_assertions=(_assert_space_ceiling_persisted,),
)
def set_team_space_ceiling_ok():
    """An operator can set a ceiling for one existing Team Space."""


@endpoint_test(
    method="PUT",
    path="/api/v1/access/spaces/{space_id}/bots-ceiling",
    scenario="non_operator_cannot_set_team_space_ceiling",
    seed=_seed_non_operator_and_team,
    input=CaseInput(
        path_params={"space_id": 1},
        json_body={"ceiling": 25},
        headers={"x-user-id": "u_operator"},
    ),
    expect=ExpectError(
        status=403,
        json_contains={"detail": "权限不足：您没有操作员权限"},
    ),
    extra_assertions=(_assert_space_ceiling_not_written,),
)
def set_team_space_ceiling_forbidden_non_operator():
    """Team membership does not replace the existing operator authorization."""


@endpoint_test(
    method="DELETE",
    path="/api/v1/access/spaces/{space_id}/bots-ceiling",
    scenario="operator_resets_team_space_ceiling",
    seed=_seed_team_with_override,
    input=CaseInput(
        path_params={"space_id": 1}, headers={"x-user-id": "u_operator"}
    ),
    expect=ExpectSuccess(status=200, json_contains={"success": True}),
    extra_assertions=(_assert_space_ceiling_reset,),
)
def reset_team_space_ceiling_ok():
    """Deleting an override restores the Team Space default of twenty."""


@endpoint_test(
    method="DELETE",
    path="/api/v1/access/spaces/{space_id}/bots-ceiling",
    scenario="non_operator_cannot_reset_team_space_ceiling",
    seed=_seed_non_operator_team_with_override,
    input=CaseInput(
        path_params={"space_id": 1}, headers={"x-user-id": "u_operator"}
    ),
    expect=ExpectError(
        status=403,
        json_contains={"detail": "权限不足：您没有操作员权限"},
    ),
    extra_assertions=(_assert_space_ceiling_override_preserved,),
)
def reset_team_space_ceiling_forbidden_non_operator():
    """A rejected reset leaves the existing Team Space override untouched."""
