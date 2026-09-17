from __future__ import annotations

import io
import os
import threading
import zipfile
from pathlib import Path

import pytest

from engine.community.core.skills import local_package as local_package_module
from engine.community.core.skills.local_package import (
    LocalSkillPackageAction,
    LocalSkillPackageInvalidError,
    LocalSkillPackagePublisher,
    LocalSkillPackagePublishFailedError,
    LocalSkillPackagePublishInProgressError,
    LocalSkillPackageRollbackFailedError,
    LocalSkillPackageTooLargeError,
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


def test_publish_accepts_backend_canonical_legacy_manifest(tmp_path: Path) -> None:
    package = _package(
        {"SKILL.md": b"name: weather\ndescription: legacy compatibility\n"}
    )

    result = LocalSkillPackagePublisher().publish(
        skill_name="weather",
        package=package,
        target=tmp_path / "skills-local" / "weather",
    )

    assert result.action is LocalSkillPackageAction.CREATED


@pytest.mark.parametrize(
    "manifest",
    [
        b"---\r\nname: weather\r\ndescription: windows\r\n---\r\n",
        b"\xef\xbb\xbf---\nname: weather\ndescription: bom\n---\n",
        "---\nname: weather\ndescription: \u4e2d\u6587\u8bf4\u660e\n---\n".encode("gbk"),
    ],
    ids=["crlf", "utf8-bom", "gbk"],
)
def test_publish_accepts_legacy_backend_manifest_encodings(
    tmp_path: Path, manifest: bytes
) -> None:
    package = _package({"SKILL.md": manifest})

    result = LocalSkillPackagePublisher().publish(
        skill_name="weather",
        package=package,
        target=tmp_path / "skills-local" / "weather",
    )

    assert result.action is LocalSkillPackageAction.CREATED
    assert (tmp_path / "skills-local/weather/SKILL.md").read_bytes() == manifest


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


def test_replaying_same_package_remains_exact(tmp_path: Path) -> None:
    target = tmp_path / "skills-local" / "weather"
    package = _package(
        {"SKILL.md": b"---\nname: weather\ndescription: forecast\n---\n"}
    )
    publisher = LocalSkillPackagePublisher()

    first = publisher.publish(skill_name="weather", package=package, target=target)
    second = publisher.publish(skill_name="weather", package=package, target=target)

    assert first.content_digest == second.content_digest
    assert second.action is LocalSkillPackageAction.REPLACED
    assert (target / "SKILL.md").read_bytes().startswith(b"---")


@pytest.mark.parametrize(
    "package",
    [
        b"x" * (10 * 1024 * 1024 + 1),
        _package(
            {
                "SKILL.md": b"---\nname: weather\ndescription: forecast\n---\n",
                "large.bin": b"x" * (10 * 1024 * 1024 + 1),
            }
        ),
        _package(
            {
                "SKILL.md": b"---\nname: weather\ndescription: forecast\n---\n",
                **{f"part-{index}.bin": b"x" * (9 * 1024 * 1024) for index in range(6)},
            }
        ),
        _package(
            {
                "SKILL.md": b"---\nname: weather\ndescription: forecast\n---\n",
                **{f"empty-{index}": b"" for index in range(500)},
            }
        ),
    ],
    ids=["compressed", "single-file", "expanded", "file-count"],
)
def test_package_limits_reject_before_write(tmp_path: Path, package: bytes) -> None:
    target = tmp_path / "skills-local" / "weather"

    with pytest.raises(LocalSkillPackageTooLargeError):
        LocalSkillPackagePublisher().publish(
            skill_name="weather", package=package, target=target
        )

    assert not target.exists()


def test_path_length_limit_rejects_before_write(tmp_path: Path) -> None:
    package = _package(
        {
            "SKILL.md": b"---\nname: weather\ndescription: forecast\n---\n",
            f"scripts/{'x' * 250}": b"x",
        }
    )

    with pytest.raises(LocalSkillPackageInvalidError):
        LocalSkillPackagePublisher().publish(
            skill_name="weather",
            package=package,
            target=tmp_path / "skills-local" / "weather",
        )


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


def test_replace_and_rollback_failure_is_explicit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "skills-local" / "weather"
    target.mkdir(parents=True)
    (target / "SKILL.md").write_bytes(b"old")
    package = _package(
        {"SKILL.md": b"---\nname: weather\ndescription: new\n---\n"}
    )
    original_replace = os.replace

    def fail_publish_and_rollback(source, destination):
        source_path = Path(source)
        destination_path = Path(destination)
        if destination_path == target and (
            ".apply-" in source_path.name or ".rollback-" in source_path.name
        ):
            raise OSError("publish or rollback failed")
        return original_replace(source, destination)

    monkeypatch.setattr(os, "replace", fail_publish_and_rollback)

    with pytest.raises(LocalSkillPackageRollbackFailedError):
        LocalSkillPackagePublisher().publish(
            skill_name="weather", package=package, target=target
        )

    assert not target.exists()
    backups = list(target.parent.glob(".weather.rollback-*"))
    assert len(backups) == 1
    assert (backups[0] / "SKILL.md").read_bytes() == b"old"


def test_partial_backup_cleanup_failure_keeps_committed_new_package(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "skills-local" / "weather"
    target.mkdir(parents=True)
    (target / "SKILL.md").write_bytes(b"old")
    (target / "old.txt").write_bytes(b"old")
    package = _package(
        {
            "SKILL.md": b"---\nname: weather\ndescription: new\n---\n",
            "new.txt": b"new",
        }
    )
    original_remove_path = local_package_module._remove_path

    def partially_remove_backup(path: Path) -> None:
        if ".rollback-" in path.name:
            (path / "SKILL.md").unlink()
            raise OSError("simulated busy backup residue")
        original_remove_path(path)

    monkeypatch.setattr(local_package_module, "_remove_path", partially_remove_backup)

    result = LocalSkillPackagePublisher().publish(
        skill_name="weather", package=package, target=target
    )

    assert result.action is LocalSkillPackageAction.REPLACED
    assert (target / "SKILL.md").read_bytes().startswith(b"---")
    assert (target / "new.txt").read_bytes() == b"new"
    assert not (target / "old.txt").exists()
    assert len(list(target.parent.glob(".weather.rollback-*"))) == 1
