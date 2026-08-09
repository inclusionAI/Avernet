"""Smoke tests for bot collaborator endpoints.

Tests the following endpoints from ``adapters/http/bot_collaborator/router.py``:
- POST /api/bot/collaborator/add
- POST /api/bot/collaborator/add_for_others
- GET /api/bot/collaborator/list
- POST /api/bot/collaborator/update
- POST /api/bot/collaborator/remove
- POST /api/bot/collaborator/leave
- POST /api/bot/collaborator/check_permission
- POST /api/bot/collaborator/lock/acquire
- POST /api/bot/collaborator/lock/release
- GET /api/bot/collaborator/lock/info
"""
from __future__ import annotations

from agentclaw.community.core.bot_collaborator.services.collaborator_lock_service import CollaboratorLockService
from agentclaw.community.core.repository.protocols.bot import BotCollabLockRepositoryProtocol
from tests.community.factories.access import make_staff_user
from tests.community.factories.bot_collaborator import make_bot, make_collaborator
from tests.community.framework import (
    CaseInput,
    ExpectError,
    ExpectSuccess,
    endpoint_test,
)


# ============================================================================
# Test Setup: seed bot owner and bot
# ============================================================================


def _seed_bot_owner(world):
    """Seed bot owner and an active service bot."""
    make_staff_user(world, user_id="u_owner")
    make_bot(world, bot_id="bot_test", owner_id="u_owner", bot_type="service", status="ACTIVE")


def _seed_bot_empty(world):
    """Seed a bot with no collaborators for empty list test."""
    make_staff_user(world, user_id="u_owner")
    make_bot(world, bot_id="bot_empty", owner_id="u_owner", bot_type="service", status="ACTIVE")


def _seed_collaborator(world):
    """Seed a collaborator for testing."""
    _seed_bot_owner(world)
    make_collaborator(
        world,
        bot_id="bot_test",
        owner_id="u_owner",
        user_id="u_collab",
        role="member",
        operator_id="u_owner",
    )


def _seed_bot_with_collab_for_lock(world):
    """Seed bot with collaborator for lock tests (lock requires collaborator to work)."""
    make_staff_user(world, user_id="u_owner")
    make_bot(world, bot_id="bot_test", owner_id="u_owner", bot_type="service", status="ACTIVE")
    # Add a collaborator so lock features work
    make_collaborator(
        world,
        bot_id="bot_test",
        owner_id="u_owner",
        user_id="u_collab",
        role="admin",
        operator_id="u_owner",
    )


def _seed_bot_with_lock(world):
    """Seed a bot with collaborator and acquire a lock for release test."""
    make_staff_user(world, user_id="u_owner")
    make_bot(world, bot_id="bot_test", owner_id="u_owner", owner_name="Owner Name", bot_type="service", status="ACTIVE")
    # Add a collaborator so lock info will be queried
    make_collaborator(
        world,
        bot_id="bot_test",
        owner_id="u_owner",
        user_id="u_collab",
        role="admin",
        operator_id="u_owner",
    )
    # Acquire a lock
    lock_service = world.get(CollaboratorLockService)
    lock_service.acquire_lock("bot_test", "u_owner", "u_owner")


def _seed_bot_owner_with_other_user(world):
    """Seed bot owner and another user for lock tests."""
    make_staff_user(world, user_id="u_owner")
    make_staff_user(world, user_id="u_other")
    make_bot(world, bot_id="bot_test", owner_id="u_owner", bot_type="service", status="ACTIVE")


# ============================================================================
# Extra Assertions for Lock API
# ============================================================================


def _assert_lock_info_valid(response, world):
    """Assert that lock info in response has valid structure."""
    data = response.json()["data"]
    lock = data.get("lock")
    assert lock is not None, "Expected lock to be present"
    assert "id" in lock, "Lock should have id"
    assert "lock_key" in lock, "Lock should have lock_key"
    assert "holder_user_id" in lock, "Lock should have holder_user_id"
    assert "holder_name" in lock, "Lock should have holder_name"
    assert "gmt_create" in lock, "Lock should have gmt_create"
    # Verify lock_key format: {bot_id}:{owner_id}
    assert lock["lock_key"] == "bot_test:u_owner", f"Unexpected lock_key: {lock['lock_key']}"


def _assert_lock_holder_is(expected_user_id):
    """Assert that lock is held by expected user."""
    def _assert(response, world):
        data = response.json()["data"]
        lock = data.get("lock")
        assert lock is not None, "Expected lock to be present"
        assert lock["holder_user_id"] == expected_user_id, (
            f"Expected holder_user_id={expected_user_id}, got {lock['holder_user_id']}"
        )
    return _assert


def _assert_lock_released(response, world):
    """Assert that lock was released by checking lock info."""
    lock_service = world.get(CollaboratorLockService)
    result = lock_service.get_lock_info("bot_test", "u_owner", "u_owner")
    assert result.lock is None, f"Expected lock to be released, but still held by {result.lock.holder_user_id if result.lock else None}"


def _assert_lock_exists(response, world):
    """Assert that lock exists after acquire."""
    lock_service = world.get(CollaboratorLockService)
    result = lock_service.get_lock_info("bot_test", "u_owner", "u_owner")
    assert result.lock is not None, "Expected lock to exist after acquire"
    assert result.lock.holder_user_id == "u_owner", f"Expected holder u_owner, got {result.lock.holder_user_id}"


def _assert_lock_held_by_other(response, world):
    """Assert that lock is still held by original owner after failed acquire attempt."""
    lock_service = world.get(CollaboratorLockService)
    result = lock_service.get_lock_info("bot_test", "u_owner", "u_owner")
    assert result.lock is not None, "Expected lock to still exist"
    assert result.lock.holder_user_id == "u_owner", (
        f"Expected lock still held by u_owner, got {result.lock.holder_user_id}"
    )


def _assert_collaborator_added(response, world):
    """Assert that collaborator was added with correct data."""
    data = response.json()["data"]
    assert data["bot_id"] == "bot_test", f"Expected bot_id=bot_test, got {data['bot_id']}"
    assert data["owner_id"] == "u_owner", f"Expected owner_id=u_owner, got {data['owner_id']}"
    assert data["user_id"] == "u_collab_new", f"Expected user_id=u_collab_new, got {data['user_id']}"
    assert data["role"] == "admin", f"Expected role=admin, got {data['role']}"
    assert "id" in data, "Response should include collaborator id"
    assert "gmt_create" in data, "Response should include gmt_create"


def _assert_collaborator_updated(response, world):
    """Assert that collaborator role was updated."""
    data = response.json()["data"]
    assert data["role"] == "admin", f"Expected role=admin after update, got {data['role']}"
    assert "gmt_modified" in data, "Response should include gmt_modified"


def _assert_collaborator_for_others_operator(response, world):
    """Assert that operator_id is owner_id (not caller) for add_for_others."""
    data = response.json()["data"]
    assert data["operator_id"] == "u_owner", (
        f"Expected operator_id=u_owner (owner_id), got {data['operator_id']}"
    )


def _assert_collaborator_list_fields(response, world):
    """Assert that collaborator list items have all required fields."""
    data = response.json()["data"]
    collaborators = data.get("collaborators", [])
    if len(collaborators) > 0:
        c = collaborators[0]
        required_fields = ["id", "bot_pk", "bot_id", "owner_id", "user_id", "role", "operator_id", "gmt_create", "gmt_modified"]
        for field in required_fields:
            assert field in c, f"Collaborator should have {field}"


def _assert_permission_check_fields(response, world):
    """Assert that permission check response has all required fields."""
    data = response.json()["data"]
    assert "has_permission" in data, "Response should have has_permission"
    assert "level" in data, "Response should have level"
    assert "level_value" in data, "Response should have level_value"
    assert isinstance(data["level_value"], int), "level_value should be an integer"


def _assert_list_contains_collaborator(response, world):
    """Assert that list response contains expected collaborator."""
    data = response.json()["data"]
    collaborators = data.get("collaborators", [])
    assert len(collaborators) >= 1, "Expected at least one collaborator"
    found = any(c["user_id"] == "u_collab" for c in collaborators)
    assert found, "Expected to find collaborator with user_id=u_collab"


def _assert_permission_level(expected_level, expected_has_permission):
    """Assert permission check result."""
    def _assert(response, world):
        data = response.json()["data"]
        assert data["has_permission"] == expected_has_permission, (
            f"Expected has_permission={expected_has_permission}, got {data['has_permission']}"
        )
        assert data["level"] == expected_level, f"Expected level={expected_level}, got {data['level']}"
    return _assert


# ============================================================================
# POST /api/bot/collaborator/add
# ============================================================================


@endpoint_test(
    method="POST",
    path="/api/bot/collaborator/add",
    scenario="ok",
    input=CaseInput(
        headers={"x-user-id": "u_owner"},
        json_body={
            "bot_id": "bot_test",
            "owner_id": "u_owner",
            "user_id": "u_collab_new",
            "role": "admin",
        }
    ),
    seed=_seed_bot_owner,
    expect=ExpectSuccess(
        status=200,
        json_contains={"success": True},
    ),
    extra_assertions=(_assert_collaborator_added,),
)
def add_collaborator_ok():
    """Add collaborator to bot returns success and correct data."""


@endpoint_test(
    method="POST",
    path="/api/bot/collaborator/add",
    scenario="bot_not_found",
    input=CaseInput(
        headers={"x-user-id": "u_owner"},
        json_body={
            "bot_id": "bot_notexist",
            "owner_id": "u_owner",
            "user_id": "u_collab",
            "role": "admin",
        }
    ),
    seed=lambda world: make_staff_user(world, user_id="u_owner"),
    expect=ExpectError(
        status=200,
        json_contains={"success": False, "error_code": 404},
    ),
)
def add_collaborator_bot_not_found():
    """Adding collaborator to non-existent bot returns 404."""


# ============================================================================
# POST /api/bot/collaborator/add - Lock tests
# ============================================================================


def _seed_bot_for_add_collab_lock(world):
    """Seed bot with collaborator and another user for add_collaborator lock tests."""
    make_staff_user(world, user_id="u_owner")
    make_staff_user(world, user_id="u_other")
    make_staff_user(world, user_id="u_new_collab")
    make_bot(world, bot_id="bot_test", owner_id="u_owner", owner_name="Owner Name", bot_type="service", status="ACTIVE")
    # Add an existing collaborator so lock mechanism is active
    make_collaborator(
        world,
        bot_id="bot_test",
        owner_id="u_owner",
        user_id="u_collab",
        user_name="Existing Collab",
        role="admin",
        operator_id="u_owner",
    )


def _seed_bot_with_lock_held_by_other(world):
    """Seed bot with lock held by another user for add_collaborator test."""
    _seed_bot_for_add_collab_lock(world)
    # Acquire lock as u_other (not the requester)
    lock_service = world.get(CollaboratorLockService)
    lock_service.acquire_lock("bot_test", "u_owner", "u_other")


def _seed_bot_with_lock_held_by_self(world):
    """Seed bot with lock held by the requester (owner) for add_collaborator test."""
    _seed_bot_for_add_collab_lock(world)
    # Acquire lock as u_owner (the requester)
    lock_service = world.get(CollaboratorLockService)
    lock_service.acquire_lock("bot_test", "u_owner", "u_owner")


def _assert_lock_acquired_by_caller(response, world):
    """Assert that lock was acquired by the caller after add_collaborator."""
    lock_service = world.get(CollaboratorLockService)
    result = lock_service.get_lock_info("bot_test", "u_owner", "u_owner")
    assert result.lock is not None, "Expected lock to exist after add_collaborator"
    assert result.lock.holder_user_id == "u_owner", f"Expected lock held by u_owner, got {result.lock.holder_user_id}"


def _assert_lock_held_by_other_after_add(response, world):
    """Assert that lock is still held by other user after failed add_collaborator."""
    lock_service = world.get(CollaboratorLockService)
    result = lock_service.get_lock_info("bot_test", "u_owner", "u_owner")
    assert result.lock is not None, "Expected lock to still exist"
    assert result.lock.holder_user_id == "u_other", f"Expected lock still held by u_other, got {result.lock.holder_user_id}"


def _assert_collaborator_not_added(response, world):
    """Assert that collaborator was NOT added (for lock failure cases)."""
    from agentclaw.community.api.collaborator_service import CollaboratorServiceProtocol
    service = world.get(CollaboratorServiceProtocol)
    collaborators = service.list_collaborators(
        bot_id="bot_test",
        owner_id="u_owner",
        user_id="u_owner",
    )
    # Should only have the existing collaborator u_collab, not u_new_collab
    user_ids = [c.user_id for c in collaborators]
    assert "u_new_collab" not in user_ids, f"Collaborator should NOT have been added, found: {user_ids}"


@endpoint_test(
    method="POST",
    path="/api/bot/collaborator/add",
    scenario="lock_held_by_other",
    input=CaseInput(
        headers={"x-user-id": "u_owner"},  # Requester is owner, but lock held by u_other
        json_body={
            "bot_id": "bot_test",
            "owner_id": "u_owner",
            "user_id": "u_new_collab",
            "role": "admin",
        }
    ),
    seed=_seed_bot_with_lock_held_by_other,
    expect=ExpectError(
        status=200,
        json_contains={"success": False, "error_code": 423},
    ),
    extra_assertions=(_assert_lock_held_by_other_after_add, _assert_collaborator_not_added),
)
def add_collaborator_lock_held_by_other():
    """Add collaborator when lock is held by another user returns 423."""


@endpoint_test(
    method="POST",
    path="/api/bot/collaborator/add",
    scenario="lock_held_by_self",
    input=CaseInput(
        headers={"x-user-id": "u_owner"},  # Requester already holds the lock
        json_body={
            "bot_id": "bot_test",
            "owner_id": "u_owner",
            "user_id": "u_new_collab",
            "role": "admin",
        }
    ),
    seed=_seed_bot_with_lock_held_by_self,
    expect=ExpectSuccess(
        status=200,
        json_contains={"success": True},
    ),
    extra_assertions=(_assert_lock_acquired_by_caller,),
)
def add_collaborator_lock_held_by_self():
    """Add collaborator when requester already holds lock succeeds (reentrant)."""


@endpoint_test(
    method="POST",
    path="/api/bot/collaborator/add",
    scenario="auto_acquire_lock",
    input=CaseInput(
        headers={"x-user-id": "u_owner"},  # Requester will auto-acquire lock
        json_body={
            "bot_id": "bot_test",
            "owner_id": "u_owner",
            "user_id": "u_new_collab",
            "role": "admin",
        }
    ),
    seed=_seed_bot_for_add_collab_lock,  # No lock initially
    expect=ExpectSuccess(
        status=200,
        json_contains={"success": True},
    ),
    extra_assertions=(_assert_lock_acquired_by_caller,),
)
def add_collaborator_auto_acquire_lock():
    """Add collaborator auto-acquires lock when no lock exists."""


def _seed_bot_with_deleted_lock(world):
    """Seed bot with lock record deleted to simulate race condition.

    This creates a state where:
    1. Lock was acquired by another user
    2. Lock record was directly deleted from DB (simulating race/timing issue)
    3. Now acquire_lock fails due to stale cache/state but get_lock_info returns None

    To trigger the race condition path (lines 128-134), we need acquire_lock
    to fail while get_lock_info shows no lock.
    """
    from agentclaw.community.core.repository.protocols.bot import BotCollabLockRepositoryProtocol

    _seed_bot_for_add_collab_lock(world)

    # Acquire lock as another user
    lock_service = world.get(CollaboratorLockService)
    lock = lock_service.acquire_lock("bot_test", "u_owner", "u_other")
    assert lock is not None

    # Delete the lock directly from repository (simulating race condition)
    lock_repo = world.get(BotCollabLockRepositoryProtocol)
    lock_repo.release("bot_test:u_owner")

    # Now acquire_lock will fail (transaction state) but get_lock_info shows None
    # This triggers the race condition branch


@endpoint_test(
    method="POST",
    path="/api/bot/collaborator/add",
    scenario="lock_race_condition",
    input=CaseInput(
        headers={"x-user-id": "u_owner"},
        json_body={
            "bot_id": "bot_test",
            "owner_id": "u_owner",
            "user_id": "u_new_collab",
            "role": "admin",
        }
    ),
    seed=_seed_bot_with_deleted_lock,
    expect=ExpectSuccess(
        status=200,
        json_contains={"success": True},
    ),
    extra_assertions=(_assert_lock_acquired_by_caller,),
)
def add_collaborator_lock_race_condition():
    """Add collaborator succeeds when lock race condition occurs.

    After lock is deleted, the next request can acquire it successfully.
    This tests that the code handles the edge case gracefully.
    """


# ============================================================================
# POST /api/bot/collaborator/add_for_others
# ============================================================================


@endpoint_test(
    method="POST",
    path="/api/bot/collaborator/add_for_others",
    scenario="ok",
    input=CaseInput(
        headers={"x-user-id": "100000"},  # Whitelisted user
        json_body={
            "bot_id": "bot_test",
            "owner_id": "u_owner",
            "user_id": "u_collab_for_others",
            "role": "admin",
        }
    ),
    seed=_seed_bot_owner,
    expect=ExpectSuccess(
        status=200,
        json_contains={"success": True},
    ),
    extra_assertions=(_assert_collaborator_for_others_operator,),
)
def add_collaborator_for_others_ok():
    """Add collaborator for others by whitelisted user returns success with operator_id=owner_id."""


@endpoint_test(
    method="POST",
    path="/api/bot/collaborator/add_for_others",
    scenario="forbidden",
    input=CaseInput(
        headers={"x-user-id": "u_owner"},  # Not whitelisted
        json_body={
            "bot_id": "bot_test",
            "owner_id": "u_owner",
            "user_id": "u_collab_for_others",
            "role": "admin",
        }
    ),
    seed=_seed_bot_owner,
    expect=ExpectError(
        status=200,
        json_contains={"success": False, "error_code": 403},
    ),
)
def add_collaborator_for_others_forbidden():
    """Add collaborator for others by non-whitelisted user returns 403."""


# ============================================================================
# GET /api/bot/collaborator/list
# ============================================================================


@endpoint_test(
    method="GET",
    path="/api/bot/collaborator/list",
    scenario="ok",
    input=CaseInput(
        headers={"x-user-id": "u_owner"},
        query_params={"bot_id": "bot_test", "owner_id": "u_owner"}
    ),
    seed=_seed_collaborator,
    expect=ExpectSuccess(
        status=200,
        json_contains={"success": True},
    ),
    extra_assertions=(_assert_list_contains_collaborator, _assert_collaborator_list_fields),
)
def list_collaborators_ok():
    """List collaborators returns success with correct collaborator data and all required fields."""


@endpoint_test(
    method="GET",
    path="/api/bot/collaborator/list",
    scenario="empty",
    input=CaseInput(
        headers={"x-user-id": "u_owner"},
        query_params={"bot_id": "bot_empty", "owner_id": "u_owner"}
    ),
    seed=_seed_bot_empty,
    expect=ExpectSuccess(
        status=200,
        json_contains={"success": True, "data": {"collaborators": []}},
    ),
)
def list_collaborators_empty():
    """List collaborators with no collaborators returns empty list."""


@endpoint_test(
    method="GET",
    path="/api/bot/collaborator/list",
    scenario="bot_not_found",
    input=CaseInput(
        headers={"x-user-id": "u_owner"},
        query_params={"bot_id": "bot_notexist", "owner_id": "u_owner"}
    ),
    seed=lambda world: make_staff_user(world, user_id="u_owner"),
    expect=ExpectError(
        status=200,
        json_contains={"success": False, "error_code": 404},
    ),
)
def list_collaborators_bot_not_found():
    """List collaborators for non-existent bot returns 404."""


# ============================================================================
# POST /api/bot/collaborator/update
# ============================================================================


@endpoint_test(
    method="POST",
    path="/api/bot/collaborator/update",
    scenario="ok",
    input=CaseInput(
        headers={"x-user-id": "u_owner"},
        json_body={
            "id": 1,  # Will be overridden by seed
            "role": "admin",
        }
    ),
    seed=_seed_collaborator,
    expect=ExpectSuccess(
        status=200,
        json_contains={"success": True},
    ),
    extra_assertions=(_assert_collaborator_updated,),
)
def update_collaborator_ok():
    """Update collaborator role returns success with updated data."""


@endpoint_test(
    method="POST",
    path="/api/bot/collaborator/update",
    scenario="not_found",
    input=CaseInput(
        headers={"x-user-id": "u_owner"},
        json_body={
            "id": 99999,
            "role": "admin",
        }
    ),
    seed=_seed_bot_owner,
    expect=ExpectError(
        status=200,
        json_contains={"success": False, "error_code": 404},
    ),
)
def update_collaborator_not_found():
    """Update non-existent collaborator returns 404."""


# ============================================================================
# POST /api/bot/collaborator/remove
# ============================================================================


@endpoint_test(
    method="POST",
    path="/api/bot/collaborator/remove",
    scenario="ok",
    input=CaseInput(
        headers={"x-user-id": "u_owner"},
        json_body={"id": 1}  # Will be overridden by seed
    ),
    seed=_seed_collaborator,
    expect=ExpectSuccess(
        status=200,
        json_contains={"success": True, "data": {"deleted": True}},
    ),
)
def remove_collaborator_ok():
    """Remove collaborator returns success with deleted=True."""


@endpoint_test(
    method="POST",
    path="/api/bot/collaborator/remove",
    scenario="not_found",
    input=CaseInput(
        headers={"x-user-id": "u_owner"},
        json_body={"id": 99999}
    ),
    seed=_seed_bot_owner,
    expect=ExpectError(
        status=200,
        json_contains={"success": False, "error_code": 404},
    ),
)
def remove_collaborator_not_found():
    """Remove non-existent collaborator returns 404."""


def _assert_current_user_left(response, world):
    """Assert current collaborator was removed after leaving collaboration."""
    from agentclaw.community.core.repository.protocols.bot import CollaboratorRepositoryProtocol
    from agentclaw.community.core.repository.protocols.bot import BotRepository
    from agentclaw.community.utils.env_utils import get_current_env

    bot_repo = world.get(BotRepository)
    bot = bot_repo.get_by_id_and_owner("bot_test", "u_owner")
    assert bot is not None

    collab_repo = world.get(CollaboratorRepositoryProtocol)
    record = collab_repo.get_by_bot_and_user(bot["id"], "u_collab", get_current_env())
    assert record is None, "Expected u_collab collaborator record to be deleted"


# ============================================================================
# POST /api/bot/collaborator/leave
# ============================================================================


@endpoint_test(
    method="POST",
    path="/api/bot/collaborator/leave",
    scenario="ok",
    input=CaseInput(
        headers={"x-user-id": "u_collab"},
        json_body={"bot_id": "bot_test", "owner_id": "u_owner"},
    ),
    seed=_seed_collaborator,
    expect=ExpectSuccess(
        status=200,
        json_contains={"success": True, "data": {"deleted": True}},
    ),
    extra_assertions=(_assert_current_user_left,),
)
def leave_collaboration_ok():
    """Collaborator can leave collaboration by removing their own record."""


@endpoint_test(
    method="POST",
    path="/api/bot/collaborator/leave",
    scenario="not_collaborator",
    input=CaseInput(
        headers={"x-user-id": "u_other"},
        json_body={"bot_id": "bot_test", "owner_id": "u_owner"},
    ),
    seed=_seed_collaborator,
    expect=ExpectError(
        status=200,
        json_contains={"success": False, "error_code": 404},
    ),
)
def leave_collaboration_not_collaborator():
    """Non-collaborator cannot leave a collaboration they are not in."""


@endpoint_test(
    method="POST",
    path="/api/bot/collaborator/leave",
    scenario="owner",
    input=CaseInput(
        headers={"x-user-id": "u_owner"},
        json_body={"bot_id": "bot_test", "owner_id": "u_owner"},
    ),
    seed=_seed_collaborator,
    expect=ExpectError(
        status=200,
        json_contains={"success": False, "error_code": 404},
    ),
)
def leave_collaboration_owner():
    """Owner is not a collaborator record and cannot use leave endpoint."""


# ============================================================================
# POST /api/bot/collaborator/check_permission
# ============================================================================


@endpoint_test(
    method="POST",
    path="/api/bot/collaborator/check_permission",
    scenario="ok",
    input=CaseInput(
        headers={"x-user-id": "u_owner"},
        json_body={
            "bot_id": "bot_test",
            "owner_id": "u_owner",
            "user_id": "u_owner",
            "required_level": "OWNER",
        }
    ),
    seed=_seed_bot_owner,
    expect=ExpectSuccess(
        status=200,
        json_contains={"success": True, "data": {"has_permission": True, "level": "OWNER"}},
    ),
    extra_assertions=(_assert_permission_check_fields,),
)
def check_permission_owner():
    """Owner permission check returns success with OWNER level and all fields."""


@endpoint_test(
    method="POST",
    path="/api/bot/collaborator/check_permission",
    scenario="member_has_permission",
    input=CaseInput(
        headers={"x-user-id": "u_owner"},
        json_body={
            "bot_id": "bot_test",
            "owner_id": "u_owner",
            "user_id": "u_collab",
            "required_level": "MEMBER",
        }
    ),
    seed=_seed_collaborator,
    expect=ExpectSuccess(
        status=200,
        json_contains={"success": True, "data": {"has_permission": True}},
    ),
    extra_assertions=(_assert_permission_check_fields,),
)
def check_permission_member():
    """Member permission check for MEMBER level returns success with all fields."""


@endpoint_test(
    method="POST",
    path="/api/bot/collaborator/check_permission",
    scenario="bot_not_found",
    input=CaseInput(
        headers={"x-user-id": "u_owner"},
        json_body={
            "bot_id": "bot_notexist",
            "owner_id": "u_owner",
            "user_id": "u_owner",
            "required_level": "OWNER",
        }
    ),
    seed=lambda world: make_staff_user(world, user_id="u_owner"),
    expect=ExpectError(
        status=200,
        json_contains={"success": False, "error_code": 404},
    ),
)
def check_permission_bot_not_found():
    """Check permission for non-existent bot returns 404."""


# ============================================================================
# POST /api/bot/collaborator/lock/acquire
# ============================================================================


@endpoint_test(
    method="POST",
    path="/api/bot/collaborator/lock/acquire",
    scenario="ok",
    input=CaseInput(
        headers={"x-user-id": "u_owner"},
        json_body={
            "bot_id": "bot_test",
            "owner_id": "u_owner",
        }
    ),
    seed=_seed_bot_with_collab_for_lock,
    expect=ExpectSuccess(
        status=200,
        json_contains={"success": True, "data": {"acquired": True}},
    ),
    extra_assertions=(_assert_lock_info_valid, _assert_lock_exists),
)
def acquire_lock_ok():
    """Acquire lock returns success with valid lock info and creates lock in DB."""


@endpoint_test(
    method="POST",
    path="/api/bot/collaborator/lock/acquire",
    scenario="reentrant",
    input=CaseInput(
        headers={"x-user-id": "u_owner"},
        json_body={
            "bot_id": "bot_test",
            "owner_id": "u_owner",
        }
    ),
    seed=_seed_bot_with_lock,  # Already has lock held by u_owner
    expect=ExpectSuccess(
        status=200,
        json_contains={"success": True, "data": {"acquired": True}},
    ),
    extra_assertions=(_assert_lock_holder_is("u_owner"),),
)
def acquire_lock_reentrant():
    """Acquire lock when already held by same user returns success (reentrant) with correct holder."""


@endpoint_test(
    method="POST",
    path="/api/bot/collaborator/lock/acquire",
    scenario="held_by_other",
    input=CaseInput(
        headers={"x-user-id": "u_other"},  # Different user
        json_body={
            "bot_id": "bot_test",
            "owner_id": "u_owner",
        }
    ),
    seed=_seed_bot_with_lock,  # Lock held by u_owner
    expect=ExpectSuccess(
        status=200,
        json_contains={"success": True, "data": {"acquired": False}},
    ),
    extra_assertions=(_assert_lock_held_by_other,),
)
def acquire_lock_held_by_other():
    """Acquire lock when held by another user returns acquired=False and lock unchanged."""


@endpoint_test(
    method="POST",
    path="/api/bot/collaborator/lock/acquire",
    scenario="anonymous_user",
    input=CaseInput(
        headers={"x-user-id": "anonymous"},
        json_body={
            "bot_id": "bot_test",
            "owner_id": "u_owner",
        }
    ),
    seed=_seed_bot_owner,
    expect=ExpectError(
        status=200,
        json_contains={"success": False, "error_code": 400},
    ),
)
def acquire_lock_anonymous():
    """Acquire lock with anonymous user returns 400."""


# ============================================================================
# POST /api/bot/collaborator/lock/release
# ============================================================================


@endpoint_test(
    method="POST",
    path="/api/bot/collaborator/lock/release",
    scenario="not_locked",
    input=CaseInput(
        headers={"x-user-id": "u_owner"},
        json_body={
            "bot_id": "bot_test",
            "owner_id": "u_owner",
            "force": False,
        }
    ),
    seed=_seed_bot_owner,
    expect=ExpectError(
        status=200,
        json_contains={"success": False, "error_code": 404},
    ),
)
def release_lock_not_locked():
    """Release lock when no lock exists returns error."""


@endpoint_test(
    method="POST",
    path="/api/bot/collaborator/lock/release",
    scenario="ok",
    input=CaseInput(
        headers={"x-user-id": "u_owner"},
        json_body={
            "bot_id": "bot_test",
            "owner_id": "u_owner",
            "force": False,
        }
    ),
    seed=_seed_bot_with_lock,
    expect=ExpectSuccess(
        status=200,
        json_contains={"success": True, "data": {"released": True}},
    ),
    extra_assertions=(_assert_lock_released,),
)
def release_lock_ok():
    """Release lock after acquiring returns success and lock is removed from DB."""


@endpoint_test(
    method="POST",
    path="/api/bot/collaborator/lock/release",
    scenario="force_release",
    input=CaseInput(
        headers={"x-user-id": "u_other"},  # Different user
        json_body={
            "bot_id": "bot_test",
            "owner_id": "u_owner",
            "force": True,  # Force release
        }
    ),
    seed=_seed_bot_with_lock,
    expect=ExpectSuccess(
        status=200,
        json_contains={"success": True, "data": {"released": True}},
    ),
    extra_assertions=(_assert_lock_released,),
)
def release_lock_force():
    """Force release lock by another user succeeds and removes lock from DB."""


@endpoint_test(
    method="POST",
    path="/api/bot/collaborator/lock/release",
    scenario="denied_not_holder",
    input=CaseInput(
        headers={"x-user-id": "u_other"},  # Different user, not force
        json_body={
            "bot_id": "bot_test",
            "owner_id": "u_owner",
            "force": False,
        }
    ),
    seed=_seed_bot_with_lock,
    expect=ExpectError(
        status=200,
        json_contains={"success": False, "error_code": 403},
    ),
)
def release_lock_denied_not_holder():
    """Release lock by non-holder without force flag returns 403."""


# ============================================================================
# GET /api/bot/collaborator/lock/info
# ============================================================================


@endpoint_test(
    method="GET",
    path="/api/bot/collaborator/lock/info",
    scenario="no_lock",
    input=CaseInput(
        headers={"x-user-id": "u_owner"},
        query_params={"bot_id": "bot_test", "owner_id": "u_owner"}
    ),
    seed=_seed_bot_owner,
    expect=ExpectSuccess(
        status=200,
        json_contains={"success": True, "data": {"locked": False, "lock": None}},
    ),
)
def get_lock_info_no_lock():
    """Get lock info when no lock exists returns unlocked status with lock=None."""


@endpoint_test(
    method="GET",
    path="/api/bot/collaborator/lock/info",
    scenario="locked",
    input=CaseInput(
        headers={"x-user-id": "u_owner"},
        query_params={"bot_id": "bot_test", "owner_id": "u_owner"}
    ),
    seed=_seed_bot_with_lock,  # Lock held by u_owner
    expect=ExpectSuccess(
        status=200,
        json_contains={"success": True, "data": {"locked": True}},
    ),
    extra_assertions=(_assert_lock_holder_is("u_owner"),),
)
def get_lock_info_locked():
    """Get lock info when lock exists returns locked status with correct holder."""


@endpoint_test(
    method="GET",
    path="/api/bot/collaborator/lock/info",
    scenario="anonymous_user",
    input=CaseInput(
        headers={"x-user-id": "anonymous"},
        query_params={"bot_id": "bot_test", "owner_id": "u_owner"}
    ),
    seed=_seed_bot_owner,
    expect=ExpectError(
        status=200,
        json_contains={"success": False, "error_code": 400},
    ),
)
def get_lock_info_anonymous():
    """Get lock info with anonymous user returns 400."""


# ============================================================================
# GET /api/bot/collaborator/lock/info - holder_name tests
# ============================================================================


def _seed_bot_with_collaborator_lock(world):
    """Seed bot, collaborator, and lock held by collaborator."""
    make_staff_user(world, user_id="u_owner")
    make_staff_user(world, user_id="u_collab")
    make_bot(world, bot_id="bot_test", owner_id="u_owner", bot_type="service", status="ACTIVE")
    make_collaborator(
        world,
        bot_id="bot_test",
        owner_id="u_owner",
        user_id="u_collab",
        user_name="Collaborator Name",
        role="admin",
        operator_id="u_owner",
    )
    # Acquire lock as collaborator
    lock_service = world.get(CollaboratorLockService)
    lock_service.acquire_lock("bot_test", "u_owner", "u_collab")


def _assert_holder_name_is(expected_name):
    """Assert that lock holder_name is expected value."""
    def _assert(response, world):
        data = response.json()["data"]
        lock = data.get("lock")
        assert lock is not None, "Expected lock to be present"
        assert lock.get("holder_name") == expected_name, (
            f"Expected holder_name={expected_name}, got {lock.get('holder_name')}"
        )
    return _assert


def _assert_holder_name_is_none(response, world):
    """Assert that lock holder_name is None (for owner or not found collaborator)."""
    data = response.json()["data"]
    lock = data.get("lock")
    assert lock is not None, "Expected lock to be present"
    assert lock.get("holder_name") is None, (
        f"Expected holder_name=None, got {lock.get('holder_name')}"
    )


@endpoint_test(
    method="GET",
    path="/api/bot/collaborator/lock/info",
    scenario="holder_is_owner",
    input=CaseInput(
        headers={"x-user-id": "u_owner"},
        query_params={"bot_id": "bot_test", "owner_id": "u_owner"}
    ),
    seed=_seed_bot_with_lock,  # Lock held by u_owner (the owner)
    expect=ExpectSuccess(
        status=200,
        json_contains={"success": True, "data": {"locked": True}},
    ),
    extra_assertions=(_assert_lock_holder_is("u_owner"), _assert_holder_name_is("Owner Name")),
)
def get_lock_info_holder_is_owner():
    """When lock holder is owner, holder_name should be owner_name from bot."""


@endpoint_test(
    method="GET",
    path="/api/bot/collaborator/lock/info",
    scenario="holder_is_collaborator",
    input=CaseInput(
        headers={"x-user-id": "u_owner"},
        query_params={"bot_id": "bot_test", "owner_id": "u_owner"}
    ),
    seed=_seed_bot_with_collaborator_lock,  # Lock held by collaborator with user_name
    expect=ExpectSuccess(
        status=200,
        json_contains={"success": True, "data": {"locked": True}},
    ),
    extra_assertions=(_assert_lock_holder_is("u_collab"), _assert_holder_name_is("Collaborator Name")),
)
def get_lock_info_holder_is_collaborator():
    """When lock holder is collaborator, holder_name should be their user_name."""


def _seed_bot_with_lock_non_collaborator(world):
    """Seed bot with collaborator, and lock held by a user who is NOT a collaborator."""
    make_staff_user(world, user_id="u_owner")
    make_staff_user(world, user_id="u_stranger")  # Not a collaborator
    make_bot(world, bot_id="bot_test", owner_id="u_owner", bot_type="service", status="ACTIVE")
    # Add a collaborator (not the lock holder)
    make_collaborator(
        world,
        bot_id="bot_test",
        owner_id="u_owner",
        user_id="u_collab",
        role="admin",
        operator_id="u_owner",
    )
    # Acquire lock as stranger (directly via service, bypassing permission check)
    lock_service = world.get(CollaboratorLockService)
    lock_service.acquire_lock("bot_test", "u_owner", "u_stranger")


@endpoint_test(
    method="GET",
    path="/api/bot/collaborator/lock/info",
    scenario="holder_not_collaborator",
    input=CaseInput(
        headers={"x-user-id": "u_owner"},
        query_params={"bot_id": "bot_test", "owner_id": "u_owner"}
    ),
    seed=_seed_bot_with_lock_non_collaborator,  # Lock held by user not in collaborator list
    expect=ExpectSuccess(
        status=200,
        json_contains={"success": True, "data": {"locked": True}},
    ),
    extra_assertions=(_assert_lock_holder_is("u_stranger"), _assert_holder_name_is_none),
)
def get_lock_info_holder_not_collaborator():
    """When lock holder is not in collaborator list, holder_name should be None."""


def _seed_bot_with_lock_no_owner_name(world):
    """Seed bot without owner_name and lock held by owner."""
    make_staff_user(world, user_id="u_owner")
    make_bot(world, bot_id="bot_test", owner_id="u_owner", owner_name=None, bot_type="service", status="ACTIVE")
    # Add a collaborator so lock info will be queried
    make_collaborator(
        world,
        bot_id="bot_test",
        owner_id="u_owner",
        user_id="u_collab",
        role="admin",
        operator_id="u_owner",
    )
    # Acquire lock as owner
    lock_service = world.get(CollaboratorLockService)
    lock_service.acquire_lock("bot_test", "u_owner", "u_owner")


@endpoint_test(
    method="GET",
    path="/api/bot/collaborator/lock/info",
    scenario="holder_is_owner_no_owner_name",
    input=CaseInput(
        headers={"x-user-id": "u_owner"},
        query_params={"bot_id": "bot_test", "owner_id": "u_owner"}
    ),
    seed=_seed_bot_with_lock_no_owner_name,  # Lock held by owner, bot has no owner_name
    expect=ExpectSuccess(
        status=200,
        json_contains={"success": True, "data": {"locked": True}},
    ),
    extra_assertions=(_assert_lock_holder_is("u_owner"), _assert_holder_name_is_none),
)
def get_lock_info_holder_is_owner_no_owner_name():
    """When lock holder is owner but bot has no owner_name, holder_name should be None."""


# ============================================================================
# POST /api/bot/collaborator/lock/steal
# ============================================================================


def _seed_bot_with_lock_for_steal(world):
    """Seed bot with collaborator and lock held by owner for steal test."""
    make_staff_user(world, user_id="u_owner")
    make_staff_user(world, user_id="u_admin")
    make_staff_user(world, user_id="u_member")
    make_bot(world, bot_id="bot_steal", owner_id="u_owner", bot_type="service", status="ACTIVE")
    # Add admin collaborator
    make_collaborator(
        world,
        bot_id="bot_steal",
        owner_id="u_owner",
        user_id="u_admin",
        role="admin",
        operator_id="u_owner",
    )
    # Add member collaborator
    make_collaborator(
        world,
        bot_id="bot_steal",
        owner_id="u_owner",
        user_id="u_member",
        role="member",
        operator_id="u_owner",
    )
    # Acquire lock as owner
    lock_service = world.get(CollaboratorLockService)
    lock_service.acquire_lock("bot_steal", "u_owner", "u_owner")


@endpoint_test(
    method="POST",
    path="/api/bot/collaborator/lock/steal",
    scenario="admin_steal_from_owner",
    input=CaseInput(
        headers={"x-user-id": "u_admin"},  # Admin steals from owner
        json_body={
            "bot_id": "bot_steal",
            "owner_id": "u_owner",
        }
    ),
    seed=_seed_bot_with_lock_for_steal,
    expect=ExpectSuccess(
        status=200,
        json_contains={"success": True, "data": {"stolen": True}},
    ),
)
def steal_lock_admin_success():
    """Admin collaborator can steal lock from owner."""


@endpoint_test(
    method="POST",
    path="/api/bot/collaborator/lock/steal",
    scenario="member_denied",
    input=CaseInput(
        headers={"x-user-id": "u_member"},  # Member tries to steal
        json_body={
            "bot_id": "bot_steal",
            "owner_id": "u_owner",
        }
    ),
    seed=_seed_bot_with_lock_for_steal,
    expect=ExpectError(
        status=403,
        json_contains={"success": False, "error_code": 403},
    ),
)
def steal_lock_member_denied():
    """Member collaborator cannot steal lock (needs ADMIN permission)."""

# Extra leave endpoint error branches for changed-line coverage.


def _seed_leave_service_bot_not_found(world):
    from agentclaw.community.api.collaborator_service import CollaboratorServiceProtocol
    from agentclaw.community.core.bot_collaborator.services.collaborator_service import BotNotFoundError

    class _StubCollaboratorService:
        def leave_collaboration(self, *args, **kwargs):
            raise BotNotFoundError("Bot 不存在: bot_id=missing, owner_id=u_owner")

    world.injector.binder.bind(CollaboratorServiceProtocol, to=_StubCollaboratorService, scope=None)


@endpoint_test(
    method="POST",
    path="/api/bot/collaborator/leave",
    scenario="anonymous",
    input=CaseInput(
        headers={"x-user-id": "anonymous"},
        json_body={"bot_id": "bot_test", "owner_id": "u_owner"},
    ),
    seed=_seed_collaborator,
    expect=ExpectError(
        status=200,
        json_contains={"success": False, "error_code": 400},
    ),
)
def leave_collaboration_anonymous():
    """Anonymous user cannot leave collaboration."""


@endpoint_test(
    method="POST",
    path="/api/bot/collaborator/leave",
    scenario="bot_not_found",
    input=CaseInput(
        headers={"x-user-id": "u_collab"},
        json_body={"bot_id": "missing", "owner_id": "u_owner"},
    ),
    seed=_seed_leave_service_bot_not_found,
    expect=ExpectError(
        status=200,
        json_contains={"success": False, "error_code": 404},
    ),
)
def leave_collaboration_bot_not_found():
    """BotNotFoundError is mapped to 404 envelope."""


def _seed_leave_service_error(world):
    from agentclaw.community.api.collaborator_service import CollaboratorServiceProtocol
    from agentclaw.community.core.bot_collaborator.services.collaborator_service import CollaboratorServiceError

    class _StubCollaboratorService:
        def leave_collaboration(self, *args, **kwargs):
            raise CollaboratorServiceError("repository unavailable")

    world.injector.binder.bind(CollaboratorServiceProtocol, to=_StubCollaboratorService, scope=None)


@endpoint_test(
    method="POST",
    path="/api/bot/collaborator/leave",
    scenario="service_error",
    input=CaseInput(
        headers={"x-user-id": "u_collab"},
        json_body={"bot_id": "bot_test", "owner_id": "u_owner"},
    ),
    seed=_seed_leave_service_error,
    expect=ExpectError(
        status=200,
        json_contains={"success": False, "error_code": 500},
    ),
)
def leave_collaboration_service_error():
    """CollaboratorServiceError is mapped to 500 envelope."""


def _seed_leave_unexpected_error(world):
    from agentclaw.community.api.collaborator_service import CollaboratorServiceProtocol

    class _StubCollaboratorService:
        def leave_collaboration(self, *args, **kwargs):
            raise RuntimeError("boom")

    world.injector.binder.bind(CollaboratorServiceProtocol, to=_StubCollaboratorService, scope=None)


@endpoint_test(
    method="POST",
    path="/api/bot/collaborator/leave",
    scenario="unexpected_error",
    input=CaseInput(
        headers={"x-user-id": "u_collab"},
        json_body={"bot_id": "bot_test", "owner_id": "u_owner"},
    ),
    seed=_seed_leave_unexpected_error,
    expect=ExpectError(
        status=200,
        json_contains={"success": False, "error_code": 500},
    ),
)
def leave_collaboration_unexpected_error():
    """Unexpected exceptions are mapped to 500 envelope."""
