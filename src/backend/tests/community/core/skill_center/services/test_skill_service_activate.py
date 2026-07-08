"""Phase 4: activate_skill must route old-link delete via device_fs.delete_tree."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_service(device_fs):
    from agentclaw.community.core.skill_center.services.skill_service import SkillService
    return SkillService(
        skill_repo=MagicMock(),
        skill_repo_sync=MagicMock(),
        category_repo=MagicMock(),
        market_cache=MagicMock(),
        active_dir=Path("/oss/skills"),
        repo_dir=Path("/oss/skills-repo"),
        local_dir=Path("/oss/skills-local"),
        device_fs_factory=lambda b, u: device_fs,
        git_sync_service_factory=MagicMock(),
    )


class TestActivateSkillReplaceOldLink:
    @pytest.mark.asyncio
    async def test_activate_calls_device_fs_when_old_link_exists(self):
        """activate_skill 发现 target_link 已存在时，应通过 device_fs.delete_tree 删旧链接。"""
        device_fs = MagicMock()
        device_fs.delete_tree = AsyncMock(return_value=True)
        svc = _make_service(device_fs)

        skill = {
            "id": "sk-1", "name": "my-skill", "git_path": "git://x/y",
            "bolt_id": "bolt-1", "user_id": "u-1",
        }

        with patch.object(svc, "get_skill", return_value=skill), patch(
            "pathlib.Path.exists", return_value=True
        ), patch(
            "pathlib.Path.is_symlink", return_value=True
        ), patch("pathlib.Path.symlink_to") as mock_symlink, patch(
            "shutil.rmtree"
        ) as mock_rmtree, patch.object(Path, "unlink") as mock_unlink:
            ok = await svc.activate_skill("git://x/y", user_id="u-1", bolt_id="bolt-1")

        assert ok is True
        device_fs.delete_tree.assert_awaited()
        called_paths = [c.args[0] for c in device_fs.delete_tree.await_args_list]
        # git://x/y → 软链名取尾名 "y"
        assert any("y" in p for p in called_paths)
        mock_rmtree.assert_not_called()
        mock_unlink.assert_not_called()