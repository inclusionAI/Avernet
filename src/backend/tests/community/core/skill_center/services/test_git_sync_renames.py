from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agentclaw.community.core.skill_center.services.git_sync import GitSyncService
from agentclaw.community.core.skill_center.services.skill_cache import GlobalSyncLock


def _make_service():
    svc = GitSyncService.__new__(GitSyncService)
    svc.config = MagicMock()
    svc.config.local_bare_repo = "/repo.git"
    return svc


def test_collect_skill_renames_from_git_diff():
    svc = _make_service()
    completed = MagicMock(
        returncode=0,
        stdout=(
            "R100\tlegacy/demo/SKILL.md\tinfra/demo/SKILL.md\n"
            "R087\tlegacy/tool/README.md\tinfra/tool/README.md\n"
            "A\tinfra/new/SKILL.md\n"
        ),
        stderr="",
    )

    with patch("subprocess.run", return_value=completed) as run:
        renames = svc._collect_skill_renames(
            source_path="skills",
            old_tree_sha="oldsha",
            new_tree_sha="newsha",
        )

    assert renames == {"git://legacy/demo": "git://infra/demo"}
    assert run.call_args.args[0] == [
        "git", "diff", "--name-status", "-M", "--diff-filter=R",
        "oldsha", "newsha", "--",
    ]
    assert run.call_args.kwargs["cwd"] == "/repo.git"


def test_collect_skill_renames_ignores_non_skill_directory_moves():
    svc = _make_service()
    completed = MagicMock(
        returncode=0,
        stdout=(
            "R100\tSKILL.md\tmoved/SKILL.md\n"
            "R100\tlegacy/demo/SKILL.md\tinfra/demo/README.md\n"
            "R100\tlegacy/tool/README.md\tinfra/tool/SKILL.md\n"
            "M\tlegacy/changed/SKILL.md\n"
            "not-a-rename-line\n"
        ),
        stderr="",
    )

    with patch("subprocess.run", return_value=completed):
        renames = svc._collect_skill_renames(
            source_path="skills",
            old_tree_sha="oldsha",
            new_tree_sha="newsha",
        )

    assert renames == {}


def test_collect_skill_renames_fails_loudly_on_git_error():
    svc = _make_service()
    completed = MagicMock(returncode=128, stdout="", stderr="bad revision")

    with patch("subprocess.run", return_value=completed):
        with pytest.raises(RuntimeError, match="Git rename detection failed"):
            svc._collect_skill_renames(
                source_path="skills",
                old_tree_sha="oldsha",
                new_tree_sha="newsha",
            )


def test_skill_dir_from_diff_path_extracts_only_nested_skill_dirs():
    assert GitSyncService._skill_dir_from_diff_path("infra/demo/SKILL.md") == "infra/demo"
    assert GitSyncService._skill_dir_from_diff_path("/infra/demo/SKILL.md/") == "infra/demo"
    assert GitSyncService._skill_dir_from_diff_path("SKILL.md") is None
    assert GitSyncService._skill_dir_from_diff_path("infra/demo/README.md") is None


def test_sync_update_database_passes_git_renames_to_skill_service():
    svc = _make_service()
    skill_service = MagicMock()
    skill_service.sync_skills_from_git.return_value = {"updated": 1}
    svc.config.skills_target = "/skills-target"
    svc._skill_service_factory = MagicMock()
    svc._skill_service_factory.create.return_value = skill_service

    renames = {"git://legacy/demo": "git://infra/demo"}
    result = svc._sync_update_database(git_renames=renames)

    assert result == {"updated": 1}
    svc._skill_service_factory.create.assert_called_once_with(
        repo_dir="/skills-target",
        global_repo_dir="/skills-target",
    )
    skill_service.sync_skills_from_git.assert_called_once_with(
        git_renames=renames
    )


async def _run_sync_inline(func, *args, **kwargs):
    return func(*args, **kwargs)


def _make_sync_service_for_sync():
    svc = _make_service()
    # repo_url is a runtime secret resolved in __init__ (bypassed by __new__);
    # set it directly so sync() passes the "no git source configured" guard.
    svc._repo_url = "https://example.test/aiworkbench.git"
    svc.config.subtrees = [
        {"name": "skills"},
        {"name": "agents"},
    ]
    svc.config.enable_oss_sync = False
    svc._cache_plugin = MagicMock()
    svc._cache_plugin.acquire_lock.return_value = "lock-value"
    svc._cache_plugin.release_lock.return_value = None
    svc._run_sync = _run_sync_inline
    svc._git_fetch = AsyncMock(return_value={"success": True})
    svc._update_database = AsyncMock(return_value={"updated": 1})
    svc._refresh_cache_async = AsyncMock()
    svc._sync_upload_skills_repo_to_oss = MagicMock()
    svc._sync_refresh_meta_to_oss = MagicMock()
    return svc


@pytest.mark.asyncio
async def test_sync_passes_skill_renames_when_skills_subtree_updates():
    svc = _make_sync_service_for_sync()
    renames = {"git://legacy/demo": "git://infra/demo"}
    svc._sync_subtree = AsyncMock(
        side_effect=[
            {"success": True, "updated": True, "renames": renames},
            {"success": True, "updated": False, "renames": {}},
        ]
    )

    with patch.object(GlobalSyncLock, "acquire", return_value=True), patch.object(
        GlobalSyncLock, "release"
    ):
        result = await svc.sync()

    assert result["success"] is True
    assert result["cache_refreshed"] is True
    svc._update_database.assert_awaited_once_with(git_renames=renames)
    svc._refresh_cache_async.assert_awaited_once()
    # enable_oss_sync=False → OSS upload/refresh skipped
    svc._sync_upload_skills_repo_to_oss.assert_not_called()
    svc._sync_refresh_meta_to_oss.assert_not_called()


@pytest.mark.asyncio
async def test_sync_does_not_update_database_when_only_agents_subtree_updates():
    svc = _make_sync_service_for_sync()
    svc._sync_subtree = AsyncMock(
        side_effect=[
            {"success": True, "updated": False, "renames": {}},
            {"success": True, "updated": True, "renames": {}},
        ]
    )

    with patch.object(GlobalSyncLock, "acquire", return_value=True), patch.object(
        GlobalSyncLock, "release"
    ):
        result = await svc.sync()

    assert result["success"] is True
    assert result["cache_refreshed"] is False
    svc._update_database.assert_not_awaited()
    svc._refresh_cache_async.assert_not_awaited()
    svc._sync_upload_skills_repo_to_oss.assert_not_called()
    # enable_oss_sync=False → OSS upload/refresh skipped
    svc._sync_refresh_meta_to_oss.assert_not_called()


@pytest.mark.asyncio
async def test_sync_aborts_database_update_on_subtree_failure():
    svc = _make_sync_service_for_sync()
    svc._sync_subtree = AsyncMock(
        side_effect=[
            {"success": True, "updated": False, "renames": {}},
            {"success": True, "updated": False, "renames": {}},
        ]
    )

    with patch.object(GlobalSyncLock, "acquire", return_value=True), patch.object(
        GlobalSyncLock, "release"
    ):
        result = await svc.sync()

    assert result["success"] is True
    assert result["cache_refreshed"] is False
    svc._update_database.assert_not_awaited()
    svc._refresh_cache_async.assert_not_awaited()
    svc._sync_upload_skills_repo_to_oss.assert_not_called()
    svc._sync_refresh_meta_to_oss.assert_not_called()


@pytest.mark.asyncio
async def test_sync_schedules_background_oss_sync_when_enabled():
    """sync() 末尾在 enable_oss_sync=True + success 时 fire-and-forget 提交后台 rsync。"""
    svc = _make_sync_service_for_sync()
    svc.config.enable_oss_sync = True  # 关键：让 sync() 末尾分支命中
    svc._sync_subtree = AsyncMock(
        side_effect=[
            {"success": True, "updated": False, "renames": {}},
            {"success": True, "updated": False, "renames": {}},
        ]
    )
    # _sync_to_oss_async 是 async 的，不能 mock 成 MagicMock（asyncio.create_task 会拒绝）
    # 用 AsyncMock 让 create_task 拿到的是 coroutine
    svc._sync_to_oss_async = AsyncMock()

    with patch.object(GlobalSyncLock, "acquire", return_value=True), patch.object(
        GlobalSyncLock, "release"
    ):
        result = await svc.sync()

    assert result["success"] is True
    # 命中 asyncio.create_task(self._sync_to_oss_async()) 这一行
    svc._sync_to_oss_async.assert_called_once()
