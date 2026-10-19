"""File-based Skills Pool activation and compatibility exports."""

from __future__ import annotations

import os
import re
from collections.abc import Callable as Callable, Sequence as Sequence
from dataclasses import dataclass as dataclass, field as field
from datetime import UTC as UTC, datetime as datetime
from enum import StrEnum as StrEnum
from pathlib import Path as Path
from uuid import uuid4 as uuid4
from engine.community.config import RepoDelivery as RepoDelivery, current_repo_delivery as current_repo_delivery
from engine.community.core.skills.layout_planner import LayoutIdentity as LayoutIdentity, RuntimeLayoutContext as RuntimeLayoutContext, SkillLayoutResolutionError as SkillLayoutResolutionError, resolve_filesystem_skill_layout as resolve_filesystem_skill_layout, resolve_local_skill_locators as resolve_local_skill_locators, resolved_filesystem_layout_evidence as resolved_filesystem_layout_evidence
from engine.community.core.skills.layout_planner import ResolvedFilesystemLayoutPlan as _Layout
from engine.community.plugins.skills_pool.layout_atomic import atomic_exchange_paths as atomic_exchange_paths
from engine.community.plugins.skills_pool.layout_probe import LAYOUT_CONTRACT_VERSION as LAYOUT_CONTRACT_VERSION, RuntimeLayoutInspectionStatus as RuntimeLayoutInspectionStatus, inspect_runtime_layout as inspect_runtime_layout
from engine.community.plugins.skills_pool.layout_sync import Manifest as Manifest, load_baseline_manifest as load_baseline_manifest, merge_post_cutover_changes as merge_post_cutover_changes, mirror_local_tree as mirror_local_tree, write_baseline_manifest as write_baseline_manifest
from engine.community.plugins.skills_pool.shared_mapping_cleanup import readonly_source as readonly_source, retire_unselected_shared_links as retire_unselected_shared_links

from engine.community.plugins.skills_pool.layout_results import PoolActivationStatus as PoolActivationStatus, MappingSourceLayout as MappingSourceLayout, MappingApplyMode as MappingApplyMode, MappingProjectionStatus as MappingProjectionStatus, MappingItemResult as MappingItemResult, MappingApplyResult as MappingApplyResult, ActiveRepoRetirementError as ActiveRepoRetirementError, ActiveRepoRestorationError as ActiveRepoRestorationError, SkillMapping as SkillMapping, PoolActivationResult as PoolActivationResult, MappingVerificationResult as MappingVerificationResult, MappingPublishResult as MappingPublishResult, _MappingPlan as _MappingPlan, _MappingRetirementPlan as _MappingRetirementPlan, _PostCutoverFinalization as _PostCutoverFinalization, _with_resolution_evidence as _with_resolution_evidence, _invalid as _invalid, _data_inconsistent as _data_inconsistent, _active_entry_conflict as _active_entry_conflict

from engine.community.plugins.skills_pool.mapping_inspection import _lexical_target as _lexical_target, _canonical_pool_source as _canonical_pool_source, _same_center_skill_identity as _same_center_skill_identity, _managed_source_failure as _managed_source_failure, _BestEffortSourceInspection as _BestEffortSourceInspection, _source_failure as _source_failure, _source_for_layout as _source_for_layout, _active_entry_inventory as _active_entry_inventory, _trusted_active_repo_bridge_rewrite_retry as _trusted_active_repo_bridge_rewrite_retry, _mapping_plan as _mapping_plan, _mapping_target_invalid as _mapping_target_invalid, _mapping_source_outside_layout as _mapping_source_outside_layout, _retirement_plan as _retirement_plan, _first_unreadable_path as _first_unreadable_path

from engine.community.plugins.skills_pool.mapping_apply import _mapping_status as _mapping_status, _best_effort_desired as _best_effort_desired, _best_effort_retire as _best_effort_retire, _best_effort_mapping_results as _best_effort_mapping_results, verify_skill_mappings as verify_skill_mappings, publish_pool_mappings as publish_pool_mappings

from engine.community.plugins.skills_pool.layout_storage import mapping_sources_use_pool as mapping_sources_use_pool, _active_marker_selects_pool as _active_marker_selects_pool, _retired_storage_entries as _retired_storage_entries, _read_active_marker as _read_active_marker, _write_active_marker as _write_active_marker, _retire_bridge as _retire_bridge, _settle_hermes_repo_bridge as _settle_hermes_repo_bridge, _publish_structural_bridge as _publish_structural_bridge, _restore_legacy_repo_bridge as _restore_legacy_repo_bridge, _finalize_active_root as _finalize_active_root, _finalize_post_cutover as _finalize_post_cutover, _capture_recreated_legacy_local as _capture_recreated_legacy_local, _ensure_quarantine_generation_owned as _ensure_quarantine_generation_owned

from engine.community.plugins.skills_pool.layout_rollback import _rollback_pool as _rollback_pool, rollback_openclaw_pool as rollback_openclaw_pool, rollback_claude_code_pool as rollback_claude_code_pool, rollback_aicoding_pool as rollback_aicoding_pool, rollback_hermes_pool as rollback_hermes_pool


def _cleanup_owned_cutover_temporary(
    *,
    temporary: Path,
    legacy_local: Path,
    pool_local: Path,
) -> bool:
    """清理由当前 generation 创建、且交换尚未发生的 canonical 临时桥。"""

    if (
        not temporary.is_symlink()
        or not legacy_local.is_dir()
        or legacy_local.is_symlink()
    ):
        return False
    try:
        target = _lexical_target(temporary)
    except OSError:
        return False
    if target != Path(os.path.abspath(pool_local)):
        return False
    temporary.unlink()
    return True


def _activate_pool(
    *,
    engine: str,
    migration_generation: str,
    preparation_id: str,
    registered_local_names: list[str],
    mappings: list[SkillMapping],
    home: str | Path = "/home/admin",
    repo_is_mounted: Callable[[Path], bool] | None = None,
    retire_path: Callable[[Path, Path], None] = os.replace,
    retire_active_repo: Callable[[str, str], dict[str, object]] | None = None,
    before_legacy_retire: Callable[[], None] | None = None,
    before_post_sync: Callable[[], None] | None = None,
) -> PoolActivationResult:
    """校验登记事实、同步完整 local，并单向退役 Legacy storage 入口。

    Backend 在调用本接口前已通过 ``begin_cutover`` 将路径消费者切到
    canonical Pool。这里先镜像完整 local corpus，再以同文件系统普通
    rename 将 Legacy local 移入 quarantine，最后发布逐 Skill 映射并删除
    Legacy storage 入口。切换窗口内允许短暂断链，但最终 active root
    绝不保留指向整个 Pool corpus 的目录 bridge。

    """

    home_path = Path(home)
    layout = _Layout.for_engine(engine, home_path)
    repo_mount_probe = repo_is_mounted or os.path.ismount
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", migration_generation):
        return _invalid("migration_generation_invalid")
    try:
        normalized_names = list(
            resolve_local_skill_locators(
                layout.pool_local,
                registered_local_names,
            )
        )
    except SkillLayoutResolutionError as error:
        return _invalid(
            "registered_local_name_invalid",
            error=str(error),
        )

    active_marker = _read_active_marker(layout.active_marker)
    if active_marker is not None and (
        active_marker.get("engine") != engine
        or active_marker.get("layout_contract_version") != LAYOUT_CONTRACT_VERSION
        or active_marker.get("preparation_id") != preparation_id
        or active_marker.get("migration_generation") != migration_generation
        or active_marker.get("activation_state") not in {"finalizing", "active"}
    ):
        return _invalid("active_marker_identity_mismatch")

    inspection = inspect_runtime_layout(
        engine=engine,
        expected_contract_version=LAYOUT_CONTRACT_VERSION,
        home=home_path,
        repo_is_mounted=repo_mount_probe,
    )
    layout_ready = (
        inspection.status is RuntimeLayoutInspectionStatus.READY
        and inspection.preparation_id == preparation_id
    )
    trusted_finalizing_retirement_retry = (
        engine == "aicoding"
        and active_marker is not None
        and active_marker.get("activation_state") == "finalizing"
        and inspection.status is RuntimeLayoutInspectionStatus.INVALID
        and inspection.preparation_id == preparation_id
        and inspection.evidence.get("reason") == "active_repo_corpus_present"
    )
    trusted_active_repo_bridge_rewrite_retry = (
        engine == "aicoding"
        and active_marker is not None
        and active_marker.get("activation_state") == "active"
        and inspection.status is RuntimeLayoutInspectionStatus.INVALID
        and inspection.preparation_id == preparation_id
        and inspection.evidence.get("reason") == "active_managed_entry_invalid"
        and _trusted_active_repo_bridge_rewrite_retry(layout=layout)
    )
    if not (
        layout_ready
        or trusted_finalizing_retirement_retry
        or trusted_active_repo_bridge_rewrite_retry
    ):
        return _invalid(
            "runtime_layout_not_ready",
            probe_status=inspection.status.value,
            preparation_id=inspection.preparation_id,
        )

    temporary = layout.legacy_local.parent / (
        f".skills-local.pool-cutover-{migration_generation}"
    )
    quarantine = (
        layout.pool_root
        / ".migration-quarantine"
        / migration_generation
        / "skills-local"
    )
    baseline_path = layout.pool_root / (
        f".cutover-baseline-{migration_generation}.json"
    )
    if active_marker is not None:
        if active_marker.get("activation_state") == "finalizing":
            ownership_failure = _ensure_quarantine_generation_owned(
                quarantine=quarantine,
                engine=engine,
                migration_generation=migration_generation,
                preparation_id=preparation_id,
                baseline_path=baseline_path,
            )
            if ownership_failure is not None:
                return ownership_failure
        try:
            completion_failure = _finalize_active_root(
                layout=layout,
                engine=engine,
                migration_generation=migration_generation,
                preparation_id=preparation_id,
                mappings=mappings,
                quarantine=quarantine,
                retire_path=retire_path,
                retire_active_repo=retire_active_repo,
                repo_is_mounted=repo_mount_probe,
            )
        except OSError as error:
            return PoolActivationResult(
                PoolActivationStatus.POST_CUTOVER_SYNC_PENDING,
                {
                    "reason": "active_root_retirement_failed",
                    "error_type": type(error).__name__,
                    "errno": error.errno,
                },
            )
        if completion_failure is not None:
            return completion_failure
        baseline_path.unlink(missing_ok=True)
        return PoolActivationResult(
            PoolActivationStatus.ALREADY_COMMITTED,
            {
                "active_marker": str(layout.active_marker),
                "legacy_storage_entries_absent": True,
                "quarantine": str(quarantine),
                # Finalization has already crossed the irreversible boundary.
                # Keep cleanup evidence conservative and deterministic instead
                # of probing persistent storage again while building the
                # ALREADY_COMMITTED response.
                "quarantine_cleanup_pending": True,
            },
        )

    try:
        if layout.legacy_local.is_symlink():
            if _lexical_target(layout.legacy_local) != Path(
                os.path.abspath(layout.pool_local)
            ):
                return _invalid("legacy_local_bridge_invalid")
            finalization = _PostCutoverFinalization({}, False)
            if temporary.is_dir() and not temporary.is_symlink():
                ownership_failure = _ensure_quarantine_generation_owned(
                    quarantine=quarantine,
                    engine=engine,
                    migration_generation=migration_generation,
                    preparation_id=preparation_id,
                    baseline_path=baseline_path,
                )
                if ownership_failure is not None:
                    return ownership_failure
                for name in normalized_names:
                    source = temporary / name
                    if not source.is_dir() or source.is_symlink():
                        return _invalid(
                            "registered_local_source_invalid",
                            source=str(source),
                        )
                finalization = _finalize_post_cutover(
                    temporary=temporary,
                    pool_local=layout.pool_local,
                    quarantine=quarantine,
                    baseline_path=baseline_path,
                )
                if finalization.failure is not None:
                    return finalization.failure
            completion_failure = _finalize_active_root(
                layout=layout,
                engine=engine,
                migration_generation=migration_generation,
                preparation_id=preparation_id,
                mappings=mappings,
                quarantine=quarantine,
                retire_path=retire_path,
                retire_active_repo=retire_active_repo,
                repo_is_mounted=repo_mount_probe,
            )
            if completion_failure is not None:
                return completion_failure
            baseline_path.unlink(missing_ok=True)
            return PoolActivationResult(
                PoolActivationStatus.ALREADY_COMMITTED,
                {
                    "active_marker": str(layout.active_marker),
                    "legacy_storage_entries_absent": True,
                    "quarantine": str(quarantine),
                    "quarantine_cleanup_pending": (finalization.cleanup_pending),
                    "post_sync": finalization.post_sync,
                },
            )
        if quarantine.is_dir() and not quarantine.is_symlink():
            ownership_failure = _ensure_quarantine_generation_owned(
                quarantine=quarantine,
                engine=engine,
                migration_generation=migration_generation,
                preparation_id=preparation_id,
                baseline_path=baseline_path,
            )
            if ownership_failure is not None:
                return ownership_failure
            if not baseline_path.is_file() or baseline_path.is_symlink():
                return _invalid(
                    "cutover_baseline_missing_after_legacy_retire",
                    path=str(baseline_path),
                )
            for name in normalized_names:
                source = quarantine / name
                if not source.is_dir() or source.is_symlink():
                    return _data_inconsistent(
                        "registered_local_source_invalid",
                        registered_name=name,
                        source=str(source),
                    )
            mapping_plan = _mapping_plan(layout=layout, mappings=mappings)
            if mapping_plan.conflicts:
                return _active_entry_conflict(mapping_plan.conflicts)
            if mapping_plan.failures:
                return _invalid(
                    "mapping_source_invalid",
                    failures=list(mapping_plan.failures),
                )
            finalization = _finalize_post_cutover(
                temporary=quarantine,
                pool_local=layout.pool_local,
                quarantine=quarantine,
                baseline_path=baseline_path,
            )
            if finalization.failure is not None:
                return finalization.failure
            residue_evidence, residue_failure = _capture_recreated_legacy_local(
                layout=layout,
                quarantine=quarantine,
                retire_path=retire_path,
            )
            if residue_failure is not None:
                return residue_failure
            completion_failure = _finalize_active_root(
                layout=layout,
                engine=engine,
                migration_generation=migration_generation,
                preparation_id=preparation_id,
                mappings=mappings,
                quarantine=quarantine,
                retire_path=retire_path,
                retire_active_repo=retire_active_repo,
                repo_is_mounted=repo_mount_probe,
            )
            if completion_failure is not None:
                return completion_failure
            baseline_path.unlink(missing_ok=True)
            return PoolActivationResult(
                PoolActivationStatus.ALREADY_COMMITTED,
                {
                    "active_marker": str(layout.active_marker),
                    "legacy_storage_entries_absent": True,
                    "quarantine": str(quarantine),
                    "quarantine_cleanup_pending": True,
                    "post_sync": finalization.post_sync,
                    "legacy_residue": residue_evidence,
                },
            )
        if not layout.legacy_local.is_dir():
            return _invalid("legacy_local_not_directory")

        for name in normalized_names:
            source = layout.legacy_local / name
            if not source.exists():
                return _data_inconsistent(
                    "registered_local_source_missing",
                    registered_name=name,
                    source=str(source),
                )
            if not source.is_dir() or source.is_symlink():
                return _data_inconsistent(
                    "registered_local_source_invalid",
                    registered_name=name,
                    source=str(source),
                )
            unreadable = _first_unreadable_path(source)
            if unreadable is not None:
                return _data_inconsistent(
                    "registered_local_source_unreadable",
                    registered_name=name,
                    source=str(source),
                    unreadable_path=str(unreadable),
                )

        local_names, staged_legacy_baseline = mirror_local_tree(
            source_root=layout.legacy_local,
            pool_local=layout.pool_local,
            staging_root=layout.pool_root / f".final-sync-{migration_generation}",
        )
        baseline = write_baseline_manifest(
            # Freeze the exact staging snapshot. Reading live Legacy here
            # would miss writes landing after staging copy but before rename.
            pool_local=layout.legacy_local,
            local_names=local_names,
            manifest_path=baseline_path,
            manifest=staged_legacy_baseline,
        )

        mapping_plan = _mapping_plan(layout=layout, mappings=mappings)
        if mapping_plan.conflicts:
            return _active_entry_conflict(mapping_plan.conflicts)
        if mapping_plan.failures:
            if any(
                failure["reason"].startswith("managed_")
                for failure in mapping_plan.failures
            ):
                return _data_inconsistent(
                    "managed_active_source_invalid",
                    failures=list(mapping_plan.failures),
                )
            return _invalid(
                "mapping_source_invalid",
                failures=list(mapping_plan.failures),
            )

        if before_legacy_retire is not None:
            before_legacy_retire()
        recovered_temporary = False
        if temporary.exists() or temporary.is_symlink():
            recovered_temporary = _cleanup_owned_cutover_temporary(
                temporary=temporary,
                legacy_local=layout.legacy_local,
                pool_local=layout.pool_local,
            )
            if not recovered_temporary:
                return _invalid(
                    "cutover_temporary_path_occupied",
                    path=str(temporary),
                )
        ownership_failure = _ensure_quarantine_generation_owned(
            quarantine=quarantine,
            engine=engine,
            migration_generation=migration_generation,
            preparation_id=preparation_id,
            baseline_path=baseline_path,
        )
        if ownership_failure is not None:
            return ownership_failure
        if quarantine.exists() or quarantine.is_symlink():
            return _invalid(
                "cutover_quarantine_path_occupied",
                path=str(quarantine),
            )
        retire_path(layout.legacy_local, quarantine)

        if before_post_sync is not None:
            before_post_sync()
        finalization = _finalize_post_cutover(
            temporary=quarantine,
            pool_local=layout.pool_local,
            quarantine=quarantine,
            baseline_path=baseline_path,
            baseline=baseline,
        )
        if finalization.failure is not None:
            return finalization.failure
        residue_evidence, residue_failure = _capture_recreated_legacy_local(
            layout=layout,
            quarantine=quarantine,
            retire_path=retire_path,
        )
        if residue_failure is not None:
            return residue_failure
        completion_failure = _finalize_active_root(
            layout=layout,
            engine=engine,
            migration_generation=migration_generation,
            preparation_id=preparation_id,
            mappings=mappings,
            quarantine=quarantine,
            retire_path=retire_path,
            retire_active_repo=retire_active_repo,
            repo_is_mounted=repo_mount_probe,
        )
        if completion_failure is not None:
            return completion_failure
        baseline_path.unlink(missing_ok=True)
        return PoolActivationResult(
            PoolActivationStatus.COMMITTED,
            {
                "active_marker": str(layout.active_marker),
                "legacy_storage_entries_absent": True,
                "quarantine": str(quarantine),
                "quarantine_cleanup_pending": finalization.cleanup_pending,
                "recovered_temporary": recovered_temporary,
                "registered_local_count": len(normalized_names),
                "local_inventory": {
                    "registered": len(normalized_names),
                    "unregistered": len(set(local_names) - set(normalized_names)),
                    "total": len(local_names),
                },
                "mapping_source_count": len(mappings),
                "active_inventory": {
                    "managed": len(mapping_plan.managed),
                    "external": len(mapping_plan.external),
                },
                "post_sync": finalization.post_sync,
                "legacy_residue": residue_evidence,
            },
        )
    except OSError as error:
        committed = layout.legacy_local.is_symlink() and _lexical_target(
            layout.legacy_local
        ) == Path(os.path.abspath(layout.pool_local))
        evidence: dict[str, object] = {
            "reason": (
                "post_cutover_cleanup_failed"
                if committed
                else "filesystem_operation_failed"
            ),
            "error_type": type(error).__name__,
            "errno": error.errno,
        }
        if committed:
            evidence.update(
                {
                    "quarantine": str(quarantine),
                    # We are already handling an I/O failure after the
                    # irreversible cutover. Do not probe the filesystem again
                    # while constructing the COMMITTED response: Python 3.12
                    # may re-raise permission/I/O errors from Path predicates.
                    "quarantine_cleanup_pending": True,
                }
            )
        return PoolActivationResult(
            (
                PoolActivationStatus.COMMITTED
                if committed
                else PoolActivationStatus.TRANSIENT_ERROR
            ),
            evidence,
        )


def activate_openclaw_pool(
    *,
    migration_generation: str,
    preparation_id: str,
    registered_local_names: list[str],
    mappings: list[SkillMapping],
    home: str | Path = "/home/admin",
    repo_is_mounted: Callable[[Path], bool] | None = None,
    exchange_paths: Callable[[Path, Path], bool] = atomic_exchange_paths,
    retire_path: Callable[[Path, Path], None] = os.replace,
    before_legacy_retire: Callable[[], None] | None = None,
    before_post_sync: Callable[[], None] | None = None,
) -> PoolActivationResult:
    # Deprecated compatibility seam for existing in-process callers. Forward
    # activation no longer depends on atomic exchange.
    del exchange_paths
    return _with_resolution_evidence(
        lambda: _activate_pool(
            engine="openclaw",
            migration_generation=migration_generation,
            preparation_id=preparation_id,
            registered_local_names=registered_local_names,
            mappings=mappings,
            home=home,
            repo_is_mounted=repo_is_mounted,
            retire_path=retire_path,
            before_legacy_retire=before_legacy_retire,
            before_post_sync=before_post_sync,
        ),
        engine="openclaw",
        source_layout=MappingSourceLayout.POOL,
        registered_local_names=registered_local_names,
        home=home,
    )


def activate_claude_code_pool(
    *,
    migration_generation: str,
    preparation_id: str,
    registered_local_names: list[str],
    mappings: list[SkillMapping],
    home: str | Path = "/home/admin",
    repo_is_mounted: Callable[[Path], bool] | None = None,
    exchange_paths: Callable[[Path, Path], bool] = atomic_exchange_paths,
    retire_path: Callable[[Path, Path], None] = os.replace,
    before_legacy_retire: Callable[[], None] | None = None,
    before_post_sync: Callable[[], None] | None = None,
) -> PoolActivationResult:
    del exchange_paths
    return _with_resolution_evidence(
        lambda: _activate_pool(
            engine="claude_code",
            migration_generation=migration_generation,
            preparation_id=preparation_id,
            registered_local_names=registered_local_names,
            mappings=mappings,
            home=home,
            repo_is_mounted=repo_is_mounted,
            retire_path=retire_path,
            before_legacy_retire=before_legacy_retire,
            before_post_sync=before_post_sync,
        ),
        engine="claude_code",
        source_layout=MappingSourceLayout.POOL,
        registered_local_names=registered_local_names,
        home=home,
    )


def activate_aicoding_pool(
    *,
    migration_generation: str,
    preparation_id: str,
    registered_local_names: list[str],
    mappings: list[SkillMapping],
    home: str | Path = "/home/admin",
    repo_is_mounted: Callable[[Path], bool] | None = None,
    exchange_paths: Callable[[Path, Path], bool] = atomic_exchange_paths,
    retire_path: Callable[[Path, Path], None] = os.replace,
    retire_active_repo: Callable[[str, str], dict[str, object]] | None = None,
    before_legacy_retire: Callable[[], None] | None = None,
    before_post_sync: Callable[[], None] | None = None,
) -> PoolActivationResult:
    del exchange_paths
    return _with_resolution_evidence(
        lambda: _activate_pool(
            engine="aicoding",
            migration_generation=migration_generation,
            preparation_id=preparation_id,
            registered_local_names=registered_local_names,
            mappings=mappings,
            home=home,
            repo_is_mounted=repo_is_mounted,
            retire_path=retire_path,
            retire_active_repo=retire_active_repo,
            before_legacy_retire=before_legacy_retire,
            before_post_sync=before_post_sync,
        ),
        engine="aicoding",
        source_layout=MappingSourceLayout.POOL,
        registered_local_names=registered_local_names,
        home=home,
    )


def activate_hermes_pool(
    *,
    migration_generation: str,
    preparation_id: str,
    registered_local_names: list[str],
    mappings: list[SkillMapping],
    home: str | Path = "/home/admin",
    repo_is_mounted: Callable[[Path], bool] | None = None,
    exchange_paths: Callable[[Path, Path], bool] = atomic_exchange_paths,
    retire_path: Callable[[Path, Path], None] = os.replace,
    before_legacy_retire: Callable[[], None] | None = None,
    before_post_sync: Callable[[], None] | None = None,
) -> PoolActivationResult:
    del exchange_paths
    return _with_resolution_evidence(
        lambda: _activate_pool(
            engine="hermes",
            migration_generation=migration_generation,
            preparation_id=preparation_id,
            registered_local_names=registered_local_names,
            mappings=mappings,
            home=home,
            repo_is_mounted=repo_is_mounted,
            retire_path=retire_path,
            before_legacy_retire=before_legacy_retire,
            before_post_sync=before_post_sync,
        ),
        engine="hermes",
        source_layout=MappingSourceLayout.POOL,
        registered_local_names=registered_local_names,
        home=home,
    )
