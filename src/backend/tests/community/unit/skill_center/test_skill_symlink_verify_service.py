import pytest
from unittest.mock import MagicMock, AsyncMock, patch

from agentclaw.community.core.skill_center.services.skill_symlink_verify_service import (
    SkillSymlinkVerifyService,
)


def _dispatcher(*, list_dir=None, exists=True):
    """A DeviceFilesystemDispatcher double whose for_bot() yields a device-fs
    with the given list_dir / exists results."""
    device_fs = MagicMock()
    device_fs.list_dir = AsyncMock(return_value=list_dir)
    device_fs.exists = AsyncMock(return_value=exists)
    dispatcher = MagicMock()
    dispatcher.for_bot.return_value = device_fs
    return dispatcher


class TestSkillSymlinkVerifyService:
    @pytest.mark.asyncio
    async def test_verify_returns_pass_when_symlink_exists(self):
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

    @pytest.mark.asyncio
    async def test_verify_returns_fail_when_symlink_missing(self):
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
            svc = SkillSymlinkVerifyService(skill_repo=mock_repo, device_fs_dispatcher=dispatcher, bot_repo=MagicMock())
            report = await svc.verify_bot("bot-123", "staff", "owner-456", env="prod")

        assert report.total == 2
        assert report.passed == 1
        assert report.failed == 1
        assert report.failures[0].reason == "symlink_not_found"

    @pytest.mark.asyncio
    async def test_verify_db_error_returns_db_error_failure(self):
        mock_repo = MagicMock()
        mock_repo.get_active_skills_by_bot = MagicMock(side_effect=RuntimeError("db connection lost"))

        svc = SkillSymlinkVerifyService(skill_repo=mock_repo, device_fs_dispatcher=_dispatcher(), bot_repo=MagicMock())
        report = await svc.verify_bot("bot-123", "staff", "owner-456", env="prod")

        assert report.total == 0
        assert report.passed == 0
        assert report.failed == 0
        assert len(report.failures) == 1
        assert report.failures[0].reason == "db_error"

    @pytest.mark.asyncio
    async def test_verify_sandbox_id_missing_returns_sandbox_not_found_failure(self):
        mock_repo = MagicMock()
        mock_repo.get_active_skills_by_bot = MagicMock(return_value=[
            {"id": "1", "name": "skill-A", "git_path": "center://uuid-A", "link_name": "skill-A"},
        ])

        with patch("agentclaw.community.core.devices.services.device_info.get_device_info_by_bot_id", return_value=(None, None)):
            svc = SkillSymlinkVerifyService(skill_repo=mock_repo, device_fs_dispatcher=_dispatcher(), bot_repo=MagicMock())
            report = await svc.verify_bot("bot-123", "staff", "owner-456", env="prod")

        assert report.total == 1
        assert report.passed == 0
        assert report.failed == 1
        assert report.failures[0].reason == "sandbox_not_found"

    @pytest.mark.asyncio
    async def test_verify_center_uuid_nas_missing_returns_nas_target_missing(self):
        # symlink present but NAS target missing.
        dispatcher = _dispatcher(
            list_dir=[{"name": "skill-A", "is_link": True}],
            exists=False,
        )

        mock_repo = MagicMock()
        mock_repo.get_active_skills_by_bot = MagicMock(return_value=[
            {"id": "1", "name": "skill-A", "git_path": "center://uuid-A", "link_name": "skill-A"},
        ])

        with patch("agentclaw.community.core.devices.services.device_info.get_device_info_by_bot_id", return_value=("arca", "sandbox-123")):
            svc = SkillSymlinkVerifyService(skill_repo=mock_repo, device_fs_dispatcher=dispatcher, bot_repo=MagicMock())
            report = await svc.verify_bot("bot-123", "staff", "owner-456", env="prod")

        assert report.total == 1
        assert report.passed == 0
        assert report.failed == 1
        assert report.failures[0].reason == "nas_target_missing"


class TestVerifyProbesConcurrently:
    """NAS 探测每条一次设备往返，逐条 await 让验证耗时 = ``skill_count × round_trip``。"""

    @staticmethod
    def _service(*, symlinks, skills, exists):
        device_fs = MagicMock()
        device_fs.list_dir = AsyncMock(
            return_value=[{"name": name, "is_link": True} for name in symlinks]
        )
        device_fs.exists = exists
        dispatcher = MagicMock()
        dispatcher.for_bot.return_value = device_fs
        repo = MagicMock()
        repo.get_active_skills_by_bot = MagicMock(return_value=skills)
        return (
            SkillSymlinkVerifyService(
                skill_repo=repo, device_fs_dispatcher=dispatcher, bot_repo=MagicMock()
            ),
            device_fs,
        )

    @pytest.mark.asyncio
    async def test_nas_probes_overlap(self):
        import asyncio

        state = {"in_flight": 0, "peak": 0}

        async def _exists(path):
            state["in_flight"] += 1
            state["peak"] = max(state["peak"], state["in_flight"])
            try:
                await asyncio.sleep(0.01)
                return True
            finally:
                state["in_flight"] -= 1

        names = [f"skill-{i}" for i in range(6)]
        svc, _ = self._service(
            symlinks=names,
            skills=[
                {"id": str(i), "name": n, "git_path": f"center://uuid-{i}", "link_name": n}
                for i, n in enumerate(names)
            ],
            exists=_exists,
        )

        with patch(
            "agentclaw.community.core.devices.services.device_info.get_device_info_by_bot_id",
            return_value=("arca", "sandbox-123"),
        ):
            report = await svc.verify_bot("bot-123", "staff", "owner-456", env="prod")

        assert report.passed == 6
        assert state["peak"] > 1

    @pytest.mark.asyncio
    async def test_failure_order_still_follows_the_skill_list(self):
        """并发探测不改变报告顺序：failures 仍按 skill 顺序排列。"""
        skills = [
            # missing symlink → decided without a probe
            {"id": "1", "name": "a", "git_path": "center://uuid-a", "link_name": "a"},
            # linked, NAS target missing → decided by a probe
            {"id": "2", "name": "b", "git_path": "center://uuid-b", "link_name": "b"},
            {"id": "3", "name": "c", "git_path": "center://uuid-c", "link_name": "c"},
        ]

        async def _exists(path):
            return path.endswith("/uuid-c/current/c")

        svc, _ = self._service(symlinks=["b", "c"], skills=skills, exists=_exists)

        with patch(
            "agentclaw.community.core.devices.services.device_info.get_device_info_by_bot_id",
            return_value=("arca", "sandbox-123"),
        ):
            report = await svc.verify_bot("bot-123", "staff", "owner-456", env="prod")

        assert [(f.skill_name, f.reason) for f in report.failures] == [
            ("a", "symlink_not_found"),
            ("b", "nas_target_missing"),
        ]
        assert report.total == 3
        assert report.passed == 1
