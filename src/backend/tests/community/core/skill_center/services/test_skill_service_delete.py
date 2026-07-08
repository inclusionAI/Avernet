"""Phase 4: delete_skill step 1 must route via device_fs.delete_tree, not shutil.rmtree."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_service(device_fs):
    """Build SkillService with a mocked device_fs_factory and stubbed deps."""
    from agentclaw.community.core.skill_center.services.skill_service import SkillService

    factory = MagicMock(return_value=device_fs)
    svc = SkillService(
        skill_repo=MagicMock(),
        skill_repo_sync=MagicMock(),
        category_repo=MagicMock(),
        market_cache=MagicMock(),
        active_dir=Path("/oss-view/.../skills"),
        repo_dir=Path("/oss-view/.../skills-repo"),
        local_dir=Path("/oss-view/.../skills-local"),
        device_fs_factory=factory,
        git_sync_service_factory=MagicMock(),
    )
    return svc, factory


class TestDeleteSkillStep1:
    @pytest.mark.asyncio
    async def test_delete_skill_calls_device_fs_delete_tree_for_active_link(self):
        """delete_skill step 1 must call device_fs.delete_tree on active_link, not shutil.rmtree."""
        device_fs = MagicMock()
        device_fs.delete_tree = AsyncMock(return_value=True)
        svc, factory = _make_service(device_fs)

        skill = {
            "id": "sk-1",
            "name": "my-skill",
            "git_path": "git://x/y",
            "bolt_id": "bolt-1",
            "user_id": "u-1",
        }
        with patch.object(svc, "get_skill", return_value=skill), patch.object(
            svc, "_can_delete_skill", return_value=True
        ), patch.object(
            svc, "get_link_name", return_value="my-skill"
        ), patch(
            "shutil.rmtree"
        ) as mock_rmtree, patch.object(
            svc._skill_repo, "delete", return_value=True
        ):
            with patch("pathlib.Path.exists", return_value=True), patch(
                "pathlib.Path.is_symlink", return_value=True
            ):
                ok = await svc.delete_skill("sk-1", user_id="u-1")

        assert ok is True
        assert device_fs.delete_tree.await_count >= 1
        called_paths = [c.args[0] for c in device_fs.delete_tree.await_args_list]
        assert any("my-skill" in p and "skills" in p for p in called_paths), \
            f"expected active_link path in delete_tree calls, got: {called_paths}"
        mock_rmtree.assert_not_called()

    @pytest.mark.asyncio
    async def test_delete_skill_swallows_active_link_delete_failure(self):
        """If device_fs.delete_tree returns False for active link, delete_skill still proceeds."""
        device_fs = MagicMock()
        device_fs.delete_tree = AsyncMock(side_effect=[False, True])
        svc, _ = _make_service(device_fs)

        skill = {
            "id": "sk-1",
            "name": "x",
            "git_path": "local:///home/admin/.../skills-local/x",
            "bolt_id": "bolt-1",
            "user_id": "u-1",
        }
        with patch.object(svc, "get_skill", return_value=skill), patch.object(
            svc, "_can_delete_skill", return_value=True
        ), patch.object(
            svc, "get_link_name", return_value="x"
        ), patch.object(
            svc._skill_repo, "delete", return_value=True
        ), patch("pathlib.Path.exists", return_value=True), patch(
            "pathlib.Path.is_symlink", return_value=True
        ):
            ok = await svc.delete_skill("sk-1", user_id="u-1")

        assert ok is True


class TestDeleteActiveEntryHelper:
    @pytest.mark.asyncio
    async def test_helper_returns_true_when_delete_tree_succeeds(self, tmp_path):
        from agentclaw.community.core.skill_center.services.skill_service import SkillService

        device_fs = MagicMock()
        device_fs.delete_tree = AsyncMock(return_value=True)
        svc = SkillService(
            skill_repo=MagicMock(),
            skill_repo_sync=MagicMock(),
            category_repo=MagicMock(),
            market_cache=MagicMock(),
            active_dir=tmp_path / "skills",
            repo_dir=tmp_path / "skills-repo",
            local_dir=tmp_path / "skills-local",
            device_fs_factory=lambda b, u: device_fs,
            git_sync_service_factory=MagicMock(),
        )

        ok = await svc._delete_active_entry(device_fs, Path("/oss/skills/foo"))

        assert ok is True
        device_fs.delete_tree.assert_awaited_once_with("/oss/skills/foo")

    @pytest.mark.asyncio
    async def test_helper_returns_false_and_logs_on_exception(self, tmp_path):
        from agentclaw.community.core.skill_center.services.skill_service import SkillService

        device_fs = MagicMock()
        device_fs.delete_tree = AsyncMock(side_effect=RuntimeError("boom"))
        svc = SkillService(
            skill_repo=MagicMock(),
            skill_repo_sync=MagicMock(),
            category_repo=MagicMock(),
            market_cache=MagicMock(),
            active_dir=tmp_path / "skills",
            repo_dir=tmp_path / "skills-repo",
            local_dir=tmp_path / "skills-local",
            device_fs_factory=lambda b, u: device_fs,
            git_sync_service_factory=MagicMock(),
        )

        ok = await svc._delete_active_entry(device_fs, Path("/oss/skills/foo"))

        assert ok is False
