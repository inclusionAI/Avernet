"""Upload device-fs path adapting for teclaw vs non-teclaw (Task 4).

teclaw records the minimal logical ``local://skills-local/<name>`` and forwards
writes to the engine under the workspace namespace (``workspace/skills-local/...``);
non-teclaw passes its host path through unchanged (identity adapter).
"""
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from agentclaw.community.core.config_compose.teclaw_paths import to_local_skill_engine_path
from agentclaw.community.core.skill_center.services.skill_service import SkillService

pytestmark = pytest.mark.unit

SKILL_MD = b"""---
name: sync-and-pr
description: a test skill
---
# Sync and PR
"""


def _service(local_dir: Path, adapter, fake_fs) -> SkillService:
    repo = MagicMock()
    repo.list_skills.return_value = []  # get_skill_by_path -> None -> create path
    repo.get_bot_local_by_name.return_value = None
    repo.get_by_git_path.return_value = None
    svc = SkillService(
        skill_repo=repo,
        skill_repo_sync=MagicMock(get_local_skills_root=MagicMock(return_value=None)),
        category_repo=MagicMock(),
        market_cache=MagicMock(),
        device_fs_factory=lambda bot_id, user_id: fake_fs,
        git_sync_service_factory=MagicMock(),
        active_dir=Path("/tmp/active"),
        repo_dir=Path("/tmp/repo"),
        local_dir=local_dir,
        local_skill_path_adapter=adapter,
    )
    svc.create_skill = MagicMock(return_value={"id": "1", "name": "sync-and-pr"})
    return svc


@pytest.mark.asyncio
async def test_teclaw_upload_adapts_path_and_stores_logical_git_path():
    fake_fs = MagicMock()
    fake_fs.delete_tree = AsyncMock(return_value=True)
    fake_fs.write_file = AsyncMock()
    svc = _service(Path("skills-local"), to_local_skill_engine_path, fake_fs)

    uploaded = [{"filename": "SKILL.md", "relative_path": "SKILL.md", "content": SKILL_MD}]
    await svc.upload_skill(uploaded, user_id="u1", bolt_id="b1")

    # device-fs sees the workspace-namespace-relative engine path (flat, no skills/)
    fake_fs.delete_tree.assert_awaited_with("workspace/skills-local/sync-and-pr")
    fake_fs.write_file.assert_awaited_with(
        "workspace/skills-local/sync-and-pr/SKILL.md", SKILL_MD
    )
    # DB git_path keeps the minimal logical form (not the engine path)
    assert svc.create_skill.call_args.kwargs["skill_path"] == "local://skills-local/sync-and-pr"


@pytest.mark.asyncio
async def test_upload_uses_bot_owner_for_skill_metadata():
    fake_fs = MagicMock()
    fake_fs.delete_tree = AsyncMock(return_value=True)
    fake_fs.write_file = AsyncMock()
    svc = _service(Path("skills-local"), to_local_skill_engine_path, fake_fs)

    uploaded = [{"filename": "SKILL.md", "relative_path": "SKILL.md", "content": SKILL_MD}]
    await svc.upload_skill(
        uploaded,
        user_id="bot-owner",
        bolt_id="b1",
    )

    assert svc.create_skill.call_args.kwargs["user_id"] == "bot-owner"


@pytest.mark.asyncio
async def test_reupload_normalizes_historical_collaborator_owner():
    fake_fs = MagicMock()
    fake_fs.delete_tree = AsyncMock(return_value=True)
    fake_fs.write_file = AsyncMock()
    svc = _service(Path("skills-local"), to_local_skill_engine_path, fake_fs)
    historical = {
        "id": "17",
        "name": "sync-and-pr",
        "bolt_id": "b1",
        "user_id": "collaborator",
        "git_path": "local://skills-local/sync-and-pr",
    }
    svc._skill_repo.get_by_git_path.return_value = historical
    svc._skill_repo.update.return_value = {**historical, "user_id": "bot-owner"}

    uploaded = [
        {
            "filename": "SKILL.md",
            "relative_path": "SKILL.md",
            "content": SKILL_MD,
        }
    ]
    result = await svc.upload_skill(
        uploaded,
        user_id="bot-owner",
        bolt_id="b1",
    )

    svc._skill_repo.get_bot_local_by_name.assert_called_once_with(
        bot_id="b1",
        name="sync-and-pr",
        user_id="bot-owner",
    )
    svc._skill_repo.get_by_git_path.assert_called_once_with(
        "local://skills-local/sync-and-pr"
    )
    assert svc._skill_repo.update.call_args.args[0] == "17"
    assert svc._skill_repo.update.call_args.args[1]["user_id"] == "bot-owner"
    assert result["user_id"] == "bot-owner"


@pytest.mark.asyncio
async def test_default_bot_upload_never_uses_unscoped_owner_lookup():
    """Shared ``default`` Bot IDs must not select another owner's same-name row."""
    fake_fs = MagicMock()
    fake_fs.delete_tree = AsyncMock(return_value=True)
    fake_fs.write_file = AsyncMock()
    svc = _service(Path("skills-local"), to_local_skill_engine_path, fake_fs)

    await svc.upload_skill(
        [{"filename": "SKILL.md", "relative_path": "SKILL.md", "content": SKILL_MD}],
        user_id="bot-owner",
        bolt_id="default",
    )

    svc._skill_repo.get_bot_local_by_name.assert_called_once_with(
        bot_id="default",
        name="sync-and-pr",
        user_id="bot-owner",
    )
    svc._skill_repo.get_by_git_path.assert_called_once_with(
        "local://skills-local/sync-and-pr"
    )


@pytest.mark.asyncio
async def test_non_teclaw_upload_passes_host_path_through_unchanged():
    fake_fs = MagicMock()
    fake_fs.delete_tree = AsyncMock(return_value=True)
    fake_fs.write_file = AsyncMock()
    host_local = Path("/aidesktop/aidesktop_pre/bolt_data/staff_1/b1/openclaw/workspace/skills/skills-local")
    # No adapter -> identity (arca/baas behavior)
    svc = _service(host_local, None, fake_fs)

    uploaded = [{"filename": "SKILL.md", "relative_path": "SKILL.md", "content": SKILL_MD}]
    await svc.upload_skill(uploaded, user_id="u1", bolt_id="b1")

    host_skill_dir = str(host_local / "sync-and-pr")
    fake_fs.delete_tree.assert_awaited_with(host_skill_dir)
    fake_fs.write_file.assert_awaited_with(f"{host_skill_dir}/SKILL.md", SKILL_MD)
    assert svc.create_skill.call_args.kwargs["skill_path"] == f"local://{host_skill_dir}"


def _readonly_service(adapter, fake_fs, skill_row) -> SkillService:
    repo = MagicMock()
    repo.get_by_id.return_value = skill_row
    repo.list_skill_set_references.return_value = []
    svc = SkillService(
        skill_repo=repo,
        skill_repo_sync=MagicMock(get_local_skills_root=MagicMock(return_value=None)),
        category_repo=MagicMock(),
        market_cache=MagicMock(),
        device_fs_factory=lambda bot_id, user_id: fake_fs,
        git_sync_service_factory=MagicMock(),
        local_dir=Path("skills-local"),
        local_skill_path_adapter=adapter,
    )
    return svc


@pytest.mark.asyncio
async def test_teclaw_get_skill_readme_reads_adapted_path():
    fake_fs = MagicMock()
    fake_fs.read_file = AsyncMock(
        side_effect=lambda p: SKILL_MD if p == "workspace/skills-local/sync-and-pr/SKILL.md" else None
    )
    svc = _readonly_service(
        to_local_skill_engine_path,
        fake_fs,
        {"id": "123", "name": "sync-and-pr", "git_path": "local://skills-local/sync-and-pr",
         "bolt_id": "b1", "user_id": "u1"},
    )
    readme = await svc.get_skill_readme("123", user_id="u1")
    assert "Sync and PR" in readme
    fake_fs.read_file.assert_any_await("workspace/skills-local/sync-and-pr/SKILL.md")


@pytest.mark.asyncio
async def test_teclaw_delete_skill_removes_adapted_path():
    fake_fs = MagicMock()
    fake_fs.exists = AsyncMock(return_value=True)
    fake_fs.delete_tree = AsyncMock(return_value=True)
    fake_fs.delete_file = AsyncMock(return_value=True)
    fake_fs.read_file = AsyncMock(return_value=None)
    fake_fs.list_dir = AsyncMock(return_value=None)
    svc = _readonly_service(
        to_local_skill_engine_path,
        fake_fs,
        {"id": "123", "name": "sync-and-pr", "git_path": "local://skills-local/sync-and-pr",
         "bolt_id": "b1", "user_id": "u1"},
    )
    svc._can_delete_skill = MagicMock(return_value=True)
    svc._skill_repo.delete = MagicMock(return_value=True)

    await svc.delete_skill("123", user_id="u1")
    fake_fs.delete_tree.assert_any_await("workspace/skills-local/sync-and-pr")


@pytest.mark.asyncio
async def test_teclaw_parse_local_skill_config_reads_via_device_fs():
    fake_fs = MagicMock()
    fake_fs.read_file = AsyncMock(return_value=SKILL_MD)
    svc = _readonly_service(to_local_skill_engine_path, fake_fs, {})
    info = await svc.parse_local_skill_config("local://skills-local/sync-and-pr", "b1", "u1")
    fake_fs.read_file.assert_awaited_with("workspace/skills-local/sync-and-pr/SKILL.md")
    assert info is not None


@pytest.mark.asyncio
async def test_parse_local_skill_config_none_for_non_local():
    fake_fs = MagicMock()
    fake_fs.read_file = AsyncMock(return_value=None)
    svc = _readonly_service(to_local_skill_engine_path, fake_fs, {})
    assert await svc.parse_local_skill_config("git://team/weather", "b1", "u1") is None
    fake_fs.read_file.assert_not_awaited()


def test_parse_skill_from_git_host_fs_unchanged_for_missing_path():
    """Regression: the host-FS read path (non-teclaw) is untouched — a missing
    absolute local path still returns None via source.exists()."""
    fake_fs = MagicMock()
    svc = _readonly_service(None, fake_fs, {})  # identity adapter; device_fs unused
    assert svc._parse_skill_from_git("local:///nonexistent/skills-local/x") is None


def test_parse_skill_path_accepts_teclaw_logical_form():
    svc = _readonly_service(None, MagicMock(), {})
    protocol, path = svc.parse_skill_path("local://skills-local/sync-and-pr")
    assert protocol == "local"
    assert str(path) == "skills-local/sync-and-pr"


def test_parse_skill_path_rejects_non_absolute_non_skills_local():
    svc = _readonly_service(None, MagicMock(), {})
    with pytest.raises(ValueError):
        svc.parse_skill_path("local://data/foo.csv")


@pytest.mark.asyncio
async def test_parse_local_skill_config_returns_none_when_missing():
    fake_fs = MagicMock()
    fake_fs.read_file = AsyncMock(return_value=None)  # SKILL.md not found
    svc = _readonly_service(to_local_skill_engine_path, fake_fs, {})
    assert await svc.parse_local_skill_config("local://skills-local/x", "b1", "u1") is None


@pytest.mark.asyncio
async def test_parse_local_skill_config_swallows_read_errors():
    fake_fs = MagicMock()
    fake_fs.read_file = AsyncMock(side_effect=RuntimeError("engine down"))
    svc = _readonly_service(to_local_skill_engine_path, fake_fs, {})
    assert await svc.parse_local_skill_config("local://skills-local/x", "b1", "u1") is None


@pytest.mark.asyncio
async def test_parse_local_skill_config_decodes_gbk_fallback():
    # GBK-encoded SKILL.md fails utf-8 decode → falls back to gbk.
    gbk_md = "---\nname: a\ndescription: 中文说明\n---".encode("gbk")
    fake_fs = MagicMock()
    fake_fs.read_file = AsyncMock(return_value=gbk_md)
    svc = _readonly_service(to_local_skill_engine_path, fake_fs, {})
    info = await svc.parse_local_skill_config("local://skills-local/x", "b1", "u1")
    assert info is not None


@pytest.mark.asyncio
async def test_teclaw_upload_rolls_back_on_write_error():
    fake_fs = MagicMock()
    fake_fs.delete_tree = AsyncMock(return_value=True)
    fake_fs.write_file = AsyncMock(side_effect=RuntimeError("write failed"))
    svc = _service(Path("skills-local"), to_local_skill_engine_path, fake_fs)
    uploaded = [{"filename": "SKILL.md", "relative_path": "SKILL.md", "content": SKILL_MD}]
    with pytest.raises(ValueError):
        await svc.upload_skill(uploaded, user_id="u1", bolt_id="b1")
    # rollback delete_tree runs on the adapted engine path (last call)
    assert fake_fs.delete_tree.await_args_list[-1].args[0] == "workspace/skills-local/sync-and-pr"
