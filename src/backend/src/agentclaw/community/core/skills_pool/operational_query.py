"""Read-only operational evidence views for Skills Pool rollout."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from injector import inject

from agentclaw.community.core.repository.protocols.bot import BotRepository
from agentclaw.community.core.repository.protocols.devices import DeviceBindingRepository
from agentclaw.community.core.skills_pool.claim_service import (
    SkillsPoolMigrationClaimService,
)
from agentclaw.community.core.skills_pool.operations import (
    RolloutBotEntry,
    SkillsPoolRolloutOperations,
)
from agentclaw.community.core.skills_pool.quarantine import (
    QuarantineOperationalView,
    SkillsPoolQuarantineService,
)
from agentclaw.community.core.repository.protocols.skills_pool import SkillsPoolLayoutRepositoryProtocol
from agentclaw.community.core.skills_pool.rollout_gate import (
    BotRuntimeForm,
    RolloutDecision,
    SkillsPoolRolloutGate,
)
from agentclaw.community.core.skills_pool.types import (
    BotSkillLayoutScope,
    BotSkillLayoutState,
    SkillLayout,
    SkillLayoutPhase,
)


class SkillsPoolOperationalQueryError(ValueError):
    """An operational query cannot resolve one exact Bot identity."""


@dataclass(frozen=True, slots=True)
class BotOperationalView:
    scope: BotSkillLayoutScope
    owner_id: str
    engine: str
    provider: str | None
    runtime_form: BotRuntimeForm
    claimed: bool
    rollout_decision: RolloutDecision
    state: BotSkillLayoutState
    quarantine: QuarantineOperationalView | None


@dataclass(frozen=True, slots=True)
class BatchOperationalReport:
    env: str
    engine: str
    batch_id: str
    rollout_config_id: int | None
    rollout_config_version: str | None
    rollout_enabled: bool
    engine_promoted: bool
    claim_config_versions: tuple[str, ...]
    configured: int
    invalid_batch_members: int
    eligible: int
    attempted: int
    claimed: int
    preparing: int
    active: int
    rolling_back: int
    failed: int
    success_rate: float | None
    data_consistent: bool
    negative_controls: int
    negative_controls_healthy: int
    teclaw_controls: int
    teclaw_controls_healthy: int
    quarantine_cleanup_eligible: int
    failure_distribution: dict[str, int]
    promotion_ready: bool


class SkillsPoolOperationalQuery:
    """Compose layout, runtime binding, rollout and quarantine evidence."""

    @inject
    def __init__(
        self,
        *,
        bot_repository: BotRepository,
        layout_repository: SkillsPoolLayoutRepositoryProtocol,
        binding_repository: DeviceBindingRepository,
        claim_service: SkillsPoolMigrationClaimService,
        rollout_gate: SkillsPoolRolloutGate,
        rollout_operations: SkillsPoolRolloutOperations,
        quarantine_service: SkillsPoolQuarantineService,
    ) -> None:
        self._bots = bot_repository
        self._layouts = layout_repository
        self._bindings = binding_repository
        self._claims = claim_service
        self._gate = rollout_gate
        self._rollout = rollout_operations
        self._quarantine = quarantine_service

    def get_bot(
        self,
        *,
        env: str,
        owner_id: str,
        bot_id: str,
    ) -> BotOperationalView:
        bot, scope = self._resolve_bot(
            env=env,
            owner_id=owner_id,
            bot_id=bot_id,
        )
        engine = bot.get("active_engine")
        if not isinstance(engine, str) or not engine:
            raise SkillsPoolOperationalQueryError("bot engine is invalid")
        runtime_form = self._claims.inspect_runtime_form(bot=bot, scope=scope)
        if runtime_form is None:
            raise SkillsPoolOperationalQueryError("bot runtime form is invalid")
        state = self._layouts.get(scope)
        binding = self._bindings.get_active_by_bot_and_owner(bot_id, owner_id)
        decision = self._gate.evaluate(
            env=env,
            owner_id=owner_id,
            bot_id=bot_id,
            engine_type=engine,
            runtime_form=runtime_form,
        )
        quarantine = (
            self._quarantine.inspect(scope, state.migration_generation)
            if state.migration_generation is not None
            else None
        )
        return BotOperationalView(
            scope=scope,
            owner_id=owner_id,
            engine=engine,
            provider=(
                str(binding.device_provider) if binding is not None else None
            ),
            runtime_form=runtime_form,
            claimed=self._is_claimed(state),
            rollout_decision=decision,
            state=state,
            quarantine=quarantine,
        )

    def summarize_batch(
        self,
        *,
        env: str,
        engine: str,
        batch_id: str,
    ) -> BatchOperationalReport:
        rollout = self._rollout.get_snapshot(env=env)
        whitelist = self._in_batch(rollout.whitelist, batch_id)
        eligible_scopes: set[BotSkillLayoutScope] = set()
        invalid_batch_members = 0
        for entry in whitelist:
            try:
                view = self.get_bot(
                    env=env,
                    owner_id=entry.owner_id,
                    bot_id=entry.bot_id,
                )
            except SkillsPoolOperationalQueryError:
                invalid_batch_members += 1
                continue
            if view.engine != engine or not view.rollout_decision.eligible:
                invalid_batch_members += 1
                continue
            eligible_scopes.add(view.scope)

        states: list[BotSkillLayoutState] = []
        for state in self._layouts.list_states(
            env=env,
            engine=engine,
            batch_id=batch_id,
        ):
            evidence = state.rollout_evidence
            if (
                evidence is not None
                and evidence.engine_type == engine
                and evidence.batch_id == batch_id
            ):
                states.append(state)
        active = sum(
            state.phase is SkillLayoutPhase.POOL_ACTIVE for state in states
        )
        active_scopes = {
            state.scope
            for state in states
            if state.phase is SkillLayoutPhase.POOL_ACTIVE
        }
        claim_config_versions = tuple(
            sorted(
                {
                    evidence.config_version
                    for state in states
                    if (evidence := state.rollout_evidence) is not None
                }
            )
        )
        claimed = sum(self._is_claimed(state) for state in states)
        failed_states = [
            state
            for state in states
            if state.phase is not SkillLayoutPhase.POOL_ACTIVE
            and (
                state.last_failure_code is not None
                or state.phase is SkillLayoutPhase.NEEDS_MANUAL_REPAIR
            )
        ]
        preparing = sum(
            state.phase
            in {
                SkillLayoutPhase.POOL_PREPARING,
                SkillLayoutPhase.POOL_READY,
                SkillLayoutPhase.POOL_ACTIVATING_PRE_CUTOVER,
                SkillLayoutPhase.POOL_CUTOVER_FINALIZING,
                SkillLayoutPhase.POOL_CUTOVER_COMMITTED,
            }
            for state in states
        )
        rolling_back = sum(
            state.phase
            in {
                SkillLayoutPhase.LEGACY_ROLLBACK_PREPARING,
                SkillLayoutPhase.LEGACY_ROLLBACK_COMMITTED,
            }
            for state in states
        )
        quarantine_eligible = 0
        for state in states:
            generation = state.migration_generation
            if generation is None:
                continue
            view = self._quarantine.inspect(state.scope, generation)
            if view is not None and view.eligible:
                quarantine_eligible += 1
        failures = Counter(
            state.last_failure_code or SkillLayoutPhase.NEEDS_MANUAL_REPAIR.value
            for state in failed_states
        )
        if invalid_batch_members:
            failures["BATCH_MEMBER_INVALID"] += invalid_batch_members
        data_consistent = not any(
            code.upper()
            in {
                "DATA_INCONSISTENT",
                "ROLLBACK_DATA_INCONSISTENT",
                "ACTIVE_ENTRY_CONFLICT",
                "MAPPING_DATA_INVALID",
                "BATCH_MEMBER_INVALID",
            }
            for code in failures
        )
        negative_controls = self._in_batch(
            rollout.negative_controls,
            batch_id,
        )
        teclaw_controls = self._in_batch(
            rollout.teclaw_controls,
            batch_id,
        )
        negative_healthy = sum(
            self._control_is_healthy(
                entry=entry,
                env=env,
                expected_engine=engine,
            )
            for entry in negative_controls
        )
        teclaw_healthy = sum(
            self._control_is_healthy(
                entry=entry,
                env=env,
                expected_engine="teclaw",
            )
            for entry in teclaw_controls
        )
        promotion_ready = (
            rollout.enabled
            and engine in rollout.promoted_engines
            and bool(states)
            and invalid_batch_members == 0
            and eligible_scopes.issubset(active_scopes)
            and len(states) == active
            and not failed_states
            and data_consistent
            and bool(negative_controls)
            and negative_healthy == len(negative_controls)
            and bool(teclaw_controls)
            and teclaw_healthy == len(teclaw_controls)
        )
        return BatchOperationalReport(
            env=env,
            engine=engine,
            batch_id=batch_id,
            rollout_config_id=rollout.config_id,
            rollout_config_version=rollout.config_version,
            rollout_enabled=rollout.enabled,
            engine_promoted=engine in rollout.promoted_engines,
            claim_config_versions=claim_config_versions,
            configured=len(whitelist),
            invalid_batch_members=invalid_batch_members,
            eligible=len(eligible_scopes),
            attempted=len(states),
            claimed=claimed,
            preparing=preparing,
            active=active,
            rolling_back=rolling_back,
            failed=len(failed_states),
            success_rate=active / len(states) if states else None,
            data_consistent=data_consistent,
            negative_controls=len(negative_controls),
            negative_controls_healthy=negative_healthy,
            teclaw_controls=len(teclaw_controls),
            teclaw_controls_healthy=teclaw_healthy,
            quarantine_cleanup_eligible=quarantine_eligible,
            failure_distribution=dict(failures),
            promotion_ready=promotion_ready,
        )

    def _control_is_healthy(
        self,
        *,
        entry: RolloutBotEntry,
        env: str,
        expected_engine: str,
    ) -> bool:
        try:
            view = self.get_bot(
                env=env,
                owner_id=entry.owner_id,
                bot_id=entry.bot_id,
            )
        except SkillsPoolOperationalQueryError:
            return False
        return (
            view.engine == expected_engine
            and not view.claimed
            and view.state.active_layout is SkillLayout.LEGACY
            and view.state.phase is SkillLayoutPhase.LEGACY_ACTIVE
            and not view.rollout_decision.eligible
        )

    def _resolve_bot(
        self,
        *,
        env: str,
        owner_id: str,
        bot_id: str,
    ) -> tuple[dict[str, object], BotSkillLayoutScope]:
        matches = self._bots.get_live_by_id_owner_and_env(
            bot_id=bot_id,
            owner_id=owner_id,
            env=env,
        )
        if len(matches) != 1:
            raise SkillsPoolOperationalQueryError(
                "bot not found" if not matches else "bot identity is ambiguous"
            )
        bot = matches[0]
        entity_id = bot.get("entity_id")
        if not isinstance(entity_id, (str, int)) or isinstance(entity_id, bool):
            raise SkillsPoolOperationalQueryError("bot entity identity is invalid")
        return bot, BotSkillLayoutScope(env, str(entity_id), bot_id)

    @staticmethod
    def _in_batch(
        entries: tuple[RolloutBotEntry, ...],
        batch_id: str,
    ) -> tuple[RolloutBotEntry, ...]:
        return tuple(entry for entry in entries if entry.batch_id == batch_id)

    @staticmethod
    def _is_claimed(state: BotSkillLayoutState) -> bool:
        return (
            state.active_layout is SkillLayout.POOL
            or state.target_layout is SkillLayout.POOL
        )
