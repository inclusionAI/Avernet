"""Inspect and validate physical mappings without mutating user assets."""

from __future__ import annotations

import os
import stat
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from engine.community.core.skills.layout_planner import (
    ResolvedFilesystemLayoutPlan as _Layout,
)
from engine.community.plugins.skills_pool.shared_mapping_cleanup import readonly_source

_SLOW_SOURCE_LIMIT = 3

from engine.community.plugins.skills_pool.layout_results import (
    MappingSourceLayout,
    SkillMapping,
    _MappingPlan,
    _MappingRetirementPlan,
)


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
        (layout.pool_center, layout.pool_center),
    ):
        normalized_root = Path(os.path.abspath(root))
        if source.is_relative_to(normalized_root):
            return pool_root / source.relative_to(normalized_root)
    return None


def _same_center_skill_identity(layout: _Layout, first: Path, second: Path) -> bool:
    """Match exact-version paths only by their shared managed Center identity."""

    root = Path(os.path.abspath(layout.pool_center))
    try:
        first_parts = Path(os.path.abspath(first)).relative_to(root).parts
        second_parts = Path(os.path.abspath(second)).relative_to(root).parts
    except ValueError:
        return False
    return (
        len(first_parts) == 2
        and len(second_parts) == 2
        and first_parts[0] == second_parts[0]
    )


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
    try:
        entries = sorted(layout.active_root.iterdir(), key=lambda path: path.name)
    except FileNotFoundError:
        if layout.active_root.is_symlink():
            raise
        return managed, tuple(external), tuple(occupied)
    for entry in entries:
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
        if target in retired_targets or (
            target not in desired and readonly_source(layout, pool_source)
        ):
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
        or target.parent not in {layout.active_root, *additional_retirement_roots}
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
    source. Local links require this explicit identity; unselected Repo/Center
    links are retired separately by complete-snapshot convergence.
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
