from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from agentclaw.community.core.skills_pool.native_creation import (
    PoolNativeCreationDecisionError,
    SkillsPoolNativeCreationPolicy,
)
from agentclaw.community.core.skills_pool.rollout_gate import (
    BotRuntimeForm,
    RolloutDecision,
    RolloutDecisionReason,
)
from agentclaw.community.core.skills_pool.types import RolloutEvidence


def _evidence() -> RolloutEvidence:
    return RolloutEvidence(
        env="pre",
        config_id=7,
        config_version="revision-1",
        batch_id=None,
        engine_type="openclaw",
        decision_reason="owner_allowlist",
    )


@pytest.mark.parametrize(
    ("bot_type", "runtime_form"),
    (
        ("personal", BotRuntimeForm.PERSONAL),
        ("desktop", BotRuntimeForm.DESKTOP),
        ("service", BotRuntimeForm.SERVICE_DRAFT),
    ),
)
def test_eligible_openclaw_creation_selects_pool_with_rollout_evidence(
    bot_type: str,
    runtime_form: BotRuntimeForm,
) -> None:
    gate = MagicMock()
    gate.evaluate.return_value = RolloutDecision(
        eligible=True,
        reason=RolloutDecisionReason.ELIGIBLE,
        evidence=_evidence(),
    )

    selection = SkillsPoolNativeCreationPolicy(gate).select(
        env="pre",
        owner_id="owner-1",
        bot_id="bot-1",
        engine_type="openclaw",
        bot_type=bot_type,
    )

    assert selection is not None
    assert selection.layout_contract_version == "skills-pool-p3-v1"
    assert selection.rollout_evidence == _evidence()
    gate.evaluate.assert_called_once_with(
        env="pre",
        owner_id="owner-1",
        bot_id="bot-1",
        engine_type="openclaw",
        runtime_form=runtime_form,
    )


@pytest.mark.parametrize(
    "reason",
    (
        RolloutDecisionReason.CONFIG_MISSING,
        RolloutDecisionReason.CONFIG_DISABLED,
        RolloutDecisionReason.BOT_NOT_ALLOWED,
        RolloutDecisionReason.BOT_EXCLUDED,
        RolloutDecisionReason.ENGINE_ADMISSION_DISABLED,
    ),
)
def test_normal_non_matches_keep_legacy(reason: RolloutDecisionReason) -> None:
    gate = MagicMock()
    gate.evaluate.return_value = RolloutDecision(eligible=False, reason=reason)

    assert (
        SkillsPoolNativeCreationPolicy(gate).select(
            env="pre",
            owner_id="owner-1",
            bot_id="bot-1",
            engine_type="openclaw",
            bot_type="personal",
        )
        is None
    )


@pytest.mark.parametrize(
    "reason",
    (
        RolloutDecisionReason.CONFIG_READ_ERROR,
        RolloutDecisionReason.CONFIG_INVALID,
        RolloutDecisionReason.CONFIG_ENV_MISMATCH,
    ),
)
def test_unreliable_policy_data_fails_creation(reason: RolloutDecisionReason) -> None:
    gate = MagicMock()
    gate.evaluate.return_value = RolloutDecision(eligible=False, reason=reason)

    with pytest.raises(PoolNativeCreationDecisionError, match=reason.value):
        SkillsPoolNativeCreationPolicy(gate).select(
            env="pre",
            owner_id="owner-1",
            bot_id="bot-1",
            engine_type="openclaw",
            bot_type="personal",
        )


def test_other_engines_do_not_consume_openclaw_creation_rollout() -> None:
    gate = MagicMock()

    assert (
        SkillsPoolNativeCreationPolicy(gate).select(
            env="pre",
            owner_id="owner-1",
            bot_id="bot-1",
            engine_type="hermes",
            bot_type="personal",
        )
        is None
    )
    gate.evaluate.assert_not_called()
