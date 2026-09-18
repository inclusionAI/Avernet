from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from agentclaw.community.core.skills_pool.native_confirmation import (
    PoolNativeLayoutConfirmationError,
    SkillsPoolNativeLayoutConfirmationService,
)
from agentclaw.community.core.skills_pool.types import (
    BotSkillLayoutScope,
    BotSkillLayoutState,
    RolloutEvidence,
    SkillLayout,
    SkillLayoutPhase,
)


def _state(
    *,
    phase: SkillLayoutPhase,
    generation: str | None = None,
    preparation_id: str | None = None,
    cutover_committed: bool = False,
):
    return BotSkillLayoutState(
        scope=BotSkillLayoutScope(env="pre", entity_id="staff_1", bot_id="bot-1"),
        active_layout=SkillLayout.POOL,
        target_layout=None,
        phase=phase,
        migration_generation=generation,
        persisted=True,
        layout_contract_version="skills-pool-p3-v1",
        preparation_id=preparation_id,
        data_plane_cutover_committed=cutover_committed,
        rollout_evidence=RolloutEvidence(
            env="pre",
            config_id=7,
            config_version="revision-1",
            batch_id=None,
            engine_type="openclaw",
            decision_reason="owner_allowlist",
        ),
    )


def _evidence() -> dict[str, object]:
    return {
        "actual_engine": "openclaw",
        "actual_layout": "pool",
        "layout_contract_version": "skills-pool-p3-v1",
        "roots_initialized": True,
    }


def test_matching_native_evidence_uses_repository_cas() -> None:
    repository = MagicMock()
    repository.get.return_value = _state(phase=SkillLayoutPhase.POOL_INITIALIZING)
    repository.confirm_pool_initializing.return_value = True
    service = SkillsPoolNativeLayoutConfirmationService(repository)
    scope = BotSkillLayoutScope(env="pre", entity_id="staff_1", bot_id="bot-1")

    service.confirm(
        env=scope.env,
        entity_id=scope.entity_id,
        bot_id=scope.bot_id,
        expected_engine="openclaw",
        evidence=_evidence(),
    )

    repository.confirm_pool_initializing.assert_called_once_with(
        scope=scope,
        layout_contract_version="skills-pool-p3-v1",
    )


def test_migration_identity_cannot_use_native_confirmation() -> None:
    repository = MagicMock()
    repository.get.return_value = _state(
        phase=SkillLayoutPhase.POOL_ACTIVE,
        generation="generation-1",
    )
    service = SkillsPoolNativeLayoutConfirmationService(repository)

    with pytest.raises(
        PoolNativeLayoutConfirmationError,
        match="not confirmable Pool-native state",
    ):
        service.confirm(
            env=repository.get.return_value.scope.env,
            entity_id=repository.get.return_value.scope.entity_id,
            bot_id=repository.get.return_value.scope.bot_id,
            expected_engine="openclaw",
            evidence=_evidence(),
        )
    repository.confirm_pool_initializing.assert_not_called()


def test_completed_migration_restart_is_accepted_without_native_cas() -> None:
    repository = MagicMock()
    repository.get.return_value = _state(
        phase=SkillLayoutPhase.POOL_ACTIVE,
        generation="generation-1",
        preparation_id="preparation-1",
        cutover_committed=True,
    )
    service = SkillsPoolNativeLayoutConfirmationService(repository)

    service.confirm(
        env=repository.get.return_value.scope.env,
        entity_id=repository.get.return_value.scope.entity_id,
        bot_id=repository.get.return_value.scope.bot_id,
        expected_engine="openclaw",
        evidence=_evidence(),
    )

    repository.confirm_pool_initializing.assert_not_called()


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("actual_engine", "hermes"),
        ("actual_layout", "legacy"),
        ("layout_contract_version", "future"),
        ("roots_initialized", False),
    ),
)
def test_mismatched_evidence_fails_closed(field: str, value: object) -> None:
    repository = MagicMock()
    service = SkillsPoolNativeLayoutConfirmationService(repository)
    evidence = _evidence()
    evidence[field] = value

    with pytest.raises(PoolNativeLayoutConfirmationError):
        service.confirm(
            env="pre",
            entity_id="staff_1",
            bot_id="bot-1",
            expected_engine="openclaw",
            evidence=evidence,
        )

    repository.get.assert_not_called()


@pytest.mark.parametrize("field", ("unexpected", None))
def test_evidence_shape_must_match_contract(field: str | None) -> None:
    repository = MagicMock()
    service = SkillsPoolNativeLayoutConfirmationService(repository)
    evidence = _evidence()
    if field is None:
        evidence.pop("roots_initialized")
    else:
        evidence[field] = "value"

    with pytest.raises(
        PoolNativeLayoutConfirmationError,
        match="fields do not match",
    ):
        service.confirm(
            env="pre",
            entity_id="staff_1",
            bot_id="bot-1",
            expected_engine="openclaw",
            evidence=evidence,
        )

    repository.get.assert_not_called()
