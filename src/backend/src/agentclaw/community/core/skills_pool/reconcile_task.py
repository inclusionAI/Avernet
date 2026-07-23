"""Durable Skills Pool reconciliation task and lifecycle wake-up adapter."""

from __future__ import annotations

import asyncio
from uuid import uuid4

from agentclaw.community.core.bot_management.repository.protocol import (
    BotRepository,
)
from agentclaw.community.core.devices.repository.protocol import (
    DeviceBindingRepository,
)
from agentclaw.community.core.devices.repository.record import DeviceBindingRecord
from agentclaw.community.core.events.types import (
    BaasPublishCompletedEvent,
    DeviceAliveEvent,
)
from agentclaw.community.core.skill_center.services.runtime_layout_probe import (
    LAYOUT_CONTRACT_VERSION,
)
from agentclaw.community.core.skills_pool.claim_service import (
    MigrationClaimOutcome,
    SkillsPoolMigrationClaimService,
)
from agentclaw.community.core.skills_pool.reconcile_service import (
    SkillsPoolReconcileOutcome,
    SkillsPoolReconcileResult,
    SkillsPoolReconcileService,
)
from agentclaw.community.core.skills_pool.repository.protocol import (
    SkillsPoolLayoutRepositoryProtocol,
)
from agentclaw.community.core.skills_pool.types import (
    BotSkillLayoutScope,
    BotSkillLayoutState,
    SkillLayout,
    SkillLayoutPhase,
)
from agentclaw.community.core.task_queue.services.registry import (
    HandlerRegistry,
)
from agentclaw.community.core.task_queue.services.task_queue_service import (
    TaskQueueService,
)
from agentclaw.community.core.task_queue.types import (
    Complete,
    Fail,
    Reschedule,
    Retry,
    TaskOutcome,
)
from agentclaw.community.kernel.lifecycle import LifecycleBase
from agentclaw.community.log import get_logger

logger = get_logger()

SKILLS_POOL_RECONCILE_TASK = "skills_pool.reconcile"
SKILLS_POOL_RECONCILE_DEADLINE_SECONDS = 24 * 60 * 60
SKILLS_POOL_LEASE_SECONDS = 300
SKILLS_POOL_LEASE_BUSY_DELAY_SECONDS = 5.0


def build_skills_pool_reconcile_payload(
    *,
    scope: BotSkillLayoutScope,
    source: str,
    signal_identity: dict[str, object],
    wakeup_id: str | None = None,
) -> dict[str, object]:
    """Build the persisted work identity.

    ``scope`` is the only durable Bot identity. Provider-specific values are
    retained solely as audit evidence and are never forwarded to the runtime.
    """

    return {
        "scope": {
            "env": scope.env,
            "entity_id": scope.entity_id,
            "bot_id": scope.bot_id,
        },
        "source": source,
        "signal_identity": dict(signal_identity),
        "wakeup_id": wakeup_id or uuid4().hex,
    }


class SkillsPoolReconcileTaskHandler:
    """Claim or resume one Bot migration and converge it under generation/lease."""

    def __init__(
        self,
        *,
        claim_service: SkillsPoolMigrationClaimService,
        layout_repository: SkillsPoolLayoutRepositoryProtocol,
        reconcile_service: SkillsPoolReconcileService,
        lease_seconds: int = SKILLS_POOL_LEASE_SECONDS,
    ) -> None:
        self._claims = claim_service
        self._layouts = layout_repository
        self._reconcile = reconcile_service
        self._lease_seconds = lease_seconds

    @property
    def task_type(self) -> str:
        return SKILLS_POOL_RECONCILE_TASK

    def handle(self, payload: dict | None) -> TaskOutcome:
        try:
            scope, wakeup_id = self._parse_payload(payload)
        except ValueError as error:
            return Fail(f"invalid payload: {error}")

        lease_owner = f"skills-pool:{wakeup_id}"
        claim = self._claims.claim(
            scope=scope,
            layout_contract_version=LAYOUT_CONTRACT_VERSION,
            lease_owner=lease_owner,
            lease_seconds=self._lease_seconds,
        )

        if claim.outcome in {
            MigrationClaimOutcome.INELIGIBLE,
            MigrationClaimOutcome.BOT_NOT_FOUND,
            MigrationClaimOutcome.INVALID_BOT_RECORD,
            MigrationClaimOutcome.ENVIRONMENT_MISMATCH,
            MigrationClaimOutcome.RUNTIME_NOT_EDITABLE,
        }:
            return Complete()
        if claim.outcome is MigrationClaimOutcome.TRANSIENT_ERROR:
            return Retry("skills pool current runtime form lookup failed")
        if claim.outcome is MigrationClaimOutcome.CLAIM_RACE_LOST:
            return Retry("skills pool migration claim race lost")
        if claim.state is None:
            return Retry("skills pool migration claim returned no state")

        lease_outcome = self._ensure_lease(
            state=claim.state,
            lease_owner=lease_owner,
            newly_claimed=claim.outcome is MigrationClaimOutcome.CLAIMED,
        )
        if lease_outcome is not None:
            return lease_outcome

        result = asyncio.run(
            self._reconcile.reconcile(
                scope=scope,
                lease_owner=lease_owner,
            )
        )
        return self._task_outcome(result)

    def _ensure_lease(
        self,
        *,
        state: BotSkillLayoutState,
        lease_owner: str,
        newly_claimed: bool,
    ) -> TaskOutcome | None:
        if state.active_layout is SkillLayout.POOL:
            return Complete()
        if state.phase is SkillLayoutPhase.NEEDS_MANUAL_REPAIR:
            return Fail("skills pool migration requires manual repair")
        if newly_claimed:
            return None
        generation = state.migration_generation
        if generation is None:
            return Fail("claimed skills pool state has no migration generation")

        acquired = False
        if state.lease_owner == lease_owner:
            acquired = self._layouts.renew_lease(
                scope=state.scope,
                migration_generation=generation,
                lease_owner=lease_owner,
                lease_seconds=self._lease_seconds,
            )
        if not acquired:
            acquired = self._layouts.try_acquire_lease(
                scope=state.scope,
                migration_generation=generation,
                lease_owner=lease_owner,
                lease_seconds=self._lease_seconds,
            )
        if acquired:
            return None
        return Reschedule(SKILLS_POOL_LEASE_BUSY_DELAY_SECONDS)

    @staticmethod
    def _task_outcome(result: SkillsPoolReconcileResult) -> TaskOutcome:
        outcome = result.outcome
        if outcome in {
            SkillsPoolReconcileOutcome.POOL_ACTIVE,
            SkillsPoolReconcileOutcome.ALREADY_ACTIVE,
            SkillsPoolReconcileOutcome.NOT_CAPABLE,
            SkillsPoolReconcileOutcome.BOT_NOT_FOUND,
            SkillsPoolReconcileOutcome.BOT_CHANGED,
        }:
            return Complete()
        if outcome is SkillsPoolReconcileOutcome.LEASE_NOT_HELD:
            return Reschedule(SKILLS_POOL_LEASE_BUSY_DELAY_SECONDS)
        if outcome in {
            SkillsPoolReconcileOutcome.NOT_CLAIMED,
            SkillsPoolReconcileOutcome.TRANSIENT_ERROR,
            SkillsPoolReconcileOutcome.STATE_RACE_LOST,
            SkillsPoolReconcileOutcome.MAPPING_FAILED,
            SkillsPoolReconcileOutcome.MAPPING_VERIFY_FAILED,
            SkillsPoolReconcileOutcome.DATABASE_COMMIT_FAILED,
        }:
            return Retry(f"skills pool reconciliation {outcome.value}")
        if (
            outcome is SkillsPoolReconcileOutcome.CUTOVER_FAILED
            and result.retryable is True
        ):
            return Retry(f"skills pool reconciliation {outcome.value}")

        logger.error(
            "[skills_pool.reconcile] migration blocked outcome=%s evidence=%s",
            outcome.value,
            result.evidence,
        )
        return Fail(f"skills pool reconciliation blocked: {outcome.value}")

    @staticmethod
    def _parse_payload(
        payload: dict | None,
    ) -> tuple[BotSkillLayoutScope, str]:
        if not isinstance(payload, dict):
            raise ValueError("payload must be an object")
        raw_scope = payload.get("scope")
        if not isinstance(raw_scope, dict):
            raise ValueError("scope must be an object")
        values: dict[str, str] = {}
        for key in ("env", "entity_id", "bot_id"):
            value = raw_scope.get(key)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"scope.{key} must be a non-empty string")
            values[key] = value
        wakeup_id = payload.get("wakeup_id")
        if not isinstance(wakeup_id, str) or not wakeup_id.strip():
            raise ValueError("wakeup_id must be a non-empty string")
        source = payload.get("source")
        if not isinstance(source, str) or not source.strip():
            raise ValueError("source must be a non-empty string")
        if not isinstance(payload.get("signal_identity"), dict):
            raise ValueError("signal_identity must be an object")
        return BotSkillLayoutScope(**values), wakeup_id


class SkillsPoolReconcileWakeupListener(LifecycleBase):
    """Validate cheap provider identity and enqueue durable Bot reconciliation."""

    def __init__(
        self,
        *,
        binding_repository: DeviceBindingRepository,
        bot_repository: BotRepository,
        task_queue_service: TaskQueueService,
        registry: HandlerRegistry | None = None,
        task_handler: SkillsPoolReconcileTaskHandler | None = None,
    ) -> None:
        self._bindings = binding_repository
        self._bots = bot_repository
        self._queue = task_queue_service
        self._registry = registry
        self._task_handler = task_handler

    async def bootstrap(self) -> None:
        if self._registry is not None and self._task_handler is not None:
            self._registry.register(self._task_handler)
        from agentclaw.community.core.events.bus import get_event_bus

        bus = get_event_bus()
        for event_type in (DeviceAliveEvent, BaasPublishCompletedEvent):
            existing = bus._handlers.get(event_type, [])  # type: ignore[attr-defined]
            if self.handle not in existing:
                bus.subscribe(event_type, self.handle, required=True)

    def handle(
        self,
        event: DeviceAliveEvent | BaasPublishCompletedEvent,
    ) -> None:
        if isinstance(event, DeviceAliveEvent):
            self._handle_device_alive(event)
            return
        if isinstance(event, BaasPublishCompletedEvent):
            self._handle_baas_publish_completed(event)

    def _handle_device_alive(self, event: DeviceAliveEvent) -> None:
        # BaaS has a publish-id signal below. Avoid turning its generic ACTIVE
        # event into a second identity path.
        if event.device_provider != "arca":
            return
        binding = self._bindings.get_by_id(event.binding_id)
        if binding is None or binding.device_provider != "arca":
            return
        if (
            binding.device_id != event.device_id
            or binding.entity_id != event.entity_id
            or binding.entity_type != event.entity_type
        ):
            return
        current_sandbox = (binding.device_props or {}).get("sandbox_id")
        if event.sandbox_id is not None and current_sandbox != event.sandbox_id:
            return
        self._enqueue(
            binding=binding,
            source="arca_device_alive",
            signal_identity={
                "binding_id": event.binding_id,
                "device_id": event.device_id,
                "sandbox_id": event.sandbox_id,
            },
        )

    def _handle_baas_publish_completed(
        self,
        event: BaasPublishCompletedEvent,
    ) -> None:
        binding = self._bindings.get_by_id(event.binding_id)
        if binding is None or binding.device_provider != "baas":
            return
        prop_key = (
            "restart_publish_id" if event.publish_kind == "restart" else "publish_id"
        )
        current_publish_id = (binding.device_props or {}).get(prop_key)
        if current_publish_id is None or str(current_publish_id) != str(
            event.publish_id
        ):
            return
        bot = self._bots.get_by_binding_id(event.binding_id)
        if (
            bot is None
            or bot.get("bot_id") != event.bot_id
            or str(bot.get("owner_id")) != event.owner_id
        ):
            return
        self._enqueue(
            binding=binding,
            bot=bot,
            source="baas_publish_completed",
            signal_identity={
                "binding_id": event.binding_id,
                "publish_id": event.publish_id,
                "publish_kind": event.publish_kind,
            },
        )

    def _enqueue(
        self,
        *,
        binding: DeviceBindingRecord,
        source: str,
        signal_identity: dict[str, object],
        bot: dict[str, object] | None = None,
    ) -> None:
        current_bot = bot or self._bots.get_by_binding_id(binding.id)
        if current_bot is None:
            return
        bot_id = current_bot.get("bot_id")
        if not isinstance(bot_id, str) or not bot_id:
            return
        payload = build_skills_pool_reconcile_payload(
            scope=BotSkillLayoutScope(
                env=binding.env,
                entity_id=binding.entity_id,
                bot_id=bot_id,
            ),
            source=source,
            signal_identity=signal_identity,
        )
        self._queue.enqueue(
            SKILLS_POOL_RECONCILE_TASK,
            payload,
            deadline_seconds=SKILLS_POOL_RECONCILE_DEADLINE_SECONDS,
        )


__all__ = [
    "SKILLS_POOL_RECONCILE_TASK",
    "SkillsPoolReconcileTaskHandler",
    "SkillsPoolReconcileWakeupListener",
    "build_skills_pool_reconcile_payload",
]
