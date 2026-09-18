"""Service API for Engine-scoped Skills Pool admission policy."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from agentclaw.community.core.skills_pool.operation_models import (
    RolloutConfigSnapshot,
)


@runtime_checkable
class SkillsPoolRolloutServiceProtocol(Protocol):
    def get_snapshot(self, *, env: str) -> RolloutConfigSnapshot: ...

    def set_feature_enabled(
        self,
        *,
        env: str,
        enabled: bool,
        expected_revision: str | None,
        operator: str,
        reason: str,
    ) -> RolloutConfigSnapshot: ...

    def set_engine_admission(
        self,
        *,
        env: str,
        engine: str,
        enabled: bool,
        expected_revision: str | None,
        operator: str,
        reason: str,
    ) -> RolloutConfigSnapshot: ...

    def set_environment_rollout(
        self,
        *,
        env: str,
        engine: str,
        enabled: bool,
        expected_revision: str | None,
        operator: str,
        reason: str,
    ) -> RolloutConfigSnapshot: ...

    def set_owner_rollout(
        self,
        *,
        env: str,
        owner_id: str,
        engine: str,
        enabled: bool,
        expected_revision: str | None,
        operator: str,
        reason: str,
    ) -> RolloutConfigSnapshot: ...

    def set_bot_allow(
        self,
        *,
        env: str,
        owner_id: str,
        bot_id: str,
        engine: str,
        present: bool,
        expected_revision: str | None,
        operator: str,
        reason: str,
    ) -> RolloutConfigSnapshot: ...

    def set_bot_exclusion(
        self,
        *,
        env: str,
        owner_id: str,
        bot_id: str,
        engine: str,
        present: bool,
        expected_revision: str | None,
        operator: str,
        reason: str,
    ) -> RolloutConfigSnapshot: ...
