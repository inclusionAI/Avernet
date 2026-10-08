"""Validate Engine-owned active marker mappings and managed Runtime links."""

from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import Protocol


class ActiveMarkerLayout(Protocol):
    active_root: Path
    pool_local: Path
    pool_repo: Path
    pool_center: Path
    legacy_local: Path
    legacy_repo: Path
    local_bridge: Path
    repo_bridge: Path


def _lexical_symlink_target(path: Path) -> Path:
    target = path.readlink()
    if not target.is_absolute():
        target = path.parent / target
    return Path(os.path.abspath(target))


def active_marker_valid(
    marker: object,
    *,
    layout: ActiveMarkerLayout,
    engine: str,
    expected_contract_version: str,
    preparation_id: str,
) -> bool:
    """Validate the immutable identity and finalizing mapping envelope."""

    if not isinstance(marker, dict):
        return False
    if (
        marker.get("engine") != engine
        or marker.get("layout_contract_version") != expected_contract_version
        or marker.get("preparation_id") != preparation_id
        or marker.get("activation_state") not in {"finalizing", "active"}
        or not isinstance(marker.get("migration_generation"), str)
    ):
        return False
    if marker["activation_state"] == "active":
        return True
    mappings = marker.get("mappings")
    if not isinstance(mappings, list):
        return False
    pool_roots = tuple(
        Path(os.path.abspath(root))
        for root in (layout.pool_local, layout.pool_repo, layout.pool_center)
    )
    seen_targets: set[Path] = set()
    for mapping in mappings:
        if not isinstance(mapping, dict):
            return False
        source_value = mapping.get("source")
        target_value = mapping.get("target")
        if not isinstance(source_value, str) or not isinstance(target_value, str):
            return False
        source = Path(os.path.abspath(source_value))
        target = Path(os.path.abspath(target_value))
        if not any(source.is_relative_to(root) for root in pool_roots):
            return False
        if (
            target.parent != Path(os.path.abspath(layout.active_root))
            or target in {layout.local_bridge, layout.repo_bridge}
            or target in seen_targets
        ):
            return False
        seen_targets.add(target)
    return True


def active_entries_failure_reason(
    layout: ActiveMarkerLayout,
    *,
    engine: str,
) -> str | None:
    """Validate mutable managed entries without freezing an old mapping set."""

    pool_roots = tuple(
        Path(os.path.abspath(root))
        for root in (layout.pool_local, layout.pool_repo, layout.pool_center)
    )
    retired_roots = tuple(
        Path(os.path.abspath(root))
        for root in (
            layout.legacy_local,
            layout.legacy_repo,
            layout.local_bridge,
            layout.repo_bridge,
        )
    )
    retired_active_corpus_roots = (
        (
            Path(os.path.abspath(layout.active_root / "skills-local")),
            Path(os.path.abspath(layout.active_root / "skills-repo")),
        )
        if engine == "aicoding"
        else ()
    )
    for entry in layout.active_root.iterdir():
        if not entry.is_symlink():
            continue
        target = _lexical_symlink_target(entry)
        if any(target.is_relative_to(root) for root in pool_roots):
            try:
                target_stat = entry.stat()
            except (FileNotFoundError, NotADirectoryError):
                return "active_managed_entry_invalid"
            if not stat.S_ISDIR(target_stat.st_mode):
                return "active_managed_entry_invalid"
            continue
        if any(target.is_relative_to(root) for root in retired_active_corpus_roots):
            return "retired_active_corpus_reference_present"
        if any(target.is_relative_to(root) for root in retired_roots):
            return "active_managed_entry_invalid"
        # External active entries predate Pool and remain outside this migration.
    return None
