"""Unit tests for the access-domain factory helpers.

Reuses the framework's per-test injector + in-memory DB fixtures so
each test starts with an empty users table.
"""
from __future__ import annotations

from agentclaw.community.core.access.services.user_service import UserService
from tests.community.factories.access import make_staff_user


def test_seeded_user_is_retrievable_via_service(world) -> None:
    """The factory's contract: after it runs, the user is fetchable via
    the real ``UserService`` — proving it goes through the real write
    path, not a back-door INSERT.
    """
    rec = make_staff_user(world, user_id="u_smoke")
    assert rec.user_id == "u_smoke"
    assert rec.user_type == "staff"
    assert rec.status == "active"

    svc = world.get(UserService)
    fetched = svc.get_user(user_id="u_smoke", user_type="staff")
    assert fetched.user_id == rec.user_id


def test_factory_is_idempotent(world) -> None:
    """Two calls with the same id must not raise; status from the
    second call overwrites the first (upsert semantics inherited
    from UserService.upsert_user).
    """
    make_staff_user(world, user_id="u_repeat", status="active")
    second = make_staff_user(world, user_id="u_repeat", status="suspended")
    assert second.status == "suspended"


def test_factory_status_override(world) -> None:
    rec = make_staff_user(world, user_id="u_status", status="suspended")
    assert rec.status == "suspended"


def test_isolation_across_cases_first(world) -> None:
    """Pair with the next test: seeding here, asserting cleanness next."""
    make_staff_user(world, user_id="u_isolated", status="active")
    svc = world.get(UserService)
    assert svc.get_user(user_id="u_isolated", user_type="staff").user_id == "u_isolated"


def test_isolation_across_cases_second(world) -> None:
    """Per-test fresh injector means the previous case's row is gone."""
    from agentclaw.community.core.access.errors import UserNotFoundError
    import pytest

    svc = world.get(UserService)
    with pytest.raises(UserNotFoundError):
        svc.get_user(user_id="u_isolated", user_type="staff")
