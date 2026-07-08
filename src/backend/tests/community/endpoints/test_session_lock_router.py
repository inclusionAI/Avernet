"""Endpoint tests for session-lock (coding 应用会话级锁) endpoints.

Tests the following endpoints from ``adapters/http/bot_collaborator/router.py``:
- POST /api/bot/collaborator/session-lock/acquire
- POST /api/bot/collaborator/session-lock/release
- POST /api/bot/collaborator/session-lock/steal
- GET  /api/bot/collaborator/session-lock/info

Session-lock endpoints do NOT mount CollaboratorPermissionInterceptor (the
design uses front-end + engine chat.send enforcement instead), so the seed
only needs a bot and users — no collaborator record required for these
endpoints to function.
"""
from __future__ import annotations

from agentclaw.community.core.bot_collaborator.services.collaborator_lock_service import CollaboratorLockService
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


def _seed_bot_for_session_lock(world):
    """Seed a bot and users for session-lock tests.

    Uses bot_type="service" so make_collaborator would work if needed.
    Session-lock endpoints don't require collaborators; we just need the
    bot and lock service to be functional.
    """
    make_staff_user(world, user_id="u_owner")
    make_staff_user(world, user_id="u_member")
    make_bot(
        world,
        bot_id="app_test",
        owner_id="u_owner",
        owner_name="Owner Name",
        bot_type="service",
        status="ACTIVE",
    )


def _seed_bot_with_session_lock(world):
    """Seed a bot with an acquired session lock (held by owner)."""
    _seed_bot_for_session_lock(world)
    lock_service = world.get(CollaboratorLockService)
    lock_service.acquire_session_lock("app_test", "u_owner", "sess_001", "u_owner")


def _seed_bot_with_session_lock_by_member(world):
    """Seed a bot with session lock held by a non-owner user."""
    _seed_bot_for_session_lock(world)
    lock_service = world.get(CollaboratorLockService)
    lock_service.acquire_session_lock("app_test", "u_owner", "sess_001", "u_member")


# ============================================================================
# Extra Assertions for Session Lock API
# ============================================================================


def _assert_session_lock_acquired(response, world):
    """Assert that session lock was acquired (acquired=True, lock present)."""
    data = response.json()["data"]
    assert data["acquired"] is True, f"Expected acquired=True, got {data['acquired']}"
    lock = data.get("lock")
    assert lock is not None, "Expected lock to be present when acquired=True"


def _assert_session_lock_not_acquired(response, world):
    """Assert that session lock was NOT acquired (acquired=False, lock=None)."""
    data = response.json()["data"]
    assert data["acquired"] is False, f"Expected acquired=False, got {data['acquired']}"
    assert data.get("lock") is None, f"Expected lock=None when not acquired, got {data['lock']}"


def _assert_session_lock_released(response, world):
    """Assert that session lock was released by checking lock info via service."""
    lock_service = world.get(CollaboratorLockService)
    result = lock_service.get_session_lock_info("app_test", "u_owner", "sess_001", "u_owner")
    assert not result.locked, "Expected session lock to be released"


def _assert_session_lock_holder_is(expected_user_id):
    """Assert session lock is held by expected user via service."""
    def _assert(response, world):
        lock_service = world.get(CollaboratorLockService)
        result = lock_service.get_session_lock_info("app_test", "u_owner", "sess_001", "u_owner")
        assert result.locked, "Expected session lock to be held"
        assert result.lock.holder_user_id == expected_user_id, (
            f"Expected holder={expected_user_id}, got {result.lock.holder_user_id}"
        )
    return _assert


# ============================================================================
# POST /api/bot/collaborator/session-lock/acquire
# ============================================================================


@endpoint_test(
    method="POST",
    path="/api/bot/collaborator/session-lock/acquire",
    scenario="ok",
    input=CaseInput(
        headers={"x-user-id": "u_owner"},
        json_body={
            "bot_id": "app_test",
            "owner_id": "u_owner",
            "session_id": "sess_001",
        },
    ),
    seed=_seed_bot_for_session_lock,
    expect=ExpectSuccess(
        status=200,
        json_contains={"success": True, "data": {"acquired": True}},
    ),
    extra_assertions=(_assert_session_lock_acquired,),
)
def session_lock_acquire_ok():
    """Acquire session lock returns success with acquired=True."""


@endpoint_test(
    method="POST",
    path="/api/bot/collaborator/session-lock/acquire",
    scenario="reentrant",
    input=CaseInput(
        headers={"x-user-id": "u_owner"},
        json_body={
            "bot_id": "app_test",
            "owner_id": "u_owner",
            "session_id": "sess_001",
        },
    ),
    seed=_seed_bot_with_session_lock, # Lock already held by u_owner
    expect=ExpectSuccess(
        status=200,
        json_contains={"success": True, "data": {"acquired": True}},
    ),
    extra_assertions=(_assert_session_lock_holder_is("u_owner"),),
)
def session_lock_acquire_reentrant():
    """Acquire session lock when already held by same user returns acquired=True (reentrant)."""


@endpoint_test(
    method="POST",
    path="/api/bot/collaborator/session-lock/acquire",
    scenario="held_by_other",
    input=CaseInput(
        headers={"x-user-id": "u_member"},  # Different user
        json_body={
            "bot_id": "app_test",
            "owner_id": "u_owner",
            "session_id": "sess_001",
        },
    ),
    seed=_seed_bot_with_session_lock, # Lock held by u_owner
    expect=ExpectSuccess(
        status=200,
        json_contains={"success": True, "data": {"acquired": False}},
    ),
)
def session_lock_acquire_held_by_other():
    """Acquire session lock when held by another user returns acquired=False."""


@endpoint_test(
    method="POST",
    path="/api/bot/collaborator/session-lock/acquire",
    scenario="anonymous_user",
    input=CaseInput(
        headers={"x-user-id": "anonymous"},
        json_body={
            "bot_id": "app_test",
            "owner_id": "u_owner",
            "session_id": "sess_001",
        },
    ),
    seed=_seed_bot_for_session_lock,
    expect=ExpectError(
        status=200,
        json_contains={"success": False, "error_code": 400},
    ),
)
def session_lock_acquire_anonymous():
    """Acquire session lock with anonymous user returns 400."""


# ============================================================================
# POST /api/bot/collaborator/session-lock/release
# ============================================================================


@endpoint_test(
    method="POST",
    path="/api/bot/collaborator/session-lock/release",
    scenario="ok",
    input=CaseInput(
        headers={"x-user-id": "u_owner"},
        json_body={
            "bot_id": "app_test",
            "owner_id": "u_owner",
            "session_id": "sess_001",
            "force": False,
        },
    ),
    seed=_seed_bot_with_session_lock, # Lock held by u_owner
    expect=ExpectSuccess(
        status=200,
        json_contains={"success": True, "data": {"released": True}},
    ),
    extra_assertions=(_assert_session_lock_released,),
)
def session_lock_release_ok():
    """Release session lock by holder returns success and removes lock."""


@endpoint_test(
    method="POST",
    path="/api/bot/collaborator/session-lock/release",
    scenario="not_held",
    input=CaseInput(
        headers={"x-user-id": "u_owner"},
        json_body={
            "bot_id": "app_test",
            "owner_id": "u_owner",
            "session_id": "sess_001",
            "force": False,
        },
    ),
    seed=_seed_bot_for_session_lock,  # No lock held
    expect=ExpectSuccess(
        status=200,
        json_contains={"success": True, "data": {"released": True}},
    ),
)
def session_lock_release_not_held():
    """Release session lock when no lock exists returns released=True (idempotent)."""


@endpoint_test(
    method="POST",
    path="/api/bot/collaborator/session-lock/release",
    scenario="denied_not_holder",
    input=CaseInput(
        headers={"x-user-id": "u_member"},  # Not the lock holder
        json_body={
            "bot_id": "app_test",
            "owner_id": "u_owner",
            "session_id": "sess_001",
            "force": False,
        },
    ),
    seed=_seed_bot_with_session_lock, # Lock held by u_owner
    expect=ExpectError(
        status=200,
        json_contains={"success": False, "error_code": 403},
    ),
)
def session_lock_release_denied_not_holder():
    """Release session lock by non-holder without force returns 403."""


@endpoint_test(
    method="POST",
    path="/api/bot/collaborator/session-lock/release",
    scenario="force_release",
    input=CaseInput(
        headers={"x-user-id": "u_member"},  # Not the lock holder
        json_body={
            "bot_id": "app_test",
            "owner_id": "u_owner",
            "session_id": "sess_001",
            "force": True,
        },
    ),
    seed=_seed_bot_with_session_lock, # Lock held by u_owner
    expect=ExpectSuccess(
        status=200,
        json_contains={"success": True, "data": {"released": True}},
    ),
    extra_assertions=(_assert_session_lock_released,),
)
def session_lock_release_force():
    """Force release session lock by non-holder returns success and removes lock."""


# ============================================================================
# POST /api/bot/collaborator/session-lock/steal
# ============================================================================


@endpoint_test(
    method="POST",
    path="/api/bot/collaborator/session-lock/steal",
    scenario="steal_from_other",
    input=CaseInput(
        headers={"x-user-id": "u_member"},  # Steal from u_owner
        json_body={
            "bot_id": "app_test",
            "owner_id": "u_owner",
            "session_id": "sess_001",
        },
    ),
    seed=_seed_bot_with_session_lock, # Lock held by u_owner
    expect=ExpectSuccess(
        status=200,
        json_contains={"success": True, "data": {"acquired": True}},
    ),
    extra_assertions=(_assert_session_lock_holder_is("u_member"),),
)
def session_lock_steal_from_other():
    """Steal session lock from another user returns acquired=True with new holder."""


@endpoint_test(
    method="POST",
    path="/api/bot/collaborator/session-lock/steal",
    scenario="steal_when_not_held",
    input=CaseInput(
        headers={"x-user-id": "u_owner"},
        json_body={
            "bot_id": "app_test",
            "owner_id": "u_owner",
            "session_id": "sess_001",
        },
    ),
    seed=_seed_bot_for_session_lock,  # No lock held
    expect=ExpectSuccess(
        status=200,
        json_contains={"success": True, "data": {"acquired": True}},
    ),
    extra_assertions=(_assert_session_lock_holder_is("u_owner"),),
)
def session_lock_steal_when_not_held():
    """Steal session lock when no lock exists acquires the lock."""


@endpoint_test(
    method="POST",
    path="/api/bot/collaborator/session-lock/steal",
    scenario="anonymous_user",
    input=CaseInput(
        headers={"x-user-id": "anonymous"},
        json_body={
            "bot_id": "app_test",
            "owner_id": "u_owner",
            "session_id": "sess_001",
        },
    ),
    seed=_seed_bot_for_session_lock,
    expect=ExpectError(
        status=200,
        json_contains={"success": False, "error_code": 400},
    ),
)
def session_lock_steal_anonymous():
    """Steal session lock with anonymous user returns 400."""


# ============================================================================
# GET /api/bot/collaborator/session-lock/info
# ============================================================================


def _assert_session_lock_info_locked(response, world):
    """Assert lock info shows locked=True."""
    data = response.json()["data"]
    assert data["locked"] is True, f"Expected locked=True, got {data['locked']}"
    assert "lock" in data, "Response should contain 'lock' field"


def _assert_session_lock_info_unlocked(response, world):
    """Assert lock info shows locked=False."""
    data = response.json()["data"]
    assert data["locked"] is False, f"Expected locked=False, got {data['locked']}"
    assert data.get("lock") is None, f"Expected lock=None when unlocked, got {data.get('lock')}"


def _assert_session_lock_is_mine(expected: bool):
    """Assert is_mine field in lock info."""
    def _assert(response, world):
        data = response.json()["data"]
        assert data["is_mine"] == expected, f"Expected is_mine={expected}, got {data['is_mine']}"
    return _assert


@endpoint_test(
    method="GET",
    path="/api/bot/collaborator/session-lock/info",
    scenario="not_locked",
    input=CaseInput(
        headers={"x-user-id": "u_owner"},
        query_params={"bot_id": "app_test", "owner_id": "u_owner", "session_id": "sess_001"},
    ),
    seed=_seed_bot_for_session_lock,  # No lock held
    expect=ExpectSuccess(
        status=200,
        json_contains={"success": True, "data": {"locked": False, "is_mine": False}},
    ),
    extra_assertions=(_assert_session_lock_info_unlocked,),
)
def session_lock_info_not_locked():
    """Get session lock info when no lock exists returns unlocked status."""


@endpoint_test(
    method="GET",
    path="/api/bot/collaborator/session-lock/info",
    scenario="locked_is_mine",
    input=CaseInput(
        headers={"x-user-id": "u_owner"},
        query_params={"bot_id": "app_test", "owner_id": "u_owner", "session_id": "sess_001"},
    ),
    seed=_seed_bot_with_session_lock, # Lock held by u_owner
    expect=ExpectSuccess(
        status=200,
        json_contains={"success": True, "data": {"locked": True, "is_mine": True}},
    ),
    extra_assertions=(_assert_session_lock_info_locked, _assert_session_lock_is_mine(True)),
)
def session_lock_info_locked_is_mine():
    """Get session lock info when held by caller returns locked=True, is_mine=True."""


@endpoint_test(
    method="GET",
    path="/api/bot/collaborator/session-lock/info",
    scenario="locked_is_not_mine",
    input=CaseInput(
        headers={"x-user-id": "u_member"},  # Different user
        query_params={"bot_id": "app_test", "owner_id": "u_owner", "session_id": "sess_001"},
    ),
    seed=_seed_bot_with_session_lock, # Lock held by u_owner
    expect=ExpectSuccess(
        status=200,
        json_contains={"success": True, "data": {"locked": True, "is_mine": False}},
    ),
    extra_assertions=(_assert_session_lock_info_locked, _assert_session_lock_is_mine(False)),
)
def session_lock_info_locked_is_not_mine():
    """Get session lock info when held by another returns locked=True, is_mine=False."""


@endpoint_test(
    method="GET",
    path="/api/bot/collaborator/session-lock/info",
    scenario="anonymous_user",
    input=CaseInput(
        headers={"x-user-id": "anonymous"},
        query_params={"bot_id": "app_test", "owner_id": "u_owner", "session_id": "sess_001"},
    ),
    seed=_seed_bot_for_session_lock,
    expect=ExpectError(
        status=200,
        json_contains={"success": False, "error_code": 400},
    ),
)
def session_lock_info_anonymous():
    """Get session lock info with anonymous user returns 400."""