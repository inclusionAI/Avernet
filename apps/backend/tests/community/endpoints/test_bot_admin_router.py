"""Tests for the admin bot update endpoint.

PUT /api/bots/{bot_id}/admin
"""
from __future__ import annotations

from agentclaw.community.core.repository.protocols.bot import TemplateRepository
from tests.community.factories.access import make_staff_user
from tests.community.factories.bot_collaborator import make_bot
from tests.community.framework import (
    CaseInput,
    ExpectError,
    ExpectSuccess,
    endpoint_test,
)


# ============================================================================
# Seed helpers
# ============================================================================


def _make_template(world, *, bot_id: str, ext: dict | None = None) -> dict:
    """Seed a template record for a bot."""
    template_repo = world.get(TemplateRepository)
    return template_repo.insert({
        "bot_id": bot_id,
        "ext": ext or {},
    })


def _seed_operator_with_bot(world):
    """Seed an operator and a bot owned by a different user."""
    make_staff_user(world, user_id="u_operator")
    make_staff_user(world, user_id="u_owner")
    make_bot(world, bot_id="bot_target", owner_id="u_owner", bot_type="personal", status="ACTIVE")


def _seed_operator_with_bot_and_template(world):
    """Seed an operator, a bot, and a template for the bot."""
    make_staff_user(world, user_id="u_operator")
    make_staff_user(world, user_id="u_owner")
    make_bot(world, bot_id="bot_tpl", owner_id="u_owner", bot_type="personal", status="ACTIVE")
    _make_template(world, bot_id="bot_tpl", ext={"image": "original:latest", "envs": {"FOO": "bar"}})


# ============================================================================
# Test cases
# ============================================================================

@endpoint_test(
    method="PUT",
    path="/api/bots/{bot_id}/admin",
    scenario="ok_update_name",
    seed=_seed_operator_with_bot,
    input=CaseInput(
        path_params={"bot_id": "bot_target"},
        json_body={
            "owner_id": "u_owner",
            "bot_name": "Admin Updated Name",
        },
        headers={"x-user-id": "u_operator"},
    ),
    expect=ExpectSuccess(
        status=200,
        json_contains={"success": True},
    ),
)
def test_admin_update_bot_name():
    """Admin can update bot name for any bot."""


@endpoint_test(
    method="PUT",
    path="/api/bots/{bot_id}/admin",
    scenario="err_missing_owner_id",
    seed=_seed_operator_with_bot,
    input=CaseInput(
        path_params={"bot_id": "bot_target"},
        json_body={
            "bot_name": "Should Fail",
        },
        headers={"x-user-id": "u_operator"},
    ),
    expect=ExpectError(
        status=200,
        json_contains={"success": False, "error_code": 400},
    ),
)
def test_admin_update_missing_owner_id():
    """Admin update requires owner_id parameter."""


@endpoint_test(
    method="PUT",
    path="/api/bots/{bot_id}/admin",
    scenario="err_invalid_name",
    seed=_seed_operator_with_bot,
    input=CaseInput(
        path_params={"bot_id": "bot_target"},
        json_body={
            "owner_id": "u_owner",
            "bot_name": "bad@name#bot",
        },
        headers={"x-user-id": "u_operator"},
    ),
    expect=ExpectError(
        status=200,
        json_contains={"success": False, "error_code": 400},
    ),
)
def test_admin_update_invalid_name_rejected():
    """Admin update rejects bot_name with special characters (400, not 500)."""


@endpoint_test(
    method="PUT",
    path="/api/bots/{bot_id}/admin",
    scenario="ok_no_name_change_desc",
    seed=_seed_operator_with_bot,
    input=CaseInput(
        path_params={"bot_id": "bot_target"},
        json_body={
            "owner_id": "u_owner",
            "bot_desc": "new desc only",
        },
        headers={"x-user-id": "u_operator"},
    ),
    expect=ExpectSuccess(
        status=200,
        json_contains={"success": True},
    ),
)
def test_admin_update_without_bot_name_ok():
    """Admin update without bot_name skips validation and succeeds."""


@endpoint_test(
    method="PUT",
    path="/api/bots/{bot_id}/admin",
    scenario="err_bot_not_found",
    seed=_seed_operator_with_bot,
    input=CaseInput(
        path_params={"bot_id": "nonexistent_bot"},
        json_body={
            "owner_id": "u_owner",
            "bot_name": "New Name",
        },
        headers={"x-user-id": "u_operator"},
    ),
    expect=ExpectError(
        status=200,
        json_contains={"success": False, "error_code": 404},
    ),
)
def test_admin_update_bot_not_found():
    """Admin update returns 404 for nonexistent bot."""


@endpoint_test(
    method="PUT",
    path="/api/bots/{bot_id}/admin",
    scenario="ok_update_template_config",
    seed=_seed_operator_with_bot_and_template,
    input=CaseInput(
        path_params={"bot_id": "bot_tpl"},
        json_body={
            "owner_id": "u_owner",
            "template_config": {
                "image": "registry.example.com/custom:v2",
                "resource_spec": {"cpu": 4, "memory": 8, "disk": 100},
            },
        },
        headers={"x-user-id": "u_operator"},
    ),
    expect=ExpectSuccess(
        status=200,
        json_contains={"success": True},
    ),
)
def test_admin_update_template_config():
    """Admin can update template_config (sandbox overrides)."""


@endpoint_test(
    method="PUT",
    path="/api/bots/{bot_id}/admin",
    scenario="ok_tolerant_resource_spec",
    seed=_seed_operator_with_bot_and_template,
    input=CaseInput(
        path_params={"bot_id": "bot_tpl"},
        json_body={
            "owner_id": "u_owner",
            "template_config": {
                "resource_spec": {"cpu": -1, "memory": 8, "disk": 100},
            },
        },
        headers={"x-user-id": "u_operator"},
    ),
    expect=ExpectSuccess(
        status=200,
        json_contains={"success": False, "error_code": 400},
    ),
)
def test_admin_update_tolerant_resource_spec():
    """Invalid resource_spec (cpu=-1) is rejected by SandboxOverrides validation."""
