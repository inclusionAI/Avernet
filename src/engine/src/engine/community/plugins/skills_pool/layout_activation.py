"""文件型引擎 Skills Pool 的完整收敛与原子 local bridge 切换。"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Callable

from engine.community.plugins.skills_pool.layout_atomic import (
    atomic_exchange_paths,
)
from engine.community.plugins.skills_pool.layout_probe import (
    LAYOUT_CONTRACT_VERSION,
    RuntimeLayoutInspectionStatus,
    inspect_runtime_layout,
)
from engine.community.plugins.skills_pool.layout_sync import (
    Manifest,
    load_baseline_manifest,
    merge_post_cutover_changes,
    mirror_local_tree,
    write_baseline_manifest,
)


class PoolActivationStatus(StrEnum):
    COMMITTED = "COMMITTED"
    ALREADY_COMMITTED = "ALREADY_COMMITTED"
    ACTIVE_ENTRY_CONFLICT = "ACTIVE_ENTRY_CONFLICT"
    DATA_INCONSISTENT = "DATA_INCONSISTENT"
    INVALID = "INVALID"
    TRANSIENT_ERROR = "TRANSIENT_ERROR"
    POST_CUTOVER_SYNC_PENDING = "POST_CUTOVER_SYNC_PENDING"
    NOT_ATOMIC = "NOT_ATOMIC"


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

    def to_data(self) -> dict[str, object]:
        return {"valid": self.valid, "evidence": self.evidence}


@dataclass(frozen=True, slots=True)
class MappingPublishResult:
    published: bool
    evidence: dict[str, object]

    def to_data(self) -> dict[str, object]:
        return {"published": self.published, "evidence": self.evidence}


@dataclass(frozen=True, slots=True)
class _Layout:
    legacy_root: Path
    legacy_local: Path
    pool_root: Path
    pool_local: Path
    pool_repo: Path
    legacy_repo: Path
    local_bridge: Path
    repo_bridge: Path

    @classmethod
    def for_home(cls, home: Path) -> "_Layout":
        return cls.for_engine("openclaw", home)

    @classmethod
    def for_engine(cls, engine: str, home: Path) -> "_Layout":
        if engine == "aicoding":
            workspace = home / ".aicoding" / "workspace"
            legacy_root = home / ".claude" / "skills"
            legacy_local = workspace / "skills" / "skills-local"
            pool_root = workspace / "skills-pool"
            return cls(
                legacy_root=legacy_root,
                legacy_local=legacy_local,
                pool_root=pool_root,
                pool_local=pool_root / "skills-local",
                pool_repo=pool_root / "skills-repo",
                legacy_repo=home / ".aicoding" / "skills-repo",
                local_bridge=legacy_root / "skills-local",
                repo_bridge=home / ".aicoding" / "skills-repo",
            )
        if engine == "claude_code":
            workspace = home / ".claude_code" / "workspace"
            legacy_root = home / ".claude" / "skills"
            legacy_local = workspace / "skills" / "skills-local"
            pool_root = workspace / "skills-pool"
            return cls(
                legacy_root=legacy_root,
                legacy_local=legacy_local,
                pool_root=pool_root,
                pool_local=pool_root / "skills-local",
                pool_repo=pool_root / "skills-repo",
                legacy_repo=home / ".claude_code" / "skills-repo",
                local_bridge=legacy_root / "skills-local",
                repo_bridge=legacy_root / "skills-repo",
            )
        if engine != "openclaw":
            raise ValueError(f"unsupported filesystem Pool engine: {engine}")
        workspace = home / ".openclaw" / "workspace"
        legacy_root = workspace / "skills"
        pool_root = workspace / "skills-pool"
        legacy_local = legacy_root / "skills-local"
        legacy_repo = legacy_root / "skills-repo"
        return cls(
            legacy_root=legacy_root,
            legacy_local=legacy_local,
            pool_root=pool_root,
            pool_local=pool_root / "skills-local",
            pool_repo=pool_root / "skills-repo",
            legacy_repo=legacy_repo,
            local_bridge=legacy_local,
            repo_bridge=legacy_repo,
        )


@dataclass(frozen=True, slots=True)
class _MappingPlan:
    managed: dict[Path, Path]
    external: tuple[Path, ...]
    failures: tuple[dict[str, str], ...]
    conflicts: tuple[dict[str, str], ...]


@dataclass(frozen=True, slots=True)
class _PostCutoverFinalization:
    post_sync: dict[str, object]
    cleanup_pending: bool
    failure: PoolActivationResult | None = None


def _lexical_target(link: Path) -> Path:
    target = link.readlink()
    if not target.is_absolute():
        target = link.parent / target
    return Path(os.path.abspath(target))


def _canonical_pool_source(layout: _Layout, source: Path) -> Path | None:
    source = Path(os.path.abspath(source))
    for root, pool_root in (
        (layout.legacy_local, layout.pool_local),
        (layout.local_bridge, layout.pool_local),
        (layout.pool_local, layout.pool_local),
        (layout.legacy_repo, layout.pool_repo),
        (layout.repo_bridge, layout.pool_repo),
        (layout.pool_repo, layout.pool_repo),
    ):
        normalized_root = Path(os.path.abspath(root))
        if source.is_relative_to(normalized_root):
            return pool_root / source.relative_to(normalized_root)
    return None


def _pool_source_failure(layout: _Layout, source: Path) -> str | None:
    """证明 source 是 canonical Pool root 内真实、可读的技能目录。"""

    normalized = Path(os.path.abspath(source))
    containing_root: Path | None = None
    for root in (layout.pool_local, layout.pool_repo):
        normalized_root = Path(os.path.abspath(root))
        if normalized.is_relative_to(normalized_root):
            containing_root = normalized_root
            break
    if containing_root is None:
        return "source_outside_pool"
    if not normalized.exists():
        return "source_missing"
    try:
        if not normalized.resolve(strict=True).is_relative_to(
            containing_root.resolve(strict=True)
        ):
            return "source_escapes_pool"
    except OSError:
        return "source_unreadable"
    if not normalized.is_dir():
        return "source_not_directory"
    unreadable = _first_unreadable_path(normalized)
    if unreadable is not None:
        return "source_unreadable"
    return None


def _active_entry_inventory(
    layout: _Layout,
) -> tuple[dict[Path, Path], tuple[Path, ...], tuple[Path, ...]]:
    managed: dict[Path, Path] = {}
    external: list[Path] = []
    occupied: list[Path] = []
    reserved = {layout.local_bridge, layout.repo_bridge}
    for entry in sorted(layout.legacy_root.iterdir(), key=lambda path: path.name):
        if entry in reserved or entry.name.startswith(".skills-local.pool-cutover-"):
            continue
        if not entry.is_symlink():
            occupied.append(entry)
            continue
        canonical = _canonical_pool_source(layout, _lexical_target(entry))
        if canonical is None:
            external.append(entry)
        else:
            managed[entry] = canonical
    return managed, tuple(external), tuple(occupied)


def _mapping_plan(
    *,
    layout: _Layout,
    mappings: list[SkillMapping],
) -> _MappingPlan:
    desired: dict[Path, Path] = {}
    failures: list[dict[str, str]] = []
    conflicts: list[dict[str, str]] = []
    for mapping in mappings:
        source_input = Path(mapping.source)
        source = Path(os.path.abspath(source_input))
        target = Path(mapping.target)
        reason = ""
        if not source_input.is_absolute():
            reason = "source_outside_pool"
        else:
            reason = _pool_source_failure(layout, source) or ""
        if not reason and (
            not target.is_absolute()
            or target.parent != layout.legacy_root
            or target in {layout.legacy_local, layout.legacy_repo}
        ):
            reason = "target_invalid"
        elif not reason and target in desired and desired[target] != source:
            conflicts.append(
                {
                    "target": str(target),
                    "requested_source": str(source),
                    "existing_source": str(desired[target]),
                }
            )
            continue
        if reason:
            failures.append(
                {"source": str(source), "target": str(target), "reason": reason}
            )
        else:
            desired[target] = source

    discovered, external, occupied = _active_entry_inventory(layout)
    for target, source in discovered.items():
        reason = _pool_source_failure(layout, source)
        if reason:
            failures.append(
                {
                    "source": str(source),
                    "target": str(target),
                    "reason": f"managed_{reason}",
                }
            )
            continue
        if target in desired and desired[target] != source:
            conflicts.append(
                {
                    "target": str(target),
                    "requested_source": str(desired[target]),
                    "existing_source": str(source),
                }
            )
        desired[target] = source
    for target in occupied:
        if target in desired:
            conflicts.append(
                {
                    "target": str(target),
                    "requested_source": str(desired[target]),
                    "existing_source": "<occupied-non-symlink>",
                }
            )
    for target in external:
        desired.pop(target, None)
    return _MappingPlan(
        managed=desired,
        external=external,
        failures=tuple(failures),
        conflicts=tuple(conflicts),
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


def _first_unreadable_path(root: Path) -> Path | None:
    for current_root, directory_names, file_names in os.walk(
        root,
        followlinks=False,
    ):
        current = Path(current_root)
        if not os.access(current, os.R_OK | os.X_OK):
            return current
        for name in [*directory_names, *file_names]:
            entry = current / name
            if entry.is_symlink():
                continue
            required = os.R_OK | os.X_OK if entry.is_dir() else os.R_OK
            if not os.access(entry, required):
                return entry
    return None


def _finalize_post_cutover(
    *,
    temporary: Path,
    pool_local: Path,
    quarantine: Path,
    baseline_path: Path,
    baseline: Manifest | None = None,
) -> _PostCutoverFinalization:
    """完成交换后的三方合并与审计快照归档；首次执行与重试共用。"""

    try:
        effective_baseline = (
            baseline if baseline is not None else load_baseline_manifest(baseline_path)
        )
        post_sync = merge_post_cutover_changes(
            source_root=temporary,
            pool_local=pool_local,
            baseline=effective_baseline,
        )
    except (OSError, ValueError) as error:
        return _PostCutoverFinalization(
            post_sync={},
            cleanup_pending=False,
            failure=PoolActivationResult(
                PoolActivationStatus.POST_CUTOVER_SYNC_PENDING,
                {
                    "reason": "post_cutover_sync_failed",
                    "error_type": type(error).__name__,
                    "errno": getattr(error, "errno", None),
                },
            ),
        )

    quarantine.parent.mkdir(parents=True, exist_ok=True)
    cleanup_pending = quarantine.exists() or quarantine.is_symlink()
    if not cleanup_pending:
        temporary.rename(quarantine)
    baseline_path.unlink(missing_ok=True)
    return _PostCutoverFinalization(
        post_sync=post_sync,
        cleanup_pending=cleanup_pending,
    )


def _activate_pool(
    *,
    engine: str,
    migration_generation: str,
    preparation_id: str,
    registered_local_names: list[str],
    mappings: list[SkillMapping],
    home: str | Path = "/home/admin",
    repo_is_mounted: Callable[[Path], bool] | None = None,
    exchange_paths: Callable[[Path, Path], bool] = atomic_exchange_paths,
    before_post_sync: Callable[[], None] | None = None,
) -> PoolActivationResult:
    """校验登记事实、同步完整 local，并原子提交 Legacy→Pool bridge。"""

    home_path = Path(home)
    layout = _Layout.for_engine(engine, home_path)
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", migration_generation):
        return _invalid("migration_generation_invalid")

    inspection = inspect_runtime_layout(
        engine=engine,
        expected_contract_version=LAYOUT_CONTRACT_VERSION,
        home=home_path,
        repo_is_mounted=repo_is_mounted or os.path.ismount,
    )
    if (
        inspection.status is not RuntimeLayoutInspectionStatus.READY
        or inspection.preparation_id != preparation_id
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
    normalized_names: list[str] = []
    for name in registered_local_names:
        path = Path(name)
        if (
            not name
            or path.is_absolute()
            or len(path.parts) != 1
            or name in {".", ".."}
        ):
            return _invalid("registered_local_name_invalid", name=name)
        if name not in normalized_names:
            normalized_names.append(name)
    try:
        if layout.legacy_local.is_symlink():
            if _lexical_target(layout.legacy_local) != Path(
                os.path.abspath(layout.pool_local)
            ):
                return _invalid("legacy_local_bridge_invalid")
            finalization = _PostCutoverFinalization({}, False)
            if temporary.is_dir() and not temporary.is_symlink():
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
            return PoolActivationResult(
                PoolActivationStatus.ALREADY_COMMITTED,
                {
                    "bridge": str(layout.legacy_local),
                    "target": str(layout.pool_local),
                    "quarantine": str(quarantine),
                    "quarantine_cleanup_pending": (finalization.cleanup_pending),
                    "post_sync": finalization.post_sync,
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

        local_names = mirror_local_tree(
            source_root=layout.legacy_local,
            pool_local=layout.pool_local,
            staging_root=layout.pool_root / f".final-sync-{migration_generation}",
        )
        baseline = write_baseline_manifest(
            pool_local=layout.pool_local,
            local_names=local_names,
            manifest_path=baseline_path,
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

        if temporary.exists() or temporary.is_symlink():
            return _invalid("cutover_temporary_path_occupied", path=str(temporary))
        temporary.symlink_to(layout.pool_local, target_is_directory=True)
        if not exchange_paths(layout.legacy_local, temporary):
            temporary.unlink(missing_ok=True)
            return PoolActivationResult(
                PoolActivationStatus.NOT_ATOMIC,
                {"reason": "atomic_exchange_unavailable"},
            )

        if not layout.legacy_local.is_symlink() or _lexical_target(
            layout.legacy_local
        ) != Path(os.path.abspath(layout.pool_local)):
            return _invalid("cutover_result_ambiguous")

        if before_post_sync is not None:
            before_post_sync()
        finalization = _finalize_post_cutover(
            temporary=temporary,
            pool_local=layout.pool_local,
            quarantine=quarantine,
            baseline_path=baseline_path,
            baseline=baseline,
        )
        if finalization.failure is not None:
            return finalization.failure
        return PoolActivationResult(
            PoolActivationStatus.COMMITTED,
            {
                "bridge": str(layout.legacy_local),
                "target": str(layout.pool_local),
                "quarantine": str(quarantine),
                "quarantine_cleanup_pending": finalization.cleanup_pending,
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
            },
        )
    except OSError as error:
        committed = layout.legacy_local.is_symlink() and _lexical_target(
            layout.legacy_local
        ) == Path(os.path.abspath(layout.pool_local))
        return PoolActivationResult(
            (
                PoolActivationStatus.COMMITTED
                if committed
                else PoolActivationStatus.TRANSIENT_ERROR
            ),
            {
                "reason": (
                    "post_cutover_cleanup_failed"
                    if committed
                    else "filesystem_operation_failed"
                ),
                "error_type": type(error).__name__,
                "errno": error.errno,
            },
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
    before_post_sync: Callable[[], None] | None = None,
) -> PoolActivationResult:
    return _activate_pool(
        engine="openclaw",
        migration_generation=migration_generation,
        preparation_id=preparation_id,
        registered_local_names=registered_local_names,
        mappings=mappings,
        home=home,
        repo_is_mounted=repo_is_mounted,
        exchange_paths=exchange_paths,
        before_post_sync=before_post_sync,
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
    before_post_sync: Callable[[], None] | None = None,
) -> PoolActivationResult:
    return _activate_pool(
        engine="claude_code",
        migration_generation=migration_generation,
        preparation_id=preparation_id,
        registered_local_names=registered_local_names,
        mappings=mappings,
        home=home,
        repo_is_mounted=repo_is_mounted,
        exchange_paths=exchange_paths,
        before_post_sync=before_post_sync,
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
    before_post_sync: Callable[[], None] | None = None,
) -> PoolActivationResult:
    return _activate_pool(
        engine="aicoding",
        migration_generation=migration_generation,
        preparation_id=preparation_id,
        registered_local_names=registered_local_names,
        mappings=mappings,
        home=home,
        repo_is_mounted=repo_is_mounted,
        exchange_paths=exchange_paths,
        before_post_sync=before_post_sync,
    )


def verify_skill_mappings(
    *,
    mappings: list[SkillMapping],
    home: str | Path = "/home/admin",
    engine: str = "openclaw",
) -> MappingVerificationResult:
    """验证受管激活入口精确解析到请求中的 Pool source。"""

    layout = _Layout.for_engine(engine, Path(home))
    plan = _mapping_plan(layout=layout, mappings=mappings)
    failures: list[dict[str, str]] = []
    failures.extend(plan.failures)
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
            "failures": failures,
        },
    )


def publish_pool_mappings(
    *,
    mappings: list[SkillMapping],
    home: str | Path = "/home/admin",
    engine: str = "openclaw",
) -> MappingPublishResult:
    """按文件系统事实对齐全部受管 Pool mapping，并保留外部入口。"""

    layout = _Layout.for_engine(engine, Path(home))
    plan = _mapping_plan(layout=layout, mappings=mappings)
    if plan.conflicts:
        return MappingPublishResult(
            published=False,
            evidence={
                "reason": "managed_active_entry_conflict",
                "conflicts": list(plan.conflicts),
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
        for target, source in plan.managed.items():
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
    return MappingPublishResult(
        published=True,
        evidence={
            "total": len(plan.managed),
            "created": created,
            "updated": updated,
            "kept": kept,
            "removed": removed,
            "external_ignored": [str(path) for path in plan.external],
        },
    )


__all__ = [
    "MappingVerificationResult",
    "MappingPublishResult",
    "PoolActivationResult",
    "PoolActivationStatus",
    "SkillMapping",
    "activate_aicoding_pool",
    "activate_claude_code_pool",
    "activate_openclaw_pool",
    "atomic_exchange_paths",
    "publish_pool_mappings",
    "verify_skill_mappings",
]
