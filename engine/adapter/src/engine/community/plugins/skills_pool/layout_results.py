"""Skill layout transition and mapping result contracts."""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from engine.community.core.skills.layout_planner import (
    LayoutIdentity,
    RuntimeLayoutContext,
    SkillLayoutResolutionError,
    resolve_filesystem_skill_layout,
    resolve_local_skill_locators,
    resolved_filesystem_layout_evidence,
)
from engine.community.plugins.skills_pool.layout_probe import LAYOUT_CONTRACT_VERSION


class PoolActivationStatus(StrEnum):
    COMMITTED = "COMMITTED"
    ALREADY_COMMITTED = "ALREADY_COMMITTED"
    ACTIVE_ENTRY_CONFLICT = "ACTIVE_ENTRY_CONFLICT"
    DATA_INCONSISTENT = "DATA_INCONSISTENT"
    INVALID = "INVALID"
    TRANSIENT_ERROR = "TRANSIENT_ERROR"
    POST_CUTOVER_SYNC_PENDING = "POST_CUTOVER_SYNC_PENDING"
    NOT_ATOMIC = "NOT_ATOMIC"


class MappingSourceLayout(StrEnum):
    """Authority layout that mapping sources must belong to."""

    POOL = "pool"
    LEGACY = "legacy"


class MappingApplyMode(StrEnum):
    """How one Mapping request handles per-entry runtime drift.

    Pool layout transitions stay fail-closed by omitting this field.  Product
    capability mutations explicitly opt into ``BEST_EFFORT`` so a user-owned
    active-root entry cannot block unrelated, safe mapping updates.
    """

    STRICT = "STRICT"
    BEST_EFFORT = "BEST_EFFORT"


class MappingProjectionStatus(StrEnum):
    CONVERGED = "CONVERGED"
    PENDING = "PENDING"
    DEGRADED = "DEGRADED"


@dataclass(frozen=True, slots=True)
class MappingItemResult:
    """One mapping or retirement result emitted by the Engine contract."""

    target: str
    source: str | None
    status: MappingProjectionStatus
    code: str | None = None
    retryable: bool = False
    action: str = "APPLY"
    mapping: dict[str, str] | None = None

    def to_data(self) -> dict[str, object]:
        data: dict[str, object] = {
            "target": self.target,
            "status": self.status.value,
            "retryable": self.retryable,
            "action": self.action,
        }
        if self.source is not None:
            data["source"] = self.source
        if self.code is not None:
            data["code"] = self.code
        if self.mapping is not None:
            data["mapping"] = self.mapping
        return data


@dataclass(frozen=True, slots=True)
class MappingApplyResult:
    """Logical steady-state result; physical paths are diagnostic only."""

    status: MappingProjectionStatus
    items: tuple[MappingItemResult, ...]
    issues: tuple[MappingItemResult, ...] = ()
    evidence: dict[str, object] = field(default_factory=dict)

    def to_data(self) -> dict[str, object]:
        return {
            "status": self.status.value,
            "items": [item.to_data() for item in self.items],
            "issues": [item.to_data() for item in self.issues],
            "evidence": self.evidence,
        }


class ActiveRepoRetirementError(RuntimeError):
    """A runtime-owned active-root corpus could not be safely retired."""

    def __init__(self, reason: str, **evidence: object) -> None:
        super().__init__(reason)
        self.reason = reason
        self.evidence = evidence


class ActiveRepoRestorationError(RuntimeError):
    """A runtime-owned Legacy active-root corpus could not be restored."""

    def __init__(self, reason: str, **evidence: object) -> None:
        super().__init__(reason)
        self.reason = reason
        self.evidence = evidence


@dataclass(frozen=True, slots=True)
class SkillMapping:
    source: str
    target: str


@dataclass(frozen=True, slots=True)
class PoolActivationResult:
    status: PoolActivationStatus
    evidence: dict[str, object]

    @property
    def committed(self) -> bool:
        return self.status in {
            PoolActivationStatus.COMMITTED,
            PoolActivationStatus.ALREADY_COMMITTED,
        }

    def to_data(self) -> dict[str, object]:
        return {
            "committed": self.committed,
            "status": self.status.value,
            "evidence": self.evidence,
        }


@dataclass(frozen=True, slots=True)
class MappingVerificationResult:
    valid: bool
    evidence: dict[str, object]
    status: MappingProjectionStatus = MappingProjectionStatus.CONVERGED
    items: tuple[MappingItemResult, ...] = ()

    def to_data(self) -> dict[str, object]:
        return {
            "valid": self.valid,
            "status": self.status.value,
            "items": [item.to_data() for item in self.items],
            "evidence": self.evidence,
        }


@dataclass(frozen=True, slots=True)
class MappingPublishResult:
    published: bool
    evidence: dict[str, object]
    status: MappingProjectionStatus = MappingProjectionStatus.CONVERGED
    items: tuple[MappingItemResult, ...] = ()

    def to_data(self) -> dict[str, object]:
        return {
            "published": self.published,
            "status": self.status.value,
            "items": [item.to_data() for item in self.items],
            "evidence": self.evidence,
        }

    def item_for(self, *, target: Path) -> MappingItemResult:
        """Return one exact target result for focused Engine conformance tests."""

        normalized = str(Path(os.path.abspath(target)))
        for item in self.items:
            if item.target == normalized:
                return item
        raise KeyError(normalized)


@dataclass(frozen=True, slots=True)
class _MappingPlan:
    managed: dict[Path, Path]
    external: tuple[Path, ...]
    failures: tuple[dict[str, str], ...]
    conflicts: tuple[dict[str, str], ...]


@dataclass(frozen=True, slots=True)
class _MappingRetirementPlan:
    remove: tuple[Path, ...]
    absent: tuple[Path, ...]
    replaced: tuple[Path, ...]
    external: tuple[Path, ...]
    failures: tuple[dict[str, str], ...]


@dataclass(frozen=True, slots=True)
class _PostCutoverFinalization:
    post_sync: dict[str, object]
    cleanup_pending: bool
    failure: PoolActivationResult | None = None


def _with_resolution_evidence(
    result_factory: Callable[[], PoolActivationResult],
    *,
    engine: str,
    source_layout: MappingSourceLayout,
    registered_local_names: list[str],
    home: str | Path,
) -> PoolActivationResult:
    try:
        plan = resolve_filesystem_skill_layout(
            LayoutIdentity(
                engine_type=engine,
                layout_contract_version=LAYOUT_CONTRACT_VERSION,
            ),
            RuntimeLayoutContext(home=Path(home)),
        )
        local_root = (
            plan.pool_local
            if source_layout is MappingSourceLayout.POOL
            else plan.legacy_local
        )
        repo_root = (
            plan.pool_repo
            if source_layout is MappingSourceLayout.POOL
            else plan.legacy_repo
        )
        local_locators = resolve_local_skill_locators(
            local_root,
            registered_local_names,
        )
    except SkillLayoutResolutionError as error:
        return _invalid("registered_local_name_invalid", error=str(error))
    result = result_factory()
    if not result.committed:
        return result
    return PoolActivationResult(
        result.status,
        {
            **result.evidence,
            "resolved_layout": resolved_filesystem_layout_evidence(
                plan,
                local_root=local_root,
                repo_root=repo_root,
            ),
            "local_locators": local_locators,
        },
    )


def _invalid(reason: str, **evidence: object) -> PoolActivationResult:
    return PoolActivationResult(
        PoolActivationStatus.INVALID,
        {"reason": reason, **evidence},
    )


def _data_inconsistent(reason: str, **evidence: object) -> PoolActivationResult:
    return PoolActivationResult(
        PoolActivationStatus.DATA_INCONSISTENT,
        {"reason": reason, **evidence},
    )


def _active_entry_conflict(
    conflicts: tuple[dict[str, str], ...],
) -> PoolActivationResult:
    return PoolActivationResult(
        PoolActivationStatus.ACTIVE_ENTRY_CONFLICT,
        {
            "reason": "managed_active_entry_conflict",
            "conflicts": list(conflicts),
        },
    )
