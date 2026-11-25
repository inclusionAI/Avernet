"""Capability release evidence for incomplete standalone delivery."""

from collections.abc import Sequence
from uuid import uuid4

from agentclaw.community.core.devices.repository.record import capability_release_property
from agentclaw.community.core.devices.services.device_context import (
    RuntimeBindingChangedError,
    RuntimeBindingTarget,
)
from agentclaw.community.core.repository.protocols.devices import DeviceBindingRepository
from agentclaw.community.core.skill_center.capability_projection_reconcile import CapabilityProjectionRetry
from agentclaw.community.core.skill_center.runtime_projection_contract import (
    RuntimeProjectionResult,
    RuntimeProjectionStatus,
)
from agentclaw.community.core.skills_pool.models import PoolSkillMapping
from agentclaw.community.utils.avernet_tenant import get_current_avernet_tenant


def component_converged(result: RuntimeProjectionResult | None, name: str) -> bool:
    if result is None:
        return False
    status = result.components.get(name)
    if status is not None:
        return status is RuntimeProjectionStatus.CONVERGED
    return result.status is RuntimeProjectionStatus.CONVERGED and not result.delivery_uncertain


def unresolved_retirements(
    candidates: tuple[PoolSkillMapping, ...],
    attempted: Sequence[PoolSkillMapping],
    result: RuntimeProjectionResult | None,
) -> tuple[PoolSkillMapping, ...]:
    if component_converged(result, "skills"):
        return ()
    if result is None or result.delivery_uncertain:
        return candidates
    # The Runtime confirmed one identity does not match. Another recorded
    # identity for that target can be checked on the next attempt.
    mismatches = {
        issue.name for issue in result.issues
        if issue.requested_action == "RETIRE" and issue.code == "RETIRED_TARGET_IDENTITY_MISMATCH"
    }
    return tuple(
        mapping for mapping in candidates
        if not (
            mapping in attempted and mapping.link_name in mismatches
            and any(other.link_name == mapping.link_name and other != mapping for other in candidates)
        )
    )


class CapabilityReleaseEvidence:
    """Use the current device binding's existing private metadata."""

    def __init__(self, bindings: DeviceBindingRepository, target: RuntimeBindingTarget) -> None:
        if target.binding_id is None or target.device_id is None:
            raise RuntimeBindingChangedError("Bot Runtime binding is unavailable for release evidence")
        self._bindings = bindings
        self._binding_id = target.binding_id
        self._device_id = target.device_id
        self._key = capability_release_property(
            tenant=get_current_avernet_tenant(), bot_id=target.bot_id, entity_id=target.entity_id,
        )

    def _decode(self, raw: object) -> CapabilityProjectionRetry:
        stored = CapabilityProjectionRetry.from_payload(raw)
        if stored is not None and stored.device_id == self._device_id:
            return stored
        return CapabilityProjectionRetry(self._device_id)

    def load(self) -> CapabilityProjectionRetry:
        binding = self._bindings.get_by_id(self._binding_id)
        if binding is None or binding.device_id != self._device_id:
            raise RuntimeBindingChangedError("Bot Runtime binding is unavailable for release evidence")
        self._snapshot = binding.device_props.get(self._key)
        return self._decode(self._snapshot)

    def remember(
        self, retired: Sequence[PoolSkillMapping], released: frozenset[str],
    ) -> None:
        def merge(raw: object) -> dict[str, object]:
            previous = self._decode(raw)
            evidence = CapabilityProjectionRetry(
                self._device_id,
                tuple(dict.fromkeys((*retired, *previous.retired_mappings))),
                previous.released_mcp | released,
            )
            # A new commit can need the same release as an in-flight send.
            return {**evidence.to_payload(), "revision": uuid4().hex}

        self._bindings.mutate_device_property(
            binding_id=self._binding_id, device_id=self._device_id, key=self._key, mutate=merge,
        )

    def save(self, pending: CapabilityProjectionRetry) -> None:
        stored = pending.to_payload() if pending.retired_mappings or pending.released_mcp else None
        # A completed delivery can acknowledge only the evidence it read.
        self._bindings.mutate_device_property(
            binding_id=self._binding_id, device_id=self._device_id, key=self._key,
            mutate=lambda current: stored if current == self._snapshot else current,
        )
