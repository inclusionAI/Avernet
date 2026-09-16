from __future__ import annotations

import io
import os
import threading
import zipfile
from pathlib import Path

import pytest

from engine.community.core.skills.local_package import (
    LocalSkillPackageAction,
    LocalSkillPackageInvalidError,
    LocalSkillPackagePublishFailedError,
    LocalSkillPackagePublishInProgressError,
    LocalSkillPackagePublisher,
)


def _package(files: dict[str, bytes]) -> bytes:
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        for path, content in files.items():
            archive.writestr(path, content)
    return stream.getvalue()


def test_publish_creates_complete_package(tmp_path: Path) -> None:
    package = _package(
        {
            "SKILL.md": b"---\nname: weather\ndescription: forecast\n---\n",
            "scripts/run.py": b"print('sunny')\n",
        }
    )

    result = LocalSkillPackagePublisher().publish(
        skill_name="weather",
        package=package,
        target=tmp_path / "skills-local" / "weather",
    )

    assert result.action is LocalSkillPackageAction.CREATED
    assert result.content_digest.startswith("sha256:")
    assert (tmp_path / "skills-local/weather/SKILL.md").read_bytes().startswith(b"---")
    assert (
        tmp_path / "skills-local/weather/scripts/run.py"
    ).read_bytes() == b"print('sunny')\n"


def test_publish_replaces_exactly_and_removes_stale_files(tmp_path: Path) -> None:
    target = tmp_path / "skills-local" / "weather"
    target.mkdir(parents=True)
    (target / "SKILL.md").write_bytes(b"---\nname: weather\ndescription: old\n---\n")
    (target / "stale.txt").write_bytes(b"stale")
    package = _package(
        {
            "SKILL.md": b"---\nname: weather\ndescription: new\n---\n",
            "fresh.txt": b"fresh",
        }
    )

    result = LocalSkillPackagePublisher().publish(
        skill_name="weather", package=package, target=target
    )

    assert result.action is LocalSkillPackageAction.REPLACED
    assert not (target / "stale.txt").exists()
    assert (target / "fresh.txt").read_bytes() == b"fresh"


def test_invalid_package_does_not_modify_existing_target(tmp_path: Path) -> None:
    target = tmp_path / "skills-local" / "weather"
    target.mkdir(parents=True)
    (target / "SKILL.md").write_bytes(b"old")
    package = _package(
        {
            "SKILL.md": b"---\nname: weather\ndescription: new\n---\n",
            "../escape.txt": b"escape",
        }
    )

    with pytest.raises(LocalSkillPackageInvalidError):
        LocalSkillPackagePublisher().publish(
            skill_name="weather", package=package, target=target
        )

    assert (target / "SKILL.md").read_bytes() == b"old"
    assert not (tmp_path / "escape.txt").exists()


def test_same_target_publish_lock_rejects_second_writer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "skills-local" / "weather"
    package = _package(
        {"SKILL.md": b"---\nname: weather\ndescription: forecast\n---\n"}
    )
    entered = threading.Event()
    release = threading.Event()
    original_replace = os.replace

    def blocking_replace(source, destination):
        if Path(destination) == target and ".apply-" in Path(source).name:
            entered.set()
            assert release.wait(timeout=5)
        return original_replace(source, destination)

    monkeypatch.setattr(os, "replace", blocking_replace)
    failure: list[BaseException] = []

    def first_publish() -> None:
        try:
            LocalSkillPackagePublisher().publish(
                skill_name="weather", package=package, target=target
            )
        except BaseException as exc:  # pragma: no cover - asserted below
            failure.append(exc)

    thread = threading.Thread(target=first_publish)
    thread.start()
    assert entered.wait(timeout=5)
    try:
        with pytest.raises(LocalSkillPackagePublishInProgressError):
            LocalSkillPackagePublisher().publish(
                skill_name="weather", package=package, target=target
            )
    finally:
        release.set()
        thread.join(timeout=5)

    assert not failure
    assert (target / "SKILL.md").is_file()


def test_replace_publish_failure_restores_old_package(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "skills-local" / "weather"
    target.mkdir(parents=True)
    (target / "SKILL.md").write_bytes(b"old")
    package = _package(
        {"SKILL.md": b"---\nname: weather\ndescription: new\n---\n"}
    )
    original_replace = os.replace
    failed_once = False

    def fail_publish(source, destination):
        nonlocal failed_once
        if (
            not failed_once
            and Path(destination) == target
            and ".apply-" in Path(source).name
        ):
            failed_once = True
            raise OSError("publish failed")
        return original_replace(source, destination)

    monkeypatch.setattr(os, "replace", fail_publish)

    with pytest.raises(LocalSkillPackagePublishFailedError):
        LocalSkillPackagePublisher().publish(
            skill_name="weather", package=package, target=target
        )

    assert (target / "SKILL.md").read_bytes() == b"old"
