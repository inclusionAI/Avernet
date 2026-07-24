"""Describe a service draft's frozen Skills layout within its publish artifact.

This module deliberately owns only the service-publish contract.  It does not
resolve the rollout whitelist and it is not a general engine-layout descriptor:
the editable draft's persisted active layout is the sole input, while the
versioned build directory remains the physical content snapshot.
"""

from __future__ import annotations

import hashlib
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agentclaw.community.core.skills_pool.models import pool_paths_for_engine
from agentclaw.community.core.skills_pool.repository.protocol import (
    SkillsPoolLayoutRepositoryProtocol,
)
from agentclaw.community.core.skills_pool.types import (
    BotSkillLayoutScope,
    SkillLayout,
    SkillLayoutPhase,
)
from agentclaw.community.utils.env_utils import get_current_env


@dataclass(frozen=True, slots=True)
class _ServiceLayoutPaths:
    active_relative: str
    legacy_local_relative: str
    pool_local_relative: str
    legacy_repo_target: str
    snapshot_repo_relatives: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CapturedServiceSkillsLayout:
    """The draft layout decision captured before physical snapshotting starts."""

    engine: str
    scope: BotSkillLayoutScope
    active_layout: SkillLayout
    layout_contract_version: str | None


_SERVICE_LAYOUTS = {
    "openclaw": _ServiceLayoutPaths(
        active_relative="workspace/skills",
        legacy_local_relative="workspace/skills/skills-local",
        pool_local_relative="workspace/skills-pool/skills-local",
        legacy_repo_target="/home/admin/.openclaw/workspace/skills/skills-repo",
        snapshot_repo_relatives=(
            "workspace/skills/skills-repo",
            "workspace/skills-pool/skills-repo",
        ),
    ),
    "claude_code": _ServiceLayoutPaths(
        active_relative="claude/skills",
        legacy_local_relative="workspace/skills/skills-local",
        pool_local_relative="workspace/skills-pool/skills-local",
        legacy_repo_target="/home/admin/.claude_code/skills-repo",
        snapshot_repo_relatives=(
            "skills-repo",
            "workspace/skills/skills-repo",
            "workspace/skills-pool/skills-repo",
            "claude/skills/skills-repo",
        ),
    ),
}

_RESERVED_ACTIVE_ENTRIES = frozenset(
    {"skills-local", "skills-repo", "skills-center"}
)
SERVICE_SKILLS_POOL_CONTRACT_VERSION = "skills-pool-p3-v1"


class ServiceSkillsManifestError(RuntimeError):
    """The draft cannot be represented as a supported Skills manifest."""


class ServiceSkillsManifestBuilder:
    """Build the Skills manifest embedded in one versioned service artifact."""

    def __init__(
        self,
        layout_repository: SkillsPoolLayoutRepositoryProtocol,
    ) -> None:
        self._layout_repository = layout_repository

    def capture(
        self,
        *,
        bot: dict[str, Any],
    ) -> CapturedServiceSkillsLayout | None:
        engine = str(bot.get("active_engine") or "openclaw").strip().lower()
        scope = BotSkillLayoutScope(
            env=str(bot.get("env") or get_current_env()),
            entity_id=str(bot.get("entity_id") or bot.get("owner_id") or ""),
            bot_id=str(bot.get("bot_id") or ""),
        )
        state = self._layout_repository.get(scope)

        if engine == "aicoding":
            raise ServiceSkillsManifestError(
                "AICoding service publishing is not supported"
            )
        if engine == "hermes":
            if state.active_layout is SkillLayout.POOL:
                raise ServiceSkillsManifestError(
                    "Hermes Pool service manifest is disabled until native "
                    "service delivery is verified"
                )
            # Preserve the pre-Pool Legacy service path.  It has no verified
            # native Hermes manifest contract, so do not falsely stamp one.
            return None

        paths = _SERVICE_LAYOUTS.get(engine)
        if paths is None:
            raise ServiceSkillsManifestError(
                f"service Skills manifest is not supported for engine: {engine}"
            )

        is_pool = state.active_layout is SkillLayout.POOL
        if is_pool and (
            not state.persisted
            or state.phase is not SkillLayoutPhase.POOL_ACTIVE
            or not state.layout_contract_version
        ):
            raise ServiceSkillsManifestError(
                "Pool service manifest requires a persisted POOL_ACTIVE draft"
            )

        return CapturedServiceSkillsLayout(
            engine=engine,
            scope=scope,
            active_layout=state.active_layout,
            layout_contract_version=(
                state.layout_contract_version if is_pool else None
            ),
        )

    def finalize(
        self,
        *,
        captured: CapturedServiceSkillsLayout,
        build_target_path: str,
    ) -> dict[str, Any]:
        engine = captured.engine
        current = self._layout_repository.get(captured.scope)
        current_contract = (
            current.layout_contract_version
            if current.active_layout is SkillLayout.POOL
            else None
        )
        if (
            current.active_layout is not captured.active_layout
            or current_contract != captured.layout_contract_version
        ):
            raise ServiceSkillsManifestError(
                "draft Skills layout changed during service build"
            )
        paths = _SERVICE_LAYOUTS[engine]
        is_pool = captured.active_layout is SkillLayout.POOL
        local_relative = (
            paths.pool_local_relative
            if is_pool
            else paths.legacy_local_relative
        )
        engine_paths = pool_paths_for_engine(engine)
        local_target = (
            engine_paths.pool_local if is_pool else engine_paths.legacy_local
        )
        repo_target = (
            engine_paths.pool_repo if is_pool else paths.legacy_repo_target
        )
        target = Path(build_target_path)
        _prune_shared_repo_content(target, paths.snapshot_repo_relatives)
        local_snapshot = target / local_relative
        active_root = target / paths.active_relative
        _require_snapshot_directory(local_snapshot, label="local Skills")
        _require_snapshot_directory(active_root, label="active Skills")
        digest, file_count = _digest_tree(local_snapshot)

        return {
            "schema_version": 1,
            "engine": engine,
            "active_layout": captured.active_layout.value,
            "layout_contract_version": captured.layout_contract_version,
            "local_snapshot": {
                "relative_path": local_relative,
                "file_count": file_count,
                "sha256": digest,
            },
            "managed_entries": _managed_entries(
                active_root,
                managed_roots=(local_target, repo_target),
            ),
            "repo": {
                "delivery": "runtime_mount",
                "included_in_local_snapshot": False,
                "target": repo_target,
            },
        }


def validate_service_skills_manifest_for_release(
    manifest: dict[str, Any],
    bot: dict[str, Any],
) -> None:
    """Fail closed when a live draft identity no longer matches its manifest."""

    if manifest.get("schema_version") != 1:
        raise ServiceSkillsManifestError(
            "unsupported service Skills manifest schema"
        )
    manifest_engine = str(manifest.get("engine") or "").strip().lower()
    live_engine = str(bot.get("active_engine") or "openclaw").strip().lower()
    if manifest_engine != live_engine:
        raise ServiceSkillsManifestError(
            "live Bot engine no longer matches the frozen service Skills manifest"
        )
    active_layout = manifest.get("active_layout")
    if active_layout not in {SkillLayout.LEGACY.value, SkillLayout.POOL.value}:
        raise ServiceSkillsManifestError(
            "invalid active layout in service Skills manifest"
        )
    if (
        active_layout == SkillLayout.POOL.value
        and manifest.get("layout_contract_version")
        != SERVICE_SKILLS_POOL_CONTRACT_VERSION
    ):
        raise ServiceSkillsManifestError(
            "Pool service Skills manifest uses an unsupported layout contract"
        )


def service_skills_manifest_env(
    manifest: dict[str, Any],
    bot: dict[str, Any],
) -> dict[str, str]:
    """Translate the frozen manifest into the backwards-compatible wire contract."""

    validate_service_skills_manifest_for_release(manifest, bot)
    env = {
        "AGENTCLAW_SKILLS_LAYOUT": str(manifest["active_layout"]),
    }
    contract = manifest.get("layout_contract_version")
    if contract:
        env["AGENTCLAW_SKILLS_LAYOUT_CONTRACT_VERSION"] = str(contract)
    return env


def service_skills_env_from_ext(
    ext: dict[str, Any] | None,
    bot: dict[str, Any],
) -> dict[str, str] | None:
    """Return the immutable runtime layout declaration from a publish ext."""

    manifest = (ext or {}).get("skills_manifest")
    if manifest is None:
        return None
    return service_skills_manifest_env(manifest, bot)


def _require_snapshot_directory(path: Path, *, label: str) -> None:
    if path.is_symlink() or not path.is_dir():
        raise ServiceSkillsManifestError(
            f"{label} snapshot directory is missing or invalid: {path}"
        )


def _managed_entries(
    active_root: Path,
    *,
    managed_roots: tuple[str, str],
) -> list[dict[str, str]]:
    if not active_root.is_dir():
        return []

    entries: list[dict[str, str]] = []
    for child in sorted(active_root.iterdir(), key=lambda path: path.name):
        if child.name in _RESERVED_ACTIVE_ENTRIES or not child.is_symlink():
            continue
        link_target = os.readlink(child)
        normalized_relative = link_target.removeprefix("./")
        is_relative_managed = (
            not link_target.startswith("/")
            and normalized_relative.split("/", 1)[0]
            in {"skills-local", "skills-repo"}
        )
        is_absolute_managed = any(
            link_target == root or link_target.startswith(f"{root}/")
            for root in managed_roots
        )
        if not is_relative_managed and not is_absolute_managed:
            continue
        entries.append({"name": child.name, "target": link_target})
    return entries


def _prune_shared_repo_content(
    target: Path,
    relative_paths: tuple[str, ...],
) -> None:
    """Remove stale copied repo directories while preserving bridge symlinks."""

    for relative_path in relative_paths:
        candidate = target / relative_path
        if candidate.is_symlink() or not candidate.exists():
            continue
        if candidate.is_dir():
            shutil.rmtree(candidate)
        else:
            candidate.unlink()


def _digest_tree(root: Path) -> tuple[str, int]:
    """Hash names, types, link targets and file bytes without following links."""

    digest = hashlib.sha256()
    file_count = 0
    if not root.exists():
        digest.update(b"missing\0")
        return digest.hexdigest(), 0

    def visit(directory: Path) -> None:
        nonlocal file_count
        for child in sorted(directory.iterdir(), key=lambda path: path.name):
            relative = child.relative_to(root).as_posix().encode()
            if child.is_symlink():
                digest.update(b"L\0" + relative + b"\0")
                digest.update(os.readlink(child).encode() + b"\0")
            elif child.is_dir():
                digest.update(b"D\0" + relative + b"\0")
                visit(child)
            elif child.is_file():
                file_count += 1
                digest.update(b"F\0" + relative + b"\0")
                with child.open("rb") as source:
                    while chunk := source.read(1024 * 1024):
                        digest.update(chunk)
                digest.update(b"\0")

    visit(root)
    return digest.hexdigest(), file_count


__all__ = [
    "CapturedServiceSkillsLayout",
    "SERVICE_SKILLS_POOL_CONTRACT_VERSION",
    "ServiceSkillsManifestBuilder",
    "ServiceSkillsManifestError",
    "service_skills_manifest_env",
    "service_skills_env_from_ext",
    "validate_service_skills_manifest_for_release",
]
