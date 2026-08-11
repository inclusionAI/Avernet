"""Phase 4: deactivate_skill must route active-link delete via device_fs.delete_tree."""
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


class TestDeactivateSkill:
    @pytest.mark.asyncio
    async def test_deactivate_calls_device_fs_delete_tree(self):
        device_fs = MagicMock()
        device_fs.delete_tree = AsyncMock(return_value=True)
        svc = _make_service(device_fs)

        with patch("pathlib.Path.exists", return_value=True), patch(
            "pathlib.Path.is_symlink", return_value=True
        ), patch(
            "shutil.rmtree"
        ) as mock_rmtree, patch.object(Path, "unlink") as mock_unlink:
            ok = await svc.deactivate_skill("my-skill", bolt_id="bolt-1", user_id="u-1")

        assert ok is True
        device_fs.delete_tree.assert_awaited()
        called_paths = [c.args[0] for c in device_fs.delete_tree.await_args_list]
        assert any("my-skill" in p for p in called_paths)
        mock_rmtree.assert_not_called()
        mock_unlink.assert_not_called()

    @pytest.mark.asyncio
    async def test_deactivate_checks_remote_runtime_when_host_path_is_missing(self):
        device_fs = MagicMock()
        device_fs.delete_tree = AsyncMock(return_value=True)
        svc = _make_service(device_fs)

        with patch("pathlib.Path.exists", return_value=False), patch(
            "pathlib.Path.is_symlink", return_value=False
        ):
            ok = await svc.deactivate_skill("missing", bolt_id="bolt-1", user_id="u-1")

        assert ok is True
        device_fs.delete_tree.assert_awaited_once_with("/oss/skills/missing")
