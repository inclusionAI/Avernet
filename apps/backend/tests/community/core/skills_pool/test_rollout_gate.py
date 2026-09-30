"""Skills Pool rollout gate 的 fail-closed 契约测试。"""

from __future__ import annotations

from typing import Any

import pytest

from agentclaw.community.core.common_config.whitelist_service import (
    CommonWhiteListService,
)
from agentclaw.community.core.skills_pool.rollout_config import (
    ROLLOUT_SCHEMA_VERSION,
    normalize_rollout_config_value,
)
from agentclaw.community.core.skills_pool.rollout_gate import (
    BotRuntimeForm,
    RolloutDecisionReason,
    SkillsPoolRolloutGate,
)


def test_legacy_rollout_entries_are_normalized_to_canonical_shape() -> None:
    assert normalize_rollout_config_value(
        {
            "enable_all": False,
            "promoted_engines": ["openclaw"],
            "whitelist": [
                {
                    "owner_id": 168944,
                    "bot_id": 42,
                    "batch_id": 7,
                }
            ],
        }
    ) == {
        "enable_all": False,
        "full_rollout_engines": [],
        "full_rollout_owners": [],
        "promoted_engines": ["openclaw"],
        "whitelist": [
            {
                "owner_id": "168944",
                "bot_id": "42",
                "batch_id": "7",
            }
        ],
        "negative_controls": [],
        "teclaw_controls": [],
    }


def test_promoted_engines_accept_only_canonical_supported_subsets() -> None:
    assert normalize_rollout_config_value(
        {
            "enable_all": False,
            "promoted_engines": ["openclaw", "aicoding"],
            "whitelist": [],
        }
    ) == {
        "enable_all": False,
        "full_rollout_engines": [],
        "full_rollout_owners": [],
        "promoted_engines": ["openclaw", "aicoding"],
        "whitelist": [],
        "negative_controls": [],
        "teclaw_controls": [],
    }

    for promoted_engines in (
        ["aicoding", "openclaw"],
        ["openclaw", "openclaw"],
        ["unsupported"],
    ):
        assert (
            normalize_rollout_config_value(
                {
                    "enable_all": False,
                    "promoted_engines": promoted_engines,
                    "whitelist": [],
                }
            )
            is None
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
    full_rollout_engines: object = None,
    full_rollout_owners: object = None,
) -> dict[str, Any]:
    return {
        "id": 42,
        "enable": "1",
        "env": env,
        "gmt_modified": "2026-07-23T12:00:00",
        "param_value": {
            "enable_all": enable_all,
            "full_rollout_engines": (
                [] if full_rollout_engines is None else full_rollout_engines
            ),
            "full_rollout_owners": (
                [] if full_rollout_owners is None else full_rollout_owners
            ),
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


def admission_policy_v2(
    *,
    engine_admission: object = None,
    bot_allowlist: object = None,
    owner_rollouts: object = None,
    environment_rollouts: object = None,
    bot_exclusions: object = None,
) -> dict[str, Any]:
    return {
        "id": 42,
        "enable": "1",
        "env": "pre",
        "gmt_modified": "2026-09-16T12:00:00",
        "ext_info": {"revision": "policy-revision-7"},
        "param_value": {
            "schema_version": ROLLOUT_SCHEMA_VERSION,
            "engine_admission": (
                {"openclaw": True}
                if engine_admission is None
                else engine_admission
            ),
            "bot_allowlist": (
                [] if bot_allowlist is None else bot_allowlist
            ),
            "owner_rollouts": (
                [] if owner_rollouts is None else owner_rollouts
            ),
            "environment_rollouts": (
                [] if environment_rollouts is None else environment_rollouts
            ),
            "bot_exclusions": (
                [] if bot_exclusions is None else bot_exclusions
            ),
        },
    }


def test_v2_policy_normalizes_only_explicit_engine_scoped_rules() -> None:
    value = admission_policy_v2(
        bot_allowlist=[
            {"owner_id": 168944, "bot_id": 42, "engine": "openclaw"}
        ],
        owner_rollouts=[{"owner_id": 168944, "engine": "openclaw"}],
        environment_rollouts=["openclaw"],
        bot_exclusions=[
            {"owner_id": 168944, "bot_id": 43, "engine": "openclaw"}
        ],
    )["param_value"]

    assert normalize_rollout_config_value(value) == {
        "schema_version": ROLLOUT_SCHEMA_VERSION,
        "engine_admission": {"openclaw": True},
        "bot_allowlist": [
            {"owner_id": "168944", "bot_id": "42", "engine": "openclaw"}
        ],
        "owner_rollouts": [
            {"owner_id": "168944", "engine": "openclaw"}
        ],
        "environment_rollouts": ["openclaw"],
        "bot_exclusions": [
            {"owner_id": "168944", "bot_id": "43", "engine": "openclaw"}
        ],
    }

    invalid = dict(value)
    invalid["bot_allowlist"] = [{"owner_id": "168944", "bot_id": "42"}]
    assert normalize_rollout_config_value(invalid) is None

    invalid_engine_list = dict(value)
    invalid_engine_list["environment_rollouts"] = [{"engine": "openclaw"}]
    assert normalize_rollout_config_value(invalid_engine_list) is None


def test_v2_engine_switch_precedes_every_allow_rule() -> None:
    config = admission_policy_v2(
        engine_admission={"openclaw": False},
        bot_allowlist=[
            {"owner_id": "owner-1", "bot_id": "bot-1", "engine": "openclaw"}
        ],
        owner_rollouts=[{"owner_id": "owner-1", "engine": "openclaw"}],
        environment_rollouts=["openclaw"],
    )

    decision = evaluate(make_gate(config))

    assert decision.eligible is False
    assert decision.reason is RolloutDecisionReason.ENGINE_ADMISSION_DISABLED


def test_v2_exclusion_precedes_exact_owner_and_environment_rules() -> None:
    config = admission_policy_v2(
        bot_allowlist=[
            {"owner_id": "owner-1", "bot_id": "bot-1", "engine": "openclaw"}
        ],
        owner_rollouts=[{"owner_id": "owner-1", "engine": "openclaw"}],
        environment_rollouts=["openclaw"],
        bot_exclusions=[
            {"owner_id": "owner-1", "bot_id": "bot-1", "engine": "openclaw"}
        ],
    )

    decision = evaluate(make_gate(config))

    assert decision.eligible is False
    assert decision.reason is RolloutDecisionReason.BOT_EXCLUDED


@pytest.mark.parametrize(
    ("policy", "expected_reason"),
    [
        (
            admission_policy_v2(
                bot_allowlist=[
                    {
                        "owner_id": "owner-1",
                        "bot_id": "bot-1",
                        "engine": "openclaw",
                    }
                ]
            ),
            "exact_bot_allowlist",
        ),
        (
            admission_policy_v2(
                owner_rollouts=[{"owner_id": "owner-1", "engine": "openclaw"}]
            ),
            "owner_rollout",
        ),
        (
            admission_policy_v2(environment_rollouts=["openclaw"]),
            "environment_rollout",
        ),
    ],
)
def test_v2_allow_rules_freeze_policy_revision_without_batch(
    policy: dict[str, Any],
    expected_reason: str,
) -> None:
    decision = evaluate(make_gate(policy))

    assert decision.eligible is True
    assert decision.evidence is not None
    assert decision.evidence.config_version == "policy-revision-7"
    assert decision.evidence.batch_id is None
    assert decision.evidence.engine_type == "openclaw"
    assert decision.evidence.decision_reason == expected_reason


def test_v2_rule_for_another_engine_never_admits_openclaw() -> None:
    config = admission_policy_v2(
        engine_admission={"openclaw": True, "hermes": True},
        bot_allowlist=[
            {"owner_id": "owner-1", "bot_id": "bot-1", "engine": "hermes"}
        ],
        owner_rollouts=[{"owner_id": "owner-1", "engine": "hermes"}],
        environment_rollouts=["hermes"],
    )

    decision = evaluate(make_gate(config))

    assert decision.eligible is False
    assert decision.reason is RolloutDecisionReason.BOT_NOT_ALLOWED


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


def test_logical_config_revision_is_frozen_into_claim_evidence() -> None:
    config = {
        **enabled_config(),
        "ext_info": {"revision": "revision-7", "audit_log": []},
    }

    decision = evaluate(make_gate(config))

    assert decision.evidence is not None
    assert decision.evidence.config_version == "revision-7"


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


def test_full_rollout_admits_future_bot_in_promoted_engine() -> None:
    decision = evaluate(
        make_gate(enabled_config(enable_all=True, whitelist=[])),
        owner_id="future-owner",
        bot_id="future-bot",
    )

    assert decision.eligible
    assert decision.evidence is not None
    assert decision.evidence.batch_id is None
    assert decision.evidence.decision_reason == "environment_full_rollout"


def test_engine_full_rollout_admits_only_that_promoted_engine() -> None:
    gate = make_gate(
        enabled_config(
            promoted_engines=["openclaw", "claude_code"],
            full_rollout_engines=["openclaw"],
            whitelist=[],
        )
    )

    openclaw = evaluate(
        gate,
        owner_id="future-owner",
        bot_id="future-bot",
    )
    claude = evaluate(
        gate,
        owner_id="future-owner",
        bot_id="future-bot",
        engine_type="claude_code",
    )

    assert openclaw.eligible
    assert openclaw.evidence is not None
    assert openclaw.evidence.decision_reason == "engine_full_rollout"
    assert claude.reason is RolloutDecisionReason.BOT_NOT_WHITELISTED


def test_owner_full_rollout_admits_future_and_restarted_bots_for_that_engine() -> None:
    gate = make_gate(
        enabled_config(
            promoted_engines=["openclaw", "claude_code"],
            full_rollout_owners=[
                {"owner_id": "owner-1", "engine": "openclaw"},
            ],
            whitelist=[],
        )
    )

    future_openclaw = evaluate(
        gate,
        owner_id="owner-1",
        bot_id="future-bot",
    )
    restarted_claude = evaluate(
        gate,
        owner_id="owner-1",
        bot_id="existing-bot",
        engine_type="claude_code",
    )
    other_owner = evaluate(
        gate,
        owner_id="owner-2",
        bot_id="future-bot",
    )

    assert future_openclaw.eligible
    assert future_openclaw.evidence is not None
    assert future_openclaw.evidence.decision_reason == "owner_full_rollout"
    assert restarted_claude.reason is RolloutDecisionReason.BOT_NOT_WHITELISTED
    assert other_owner.reason is RolloutDecisionReason.BOT_NOT_WHITELISTED


def test_exact_negative_control_overrides_owner_full_rollout() -> None:
    config = enabled_config(
        full_rollout_owners=[
            {"owner_id": "owner-1", "engine": "openclaw"},
        ],
        whitelist=[],
    )
    config["param_value"]["negative_controls"] = [
        {
            "owner_id": "owner-1",
            "bot_id": "control-bot",
            "batch_id": "openclaw-canary-1",
        }
    ]
    gate = make_gate(config)

    control = evaluate(gate, owner_id="owner-1", bot_id="control-bot")
    ordinary = evaluate(gate, owner_id="owner-1", bot_id="ordinary-bot")

    assert not control.eligible
    assert control.reason is RolloutDecisionReason.BOT_NEGATIVE_CONTROL
    assert ordinary.eligible


@pytest.mark.parametrize(
    "entry",
    [
        {"owner_id": "*", "engine": "openclaw"},
        {"owner_id": "owner-1", "engine": "unknown"},
        {"owner_id": "owner-1", "engine": "claude_code"},
        {"owner_id": "owner-1"},
        {"owner_id": "owner-1", "engine": "openclaw", "bot_id": "bot-1"},
    ],
)
def test_invalid_owner_full_rollout_entry_fails_closed(
    entry: dict[str, object],
) -> None:
    decision = evaluate(
        make_gate(enabled_config(full_rollout_owners=[entry], whitelist=[]))
    )

    assert not decision.eligible
    assert decision.reason is RolloutDecisionReason.CONFIG_INVALID


def test_full_rollout_still_rejects_unpromoted_engine() -> None:
    decision = evaluate(
        make_gate(enabled_config(enable_all=True, whitelist=[])),
        engine_type="claude_code",
    )

    assert not decision.eligible
    assert decision.reason is RolloutDecisionReason.ENGINE_NOT_PROMOTED


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
    promotion_order = ["openclaw", "claude_code", "aicoding", "hermes"]
    gate = make_gate(
        enabled_config(
            promoted_engines=promotion_order[: promotion_order.index(engine_type) + 1]
        )
    )

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


@pytest.mark.parametrize(
    "engine_type",
    ["openclaw", "claude_code", "aicoding", "hermes"],
)
def test_desktop_uses_the_existing_engine_and_whitelist_gate(
    engine_type: str,
) -> None:
    gate = make_gate(
        enabled_config(
            promoted_engines=[
                "openclaw",
                "claude_code",
                "aicoding",
                "hermes",
            ],
        )
    )

    assert evaluate(
        gate,
        engine_type=engine_type,
        runtime_form=BotRuntimeForm.DESKTOP,
    ).eligible


@pytest.mark.parametrize("runtime_form", [None, "personal", object()])
def test_unknown_runtime_form_fails_closed(runtime_form: object) -> None:
    decision = evaluate(make_gate(enabled_config()), runtime_form=runtime_form)

    assert not decision.eligible
    assert decision.reason is RolloutDecisionReason.RUNTIME_NOT_EDITABLE


def test_unknown_rollout_config_key_fails_closed() -> None:
    config = enabled_config()
    config["param_value"]["enable_pattern"] = "*"

    decision = evaluate(make_gate(config))

    assert not decision.eligible
    assert decision.reason is RolloutDecisionReason.CONFIG_INVALID


def test_invalid_control_entry_fails_closed() -> None:
    config = enabled_config()
    config["param_value"]["negative_controls"] = [
        {"owner_id": "owner-2", "bot_id": "*", "batch_id": "batch-1"}
    ]

    decision = evaluate(make_gate(config))

    assert not decision.eligible
    assert decision.reason is RolloutDecisionReason.CONFIG_INVALID
