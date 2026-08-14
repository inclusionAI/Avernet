"""Safe generation-scoped Migration Quarantine cleanup."""

from __future__ import annotations

import re
import shutil
import os
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from engine.community.plugins.skills_pool.layout_activation import _Layout

_GENERATION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


class QuarantineCleanupStatus(StrEnum):
    CLEANED = "CLEANED"
    ALREADY_ABSENT = "ALREADY_ABSENT"
    INVALID = "INVALID"
    TRANSIENT_ERROR = "TRANSIENT_ERROR"


@dataclass(frozen=True, slots=True)
class QuarantineCleanupResult:
    status: QuarantineCleanupStatus
    evidence: dict[str, object]

    def to_data(self) -> dict[str, object]:
        return {"status": self.status.value, "evidence": self.evidence}


def cleanup_quarantine(
    *,
    engine: str,
    home: Path,
    migration_generation: str,
) -> QuarantineCleanupResult:
    if not _GENERATION.fullmatch(migration_generation):
        return QuarantineCleanupResult(
            QuarantineCleanupStatus.INVALID,
            {"reason": "invalid_migration_generation"},
        )
    layout = _Layout.for_engine(engine, home)
    root = layout.pool_root / ".migration-quarantine"
    generation = root / migration_generation
    trusted_home = Path(os.path.abspath(home))
    try:
        relative_root = root.relative_to(trusted_home)
    except ValueError:
        return QuarantineCleanupResult(
            QuarantineCleanupStatus.INVALID,
            {"reason": "quarantine_root_outside_home"},
        )
    current = trusted_home
    unsafe_ancestor = current.is_symlink()
    for part in relative_root.parts:
        current = current / part
        unsafe_ancestor = unsafe_ancestor or current.is_symlink()
    if unsafe_ancestor:
        return QuarantineCleanupResult(
            QuarantineCleanupStatus.INVALID,
            {"reason": "quarantine_ancestor_is_symlink"},
        )
    if not generation.exists() and not generation.is_symlink():
        return QuarantineCleanupResult(
            QuarantineCleanupStatus.ALREADY_ABSENT,
            {"generation": migration_generation},
        )
    if generation.is_symlink() or not generation.is_dir():
        return QuarantineCleanupResult(
            QuarantineCleanupStatus.INVALID,
            {"reason": "generation_not_real_directory"},
        )
    try:
        if not generation.resolve(strict=True).is_relative_to(
            root.resolve(strict=True)
        ):
            return QuarantineCleanupResult(
                QuarantineCleanupStatus.INVALID,
                {"reason": "generation_escapes_quarantine_root"},
            )
    except OSError as error:
        return QuarantineCleanupResult(
            QuarantineCleanupStatus.TRANSIENT_ERROR,
            {"reason": "quarantine_resolve_failed", "error_type": type(error).__name__},
        )
    try:
        shutil.rmtree(generation)
    except OSError as error:
        return QuarantineCleanupResult(
            QuarantineCleanupStatus.TRANSIENT_ERROR,
            {"reason": "remove_failed", "error_type": type(error).__name__},
        )
    return QuarantineCleanupResult(
        QuarantineCleanupStatus.CLEANED,
        {"generation": migration_generation, "path_absent": True},
    )
