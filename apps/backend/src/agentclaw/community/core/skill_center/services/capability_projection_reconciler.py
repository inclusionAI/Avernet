"""Deliver committed capability state under the current Bot's delivery lease."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from contextlib import AbstractContextManager, asynccontextmanager
from dataclasses import dataclass, replace
from functools import partial
from typing import Protocol

from agentclaw.community.core.bot_management.readiness import is_bot_ready
from agentclaw.community.core.devices.services.device_context import (
    RuntimeBindingTarget,
    expect_runtime_binding,
)
from agentclaw.community.core.repository.protocols.bot import BotRepository
from agentclaw.community.core.repository.protocols.devices import DeviceBindingRepository
from agentclaw.community.core.skill_center.capability_projection_intent import (
    CapabilityProjectionIntent,
    capability_projection_intent,
    defer_capability_projection,
)
from agentclaw.community.core.skill_center.capability_projection_reconcile import (
    CapabilityProjectionRetry,
    DeferredCapabilityProjection,
)
from agentclaw.community.core.skill_center.runtime_projection_contract import (
    BotRuntimeProjectorProtocol,
    ProjectionScope,
    ResolvedSkillPlan,
    RuntimeProjectionResult,
    RuntimeProjectionStatus,
)
from agentclaw.community.core.skills_pool.mapping_intent import retirement_request
from agentclaw.community.core.skills_pool.models import PoolSkillMapping
from agentclaw.community.log import get_logger

from .capability_delivery_guard import (
    CapabilityDeliveryGuard,
    CapabilityDeliveryLease,
    CapabilityDeliveryUnavailable,
)
from .passport_write_checkpoint import passport_write_checkpoint
from .skill_retirement import SkillRetirementEvidence, unresolved_retirements


logger = get_logger()


class _RuntimeProjector(BotRuntimeProjectorProtocol, Protocol):
    async def apply_mcp_and_cli_plan(
        self, *, plan: ResolvedSkillPlan, scope: ProjectionScope,
    ) -> RuntimeProjectionResult: ...


@dataclass(slots=True)
class _ApplyDelivery:
    projector: _RuntimeProjector
    reconciler: CapabilityProjectionReconciler
    lease: CapabilityDeliveryLease
    intent: CapabilityProjectionIntent
    binding_before: RuntimeBindingTarget | None
    mappings_before: tuple[PoolSkillMapping, ...]
    retry: CapabilityProjectionRetry | None

    def write_scope(self) -> AbstractContextManager[None]:
        return defer_capability_projection(self.intent)

    async def finish(
        self, *, initialize: bool,
    ) -> tuple[RuntimeProjectionResult, CapabilityProjectionRetry | None]:
        scope = self.intent.scope()
        if not initialize and not (scope.requires_device_delivery or scope.passport):
            return RuntimeProjectionResult.skipped(reason="NO_COMMITTED_CAPABILITY_CHANGE"), self.retry

        bot_id, entity_id = self.intent.bot_id, self.intent.entity_id
        # Keep the old evidence if we cannot read the binding or resolve the
        # committed state. The next Apply still has to validate both.
        retry = self._retry_for(scope, self.binding_before)
        try:
            binding = await asyncio.to_thread(self.reconciler.current_binding, bot_id, entity_id)
            if binding is None or binding.device_id is None:
                return self._not_ready(), retry or self.retry
            retry = self._retry_for(scope, binding)
            if (
                not (scope.requires_device_delivery or scope.passport)
                or self.retry is not None
                or binding != self.binding_before
            ):
                scope = ProjectionScope.everything()
            if retry is not None:
                scope = replace(
                    scope,
                    skills=scope.skills or bool(retry.retired_mappings),
                    mcp=scope.mcp or bool(retry.released_mcp),
                    released_mcp=scope.released_mcp | retry.released_mcp,
                )
            evidence = self.reconciler.retirement_evidence(binding)
            standalone = await asyncio.to_thread(evidence.load) if scope.skills else ()
            if standalone and retry is not None:
                candidates = tuple(dict.fromkeys((*retry.retired_mappings, *standalone)))
                retry = replace(retry, retired_mappings=candidates)
            plan = await asyncio.to_thread(
                self.projector.resolve_plan,
                bot_id=bot_id, owner_id=binding.owner_id, scope=scope,
            )
            if not is_bot_ready(plan.bot):
                return self._not_ready(), retry
            candidates = retry.retired_mappings if retry is not None else ()
            retired = retirement_request(candidates, plan.projection.skill_mappings) if scope.skills else []
            result = await self.reconciler.deliver(
                self.lease, binding,
                partial(self.projector.apply_plan, plan=plan, retired_mappings=retired, scope=scope),
            )
            pending = unresolved_retirements(candidates, retired, result)
            if standalone:
                remaining = tuple(mapping for mapping in pending if mapping in standalone)
                if remaining != standalone:
                    await self.lease.verify()
                    await asyncio.to_thread(evidence.save, remaining)
        except CapabilityDeliveryUnavailable:
            return self.reconciler.delivery_busy(), retry
        except Exception:
            logger.exception("Apply capability delivery failed bot_id=%s", bot_id)
            return RuntimeProjectionResult.pending(
                code="CAPABILITY_PROJECTION_UNAVAILABLE",
                reason="Capability synchronization could not be confirmed.",
            ), retry

        if result.status is RuntimeProjectionStatus.CONVERGED:
            return result, None
        if result.status is RuntimeProjectionStatus.SKIPPED and not pending and not scope.released_mcp:
            return result, None
        releases = (
            frozenset() if result.components.get("mcp") is RuntimeProjectionStatus.CONVERGED
            else scope.released_mcp
        )
        return result, CapabilityProjectionRetry(binding.device_id, pending, releases)

    def _retry_for(
        self, scope: ProjectionScope, binding: RuntimeBindingTarget | None,
    ) -> CapabilityProjectionRetry | None:
        if binding is None or binding.device_id is None:
            return None
        same_device = self.binding_before is not None and binding.device_id == self.binding_before.device_id
        earlier = self.retry if self.retry is not None and self.retry.device_id == binding.device_id else None
        mappings = self.mappings_before if same_device and scope.skills else ()
        releases = scope.released_mcp if same_device else frozenset()
        if earlier is not None:
            mappings = tuple(dict.fromkeys((*mappings, *earlier.retired_mappings)))
            releases |= earlier.released_mcp
        return CapabilityProjectionRetry(binding.device_id, mappings, releases)

    @staticmethod
    def _not_ready() -> RuntimeProjectionResult:
        return RuntimeProjectionResult.pending(
            code="BOT_RUNTIME_NOT_READY", reason="The Bot Runtime is not ready.",
        )


class CapabilityProjectionReconciler:
    def __init__(
        self,
        *,
        projector: _RuntimeProjector,
        guard: CapabilityDeliveryGuard,
        bots: BotRepository,
        bindings: DeviceBindingRepository,
        defer_enabled: bool,
    ) -> None:
        self._projector = projector
        self._guard = guard
        self._bots = bots
        self._bindings = bindings
        self.defer_enabled = defer_enabled

    @asynccontextmanager
    async def deferred_apply(
        self, *, bot_id: str, entity_id: str,
        retry: CapabilityProjectionRetry | None,
    ) -> AsyncIterator[DeferredCapabilityProjection]:
        async with self._guard.hold(bot_id=bot_id, entity_id=entity_id) as lease:
            binding = await asyncio.to_thread(self.current_binding, bot_id, entity_id)
            previous = ()
            if binding is not None:
                previous = await self._projector.snapshot_skill_mappings(
                    bot_id=bot_id, owner_id=binding.owner_id,
                )
            yield _ApplyDelivery(
                self._projector, self, lease, CapabilityProjectionIntent(bot_id, entity_id),
                binding, previous, retry,
            )

    async def project_competing_change(
        self,
        *,
        bot_id: str,
        owner_id: str,
        entity_id: str | None,
        scope: ProjectionScope,
        retired_mappings: Sequence[PoolSkillMapping],
        legacy: Callable[[], Awaitable[RuntimeProjectionResult]],
        mcp_and_cli_only: bool = False,
        desktop_recovery: bool = False,
    ) -> RuntimeProjectionResult:
        """Run standalone configuration and lifecycle delivery on the same lease."""
        if entity_id is None:
            bot = await asyncio.to_thread(self._bots.get_by_id_and_owner, bot_id, owner_id)
            if bot is None:
                bot = await asyncio.to_thread(self._bots.get_by_id_and_entity, bot_id, owner_id)
            if bot is None:
                if self.defer_enabled:
                    return RuntimeProjectionResult.skipped(reason="CAPABILITY_PROJECTION_SUPERSEDED")
                return await legacy()
            entity_id = str(bot.get("entity_id") or owner_id)
        intent = capability_projection_intent(bot_id=bot_id, entity_id=entity_id)
        if intent is not None:
            return RuntimeProjectionResult.skipped(reason="DEFERRED_TO_MANIFEST_APPLY")
        if retired_mappings and not scope.skills:
            scope = replace(scope, skills=True)
        if not (scope.requires_device_delivery or scope.passport):
            return RuntimeProjectionResult.skipped(reason="NO_CAPABILITY_PROJECTION")
        try:
            async with self._guard.hold(bot_id=bot_id, entity_id=entity_id) as lease:
                if not self.defer_enabled:
                    await lease.verify()
                    return await self._await_delivery(legacy)
                binding = await asyncio.to_thread(self.current_binding, bot_id, entity_id)
                if binding is None:
                    return _ApplyDelivery._not_ready()
                return await self._deliver_standalone(
                    lease, binding, scope, retired_mappings,
                    mcp_and_cli_only=mcp_and_cli_only, desktop_recovery=desktop_recovery,
                )
        except CapabilityDeliveryUnavailable:
            return self.delivery_busy()

    async def _deliver_standalone(
        self, lease: CapabilityDeliveryLease, binding: RuntimeBindingTarget, scope: ProjectionScope,
        retired_mappings: Sequence[PoolSkillMapping], *, mcp_and_cli_only: bool, desktop_recovery: bool,
    ) -> RuntimeProjectionResult:
        evidence = self.retirement_evidence(binding)
        previous = await asyncio.to_thread(evidence.load) if scope.skills else ()
        candidates = tuple(dict.fromkeys((*retired_mappings, *previous)))
        attempted: list[PoolSkillMapping] = []
        result = None
        try:
            plan = await asyncio.to_thread(
                self._projector.resolve_plan, bot_id=binding.bot_id, owner_id=binding.owner_id, scope=scope,
            )
            recovering_pending_desktop = (
                desktop_recovery and plan.bot.get("bot_type") == "desktop"
                and str(plan.bot.get("status") or "").upper() == "PENDING"
            )
            if not is_bot_ready(plan.bot) and not recovering_pending_desktop:
                return _ApplyDelivery._not_ready()
            if mcp_and_cli_only:
                operation = partial(self._projector.apply_mcp_and_cli_plan, plan=plan, scope=scope)
            else:
                attempted = retirement_request(candidates, plan.projection.skill_mappings)
                operation = partial(self._projector.apply_plan, plan=plan, retired_mappings=attempted, scope=scope)
            result = await self.deliver(lease, binding, operation)
            return result
        finally:
            remaining = tuple(
                mapping for mapping in unresolved_retirements(candidates, attempted, result)
                if mapping.corpus == "local"
            )
            if remaining != previous:
                await lease.verify()
                await asyncio.to_thread(evidence.save, remaining)

    def retirement_evidence(self, binding: RuntimeBindingTarget) -> SkillRetirementEvidence:
        return SkillRetirementEvidence(self._bindings, binding)

    def current_binding(self, bot_id: str, entity_id: str) -> RuntimeBindingTarget | None:
        bot = self._bots.get_by_id_and_entity(bot_id, entity_id)
        if bot is None or bot.get("binding_id") is None:
            return None
        binding = self._bindings.get_active_by_bot_and_owner(bot_id, entity_id)
        if binding is None or binding.id != bot["binding_id"] or not binding.device_id:
            return None
        return RuntimeBindingTarget(bot_id, str(bot["owner_id"]), entity_id, binding.id, binding.device_id)

    async def deliver(
        self, lease: CapabilityDeliveryLease, binding: RuntimeBindingTarget,
        operation: Callable[[], Awaitable[RuntimeProjectionResult]],
    ) -> RuntimeProjectionResult:
        def verify_binding() -> bool:
            return self.current_binding(binding.bot_id, binding.entity_id) == binding

        def verify_passport_write() -> None:
            if not verify_binding():
                raise CapabilityDeliveryUnavailable()
            lease.verify_from_worker()

        if not await asyncio.to_thread(verify_binding):
            return RuntimeProjectionResult.pending(
                code="CAPABILITY_BINDING_CHANGED",
                reason="The Bot Runtime binding changed before synchronization.",
            )
        await lease.verify()
        with expect_runtime_binding(binding), passport_write_checkpoint(verify_passport_write):
            return await self._await_delivery(operation)

    @staticmethod
    async def _await_delivery(
        operation: Callable[[], Awaitable[RuntimeProjectionResult]],
    ) -> RuntimeProjectionResult:
        # Let an active request finish under its lease even if the caller is cancelled.
        delivery = asyncio.gather(operation(), return_exceptions=True)
        cancellation = None
        while not delivery.done():
            try:
                await asyncio.shield(delivery)
            except asyncio.CancelledError as exc:
                cancellation = exc
        if cancellation is not None:
            raise cancellation
        (result,) = delivery.result()
        if isinstance(result, BaseException):
            raise result
        return result

    @staticmethod
    def delivery_busy() -> RuntimeProjectionResult:
        return RuntimeProjectionResult.pending(
            code="CAPABILITY_DELIVERY_BUSY",
            reason="Capability configuration is awaiting the shared Runtime delivery lease.",
        )
