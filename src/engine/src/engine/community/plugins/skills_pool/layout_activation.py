"""文件型引擎 Skills Pool 的完整收敛与单向 Legacy storage 退役。"""

from __future__ import annotations

import json
import logging
import os
import re
import stat
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from uuid import uuid4

from engine.community.config import RepoDelivery, current_repo_delivery
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

logger = logging.getLogger(__name__)
_SLOW_SOURCE_LIMIT = 3


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
        return data


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
        Path(os.path.abspath(root))
        for root in (layout.pool_local, layout.pool_repo, layout.pool_center)
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
    stable_repo_root = (
        Path(os.path.abspath(layout.repo_bridge))
        if engine in {"aicoding", "hermes"}
        else None
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
        elif stable_repo_root is not None and normalized.is_relative_to(
            stable_repo_root
        ):
            if _active_marker_selects_pool(layout=layout, engine=engine):
                has_pool = True
            else:
                has_legacy = True
        elif any(normalized.is_relative_to(root) for root in legacy_roots):
            has_legacy = True
    if has_pool and has_legacy:
        raise ValueError("mapping sources mix Legacy and Pool managed roots")
    if has_pool:
        return True
    if has_legacy:
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
    if engine == "aicoding":
        entries.append(layout.active_root / "skills-repo")
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


def _publish_structural_bridge(path: Path, target: Path) -> None:
    """Atomically publish a descriptor-owned directory symlink."""

    target = Path(os.path.abspath(target))
    if path.is_symlink() and _lexical_target(path) == target:
        return
    if path.exists() and not path.is_symlink():
        raise OSError(f"stable structure entry is not a symlink: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.pool-bridge")
    try:
        temporary.symlink_to(target, target_is_directory=True)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _restore_legacy_repo_bridge(
    *,
    layout: _Layout,
) -> None:
    """Restore the descriptor's Legacy repo view after rollback."""

    repo_delivery = current_repo_delivery()
    if repo_delivery is RepoDelivery.MOUNT or layout.repo_bridge == layout.legacy_repo:
        target = layout.pool_repo
    else:
        target = layout.legacy_repo
    _publish_structural_bridge(layout.repo_bridge, target)


def _finalize_active_root(
    *,
    layout: _Layout,
    engine: str,
    migration_generation: str,
    preparation_id: str,
    mappings: list[SkillMapping],
    quarantine: Path,
    retire_path: Callable[[Path, Path], None],
    retire_active_repo: Callable[[str, str], dict[str, object]] | None,
    repo_is_mounted: Callable[[Path], bool],
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
    active_repo = layout.active_root / "skills-repo"
    if engine == "aicoding" and (active_repo.exists() or active_repo.is_symlink()):
        if retire_active_repo is None:
            return PoolActivationResult(
                PoolActivationStatus.POST_CUTOVER_SYNC_PENDING,
                {
                    "reason": "active_repo_retirement_required",
                    "path": str(active_repo),
                },
            )
        try:
            retirement_evidence = retire_active_repo(
                migration_generation,
                preparation_id,
            )
        except ActiveRepoRetirementError as error:
            return PoolActivationResult(
                PoolActivationStatus.POST_CUTOVER_SYNC_PENDING,
                {
                    "reason": "active_repo_retirement_failed",
                    "retirement_reason": error.reason,
                    **error.evidence,
                },
            )
        except OSError as error:
            return PoolActivationResult(
                PoolActivationStatus.POST_CUTOVER_SYNC_PENDING,
                {
                    "reason": "active_repo_retirement_failed",
                    "retirement_reason": "active_repo_retirement_io_error",
                    "error_type": type(error).__name__,
                    "errno": error.errno,
                },
            )
        if active_repo.exists() or active_repo.is_symlink():
            return PoolActivationResult(
                PoolActivationStatus.POST_CUTOVER_SYNC_PENDING,
                {
                    "reason": "active_repo_retirement_unverified",
                    "path": str(active_repo),
                    "retirement": retirement_evidence,
                },
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
    repo_delivery = current_repo_delivery()
    if repo_delivery is RepoDelivery.DOWNLOAD and engine in {"aicoding", "hermes"}:
        _publish_structural_bridge(layout.repo_bridge, layout.pool_repo)
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
    if engine in {"aicoding", "hermes"}:
        try:
            stable_repo_bridge_valid = (
                layout.repo_bridge.is_symlink()
                and _lexical_target(layout.repo_bridge)
                == Path(os.path.abspath(layout.pool_repo))
            )
        except OSError:
            stable_repo_bridge_valid = False
        if not stable_repo_bridge_valid:
            return PoolActivationResult(
                PoolActivationStatus.POST_CUTOVER_SYNC_PENDING,
                {
                    "reason": "stable_repo_bridge_invalid",
                    "path": str(layout.repo_bridge),
                },
            )
    final_verification = verify_skill_mappings(
        mappings=mappings,
        home=layout.pool_root.parents[2],
        engine=engine,
    )
    if not final_verification.valid:
        return PoolActivationResult(
            PoolActivationStatus.POST_CUTOVER_SYNC_PENDING,
            {
                "reason": "post_retirement_mapping_verify_failed",
                "mapping": final_verification.evidence,
            },
        )
    final_inspection = inspect_runtime_layout(
        engine=engine,
        expected_contract_version=LAYOUT_CONTRACT_VERSION,
        home=layout.pool_root.parents[2],
        repo_is_mounted=repo_is_mounted,
    )
    if final_inspection.status is not RuntimeLayoutInspectionStatus.READY:
        return PoolActivationResult(
            PoolActivationStatus.POST_CUTOVER_SYNC_PENDING,
            {
                "reason": "post_retirement_layout_invalid",
                "probe_status": final_inspection.status.value,
                "probe": final_inspection.evidence,
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
        (layout.pool_center, layout.pool_center),
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


@dataclass(slots=True)
class _BestEffortSourceInspection:
    """Request-local, bounded source checks for ordinary runtime projection."""

    layout: _Layout
    source_layout: MappingSourceLayout
    results: dict[Path, str | None] = field(default_factory=dict)
    resolved_roots: dict[Path, Path | None] = field(default_factory=dict)
    durations_ms: dict[Path, float] = field(default_factory=dict)
    checks: int = 0
    cache_hits: int = 0

    def failure(self, source: Path) -> str | None:
        normalized = Path(os.path.abspath(source))
        if normalized in self.results:
            self.cache_hits += 1
            return self.results[normalized]
        started_at = time.perf_counter()
        result = self._inspect(normalized)
        self.checks += 1
        self.results[normalized] = result
        self.durations_ms[normalized] = (time.perf_counter() - started_at) * 1000
        return result

    def _inspect(self, source: Path) -> str | None:
        roots, outside_reason = self._roots()
        containing_root: Path | None = None
        for root in roots:
            normalized_root = Path(os.path.abspath(root))
            if source.is_relative_to(normalized_root):
                containing_root = normalized_root
                break
        if containing_root is None:
            return outside_reason
        try:
            source_stat = source.stat()
        except FileNotFoundError:
            return "source_missing"
        except OSError:
            return "source_unreadable"
        if not stat.S_ISDIR(source_stat.st_mode):
            return "source_not_directory"
        resolved_root = self._resolved_root(containing_root)
        if resolved_root is None:
            return "source_unreadable"
        try:
            if not source.resolve(strict=True).is_relative_to(resolved_root):
                return "source_escapes_pool"
        except OSError:
            return "source_unreadable"
        if not os.access(source, os.R_OK | os.X_OK):
            return "source_unreadable"
        return None

    def _resolved_root(self, root: Path) -> Path | None:
        if root not in self.resolved_roots:
            try:
                self.resolved_roots[root] = root.resolve(strict=True)
            except OSError:
                self.resolved_roots[root] = None
        return self.resolved_roots[root]

    def _roots(self) -> tuple[tuple[Path, ...], str]:
        if self.source_layout is MappingSourceLayout.LEGACY:
            roots = [
                self.layout.legacy_local,
                self.layout.legacy_repo,
                self.layout.pool_center,
            ]
            if self.layout.engine_type == "claude_code":
                roots.append(self.layout.repo_bridge)
            return tuple(roots), "source_outside_legacy"
        return (
            self.layout.pool_local,
            self.layout.pool_repo,
            self.layout.pool_center,
        ), "source_outside_pool"

    def evidence(self) -> dict[str, object]:
        slowest = sorted(
            self.durations_ms.items(), key=lambda item: item[1], reverse=True
        )[:_SLOW_SOURCE_LIMIT]
        return {
            "source_checks": self.checks,
            "source_check_cache_hits": self.cache_hits,
            "unique_sources": len(self.results),
            "slow_sources": [
                {"name": source.name, "duration_ms": round(duration_ms, 3)}
                for source, duration_ms in slowest
            ],
        }


def _source_failure(
    layout: _Layout,
    source: Path,
    *,
    source_layout: MappingSourceLayout,
) -> str | None:
    if source_layout is MappingSourceLayout.LEGACY:
        roots = [layout.legacy_local, layout.legacy_repo, layout.pool_center]
        if layout.engine_type == "claude_code":
            roots.append(layout.repo_bridge)
        return _managed_source_failure(
            source,
            roots=tuple(roots),
            outside_reason="source_outside_legacy",
        )
    return _managed_source_failure(
        source,
        roots=(layout.pool_local, layout.pool_repo, layout.pool_center),
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


def _trusted_active_repo_bridge_rewrite_retry(*, layout: _Layout) -> bool:
    """判定已激活 AICoding Bot 能否将旧 repo bridge 映射重发为 Pool 直链。

    早期 Pool-active 运行时会把已激活的 repo Skill 指向稳定的
    ``repo_bridge``。该 bridge 最终解析到 Pool repo，因而当时可用；新版
    probe 则要求 active root 中的受管入口词法上直接指向 Pool。这里仅为
    已提交布局的前滚收敛放行这一种旧格式，不能把 Legacy、dangling 或
    未知路径当作可信映射。
    """

    active_root = Path(os.path.abspath(layout.active_root))
    pool_local = Path(os.path.abspath(layout.pool_local))
    pool_repo = Path(os.path.abspath(layout.pool_repo))
    repo_bridge = Path(os.path.abspath(layout.repo_bridge))
    retired_roots = (
        Path(os.path.abspath(layout.legacy_local)),
        Path(os.path.abspath(layout.legacy_repo)),
        Path(os.path.abspath(layout.local_bridge)),
    )
    saw_bridge_mapping = False

    try:
        resolved_pool_repo = pool_repo.resolve(strict=True)
        for entry in active_root.iterdir():
            if not entry.is_symlink():
                # Relay/AIX own real directories; they are not Pool mappings.
                continue
            target = _lexical_target(entry)
            if target.is_relative_to(pool_local) or target.is_relative_to(pool_repo):
                try:
                    if not entry.is_dir():
                        return False
                except OSError:
                    return False
                continue
            if target.is_relative_to(repo_bridge):
                try:
                    resolved = target.resolve(strict=True)
                except OSError:
                    return False
                if (
                    not resolved.is_relative_to(resolved_pool_repo)
                    or not resolved.is_dir()
                ):
                    return False
                saw_bridge_mapping = True
                continue
            if any(target.is_relative_to(root) for root in retired_roots):
                return False
            # External symlinks predate Pool and must remain outside its scope.
    except OSError:
        return False
    return saw_bridge_mapping


def _mapping_plan(
    *,
    layout: _Layout,
    mappings: list[SkillMapping],
    source_layout: MappingSourceLayout = MappingSourceLayout.POOL,
    retired_targets: frozenset[Path] = frozenset(),
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
            or (
                layout.engine_type == "aicoding"
                and target == layout.active_root / "skills-repo"
            )
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
        if target in retired_targets:
            continue
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


def _mapping_target_invalid(
    layout: _Layout,
    target: Path,
    *,
    additional_retirement_roots: Sequence[Path] = (),
) -> bool:
    return (
        not target.is_absolute()
        or target.parent
        not in {layout.active_root, *additional_retirement_roots}
        or target
        in {
            layout.legacy_local,
            layout.legacy_repo,
            layout.local_bridge,
            layout.repo_bridge,
        }
        or (
            layout.engine_type == "aicoding"
            and target == layout.active_root / "skills-repo"
        )
    )


def _mapping_source_outside_layout(
    layout: _Layout,
    source: Path,
    *,
    source_layout: MappingSourceLayout,
) -> bool:
    roots = (
        (layout.legacy_local, layout.legacy_repo, layout.pool_center)
        if source_layout is MappingSourceLayout.LEGACY
        else (layout.pool_local, layout.pool_repo, layout.pool_center)
    )
    normalized = Path(os.path.abspath(source))
    return not any(
        normalized.is_relative_to(Path(os.path.abspath(root))) for root in roots
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
        if current != source:
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
                        code="TARGET_NOT_SYMLINK" if not target.is_symlink() else "TARGET_MISMATCH",
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

    # A previously published managed link is preserved by the full-snapshot
    # contract.  Report its missing source as pending so unrelated product
    # mutations do not hide existing runtime drift.
    retired_targets = {Path(item.target) for item in retired_mappings}
    for target, pool_source in discovered.items():
        if target in desired or target in retired_targets:
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


def _retirement_plan(
    *,
    layout: _Layout,
    mappings: list[SkillMapping],
    retired_mappings: list[SkillMapping],
    source_layout: MappingSourceLayout,
    additional_retirement_roots: Sequence[Path] = (),
) -> _MappingRetirementPlan:
    """Validate exact managed identities that may be removed.

    Retirement is deliberately narrower than full-set cleanup: an entry is
    removed only while it still points lexically to the exact old managed
    source. Filesystem-only managed entries and external symlinks remain
    outside Backend product-state authority.
    """

    desired = {
        Path(item.target): Path(os.path.abspath(Path(item.source)))
        for item in mappings
        if Path(item.target).is_absolute()
    }
    remove: list[Path] = []
    absent: list[Path] = []
    replaced: list[Path] = []
    external: list[Path] = []
    failures: list[dict[str, str]] = []
    seen: dict[Path, Path] = {}
    for mapping in retired_mappings:
        source_input = Path(mapping.source)
        source = Path(os.path.abspath(source_input))
        target = Path(mapping.target)
        reason = ""
        if not source_input.is_absolute() or _mapping_source_outside_layout(
            layout,
            source,
            source_layout=source_layout,
        ):
            reason = (
                "source_outside_legacy"
                if source_layout is MappingSourceLayout.LEGACY
                else "source_outside_pool"
            )
        elif _mapping_target_invalid(
            layout,
            target,
            additional_retirement_roots=additional_retirement_roots,
        ):
            reason = "target_invalid"
        elif target in seen and seen[target] != source:
            reason = "retired_target_ambiguous"
        if reason:
            failures.append(
                {"source": str(source), "target": str(target), "reason": reason}
            )
            continue
        seen[target] = source
        if desired.get(target) == source:
            replaced.append(target)
            continue
        if not target.exists() and not target.is_symlink():
            absent.append(target)
            continue
        if not target.is_symlink():
            failures.append(
                {
                    "source": str(source),
                    "target": str(target),
                    "reason": "retired_target_occupied",
                }
            )
            continue
        current = _lexical_target(target)
        if current == source:
            remove.append(target)
            continue
        if _canonical_pool_source(layout, current) is None:
            external.append(target)
            continue
        if desired.get(target) == current:
            replaced.append(target)
            continue
        failures.append(
            {
                "source": str(source),
                "target": str(target),
                "existing_source": str(current),
                "reason": "retired_target_identity_mismatch",
            }
        )
    return _MappingRetirementPlan(
        remove=tuple(remove),
        absent=tuple(absent),
        replaced=tuple(replaced),
        external=tuple(external),
        failures=tuple(failures),
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


def verify_skill_mappings(
    *,
    mappings: list[SkillMapping],
    retired_mappings: Sequence[SkillMapping] = (),
    home: str | Path = "/home/admin",
    engine: str = "openclaw",
    source_layout: MappingSourceLayout = MappingSourceLayout.POOL,
    additional_retirement_roots: Sequence[Path] = (),
    apply_mode: MappingApplyMode = MappingApplyMode.STRICT,
) -> MappingVerificationResult:
    """验证受管激活入口精确解析到声明 layout 的 source。"""

    layout = _Layout.for_engine(engine, Path(home))
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
    retired_mappings: Sequence[SkillMapping] = (),
    home: str | Path = "/home/admin",
    engine: str = "openclaw",
    source_layout: MappingSourceLayout = MappingSourceLayout.POOL,
    additional_retirement_roots: Sequence[Path] = (),
    apply_mode: MappingApplyMode = MappingApplyMode.STRICT,
) -> MappingPublishResult:
    """按声明 layout 对齐全部受管 mapping，并保留外部入口。"""

    layout = _Layout.for_engine(engine, Path(home))
    if apply_mode is MappingApplyMode.BEST_EFFORT:
        items, evidence = _best_effort_mapping_results(
            layout=layout,
            mappings=mappings,
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
            "retired_absent": [str(path) for path in retirement.absent],
            "retired_replaced": [str(path) for path in retirement.replaced],
            "retired_external_ignored": [str(path) for path in retirement.external],
        },
    )


__all__ = [
    "ActiveRepoRestorationError",
    "MappingPublishResult",
    "MappingApplyMode",
    "MappingItemResult",
    "MappingProjectionStatus",
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
