"""Plugin API for durable cleanup of obsolete Bot-owned Local Skill packages."""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class LocalSkillCleanupRepository(Protocol):
    """Persist and progress retryable cleanup work within one exact Bot scope."""

    def record_pending(
        self,
        *,
        env: str,
        owner_id: str,
        bot_id: str,
        skill_id: str,
        package_locator: str,
        requires_runtime_restore: bool,
    ) -> int | None: ...

    def list_pending(self, *, env: str, owner_id: str, bot_id: str) -> list[dict]: ...

    def mark_cleaned(
        self, *, work_id: int, env: str, owner_id: str, bot_id: str
    ) -> bool: ...

    def mark_failed(
        self, *, work_id: int, env: str, owner_id: str, bot_id: str, error: str
    ) -> bool: ...

    def cancel_pending(
        self, *, work_id: int, env: str, owner_id: str, bot_id: str
    ) -> bool: ...
