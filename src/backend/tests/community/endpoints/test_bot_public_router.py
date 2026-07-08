"""Smoke tests for bot public endpoints.

Tests the following endpoints from ``adapters/http/bot_public/router.py``:
- POST /api/bots/{bot_id}/public - 公开/取消公开 Bot (支持协作者场景)
"""
from __future__ import annotations

from unittest.mock import patch, MagicMock

from tests.community.factories.access import make_staff_user
from tests.community.factories.bot_collaborator import make_bot, make_collaborator
from tests.community.framework import (
    CaseInput,
    ExpectError,
    ExpectSuccess,
    endpoint_test,
)


# ============================================================================
# Test Setup: seed users, bots, and collaborators
# ============================================================================


def _seed_owner_with_bot(world):
    """Seed a bot owner with an active bot."""
    make_staff_user(world, user_id="u_owner")
    make_bot(world, bot_id="bot_test", owner_id="u_owner", bot_type="service", status="ACTIVE")


def _seed_collaborator_scenario(world):
    """Seed a bot owner, a collaborator with admin role, and a bot.

    Also acquires a lock for the collaborator so they can perform operations.
    """
    from agentclaw.community.core.bot_collaborator.services.collaborator_lock_service import CollaboratorLockService

    make_staff_user(world, user_id="u_owner")
    make_staff_user(world, user_id="u_collab_admin")
    make_bot(world, bot_id="bot_collab_test", owner_id="u_owner", bot_type="service", status="ACTIVE")
    # Add collaborator with admin role (can public/unpublic)
    make_collaborator(
        world,
        bot_id="bot_collab_test",
        owner_id="u_owner",
        user_id="u_collab_admin",
        role="admin",
        operator_id="u_owner",
    )
    # Acquire lock for collaborator
    lock_service = world.get(CollaboratorLockService)
    lock_service.acquire_lock("bot_collab_test", "u_owner", "u_collab_admin")


def _seed_collaborator_member_scenario(world):
    """Seed a collaborator with member role (lower permission).

    Also acquires a lock for the member so they can perform operations.
    """
    from agentclaw.community.core.bot_collaborator.services.collaborator_lock_service import CollaboratorLockService

    make_staff_user(world, user_id="u_owner")
    make_staff_user(world, user_id="u_collab_member")
    make_bot(world, bot_id="bot_member_test", owner_id="u_owner", bot_type="service", status="ACTIVE")
    # Add collaborator with member role (may not have enough permission)
    make_collaborator(
        world,
        bot_id="bot_member_test",
        owner_id="u_owner",
        user_id="u_collab_member",
        role="member",
        operator_id="u_owner",
    )
    # Acquire lock for member
    lock_service = world.get(CollaboratorLockService)
    lock_service.acquire_lock("bot_member_test", "u_owner", "u_collab_member")


def _seed_stranger_scenario(world):
    """Seed a bot owner and stranger user (no permission)."""
    make_staff_user(world, user_id="u_owner")
    make_staff_user(world, user_id="u_stranger")
    make_bot(world, bot_id="bot_stranger_test", owner_id="u_owner", bot_type="service", status="ACTIVE")


# ============================================================================
# Extra Assertions
# ============================================================================


def _assert_public_success(response, world):
    """Assert that public operation succeeded."""
    data = response.json()
    assert data.get("success") is True, f"Expected success=True, got: {data}"
    # ApiResponse defaults error_code to 200 on success
    assert data.get("error_code") == 200, f"Expected error_code=200, got: {data.get('error_code')}"


def _assert_permission_denied(response, world):
    """Assert that permission was denied.

    Interceptor returns HTTP 403 (standard REST behavior) with error_code in body.
    """
    data = response.json()
    assert data.get("success") is False, f"Expected success=False, got: {data}"
    assert data.get("error_code") == 403, f"Expected error_code=403, got: {data.get('error_code')}"


def _assert_bot_not_found(response, world):
    """Assert that bot was not found."""
    data = response.json()
    assert data.get("success") is False, f"Expected success=False, got: {data}"
    assert data.get("error_code") == 404, f"Expected error_code=404, got: {data.get('error_code')}"


# ============================================================================
# POST /api/bots/{bot_id}/public - Owner Operation (无 owner_id)
# ============================================================================


@endpoint_test(
    method="POST",
    path="/api/bots/{bot_id}/public",
    scenario="owner_public_without_owner_id",
    input=CaseInput(
        path_params={"bot_id": "bot_test"},
        headers={"x-user-id": "u_owner"},
        json_body={
            "public": "1",
            "permission_owner": "caller",
            "friend_approval": "0",
        },
    ),
    seed=_seed_owner_with_bot,
    expect=ExpectSuccess(
        status=200,
        json_contains={"success": True},
    ),
    extra_assertions=(_assert_public_success,),
)
def test_owner_public_bot_without_owner_id():
    """Owner 可以公开自己的 Bot，不需要提供 owner_id 参数。"""


@endpoint_test(
    method="POST",
    path="/api/bots/{bot_id}/public",
    scenario="owner_unpublic_without_owner_id",
    input=CaseInput(
        path_params={"bot_id": "bot_test"},
        headers={"x-user-id": "u_owner"},
        json_body={
            "public": "0",
            "permission_owner": "caller",
            "friend_approval": "0",
        },
    ),
    seed=_seed_owner_with_bot,
    expect=ExpectSuccess(
        status=200,
        json_contains={"success": True},
    ),
    extra_assertions=(_assert_public_success,),
)
def test_owner_unpublic_bot_without_owner_id():
    """Owner 可以取消公开自己的 Bot，不需要提供 owner_id 参数。"""


# ============================================================================
# POST /api/bots/{bot_id}/public - Owner Operation (显式提供 owner_id)
# ============================================================================


@endpoint_test(
    method="POST",
    path="/api/bots/{bot_id}/public",
    scenario="owner_public_with_explicit_owner_id",
    input=CaseInput(
        path_params={"bot_id": "bot_test"},
        headers={"x-user-id": "u_owner"},
        json_body={
            "public": "1",
            "permission_owner": "caller",
            "friend_approval": "0",
            "owner_id": "u_owner",  # 显式提供 owner_id
        },
    ),
    seed=_seed_owner_with_bot,
    expect=ExpectSuccess(
        status=200,
        json_contains={"success": True},
    ),
    extra_assertions=(_assert_public_success,),
)
def test_owner_public_bot_with_explicit_owner_id():
    """Owner 显式提供 owner_id 时可以公开自己的 Bot。"""


# ============================================================================
# POST /api/bots/{bot_id}/public - Collaborator Operation (提供 owner_id)
# ============================================================================


@endpoint_test(
    method="POST",
    path="/api/bots/{bot_id}/public",
    scenario="collaborator_admin_public_with_owner_id",
    input=CaseInput(
        path_params={"bot_id": "bot_collab_test"},
        headers={"x-user-id": "u_collab_admin"},
        json_body={
            "public": "1",
            "permission_owner": "caller",
            "friend_approval": "0",
            "owner_id": "u_owner",  # 协作者必须提供 owner_id
        },
    ),
    seed=_seed_collaborator_scenario,
    expect=ExpectSuccess(
        status=200,
        json_contains={"success": True},
    ),
    extra_assertions=(_assert_public_success,),
)
def test_collaborator_admin_can_public_bot_with_owner_id():
    """有 admin 权限的协作者可以公开 Bot，需要提供 owner_id 参数。"""


@endpoint_test(
    method="POST",
    path="/api/bots/{bot_id}/public",
    scenario="collaborator_admin_unpublic_with_owner_id",
    input=CaseInput(
        path_params={"bot_id": "bot_collab_test"},
        headers={"x-user-id": "u_collab_admin"},
        json_body={
            "public": "0",
            "permission_owner": "caller",
            "friend_approval": "0",
            "owner_id": "u_owner",  # 协作者必须提供 owner_id
        },
    ),
    seed=_seed_collaborator_scenario,
    expect=ExpectSuccess(
        status=200,
        json_contains={"success": True},
    ),
    extra_assertions=(_assert_public_success,),
)
def test_collaborator_admin_can_unpublic_bot_with_owner_id():
    """有 admin 权限的协作者可以取消公开 Bot，需要提供 owner_id 参数。"""


# ============================================================================
# POST /api/bots/{bot_id}/public - Permission Denied
# ============================================================================


@endpoint_test(
    method="POST",
    path="/api/bots/{bot_id}/public",
    scenario="stranger_public_denied",
    input=CaseInput(
        path_params={"bot_id": "bot_stranger_test"},
        headers={"x-user-id": "u_stranger"},
        json_body={
            "public": "1",
            "permission_owner": "caller",
            "friend_approval": "0",
            "owner_id": "u_owner",  # 尝试操作别人的 Bot
        },
    ),
    seed=_seed_stranger_scenario,
    expect=ExpectError(
        status=403,
        json_contains={"success": False, "error_code": 403},
    ),
    extra_assertions=(_assert_permission_denied,),
)
def test_stranger_cannot_public_others_bot():
    """陌生人无法公开别人的 Bot，即使提供 owner_id。"""


@endpoint_test(
    method="POST",
    path="/api/bots/{bot_id}/public",
    scenario="collaborator_member_public_denied",
    input=CaseInput(
        path_params={"bot_id": "bot_member_test"},
        headers={"x-user-id": "u_collab_member"},
        json_body={
            "public": "1",
            "permission_owner": "caller",
            "friend_approval": "0",
            "owner_id": "u_owner",
        },
    ),
    seed=_seed_collaborator_member_scenario,
    expect=ExpectError(
        status=403,
        json_contains={"success": False, "error_code": 403},
    ),
    extra_assertions=(_assert_permission_denied,),
)
def test_collaborator_member_without_admin_permission_denied():
    """member 权限的协作者（没有 ADMIN 权限）无法公开 Bot。"""


# ============================================================================
# POST /api/bots/{bot_id}/public - Collaborator without owner_id (should use current user as owner)
# ============================================================================


@endpoint_test(
    method="POST",
    path="/api/bots/{bot_id}/public",
    scenario="collaborator_without_owner_id_uses_current_user",
    input=CaseInput(
        path_params={"bot_id": "bot_collab_test"},
        headers={"x-user-id": "u_collab_admin"},
        json_body={
            "public": "1",
            "permission_owner": "caller",
            "friend_approval": "0",
            # 不提供 owner_id - 此时使用当前用户 ID 查询，但 bot 的真正 owner 是 u_owner
        },
    ),
    seed=_seed_collaborator_scenario,
    expect=ExpectError(
        status=200,
        json_contains={"success": False, "error_code": 404},
    ),
    extra_assertions=(_assert_bot_not_found,),
)
def test_collaborator_without_owner_id_uses_current_user():
    """协作者不提供 owner_id 时，使用当前用户 ID 作为 owner_id。

    这种情况下：
    1. 拦截器会跳过权限检查（因为 user_id == owner_id）
    2. 但业务逻辑层用 owner_id=当前用户ID 查询 bot
    3. 由于 bot 的真正 owner 不是当前用户，所以找不到 bot

    协作者必须正确提供 owner_id 参数才能操作 bot。
    """


# ============================================================================
# POST /api/bots/{bot_id}/public - Bot not found
# ============================================================================


@endpoint_test(
    method="POST",
    path="/api/bots/{bot_id}/public",
    scenario="public_nonexistent_bot",
    input=CaseInput(
        path_params={"bot_id": "bot_not_exist"},
        headers={"x-user-id": "u_owner"},
        json_body={
            "public": "1",
            "permission_owner": "caller",
            "friend_approval": "0",
        },
    ),
    seed=_seed_owner_with_bot,
    expect=ExpectError(
        status=200,
        json_contains={"success": False, "error_code": 404},
    ),
    extra_assertions=(_assert_bot_not_found,),
)
def test_public_nonexistent_bot_returns_404():
    """公开不存在的 Bot 返回 404。"""


# ============================================================================
# POST /api/bots/{bot_id}/public - Interceptor parameter extraction
# ============================================================================


def _seed_for_interceptor_test(world):
    """Seed data for interceptor parameter extraction test.

    Also acquires a lock for the collaborator so they can perform operations.
    """
    from agentclaw.community.core.bot_collaborator.services.collaborator_lock_service import CollaboratorLockService

    make_staff_user(world, user_id="u_interceptor_owner")
    make_staff_user(world, user_id="u_interceptor_collab")
    make_bot(
        world,
        bot_id="bot_interceptor_test",
        owner_id="u_interceptor_owner",
        bot_type="service",
        status="ACTIVE",
    )
    make_collaborator(
        world,
        bot_id="bot_interceptor_test",
        owner_id="u_interceptor_owner",
        user_id="u_interceptor_collab",
        role="admin",
        operator_id="u_interceptor_owner",
    )
    # Acquire lock for collaborator
    lock_service = world.get(CollaboratorLockService)
    lock_service.acquire_lock("bot_interceptor_test", "u_interceptor_owner", "u_interceptor_collab")


@endpoint_test(
    method="POST",
    path="/api/bots/{bot_id}/public",
    scenario="interceptor_extracts_owner_id_from_request",
    input=CaseInput(
        path_params={"bot_id": "bot_interceptor_test"},
        headers={"x-user-id": "u_interceptor_collab"},
        json_body={
            "public": "1",
            "permission_owner": "caller",
            "friend_approval": "0",
            "owner_id": "u_interceptor_owner",  # 拦截器应该正确提取这个参数
        },
    ),
    seed=_seed_for_interceptor_test,
    expect=ExpectSuccess(
        status=200,
        json_contains={"success": True},
    ),
    extra_assertions=(_assert_public_success,),
)
def test_interceptor_correctly_extracts_owner_id_from_request_body():
    """拦截器应该正确从 request body 中提取 owner_id 参数。

    验证 CollaboratorPermissionInterceptor 的配置：
    - bot_id="$bot_id" (从 path 参数提取)
    - owner_id="$req.owner_id" (从 request body 提取，参数名为 req)
    """