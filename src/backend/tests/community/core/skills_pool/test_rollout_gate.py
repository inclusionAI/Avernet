"""Skills Pool rollout gate 的 fail-closed 契约测试。"""

from __future__ import annotations

from typing import Any

import pytest

from agentclaw.community.core.common_config.whitelist_service import (
    CommonWhiteListService,
)
from agentclaw.community.core.skills_pool.rollout_gate import (
    BotRuntimeForm,
    RolloutDecisionReason,
    SkillsPoolRolloutGate,
)


class FakeCommonConfigService:
    def __init__(
        self,
        config: dict[str, Any] | None = None,
        *,
        error: Exception | None = None,
    ) -> None:
        self.config = config
        self.error = error

    def get_config(self, **_: object) -> dict[str, Any] | None:
        if self.error is not None:
            raise self.error
        return self.config


def enabled_config(
    *,
    env: str = "pre",
    promoted_engines: object = None,
    whitelist: object = None,
    enable_all: object = False,
) -> dict[str, Any]:
    return {
        "id": 42,
        "enable": "1",
        "env": env,
        "gmt_modified": "2026-07-23T12:00:00",
        "param_value": {
            "enable_all": enable_all,
            "promoted_engines": (
                ["openclaw"] if promoted_engines is None else promoted_engines
            ),
            "whitelist": (
                [
                    {
                        "owner_id": "owner-1",
                        "bot_id": "bot-1",
                        "batch_id": "openclaw-canary-1",
                    }
                ]
                if whitelist is None
                else whitelist
            ),
        },
    }


def make_gate(
    config: dict[str, Any] | None = None,
    *,
    error: Exception | None = None,
) -> SkillsPoolRolloutGate:
    config_service = FakeCommonConfigService(config, error=error)
    whitelist_service = CommonWhiteListService(config_service)
    return SkillsPoolRolloutGate(config_service, whitelist_service)


def evaluate(
    gate: SkillsPoolRolloutGate,
    *,
    env: str = "pre",
    owner_id: str = "owner-1",
    bot_id: str = "bot-1",
    engine_type: str = "openclaw",
    runtime_form: BotRuntimeForm = BotRuntimeForm.PERSONAL,
):
    return gate.evaluate(
        env=env,
        owner_id=owner_id,
        bot_id=bot_id,
        engine_type=engine_type,
        runtime_form=runtime_form,
    )


def test_exact_bot_in_promoted_engine_is_eligible_with_audit_evidence() -> None:
    decision = evaluate(make_gate(enabled_config()))

    assert decision.eligible
    assert decision.reason is RolloutDecisionReason.ELIGIBLE
    assert decision.evidence is not None
    assert decision.evidence.env == "pre"
    assert decision.evidence.config_id == 42
    assert decision.evidence.config_version == "2026-07-23T12:00:00"
    assert decision.evidence.batch_id == "openclaw-canary-1"
    assert decision.evidence.engine_type == "openclaw"


@pytest.mark.parametrize(
    ("gate", "reason"),
    [
        (make_gate(None), RolloutDecisionReason.CONFIG_MISSING),
        (
            make_gate({**enabled_config(), "enable": "0"}),
            RolloutDecisionReason.CONFIG_DISABLED,
        ),
        (
            make_gate(error=RuntimeError("db unavailable")),
            RolloutDecisionReason.CONFIG_READ_ERROR,
        ),
        (
            make_gate(enabled_config(whitelist="not-a-list")),
            RolloutDecisionReason.CONFIG_INVALID,
        ),
        (
            make_gate(
                enabled_config(
                    whitelist=[
                        {
                            "owner_id": "owner-1",
                            "bot_id": "bot-1",
                            "git_path": "local:///legacy/path",
                        }
                    ]
                )
            ),
            RolloutDecisionReason.CONFIG_INVALID,
        ),
        (
            make_gate(enabled_config(enable_all=True)),
            RolloutDecisionReason.ENABLE_ALL_FORBIDDEN,
        ),
    ],
)
def test_missing_disabled_failed_or_invalid_config_fails_closed(
    gate: SkillsPoolRolloutGate,
    reason: RolloutDecisionReason,
) -> None:
    decision = evaluate(gate)

    assert not decision.eligible
    assert decision.reason is reason
    assert decision.evidence is None


def test_environment_engine_and_exact_identity_are_all_required() -> None:
    gate = make_gate(enabled_config())

    assert (
        evaluate(gate, env="prod").reason is RolloutDecisionReason.CONFIG_ENV_MISMATCH
    )
    assert (
        evaluate(gate, engine_type="claude_code").reason
        is RolloutDecisionReason.ENGINE_NOT_PROMOTED
    )
    assert (
        evaluate(gate, owner_id="other-owner").reason
        is RolloutDecisionReason.BOT_NOT_WHITELISTED
    )


@pytest.mark.parametrize("engine_type", ["teclaw", "moltis", "", "unknown"])
def test_non_pool_engine_never_matches(engine_type: str) -> None:
    config = enabled_config(promoted_engines=[engine_type])

    assert (
        evaluate(make_gate(config), engine_type=engine_type).reason
        is RolloutDecisionReason.ENGINE_NOT_SUPPORTED
    )


@pytest.mark.parametrize("engine_type", ["openclaw", "aicoding", "hermes"])
def test_service_draft_is_editable_but_published_service_is_not(
    engine_type: str,
) -> None:
    gate = make_gate(enabled_config(promoted_engines=[engine_type]))

    assert evaluate(
        gate,
        engine_type=engine_type,
        runtime_form=BotRuntimeForm.SERVICE_DRAFT,
    ).eligible
    assert (
        evaluate(
            gate,
            engine_type=engine_type,
            runtime_form=BotRuntimeForm.PUBLISHED_SERVICE,
        ).reason
        is RolloutDecisionReason.RUNTIME_NOT_EDITABLE
    )


@pytest.mark.parametrize("runtime_form", [None, "personal", object()])
def test_unknown_runtime_form_fails_closed(runtime_form: object) -> None:
    decision = evaluate(make_gate(enabled_config()), runtime_form=runtime_form)

    assert not decision.eligible
    assert decision.reason is RolloutDecisionReason.RUNTIME_NOT_EDITABLE
