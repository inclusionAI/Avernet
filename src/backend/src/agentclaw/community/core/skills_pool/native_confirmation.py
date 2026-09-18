"""Confirm Pool-native initialization from authenticated startup evidence."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from injector import inject

from agentclaw.community.core.devices.protocols import (
    LayoutInitializationConflictError,
    LayoutInitializationConfirmationError,
    LayoutInitializationEvidenceError,
)
from agentclaw.community.core.repository.protocols.skills_pool import (
    SkillsPoolLayoutRepositoryProtocol,
)
from agentclaw.community.core.skill_center.services.runtime_layout_probe import (
    LAYOUT_CONTRACT_VERSION,
)
from agentclaw.community.core.skills_pool.types import (
    BotSkillLayoutScope,
    SkillLayout,
    is_migrated_pool_active_state,
    is_pool_native_state,
)


PoolNativeLayoutConfirmationError = LayoutInitializationConfirmationError


_EVIDENCE_FIELDS = {
    "actual_engine",
    "actual_layout",
    "layout_contract_version",
    "roots_initialized",
}


@dataclass(frozen=True, slots=True)
class PoolLayoutInitializationEvidence:
    actual_engine: str
    actual_layout: str
    layout_contract_version: str
    roots_initialized: bool

    @classmethod
    def from_mapping(
        cls, value: Mapping[str, object]
    ) -> PoolLayoutInitializationEvidence:
        return cls(
            actual_engine=str(value.get("actual_engine") or ""),
            actual_layout=str(value.get("actual_layout") or ""),
            layout_contract_version=str(value.get("layout_contract_version") or ""),
            roots_initialized=value.get("roots_initialized") is True,
        )


class SkillsPoolNativeLayoutConfirmationService:
    """Advance only a matching ``pool_initializing`` row by repository CAS."""

    @inject
    def __init__(
        self,
        layout_repository: SkillsPoolLayoutRepositoryProtocol,
    ) -> None:
        self._layouts = layout_repository

    def confirm(
        self,
        *,
        binding_id: int,
        startup_identity: str,
        env: str,
        entity_id: str,
        bot_id: str,
        expected_engine: str,
        evidence: dict[str, object],
    ) -> None:
        scope = BotSkillLayoutScope(env=env, entity_id=entity_id, bot_id=bot_id)
        if set(evidence) != _EVIDENCE_FIELDS:
            raise LayoutInitializationEvidenceError(
                "layout evidence fields do not match the contract"
            )
        observed = PoolLayoutInitializationEvidence.from_mapping(evidence)
        if expected_engine != "openclaw" or observed.actual_engine != expected_engine:
            raise LayoutInitializationEvidenceError(
                "layout engine does not match Bot"
            )
        if observed.actual_layout != SkillLayout.POOL.value:
            raise LayoutInitializationEvidenceError("reported layout is not pool")
        if observed.layout_contract_version != LAYOUT_CONTRACT_VERSION:
            raise LayoutInitializationEvidenceError(
                "layout contract version is unsupported"
            )
        if not observed.roots_initialized:
            raise LayoutInitializationEvidenceError(
                "root-level Pool initialization is incomplete"
            )

        state = self._layouts.get(scope)
        migrated_active = is_migrated_pool_active_state(
            state,
            layout_contract_version=observed.layout_contract_version,
        )
        pool_native = is_pool_native_state(
            state,
            layout_contract_version=observed.layout_contract_version,
            engine_type=expected_engine,
        )
        if not migrated_active and not pool_native:
            raise LayoutInitializationEvidenceError(
                "persisted layout is not confirmable Pool-native state"
            )
        if not self._layouts.confirm_pool_initializing(
            scope=scope,
            layout_contract_version=observed.layout_contract_version,
            binding_id=binding_id,
            startup_identity=startup_identity,
        ):
            raise LayoutInitializationConflictError(
                "Pool-native layout confirmation CAS did not commit"
            )


__all__ = [
    "PoolLayoutInitializationEvidence",
    "PoolNativeLayoutConfirmationError",
    "SkillsPoolNativeLayoutConfirmationService",
]
