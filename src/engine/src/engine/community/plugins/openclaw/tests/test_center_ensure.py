from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from engine.community.plugins.openclaw._skills import (
    _SkillsEnsureError,
    _SkillsPortMixin,
)


@pytest.mark.asyncio
async def test_center_ensure_rejects_incomplete_version_before_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "nas"
    destination = tmp_path / "pool"
    version = source / "2e0f2a89-5f8e-4df2-bc3e-797f5f02d26a" / "2026.8.19"
    version.mkdir(parents=True)
    (version / "partial.txt").write_text("not a skill")

    port = _SkillsPortMixin()
    monkeypatch.setattr(
        port,
        "_skills_rsync_dir",
        lambda src, dst: shutil.copytree(src, dst, dirs_exist_ok=True),
    )

    with pytest.raises(_SkillsEnsureError, match="SKILL.md"):
        await port._skills_ensure_one(
            {
                "skill_uuid": "2e0f2a89-5f8e-4df2-bc3e-797f5f02d26a",
                "version": "2026.8.19",
            },
            source,
            destination,
        )

    assert not (destination / "2e0f2a89-5f8e-4df2-bc3e-797f5f02d26a").exists()


@pytest.mark.asyncio
async def test_center_ensure_rejects_untrusted_identity_before_path_resolution(
    tmp_path: Path,
) -> None:
    port = _SkillsPortMixin()

    with pytest.raises(_SkillsEnsureError, match="skill_uuid"):
        await port._skills_ensure_one(
            {"skill_uuid": "../escape", "version": "2026.8.19"},
            tmp_path / "nas",
            tmp_path / "pool",
        )

    assert not (tmp_path / "pool").exists()


@pytest.mark.asyncio
async def test_center_ensure_preserves_existing_incomplete_version(
    tmp_path: Path,
) -> None:
    skill_uuid = "2e0f2a89-5f8e-4df2-bc3e-797f5f02d26a"
    version = "2026.8.19"
    source_version = tmp_path / "nas" / skill_uuid / version
    source_version.mkdir(parents=True)
    (source_version / "SKILL.md").write_text("valid")
    destination_version = tmp_path / "pool" / skill_uuid / version
    destination_version.mkdir(parents=True)
    (destination_version / "partial.txt").write_text("preserve for repair")

    with pytest.raises(_SkillsEnsureError, match="existing center version incomplete"):
        await _SkillsPortMixin()._skills_ensure_one(
            {"skill_uuid": skill_uuid, "version": version},
            tmp_path / "nas",
            tmp_path / "pool",
        )

    assert (destination_version / "partial.txt").read_text() == "preserve for repair"
