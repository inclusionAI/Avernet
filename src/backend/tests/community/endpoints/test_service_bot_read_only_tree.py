"""Smoke tests for service-bot read-only/tree endpoint.

Tests the following endpoint from ``api/service_bot/router_build.py``:
- GET /api/service-bot/read-only/tree
"""
from __future__ import annotations

from agentclaw.community.core.bot_management.repository.protocol import BotRepository
from tests.community.factories.access import make_staff_user
from tests.community.framework import (
    CaseInput,
    ExpectError,
    ExpectSuccess,
    endpoint_test,
)


# ============================================================================
# Test Setup: seed bot owner and bot
# ============================================================================


def _seed_service_bot(world):
    """Seed bot owner and an active service bot with openclaw engine."""
    make_staff_user(world, user_id="u_owner")
    bot_repo = world.get(BotRepository)
    bot_repo.insert(
        {
            "bot_id": "bot_readonly",
            "bot_name": "Bot readonly",
            "owner_id": "u_owner",
            "bot_type": "service",
            "status": "ACTIVE",
            "entity_id": "u_owner",
            "entity_type": "user",
            "creator_id": "u_owner",
            "active_engine": "openclaw",
        }
    )


def _seed_inactive_bot(world):
    """Seed an inactive service bot."""
    make_staff_user(world, user_id="u_owner")
    bot_repo = world.get(BotRepository)
    bot_repo.insert(
        {
            "bot_id": "bot_inactive",
            "bot_name": "Bot inactive",
            "owner_id": "u_owner",
            "bot_type": "service",
            "status": "INACTIVE",
            "entity_id": "u_owner",
            "entity_type": "user",
            "creator_id": "u_owner",
            "active_engine": "openclaw",
        }
    )


def _seed_non_service_bot(world):
    """Seed a non-service type bot (e.g., normal bot)."""
    make_staff_user(world, user_id="u_owner")
    bot_repo = world.get(BotRepository)
    bot_repo.insert(
        {
            "bot_id": "bot_normal",
            "bot_name": "Bot normal",
            "owner_id": "u_owner",
            "bot_type": "normal",
            "status": "ACTIVE",
            "entity_id": "u_owner",
            "entity_type": "user",
            "creator_id": "u_owner",
            "active_engine": "openclaw",
        }
    )


def _seed_bot_with_other_owner(world):
    """Seed a bot owned by another user."""
    make_staff_user(world, user_id="u_owner")
    make_staff_user(world, user_id="u_other")
    bot_repo = world.get(BotRepository)
    bot_repo.insert(
        {
            "bot_id": "bot_other_owner",
            "bot_name": "Bot other owner",
            "owner_id": "u_other",
            "bot_type": "service",
            "status": "ACTIVE",
            "entity_id": "u_other",
            "entity_type": "user",
            "creator_id": "u_other",
            "active_engine": "openclaw",
        }
    )


def _seed_bot_with_custom_rules(world):
    """Seed a service bot with custom read-only rules."""
    make_staff_user(world, user_id="u_owner")
    bot_repo = world.get(BotRepository)
    bot_repo.insert(
        {
            "bot_id": "bot_custom_rules",
            "bot_name": "Bot custom rules",
            "owner_id": "u_owner",
            "bot_type": "service",
            "status": "ACTIVE",
            "entity_id": "u_owner",
            "entity_type": "user",
            "creator_id": "u_owner",
            "active_engine": "openclaw",
            "ext": {
                "read_only_rules": [
                    {"path": "/custom/path/file.txt", "rule_type": "file"},
                    {"path": "/custom/dir/*", "rule_type": "glob"},
                ]
            },
        }
    )


# ============================================================================
# Extra Assertions
# ============================================================================


def _assert_tree_response_fields(response, world):
    """Assert that tree response has all required fields."""
    data = response.json()["data"]
    assert "base_path" in data, "Response should have base_path"
    assert "items" in data, "Response should have items"
    assert "default_rules" in data, "Response should have default_rules"
    assert "custom_rules" in data, "Response should have custom_rules"
    assert isinstance(data["items"], list), "items should be a list"
    assert isinstance(data["default_rules"], list), "default_rules should be a list"
    assert isinstance(data["custom_rules"], list), "custom_rules should be a list"


def _assert_item_fields(response, world):
    """Assert that each item in tree has required fields."""
    data = response.json()["data"]
    items = data.get("items", [])
    if len(items) > 0:
        item = items[0]
        required_fields = ["name", "path", "is_dir", "is_readonly_default", "is_readonly_custom"]
        for field in required_fields:
            assert field in item, f"Item should have {field}"
        assert isinstance(item["is_dir"], bool), "is_dir should be boolean"
        assert isinstance(item["is_readonly_default"], bool), "is_readonly_default should be boolean"
        assert isinstance(item["is_readonly_custom"], bool), "is_readonly_custom should be boolean"


def _assert_rule_fields(response, world):
    """Assert that rules have correct structure."""
    data = response.json()["data"]
    default_rules = data.get("default_rules", [])
    if len(default_rules) > 0:
        rule = default_rules[0]
        assert "path" in rule, "Rule should have path"
        assert "rule_type" in rule, "Rule should have rule_type"


def _assert_custom_rules_present(response, world):
    """Assert that custom rules are present in response."""
    data = response.json()["data"]
    custom_rules = data.get("custom_rules", [])
    assert len(custom_rules) >= 2, "Should have at least 2 custom rules"


def _assert_base_path_is_string(response, world):
    """Assert that base_path is a non-empty string."""
    data = response.json()["data"]
    base_path = data.get("base_path", "")
    assert isinstance(base_path, str), "base_path should be a string"
    assert len(base_path) > 0, "base_path should not be empty"


# ============================================================================
# Authentication & Authorization Tests
# ============================================================================


@endpoint_test(
    method="GET",
    path="/api/service-bot/read-only/tree",
    scenario="anonymous_user",
    input=CaseInput(
        headers={"x-user-id": "anonymous"},
        query_params={"bot_id": "bot_readonly", "owner_id": "u_owner"}
    ),
    seed=_seed_service_bot,
    expect=ExpectError(
        status=403,  # Interceptor returns 403 for anonymous user trying to access another user's bot
        json_contains={"success": False, "error_code": 403},
    ),
)
def get_read_only_tree_anonymous():
    """Get read-only tree with anonymous user returns 403."""


@endpoint_test(
    method="GET",
    path="/api/service-bot/read-only/tree",
    scenario="missing_user_header",
    input=CaseInput(
        headers={},  # No x-user-id header
        query_params={"bot_id": "bot_readonly", "owner_id": "u_owner"}
    ),
    seed=_seed_service_bot,
    expect=ExpectError(
        status=401,  # FastAPI auth middleware returns 401
    ),
)
def get_read_only_tree_missing_user():
    """Get read-only tree without user header returns 401."""


def _seed_bot_with_two_users(world):
    """Seed both users and bot owned by u_owner."""
    make_staff_user(world, user_id="u_owner")
    make_staff_user(world, user_id="u_other")
    bot_repo = world.get(BotRepository)
    bot_repo.insert(
        {
            "bot_id": "bot_readonly",
            "bot_name": "Bot readonly",
            "owner_id": "u_owner",
            "bot_type": "service",
            "status": "ACTIVE",
            "entity_id": "u_owner",
            "entity_type": "user",
            "creator_id": "u_owner",
            "active_engine": "openclaw",
        }
    )


@endpoint_test(
    method="GET",
    path="/api/service-bot/read-only/tree",
    scenario="user_not_owner",
    input=CaseInput(
        headers={"x-user-id": "u_other"},
        query_params={"bot_id": "bot_readonly", "owner_id": "u_owner"}
    ),
    seed=_seed_bot_with_two_users,
    expect=ExpectError(
        status=403,  # Interceptor returns 403 HTTP status code for permission denied
        json_contains={"success": False, "error_code": 403},
    ),
)
def get_read_only_tree_user_not_owner():
    """Get read-only tree by user who doesn't own the bot returns 403."""


# ============================================================================
# Parameter Validation Tests
# ============================================================================


@endpoint_test(
    method="GET",
    path="/api/service-bot/read-only/tree",
    scenario="missing_bot_id",
    input=CaseInput(
        headers={"x-user-id": "u_owner"},
        query_params={}  # No bot_id
    ),
    seed=lambda world: make_staff_user(world, user_id="u_owner"),
    expect=ExpectError(
        status=422,  # FastAPI validation error
    ),
)
def get_read_only_tree_missing_bot_id():
    """Get read-only tree without bot_id returns 422 validation error."""


@endpoint_test(
    method="GET",
    path="/api/service-bot/read-only/tree",
    scenario="empty_bot_id",
    input=CaseInput(
        headers={"x-user-id": "u_owner"},
        query_params={"bot_id": "", "owner_id": "u_owner"}
    ),
    seed=lambda world: make_staff_user(world, user_id="u_owner"),
    expect=ExpectError(
        status=200,
        json_contains={"success": False},
    ),
)
def get_read_only_tree_empty_bot_id():
    """Get read-only tree with empty bot_id returns error."""


@endpoint_test(
    method="GET",
    path="/api/service-bot/read-only/tree",
    scenario="bot_id_with_special_chars",
    input=CaseInput(
        headers={"x-user-id": "u_owner"},
        query_params={"bot_id": "bot<script>alert(1)</script>", "owner_id": "u_owner"}
    ),
    seed=lambda world: make_staff_user(world, user_id="u_owner"),
    expect=ExpectError(
        status=200,
        json_contains={"success": False},
    ),
)
def get_read_only_tree_special_chars():
    """Get read-only tree with XSS-like bot_id returns error."""


@endpoint_test(
    method="GET",
    path="/api/service-bot/read-only/tree",
    scenario="bot_id_sql_injection_attempt",
    input=CaseInput(
        headers={"x-user-id": "u_owner"},
        query_params={"bot_id": "bot'; DROP TABLE bots; --", "owner_id": "u_owner"}
    ),
    seed=lambda world: make_staff_user(world, user_id="u_owner"),
    expect=ExpectError(
        status=200,
        json_contains={"success": False},
    ),
)
def get_read_only_tree_sql_injection():
    """Get read-only tree with SQL injection attempt in bot_id returns error."""


@endpoint_test(
    method="GET",
    path="/api/service-bot/read-only/tree",
    scenario="very_long_bot_id",
    input=CaseInput(
        headers={"x-user-id": "u_owner"},
        query_params={"bot_id": "a" * 1000, "owner_id": "u_owner"}
    ),
    seed=lambda world: make_staff_user(world, user_id="u_owner"),
    expect=ExpectError(
        status=200,
        json_contains={"success": False},
    ),
)
def get_read_only_tree_very_long_bot_id():
    """Get read-only tree with very long bot_id returns error."""


# ============================================================================
# Path Parameter Tests
# ============================================================================


@endpoint_test(
    method="GET",
    path="/api/service-bot/read-only/tree",
    scenario="path_traversal_attempt",
    input=CaseInput(
        headers={"x-user-id": "u_owner"},
        query_params={"bot_id": "bot_readonly", "owner_id": "u_owner", "path": "../../../etc/passwd"}
    ),
    seed=_seed_service_bot,
    expect=ExpectError(
        status=200,
        json_contains={"success": False, "error_code": 400},
    ),
)
def get_read_only_tree_path_traversal():
    """Get read-only tree with path traversal attempt returns an application error."""


@endpoint_test(
    method="GET",
    path="/api/service-bot/read-only/tree",
    scenario="absolute_path",
    input=CaseInput(
        headers={"x-user-id": "u_owner"},
        query_params={"bot_id": "bot_readonly", "owner_id": "u_owner", "path": "/etc/passwd"}
    ),
    seed=_seed_service_bot,
    expect=ExpectError(
        status=200,
        json_contains={"success": False, "error_code": 400},
    ),
)
def get_read_only_tree_absolute_path():
    """Get read-only tree with absolute path returns an application error."""


@endpoint_test(
    method="GET",
    path="/api/service-bot/read-only/tree",
    scenario="path_with_null_byte",
    input=CaseInput(
        headers={"x-user-id": "u_owner"},
        query_params={"bot_id": "bot_readonly", "owner_id": "u_owner", "path": "dir\u0000file.txt"}
    ),
    seed=_seed_service_bot,
    expect=ExpectError(
        status=200,
        json_contains={"success": False, "error_code": 400},
    ),
)
def get_read_only_tree_null_byte_path():
    """Get read-only tree with null byte in path returns an application error."""


# ============================================================================
# Recursive Parameter Tests
# ============================================================================


@endpoint_test(
    method="GET",
    path="/api/service-bot/read-only/tree",
    scenario="recursive_true",
    input=CaseInput(
        headers={"x-user-id": "u_owner"},
        query_params={"bot_id": "bot_readonly", "owner_id": "u_owner", "recursive": "true"}
    ),
    seed=_seed_service_bot,
    expect=ExpectSuccess(
        status=200,
        json_contains={"success": True},
    ),
    extra_assertions=(_assert_tree_response_fields,),
)
def get_read_only_tree_recursive_true():
    """Get read-only tree with recursive=true returns success."""


@endpoint_test(
    method="GET",
    path="/api/service-bot/read-only/tree",
    scenario="recursive_false",
    input=CaseInput(
        headers={"x-user-id": "u_owner"},
        query_params={"bot_id": "bot_readonly", "owner_id": "u_owner", "recursive": "false"}
    ),
    seed=_seed_service_bot,
    expect=ExpectSuccess(
        status=200,
        json_contains={"success": True},
    ),
    extra_assertions=(_assert_tree_response_fields,),
)
def get_read_only_tree_recursive_false():
    """Get read-only tree with recursive=false returns success."""


@endpoint_test(
    method="GET",
    path="/api/service-bot/read-only/tree",
    scenario="recursive_invalid",
    input=CaseInput(
        headers={"x-user-id": "u_owner"},
        query_params={"bot_id": "bot_readonly", "owner_id": "u_owner", "recursive": "invalid"}
    ),
    seed=_seed_service_bot,
    expect=ExpectError(
        status=422,  # FastAPI validation error for invalid boolean
    ),
)
def get_read_only_tree_recursive_invalid():
    """Get read-only tree with invalid recursive value returns 422 validation error."""


# ============================================================================
# Bot State Tests
# ============================================================================


@endpoint_test(
    method="GET",
    path="/api/service-bot/read-only/tree",
    scenario="inactive_bot",
    input=CaseInput(
        headers={"x-user-id": "u_owner"},
        query_params={"bot_id": "bot_inactive", "owner_id": "u_owner"}
    ),
    seed=_seed_inactive_bot,
    expect=ExpectSuccess(
        status=200,
        json_contains={"success": True},
    ),
    extra_assertions=(_assert_tree_response_fields,),
)
def get_read_only_tree_inactive_bot():
    """Get read-only tree for inactive bot returns success."""


@endpoint_test(
    method="GET",
    path="/api/service-bot/read-only/tree",
    scenario="non_service_bot",
    input=CaseInput(
        headers={"x-user-id": "u_owner"},
        query_params={"bot_id": "bot_normal", "owner_id": "u_owner"}
    ),
    seed=_seed_non_service_bot,
    expect=ExpectSuccess(
        status=200,
        json_contains={"success": True},
    ),
    extra_assertions=(_assert_tree_response_fields,),
)
def get_read_only_tree_non_service_bot():
    """Get read-only tree for non-service type bot returns success."""


# ============================================================================
# Success Cases with Full Assertions
# ============================================================================


@endpoint_test(
    method="GET",
    path="/api/service-bot/read-only/tree",
    scenario="ok",
    input=CaseInput(
        headers={"x-user-id": "u_owner"},
        query_params={"bot_id": "bot_readonly", "owner_id": "u_owner"}
    ),
    seed=_seed_service_bot,
    expect=ExpectSuccess(
        status=200,
        json_contains={"success": True},
    ),
    extra_assertions=(_assert_tree_response_fields, _assert_item_fields, _assert_rule_fields, _assert_base_path_is_string),
)
def get_read_only_tree_ok():
    """Get read-only tree returns success with all required fields."""


@endpoint_test(
    method="GET",
    path="/api/service-bot/read-only/tree",
    scenario="bot_not_found",
    input=CaseInput(
        headers={"x-user-id": "u_owner"},
        query_params={"bot_id": "bot_notexist", "owner_id": "u_owner"}
    ),
    seed=lambda world: make_staff_user(world, user_id="u_owner"),
    expect=ExpectError(
        status=200,
        json_contains={"success": False},  # Bot not found returns success=False
    ),
)
def get_read_only_tree_bot_not_found():
    """Get read-only tree for non-existent bot returns error."""


@endpoint_test(
    method="GET",
    path="/api/service-bot/read-only/tree",
    scenario="bot_with_custom_rules",
    input=CaseInput(
        headers={"x-user-id": "u_owner"},
        query_params={"bot_id": "bot_custom_rules", "owner_id": "u_owner"}
    ),
    seed=_seed_bot_with_custom_rules,
    expect=ExpectSuccess(
        status=200,
        json_contains={"success": True},
    ),
    extra_assertions=(_assert_tree_response_fields, _assert_custom_rules_present),
)
def get_read_only_tree_custom_rules():
    """Get read-only tree for bot with custom rules returns success."""


@endpoint_test(
    method="GET",
    path="/api/service-bot/read-only/tree",
    scenario="bot_owned_by_other_user",
    input=CaseInput(
        headers={"x-user-id": "u_owner"},
        query_params={"bot_id": "bot_other_owner", "owner_id": "u_other"}
    ),
    seed=_seed_bot_with_other_owner,
    expect=ExpectError(
        status=403,  # Interceptor returns 403 HTTP status code
        json_contains={"success": False, "error_code": 403},
    ),
)
def get_read_only_tree_bot_owned_by_other():
    """Get read-only tree for bot owned by another user returns 403."""