"""Skills Pool operational evidence queries."""

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

from agentclaw.community.core.skills_pool.operational_query import (
    SkillsPoolOperationalQuery,
)
from agentclaw.community.core.skills_pool.operations import (
    RolloutBotEntry,
    RolloutConfigSnapshot,
)
from agentclaw.community.core.skills_pool.rollout_gate import (
    BotRuntimeForm,
    RolloutDecision,
    RolloutDecisionReason,
)
from agentclaw.community.core.skills_pool.types import (
    BotSkillLayoutScope,
    BotSkillLayoutState,
    RolloutEvidence,
    SkillLayout,
    SkillLayoutPhase,
)


SCOPE = BotSkillLayoutScope(env="pre", entity_id="owner-1", bot_id="bot-1")


class FakeBots:
    default_records = [
        {
                "bot_id": "bot-1",
                "owner_id": "owner-1",
                "entity_id": "owner-1",
                "env": "pre",
                "bot_type": "personal",
                "active_engine": "openclaw",
        },
        {
            "bot_id": "negative-1",
            "owner_id": "owner-2",
            "entity_id": "owner-2",
            "env": "pre",
            "bot_type": "personal",
            "active_engine": "openclaw",
        },
        {
            "bot_id": "teclaw-1",
            "owner_id": "owner-3",
            "entity_id": "owner-3",
            "env": "pre",
            "bot_type": "personal",
            "active_engine": "teclaw",
        },
    ]

    def __init__(
        self,
        records: list[dict[str, object]] | None = None,
    ) -> None:
        self.records = records or self.default_records

    def get_live_by_id_owner_and_env(self, **kwargs: object):
        return [
            record
            for record in self.records
            if record["bot_id"] == kwargs["bot_id"]
            and record["owner_id"] == kwargs["owner_id"]
            and record["env"] == kwargs["env"]
        ]

    def get_by_id_and_entity(self, bot_id: str, entity_id: str):
        return next(
            (
                record
                for record in self.records
                if record["bot_id"] == bot_id
                and record["entity_id"] == entity_id
            ),
            None,
        )


class FakeLayouts:
    def __init__(self) -> None:
        self.state = replace(
            BotSkillLayoutState.legacy_default(SCOPE),
            active_layout=SkillLayout.POOL,
            phase=SkillLayoutPhase.POOL_ACTIVE,
            migration_generation="generation-1",
            preparation_id="preparation-1",
            layout_contract_version="skills-pool-p3-v1",
            last_probe_result="READY",
            persisted=True,
            rollout_evidence=RolloutEvidence(
                env="pre",
                config_id=7,
                config_version="v7",
                batch_id="batch-1",
                engine_type="openclaw",
                decision_reason="eligible",
            ),
        )

    def get(self, scope: BotSkillLayoutScope) -> BotSkillLayoutState:
        if scope == SCOPE:
            return self.state
        return BotSkillLayoutState.legacy_default(scope)

    def list_states(
        self,
        *,
        env: str,
        engine: str | None = None,
        batch_id: str | None = None,
    ) -> list[BotSkillLayoutState]:
        assert env == "pre"
        assert engine == "openclaw"
        assert batch_id == "batch-1"
        return [self.state]


class FakeBindings:
    def get_active_by_bot_and_owner(self, bot_id: str, owner_id: str):
        provider = "baas" if bot_id == "teclaw-1" else "arca"
        return SimpleNamespace(device_provider=provider, status="ACTIVE")


class FakeClaims:
    def inspect_runtime_form(self, **_: object) -> BotRuntimeForm:
        return BotRuntimeForm.PERSONAL


class FakeGate:
    def __init__(self, eligible_bot_ids: set[str] | None = None) -> None:
        self.eligible_bot_ids = eligible_bot_ids or {"bot-1"}

    def evaluate(self, **kwargs: object) -> RolloutDecision:
        if kwargs["bot_id"] in self.eligible_bot_ids:
            return RolloutDecision(True, RolloutDecisionReason.ELIGIBLE)
        reason = (
            RolloutDecisionReason.ENGINE_NOT_SUPPORTED
            if kwargs["engine_type"] == "teclaw"
            else RolloutDecisionReason.BOT_NOT_WHITELISTED
        )
        return RolloutDecision(False, reason)


class FakeRollout:
    def __init__(
        self,
        whitelist: tuple[RolloutBotEntry, ...] | None = None,
    ) -> None:
        self.whitelist = (
            (RolloutBotEntry("owner-1", "bot-1", "batch-1"),)
            if whitelist is None
            else whitelist
        )

    def get_snapshot(self, *, env: str) -> RolloutConfigSnapshot:
        return RolloutConfigSnapshot(
            env=env,
            config_id=7,
            config_version="v7",
            record_version="2026-07-25T10:00:00",
            config_revision="v7",
            enabled=True,
            enable_all=False,
            promoted_engines=("openclaw",),
            whitelist=self.whitelist,
            negative_controls=(
                RolloutBotEntry("owner-2", "negative-1", "batch-1"),
            ),
            teclaw_controls=(
                RolloutBotEntry("owner-3", "teclaw-1", "batch-1"),
            ),
            audit_log=(),
        )


class FakeQuarantine:
    def inspect(self, scope: BotSkillLayoutScope, generation: str):
        assert (scope, generation) == (SCOPE, "generation-1")
        return SimpleNamespace(
            eligible=True,
            blockers=(),
            eligible_at=None,
            age=None,
            record=SimpleNamespace(
                status="retained",
                runtime_evidence={"source": "arca_device_alive"},
            ),
        )


def build_query(
    layouts: FakeLayouts | None = None,
    *,
    bots: FakeBots | None = None,
    gate: FakeGate | None = None,
    rollout: FakeRollout | None = None,
) -> SkillsPoolOperationalQuery:
    return SkillsPoolOperationalQuery(
        bot_repository=bots or FakeBots(),
        layout_repository=layouts or FakeLayouts(),
        binding_repository=FakeBindings(),
        claim_service=FakeClaims(),
        rollout_gate=gate or FakeGate(),
        rollout_operations=rollout or FakeRollout(),
        quarantine_service=FakeQuarantine(),
    )


def test_single_bot_query_exposes_current_runtime_and_migration_evidence() -> None:
    view = build_query().get_bot(
        env="pre",
        owner_id="owner-1",
        bot_id="bot-1",
    )

    assert view.engine == "openclaw"
    assert view.provider == "arca"
    assert view.runtime_form is BotRuntimeForm.PERSONAL
    assert view.claimed is True
    assert view.state.phase is SkillLayoutPhase.POOL_ACTIVE
    assert view.state.preparation_id == "preparation-1"
    assert view.quarantine is not None
    assert view.quarantine.eligible is True


def test_batch_query_reports_migration_and_explicit_control_counts() -> None:
    report = build_query().summarize_batch(
        env="pre",
        engine="openclaw",
        batch_id="batch-1",
    )

    assert report.eligible == 1
    assert report.configured == 1
    assert report.invalid_batch_members == 0
    assert report.attempted == 1
    assert report.claimed == 1
    assert report.preparing == 0
    assert report.active == 1
    assert report.rolling_back == 0
    assert report.failed == 0
    assert report.negative_controls == 1
    assert report.negative_controls_healthy == 1
    assert report.teclaw_controls == 1
    assert report.teclaw_controls_healthy == 1
    assert report.quarantine_cleanup_eligible == 1
    assert report.rollout_config_id == 7
    assert report.rollout_config_version == "v7"
    assert report.rollout_enabled is True
    assert report.engine_promoted is True
    assert report.claim_config_versions == ("v7",)
    assert report.success_rate == 1.0
    assert report.data_consistent is True
    assert report.failure_distribution == {}
    assert report.promotion_ready is True


def test_missing_whitelist_member_blocks_batch_acceptance() -> None:
    report = build_query(
        rollout=FakeRollout(
            (
                RolloutBotEntry("owner-1", "bot-1", "batch-1"),
                RolloutBotEntry("missing-owner", "missing-bot", "batch-1"),
            )
        ),
    ).summarize_batch(
        env="pre",
        engine="openclaw",
        batch_id="batch-1",
    )

    assert report.configured == 2
    assert report.invalid_batch_members == 1
    assert report.data_consistent is False
    assert report.failure_distribution == {"BATCH_MEMBER_INVALID": 1}
    assert report.promotion_ready is False


def test_wrong_engine_whitelist_member_blocks_batch_acceptance() -> None:
    report = build_query(
        rollout=FakeRollout(
            (
                RolloutBotEntry("owner-1", "bot-1", "batch-1"),
                RolloutBotEntry("owner-3", "teclaw-1", "batch-1"),
            )
        ),
    ).summarize_batch(
        env="pre",
        engine="openclaw",
        batch_id="batch-1",
    )

    assert report.configured == 2
    assert report.invalid_batch_members == 1
    assert report.data_consistent is False
    assert report.failure_distribution == {"BATCH_MEMBER_INVALID": 1}
    assert report.promotion_ready is False


def test_data_inconsistency_blocks_manual_batch_promotion() -> None:
    layouts = FakeLayouts()
    layouts.state = replace(
        layouts.state,
        active_layout=SkillLayout.LEGACY,
        target_layout=SkillLayout.POOL,
        phase=SkillLayoutPhase.POOL_PREPARING,
        last_failure_code="DATA_INCONSISTENT",
    )

    report = build_query(layouts).summarize_batch(
        env="pre",
        engine="openclaw",
        batch_id="batch-1",
    )

    assert report.success_rate == 0.0
    assert report.failure_distribution == {"DATA_INCONSISTENT": 1}
    assert report.data_consistent is False
    assert report.promotion_ready is False


def test_resolved_historical_failure_does_not_keep_active_bot_failed() -> None:
    layouts = FakeLayouts()
    layouts.state = replace(
        layouts.state,
        last_failure_code="MAPPING_PUBLISH_FAILED",
        last_failure_stage="mapping_publish",
        last_failure_retryable=True,
    )

    report = build_query(layouts).summarize_batch(
        env="pre",
        engine="openclaw",
        batch_id="batch-1",
    )

    assert report.active == 1
    assert report.failed == 0
    assert report.failure_distribution == {}
    assert report.promotion_ready is True


def test_promotion_matches_exact_eligible_and_active_bot_scopes() -> None:
    bot_b = {
        "bot_id": "bot-2",
        "owner_id": "owner-4",
        "entity_id": "owner-4",
        "env": "pre",
        "bot_type": "personal",
        "active_engine": "openclaw",
    }
    query = build_query(
        bots=FakeBots([*FakeBots.default_records, bot_b]),
        gate=FakeGate({"bot-2"}),
        rollout=FakeRollout(
            (RolloutBotEntry("owner-4", "bot-2", "batch-1"),)
        ),
    )

    report = query.summarize_batch(
        env="pre",
        engine="openclaw",
        batch_id="batch-1",
    )

    assert report.eligible == 1
    assert report.active == 1
    assert report.promotion_ready is False


def test_completed_batch_remains_promotable_after_canary_whitelist_cleanup() -> None:
    report = build_query(
        rollout=FakeRollout(whitelist=()),
    ).summarize_batch(
        env="pre",
        engine="openclaw",
        batch_id="batch-1",
    )

    assert report.eligible == 0
    assert report.attempted == 1
    assert report.active == 1
    assert report.promotion_ready is True
