"""Unit tests for SkillCenterSyncService.scan_after_sync()."""
from unittest.mock import MagicMock, patch


def _make_sync_service(nas_root: str, skill_repo=None, scan_service=None):
    from agentclaw.community.core.skill_center.services.skill_center_sync_service import SkillCenterSyncService
    return SkillCenterSyncService(
        skill_center_client=MagicMock(),
        sync_log_repo=MagicMock(),
        nas_root=nas_root,
        skill_repo=skill_repo if skill_repo is not None else MagicMock(),
        cache_plugin=MagicMock(),
        skill_scan_service_provider=lambda: scan_service if scan_service is not None else MagicMock(),
    )


class TestScanAfterSync:
    def test_scan_after_sync_calls_scan_skill_with_current_dir(self, tmp_path):
        """scan_after_sync 应扫描 NAS 下 <uuid>/current/ 目录。"""
        skill_dir = tmp_path / "uuid-abc" / "1.0.0"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text("# Test")
        current = tmp_path / "uuid-abc" / "current"
        current.symlink_to("1.0.0")

        mock_skill_repo = MagicMock()
        mock_skill_repo.get_by_uuid.return_value = {"id": "skill-id-1", "name": "test"}

        mock_scan_service = MagicMock()
        mock_scan_result = MagicMock()
        mock_scan_result.mcp_dependencies = []
        mock_scan_result.risk_tags = []
        mock_scan_service.scan_skill.return_value = mock_scan_result
        mock_scan_service.start.return_value = True

        svc = _make_sync_service(
            str(tmp_path),
            skill_repo=mock_skill_repo,
            scan_service=mock_scan_service,
        )
        svc.scan_after_sync("uuid-abc", "dev")

        mock_scan_service.scan_skill.assert_called_once_with(str(current.resolve()))

    def test_scan_after_sync_skips_when_current_missing(self, tmp_path):
        """current symlink 不存在时应跳过，不抛异常。"""
        (tmp_path / "uuid-no-current").mkdir()
        mock_scan_service = MagicMock()
        svc = _make_sync_service(str(tmp_path), scan_service=mock_scan_service)

        svc.scan_after_sync("uuid-no-current", "dev")

        mock_scan_service.scan_skill.assert_not_called()

    def test_scan_after_sync_updates_db_with_results(self, tmp_path):
        """扫描成功时应更新 skill 的 mcp_dependencies 和 risk_tags。"""
        skill_dir = tmp_path / "uuid-xyz" / "2.0.0"
        skill_dir.mkdir(parents=True)
        (tmp_path / "uuid-xyz" / "current").symlink_to("2.0.0")

        mock_skill_repo = MagicMock()
        mock_skill_repo.get_by_uuid.return_value = {"id": "99", "name": "xyz"}
        mock_skill_repo.update_mcp_dependencies.return_value = {"id": "99"}
        mock_skill_repo.update_risk_tags.return_value = {"id": "99"}

        mock_scan_result = MagicMock()
        mock_dep = MagicMock()
        mock_dep.code = "odps"
        mock_dep.name = "ODPS"
        mock_dep.url = "http://odps"
        mock_scan_result.mcp_dependencies = [mock_dep]
        mock_scan_result.risk_tags = []

        mock_scan_service = MagicMock()
        mock_scan_service.scan_skill.return_value = mock_scan_result
        mock_scan_service.start.return_value = True
        mock_scan_service._filter_mcp_dependencies.return_value = [
            {"code": "odps", "name": "ODPS", "url": "http://odps"}
        ]

        svc = _make_sync_service(
            str(tmp_path),
            skill_repo=mock_skill_repo,
            scan_service=mock_scan_service,
        )
        svc.scan_after_sync("uuid-xyz", "dev")

        mock_skill_repo.get_by_uuid.assert_called_once_with("uuid-xyz", "dev")
        mock_skill_repo.update_mcp_dependencies.assert_called_once_with(
            "99", [{"code": "odps", "name": "ODPS", "url": "http://odps"}]
        )
        mock_skill_repo.update_risk_tags.assert_called_once_with("99", [])

    def test_scan_after_sync_does_not_raise_on_scan_failure(self, tmp_path):
        """SDK 扫描抛异常时不向外传播。"""
        skill_dir = tmp_path / "uuid-err" / "1.0.0"
        skill_dir.mkdir(parents=True)
        (tmp_path / "uuid-err" / "current").symlink_to("1.0.0")

        mock_scan_service = MagicMock()
        mock_scan_service.scan_skill.side_effect = RuntimeError("SDK error")
        mock_scan_service.start.return_value = True

        svc = _make_sync_service(str(tmp_path), scan_service=mock_scan_service)
        # 不应抛异常
        svc.scan_after_sync("uuid-err", "dev")


class TestForceSyncTriggersScan:
    def test_force_sync_calls_scan_after_sync_on_success(self, tmp_path):
        """force_sync 成功后应自动调用 scan_after_sync。"""
        import zipfile

        zip_path = tmp_path / "skill.zip"
        with zipfile.ZipFile(zip_path, "w") as z:
            z.writestr("SKILL.md", "# Test")

        mock_client = MagicMock()
        mock_client.list_versions.return_value = [{"versionNumber": "1.0.0"}]
        mock_client.get_download_url.return_value = {
            "success": True,
            "data": {"intranetDownloadUrl": f"file://{zip_path}"},
        }

        mock_log_repo = MagicMock()
        mock_log_repo.find_latest.return_value = None

        from agentclaw.community.core.skill_center.services.skill_center_sync_service import SkillCenterSyncService
        svc = SkillCenterSyncService(
            skill_center_client=mock_client,
            sync_log_repo=mock_log_repo,
            skill_repo=MagicMock(),
            cache_plugin=MagicMock(),
            skill_scan_service_provider=lambda: MagicMock(),
            nas_root=str(tmp_path),
        )

        with patch.object(svc, "scan_after_sync") as mock_scan:
            svc.force_sync("uuid-trigger", "dev", version="1.0.0")

        mock_scan.assert_called_once_with("uuid-trigger", "dev")

    def test_force_sync_skips_scan_when_already_synced(self, tmp_path):
        """force_sync skip（已同步）时不应调用 scan_after_sync。"""
        skill_dir = tmp_path / "uuid-skip" / "1.0.0"
        skill_dir.mkdir(parents=True)

        mock_client = MagicMock()
        mock_client.list_versions.return_value = [{"versionNumber": "1.0.0"}]

        mock_log_repo = MagicMock()
        mock_log_repo.find_latest.return_value = {"status": "success", "version": "1.0.0"}

        from agentclaw.community.core.skill_center.services.skill_center_sync_service import SkillCenterSyncService
        svc = SkillCenterSyncService(
            skill_center_client=mock_client,
            sync_log_repo=mock_log_repo,
            skill_repo=MagicMock(),
            cache_plugin=MagicMock(),
            skill_scan_service_provider=lambda: MagicMock(),
            nas_root=str(tmp_path),
        )

        with patch.object(svc, "scan_after_sync") as mock_scan:
            svc.force_sync("uuid-skip", "dev", version="1.0.0")

        mock_scan.assert_not_called()
