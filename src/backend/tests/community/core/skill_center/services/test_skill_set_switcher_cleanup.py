"""Unit tests for SkillSetSwitcher._cleanup_all_non_reserved_items via DeviceFileSystemPlugin (plan-05)."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

def _edit_guard():
    guard = MagicMock()
    guard.acquire_for_edit_wait = AsyncMock(return_value=object())
    return guard


def _make_factory(dispatcher=None, **overrides):
    """Build a SkillSetSwitcherFactory with mock deps + optional dispatcher."""
    from agentclaw.community.core.skill_center.services.skill_set_service import (
        SkillSetSwitcherFactory,
    )
    return SkillSetSwitcherFactory(
        skill_set_factory=overrides.get("skill_set_factory", MagicMock()),
        resolver=overrides.get("resolver", MagicMock()),
        device_sync_dispatcher=overrides.get("device_sync_dispatcher", MagicMock()),
        device_plugin=overrides.get("device_plugin", MagicMock()),
        path_factory=overrides.get("path_factory", MagicMock()),
        device_fs_dispatcher=dispatcher or MagicMock(),
        edit_guard=overrides.get("edit_guard", _edit_guard()),
    )


def test_factory_ctor_accepts_dispatcher():
    factory = _make_factory()
    assert factory is not None


def test_switcher_constructed_with_dispatcher(tmp_path):
    """factory.create(...) 把 dispatcher 透传给 SkillSetSwitcher。"""
    from agentclaw.community.core.skill_center.services.skill_set_service import (
        SkillSetSwitcher,
    )

    dispatcher = MagicMock()
    factory = _make_factory(dispatcher=dispatcher)

    # path_factory mock：让 SkillSetSwitcher 走 deprecated-paths 分支（无 user_id/entity_id）
    # 这样它直接用 ctor 默认值不调 _get_bot_paths
    factory._path_factory = MagicMock()

    switcher = factory.create(bot_id="bot-1")
    assert isinstance(switcher, SkillSetSwitcher)
    assert switcher._device_fs_dispatcher is dispatcher


def test_cleanup_uses_plugin_list_dir_then_delete_tree(monkeypatch, tmp_path):
    """改造后：_cleanup_all_non_reserved_items 通过 device_fs.list_dir +
    delete_tree 走 plugin，不再调用 pathlib.Path.iterdir / shutil.rmtree。"""
    from agentclaw.community.core.skill_center.services.skill_set_service import (
        SkillSetSwitcher,
    )

    # Mock plugin: list_dir 返 3 entries（2 个该清的、1 个 reserved）
    fake_plugin = MagicMock()

    async def _list_dir(path, *, recursive=False):
        return [
            {"name": "skills-repo", "path": f"{path}/skills-repo", "is_dir": True, "relative_path": "skills-repo"},
            {"name": "stale-symlink", "path": f"{path}/stale-symlink", "is_dir": False, "relative_path": "stale-symlink"},
            {"name": "stale-dir", "path": f"{path}/stale-dir", "is_dir": True, "relative_path": "stale-dir"},
        ]

    async def _delete_tree(path):
        return True

    fake_plugin.list_dir = _list_dir
    fake_plugin.delete_tree = MagicMock(side_effect=_delete_tree)

    dispatcher = MagicMock()
    dispatcher.dispatch = MagicMock(return_value=fake_plugin)

    resolver = MagicMock()
    fake_ctx = MagicMock()
    resolver.resolve_for_bot.return_value = fake_ctx

    skill_set_factory = MagicMock()
    skill_set_service_mock = MagicMock()
    skill_set_service_mock.skill_service.RESERVED_SKILL_NAMES = {
        "skills-repo", "skills-local", ".current_skill_set", "skill_sets.json",
    }
    skill_set_factory.create.return_value = skill_set_service_mock

    # Configure path_factory so _get_bot_paths returns the real tmp_path-based dirs
    # (init takes the new-path branch when user_id/entity_id is set).
    path_factory = MagicMock()
    skills_root = tmp_path / "skills"
    path_factory.get_bot_skills_dir.return_value = skills_root
    path_factory.get_bot_skills_repo_dir.return_value = skills_root / "skills-repo"
    path_factory.get_bot_skills_local_dir.return_value = skills_root / "skills-local"

    switcher = SkillSetSwitcher(
        skill_set_factory=skill_set_factory,
        resolver=resolver,
        device_sync_dispatcher=MagicMock(),
        device_plugin=MagicMock(),
        path_factory=path_factory,
        device_fs_dispatcher=dispatcher,
        edit_guard=_edit_guard(),
        skills_dir=tmp_path / "skills",
        bot_id="bot-1",
        user_id="staff_u001",
    )

    cleaned = switcher._cleanup_all_non_reserved_items()

    # 应清除 stale-symlink 和 stale-dir，跳过 skills-repo
    assert sorted(cleaned) == ["stale-dir", "stale-symlink"]
    # plugin.delete_tree 被调 2 次，路径为容器路径（从 list_dir entry 的 path 取）
    assert fake_plugin.delete_tree.call_count == 2
    called_paths = sorted(call.args[0] for call in fake_plugin.delete_tree.call_args_list)
    assert called_paths == [f"{tmp_path}/skills/stale-dir", f"{tmp_path}/skills/stale-symlink"]
    # resolver 被调 1 次,dispatcher.dispatch 被调 1 次拿到 plugin
    resolver.resolve_for_bot.assert_called_once_with("bot-1", "staff_u001")
    dispatcher.dispatch.assert_called_once_with(fake_ctx)


def test_cleanup_skills_dir_does_not_exist_returns_empty(tmp_path):
    """skills_dir 不存在时返空（plugin.list_dir 返 None）。"""
    from agentclaw.community.core.skill_center.services.skill_set_service import (
        SkillSetSwitcher,
    )

    fake_plugin = MagicMock()
    async def _list_none(path, *, recursive=False):
        return None
    fake_plugin.list_dir = _list_none

    dispatcher = MagicMock()
    dispatcher.dispatch.return_value = fake_plugin

    skill_set_factory = MagicMock()
    skill_set_service_mock = MagicMock()
    skill_set_service_mock.skill_service.RESERVED_SKILL_NAMES = set()
    skill_set_factory.create.return_value = skill_set_service_mock

    switcher = SkillSetSwitcher(
        skill_set_factory=skill_set_factory,
        resolver=MagicMock(),
        device_sync_dispatcher=MagicMock(),
        device_plugin=MagicMock(),
        path_factory=MagicMock(),
        device_fs_dispatcher=dispatcher,
        edit_guard=_edit_guard(),
        skills_dir=tmp_path / "nope",
        bot_id="bot-1",
        user_id="staff_u001",
    )

    assert switcher._cleanup_all_non_reserved_items() == []


def test_cleanup_delete_failure_continues_and_logs(tmp_path, caplog):
    """单个 delete_tree 失败 → 继续处理其他 entry，记 warning。"""
    from agentclaw.community.core.skill_center.services.skill_set_service import (
        SkillSetSwitcher,
    )

    fake_plugin = MagicMock()
    async def _list(path, *, recursive=False):
        return [
            {"name": "good", "path": f"{path}/good", "is_dir": False, "relative_path": "good"},
            {"name": "bad", "path": f"{path}/bad", "is_dir": True, "relative_path": "bad"},
        ]

    delete_calls = []
    async def _delete(path):
        delete_calls.append(path)
        if path.endswith("/bad"):
            raise RuntimeError("simulated failure")
        return True

    fake_plugin.list_dir = _list
    fake_plugin.delete_tree = _delete

    dispatcher = MagicMock()
    dispatcher.dispatch.return_value = fake_plugin

    skill_set_factory = MagicMock()
    skill_set_service_mock = MagicMock()
    skill_set_service_mock.skill_service.RESERVED_SKILL_NAMES = set()
    skill_set_factory.create.return_value = skill_set_service_mock

    # Configure path_factory so _get_bot_paths returns the real tmp_path-based dirs
    # (init takes the new-path branch when user_id/entity_id is set).
    path_factory = MagicMock()
    skills_root = tmp_path / "skills"
    path_factory.get_bot_skills_dir.return_value = skills_root
    path_factory.get_bot_skills_repo_dir.return_value = skills_root / "skills-repo"
    path_factory.get_bot_skills_local_dir.return_value = skills_root / "skills-local"

    switcher = SkillSetSwitcher(
        skill_set_factory=skill_set_factory,
        resolver=MagicMock(),
        device_sync_dispatcher=MagicMock(),
        device_plugin=MagicMock(),
        path_factory=path_factory,
        device_fs_dispatcher=dispatcher,
        edit_guard=_edit_guard(),
        skills_dir=tmp_path / "skills",
        bot_id="bot-1",
        user_id="staff_u001",
    )

    cleaned = switcher._cleanup_all_non_reserved_items()
    assert cleaned == ["good"]  # bad 未被记入 cleaned（失败）
    assert len(delete_calls) == 2  # 两条都尝试调


def _switcher_over(tmp_path, plugin, reserved=frozenset()):
    """A SkillSetSwitcher whose cleanup runs against ``plugin``."""
    from agentclaw.community.core.skill_center.services.skill_set_service import (
        SkillSetSwitcher,
    )

    dispatcher = MagicMock()
    dispatcher.dispatch.return_value = plugin
    skill_set_factory = MagicMock()
    skill_set_service_mock = MagicMock()
    skill_set_service_mock.skill_service.RESERVED_SKILL_NAMES = set(reserved)
    skill_set_factory.create.return_value = skill_set_service_mock
    path_factory = MagicMock()
    skills_root = tmp_path / "skills"
    path_factory.get_bot_skills_dir.return_value = skills_root
    path_factory.get_bot_skills_repo_dir.return_value = skills_root / "skills-repo"
    path_factory.get_bot_skills_local_dir.return_value = skills_root / "skills-local"
    return SkillSetSwitcher(
        skill_set_factory=skill_set_factory,
        resolver=MagicMock(),
        device_sync_dispatcher=MagicMock(),
        device_plugin=MagicMock(),
        path_factory=path_factory,
        device_fs_dispatcher=dispatcher,
        edit_guard=_edit_guard(),
        skills_dir=skills_root,
        bot_id="bot-1",
        user_id="staff_u001",
    )


def _entries(names, root):
    return [
        {"name": name, "path": f"{root}/{name}", "is_dir": True, "relative_path": name}
        for name in names
    ]


def test_cleanup_removes_entries_concurrently(tmp_path):
    """每次删除都是一次设备往返；逐个 await 让一次切换 = ``entry_count × 往返``。"""
    import asyncio

    state = {"in_flight": 0, "peak": 0}
    names = [f"stale-{index}" for index in range(6)]

    async def _delete_tree(path):
        state["in_flight"] += 1
        state["peak"] = max(state["peak"], state["in_flight"])
        try:
            await asyncio.sleep(0.01)
            return True
        finally:
            state["in_flight"] -= 1

    plugin = MagicMock()

    async def _list_dir(path, *, recursive=False):
        return _entries(names, path)

    plugin.list_dir = _list_dir
    plugin.delete_tree = _delete_tree

    switcher = _switcher_over(tmp_path, plugin)
    cleaned = switcher._cleanup_all_non_reserved_items()

    assert sorted(cleaned) == sorted(names)
    assert state["peak"] > 1


def test_cleanup_reports_every_entry_even_when_one_device_call_fails(tmp_path):
    """一个条目删除失败既不隐藏其他条目的结果，也不中止它们 —— 与逐条循环一致。"""

    async def _delete_tree(path):
        if path.endswith("/boom"):
            raise OSError("device rejected boom")
        return not path.endswith("/refused")

    plugin = MagicMock()

    async def _list_dir(path, *, recursive=False):
        return _entries(["ok-1", "boom", "refused", "ok-2"], path)

    plugin.list_dir = _list_dir
    plugin.delete_tree = _delete_tree

    switcher = _switcher_over(tmp_path, plugin)
    cleaned = switcher._cleanup_all_non_reserved_items()

    # order follows the listing, and only the genuinely removed entries are named
    assert cleaned == ["ok-1", "ok-2"]
