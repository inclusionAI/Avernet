"""E2E integration tests for verify_symlinks endpoint."""
import pytest
from unittest.mock import MagicMock, AsyncMock, patch

from agentclaw.community.core.skill_center.services.skill_symlink_verify_service import (
    SkillSymlinkVerifyService,
)


def _dispatcher(*, list_dir=None, exists=True):
    """DeviceFilesystemDispatcher double → for_bot() yields a device-fs."""
    device_fs = MagicMock()
    device_fs.list_dir = AsyncMock(return_value=list_dir)
    device_fs.exists = AsyncMock(return_value=exists)
    dispatcher = MagicMock()
    dispatcher.for_bot.return_value = device_fs
    return dispatcher


@pytest.mark.asyncio
class TestVerifySymlinksE2E:
    """End-to-end tests for symlink verification with mocked DB + device-fs."""

    async def test_verify_all_pass(self):
        """全绿场景：symlink 存在 + NAS 目标存在"""
        dispatcher = _dispatcher(
            list_dir=[
                {"name": "skill-A", "is_link": True},
                {"name": "skill-B", "is_link": True},
            ],
            exists=True,
        )

        mock_repo = MagicMock()
        mock_repo.get_active_skills_by_bot = MagicMock(return_value=[
            {"id": "1", "name": "skill-A", "git_path": "center://uuid-A", "link_name": "skill-A"},
            {"id": "2", "name": "skill-B", "git_path": "git://biz/skill-B", "link_name": "skill-B"},
        ])

        with patch("agentclaw.community.core.devices.services.device_info.get_device_info_by_bot_id", return_value=("arca", "sandbox-123")):
            svc = SkillSymlinkVerifyService(
                skill_repo=mock_repo,
                device_fs_dispatcher=dispatcher, bot_repo=MagicMock())

            report = await svc.verify_bot("bot-123", "staff", "owner-456", env="prod")

        assert report.total == 2
        assert report.passed == 2
        assert report.failed == 0
        assert len(report.failures) == 0

    async def test_verify_symlink_missing_reports_fail(self):
        """symlink 不存在时报告 symlink_not_found"""
        dispatcher = _dispatcher(
            list_dir=[{"name": "skill-A", "is_link": True}],
            exists=True,
        )

        mock_repo = MagicMock()
        mock_repo.get_active_skills_by_bot = MagicMock(return_value=[
            {"id": "1", "name": "skill-A", "git_path": "center://uuid-A", "link_name": "skill-A"},
            {"id": "2", "name": "skill-C", "git_path": "center://uuid-C", "link_name": "skill-C"},
        ])

        with patch("agentclaw.community.core.devices.services.device_info.get_device_info_by_bot_id", return_value=("arca", "sandbox-123")):
            svc = SkillSymlinkVerifyService(
                skill_repo=mock_repo,
                device_fs_dispatcher=dispatcher, bot_repo=MagicMock())

            report = await svc.verify_bot("bot-123", "staff", "owner-456", env="prod")

        assert report.total == 2
        assert report.passed == 1
        assert report.failed == 1
        assert report.failures[0].reason == "symlink_not_found"
        assert report.failures[0].skill_id == "2"

    async def test_verify_center_uuid_nas_missing_reports_fail(self):
        """center:// 指向 UUID 但 NAS 目录不存在"""
        dispatcher = _dispatcher(
            list_dir=[{"name": "skill-A", "is_link": True}],
            exists=False,
        )

        mock_repo = MagicMock()
        mock_repo.get_active_skills_by_bot = MagicMock(return_value=[
            {"id": "1", "name": "skill-A", "git_path": "center://uuid-A", "link_name": "skill-A"},
        ])

        with patch("agentclaw.community.core.devices.services.device_info.get_device_info_by_bot_id", return_value=("arca", "sandbox-123")):
            svc = SkillSymlinkVerifyService(
                skill_repo=mock_repo,
                device_fs_dispatcher=dispatcher, bot_repo=MagicMock())

            report = await svc.verify_bot("bot-123", "staff", "owner-456", env="prod")

        assert report.total == 1
        assert report.passed == 0
        assert report.failed == 1
        assert report.failures[0].reason == "nas_target_missing"
        assert report.failures[0].skill_id == "1"
