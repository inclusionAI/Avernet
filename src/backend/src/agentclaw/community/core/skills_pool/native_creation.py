"""Select the initial layout for a newly created OpenClaw Bot."""

from __future__ import annotations

from injector import inject

from agentclaw.community.core.skills_pool.rollout_gate import (
    BotRuntimeForm,
    RolloutDecisionReason,
    SkillsPoolRolloutGate,
)
from agentclaw.community.core.skills_pool.types import InitialSkillLayoutSelection
from agentclaw.community.core.skill_center.services.runtime_layout_probe import (
    LAYOUT_CONTRACT_VERSION,
)


class PoolNativeCreationDecisionError(RuntimeError):
    """The initial layout cannot be decided safely for Bot creation."""


_FATAL_DECISIONS = frozenset(
    {
        RolloutDecisionReason.CONFIG_READ_ERROR,
        RolloutDecisionReason.CONFIG_INVALID,
        RolloutDecisionReason.CONFIG_ENV_MISMATCH,
    }
)


class SkillsPoolNativeCreationPolicy:
    """Fail closed on bad policy data and select Pool only for OpenClaw."""

    @inject
    def __init__(self, rollout_gate: SkillsPoolRolloutGate) -> None:
        self._rollout_gate = rollout_gate

    def select(
        self,
        *,
        env: str,
        owner_id: str,
        bot_id: str,
        engine_type: str,
        bot_type: str,
    ) -> InitialSkillLayoutSelection | None:
        if engine_type != "openclaw":
            return None
        runtime_form = {
            "personal": BotRuntimeForm.PERSONAL,
            "desktop": BotRuntimeForm.DESKTOP,
            "service": BotRuntimeForm.SERVICE_DRAFT,
        }.get(bot_type)
        if runtime_form is None:
            return None
        decision = self._rollout_gate.evaluate(
            env=env,
            owner_id=owner_id,
            bot_id=bot_id,
            engine_type=engine_type,
            runtime_form=runtime_form,
        )
        if decision.reason in _FATAL_DECISIONS:
            raise PoolNativeCreationDecisionError(
                f"Skills Pool creation policy is unavailable: {decision.reason.value}"
            )
        if not decision.eligible:
            return None
        if decision.evidence is None:
            raise PoolNativeCreationDecisionError(
                "eligible Skills Pool creation decision has no evidence"
            )
        return InitialSkillLayoutSelection(
            layout_contract_version=LAYOUT_CONTRACT_VERSION,
            rollout_evidence=decision.evidence,
        )


__all__ = [
    "PoolNativeCreationDecisionError",
    "SkillsPoolNativeCreationPolicy",
]
