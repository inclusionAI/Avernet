"""Manage corpus bridges, cutover markers and retained Local storage."""

from __future__ import annotations

import json
import logging
import os
import re
import stat
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from engine.community.config import RepoDelivery, current_repo_delivery
from engine.community.core.skills.layout_planner import (
    ResolvedFilesystemLayoutPlan as _Layout,
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
)

logger = logging.getLogger(__name__)

from engine.community.plugins.skills_pool.layout_results import (
    ActiveRepoRetirementError,
    PoolActivationResult,
    PoolActivationStatus,
    SkillMapping,
    _invalid,
    _PostCutoverFinalization,
)
from engine.community.plugins.skills_pool.mapping_apply import (
    publish_pool_mappings,
    verify_skill_mappings,
)
from engine.community.plugins.skills_pool.mapping_inspection import (
    _lexical_target,
)


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


def _settle_hermes_repo_bridge(layout: _Layout) -> None:
    """Retire the obsolete platform bridge while preserving unowned objects."""

    path = layout.repo_bridge
    try:
        entry_stat = path.lstat()
    except FileNotFoundError:
        return
    except OSError as error:
        logger.warning(
            "Hermes Pool could not inspect obsolete repo bridge path=%s error=%s",
            path,
            type(error).__name__,
        )
        return
    if not stat.S_ISLNK(entry_stat.st_mode):
        logger.warning("Hermes Pool retained unowned Legacy repo object path=%s", path)
        return
    try:
        target = _lexical_target(path)
    except OSError as error:
        logger.warning(
            "Hermes Pool retained unreadable Legacy repo symlink path=%s error=%s",
            path,
            type(error).__name__,
        )
        return
    expected = Path(os.path.abspath(layout.pool_repo))
    if target != expected:
        logger.warning(
            "Hermes Pool retained unexpected Legacy repo symlink path=%s target=%s",
            path,
            target,
        )
        return
    try:
        path.unlink()
    except OSError as error:
        logger.warning(
            "Hermes Pool could not retire obsolete repo bridge path=%s error=%s",
            path,
            type(error).__name__,
        )
        return
    logger.info("Hermes Pool retired obsolete repo bridge path=%s", path)


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
    if repo_delivery is RepoDelivery.DOWNLOAD and engine == "aicoding":
        _publish_structural_bridge(layout.repo_bridge, layout.pool_repo)
    if engine == "hermes":
        _settle_hermes_repo_bridge(layout)
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
    if engine == "aicoding":
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
            publish_root=(
                pool_local.parent / f".post-sync-{quarantine.parent.name}"
            ),
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
            publish_root=(
                layout.pool_root / f".post-sync-{quarantine.parent.name}"
            ),
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
            publish_root=(
                layout.pool_root / f".post-sync-{quarantine.parent.name}"
            ),
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
