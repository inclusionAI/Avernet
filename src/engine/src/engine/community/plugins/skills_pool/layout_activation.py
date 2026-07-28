"""文件型引擎 Skills Pool 的完整收敛与单向 Legacy storage 退役。"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from uuid import uuid4

from engine.community.core.skills.layout_planner import (
    LayoutIdentity,
    RuntimeLayoutContext,
    SkillLayoutResolutionError,
    resolve_filesystem_skill_layout,
    resolve_local_skill_locators,
    resolved_filesystem_layout_evidence,
)
from engine.community.core.skills.layout_planner import (
    ResolvedFilesystemLayoutPlan as _Layout,
)
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


class MappingSourceLayout(StrEnum):
    """Authority layout that mapping sources must belong to."""

    POOL = "pool"
    LEGACY = "legacy"


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


def mapping_sources_use_pool(
    *,
    engine: str,
    sources: list[str | Path],
    home: str | Path = "/home/admin",
) -> bool:
    """Return whether a mapping set selects canonical Pool managed sources.

    The regular bindpath API predates Skills Pool and has no explicit layout
    field.  Backend nevertheless sends the complete desired mapping set with
    canonical Pool sources once runtime cutover has committed.  Engine
    adapters use this shared classifier to avoid recreating retired Legacy
    corpus bridges during subsequent CRUD reconciliation. External mappings
    may coexist with managed Pool mappings; a managed Legacy/Pool mixture is
    rejected because it cannot represent one authoritative runtime layout.
    """

    layout = _Layout.for_engine(engine, Path(home))
    pool_roots = tuple(
        Path(os.path.abspath(root)) for root in (layout.pool_local, layout.pool_repo)
    )
    legacy_roots = tuple(
        Path(os.path.abspath(root))
        for root in (
            layout.legacy_local,
            layout.legacy_repo,
            layout.local_bridge,
            layout.repo_bridge,
        )
    )
    has_pool = False
    has_legacy = False
    for raw_source in sources:
        source = Path(raw_source)
        if not source.is_absolute():
            continue
        normalized = Path(os.path.abspath(source))
        if any(normalized.is_relative_to(root) for root in pool_roots):
            has_pool = True
        elif any(normalized.is_relative_to(root) for root in legacy_roots):
            has_legacy = True
    if has_pool and has_legacy:
        raise ValueError("mapping sources mix Legacy and Pool managed roots")
    if has_pool:
        return True
    if has_legacy or not sources:
        return False
    return _active_marker_selects_pool(layout=layout, engine=engine)


def _active_marker_selects_pool(*, layout: _Layout, engine: str) -> bool:
    """Resolve an external-only mapping set from the persisted runtime layout.

    External mappings intentionally survive Pool migration, so their source
    paths cannot identify the authoritative managed layout.  In that narrow
    case the runtime-owned active marker is the stable authority.
    """

    marker_path = layout.active_marker
    if not marker_path.exists() and not marker_path.is_symlink():
        return False
    marker = _read_active_marker(marker_path)
    if marker is None:
        raise ValueError("Pool active marker is unreadable or malformed")
    if (
        marker.get("engine") != engine
        or marker.get("layout_contract_version") != LAYOUT_CONTRACT_VERSION
        or not isinstance(marker.get("preparation_id"), str)
        or not marker["preparation_id"]
        or not isinstance(marker.get("migration_generation"), str)
        or not marker["migration_generation"]
        or marker.get("activation_state") not in {"finalizing", "active"}
    ):
        raise ValueError("Pool active marker contract is invalid")
    return True


def _retired_storage_entries(
    *,
    layout: _Layout,
    engine: str,
) -> tuple[Path, ...]:
    """Entries that must disappear once the active layout is Pool.

    AICoding and Hermes keep a stable repo namespace outside their engine's
    active scan root.  It remains a valid read-only bridge to canonical Pool.
    OpenClaw and Claude Code place the repo bridge inside the active root, so
    it must be retired together with the local corpus bridge.
    """

    entries = [layout.legacy_local, layout.local_bridge]
    if engine in {"openclaw", "claude_code"}:
        entries.append(layout.repo_bridge)
    return tuple(dict.fromkeys(entries))


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


def _read_active_marker(path: Path) -> dict[str, object] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (
        FileNotFoundError,
        OSError,
        UnicodeDecodeError,
        json.JSONDecodeError,
    ):
        return None
    return value if isinstance(value, dict) else None


def _write_active_marker(
    *,
    layout: _Layout,
    engine: str,
    migration_generation: str,
    preparation_id: str,
    mappings: list[SkillMapping],
    activation_state: str,
) -> None:
    value = {
        "engine": engine,
        "layout_contract_version": LAYOUT_CONTRACT_VERSION,
        "preparation_id": preparation_id,
        "migration_generation": migration_generation,
        "activation_state": activation_state,
        "updated_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        # Keep the key for compatibility with already-published image startup
        # scripts. Only finalizing carries recovery mappings; active is stable.
        "mappings": [],
    }
    if activation_state == "finalizing":
        # Recovery material for the short cutover window only. Once active,
        # skill mappings are mutable product state and must not become part of
        # the persisted layout contract.
        value["mappings"] = [
            {"source": mapping.source, "target": mapping.target} for mapping in mappings
        ]
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    temporary = layout.pool_root / (f".pool-active.tmp-{migration_generation}")
    with temporary.open("wb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, layout.active_marker)
    directory_fd = os.open(layout.pool_root, os.O_RDONLY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def _retire_bridge(path: Path, *, allowed_targets: tuple[Path, ...]) -> None:
    if not path.exists() and not path.is_symlink():
        return
    if not path.is_symlink():
        raise OSError(f"legacy storage entry is not a symlink: {path}")
    target = _lexical_target(path)
    normalized_targets = {
        Path(os.path.abspath(candidate)) for candidate in allowed_targets
    }
    if target not in normalized_targets:
        raise OSError(f"legacy storage entry points elsewhere: {path}")
    path.unlink()


def _finalize_active_root(
    *,
    layout: _Layout,
    engine: str,
    migration_generation: str,
    preparation_id: str,
    mappings: list[SkillMapping],
    quarantine: Path,
    retire_path: Callable[[Path, Path], None],
) -> PoolActivationResult | None:
    published = publish_pool_mappings(
        mappings=mappings,
        home=layout.pool_root.parents[2],
        engine=engine,
    )
    if not published.published:
        return PoolActivationResult(
            PoolActivationStatus.POST_CUTOVER_SYNC_PENDING,
            {
                "reason": "pool_mapping_publish_failed",
                "mapping": published.evidence,
            },
        )
    verified = verify_skill_mappings(
        mappings=mappings,
        home=layout.pool_root.parents[2],
        engine=engine,
    )
    if not verified.valid:
        return PoolActivationResult(
            PoolActivationStatus.POST_CUTOVER_SYNC_PENDING,
            {
                "reason": "pool_mapping_verify_failed",
                "mapping": verified.evidence,
            },
        )
    _write_active_marker(
        layout=layout,
        engine=engine,
        migration_generation=migration_generation,
        preparation_id=preparation_id,
        mappings=mappings,
        activation_state="finalizing",
    )
    _residue_evidence, residue_failure = _capture_recreated_legacy_local(
        layout=layout,
        quarantine=quarantine,
        retire_path=retire_path,
    )
    if residue_failure is not None:
        return residue_failure
    _retire_bridge(
        layout.local_bridge,
        allowed_targets=(layout.legacy_local, layout.pool_local),
    )
    if engine in {"openclaw", "claude_code"}:
        _retire_bridge(
            layout.repo_bridge,
            allowed_targets=(layout.legacy_repo, layout.pool_repo),
        )
    if layout.legacy_local != layout.local_bridge:
        _retire_bridge(
            layout.legacy_local,
            allowed_targets=(layout.pool_local,),
        )
    remaining_storage_entries = [
        str(path)
        for path in _retired_storage_entries(layout=layout, engine=engine)
        if path.exists() or path.is_symlink()
    ]
    if remaining_storage_entries:
        return PoolActivationResult(
            PoolActivationStatus.POST_CUTOVER_SYNC_PENDING,
            {
                "reason": "legacy_storage_entries_remain",
                "paths": sorted(remaining_storage_entries),
            },
        )
    _write_active_marker(
        layout=layout,
        engine=engine,
        migration_generation=migration_generation,
        preparation_id=preparation_id,
        mappings=mappings,
        activation_state="active",
    )
    return None


def _lexical_target(link: Path) -> Path:
    target = link.readlink()
    if not target.is_absolute():
        target = link.parent / target
    return Path(os.path.abspath(target))


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


def _managed_source_failure(
    source: Path,
    *,
    roots: tuple[Path, ...],
    outside_reason: str,
) -> str | None:
    """证明 source 是指定 layout root 内真实、可读的技能目录。"""

    normalized = Path(os.path.abspath(source))
    containing_root: Path | None = None
    for root in roots:
        normalized_root = Path(os.path.abspath(root))
        if normalized.is_relative_to(normalized_root):
            containing_root = normalized_root
            break
    if containing_root is None:
        return outside_reason
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


def _source_failure(
    layout: _Layout,
    source: Path,
    *,
    source_layout: MappingSourceLayout,
) -> str | None:
    if source_layout is MappingSourceLayout.LEGACY:
        return _managed_source_failure(
            source,
            roots=(layout.legacy_local, layout.legacy_repo),
            outside_reason="source_outside_legacy",
        )
    return _managed_source_failure(
        source,
        roots=(layout.pool_local, layout.pool_repo),
        outside_reason="source_outside_pool",
    )


def _source_for_layout(
    layout: _Layout,
    pool_source: Path,
    *,
    source_layout: MappingSourceLayout,
) -> Path:
    if source_layout is MappingSourceLayout.POOL:
        return pool_source
    normalized = Path(os.path.abspath(pool_source))
    for pool_root, legacy_root in (
        (layout.pool_local, layout.legacy_local),
        (layout.pool_repo, layout.legacy_repo),
    ):
        normalized_pool_root = Path(os.path.abspath(pool_root))
        if normalized.is_relative_to(normalized_pool_root):
            return legacy_root / normalized.relative_to(normalized_pool_root)
    return pool_source


def _active_entry_inventory(
    layout: _Layout,
) -> tuple[dict[Path, Path], tuple[Path, ...], tuple[Path, ...]]:
    managed: dict[Path, Path] = {}
    external: list[Path] = []
    occupied: list[Path] = []
    reserved = {layout.local_bridge, layout.repo_bridge}
    for entry in sorted(layout.active_root.iterdir(), key=lambda path: path.name):
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
    source_layout: MappingSourceLayout = MappingSourceLayout.POOL,
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
            reason = (
                "source_outside_legacy"
                if source_layout is MappingSourceLayout.LEGACY
                else "source_outside_pool"
            )
        else:
            reason = (
                _source_failure(
                    layout,
                    source,
                    source_layout=source_layout,
                )
                or ""
            )
        if not reason and (
            not target.is_absolute()
            or target.parent != layout.active_root
            or target
            in {
                layout.legacy_local,
                layout.legacy_repo,
                layout.local_bridge,
                layout.repo_bridge,
            }
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
    for target, pool_source in discovered.items():
        source = _source_for_layout(
            layout,
            pool_source,
            source_layout=source_layout,
        )
        reason = _source_failure(
            layout,
            source,
            source_layout=source_layout,
        )
        if reason:
            failures.append(
                {
                    "source": str(source),
                    "target": str(target),
                    "reason": f"managed_{reason}",
                }
            )
            continue
        if (
            source_layout is MappingSourceLayout.POOL
            and target in desired
            and desired[target] != source
        ):
            conflicts.append(
                {
                    "target": str(target),
                    "requested_source": str(desired[target]),
                    "existing_source": str(source),
                }
            )
        if source_layout is MappingSourceLayout.POOL or target not in desired:
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
    if temporary != quarantine:
        if cleanup_pending:
            raise OSError(f"cutover quarantine already exists: {quarantine}")
        temporary.rename(quarantine)
        cleanup_pending = True
    return _PostCutoverFinalization(
        post_sync=post_sync,
        cleanup_pending=cleanup_pending,
    )


def _capture_recreated_legacy_local(
    *,
    layout: _Layout,
    quarantine: Path,
    retire_path: Callable[[Path, Path], None],
    max_captures: int = 3,
) -> tuple[dict[str, object], PoolActivationResult | None]:
    """收集 rename 后被容器内旧路径 writer 重新创建的极窄窗口增量。

    ``begin_cutover`` 后 Backend 已写 canonical Pool，但运行中 Agent 仍可能
    在几秒窗口内直接重建 Legacy local。这里只将 Pool 中不存在的新路径
    best-effort 合入；冲突保留 Pool 权威版本，完整 residue 留在同 generation
    quarantine 供审计和后续清理。
    """

    captured: list[dict[str, object]] = []
    residue_index = 1
    existing_residues: list[tuple[int, Path]] = []
    if quarantine.parent.is_dir() and not quarantine.parent.is_symlink():
        for entry in quarantine.parent.iterdir():
            match = re.fullmatch(r"skills-local-residue-([1-9][0-9]*)", entry.name)
            if match is None:
                continue
            if not entry.is_dir() or entry.is_symlink():
                return (
                    {
                        "captured_count": len(captured),
                        "captures": captured,
                    },
                    _invalid(
                        "legacy_local_residue_invalid",
                        path=str(entry),
                    ),
                )
            existing_residues.append((int(match.group(1)), entry))
    for index, residue in sorted(existing_residues):
        post_sync = merge_post_cutover_changes(
            source_root=residue,
            pool_local=layout.pool_local,
            baseline={},
        )
        captured.append(
            {
                "path": str(residue),
                "post_sync": post_sync,
                "replayed": True,
            }
        )
        residue_index = max(residue_index, index + 1)

    for _capture_index in range(1, max_captures + 1):
        if not layout.legacy_local.exists() and not layout.legacy_local.is_symlink():
            return (
                {
                    "captured_count": len(captured),
                    "captures": captured,
                },
                None,
            )
        if layout.legacy_local.is_symlink():
            return (
                {
                    "captured_count": len(captured),
                    "captures": captured,
                },
                None,
            )
        if not layout.legacy_local.is_dir():
            return (
                {
                    "captured_count": len(captured),
                    "captures": captured,
                },
                _invalid(
                    "legacy_local_residue_invalid",
                    path=str(layout.legacy_local),
                ),
            )

        while residue_index <= 128:
            residue = quarantine.parent / f"skills-local-residue-{residue_index}"
            residue_index += 1
            if not residue.exists() and not residue.is_symlink():
                break
        else:
            return (
                {
                    "captured_count": len(captured),
                    "captures": captured,
                },
                _invalid(
                    "legacy_local_residue_quarantine_exhausted",
                    path=str(quarantine.parent),
                ),
            )
        retire_path(layout.legacy_local, residue)
        post_sync = merge_post_cutover_changes(
            source_root=residue,
            pool_local=layout.pool_local,
            baseline={},
        )
        captured.append(
            {
                "path": str(residue),
                "post_sync": post_sync,
            }
        )

    if layout.legacy_local.exists() or layout.legacy_local.is_symlink():
        return (
            {
                "captured_count": len(captured),
                "captures": captured,
            },
            PoolActivationResult(
                PoolActivationStatus.POST_CUTOVER_SYNC_PENDING,
                {
                    "reason": "legacy_local_recreated_during_cutover",
                    "captured_count": len(captured),
                },
            ),
        )
    return (
        {
            "captured_count": len(captured),
            "captures": captured,
        },
        None,
    )


def _ensure_quarantine_generation_owned(
    *,
    quarantine: Path,
    engine: str,
    migration_generation: str,
    preparation_id: str,
    baseline_path: Path,
) -> PoolActivationResult | None:
    """以 generation 目录和 owner marker 证明 quarantine 写入所有权。

    初次调用以 exclusive mkdir 取得 generation namespace；后续调用必须
    匹配 owner marker。兼容旧实现已完成 rename、但尚未写 owner 的可恢复
    状态时，只允许 baseline 存在且目录内全部是已知 quarantine 项。
    """

    generation_dir = quarantine.parent
    quarantine_root = generation_dir.parent
    quarantine_root.mkdir(parents=True, exist_ok=True)
    created = False
    try:
        generation_dir.mkdir()
        created = True
    except FileExistsError:
        if not generation_dir.is_dir() or generation_dir.is_symlink():
            return _invalid(
                "cutover_quarantine_generation_invalid",
                path=str(generation_dir),
            )

    owner_path = generation_dir / ".owner.json"
    expected_owner = {
        "engine": engine,
        "migration_generation": migration_generation,
        "preparation_id": preparation_id,
    }
    known_entry = re.compile(
        r"(?:skills-local(?:-residue-[1-9][0-9]*)?|"
        r"\.owner\.json|\.owner\.invalid-[A-Fa-f0-9]+|"
        r"\.owner\.tmp-[A-Fa-f0-9]+)"
    )
    unknown_entries = sorted(
        entry.name
        for entry in generation_dir.iterdir()
        if known_entry.fullmatch(entry.name) is None
    )

    if owner_path.is_file() and not owner_path.is_symlink():
        try:
            owner = json.loads(owner_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            if (
                not baseline_path.is_file()
                or baseline_path.is_symlink()
                or unknown_entries
            ):
                return _invalid(
                    "cutover_quarantine_owner_invalid",
                    path=str(owner_path),
                )
            owner_path.rename(generation_dir / f".owner.invalid-{uuid4().hex}")
            owner = None
        if owner == expected_owner:
            return None
        if owner is not None:
            return _invalid(
                "cutover_quarantine_owner_mismatch",
                path=str(owner_path),
            )
    if owner_path.exists() or owner_path.is_symlink():
        return _invalid(
            "cutover_quarantine_owner_invalid",
            path=str(owner_path),
        )

    if not created and (
        not baseline_path.is_file() or baseline_path.is_symlink() or unknown_entries
    ):
        return _invalid(
            "cutover_quarantine_ownership_unproven",
            path=str(generation_dir),
            unknown_entries=unknown_entries,
        )

    owner_temporary = generation_dir / f".owner.tmp-{uuid4().hex}"
    try:
        with owner_temporary.open("x", encoding="utf-8") as stream:
            json.dump(
                expected_owner,
                stream,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(owner_temporary, owner_path)
        except FileExistsError:
            try:
                raced_owner = json.loads(owner_path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                return _invalid(
                    "cutover_quarantine_owner_raced_invalid",
                    path=str(owner_path),
                )
            if raced_owner != expected_owner:
                return _invalid(
                    "cutover_quarantine_owner_raced_mismatch",
                    path=str(owner_path),
                )
        directory_fd = os.open(generation_dir, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except FileExistsError:
        return _invalid(
            "cutover_quarantine_owner_temporary_raced",
            path=str(owner_temporary),
        )
    finally:
        owner_temporary.unlink(missing_ok=True)
    return None


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
    active_marker = _read_active_marker(layout.active_marker)
    if active_marker is not None:
        if (
            active_marker.get("engine") != engine
            or active_marker.get("layout_contract_version") != LAYOUT_CONTRACT_VERSION
            or active_marker.get("preparation_id") != preparation_id
            or active_marker.get("migration_generation") != migration_generation
            or active_marker.get("activation_state") not in {"finalizing", "active"}
        ):
            return _invalid("active_marker_identity_mismatch")
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
                "quarantine_cleanup_pending": (
                    quarantine.exists() or quarantine.is_symlink()
                ),
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


def _rollback_pool(
    *,
    engine: str,
    rollback_generation: str,
    registered_local_names: list[str],
    home: str | Path = "/home/admin",
    exchange_paths: Callable[[Path, Path], bool] = atomic_exchange_paths,
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
        layout.repo_bridge.parent.mkdir(parents=True, exist_ok=True)
        if not layout.repo_bridge.exists() and not layout.repo_bridge.is_symlink():
            layout.repo_bridge.symlink_to(
                layout.pool_repo,
                target_is_directory=True,
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
) -> PoolActivationResult:
    return _with_resolution_evidence(
        lambda: _rollback_pool(
            engine="aicoding",
            rollback_generation=rollback_generation,
            registered_local_names=registered_local_names,
            home=home,
            exchange_paths=exchange_paths,
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


def verify_skill_mappings(
    *,
    mappings: list[SkillMapping],
    home: str | Path = "/home/admin",
    engine: str = "openclaw",
    source_layout: MappingSourceLayout = MappingSourceLayout.POOL,
) -> MappingVerificationResult:
    """验证受管激活入口精确解析到声明 layout 的 source。"""

    layout = _Layout.for_engine(engine, Path(home))
    plan = _mapping_plan(
        layout=layout,
        mappings=mappings,
        source_layout=source_layout,
    )
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
    source_layout: MappingSourceLayout = MappingSourceLayout.POOL,
) -> MappingPublishResult:
    """按声明 layout 对齐全部受管 mapping，并保留外部入口。"""

    layout = _Layout.for_engine(engine, Path(home))
    plan = _mapping_plan(
        layout=layout,
        mappings=mappings,
        source_layout=source_layout,
    )
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
    "MappingPublishResult",
    "MappingSourceLayout",
    "MappingVerificationResult",
    "PoolActivationResult",
    "PoolActivationStatus",
    "SkillMapping",
    "activate_aicoding_pool",
    "activate_claude_code_pool",
    "activate_hermes_pool",
    "activate_openclaw_pool",
    "atomic_exchange_paths",
    "mapping_sources_use_pool",
    "publish_pool_mappings",
    "rollback_aicoding_pool",
    "rollback_claude_code_pool",
    "rollback_hermes_pool",
    "rollback_openclaw_pool",
    "verify_skill_mappings",
]
