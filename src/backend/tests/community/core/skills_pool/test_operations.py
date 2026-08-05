"""Skills Pool operator control-plane behavior."""

from __future__ import annotations

from dataclasses import replace

import pytest

from agentclaw.community.core.skills_pool.operations import (
    BatchPromotionEvidence,
    RolloutControlGroup,
    RolloutOperationError,
    RolloutOwnerEntry,
    SkillsPoolRolloutOperations,
)
from agentclaw.community.core.skills_pool.types import (
    BotSkillLayoutScope,
    BotSkillLayoutState,
    RolloutEvidence,
    SkillLayout,
    SkillLayoutPhase,
)


ENV = "pre"
OWNER = "owner-1"
BOT_ID = "bot-1"
SCOPE = BotSkillLayoutScope(env=ENV, entity_id=OWNER, bot_id=BOT_ID)


class FakeCommonConfig:
    def __init__(self, config: dict[str, object] | None = None) -> None:
        self.config = config
        self.upserts: list[dict[str, object]] = []

    def get_config(self, **_: object) -> dict[str, object] | None:
        return self.config

    def _save(self, kwargs: dict[str, object]) -> int:
        self.upserts.append(kwargs)
        value = kwargs["param_value"]
        assert isinstance(value, dict)
        self.config = {
            "id": 42,
            "enable": kwargs["enable"],
            "env": kwargs["env"],
            "param_value": value,
            "ext_info": kwargs["ext_info"],
            "gmt_modified": "2026-07-25T10:00:00+00:00",
        }
        return 42


class FakeRolloutRepository:
    def __init__(self, configs: FakeCommonConfig) -> None:
        self.configs = configs
        self.audit: list[dict[str, object]] = []
        self.cas_succeeds = True

    def list_audit_events(self, *, env: str) -> list[dict[str, object]]:
        return [event for event in self.audit if event["env"] == env]

    def commit_change(self, **kwargs: object) -> bool:
        if not self.cas_succeeds:
            return False
        current = self.configs.config
        if current is not None:
            ext = current.get("ext_info")
            revision = ext.get("revision") if isinstance(ext, dict) else None
            if revision != kwargs["expected_revision"]:
                return False
        audit = kwargs["audit"]
        value = kwargs["value"]
        assert isinstance(audit, dict)
        assert isinstance(value, dict)
        self.audit.append(audit)
        self.configs._save(
            {
                "param_value": value,
                "ext_info": {
                    "revision": kwargs["next_revision"],
                    "last_action": audit["action"],
                },
                "enable": "1" if kwargs["enabled"] else "0",
                "env": kwargs["env"],
            }
        )
        return True


class FakeBots:
    def __init__(self) -> None:
        self.matches: list[dict[str, object]] = [
            {
                "bot_id": BOT_ID,
                "owner_id": OWNER,
                "entity_id": OWNER,
                "env": ENV,
                "active_engine": "openclaw",
            }
        ]

    def get_live_by_id_owner_and_env(
        self,
        **identity: object,
    ) -> list[dict[str, object]]:
        return [
            bot
            for bot in self.matches
            if str(bot["bot_id"]) == str(identity["bot_id"])
            and str(bot["owner_id"]) == str(identity["owner_id"])
            and bot["env"] == identity["env"]
        ]


class FakeLayouts:
    def __init__(self) -> None:
        self.state = BotSkillLayoutState.legacy_default(SCOPE)

    def get(self, scope: BotSkillLayoutScope) -> BotSkillLayoutState:
        assert scope == SCOPE
        return self.state

    def list_states(
        self,
        *,
        env: str,
        engine: str | None = None,
        batch_id: str | None = None,
    ) -> list[BotSkillLayoutState]:
        evidence = self.state.rollout_evidence
        if (
            self.state.persisted
            and self.state.scope.env == env
            and (
                engine is None
                or evidence is not None
                and evidence.engine_type == engine
            )
            and (
                batch_id is None
                or evidence is not None
                and evidence.batch_id == batch_id
            )
        ):
            return [self.state]
        return []


def build_operations(
    *,
    config: dict[str, object] | None = None,
) -> tuple[
    SkillsPoolRolloutOperations,
    FakeCommonConfig,
    FakeBots,
    FakeLayouts,
]:
    configs = FakeCommonConfig(config)
    bots = FakeBots()
    layouts = FakeLayouts()
    rollout_repository = FakeRolloutRepository(configs)
    return (
        SkillsPoolRolloutOperations(
            common_config_service=configs,
            bot_repository=bots,
            layout_repository=layouts,
            rollout_repository=rollout_repository,
        ),
        configs,
        bots,
        layouts,
    )


def rollout_config(
    *,
    enabled: bool = True,
    enable_all: bool = False,
    full_rollout_engines: list[str] | None = None,
    full_rollout_owners: list[dict[str, object]] | None = None,
    promoted_engines: list[str] | None = None,
    whitelist: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    return {
        "id": 7,
        "enable": "1" if enabled else "0",
        "env": ENV,
        "gmt_modified": "2026-07-25T09:00:00+00:00",
        "ext_info": {},
        "param_value": {
            "enable_all": enable_all,
            "full_rollout_engines": full_rollout_engines or [],
            "full_rollout_owners": full_rollout_owners or [],
            "promoted_engines": promoted_engines or [],
            "whitelist": whitelist or [],
            "negative_controls": [],
            "teclaw_controls": [],
        },
    }


def test_enabling_missing_rollout_creates_safe_exact_bot_configuration() -> None:
    operations, configs, _, _ = build_operations()

    snapshot = operations.set_feature_enabled(
        env=ENV,
        enabled=True,
        operator="freddie",
        reason="start pre canary",
    )

    assert snapshot.enabled is True
    assert snapshot.enable_all is False
    assert snapshot.promoted_engines == ()
    assert snapshot.whitelist == ()
    assert configs.upserts[0]["param_value"] == {
        "enable_all": False,
        "full_rollout_engines": [],
        "full_rollout_owners": [],
        "promoted_engines": [],
        "whitelist": [],
        "negative_controls": [],
        "teclaw_controls": [],
    }


def test_engine_promotion_is_independent_canonical_and_idempotent() -> None:
    operations, configs, _, _ = build_operations(
        config=rollout_config(
            promoted_engines=["openclaw"],
            whitelist=[
                {
                    "owner_id": OWNER,
                    "bot_id": BOT_ID,
                    "batch_id": "openclaw-unaccepted",
                }
            ],
        )
    )

    hermes_promoted = operations.promote_engine(
        env=ENV,
        engine="hermes",
        operator="freddie",
        reason="test Hermes in parallel",
    )
    aicoding_promoted = operations.promote_engine(
        env=ENV,
        engine="aicoding",
        operator="freddie",
        reason="test AICoding in parallel",
    )
    repeated = operations.promote_engine(
        env=ENV,
        engine="hermes",
        operator="freddie",
        reason="idempotent retry",
    )

    assert hermes_promoted.promoted_engines == ("openclaw", "hermes")
    assert aicoding_promoted.promoted_engines == (
        "openclaw",
        "aicoding",
        "hermes",
    )
    assert repeated.promoted_engines == ("openclaw", "aicoding", "hermes")
    assert len(configs.upserts) == 2


def test_full_rollout_requires_accepted_promoted_engine_then_admits_environment() -> (
    None
):
    operations, configs, _, _ = build_operations(
        config=rollout_config(promoted_engines=["openclaw"])
    )

    with pytest.raises(RolloutOperationError, match="accepted openclaw batch"):
        operations.set_full_rollout(
            env=ENV,
            enabled=True,
            operator="freddie",
            reason="too early",
        )

    operations.accept_batch(
        env=ENV,
        operator="freddie",
        reason="canary passed",
        acceptance=BatchPromotionEvidence(
            engine="openclaw",
            batch_id="openclaw-canary-1",
            promotion_ready=True,
            report={
                "rollout_config_version": "2026-07-25T09:00:00+00:00",
                "promotion_ready": True,
            },
        ),
    )
    snapshot = operations.set_full_rollout(
        env=ENV,
        enabled=True,
        operator="freddie",
        reason="promote pre openclaw",
    )

    assert snapshot.enable_all is True
    assert configs.config is not None
    assert configs.config["param_value"]["enable_all"] is True
    assert snapshot.audit_log[-1].action == "full_rollout:environment:enable"


def test_environment_full_rollout_checks_every_non_prefix_promoted_engine() -> None:
    operations, _, _, _ = build_operations(
        config=rollout_config(promoted_engines=["openclaw", "aicoding"])
    )
    operations.accept_batch(
        env=ENV,
        operator="freddie",
        reason="openclaw canary passed",
        acceptance=BatchPromotionEvidence(
            engine="openclaw",
            batch_id="openclaw-canary-1",
            promotion_ready=True,
            report={
                "rollout_config_version": "2026-07-25T09:00:00+00:00",
                "promotion_ready": True,
            },
        ),
    )

    with pytest.raises(RolloutOperationError, match="accepted aicoding batch"):
        operations.set_full_rollout(
            env=ENV,
            enabled=True,
            operator="freddie",
            reason="aicoding still needs acceptance",
        )


def test_engine_full_rollout_does_not_admit_other_promoted_engine() -> None:
    operations, configs, _, _ = build_operations(
        config=rollout_config(promoted_engines=["openclaw"])
    )
    operations.accept_batch(
        env=ENV,
        operator="freddie",
        reason="canary passed",
        acceptance=BatchPromotionEvidence(
            engine="openclaw",
            batch_id="openclaw-canary-1",
            promotion_ready=True,
            report={
                "rollout_config_version": "2026-07-25T09:00:00+00:00",
                "promotion_ready": True,
            },
        ),
    )

    snapshot = operations.set_full_rollout(
        env=ENV,
        engine="openclaw",
        enabled=True,
        operator="freddie",
        reason="promote only openclaw",
    )

    assert snapshot.enable_all is False
    assert snapshot.full_rollout_engines == ("openclaw",)
    assert configs.config["param_value"]["full_rollout_engines"] == ["openclaw"]
    assert snapshot.audit_log[-1].action == "full_rollout:openclaw:enable"


def test_owner_full_rollout_requires_and_audits_latest_engine_acceptance() -> None:
    operations, configs, _, _ = build_operations(
        config=rollout_config(promoted_engines=["openclaw"])
    )

    with pytest.raises(RolloutOperationError, match="accepted openclaw batch"):
        operations.set_owner_full_rollout(
            env=ENV,
            owner_id=OWNER,
            engine="openclaw",
            enabled=True,
            acceptance_batch_id=None,
            operator="freddie",
            reason="too early",
        )

    operations.accept_batch(
        env=ENV,
        operator="freddie",
        reason="canary passed",
        acceptance=BatchPromotionEvidence(
            engine="openclaw",
            batch_id="openclaw-canary-1",
            promotion_ready=True,
            report={
                "rollout_config_version": "2026-07-25T09:00:00+00:00",
                "promotion_ready": True,
            },
        ),
    )
    snapshot = operations.set_owner_full_rollout(
        env=ENV,
        owner_id=OWNER,
        engine="openclaw",
        enabled=True,
        acceptance_batch_id="openclaw-canary-1",
        operator="freddie",
        reason="expand to all owner bots",
    )

    assert snapshot.full_rollout_owners == (
        RolloutOwnerEntry(owner_id=OWNER, engine="openclaw"),
    )
    assert configs.config is not None
    assert configs.config["param_value"]["full_rollout_owners"] == [
        {"owner_id": OWNER, "engine": "openclaw"}
    ]
    event = snapshot.audit_log[-1]
    assert event.action == f"owner_full_rollout:{OWNER}:openclaw:enable"
    assert event.batch_id == "openclaw-canary-1"


def test_owner_full_rollout_is_engine_scoped_and_can_be_disabled() -> None:
    operations, _, _, _ = build_operations(
        config=rollout_config(
            promoted_engines=["openclaw", "claude_code"],
            full_rollout_owners=[
                {"owner_id": OWNER, "engine": "openclaw"},
                {"owner_id": "owner-2", "engine": "claude_code"},
            ],
        )
    )

    snapshot = operations.set_owner_full_rollout(
        env=ENV,
        owner_id=OWNER,
        engine="openclaw",
        enabled=False,
        acceptance_batch_id=None,
        operator="freddie",
        reason="pause owner claims",
    )

    assert snapshot.full_rollout_owners == (
        RolloutOwnerEntry(owner_id="owner-2", engine="claude_code"),
    )
    assert snapshot.audit_log[-1].action == (
        f"owner_full_rollout:{OWNER}:openclaw:disable"
    )


def test_full_rollout_can_be_disabled_without_reverting_claimed_bots() -> None:
    operations, _, _, _ = build_operations(
        config=rollout_config(
            enable_all=True,
            promoted_engines=["openclaw"],
        )
    )

    snapshot = operations.set_full_rollout(
        env=ENV,
        enabled=False,
        operator="freddie",
        reason="pause new claims",
    )

    assert snapshot.enable_all is False
    assert snapshot.promoted_engines == ("openclaw",)


def test_engine_promotion_audit_does_not_depend_on_another_engine_batch() -> None:
    operations, _, _, _ = build_operations(
        config=rollout_config(
            promoted_engines=["openclaw"],
            whitelist=[
                {
                    "owner_id": OWNER,
                    "bot_id": BOT_ID,
                    "batch_id": "openclaw-unaccepted",
                }
            ],
        )
    )

    promoted = operations.promote_engine(
        env=ENV,
        engine="claude_code",
        operator="freddie",
        reason="test Claude Code independently",
    )

    assert promoted.promoted_engines == ("openclaw", "claude_code")
    event = promoted.audit_log[-1]
    assert event.reason == "test Claude Code independently"
    assert event.batch_id is None
    assert event.evidence is None
    assert event.effective_config_version == promoted.config_revision


def test_next_batch_requires_latest_persisted_acceptance() -> None:
    operations, _, _, _ = build_operations(
        config=rollout_config(promoted_engines=["openclaw"])
    )
    operations.accept_batch(
        env=ENV,
        operator="freddie",
        reason="canary passed",
        acceptance=BatchPromotionEvidence(
            engine="openclaw",
            batch_id="batch-1",
            promotion_ready=True,
            report={
                "rollout_config_version": "2026-07-25T09:00:00+00:00",
                "promotion_ready": True,
            },
        ),
    )

    with pytest.raises(RolloutOperationError, match="latest accepted batch"):
        operations.add_bot(
            env=ENV,
            owner_id=OWNER,
            bot_id=BOT_ID,
            batch_id="batch-2",
            acceptance_batch_id=None,
            operator="freddie",
            reason="expand",
        )

    result = operations.add_bot(
        env=ENV,
        owner_id=OWNER,
        bot_id=BOT_ID,
        batch_id="batch-2",
        acceptance_batch_id="batch-1",
        operator="freddie",
        reason="expand after acceptance",
    )

    assert result.snapshot.whitelist[0].batch_id == "batch-2"


def test_only_one_unaccepted_batch_can_be_open_per_engine() -> None:
    operations, _, _, _ = build_operations(
        config=rollout_config(
            promoted_engines=["openclaw"],
            whitelist=[
                {
                    "owner_id": OWNER,
                    "bot_id": BOT_ID,
                    "batch_id": "batch-1",
                }
            ],
        )
    )

    with pytest.raises(
        RolloutOperationError,
        match="current engine batch must be accepted",
    ):
        operations.add_bot(
            env=ENV,
            owner_id=OWNER,
            bot_id=BOT_ID,
            batch_id="batch-2",
            acceptance_batch_id=None,
            operator="freddie",
            reason="must not skip batch acceptance",
        )


def test_claimed_batch_stays_open_after_whitelist_removal() -> None:
    operations, _, _, layouts = build_operations(
        config=rollout_config(
            promoted_engines=["openclaw"],
            whitelist=[
                {
                    "owner_id": OWNER,
                    "bot_id": BOT_ID,
                    "batch_id": "batch-1",
                }
            ],
        )
    )
    layouts.state = replace(
        layouts.state,
        target_layout=SkillLayout.POOL,
        phase=SkillLayoutPhase.POOL_PREPARING,
        migration_generation="generation-1",
        persisted=True,
        rollout_evidence=RolloutEvidence(
            env=ENV,
            config_id=7,
            config_version="config-1",
            batch_id="batch-1",
            engine_type="openclaw",
            decision_reason="exact_bot_whitelist",
        ),
    )
    operations.remove_bot(
        env=ENV,
        owner_id=OWNER,
        bot_id=BOT_ID,
        operator="freddie",
        reason="remove claimed bot from whitelist",
    )

    with pytest.raises(
        RolloutOperationError,
        match="current engine batch must be accepted",
    ):
        operations.add_bot(
            env=ENV,
            owner_id=OWNER,
            bot_id=BOT_ID,
            batch_id="batch-2",
            acceptance_batch_id=None,
            operator="freddie",
            reason="must not bypass claimed batch",
        )


def test_other_engine_can_promote_while_existing_engine_batch_is_open() -> None:
    operations, _, _, _ = build_operations(
        config=rollout_config(promoted_engines=["openclaw"])
    )
    operations.accept_batch(
        env=ENV,
        operator="freddie",
        reason="first batch passed",
        acceptance=BatchPromotionEvidence(
            engine="openclaw",
            batch_id="batch-1",
            promotion_ready=True,
            report={
                "rollout_config_version": "2026-07-25T09:00:00+00:00",
                "promotion_ready": True,
            },
        ),
    )
    operations.add_bot(
        env=ENV,
        owner_id=OWNER,
        bot_id=BOT_ID,
        batch_id="batch-2",
        acceptance_batch_id="batch-1",
        operator="freddie",
        reason="open second batch",
    )

    promoted = operations.promote_engine(
        env=ENV,
        engine="claude_code",
        acceptance_batch_id="obsolete-cross-engine-acceptance",
        operator="freddie",
        reason="test engines in parallel",
    )

    assert promoted.promoted_engines == ("openclaw", "claude_code")
    assert promoted.whitelist[0].batch_id == "batch-2"
    assert promoted.audit_log[-1].batch_id is None
    assert promoted.audit_log[-1].evidence is None


def test_stale_batch_report_cannot_be_accepted() -> None:
    operations, _, _, _ = build_operations(
        config=rollout_config(promoted_engines=["openclaw"])
    )

    with pytest.raises(RolloutOperationError, match="report is stale"):
        operations.accept_batch(
            env=ENV,
            operator="freddie",
            reason="stale report",
            acceptance=BatchPromotionEvidence(
                engine="openclaw",
                batch_id="batch-1",
                promotion_ready=True,
                report={
                    "rollout_config_version": "old-revision",
                    "promotion_ready": True,
                },
            ),
        )


def test_config_cas_conflict_never_overwrites_a_concurrent_change() -> None:
    operations, configs, _, _ = build_operations(config=rollout_config())
    operations._repository.cas_succeeds = False

    with pytest.raises(RolloutOperationError, match="changed concurrently"):
        operations.promote_engine(
            env=ENV,
            engine="openclaw",
            operator="freddie",
            reason="start first engine",
        )

    assert configs.upserts == []


def test_operations_reject_unknown_rollout_config_keys() -> None:
    config = rollout_config()
    config["param_value"]["enable_pattern"] = "*"
    operations, _, _, _ = build_operations(config=config)

    with pytest.raises(RolloutOperationError, match="config is invalid"):
        operations.get_snapshot(env=ENV)


def test_removing_whitelist_reports_claimed_state_without_reverting_it() -> None:
    operations, _, _, layouts = build_operations(
        config=rollout_config(
            promoted_engines=["openclaw"],
            whitelist=[
                {
                    "owner_id": OWNER,
                    "bot_id": BOT_ID,
                    "batch_id": "openclaw-canary-1",
                }
            ],
        )
    )
    layouts.state = replace(
        layouts.state,
        active_layout=SkillLayout.LEGACY,
        target_layout=SkillLayout.POOL,
        phase=SkillLayoutPhase.POOL_PREPARING,
        migration_generation="generation-1",
        persisted=True,
    )

    result = operations.remove_bot(
        env=ENV,
        owner_id=OWNER,
        bot_id=BOT_ID,
        operator="freddie",
        reason="canary completed",
    )

    assert result.changed is True
    assert result.claimed_before is True
    assert result.claimed_after is True
    assert result.snapshot.whitelist == ()
    assert layouts.state.migration_generation == "generation-1"


def test_deleted_bot_can_still_be_removed_from_whitelist() -> None:
    operations, _, _, _ = build_operations(
        config=rollout_config(
            promoted_engines=["openclaw"],
            whitelist=[
                {
                    "owner_id": OWNER,
                    "bot_id": "deleted-bot",
                    "batch_id": "batch-1",
                }
            ],
        )
    )

    result = operations.remove_bot(
        env=ENV,
        owner_id=OWNER,
        bot_id="deleted-bot",
        operator="freddie",
        reason="remove orphaned whitelist entry",
    )

    assert result.changed is True
    assert result.claimed_before is False
    assert result.claimed_after is False
    assert result.snapshot.whitelist == ()


def test_deleted_bot_keeps_its_batch_open_until_operator_removes_it() -> None:
    operations, _, _, _ = build_operations(
        config=rollout_config(
            promoted_engines=["openclaw"],
            whitelist=[
                {
                    "owner_id": OWNER,
                    "bot_id": "deleted-bot",
                    "batch_id": "batch-1",
                }
            ],
        )
    )

    with pytest.raises(
        RolloutOperationError,
        match="current engine batch must be accepted",
    ):
        operations.add_bot(
            env=ENV,
            owner_id=OWNER,
            bot_id=BOT_ID,
            batch_id="batch-2",
            acceptance_batch_id=None,
            operator="freddie",
            reason="must clean orphaned batch first",
        )


def test_control_samples_are_explicit_and_never_enter_whitelist() -> None:
    operations, _, _, _ = build_operations(config=rollout_config())

    snapshot = operations.set_control_bot(
        env=ENV,
        owner_id=OWNER,
        bot_id=BOT_ID,
        batch_id="openclaw-canary-1",
        group=RolloutControlGroup.NEGATIVE,
        present=True,
        operator="freddie",
        reason="register negative control",
    )

    assert snapshot.whitelist == ()
    assert len(snapshot.negative_controls) == 1
    assert snapshot.negative_controls[0].owner_id == OWNER
    assert snapshot.negative_controls[0].bot_id == BOT_ID
    assert snapshot.negative_controls[0].batch_id == "openclaw-canary-1"
