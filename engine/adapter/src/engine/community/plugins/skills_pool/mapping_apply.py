"""Apply and verify complete Skill mapping snapshots."""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Sequence
from pathlib import Path

from engine.community.core.skills.layout_planner import (
    ResolvedFilesystemLayoutPlan as _Layout,
)
from engine.community.core.skills.layout_planner import RuntimeLayoutContext
from engine.community.plugins.skills_pool.shared_mapping_cleanup import (
    readonly_source,
    retire_unselected_shared_links,
)

logger = logging.getLogger(__name__)

from engine.community.plugins.skills_pool.layout_results import (
    MappingApplyMode,
    MappingItemResult,
    MappingProjectionStatus,
    MappingPublishResult,
    MappingSourceLayout,
    MappingVerificationResult,
    SkillMapping,
)
from engine.community.plugins.skills_pool.mapping_inspection import (
    _active_entry_inventory,
    _BestEffortSourceInspection,
    _canonical_pool_source,
    _lexical_target,
    _mapping_plan,
    _mapping_source_outside_layout,
    _mapping_target_invalid,
    _retirement_plan,
    _same_center_skill_identity,
    _source_for_layout,
)


def _mapping_status(items: Sequence[MappingItemResult]) -> MappingProjectionStatus:
    if any(item.status is MappingProjectionStatus.DEGRADED for item in items):
        return MappingProjectionStatus.DEGRADED
    if any(item.status is MappingProjectionStatus.PENDING for item in items):
        return MappingProjectionStatus.PENDING
    return MappingProjectionStatus.CONVERGED


def _best_effort_desired(
    *,
    layout: _Layout,
    mappings: Sequence[SkillMapping],
    source_layout: MappingSourceLayout,
    inspection: _BestEffortSourceInspection,
) -> tuple[dict[Path, Path], list[MappingItemResult]]:
    """Classify requested mappings without treating source availability as unsafe.

    ``STRICT`` delegates this work to ``_mapping_plan`` and rejects every
    failure before touching the filesystem.  Product projection needs the same
    path-safety checks, but a missing/unreadable managed source is a temporary
    availability condition: its lexical link remains a safe desired entry.
    """

    desired: dict[Path, Path] = {}
    outcomes: list[MappingItemResult] = []
    duplicate_targets: set[Path] = set()
    for mapping in mappings:
        source_input = Path(mapping.source)
        source = Path(os.path.abspath(source_input))
        target = Path(mapping.target)
        if not source_input.is_absolute():
            outcomes.append(
                MappingItemResult(
                    target=str(target),
                    source=str(source),
                    status=MappingProjectionStatus.DEGRADED,
                    code=(
                        "SOURCE_OUTSIDE_LEGACY"
                        if source_layout is MappingSourceLayout.LEGACY
                        else "SOURCE_OUTSIDE_POOL"
                    ),
                )
            )
            continue
        if _mapping_target_invalid(layout, target):
            outcomes.append(
                MappingItemResult(
                    target=str(target),
                    source=str(source),
                    status=MappingProjectionStatus.DEGRADED,
                    code="TARGET_INVALID",
                )
            )
            continue
        source_reason = inspection.failure(source)
        if source_reason not in {None, "source_missing", "source_unreadable"}:
            outcomes.append(
                MappingItemResult(
                    target=str(target),
                    source=str(source),
                    status=MappingProjectionStatus.DEGRADED,
                    code=source_reason.upper(),
                )
            )
            continue
        existing = desired.get(target)
        if existing is not None and existing != source:
            duplicate_targets.add(target)
            continue
        desired[target] = source

    for target in duplicate_targets:
        source = desired.pop(target, None)
        if source is not None:
            outcomes.append(
                MappingItemResult(
                    target=str(target),
                    source=str(source),
                    status=MappingProjectionStatus.DEGRADED,
                    code="MANAGED_SOURCE_CONFLICT",
                )
            )
        for mapping in mappings:
            if Path(mapping.target) == target and Path(mapping.source) != source:
                outcomes.append(
                    MappingItemResult(
                        target=str(target),
                        source=str(Path(os.path.abspath(mapping.source))),
                        status=MappingProjectionStatus.DEGRADED,
                        code="MANAGED_SOURCE_CONFLICT",
                    )
                )
    return desired, outcomes


def _best_effort_retire(
    *,
    layout: _Layout,
    retired_mappings: Sequence[SkillMapping],
    desired_targets: frozenset[Path],
    source_layout: MappingSourceLayout,
    additional_retirement_roots: Sequence[Path],
    apply: bool,
) -> tuple[list[MappingItemResult], list[str], list[str]]:
    """Retire only exact managed links; preserve every unknown filesystem entry."""

    outcomes: list[MappingItemResult] = []
    removed: list[str] = []
    absent: list[str] = []
    for mapping in retired_mappings:
        source_input = Path(mapping.source)
        source = Path(os.path.abspath(source_input))
        target = Path(mapping.target)
        if target in desired_targets:
            # A desired replacement owns this target and will update it below.
            continue
        if (
            not source_input.is_absolute()
            or _mapping_source_outside_layout(
                layout,
                source,
                source_layout=source_layout,
            )
            or _mapping_target_invalid(
                layout,
                target,
                additional_retirement_roots=additional_retirement_roots,
            )
        ):
            outcomes.append(
                MappingItemResult(
                    target=str(target),
                    source=str(source),
                    status=MappingProjectionStatus.DEGRADED,
                    code="RETIRED_MAPPING_INVALID",
                    action="RETIRE",
                )
            )
            continue
        try:
            target.lstat()
        except FileNotFoundError:
            absent.append(str(target))
            outcomes.append(
                MappingItemResult(
                    target=str(target),
                    source=str(source),
                    status=MappingProjectionStatus.CONVERGED,
                    action="RETIRE",
                )
            )
            continue
        except OSError:
            outcomes.append(
                MappingItemResult(
                    target=str(target),
                    source=str(source),
                    status=MappingProjectionStatus.PENDING,
                    code="MAPPING_PUBLISH_IO_ERROR",
                    retryable=True,
                    action="RETIRE",
                )
            )
            continue
        if not target.is_symlink():
            outcomes.append(
                MappingItemResult(
                    target=str(target),
                    source=str(source),
                    status=MappingProjectionStatus.DEGRADED,
                    code="UNMANAGED_ACTIVE_ENTRY_RETAINED",
                    action="RETIRE",
                )
            )
            continue
        current = _lexical_target(target)
        if current != source and not _same_center_skill_identity(
            layout, current, source
        ):
            outcomes.append(
                MappingItemResult(
                    target=str(target),
                    source=str(source),
                    status=MappingProjectionStatus.DEGRADED,
                    code=(
                        "EXTERNAL_ACTIVE_ENTRY_RETAINED"
                        if _canonical_pool_source(layout, current) is None
                        else "RETIRED_TARGET_IDENTITY_MISMATCH"
                    ),
                    action="RETIRE",
                )
            )
            continue
        if apply:
            try:
                target.unlink()
            except OSError:
                outcomes.append(
                    MappingItemResult(
                        target=str(target),
                        source=str(source),
                        status=MappingProjectionStatus.PENDING,
                        code="MAPPING_PUBLISH_IO_ERROR",
                        retryable=True,
                        action="RETIRE",
                    )
                )
                continue
            removed.append(str(target))
        else:
            outcomes.append(
                MappingItemResult(
                    target=str(target),
                    source=str(source),
                    status=MappingProjectionStatus.DEGRADED,
                    code="RETIRED_TARGET_STILL_PRESENT",
                    action="RETIRE",
                )
            )
            continue
        outcomes.append(
            MappingItemResult(
                target=str(target),
                source=str(source),
                status=MappingProjectionStatus.CONVERGED,
                action="RETIRE",
            )
        )
    return outcomes, removed, absent


def _best_effort_mapping_results(
    *,
    layout: _Layout,
    deferred_mappings: Sequence[SkillMapping] = (),
    mappings: Sequence[SkillMapping],
    retired_mappings: Sequence[SkillMapping],
    source_layout: MappingSourceLayout,
    additional_retirement_roots: Sequence[Path],
    apply: bool,
) -> tuple[tuple[MappingItemResult, ...], dict[str, object]]:
    """Apply or inspect one full desired mapping set without touching unknown data."""

    started_at = time.perf_counter()
    inspection = _BestEffortSourceInspection(
        layout=layout,
        source_layout=source_layout,
    )
    classify_started_at = time.perf_counter()
    desired, outcomes = _best_effort_desired(
        layout=layout,
        mappings=mappings,
        source_layout=source_layout,
        inspection=inspection,
    )
    classify_ms = (time.perf_counter() - classify_started_at) * 1000
    retire_started_at = time.perf_counter()
    retired, removed, absent = _best_effort_retire(
        layout=layout,
        retired_mappings=retired_mappings,
        desired_targets=frozenset(desired),
        source_layout=source_layout,
        additional_retirement_roots=additional_retirement_roots,
        apply=apply,
    )
    retire_ms = (time.perf_counter() - retire_started_at) * 1000
    outcomes.extend(retired)

    inventory_started_at = time.perf_counter()
    discovered, external, occupied = _active_entry_inventory(layout)
    inventory_ms = (time.perf_counter() - inventory_started_at) * 1000
    external_targets = set(external)
    occupied_targets = set(occupied)
    created: list[str] = []
    updated: list[str] = []
    kept: list[str] = []
    reconcile_started_at = time.perf_counter()
    for target, source in desired.items():
        if target in external_targets:
            outcomes.append(
                MappingItemResult(
                    target=str(target),
                    source=str(source),
                    status=MappingProjectionStatus.DEGRADED,
                    code="EXTERNAL_ACTIVE_ENTRY_RETAINED",
                )
            )
            continue
        if target in occupied_targets:
            outcomes.append(
                MappingItemResult(
                    target=str(target),
                    source=str(source),
                    status=MappingProjectionStatus.DEGRADED,
                    code="UNMANAGED_ACTIVE_ENTRY_RETAINED",
                )
            )
            continue
        source_reason = inspection.failure(source)
        pending = source_reason in {"source_missing", "source_unreadable"}
        if not apply:
            if not target.is_symlink() or _lexical_target(target) != source:
                outcomes.append(
                    MappingItemResult(
                        target=str(target),
                        source=str(source),
                        status=MappingProjectionStatus.DEGRADED,
                        code="TARGET_NOT_SYMLINK"
                        if not target.is_symlink()
                        else "TARGET_MISMATCH",
                    )
                )
            else:
                outcomes.append(
                    MappingItemResult(
                        target=str(target),
                        source=str(source),
                        status=(
                            MappingProjectionStatus.PENDING
                            if pending
                            else MappingProjectionStatus.CONVERGED
                        ),
                        code="MANAGED_SOURCE_MISSING" if pending else None,
                        retryable=pending,
                    )
                )
            continue
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.is_symlink():
                if _lexical_target(target) == source:
                    kept.append(str(target))
                else:
                    target.unlink()
                    target.symlink_to(source, target_is_directory=True)
                    updated.append(str(target))
            else:
                target.symlink_to(source, target_is_directory=True)
                created.append(str(target))
        except OSError:
            outcomes.append(
                MappingItemResult(
                    target=str(target),
                    source=str(source),
                    status=MappingProjectionStatus.PENDING,
                    code="MAPPING_PUBLISH_IO_ERROR",
                    retryable=True,
                )
            )
            continue
        outcomes.append(
            MappingItemResult(
                target=str(target),
                source=str(source),
                status=(
                    MappingProjectionStatus.PENDING
                    if pending
                    else MappingProjectionStatus.CONVERGED
                ),
                code="MANAGED_SOURCE_MISSING" if pending else None,
                retryable=pending,
            )
        )
    reconcile_ms = (time.perf_counter() - reconcile_started_at) * 1000

    # Local is user-writable. Only explicit retirements may remove Local links.
    retired_targets = {Path(item.target) for item in retired_mappings}
    for target, pool_source in discovered.items():
        if target in desired or target in retired_targets or readonly_source(layout, pool_source):
            continue
        source = _source_for_layout(
            layout,
            pool_source,
            source_layout=source_layout,
        )
        reason = inspection.failure(source)
        if reason in {"source_missing", "source_unreadable"}:
            outcomes.append(
                MappingItemResult(
                    target=str(target),
                    source=str(source),
                    status=MappingProjectionStatus.PENDING,
                    code="MANAGED_SOURCE_MISSING",
                    retryable=True,
                )
            )

    # A rejected snapshot must not authorize destructive inference.
    if not any(item.status is MappingProjectionStatus.DEGRADED for item in outcomes):
        selected_targets = {Path(item.target) for item in [*mappings, *deferred_mappings]}
        for item in retire_unselected_shared_links(layout, selected_targets, apply=apply):
            outcomes.append(MappingItemResult(
                target=str(item.target), source=str(item.source), action="RETIRE",
                status=(MappingProjectionStatus.PENDING if item.retryable else
                        MappingProjectionStatus.DEGRADED if item.code else MappingProjectionStatus.CONVERGED),
                code=item.code, retryable=item.retryable,
            ))
            if item.code is None and apply:
                removed.append(str(item.target))

    evidence = {
        "total": len(desired),
        "created": created,
        "updated": updated,
        "kept": kept,
        "removed": removed,
        "retired_absent": absent,
        "external_ignored": [str(path) for path in external],
        **inspection.evidence(),
        "source_check_ms": round(sum(inspection.durations_ms.values()), 3),
        "classify_ms": round(classify_ms, 3),
        "retire_ms": round(retire_ms, 3),
        "inventory_ms": round(inventory_ms, 3),
        "reconcile_ms": round(reconcile_ms, 3),
        "duration_ms": round((time.perf_counter() - started_at) * 1000, 3),
    }
    logger.info(
        "[SkillsPoolMapping] best_effort apply=%s duration_ms=%s desired=%s "
        "retired=%s inventory=%s source_checks=%s source_cache_hits=%s "
        "source_check_ms=%s classify_ms=%s retire_ms=%s inventory_ms=%s "
        "reconcile_ms=%s "
        "slow_sources=%s status=%s",
        apply,
        evidence["duration_ms"],
        len(desired),
        len(retired_mappings),
        len(discovered) + len(external) + len(occupied),
        evidence["source_checks"],
        evidence["source_check_cache_hits"],
        evidence["source_check_ms"],
        evidence["classify_ms"],
        evidence["retire_ms"],
        evidence["inventory_ms"],
        evidence["reconcile_ms"],
        evidence["slow_sources"],
        _mapping_status(outcomes).value,
    )
    return tuple(outcomes), evidence


def verify_skill_mappings(
    *,
    mappings: list[SkillMapping],
    retired_mappings: Sequence[SkillMapping] = (),
    home: str | Path = "/home/admin",
    layout_context: RuntimeLayoutContext | None = None,
    engine: str = "openclaw",
    source_layout: MappingSourceLayout = MappingSourceLayout.POOL,
    additional_retirement_roots: Sequence[Path] = (),
    apply_mode: MappingApplyMode = MappingApplyMode.STRICT,
) -> MappingVerificationResult:
    """验证受管激活入口精确解析到声明 layout 的 source。"""

    layout = (
        _Layout.for_runtime_context(engine, layout_context)
        if layout_context is not None
        else _Layout.for_engine(engine, Path(home))
    )
    if apply_mode is MappingApplyMode.BEST_EFFORT:
        items, evidence = _best_effort_mapping_results(
            layout=layout,
            mappings=mappings,
            retired_mappings=retired_mappings,
            source_layout=source_layout,
            additional_retirement_roots=additional_retirement_roots,
            apply=False,
        )
        status = _mapping_status(items)
        return MappingVerificationResult(
            valid=status is MappingProjectionStatus.CONVERGED,
            evidence=evidence,
            status=status,
            items=items,
        )
    retirement = _retirement_plan(
        layout=layout,
        mappings=mappings,
        retired_mappings=list(retired_mappings),
        source_layout=source_layout,
        additional_retirement_roots=additional_retirement_roots,
    )
    plan = _mapping_plan(
        layout=layout,
        mappings=mappings,
        source_layout=source_layout,
        retired_targets=frozenset(Path(item.target) for item in retired_mappings),
    )
    failures: list[dict[str, str]] = []
    failures.extend(retirement.failures)
    for target in retirement.remove:
        failures.append(
            {"target": str(target), "reason": "retired_target_still_present"}
        )
    failures.extend(plan.failures)
    for item in retire_unselected_shared_links(
        layout, {Path(mapping.target) for mapping in mappings}, apply=False,
    ):
        failures.append({"target": str(item.target), "reason": item.code or "shared_mapping_present"})
    for conflict in plan.conflicts:
        failures.append({**conflict, "reason": "managed_source_conflict"})
    for target, source in plan.managed.items():
        reason = ""
        if not target.is_symlink():
            reason = "target_not_symlink"
        elif _lexical_target(target) != Path(os.path.abspath(source)):
            reason = "target_mismatch"
        if reason:
            failures.append(
                {"source": str(source), "target": str(target), "reason": reason}
            )
    return MappingVerificationResult(
        valid=not failures,
        evidence={
            "checked": len(mappings),
            "managed_checked": len(plan.managed),
            "external_ignored": len(plan.external),
            "retired_checked": len(retired_mappings),
            "retired_external_ignored": len(retirement.external),
            "failures": failures,
        },
    )


def publish_pool_mappings(
    *,
    mappings: list[SkillMapping],
    deferred_mappings: Sequence[SkillMapping] = (),
    retired_mappings: Sequence[SkillMapping] = (),
    home: str | Path = "/home/admin",
    layout_context: RuntimeLayoutContext | None = None,
    engine: str = "openclaw",
    source_layout: MappingSourceLayout = MappingSourceLayout.POOL,
    additional_retirement_roots: Sequence[Path] = (),
    apply_mode: MappingApplyMode = MappingApplyMode.STRICT,
) -> MappingPublishResult:
    """按声明 layout 对齐全部受管 mapping，并保留外部入口。"""

    layout = (
        _Layout.for_runtime_context(engine, layout_context)
        if layout_context is not None
        else _Layout.for_engine(engine, Path(home))
    )
    if apply_mode is MappingApplyMode.BEST_EFFORT:
        items, evidence = _best_effort_mapping_results(
            layout=layout,
            mappings=mappings,
            deferred_mappings=deferred_mappings,
            retired_mappings=retired_mappings,
            source_layout=source_layout,
            additional_retirement_roots=additional_retirement_roots,
            apply=True,
        )
        status = _mapping_status(items)
        return MappingPublishResult(
            published=status is MappingProjectionStatus.CONVERGED,
            evidence=evidence,
            status=status,
            items=items,
        )
    retirement = _retirement_plan(
        layout=layout,
        mappings=mappings,
        retired_mappings=list(retired_mappings),
        source_layout=source_layout,
        additional_retirement_roots=additional_retirement_roots,
    )
    plan = _mapping_plan(
        layout=layout,
        mappings=mappings,
        source_layout=source_layout,
        retired_targets=frozenset(Path(item.target) for item in retired_mappings),
    )
    if plan.conflicts:
        return MappingPublishResult(
            published=False,
            evidence={
                "reason": "managed_active_entry_conflict",
                "conflicts": list(plan.conflicts),
            },
        )
    if retirement.failures:
        return MappingPublishResult(
            published=False,
            evidence={
                "reason": "retired_mapping_invalid",
                "failures": list(retirement.failures),
            },
        )
    if plan.failures:
        return MappingPublishResult(
            published=False,
            evidence={
                "reason": "mapping_invalid",
                "failures": list(plan.failures),
            },
        )

    created: list[str] = []
    updated: list[str] = []
    kept: list[str] = []
    removed: list[str] = []
    try:
        for target in retirement.remove:
            target.unlink()
            removed.append(str(target))
        for target, source in plan.managed.items():
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.is_symlink():
                if _lexical_target(target) == Path(os.path.abspath(source)):
                    kept.append(str(target))
                    continue
                target.unlink()
                updated.append(str(target))
            elif target.exists():
                return MappingPublishResult(
                    published=False,
                    evidence={
                        "reason": "managed_target_occupied",
                        "target": str(target),
                    },
                )
            target.symlink_to(source, target_is_directory=True)
            if str(target) not in updated:
                created.append(str(target))

    except OSError as error:
        return MappingPublishResult(
            published=False,
            evidence={
                "reason": "mapping_publish_io_error",
                "error_type": type(error).__name__,
                "errno": error.errno,
            },
        )
    shared_retirements = retire_unselected_shared_links(
        layout, {Path(item.target) for item in [*mappings, *deferred_mappings]}, apply=True,
    )
    if any(item.code for item in shared_retirements):
        return MappingPublishResult(
            published=False, evidence={"reason": "shared_mapping_retirement_failed"},
            status=MappingProjectionStatus.PENDING,
        )
    removed.extend(str(item.target) for item in shared_retirements)
    return MappingPublishResult(
        published=True,
        evidence={
            "total": len(plan.managed),
            "created": created,
            "updated": updated,
            "kept": kept,
            "removed": removed,
            "external_ignored": [str(path) for path in plan.external],
            "retired_absent": [str(path) for path in retirement.absent],
            "retired_replaced": [str(path) for path in retirement.replaced],
            "retired_external_ignored": [str(path) for path in retirement.external],
        },
    )
