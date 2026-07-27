"""Service API Protocol for Skills Pool rollout control."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from agentclaw.community.core.skills_pool.operations import (
    BatchPromotionEvidence,
    RolloutConfigSnapshot,
    RolloutControlGroup,
    WhitelistMutationResult,
)


@runtime_checkable
class SkillsPoolRolloutServiceProtocol(Protocol):
    def get_snapshot(self, *, env: str) -> RolloutConfigSnapshot: ...

    def set_feature_enabled(
        self,
        *,
        env: str,
        enabled: bool,
        operator: str,
        reason: str,
    ) -> RolloutConfigSnapshot: ...

    def set_full_rollout(
        self,
        *,
        env: str,
        enabled: bool,
        engine: str | None = None,
        operator: str,
        reason: str,
    ) -> RolloutConfigSnapshot: ...

    def promote_engine(
        self,
        *,
        env: str,
        engine: str,
        operator: str,
        reason: str,
        acceptance_batch_id: str | None = None,
    ) -> RolloutConfigSnapshot: ...

    def accept_batch(
        self,
        *,
        env: str,
        acceptance: BatchPromotionEvidence,
        operator: str,
        reason: str,
    ) -> RolloutConfigSnapshot: ...

    def add_bot(
        self,
        *,
        env: str,
        owner_id: str,
        bot_id: str,
        batch_id: str,
        acceptance_batch_id: str | None,
        operator: str,
        reason: str,
    ) -> WhitelistMutationResult: ...

    def remove_bot(
        self,
        *,
        env: str,
        owner_id: str,
        bot_id: str,
        operator: str,
        reason: str,
    ) -> WhitelistMutationResult: ...

    def set_control_bot(
        self,
        *,
        env: str,
        owner_id: str,
        bot_id: str,
        batch_id: str,
        group: RolloutControlGroup,
        present: bool,
        operator: str,
        reason: str,
    ) -> RolloutConfigSnapshot: ...
