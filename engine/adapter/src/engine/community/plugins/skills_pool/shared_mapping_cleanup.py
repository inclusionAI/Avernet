"""Full-snapshot retirement of read-only corpus links, independent of layout."""

import os
from dataclasses import dataclass
from pathlib import Path

from engine.community.core.skills.layout_planner import ResolvedFilesystemLayoutPlan


def readonly_source(layout: ResolvedFilesystemLayoutPlan, source: Path) -> bool:
    normalized = Path(os.path.abspath(source))
    for candidate in (layout.legacy_repo, layout.repo_bridge, layout.pool_repo, layout.pool_center):
        root = Path(os.path.abspath(candidate))
        if normalized != root and normalized.is_relative_to(root):
            # A nested symlink escaping a corpus is an external entry. Missing
            # packages remain classifiable without requiring their bytes.
            return normalized.resolve(strict=False).is_relative_to(root.resolve(strict=False))
    return False


def _target(path: Path) -> Path:
    source = path.readlink()
    return Path(os.path.abspath(source if source.is_absolute() else path.parent / source))


@dataclass(frozen=True)
class SharedLinkRetirement:
    target: Path
    source: Path
    code: str | None = None
    retryable: bool = False


def retire_unselected_shared_links(
    layout: ResolvedFilesystemLayoutPlan,
    selected_targets: set[Path],
    *,
    apply: bool,
) -> list[SharedLinkRetirement]:
    """Inspect direct entries only; never follow or delete source packages.

    ``selected_targets`` is the *declared* full snapshot, including packages
    whose content preparation failed. Source availability cannot grant deletion.
    Local, external, structural and real-directory entries remain untouched.
    """
    reserved = {
        layout.local_bridge, layout.repo_bridge,
        layout.active_root / "skills-local", layout.active_root / "skills-repo",
        layout.active_root / "skill-center",
    }
    try:
        entries = sorted(layout.active_root.iterdir())
    except FileNotFoundError:
        if layout.active_root.is_symlink():
            raise
        return []
    results = []
    for entry in entries:
        if entry in selected_targets or entry in reserved or not entry.is_symlink():
            continue
        source = entry
        try:
            source = _target(entry)
            if not readonly_source(layout, source):
                continue
            if not apply:
                results.append(SharedLinkRetirement(entry, source, "UNSELECTED_SHARED_MAPPING_RETAINED"))
                continue
            # Recheck after classification; a user-owned replacement is not ours.
            if not entry.is_symlink() or _target(entry) != source:
                results.append(SharedLinkRetirement(entry, source, "RETIRED_TARGET_IDENTITY_MISMATCH"))
                continue
            entry.unlink()
            results.append(SharedLinkRetirement(entry, source))
        except FileNotFoundError:
            continue  # An overlapping explicit retirement is idempotent.
        except OSError:
            # Never turn an inaccessible entry into a successful convergence.
            results.append(SharedLinkRetirement(entry, source, "MAPPING_PUBLISH_IO_ERROR", True))
    return results
