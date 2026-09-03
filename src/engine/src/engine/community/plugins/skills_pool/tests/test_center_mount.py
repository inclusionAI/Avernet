from __future__ import annotations

from pathlib import Path

import pytest

from engine.community.plugins.skills_pool import center_mount as subject
from engine.community.plugins.skills_pool.center_mount import (
    CenterMountStatus,
    inspect_center_mount,
    inspect_center_version,
)


def test_missing_mount_requires_restart(tmp_path: Path) -> None:
    result = inspect_center_mount(tmp_path / "missing")

    assert result.status is CenterMountStatus.NOT_READY
    assert result.reason == "center_mount_missing"
    assert result.restart_required is True


@pytest.mark.parametrize("kind", ["file", "symlink"])
def test_non_directory_mountpoint_requires_restart(tmp_path: Path, kind: str) -> None:
    root = tmp_path / "skill-center"
    if kind == "file":
        root.write_text("not a directory")
    else:
        target = tmp_path / "target"
        target.mkdir()
        root.symlink_to(target, target_is_directory=True)

    result = inspect_center_mount(root, is_mounted=lambda _path: True)

    assert result.status is CenterMountStatus.NOT_READY
    assert result.reason == "center_mount_not_directory"


def test_plain_directory_is_not_treated_as_a_mount(tmp_path: Path) -> None:
    root = tmp_path / "skill-center"
    root.mkdir()

    result = inspect_center_mount(root, is_mounted=lambda _path: False)

    assert result.status is CenterMountStatus.NOT_READY
    assert result.reason == "center_mount_not_mounted"
    assert result.restart_required is True


def test_mount_checker_failure_is_transient(tmp_path: Path) -> None:
    root = tmp_path / "skill-center"
    root.mkdir()

    def fail(_path: Path) -> bool:
        raise OSError("mount table unavailable")

    result = inspect_center_mount(root, is_mounted=fail)

    assert result.status is CenterMountStatus.UNAVAILABLE
    assert result.reason == "center_mount_temporarily_unavailable"
    assert result.restart_required is False


@pytest.mark.parametrize(
    ("error", "reason"),
    [
        (PermissionError("denied"), "center_mount_unreadable"),
        (OSError("io"), "center_mount_temporarily_unavailable"),
    ],
)
def test_mountpoint_stat_failure_is_unavailable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    error: OSError,
    reason: str,
) -> None:
    root = tmp_path / "skill-center"
    original_lstat = Path.lstat

    def fail_lstat(path: Path):
        if path == root:
            raise error
        return original_lstat(path)

    monkeypatch.setattr(Path, "lstat", fail_lstat)
    result = inspect_center_mount(root)

    assert result.status is CenterMountStatus.UNAVAILABLE
    assert result.reason == reason


@pytest.mark.parametrize(
    ("error", "reason"),
    [
        (PermissionError("denied"), "center_mount_unreadable"),
        (OSError("io"), "center_mount_temporarily_unavailable"),
    ],
)
def test_mounted_directory_must_be_readable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    error: OSError,
    reason: str,
) -> None:
    root = tmp_path / "skill-center"
    root.mkdir()

    def fail_scandir(path: Path):
        assert path == root
        raise error

    monkeypatch.setattr(subject.os, "scandir", fail_scandir)
    result = inspect_center_mount(root, is_mounted=lambda _path: True)

    assert result.status is CenterMountStatus.UNAVAILABLE
    assert result.reason == reason


def test_readable_mounted_directory_is_ready(tmp_path: Path) -> None:
    root = tmp_path / "skill-center"
    root.mkdir()

    result = inspect_center_mount(root, is_mounted=lambda _path: True)

    assert result.status is CenterMountStatus.READY
    assert result.to_evidence() == {
        "status": "READY",
        "reason": None,
        "restart_required": False,
    }


@pytest.mark.parametrize(
    ("skill_uuid", "version"),
    [("../escape", "1"), ("u1", "../escape"), ("", "1")],
)
def test_exact_version_rejects_unsafe_components(
    tmp_path: Path,
    skill_uuid: str,
    version: str,
) -> None:
    result = inspect_center_version(
        tmp_path,
        skill_uuid=skill_uuid,
        version=version,
    )

    assert result.code == "CENTER_VERSION_INVALID"


def test_exact_version_requires_directory_and_regular_manifest(tmp_path: Path) -> None:
    root = tmp_path / "skill-center"
    version = root / "u1" / "1"
    version.mkdir(parents=True)
    target = root / "manifest-target"
    target.write_text("---\nname: demo\n---\n")
    (version / "SKILL.md").symlink_to(target)

    result = inspect_center_version(root, skill_uuid="u1", version="1")

    assert result.code == "CENTER_VERSION_INVALID"
    assert result.reason == "Skill Center 版本目录无效"


@pytest.mark.parametrize(
    ("error", "reason"),
    [
        (PermissionError("denied"), "Skill Center 目录当前不可读取，请稍后重试"),
        (OSError("io"), "Skill Center 目录当前不可用，请稍后重试"),
    ],
)
def test_exact_version_reports_stat_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    error: OSError,
    reason: str,
) -> None:
    root = tmp_path / "skill-center"
    version = root / "u1" / "1"
    version.mkdir(parents=True)
    (version / "SKILL.md").write_text("---\nname: demo\n---\n")
    original_lstat = Path.lstat

    def fail_lstat(path: Path):
        if path == version:
            raise error
        return original_lstat(path)

    monkeypatch.setattr(Path, "lstat", fail_lstat)
    result = inspect_center_version(root, skill_uuid="u1", version="1")

    assert result.code == "CENTER_MOUNT_UNAVAILABLE"
    assert result.reason == reason


@pytest.mark.parametrize(
    ("error", "reason"),
    [
        (PermissionError("denied"), "Skill Center 目录当前不可读取，请稍后重试"),
        (OSError("io"), "Skill Center 目录当前不可用，请稍后重试"),
    ],
)
def test_exact_version_reports_manifest_read_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    error: OSError,
    reason: str,
) -> None:
    root = tmp_path / "skill-center"
    version = root / "u1" / "1"
    version.mkdir(parents=True)
    manifest = version / "SKILL.md"
    manifest.write_text("---\nname: demo\n---\n")
    original_open = Path.open

    def fail_open(path: Path, *args, **kwargs):
        if path == manifest:
            raise error
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", fail_open)
    result = inspect_center_version(root, skill_uuid="u1", version="1")

    assert result.code == "CENTER_MOUNT_UNAVAILABLE"
    assert result.reason == reason


def test_exact_version_accepts_readable_manifest(tmp_path: Path) -> None:
    root = tmp_path / "skill-center"
    version = root / "u1" / "1"
    version.mkdir(parents=True)
    (version / "SKILL.md").write_text("---\nname: demo\n---\n")

    result = inspect_center_version(root, skill_uuid="u1", version="1")

    assert result.ready is True
    assert result.code is None
