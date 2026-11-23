"""Exact identities still needed after an incomplete standalone Skill delivery."""

from collections.abc import Sequence

from agentclaw.community.core.devices.repository.record import skill_retirement_property
from agentclaw.community.core.devices.services.device_context import RuntimeBindingTarget
from agentclaw.community.core.repository.protocols.devices import DeviceBindingRepository
from agentclaw.community.core.skill_center.runtime_projection_contract import (
    RuntimeProjectionResult,
    RuntimeProjectionStatus,
)
from agentclaw.community.core.skills_pool.mapping_intent import logical_skill_mappings_from_evidence
from agentclaw.community.core.skills_pool.models import PoolSkillMapping
from agentclaw.community.utils.avernet_tenant import get_current_avernet_tenant

from .capability_delivery_guard import CapabilityDeliveryUnavailable


def unresolved_retirements(
    candidates: tuple[PoolSkillMapping, ...],
    attempted: Sequence[PoolSkillMapping],
    result: RuntimeProjectionResult | None,
) -> tuple[PoolSkillMapping, ...]:
    if result is None:
        return candidates
    if result.components.get("skills") is RuntimeProjectionStatus.CONVERGED:
        return ()
    if result.delivery_uncertain:
        return candidates
    if result.status is RuntimeProjectionStatus.CONVERGED:
        return ()
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


class SkillRetirementEvidence:
    """Use the current device binding's existing private metadata."""

    def __init__(self, bindings: DeviceBindingRepository, target: RuntimeBindingTarget) -> None:
        self._bindings = bindings
        self._target = target
        self._key = skill_retirement_property(
            tenant=get_current_avernet_tenant(), bot_id=target.bot_id, entity_id=target.entity_id,
        )

    def load(self) -> tuple[PoolSkillMapping, ...]:
        if self._target.binding_id is None:
            raise CapabilityDeliveryUnavailable()
        binding = self._bindings.get_by_id(self._target.binding_id)
        if binding is None or binding.device_id != self._target.device_id:
            raise CapabilityDeliveryUnavailable()
        stored = binding.device_props.get(self._key)
        if stored is None:
            return ()
        if not isinstance(stored, dict):
            raise ValueError("Invalid standalone Skill retirement evidence")
        if stored.get("device_id") != self._target.device_id:
            return ()
        items = stored.get("retired_mappings")
        if not isinstance(items, list):
            raise ValueError("Invalid standalone Skill retirement evidence")
        return tuple(
            candidate for item in items
            for candidate in logical_skill_mappings_from_evidence({"retired_mappings": [item]})
        )

    def save(self, pending: tuple[PoolSkillMapping, ...]) -> None:
        if self._target.binding_id is None:
            raise CapabilityDeliveryUnavailable()
        stored = {
            "device_id": self._target.device_id,
            "retired_mappings": [mapping.to_dict() for mapping in pending],
        } if pending else None
        self._bindings.update_device_props(
            binding_id=self._target.binding_id, props={self._key: stored},
        )
