"""Skills Pool 首次迁移认领的应用服务。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from uuid import uuid4

from injector import inject

from agentclaw.community.core.repository.protocols.bot import BotRepository
from agentclaw.community.core.repository.protocols.publishing import BotPublishRepositoryProtocol
from agentclaw.community.core.service_bot.repository.models import PublishStatus
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
)
from agentclaw.community.log import get_logger

logger = get_logger()


class MigrationClaimOutcome(StrEnum):
    """一次认领请求的稳定业务结果。"""

    CLAIMED = "claimed"
    ALREADY_CLAIMED = "already_claimed"
    INELIGIBLE = "ineligible"
    BOT_NOT_FOUND = "bot_not_found"
    INVALID_BOT_RECORD = "invalid_bot_record"
    ENVIRONMENT_MISMATCH = "environment_mismatch"
    RUNTIME_NOT_EDITABLE = "runtime_not_editable"
    TRANSIENT_ERROR = "transient_error"
    CLAIM_RACE_LOST = "claim_race_lost"


@dataclass(frozen=True, slots=True)
class MigrationClaimResult:
    """认领结果；未认领时仍返回当前 Legacy 缺省状态。"""

    outcome: MigrationClaimOutcome
    state: BotSkillLayoutState | None
    rollout_decision: RolloutDecision | None = None


class SkillsPoolMigrationClaimService:
    """由当前 Bot 事实派生 owner/engine，并原子认领一次迁移。"""

    @inject
    def __init__(
        self,
        bot_repository: BotRepository,
        bot_publish_repository: BotPublishRepositoryProtocol,
        layout_repository: SkillsPoolLayoutRepositoryProtocol,
        rollout_gate: SkillsPoolRolloutGate,
    ) -> None:
        self._bot_repository = bot_repository
        self._bot_publish_repository = bot_publish_repository
        self._layout_repository = layout_repository
        self._rollout_gate = rollout_gate

    @staticmethod
    def _has_pool_migration(state: BotSkillLayoutState) -> bool:
        return (
            state.active_layout is SkillLayout.POOL
            or state.target_layout is SkillLayout.POOL
        )

    def resolve_runtime_form(
        self,
        *,
        bot: dict[str, object],
        scope: BotSkillLayoutScope,
    ) -> BotRuntimeForm | None:
        """从持久化事实派生运行形态，不接受调用方自报草稿/ONLINE。"""

        bot_type = bot.get("bot_type")
        if bot_type == "personal":
            return BotRuntimeForm.PERSONAL
        if bot_type == "desktop":
            return BotRuntimeForm.DESKTOP
        if bot_type != "service":
            return None

        try:
            draft = self._bot_publish_repository.get_draft_by_publish_bot_id(
                publish_bot_id=scope.bot_id,
                env=scope.env,
            )
        except Exception as error:
            logger.exception(
                "[skills_pool.claim] service draft lookup failed "
                "env=%s entity_id=%s bot_id=%s",
                scope.env,
                scope.entity_id,
                scope.bot_id,
            )
            raise _RuntimeFormLookupError from error
        if draft is None:
            return None
        if (
            draft.status != PublishStatus.DRAFT
            or draft.env != scope.env
            or draft.publish_bot_id != scope.bot_id
            or draft.source_bot_id != scope.bot_id
            or draft.source_bot_pk != bot.get("id")
        ):
            return None
        return BotRuntimeForm.SERVICE_DRAFT

    def inspect_runtime_form(
        self,
        *,
        bot: dict[str, object],
        scope: BotSkillLayoutScope,
    ) -> BotRuntimeForm | None:
        """Resolve an observable form, including non-editable published services."""

        runtime_form = self.resolve_runtime_form(bot=bot, scope=scope)
        if runtime_form is None and bot.get("bot_type") == "service":
            return BotRuntimeForm.PUBLISHED_SERVICE
        return runtime_form

    def claim(
        self,
        *,
        scope: BotSkillLayoutScope,
        layout_contract_version: str,
        lease_owner: str,
        lease_seconds: int,
    ) -> MigrationClaimResult:
        """首次命中 gate 时提交 generation；已认领状态不再重读白名单。"""

        current = self._layout_repository.get(scope)
        if current.active_layout is SkillLayout.POOL:
            return MigrationClaimResult(
                outcome=MigrationClaimOutcome.ALREADY_CLAIMED,
                state=current,
            )

        bot = self._bot_repository.get_by_id_and_entity(
            scope.bot_id,
            scope.entity_id,
        )
        if bot is None:
            return MigrationClaimResult(
                outcome=MigrationClaimOutcome.BOT_NOT_FOUND,
                state=current,
            )

        if bot.get("env") != scope.env:
            return MigrationClaimResult(
                outcome=MigrationClaimOutcome.ENVIRONMENT_MISMATCH,
                state=current,
            )

        try:
            runtime_form = self.resolve_runtime_form(bot=bot, scope=scope)
        except _RuntimeFormLookupError:
            return MigrationClaimResult(
                outcome=MigrationClaimOutcome.TRANSIENT_ERROR,
                state=current,
            )
        if runtime_form is None:
            return MigrationClaimResult(
                outcome=MigrationClaimOutcome.RUNTIME_NOT_EDITABLE,
                state=current,
            )
        if self._has_pool_migration(current):
            return MigrationClaimResult(
                outcome=MigrationClaimOutcome.ALREADY_CLAIMED,
                state=current,
            )

        owner_id = bot.get("owner_id")
        engine_type = bot.get("active_engine")
        if (
            not isinstance(owner_id, (str, int))
            or isinstance(owner_id, bool)
            or not str(owner_id).strip()
            or not isinstance(engine_type, str)
            or not engine_type
        ):
            return MigrationClaimResult(
                outcome=MigrationClaimOutcome.INVALID_BOT_RECORD,
                state=current,
            )

        decision = self._rollout_gate.evaluate(
            env=scope.env,
            owner_id=str(owner_id),
            bot_id=scope.bot_id,
            engine_type=engine_type,
            runtime_form=runtime_form,
        )
        if not decision.eligible or decision.evidence is None:
            return MigrationClaimResult(
                outcome=MigrationClaimOutcome.INELIGIBLE,
                state=current,
                rollout_decision=decision,
            )

        claimed = self._layout_repository.claim_pool_migration(
            scope=scope,
            layout_contract_version=layout_contract_version,
            migration_generation=str(uuid4()),
            rollout_evidence=decision.evidence,
            lease_owner=lease_owner,
            lease_seconds=lease_seconds,
        )
        if claimed is not None:
            return MigrationClaimResult(
                outcome=MigrationClaimOutcome.CLAIMED,
                state=claimed,
                rollout_decision=decision,
            )

        raced = self._layout_repository.get(scope)
        if self._has_pool_migration(raced):
            return MigrationClaimResult(
                outcome=MigrationClaimOutcome.ALREADY_CLAIMED,
                state=raced,
            )
        return MigrationClaimResult(
            outcome=MigrationClaimOutcome.CLAIM_RACE_LOST,
            state=raced,
            rollout_decision=decision,
        )


class _RuntimeFormLookupError(RuntimeError):
    """Current service-draft facts could not be read temporarily."""
