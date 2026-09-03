from __future__ import annotations

from pathlib import Path

import pytest

from engine.community.plugins.openclaw.plugin_impl import OpenClawPluginImpl


def _plugin_for_root(
    monkeypatch: pytest.MonkeyPatch,
    root: Path,
    *,
    mounted: bool,
) -> OpenClawPluginImpl:
    plugin = OpenClawPluginImpl()
    monkeypatch.setattr(plugin, "_skills_center_root", lambda: root)
    monkeypatch.setattr(
        plugin,
        "_skills_center_is_mounted",
        lambda path: mounted and path == root,
    )
    return plugin


@pytest.mark.asyncio
async def test_ensure_is_read_only_when_center_mount_is_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "skill-center"
    plugin = _plugin_for_root(monkeypatch, root, mounted=False)

    result = await plugin.ensure_center_skills(
        {"items": [{"skill_uuid": "u1", "version": "1"}]}
    )

    assert result == {
        "ok": [],
        "failed": [
            {
                "skill_uuid": "u1",
                "version": "1",
                "code": "CENTER_MOUNT_NOT_READY",
                "reason": "Skill Center 目录尚未挂载，请重启 Bot 后重试",
            }
        ],
    }
    assert not root.exists()


@pytest.mark.asyncio
async def test_ensure_accepts_an_exact_readable_center_version(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "skill-center"
    version = root / "u1" / "1"
    version.mkdir(parents=True)
    (version / "SKILL.md").write_text("---\nname: demo\n---\n")
    plugin = _plugin_for_root(monkeypatch, root, mounted=True)

    item = {"skill_uuid": "u1", "version": "1"}
    result = await plugin.ensure_center_skills({"items": [item]})

    assert result == {"ok": [item], "failed": []}


@pytest.mark.asyncio
async def test_ensure_reports_missing_exact_center_version_without_writing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "skill-center"
    root.mkdir()
    plugin = _plugin_for_root(monkeypatch, root, mounted=True)

    result = await plugin.ensure_center_skills(
        {"items": [{"skill_uuid": "u1", "version": "2"}]}
    )

    assert result["ok"] == []
    assert result["failed"][0]["code"] == "CENTER_VERSION_NOT_FOUND"
    assert not (root / "u1").exists()
