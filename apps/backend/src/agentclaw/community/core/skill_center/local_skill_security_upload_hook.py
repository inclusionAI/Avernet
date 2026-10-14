"""Consumer contract for optional post-success Local Skill observation.

The hook runs after the formal upload commits. ``cancelled`` is set when the
bounded handoff expires; implementations must not select a current observation
after seeing it. A late immutable snapshot may remain as an orphan for cleanup.
"""

from __future__ import annotations

from threading import Event
from typing import Protocol, runtime_checkable


@runtime_checkable
class LocalSkillSecurityUploadHook(Protocol):
    def record_deletion(
        self, *, bot_id: str, owner_id: str, actor_id: str, skill_id: int,
        incarnation_id: str | None,
    ) -> None:
        """Record a successful formal delete for independent metadata sync."""
        ...

    def current_incarnation_id(
        self, *, bot_id: str, owner_id: str, skill_id: int
    ) -> str | None:
        """Read or bind the formal asset's UUID after upload succeeds."""
        ...

    def observe(
        self,
        *,
        bot_id: str,
        owner_id: str,
        actor_id: str,
        skill_id: int,
        operation: str,
        name: str,
        description: str,
        canonical_zip: bytes,
        observation_id: str,
        incarnation_id: str,
        cancelled: Event,
    ) -> None:
        """Store this exact upload or stop before publishing when cancelled."""
        ...

    def record_handoff_timeout(
        self,
        *,
        bot_id: str,
        owner_id: str,
        actor_id: str,
        skill_id: int,
        observation_id: str,
        digest: str,
        incarnation_id: str,
        name: str,
        description: str,
        creator_id: str | None,
    ) -> None:
        """Record an abandoned attempt; failure of this call is log-only."""
        ...


__all__ = ["LocalSkillSecurityUploadHook"]
