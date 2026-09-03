"""Read-only inspection of the shared Skill Center corpus mount."""

from __future__ import annotations

import os
import stat
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path


class CenterMountStatus(StrEnum):
    READY = "READY"
    NOT_READY = "NOT_READY"
    UNAVAILABLE = "UNAVAILABLE"


@dataclass(frozen=True, slots=True)
class CenterMountInspection:
    status: CenterMountStatus
    reason: str | None
    restart_required: bool

    def to_evidence(self) -> dict[str, object]:
        return {
            "status": self.status.value,
            "reason": self.reason,
            "restart_required": self.restart_required,
        }


@dataclass(frozen=True, slots=True)
class CenterVersionInspection:
    ready: bool
    code: str | None = None
    reason: str | None = None


def inspect_center_mount(
    root: Path,
    *,
    is_mounted: Callable[[Path], bool] = os.path.ismount,
) -> CenterMountInspection:
    """Inspect the provisioned corpus without creating or repairing anything."""

    try:
        root_stat = root.lstat()
    except (FileNotFoundError, NotADirectoryError):
        return CenterMountInspection(
            CenterMountStatus.NOT_READY,
            "center_mount_missing",
            True,
        )
    except PermissionError:
        return CenterMountInspection(
            CenterMountStatus.UNAVAILABLE,
            "center_mount_unreadable",
            False,
        )
    except OSError:
        return CenterMountInspection(
            CenterMountStatus.UNAVAILABLE,
            "center_mount_temporarily_unavailable",
            False,
        )

    if stat.S_ISLNK(root_stat.st_mode) or not stat.S_ISDIR(root_stat.st_mode):
        return CenterMountInspection(
            CenterMountStatus.NOT_READY,
            "center_mount_not_directory",
            True,
        )
    try:
        mounted = is_mounted(root)
    except OSError:
        return CenterMountInspection(
            CenterMountStatus.UNAVAILABLE,
            "center_mount_temporarily_unavailable",
            False,
        )
    if not mounted:
        return CenterMountInspection(
            CenterMountStatus.NOT_READY,
            "center_mount_not_mounted",
            True,
        )
    try:
        with os.scandir(root):
            pass
    except PermissionError:
        return CenterMountInspection(
            CenterMountStatus.UNAVAILABLE,
            "center_mount_unreadable",
            False,
        )
    except OSError:
        return CenterMountInspection(
            CenterMountStatus.UNAVAILABLE,
            "center_mount_temporarily_unavailable",
            False,
        )
    return CenterMountInspection(CenterMountStatus.READY, None, False)


def _safe_component(value: str) -> bool:
    path = Path(value)
    return (
        bool(value)
        and not path.is_absolute()
        and path.name == value
        and value not in {".", ".."}
    )


def inspect_center_version(
    root: Path,
    *,
    skill_uuid: str,
    version: str,
) -> CenterVersionInspection:
    """Verify that one exact mounted version contains a readable ``SKILL.md``."""

    if not _safe_component(skill_uuid) or not _safe_component(version):
        return CenterVersionInspection(
            False,
            "CENTER_VERSION_INVALID",
            "Skill Center 版本标识无效",
        )

    version_root = root / skill_uuid / version
    manifest = version_root / "SKILL.md"
    try:
        version_stat = version_root.lstat()
        manifest_stat = manifest.lstat()
    except (FileNotFoundError, NotADirectoryError):
        return CenterVersionInspection(
            False,
            "CENTER_VERSION_NOT_FOUND",
            "Skill Center 中未找到指定版本",
        )
    except PermissionError:
        return CenterVersionInspection(
            False,
            "CENTER_MOUNT_UNAVAILABLE",
            "Skill Center 目录当前不可读取，请稍后重试",
        )
    except OSError:
        return CenterVersionInspection(
            False,
            "CENTER_MOUNT_UNAVAILABLE",
            "Skill Center 目录当前不可用，请稍后重试",
        )
    if (
        stat.S_ISLNK(version_stat.st_mode)
        or not stat.S_ISDIR(version_stat.st_mode)
        or stat.S_ISLNK(manifest_stat.st_mode)
        or not stat.S_ISREG(manifest_stat.st_mode)
    ):
        return CenterVersionInspection(
            False,
            "CENTER_VERSION_INVALID",
            "Skill Center 版本目录无效",
        )
    try:
        with manifest.open("rb") as stream:
            stream.read(1)
    except PermissionError:
        return CenterVersionInspection(
            False,
            "CENTER_MOUNT_UNAVAILABLE",
            "Skill Center 目录当前不可读取，请稍后重试",
        )
    except OSError:
        return CenterVersionInspection(
            False,
            "CENTER_MOUNT_UNAVAILABLE",
            "Skill Center 目录当前不可用，请稍后重试",
        )
    return CenterVersionInspection(True)


__all__ = [
    "CenterMountInspection",
    "CenterMountStatus",
    "CenterVersionInspection",
    "inspect_center_mount",
    "inspect_center_version",
]
