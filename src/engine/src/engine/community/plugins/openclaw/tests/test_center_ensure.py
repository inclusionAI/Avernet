from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from engine.community.plugins.openclaw._skills import (
    _SkillsEnsureError,
    _SkillsPortMixin,
)

_SKILL_UUID = "2e0f2a89-5f8e-4df2-bc3e-797f5f02d26a"
_VERSION = "2026.8.19"


@pytest.mark.asyncio
async def test_center_ensure_rejects_incomplete_version_before_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "nas"
    destination = tmp_path / "pool"
    version = source / _SKILL_UUID / _VERSION
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
                "skill_uuid": _SKILL_UUID,
                "version": _VERSION,
            },
            source,
            destination,
        )

    assert not (destination / _SKILL_UUID).exists()


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
    source_version = tmp_path / "nas" / _SKILL_UUID / _VERSION
    source_version.mkdir(parents=True)
    (source_version / "SKILL.md").write_text("valid")
    destination_version = tmp_path / "pool" / _SKILL_UUID / _VERSION
    destination_version.mkdir(parents=True)
    (destination_version / "partial.txt").write_text("preserve for repair")

    with pytest.raises(_SkillsEnsureError, match="existing center version incomplete"):
        await _SkillsPortMixin()._skills_ensure_one(
            {"skill_uuid": _SKILL_UUID, "version": _VERSION},
            tmp_path / "nas",
            tmp_path / "pool",
        )

    assert (destination_version / "partial.txt").read_text() == "preserve for repair"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("item", "message"),
    [
        ({"skill_uuid": None, "version": _VERSION}, "skill_uuid"),
        (
            {
                "skill_uuid": "00000000-0000-1000-8000-000000000000",
                "version": _VERSION,
            },
            "skill_uuid",
        ),
        ({"skill_uuid": _SKILL_UUID, "version": "../escape"}, "version"),
    ],
)
async def test_center_ensure_rejects_invalid_identity_fields(
    tmp_path: Path,
    item: dict[str, str | None],
    message: str,
) -> None:
    with pytest.raises(_SkillsEnsureError, match=message):
        await _SkillsPortMixin()._skills_ensure_one(
            item,
            tmp_path / "nas",
            tmp_path / "pool",
        )


@pytest.mark.asyncio
async def test_center_ensure_materializes_a_complete_exact_version(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "nas"
    destination = tmp_path / "pool"
    source_version = source / _SKILL_UUID / _VERSION
    source_version.mkdir(parents=True)
    (source_version / "SKILL.md").write_text("complete")
    port = _SkillsPortMixin()
    monkeypatch.setattr(
        port,
        "_skills_rsync_dir",
        lambda src, dst: shutil.copytree(src, dst, dirs_exist_ok=True),
    )

    await port._skills_ensure_one(
        {"skill_uuid": _SKILL_UUID, "version": _VERSION},
        source,
        destination,
    )

    assert (destination / _SKILL_UUID / _VERSION / "SKILL.md").read_text() == "complete"
    assert not (destination / _SKILL_UUID / "current").exists()


@pytest.mark.asyncio
async def test_center_ensure_refuses_a_destination_created_during_materialization(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "nas"
    destination = tmp_path / "pool"
    source_version = source / _SKILL_UUID / _VERSION
    source_version.mkdir(parents=True)
    (source_version / "SKILL.md").write_text("complete")
    destination_version = destination / _SKILL_UUID / _VERSION
    port = _SkillsPortMixin()

    def materialize_then_race(src: Path, temporary: Path) -> None:
        shutil.copytree(src, temporary, dirs_exist_ok=True)
        destination_version.mkdir(parents=True)
        (destination_version / "other.txt").write_text("concurrent")

    monkeypatch.setattr(port, "_skills_rsync_dir", materialize_then_race)

    with pytest.raises(_SkillsEnsureError, match="existing center version incomplete"):
        await port._skills_ensure_one(
            {"skill_uuid": _SKILL_UUID, "version": _VERSION},
            source,
            destination,
        )

    assert (destination_version / "other.txt").read_text() == "concurrent"
    assert not list((destination / _SKILL_UUID).glob(f".{_VERSION}.tmp.*"))


@pytest.mark.asyncio
async def test_center_ensure_cleans_temporary_directory_after_copy_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "nas"
    destination = tmp_path / "pool"
    source_version = source / _SKILL_UUID / _VERSION
    source_version.mkdir(parents=True)
    (source_version / "SKILL.md").write_text("complete")
    port = _SkillsPortMixin()

    def fail_copy(_src: Path, _temporary: Path) -> None:
        raise RuntimeError("rsync failed")

    monkeypatch.setattr(port, "_skills_rsync_dir", fail_copy)

    with pytest.raises(RuntimeError, match="rsync failed"):
        await port._skills_ensure_one(
            {"skill_uuid": _SKILL_UUID, "version": _VERSION},
            source,
            destination,
        )

    assert not list((destination / _SKILL_UUID).glob(f".{_VERSION}.tmp.*"))
