"""Desktop provider preparation for the public Skills Pool layout."""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from uuid import uuid4

from engine.community.config import RepoDelivery
from engine.community.core.skills.layout_planner import (
    LAYOUT_CONTRACT_VERSION,
    LayoutIdentity,
    RuntimeLayoutContext,
    SkillLayoutResolutionError,
    resolve_filesystem_skill_layout,
)
from engine.community.plugins.skills_pool.layout_probe import (
    RuntimeLayoutInspectionStatus,
    inspect_runtime_layout,
)
from engine.community.plugins.skills_pool.layout_sync import mirror_local_tree


class DesktopPreparationStatus(StrEnum):
    PREPARED = "PREPARED"
    ALREADY_PREPARED = "ALREADY_PREPARED"
    ACTIVE_LAYOUT = "ACTIVE_LAYOUT"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    FAILED = "FAILED"


@dataclass(frozen=True, slots=True)
class DesktopPreparationResult:
    status: DesktopPreparationStatus
    preparation_id: str | None = None
    reason: str | None = None


def _lexical_target(path: Path) -> Path:
    target = path.readlink()
    if not target.is_absolute():
        target = path.parent / target
    return Path(os.path.abspath(target))


def _publish_repo_delivery_bridge(path: Path, source: Path) -> None:
    source = Path(os.path.abspath(source))
    if path.is_symlink() and _lexical_target(path) == source:
        return
    if path.exists() and not path.is_symlink():
        raise OSError(f"Pool repo entry is not a symlink: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.prepare")
    try:
        temporary.symlink_to(source, target_is_directory=True)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _pre_cutover_structural_bridges(
    *,
    engine: str,
    repo_source: Path,
    local_bridge: Path,
    legacy_local: Path,
    repo_bridge: Path,
    legacy_repo: Path,
) -> list[dict[str, object]]:
    bridges: list[dict[str, object]] = []
    if engine != "openclaw":
        bridges.append(
            {
                "name": "stable_local_bridge",
                "path": str(local_bridge),
                "target": str(legacy_local),
                "valid": (
                    local_bridge.is_symlink()
                    and _lexical_target(local_bridge)
                    == Path(os.path.abspath(legacy_local))
                ),
            }
        )
        bridges.append(
            {
                "name": "legacy_repo_delivery",
                "path": str(legacy_repo),
                "target": str(repo_source),
                "valid": (
                    legacy_repo.is_symlink()
                    and _lexical_target(legacy_repo)
                    == Path(os.path.abspath(repo_source))
                ),
            }
        )
    if engine == "claude_code":
        bridges.append(
            {
                "name": "legacy_repo_bridge",
                "path": str(repo_bridge),
                "target": str(legacy_repo),
                "valid": (
                    repo_bridge.is_symlink()
                    and _lexical_target(repo_bridge)
                    == Path(os.path.abspath(legacy_repo))
                ),
            }
        )
    return bridges


def _external_active_entry_count(
    *,
    active_root: Path,
    reserved: set[Path],
    managed_roots: tuple[Path, ...],
) -> int:
    if not active_root.is_dir():
        return 0
    normalized_roots = tuple(Path(os.path.abspath(root)) for root in managed_roots)
    count = 0
    for entry in active_root.iterdir():
        if entry in reserved:
            continue
        if not entry.is_symlink():
            count += 1
            continue
        target = _lexical_target(entry)
        if not any(target.is_relative_to(root) for root in normalized_roots):
            count += 1
    return count


def _write_marker(path: Path, marker: dict[str, object]) -> None:
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    payload = json.dumps(
        marker,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    try:
        with temporary.open("wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        temporary.unlink(missing_ok=True)


def prepare_desktop_pool(
    *,
    engine: str,
    repo_source: Path,
    home: Path = Path("/home/admin"),
) -> DesktopPreparationResult:
    """Prepare a Desktop runtime without changing its active Legacy layout."""

    try:
        layout = resolve_filesystem_skill_layout(
            LayoutIdentity(
                engine_type=engine,
                layout_contract_version=LAYOUT_CONTRACT_VERSION,
            ),
            RuntimeLayoutContext(home=home),
        )
    except SkillLayoutResolutionError:
        return DesktopPreparationResult(
            DesktopPreparationStatus.NOT_APPLICABLE,
            reason="filesystem_layout_not_supported",
        )

    repo_source = Path(os.path.abspath(repo_source))
    try:
        if not repo_source.is_dir():
            return DesktopPreparationResult(
                DesktopPreparationStatus.FAILED,
                reason="repo_source_not_directory",
            )
        with os.scandir(repo_source):
            pass
    except OSError:
        return DesktopPreparationResult(
            DesktopPreparationStatus.FAILED,
            reason="repo_source_unreadable",
        )

    existing = inspect_runtime_layout(
        engine=engine,
        expected_contract_version=LAYOUT_CONTRACT_VERSION,
        home=home,
        repo_delivery=RepoDelivery.DOWNLOAD,
    )
    if (
        existing.status is RuntimeLayoutInspectionStatus.READY
        and existing.preparation_id is not None
    ):
        return DesktopPreparationResult(
            DesktopPreparationStatus.ALREADY_PREPARED,
            preparation_id=existing.preparation_id,
        )
    if layout.active_marker.exists() or layout.active_marker.is_symlink():
        return DesktopPreparationResult(
            DesktopPreparationStatus.ACTIVE_LAYOUT,
            reason="pool_layout_already_active",
        )

    staging = layout.pool_root / f".preparation-staging-{uuid4().hex}"
    try:
        layout.pool_root.mkdir(parents=True, exist_ok=True)
        if layout.pool_local.is_symlink() or (
            layout.pool_local.exists() and not layout.pool_local.is_dir()
        ):
            raise OSError(f"Pool local is not a directory: {layout.pool_local}")
        layout.pool_local.mkdir(exist_ok=True)
        if layout.legacy_local.is_symlink():
            raise OSError(
                f"Legacy local is not a directory: {layout.legacy_local}"
            )
        layout.legacy_local.mkdir(parents=True, exist_ok=True)
        if not layout.legacy_local.is_dir():
            raise OSError(
                f"Legacy local is not a directory: {layout.legacy_local}"
            )

        mirror_local_tree(
            source_root=layout.legacy_local,
            pool_local=layout.pool_local,
            staging_root=staging,
            remove_missing=False,
        )
        _publish_repo_delivery_bridge(layout.pool_repo, repo_source)

        structural_bridges = _pre_cutover_structural_bridges(
            engine=engine,
            repo_source=repo_source,
            local_bridge=layout.local_bridge,
            legacy_local=layout.legacy_local,
            repo_bridge=layout.repo_bridge,
            legacy_repo=layout.legacy_repo,
        )
        if any(bridge["valid"] is not True for bridge in structural_bridges):
            raise OSError(
                "pre-cutover structural bridge is invalid"
            )

        preparation_id = str(uuid4())
        prepared_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        marker: dict[str, object] = {
            "engine": engine,
            "layout_contract_version": LAYOUT_CONTRACT_VERSION,
            "preparation_id": preparation_id,
            "prepared_at": prepared_at,
            "pool_local_root": str(layout.pool_local),
            "pool_repo_root": str(layout.pool_repo),
            "repo_delivery": RepoDelivery.DOWNLOAD.value,
            "repo_delivery_source": str(repo_source),
            "validation_summary": {
                "all_valid": True,
                "pool_local": {
                    "path": str(layout.pool_local),
                    "valid": True,
                },
                "pool_repo": {
                    "path": str(layout.pool_repo),
                    "source": str(repo_source),
                    "readable_delivery": True,
                    "valid": True,
                },
                "repo_delivery_bridge": {
                    "path": str(layout.pool_repo),
                    "target": str(repo_source),
                    "valid": True,
                },
                "structural_bridges": structural_bridges,
                "managed_active_entries": [],
                "external_active_entry_count": _external_active_entry_count(
                    active_root=layout.active_root,
                    reserved={layout.local_bridge, layout.repo_bridge},
                    managed_roots=(
                        layout.legacy_local,
                        layout.legacy_repo,
                        layout.pool_local,
                        layout.pool_repo,
                    ),
                ),
                **(
                    {"legacy_bridge_verified": True}
                    if engine == "hermes"
                    else {}
                ),
            },
        }
        _write_marker(layout.ready_marker, marker)
        verified = inspect_runtime_layout(
            engine=engine,
            expected_contract_version=LAYOUT_CONTRACT_VERSION,
            home=home,
            repo_delivery=RepoDelivery.DOWNLOAD,
        )
        if verified.status is RuntimeLayoutInspectionStatus.READY:
            if verified.preparation_id == preparation_id:
                return DesktopPreparationResult(
                    DesktopPreparationStatus.PREPARED,
                    preparation_id=preparation_id,
                )
            return DesktopPreparationResult(
                DesktopPreparationStatus.ALREADY_PREPARED,
                preparation_id=verified.preparation_id,
            )
        if verified.preparation_id == preparation_id:
            layout.ready_marker.unlink(missing_ok=True)
        return DesktopPreparationResult(
            DesktopPreparationStatus.FAILED,
            reason="prepared_layout_failed_probe",
        )
    except OSError as error:
        return DesktopPreparationResult(
            DesktopPreparationStatus.FAILED,
            reason=type(error).__name__,
        )
    finally:
        if staging.is_dir() and not staging.is_symlink():
            shutil.rmtree(staging)


__all__ = [
    "DesktopPreparationResult",
    "DesktopPreparationStatus",
    "prepare_desktop_pool",
]
