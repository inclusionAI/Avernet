"""Deliver committed capability state to the current Bot Runtime."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from contextlib import AbstractContextManager, asynccontextmanager
from dataclasses import dataclass, replace
from functools import partial
from typing import Protocol

from agentclaw.community.core.bot_management.readiness import is_bot_ready
from agentclaw.community.core.devices.services.device_context import (
    RuntimeBindingChangedError,
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

from .passport_write_checkpoint import passport_write_checkpoint
from .capability_release_evidence import (
    CapabilityReleaseEvidence,
    component_converged,
    unresolved_retirements,
)


logger = get_logger()


class _RuntimeProjector(BotRuntimeProjectorProtocol, Protocol):
    async def apply_mcp_and_cli_plan(
        self, *, plan: ResolvedSkillPlan, scope: ProjectionScope,
    ) -> RuntimeProjectionResult: ...


@dataclass(slots=True)
class _ApplyDelivery:
    projector: _RuntimeProjector
    reconciler: CapabilityProjectionReconciler
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
            evidence = self.reconciler.release_evidence(binding)
            standalone = await asyncio.to_thread(evidence.load)
            if retry is not None:
                candidates = retry.retired_mappings
                releases = retry.released_mcp
                if scope.skills:
                    candidates = tuple(dict.fromkeys((*candidates, *standalone.retired_mappings)))
                if scope.mcp:
                    releases |= standalone.released_mcp
                    scope = replace(
                        scope, released_mcp=releases, updated_mcp=scope.updated_mcp | standalone.released_mcp,
                    )
                retry = replace(retry, retired_mappings=candidates, released_mcp=releases)
            plan = await asyncio.to_thread(
                self.projector.resolve_plan,
                bot_id=bot_id, owner_id=binding.owner_id, scope=scope,
            )
            if not is_bot_ready(plan.bot):
                return self._not_ready(), retry
            candidates = retry.retired_mappings if retry is not None else ()
            retired = retirement_request(candidates, plan.projection.skill_mappings) if scope.skills else []
            result = await self.reconciler.deliver(
                binding,
                partial(self.projector.apply_plan, plan=plan, retired_mappings=retired, scope=scope),
            )
            pending = unresolved_retirements(candidates, retired, result)
            releases = frozenset() if component_converged(result, "mcp") else scope.released_mcp
            remaining = replace(
                standalone,
                retired_mappings=(
                    tuple(mapping for mapping in pending if mapping in standalone.retired_mappings)
                    if scope.skills else standalone.retired_mappings
                ),
                released_mcp=(releases & standalone.released_mcp) if scope.mcp else standalone.released_mcp,
            )
            if remaining != standalone:
                await asyncio.to_thread(evidence.save, remaining)
        except RuntimeBindingChangedError:
            return _binding_changed(), retry
        except Exception:
            logger.exception("Apply capability delivery failed bot_id=%s", bot_id)
            return RuntimeProjectionResult.pending(
                code="CAPABILITY_PROJECTION_UNAVAILABLE",
                reason="Capability synchronization could not be confirmed.",
            ), retry

        if not (pending or releases) and result.status in {
            RuntimeProjectionStatus.CONVERGED, RuntimeProjectionStatus.SKIPPED,
        }:
            return result, None
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
        bots: BotRepository,
        bindings: DeviceBindingRepository,
        defer_enabled: bool,
    ) -> None:
        self._projector = projector
        self._bots = bots
        self._bindings = bindings
        self.defer_enabled = defer_enabled

    @asynccontextmanager
    async def deferred_apply(
        self, *, bot_id: str, entity_id: str,
        retry: CapabilityProjectionRetry | None,
    ) -> AsyncIterator[DeferredCapabilityProjection]:
        binding = await asyncio.to_thread(self.current_binding, bot_id, entity_id)
        previous = ()
        if binding is not None:
            previous = await self._projector.snapshot_skill_mappings(
                bot_id=bot_id, owner_id=binding.owner_id,
            )
        yield _ApplyDelivery(
            self._projector, self, CapabilityProjectionIntent(bot_id, entity_id),
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
        """Deliver standalone and lifecycle changes to the current Runtime."""
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
        if not self.defer_enabled and not (scope.requires_device_delivery or scope.passport):
            return RuntimeProjectionResult.skipped(reason="NO_CAPABILITY_PROJECTION")
        recorded_binding = None
        if self.defer_enabled:
            recorded_binding = await self._remember_releases(bot_id, entity_id, retired_mappings, scope.released_mcp)
        try:
            if not self.defer_enabled:
                return await self._await_delivery(legacy)
            binding = await asyncio.to_thread(self.current_binding, bot_id, entity_id)
            if binding is None:
                return _ApplyDelivery._not_ready()
            if recorded_binding is not None and binding != recorded_binding:
                return _binding_changed()
            return await self._deliver_standalone(
                binding, scope, retired_mappings,
                mcp_and_cli_only=mcp_and_cli_only, desktop_recovery=desktop_recovery,
            )
        except RuntimeBindingChangedError:
            return _binding_changed()

    async def remember_release_candidates(
        self, *, bot_id: str, entity_id: str, codes: frozenset[str],
        candidates: Sequence[PoolSkillMapping] = (),
        candidate_device_id: str | None = None,
    ) -> None:
        if not self.defer_enabled:
            return
        await self._remember_releases(
            bot_id, entity_id, candidates if candidate_device_id else (), codes,
            candidate_device_id=candidate_device_id,
        )

    async def _remember_releases(
        self, bot_id: str, entity_id: str, candidates: Sequence[PoolSkillMapping], released: frozenset[str],
        *, candidate_device_id: str | None = None,
    ) -> RuntimeBindingTarget | None:
        local = tuple(mapping for mapping in candidates if mapping.corpus == "local")
        if not local and not released:
            return None
        binding = await asyncio.to_thread(self.current_binding, bot_id, entity_id)
        if binding is not None:
            if candidate_device_id is not None and binding.device_id != candidate_device_id:
                local = ()
            if local or released:
                evidence = self.release_evidence(binding)
                await asyncio.to_thread(evidence.remember, local, released)
        return binding

    async def _deliver_standalone(
        self, binding: RuntimeBindingTarget, scope: ProjectionScope,
        retired_mappings: Sequence[PoolSkillMapping], *, mcp_and_cli_only: bool, desktop_recovery: bool,
    ) -> RuntimeProjectionResult:
        evidence = self.release_evidence(binding)
        previous = await asyncio.to_thread(evidence.load)
        if not (scope.requires_device_delivery or scope.passport):
            if not previous.released_mcp:
                return RuntimeProjectionResult.skipped(reason="NO_CAPABILITY_PROJECTION")
            # A repeated standalone uninstall has no remaining DB delta.
            scope = replace(scope, mcp=True)
        candidates = tuple(dict.fromkeys((*retired_mappings, *previous.retired_mappings))) if scope.skills else ()
        if scope.mcp:
            # The projector deletes absent codes and pushes the current config
            # if a previous release now has another source of supply.
            scope = replace(
                scope,
                released_mcp=scope.released_mcp | previous.released_mcp,
                updated_mcp=scope.updated_mcp | previous.released_mcp,
            )
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
            result = await self.deliver(binding, operation)
            return result
        finally:
            retired = previous.retired_mappings
            if scope.skills:
                retired = tuple(
                    mapping for mapping in unresolved_retirements(candidates, attempted, result)
                    if mapping.corpus == "local"
                )
            released = previous.released_mcp
            if scope.mcp:
                released = frozenset() if component_converged(result, "mcp") else scope.released_mcp
            remaining = replace(previous, retired_mappings=retired, released_mcp=released)
            if remaining != previous:
                await asyncio.to_thread(evidence.save, remaining)

    def release_evidence(self, binding: RuntimeBindingTarget) -> CapabilityReleaseEvidence:
        return CapabilityReleaseEvidence(self._bindings, binding)

    def current_binding(self, bot_id: str, entity_id: str) -> RuntimeBindingTarget | None:
        bot = self._bots.get_by_id_and_entity(bot_id, entity_id)
        if bot is None or bot.get("binding_id") is None:
            return None
        binding = self._bindings.get_active_by_bot_and_owner(bot_id, entity_id)
        if binding is None or binding.id != bot["binding_id"] or not binding.device_id:
            return None
        return RuntimeBindingTarget(bot_id, str(bot["owner_id"]), entity_id, binding.id, binding.device_id)

    async def deliver(
        self, binding: RuntimeBindingTarget,
        operation: Callable[[], Awaitable[RuntimeProjectionResult]],
    ) -> RuntimeProjectionResult:
        def verify_binding() -> bool:
            return self.current_binding(binding.bot_id, binding.entity_id) == binding

        def verify_passport_write() -> None:
            if not verify_binding():
                raise RuntimeBindingChangedError("Bot Runtime binding changed before Passport write")

        if not await asyncio.to_thread(verify_binding):
            return _binding_changed()
        with expect_runtime_binding(binding), passport_write_checkpoint(verify_passport_write):
            return await self._await_delivery(operation)

    @staticmethod
    async def _await_delivery(
        operation: Callable[[], Awaitable[RuntimeProjectionResult]],
    ) -> RuntimeProjectionResult:
        # Keep observing a started delivery until it finishes, even if its caller is cancelled.
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


def _binding_changed() -> RuntimeProjectionResult:
    return RuntimeProjectionResult.pending(
        code="CAPABILITY_BINDING_CHANGED",
        reason="The Bot Runtime binding changed before synchronization.",
    )
