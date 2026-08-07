"""Endpoint tests for POST /api/service-bot/publish/update-service-bot-config.

Tests the following endpoint from ``adapters/http/service_bot/router_publish.py``:
- POST /api/service-bot/publish/update-service-bot-config

This endpoint updates bot.ext.service_bot_config with ADMIN permission.
Uses CollaboratorPermissionInterceptor for access control.
"""
from __future__ import annotations

from agentclaw.community.core.bot_collaborator.services.collaborator_lock_service import CollaboratorLockService
from agentclaw.community.core.bot_management.repository.protocol import BotRepository
from agentclaw.community.core.service_bot.services.bot_publish_service import BotPublishServiceError
from tests.community.factories.access import make_staff_user
from tests.community.factories.bot_collaborator import make_bot, make_collaborator
from tests.community.framework import (
    CaseInput,
    ExpectError,
    ExpectSuccess,
    bind_failing_method,
    endpoint_test,
)
from tests.community.framework.world import World


def _seed_bot_with_admin(world: World) -> None:
    """Seed bot with admin collaborator and acquire lock."""
    make_staff_user(world, user_id="u_owner")
    make_staff_user(world, user_id="u_admin")
    make_bot(world, bot_id="bot_test", owner_id="u_owner", bot_type="service", status="ACTIVE")
    make_collaborator(
        world,
        bot_id="bot_test",
        owner_id="u_owner",
        user_id="u_admin",
        role="admin",
        operator_id="u_owner",
    )
    # Acquire lock for admin collaborator
    lock_service = world.get(CollaboratorLockService)
    lock_service.acquire_lock("bot_test", "u_owner", "u_admin")


def _seed_bot_with_member(world: World) -> None:
    """Seed bot with member collaborator (insufficient permission) and acquire lock."""
    make_staff_user(world, user_id="u_owner")
    make_staff_user(world, user_id="u_member")
    make_bot(world, bot_id="bot_test", owner_id="u_owner", bot_type="service", status="ACTIVE")
    make_collaborator(
        world,
        bot_id="bot_test",
        owner_id="u_owner",
        user_id="u_member",
        role="member",
        operator_id="u_owner",
    )
    # Acquire lock for member collaborator
    lock_service = world.get(CollaboratorLockService)
    lock_service.acquire_lock("bot_test", "u_owner", "u_member")


def _seed_bot_owner_only(world: World) -> None:
    """Seed bot with only owner."""
    make_staff_user(world, user_id="u_owner")
    make_bot(world, bot_id="bot_test", owner_id="u_owner", bot_type="service", status="ACTIVE")


def _seed_bot_with_existing_config(world: World) -> None:
    """Seed bot with existing service_bot_config."""
    make_staff_user(world, user_id="u_owner")
    make_bot(world, bot_id="bot_test", owner_id="u_owner", bot_type="service", status="ACTIVE")
    # Set initial service_bot_config
    repo = world.get(BotRepository)
    repo.update_by_owner("bot_test", "u_owner", {
        "ext": {"service_bot_config": {"device_count": 1, "cpu": 2}}
    })


def _seed_bot_with_ext_as_string(world: World) -> None:
    """Seed bot with ext as JSON string (legacy format)."""
    import json
    make_staff_user(world, user_id="u_owner")
    make_bot(world, bot_id="bot_test", owner_id="u_owner", bot_type="service", status="ACTIVE")
    # Set ext as JSON string - simulates legacy data format
    repo = world.get(BotRepository)
    repo.update_by_owner("bot_test", "u_owner", {
        "ext": json.dumps({"service_bot_config": {"device_count": 1}})
    })


def _seed_bot_with_invalid_service_bot_config(world: World) -> None:
    """Seed bot with service_bot_config as non-dict value."""
    make_staff_user(world, user_id="u_owner")
    make_bot(world, bot_id="bot_test", owner_id="u_owner", bot_type="service", status="ACTIVE")
    # Set service_bot_config to a string instead of dict
    repo = world.get(BotRepository)
    repo.update_by_owner("bot_test", "u_owner", {
        "ext": {"service_bot_config": "invalid_string_value"}
    })


def _assert_config_updated(response, world: World) -> None:
    """Verify service_bot_config was updated in DB."""
    repo = world.get(BotRepository)
    _, items = repo.list_by_conditions(bot_id="bot_test", page=1, page_size=1)
    assert items, "bot should exist after update"
    ext = items[0].get("ext") or {}
    service_bot_config = ext.get("service_bot_config") or {}
    assert service_bot_config.get("device_count") == 2, f"device_count should be 2, got {service_bot_config}"
    assert service_bot_config.get("cpu") == 2, f"cpu should remain 2, got {service_bot_config}"


def _assert_config_merged(response, world: World) -> None:
    """Verify service_bot_config was merged (not replaced)."""
    repo = world.get(BotRepository)
    _, items = repo.list_by_conditions(bot_id="bot_test", page=1, page_size=1)
    assert items, "bot should exist after update"
    ext = items[0].get("ext") or {}
    service_bot_config = ext.get("service_bot_config") or {}
    # Original values should remain
    assert service_bot_config.get("device_count") == 1, "original device_count should remain"
    assert service_bot_config.get("cpu") == 4, "cpu should be updated to 4"
    # New value should be added
    assert service_bot_config.get("memory") == 8192, "memory should be added"


# ============================================================================
# Happy Path - Admin can update
# ============================================================================


@endpoint_test(
    method="POST",
    path="/api/service-bot/publish/update-service-bot-config",
    scenario="admin_can_update",
    input=CaseInput(
        headers={"x-user-id": "u_admin"},
        json_body={
            "bot_id": "bot_test",
            "owner_id": "u_owner",
            "config_update": {"device_count": 2, "cpu": 2},
        },
    ),
    seed=_seed_bot_with_admin,
    expect=ExpectSuccess(
        status=200,
        json_contains={"success": True},
    ),
    extra_assertions=(_assert_config_updated,),
)
def update_service_bot_config_admin_ok():
    """ADMIN collaborator can update service_bot_config."""


# ============================================================================
# Happy Path - Owner can update
# ============================================================================


@endpoint_test(
    method="POST",
    path="/api/service-bot/publish/update-service-bot-config",
    scenario="owner_can_update",
    input=CaseInput(
        headers={"x-user-id": "u_owner"},
        json_body={
            "bot_id": "bot_test",
            "owner_id": "u_owner",
            "config_update": {"device_count": 3},
        },
    ),
    seed=_seed_bot_owner_only,
    expect=ExpectSuccess(
        status=200,
        json_contains={"success": True},
    ),
)
def update_service_bot_config_owner_ok():
    """Bot owner can update service_bot_config."""


# ============================================================================
# Happy Path - Config merge (incremental update)
# ============================================================================


@endpoint_test(
    method="POST",
    path="/api/service-bot/publish/update-service-bot-config",
    scenario="config_merge",
    input=CaseInput(
        headers={"x-user-id": "u_owner"},
        json_body={
            "bot_id": "bot_test",
            "owner_id": "u_owner",
            "config_update": {"cpu": 4, "memory": 8192},
        },
    ),
    seed=_seed_bot_with_existing_config,
    expect=ExpectSuccess(
        status=200,
        json_contains={"success": True},
    ),
    extra_assertions=(_assert_config_merged,),
)
def update_service_bot_config_merge():
    """Config should be merged (not replaced) with existing values."""


# ============================================================================
# Error Path - Member denied
# ============================================================================


@endpoint_test(
    method="POST",
    path="/api/service-bot/publish/update-service-bot-config",
    scenario="member_denied",
    input=CaseInput(
        headers={"x-user-id": "u_member"},
        json_body={
            "bot_id": "bot_test",
            "owner_id": "u_owner",
            "config_update": {"device_count": 2},
        },
    ),
    seed=_seed_bot_with_member,
    expect=ExpectError(
        status=403,
        json_contains={"success": False, "error_code": 403},
    ),
)
def update_service_bot_config_member_forbidden():
    """MEMBER collaborator is forbidden from updating service_bot_config."""


# ============================================================================
# Error Path - Bot not found
# ============================================================================


@endpoint_test(
    method="POST",
    path="/api/service-bot/publish/update-service-bot-config",
    scenario="bot_not_found",
    input=CaseInput(
        headers={"x-user-id": "u_owner"},
        json_body={
            "bot_id": "nonexistent_bot",
            "owner_id": "u_owner",
            "config_update": {"device_count": 2},
        },
    ),
    seed=_seed_bot_owner_only,
    expect=ExpectError(
        status=200,
        json_contains={"success": False, "error_code": 404},
    ),
)
def update_service_bot_config_bot_not_found():
    """Returns 404 when bot does not exist."""


# ============================================================================
# Error Path - Empty bot_id
# ============================================================================


@endpoint_test(
    method="POST",
    path="/api/service-bot/publish/update-service-bot-config",
    scenario="empty_bot_id",
    input=CaseInput(
        headers={"x-user-id": "u_owner"},
        json_body={
            "bot_id": "",
            "owner_id": "u_owner",
            "config_update": {"device_count": 2},
        },
    ),
    seed=_seed_bot_owner_only,
    expect=ExpectError(
        status=200,
        json_contains={"success": False, "error_code": 400},
    ),
)
def update_service_bot_config_empty_bot_id():
    """Returns 400 when bot_id is empty."""


# ============================================================================
# Error Path - Empty owner_id
# ============================================================================


@endpoint_test(
    method="POST",
    path="/api/service-bot/publish/update-service-bot-config",
    scenario="empty_owner_id",
    input=CaseInput(
        headers={"x-user-id": "u_owner"},
        json_body={
            "bot_id": "bot_test",
            "owner_id": "",
            "config_update": {"device_count": 2},
        },
    ),
    seed=_seed_bot_owner_only,
    expect=ExpectError(
        status=200,
        json_contains={"success": False, "error_code": 400},
    ),
)
def update_service_bot_config_empty_owner_id():
    """Returns 400 when owner_id is empty."""


# ============================================================================
# Error Path - Empty config_update
# ============================================================================


@endpoint_test(
    method="POST",
    path="/api/service-bot/publish/update-service-bot-config",
    scenario="empty_config_update",
    input=CaseInput(
        headers={"x-user-id": "u_owner"},
        json_body={
            "bot_id": "bot_test",
            "owner_id": "u_owner",
            "config_update": {},
        },
    ),
    seed=_seed_bot_owner_only,
    expect=ExpectError(
        status=200,
        json_contains={"success": False, "error_code": 400},
    ),
)
def update_service_bot_config_empty_config():
    """Returns 400 when config_update is empty."""


# ============================================================================
# Happy Path - Ext as JSON string (legacy format)
# ============================================================================


def _assert_ext_string_parsed(response, world: World) -> None:
    """Verify ext JSON string was parsed and config merged."""
    repo = world.get(BotRepository)
    _, items = repo.list_by_conditions(bot_id="bot_test", page=1, page_size=1)
    assert items, "bot should exist after update"
    ext = items[0].get("ext") or {}
    # ext should be a dict now (after parsing and update)
    assert isinstance(ext, dict), f"ext should be dict, got {type(ext)}"
    service_bot_config = ext.get("service_bot_config") or {}
    # Original value from JSON string should be preserved
    assert service_bot_config.get("device_count") == 1, "original device_count should remain"
    # New value should be added
    assert service_bot_config.get("memory") == 4096, "memory should be added"


@endpoint_test(
    method="POST",
    path="/api/service-bot/publish/update-service-bot-config",
    scenario="ext_as_json_string",
    input=CaseInput(
        headers={"x-user-id": "u_owner"},
        json_body={
            "bot_id": "bot_test",
            "owner_id": "u_owner",
            "config_update": {"memory": 4096},
        },
    ),
    seed=_seed_bot_with_ext_as_string,
    expect=ExpectSuccess(
        status=200,
        json_contains={"success": True},
    ),
    extra_assertions=(_assert_ext_string_parsed,),
)
def update_service_bot_config_ext_as_string():
    """When ext is a JSON string, it should be parsed and merged."""


# ============================================================================
# Happy Path - Invalid service_bot_config replaced with valid dict
# ============================================================================


def _assert_invalid_config_replaced(response, world: World) -> None:
    """Verify invalid service_bot_config was replaced with valid dict."""
    repo = world.get(BotRepository)
    _, items = repo.list_by_conditions(bot_id="bot_test", page=1, page_size=1)
    assert items, "bot should exist after update"
    ext = items[0].get("ext") or {}
    service_bot_config = ext.get("service_bot_config") or {}
    # Invalid string value should be replaced with valid config
    assert isinstance(service_bot_config, dict), f"service_bot_config should be dict, got {type(service_bot_config)}"
    # New value should be present
    assert service_bot_config.get("device_count") == 5, "device_count should be 5"


@endpoint_test(
    method="POST",
    path="/api/service-bot/publish/update-service-bot-config",
    scenario="invalid_service_bot_config",
    input=CaseInput(
        headers={"x-user-id": "u_owner"},
        json_body={
            "bot_id": "bot_test",
            "owner_id": "u_owner",
            "config_update": {"device_count": 5},
        },
    ),
    seed=_seed_bot_with_invalid_service_bot_config,
    expect=ExpectSuccess(
        status=200,
        json_contains={"success": True},
    ),
    extra_assertions=(_assert_invalid_config_replaced,),
)
def update_service_bot_config_invalid_config_replaced():
    """When service_bot_config is not a dict, it should be replaced with valid config."""


# ============================================================================
# Error Path - BotPublishServiceError
# ============================================================================


def _seed_and_fail_publish(world: World) -> None:
    """Seed the bot, then make the ext write fail the way the domain would.

    Nothing a caller can send makes ``update_bot_ext`` refuse — the refusal
    comes from downstream publish state — so the failure is injected at the
    DI seam. The router branch under test is the one that turns
    ``BotPublishServiceError`` into error_code 403.
    """
    from agentclaw.community.api.bot_service import BotServiceProtocol
    make_staff_user(world, user_id="u_owner")
    make_bot(world, bot_id="bot_test", owner_id="u_owner", bot_type="service", status="ACTIVE")
    bind_failing_method(
        world,
        BotServiceProtocol,
        "update_bot_ext",
        BotPublishServiceError("Permission denied for update"),
    )


@endpoint_test(
    method="POST",
    path="/api/service-bot/publish/update-service-bot-config",
    scenario="publish_service_error",
    input=CaseInput(
        headers={"x-user-id": "u_owner"},
        json_body={
            "bot_id": "bot_test",
            "owner_id": "u_owner",
            "config_update": {"device_count": 2},
        },
    ),
    seed=_seed_and_fail_publish,
    expect=ExpectError(
        status=200,
        json_contains={"success": False, "error_code": 403},
    ),
)
def update_service_bot_config_publish_error():
    """Returns 403 when BotPublishServiceError is raised."""


# ============================================================================
# Error Path - Unexpected Exception
# ============================================================================


def _seed_and_fail_unexpectedly(world: World) -> None:
    """Seed the bot, then drop the connection under the ext write.

    A lost database connection is the archetypal failure this branch is for:
    it cannot be provoked from the request, and the router must still answer
    with the 500 envelope rather than a bare traceback.
    """
    from agentclaw.community.api.bot_service import BotServiceProtocol
    make_staff_user(world, user_id="u_owner")
    make_bot(world, bot_id="bot_test", owner_id="u_owner", bot_type="service", status="ACTIVE")
    bind_failing_method(
        world,
        BotServiceProtocol,
        "update_bot_ext",
        RuntimeError("Database connection lost"),
    )


@endpoint_test(
    method="POST",
    path="/api/service-bot/publish/update-service-bot-config",
    scenario="unexpected_error",
    input=CaseInput(
        headers={"x-user-id": "u_owner"},
        json_body={
            "bot_id": "bot_test",
            "owner_id": "u_owner",
            "config_update": {"device_count": 2},
        },
    ),
    seed=_seed_and_fail_unexpectedly,
    expect=ExpectError(
        status=200,
        json_contains={"success": False, "error_code": 500},
    ),
)
def update_service_bot_config_unexpected_error():
    """Returns 500 when unexpected Exception is raised."""