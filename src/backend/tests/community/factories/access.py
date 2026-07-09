"""Seed helpers for the ``access`` domain.

Wraps the real ``UserService`` so seeded users go through the same
write path the production code uses — domain invariants (FK
constraints, status validation, etc.) are exercised as in real boots.
"""
from __future__ import annotations

from agentclaw.community.core.access.models import UserInfoRecord
from agentclaw.community.core.access.services.user_service import UserService
from tests.community.framework.world import World


def make_staff_user(
    world: World,
    *,
    user_id: str = "u_smoke",
    status: str = "active",
) -> UserInfoRecord:
    """Seed a ``user_type="staff"`` user and return the resulting record.

    Idempotent: ``UserService.upsert_user`` is ``ON DUPLICATE KEY
    UPDATE status`` semantics, so calling this twice with the same
    ``user_id`` does not raise — the second call just refreshes
    ``status`` to whatever value was passed.

    Parameters mirror the canonical fields the policy repository
    requires. Other domain attributes (display name, etc.) are not
    surfaced by ``UserService`` and are therefore not exposed here;
    add them when a real test needs them.
    """
    svc = world.get(UserService)
    svc.upsert_user(user_id=user_id, user_type="staff", status=status)
    return svc.get_user(user_id=user_id, user_type="staff")
