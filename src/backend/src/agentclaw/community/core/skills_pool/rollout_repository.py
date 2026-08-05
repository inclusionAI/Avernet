"""Persistence boundary for atomic rollout config and append-only audit."""

from typing import Protocol, runtime_checkable


@runtime_checkable
class SkillsPoolRolloutRepositoryProtocol(Protocol):
    def commit_change(
        self,
        *,
        env: str,
        config_id: int | None,
        expected_revision: str | None,
        expected_enable: bool,
        expected_value: dict[str, object],
        next_revision: str,
        enabled: bool,
        value: dict[str, object],
        audit: dict[str, object],
    ) -> bool: ...

    def list_audit_events(self, *, env: str) -> list[dict[str, object]]: ...
