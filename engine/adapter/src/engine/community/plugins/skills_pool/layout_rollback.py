"""Restore retained Legacy storage after a Pool cutover."""

from __future__ import annotations

import os
import re
from collections.abc import Callable
from pathlib import Path

from engine.community.config import RepoDelivery, current_repo_delivery
from engine.community.core.skills.layout_planner import (
    ResolvedFilesystemLayoutPlan as _Layout,
)
from engine.community.core.skills.layout_planner import (
    SkillLayoutResolutionError,
    resolve_local_skill_locators,
)
from engine.community.plugins.skills_pool.layout_atomic import atomic_exchange_paths
from engine.community.plugins.skills_pool.layout_results import (
    ActiveRepoRestorationError,
    MappingSourceLayout,
    PoolActivationResult,
    PoolActivationStatus,
    _data_inconsistent,
    _invalid,
    _with_resolution_evidence,
)
from engine.community.plugins.skills_pool.layout_storage import (
    _read_active_marker,
    _restore_legacy_repo_bridge,
)
from engine.community.plugins.skills_pool.layout_sync import mirror_local_tree
from engine.community.plugins.skills_pool.mapping_inspection import (
    _lexical_target,
)


def _rollback_pool(
    *,
    engine: str,
    rollback_generation: str,
    registered_local_names: list[str],
    home: str | Path = "/home/admin",
    exchange_paths: Callable[[Path, Path], bool] = atomic_exchange_paths,
    restore_active_repo: Callable[[str, str], dict[str, object]] | None = None,
) -> PoolActivationResult:
    """Rebuild Legacy from Pool for the pre-recursive-engine rollout window."""

    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", rollback_generation):
        return _invalid("rollback_generation_invalid")
    layout = _Layout.for_engine(engine, Path(home))
    try:
        normalized_names = list(
            resolve_local_skill_locators(
                layout.legacy_local,
                registered_local_names,
            )
        )
    except SkillLayoutResolutionError as error:
        return _invalid(
            "registered_local_name_invalid",
            error=str(error),
        )

    rebuild = layout.legacy_local.parent / (
        f".skills-local.pool-rollback-{rollback_generation}"
    )
    copy_staging = layout.pool_root / (f".rollback-copy-{rollback_generation}")

    def finish_legacy_structure() -> PoolActivationResult | None:
        _restore_legacy_repo_bridge(layout=layout)
        active_repo = layout.active_root / "skills-repo"
        if (
            engine == "aicoding"
            and current_repo_delivery() is RepoDelivery.MOUNT
            and not (active_repo.exists() or active_repo.is_symlink())
        ):
            if restore_active_repo is None:
                return PoolActivationResult(
                    PoolActivationStatus.POST_CUTOVER_SYNC_PENDING,
                    {
                        "reason": "active_repo_restoration_required",
                        "path": str(active_repo),
                    },
                )
            try:
                active_marker = _read_active_marker(layout.active_marker)
                if active_marker is None:
                    return PoolActivationResult(
                        PoolActivationStatus.POST_CUTOVER_SYNC_PENDING,
                        {"reason": "active_repo_restoration_identity_unavailable"},
                    )
                migration_generation = active_marker.get("migration_generation")
                preparation_id = active_marker.get("preparation_id")
                if not isinstance(migration_generation, str) or not isinstance(
                    preparation_id, str
                ):
                    return PoolActivationResult(
                        PoolActivationStatus.POST_CUTOVER_SYNC_PENDING,
                        {"reason": "active_repo_restoration_identity_invalid"},
                    )
                restoration = restore_active_repo(
                    migration_generation,
                    preparation_id,
                )
            except ActiveRepoRestorationError as error:
                return PoolActivationResult(
                    PoolActivationStatus.POST_CUTOVER_SYNC_PENDING,
                    {
                        "reason": "active_repo_restoration_failed",
                        "restoration_reason": error.reason,
                        **error.evidence,
                    },
                )
            except OSError as error:
                return PoolActivationResult(
                    PoolActivationStatus.POST_CUTOVER_SYNC_PENDING,
                    {
                        "reason": "active_repo_restoration_failed",
                        "restoration_reason": "active_repo_restoration_io_error",
                        "error_type": type(error).__name__,
                        "errno": error.errno,
                    },
                )
            if not active_repo.is_dir() or active_repo.is_symlink():
                return PoolActivationResult(
                    PoolActivationStatus.POST_CUTOVER_SYNC_PENDING,
                    {
                        "reason": "active_repo_restoration_unverified",
                        "path": str(active_repo),
                        "restoration": restoration,
                    },
                )
        if layout.local_bridge != layout.legacy_local:
            layout.local_bridge.parent.mkdir(parents=True, exist_ok=True)
            if (
                not layout.local_bridge.exists()
                and not layout.local_bridge.is_symlink()
            ):
                layout.local_bridge.symlink_to(
                    layout.legacy_local,
                    target_is_directory=True,
                )
        layout.active_marker.unlink(missing_ok=True)
        return None

    try:
        if layout.legacy_local.is_dir() and not layout.legacy_local.is_symlink():
            for name in normalized_names:
                source = layout.legacy_local / name
                if not source.is_dir() or source.is_symlink():
                    return _data_inconsistent(
                        "rebuilt_legacy_source_invalid",
                        registered_name=name,
                        source=str(source),
                    )
            structure_failure = finish_legacy_structure()
            if structure_failure is not None:
                return structure_failure
            return PoolActivationResult(
                PoolActivationStatus.ALREADY_COMMITTED,
                {
                    "legacy_local": str(layout.legacy_local),
                    "source": str(layout.pool_local),
                },
            )
        legacy_local_absent = (
            not layout.legacy_local.exists() and not layout.legacy_local.is_symlink()
        )
        if not legacy_local_absent and (
            not layout.legacy_local.is_symlink()
            or _lexical_target(layout.legacy_local)
            != Path(os.path.abspath(layout.pool_local))
        ):
            return _invalid("pool_local_bridge_invalid")
        if not layout.pool_local.is_dir() or layout.pool_local.is_symlink():
            return _data_inconsistent(
                "pool_local_not_directory",
                source=str(layout.pool_local),
            )
        if rebuild.is_symlink() or (rebuild.exists() and not rebuild.is_dir()):
            return _invalid("rollback_temporary_path_occupied", path=str(rebuild))
        rebuild.mkdir(exist_ok=True)
        local_names, _rollback_baseline = mirror_local_tree(
            source_root=layout.pool_local,
            pool_local=rebuild,
            staging_root=copy_staging,
        )
        for name in normalized_names:
            source = rebuild / name
            if not source.is_dir() or source.is_symlink():
                return _data_inconsistent(
                    "registered_pool_source_invalid",
                    registered_name=name,
                    source=str(source),
                )
        if legacy_local_absent:
            os.replace(rebuild, layout.legacy_local)
        elif not exchange_paths(layout.legacy_local, rebuild):
            return PoolActivationResult(
                PoolActivationStatus.NOT_ATOMIC,
                {"reason": "atomic_exchange_unavailable"},
            )
        if not layout.legacy_local.is_dir() or layout.legacy_local.is_symlink():
            return _invalid("rollback_result_ambiguous")
        # The displaced object is only the compatibility symlink. Pool content
        # itself remains intact for evidence and forward recovery.
        rebuild.unlink(missing_ok=True)
        structure_failure = finish_legacy_structure()
        if structure_failure is not None:
            return structure_failure
        return PoolActivationResult(
            PoolActivationStatus.COMMITTED,
            {
                "legacy_local": str(layout.legacy_local),
                "source": str(layout.pool_local),
                "local_inventory": {
                    "registered": len(normalized_names),
                    "unregistered": len(set(local_names) - set(normalized_names)),
                    "total": len(local_names),
                },
            },
        )
    except OSError as error:
        committed = (
            layout.legacy_local.is_dir() and not layout.legacy_local.is_symlink()
        )
        return PoolActivationResult(
            (
                PoolActivationStatus.COMMITTED
                if committed
                else PoolActivationStatus.TRANSIENT_ERROR
            ),
            {
                "reason": (
                    "post_rollback_cleanup_failed"
                    if committed
                    else "rollback_filesystem_operation_failed"
                ),
                "error_type": type(error).__name__,
                "errno": error.errno,
            },
        )


def rollback_openclaw_pool(
    *,
    rollback_generation: str,
    registered_local_names: list[str],
    home: str | Path = "/home/admin",
    exchange_paths: Callable[[Path, Path], bool] = atomic_exchange_paths,
) -> PoolActivationResult:
    return _with_resolution_evidence(
        lambda: _rollback_pool(
            engine="openclaw",
            rollback_generation=rollback_generation,
            registered_local_names=registered_local_names,
            home=home,
            exchange_paths=exchange_paths,
        ),
        engine="openclaw",
        source_layout=MappingSourceLayout.LEGACY,
        registered_local_names=registered_local_names,
        home=home,
    )


def rollback_claude_code_pool(
    *,
    rollback_generation: str,
    registered_local_names: list[str],
    home: str | Path = "/home/admin",
    exchange_paths: Callable[[Path, Path], bool] = atomic_exchange_paths,
) -> PoolActivationResult:
    return _with_resolution_evidence(
        lambda: _rollback_pool(
            engine="claude_code",
            rollback_generation=rollback_generation,
            registered_local_names=registered_local_names,
            home=home,
            exchange_paths=exchange_paths,
        ),
        engine="claude_code",
        source_layout=MappingSourceLayout.LEGACY,
        registered_local_names=registered_local_names,
        home=home,
    )


def rollback_aicoding_pool(
    *,
    rollback_generation: str,
    registered_local_names: list[str],
    home: str | Path = "/home/admin",
    exchange_paths: Callable[[Path, Path], bool] = atomic_exchange_paths,
    restore_active_repo: Callable[[str, str], dict[str, object]] | None = None,
) -> PoolActivationResult:
    return _with_resolution_evidence(
        lambda: _rollback_pool(
            engine="aicoding",
            rollback_generation=rollback_generation,
            registered_local_names=registered_local_names,
            home=home,
            exchange_paths=exchange_paths,
            restore_active_repo=restore_active_repo,
        ),
        engine="aicoding",
        source_layout=MappingSourceLayout.LEGACY,
        registered_local_names=registered_local_names,
        home=home,
    )


def rollback_hermes_pool(
    *,
    rollback_generation: str,
    registered_local_names: list[str],
    home: str | Path = "/home/admin",
    exchange_paths: Callable[[Path, Path], bool] = atomic_exchange_paths,
) -> PoolActivationResult:
    return _with_resolution_evidence(
        lambda: _rollback_pool(
            engine="hermes",
            rollback_generation=rollback_generation,
            registered_local_names=registered_local_names,
            home=home,
            exchange_paths=exchange_paths,
        ),
        engine="hermes",
        source_layout=MappingSourceLayout.LEGACY,
        registered_local_names=registered_local_names,
        home=home,
    )
