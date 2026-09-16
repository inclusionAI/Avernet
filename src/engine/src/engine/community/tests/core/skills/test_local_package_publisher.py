from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest

from engine.community.core.skills.local_package import (
    LocalSkillPackageAction,
    LocalSkillPackageInvalidError,
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
