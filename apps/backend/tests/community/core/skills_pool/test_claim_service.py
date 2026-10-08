"""Skills Pool 首次迁移认领服务测试。"""

from __future__ import annotations

from types import SimpleNamespace

from agentclaw.community.core.skills_pool.claim_service import (
    MigrationClaimOutcome,
    SkillsPoolMigrationClaimService,
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


class FakeBotRepository:
    def __init__(self, bot: dict[str, object] | None) -> None:
        self.bot = bot

    def get_by_id_and_entity(
        self, bot_id: str, entity_id: str
    ) -> dict[str, object] | None:
        return self.bot


class FakeBotPublishRepository:
    def __init__(self, draft: object | None = None) -> None:
        self.draft = draft

    def get_draft_by_publish_bot_id(
        self,
        publish_bot_id: str,
        env: str,
    ) -> object | None:
        return self.draft


class FailingBotPublishRepository:
    def get_draft_by_publish_bot_id(self, publish_bot_id: str, env: str):
        raise RuntimeError("database unavailable")


class FakeLayoutRepository:
    def __init__(self) -> None:
        self.state: BotSkillLayoutState | None = None
        self.claim_calls = 0

    def get(self, scope: BotSkillLayoutScope) -> BotSkillLayoutState:
        return self.state or BotSkillLayoutState.legacy_default(scope)

    def claim_pool_migration(
        self,
        *,
        scope: BotSkillLayoutScope,
        layout_contract_version: str,
        migration_generation: str,
        rollout_evidence: RolloutEvidence,
        lease_owner: str,
        lease_seconds: int,
    ) -> BotSkillLayoutState | None:
        self.claim_calls += 1
        if self.state is not None:
            return None
        self.state = BotSkillLayoutState(
            scope=scope,
            active_layout=SkillLayout.LEGACY,
            target_layout=SkillLayout.POOL,
            phase=SkillLayoutPhase.POOL_PREPARING,
            migration_generation=migration_generation,
            persisted=True,
            layout_contract_version=layout_contract_version,
            lease_owner=lease_owner,
            rollout_evidence=rollout_evidence,
        )
        return self.state


class RecordingGate:
    def __init__(self, decision: RolloutDecision) -> None:
        self.decision = decision
        self.calls: list[dict[str, object]] = []

    def evaluate(self, **kwargs: object) -> RolloutDecision:
        self.calls.append(kwargs)
        return self.decision


def eligible_decision() -> RolloutDecision:
    return RolloutDecision(
        eligible=True,
        reason=RolloutDecisionReason.ELIGIBLE,
        evidence=RolloutEvidence(
            env="pre",
            config_id=42,
            config_version="2026-07-23T12:00:00",
            batch_id="batch-1",
            engine_type="openclaw",
            decision_reason="eligible",
        ),
    )


def personal_bot(*, engine: str = "openclaw") -> dict[str, object]:
    return {
        "id": 101,
        "bot_id": "bot-1",
        "entity_id": "entity-1",
        "owner_id": "owner-from-db",
        "active_engine": engine,
        "bot_type": "personal",
        "env": "pre",
    }


def claim(
    service: SkillsPoolMigrationClaimService,
):
    return service.claim(
        scope=BotSkillLayoutScope(
            env="pre",
            entity_id="entity-1",
            bot_id="bot-1",
        ),
        layout_contract_version="skills-pool-v1",
        lease_owner="worker-1",
        lease_seconds=60,
    )


def test_claim_derives_owner_and_engine_from_current_bot_record() -> None:
    layouts = FakeLayoutRepository()
    gate = RecordingGate(eligible_decision())
    service = SkillsPoolMigrationClaimService(
        FakeBotRepository(personal_bot()),
        FakeBotPublishRepository(),
        layouts,
        gate,
    )

    result = claim(service)

    assert result.outcome is MigrationClaimOutcome.CLAIMED
    assert result.state is not None
    assert result.state.migration_generation
    assert layouts.claim_calls == 1
    assert gate.calls == [
        {
            "env": "pre",
            "owner_id": "owner-from-db",
            "bot_id": "bot-1",
            "engine_type": "openclaw",
            "runtime_form": BotRuntimeForm.PERSONAL,
        }
    ]


def test_claim_is_sticky_after_whitelist_removal() -> None:
    layouts = FakeLayoutRepository()
    gate = RecordingGate(eligible_decision())
    service = SkillsPoolMigrationClaimService(
        FakeBotRepository(personal_bot()),
        FakeBotPublishRepository(),
        layouts,
        gate,
    )
    first = claim(service)
    gate.decision = RolloutDecision(
        eligible=False,
        reason=RolloutDecisionReason.BOT_NOT_WHITELISTED,
    )

    repeated = claim(service)

    assert first.outcome is MigrationClaimOutcome.CLAIMED
    assert repeated.outcome is MigrationClaimOutcome.ALREADY_CLAIMED
    assert repeated.state == first.state
    assert len(gate.calls) == 1
    assert layouts.claim_calls == 1


def test_claimed_service_generation_stops_when_draft_is_no_longer_editable() -> None:
    bot = {**personal_bot(), "bot_type": "service"}
    drafts = FakeBotPublishRepository(
        SimpleNamespace(
            source_bot_pk=bot["id"],
            source_bot_id=bot["bot_id"],
            publish_bot_id=bot["bot_id"],
            status="draft",
            env=bot["env"],
        )
    )
    layouts = FakeLayoutRepository()
    gate = RecordingGate(eligible_decision())
    service = SkillsPoolMigrationClaimService(
        FakeBotRepository(bot),
        drafts,
        layouts,
        gate,
    )

    first = claim(service)
    drafts.draft = None
    repeated = claim(service)

    assert first.outcome is MigrationClaimOutcome.CLAIMED
    assert repeated.outcome is MigrationClaimOutcome.RUNTIME_NOT_EDITABLE
    assert repeated.state == first.state
    assert len(gate.calls) == 1


def test_claimed_service_generation_retries_current_draft_lookup_failure() -> None:
    bot = {**personal_bot(), "bot_type": "service"}
    layouts = FakeLayoutRepository()
    layouts.state = BotSkillLayoutState(
        scope=BotSkillLayoutScope(
            env="pre",
            entity_id="entity-1",
            bot_id="bot-1",
        ),
        active_layout=SkillLayout.LEGACY,
        target_layout=SkillLayout.POOL,
        phase=SkillLayoutPhase.POOL_PREPARING,
        migration_generation="generation-1",
        persisted=True,
    )
    service = SkillsPoolMigrationClaimService(
        FakeBotRepository(bot),
        FailingBotPublishRepository(),
        layouts,
        RecordingGate(eligible_decision()),
    )

    result = claim(service)

    assert result.outcome is MigrationClaimOutcome.TRANSIENT_ERROR
    assert result.state == layouts.state


def test_ineligible_bot_does_not_persist_layout_state() -> None:
    layouts = FakeLayoutRepository()
    gate = RecordingGate(
        RolloutDecision(
            eligible=False,
            reason=RolloutDecisionReason.BOT_NOT_WHITELISTED,
        )
    )
    service = SkillsPoolMigrationClaimService(
        FakeBotRepository(personal_bot()),
        FakeBotPublishRepository(),
        layouts,
        gate,
    )

    result = claim(service)

    assert result.outcome is MigrationClaimOutcome.INELIGIBLE
    assert result.state is not None
    assert result.state.phase is SkillLayoutPhase.LEGACY_ACTIVE
    assert not result.state.persisted
    assert layouts.claim_calls == 0


def test_published_service_and_teclaw_do_not_claim() -> None:
    published_layouts = FakeLayoutRepository()
    published_gate = RecordingGate(eligible_decision())
    published_service = SkillsPoolMigrationClaimService(
        FakeBotRepository({**personal_bot(), "bot_type": "service"}),
        FakeBotPublishRepository(draft=None),
        published_layouts,
        published_gate,
    )

    published_result = claim(published_service)

    assert published_result.outcome is MigrationClaimOutcome.RUNTIME_NOT_EDITABLE
    assert published_layouts.claim_calls == 0
    assert published_gate.calls == []

    teclaw_layouts = FakeLayoutRepository()
    teclaw_gate = RecordingGate(
        RolloutDecision(
            eligible=False,
            reason=RolloutDecisionReason.ENGINE_NOT_SUPPORTED,
        )
    )
    teclaw_service = SkillsPoolMigrationClaimService(
        FakeBotRepository(personal_bot(engine="teclaw")),
        FakeBotPublishRepository(),
        teclaw_layouts,
        teclaw_gate,
    )

    teclaw_result = claim(teclaw_service)

    assert teclaw_result.outcome is MigrationClaimOutcome.INELIGIBLE
    assert teclaw_layouts.claim_calls == 0


def test_service_draft_form_is_derived_from_current_publish_record() -> None:
    bot = {**personal_bot(), "bot_type": "service"}
    draft = SimpleNamespace(
        source_bot_pk=bot["id"],
        source_bot_id=bot["bot_id"],
        publish_bot_id=bot["bot_id"],
        status="draft",
        env=bot["env"],
    )
    gate = RecordingGate(eligible_decision())
    service = SkillsPoolMigrationClaimService(
        FakeBotRepository(bot),
        FakeBotPublishRepository(draft),
        FakeLayoutRepository(),
        gate,
    )

    result = claim(service)

    assert result.outcome is MigrationClaimOutcome.CLAIMED
    assert gate.calls[0]["runtime_form"] is BotRuntimeForm.SERVICE_DRAFT


def test_desktop_form_enters_the_existing_rollout_gate() -> None:
    bot = {**personal_bot(), "bot_type": "desktop"}
    gate = RecordingGate(eligible_decision())
    service = SkillsPoolMigrationClaimService(
        FakeBotRepository(bot),
        FakeBotPublishRepository(),
        FakeLayoutRepository(),
        gate,
    )

    result = claim(service)

    assert result.outcome is MigrationClaimOutcome.CLAIMED
    assert gate.calls[0]["runtime_form"] is BotRuntimeForm.DESKTOP


def test_ineligible_desktop_keeps_the_unpersisted_legacy_state() -> None:
    layouts = FakeLayoutRepository()
    gate = RecordingGate(
        RolloutDecision(
            eligible=False,
            reason=RolloutDecisionReason.CONFIG_DISABLED,
        )
    )
    service = SkillsPoolMigrationClaimService(
        FakeBotRepository({**personal_bot(), "bot_type": "desktop"}),
        FakeBotPublishRepository(),
        layouts,
        gate,
    )

    result = claim(service)

    assert result.outcome is MigrationClaimOutcome.INELIGIBLE
    assert result.state is not None
    assert result.state.phase is SkillLayoutPhase.LEGACY_ACTIVE
    assert result.state.persisted is False
    assert layouts.claim_calls == 0
    assert gate.calls[0]["runtime_form"] is BotRuntimeForm.DESKTOP


def test_scope_environment_must_match_current_bot_record() -> None:
    service = SkillsPoolMigrationClaimService(
        FakeBotRepository({**personal_bot(), "env": "prod"}),
        FakeBotPublishRepository(),
        FakeLayoutRepository(),
        RecordingGate(eligible_decision()),
    )

    result = claim(service)

    assert result.outcome is MigrationClaimOutcome.ENVIRONMENT_MISMATCH
