"""Tests for SkillBatchSyncService — scan, dedup, pack, publish, poll."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from agentclaw.community.core.skill_center.services.skill_batch_sync_service import (
    BatchSyncReport,
    SkillBatchSyncService,
    SyncResult,
    generate_report,
)
from agentclaw.community.plugins.local.oss_storage import MockObjectStoragePlugin


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_skill_dir(base: Path, rel_path: str, frontmatter: str = "") -> Path:
    d = base / rel_path
    d.mkdir(parents=True, exist_ok=True)
    content = frontmatter or f"---\nname: {d.name}\ndescription: test\nversion: '1.0.0'\n---\n# {d.name}\n"
    (d / "SKILL.md").write_text(content, encoding="utf-8")
    return d


@pytest.fixture
def skills_root(tmp_path: Path) -> Path:
    _make_skill_dir(tmp_path, "business/ha-duty/alarm-inspector")
    _make_skill_dir(tmp_path, "tools/code-review")
    _make_skill_dir(
        tmp_path,
        "business/ha-publish/release-banche",
        "---\nname: release-banche\ndescription: ha version\nversion: '3.0.0'\nauthor: tester\n---\n# release-banche\n",
    )
    _make_skill_dir(
        tmp_path,
        "business/other/release-banche",
        "---\nname: release-banche\ndescription: other\nversion: '1.0.0'\n---\n# release-banche\n",
    )
    return tmp_path


@pytest.fixture
def mock_sc_client() -> MagicMock:
    client = MagicMock()
    client.upload_and_publish.return_value = {"success": True, "data": {"skillCode": "test"}}
    client.query_publish_status.return_value = {
        "success": True,
        "data": {"status": "PUBLISHED"},
    }
    return client


@pytest.fixture
def mock_oss() -> MagicMock:
    oss = MagicMock()
    oss.sign_url.return_value = "https://oss.example.com/signed"
    return oss


# ---------------------------------------------------------------------------
# Scan & Dedup
# ---------------------------------------------------------------------------

class TestScanSkills:
    def test_finds_all_skill_dirs(self, skills_root: Path, mock_sc_client: MagicMock):
        svc = SkillBatchSyncService(skill_center_client=mock_sc_client, oss=MockObjectStoragePlugin(), skill_repo=MagicMock())
        dirs = svc._scan_skills(skills_root)
        codes = {d.name for d in dirs}
        assert "alarm-inspector" in codes
        assert "code-review" in codes
        assert "release-banche" in codes

    def test_dedup_keeps_preferred(self, skills_root: Path, mock_sc_client: MagicMock):
        svc = SkillBatchSyncService(skill_center_client=mock_sc_client, oss=MockObjectStoragePlugin(), skill_repo=MagicMock())
        dirs = svc._scan_skills(skills_root)
        rb = [d for d in dirs if d.name == "release-banche"]
        assert len(rb) == 1
        assert "ha-publish" in str(rb[0])

    def test_scan_empty_dir(self, tmp_path: Path, mock_sc_client: MagicMock):
        svc = SkillBatchSyncService(skill_center_client=mock_sc_client, oss=MockObjectStoragePlugin(), skill_repo=MagicMock())
        dirs = svc._scan_skills(tmp_path)
        assert dirs == []


# ---------------------------------------------------------------------------
# Build Metadata
# ---------------------------------------------------------------------------

class TestBuildMetadata:
    def test_no_path_derived_tags(self, skills_root: Path):
        skill_path = skills_root / "business" / "ha-duty" / "alarm-inspector"
        parsed = {"name": "alarm-inspector", "description": "test", "version": "1.0.0", "author": "",
                  "category": "general"}
        meta = SkillBatchSyncService._build_metadata(parsed, skill_path, skills_root)
        assert "tags" not in meta
        assert meta["skillCode"] == "alarm-inspector"

    def test_default_author(self, skills_root: Path):
        skill_path = skills_root / "tools" / "code-review"
        parsed = {"name": "code-review", "description": "test", "version": "2.0.0", "author": "", "category": "general"}
        meta = SkillBatchSyncService._build_metadata(parsed, skill_path, skills_root)
        assert "creatorWorkNo" not in meta
        assert "creatorNickName" not in meta
        assert meta["_author_anomaly"] is True

    def test_explicit_author(self, skills_root: Path):
        skill_path = skills_root / "business" / "ha-publish" / "release-banche"
        parsed = {"name": "release-banche", "description": "test", "version": "3.0.0", "author": "tester",
                  "category": "general"}
        meta = SkillBatchSyncService._build_metadata(parsed, skill_path, skills_root)
        assert "creatorWorkNo" not in meta
        assert "creatorNickName" not in meta
        assert meta["_author_anomaly"] is True

    def test_workno_author(self, skills_root: Path):
        skill_path = skills_root / "tools" / "code-review"
        parsed = {"name": "code-review", "description": "test", "version": "1.0.0", "author": "348651",
                  "category": "general"}
        meta = SkillBatchSyncService._build_metadata(parsed, skill_path, skills_root)
        assert meta["creatorNickName"] == "348651"
        assert meta["creatorWorkNo"] == "348651"
        assert meta["_author_anomaly"] is False


# ---------------------------------------------------------------------------
# Pack (local mock — no OSS)
# ---------------------------------------------------------------------------

class TestPackAndUpload:
    def test_oss_upload(self, tmp_path: Path, mock_sc_client: MagicMock):
        (tmp_path / "SKILL.md").write_text("# test", encoding="utf-8")
        mock_oss = MagicMock()
        mock_oss.sign_url.return_value = "https://oss.example.com/signed"
        svc = SkillBatchSyncService(skill_center_client=mock_sc_client, oss=mock_oss, skill_repo=MagicMock())
        url = svc._pack_and_upload(tmp_path, "my-skill", "1.0.0")
        assert url == "https://oss.example.com/signed"
        mock_oss.put_object.assert_called_once()

    def test_oss_upload_excludes_child_dirs(self, tmp_path: Path, mock_sc_client: MagicMock):
        """子目录（含 SKILL.md）的文件不应出现在 ZIP 里。"""
        import zipfile as zf

        (tmp_path / "SKILL.md").write_text("# parent", encoding="utf-8")
        (tmp_path / "main.py").write_text("print(1)", encoding="utf-8")
        child = tmp_path / "sub-skill"
        child.mkdir()
        (child / "SKILL.md").write_text("# child", encoding="utf-8")
        (child / "child.py").write_text("print(2)", encoding="utf-8")

        captured_bytes: list[bytes] = []
        mock_oss = MagicMock()
        mock_oss.put_object.side_effect = lambda path, data: captured_bytes.append(data)
        mock_oss.sign_url.return_value = "https://oss.example.com/signed"

        svc = SkillBatchSyncService(skill_center_client=mock_sc_client, oss=mock_oss, skill_repo=MagicMock())
        svc._pack_and_upload(tmp_path, "parent-skill", "1.0.0", exclude_dirs={child})

        assert len(captured_bytes) == 1
        import io
        with zf.ZipFile(io.BytesIO(captured_bytes[0]), "r") as z:
            names = z.namelist()
        assert "SKILL.md" in names
        assert "main.py" in names
        assert "sub-skill/SKILL.md" not in names
        assert "sub-skill/child.py" not in names


# ---------------------------------------------------------------------------
# Poll
# ---------------------------------------------------------------------------

class TestBatchPoll:
    def test_immediate_success(self, mock_sc_client: MagicMock):
        svc = SkillBatchSyncService(skill_center_client=mock_sc_client, oss=MockObjectStoragePlugin(), skill_repo=MagicMock())
        results = svc._batch_poll([("test-skill", False)])
        assert len(results) == 1
        assert results[0].success is True
        assert results[0].status == "PUBLISHED"

    def test_terminal_fail(self, mock_sc_client: MagicMock):
        mock_sc_client.query_publish_status.return_value = {
            "success": True,
            "data": {"status": "PUBLISH_FAILED"},
        }
        svc = SkillBatchSyncService(skill_center_client=mock_sc_client, oss=MockObjectStoragePlugin(), skill_repo=MagicMock())
        results = svc._batch_poll([("test-skill", False)])
        assert len(results) == 1
        assert results[0].success is False
        assert results[0].status == "PUBLISH_FAILED"

    @patch("agentclaw.community.core.skill_center.services.skill_batch_sync_service.time.sleep")
    def test_polls_then_succeeds(self, mock_sleep, mock_sc_client: MagicMock):
        mock_sc_client.query_publish_status.side_effect = [
            {"success": True, "data": {"status": "SECURITY_SCANNING"}},
            {"success": True, "data": {"status": "PRE_RELEASING"}},
            {"success": True, "data": {"status": "PUBLISHED"}},
        ]
        svc = SkillBatchSyncService(skill_center_client=mock_sc_client, oss=MockObjectStoragePlugin(), skill_repo=MagicMock())
        results = svc._batch_poll([("test-skill", True)])
        assert len(results) == 1
        assert results[0].success is True
        assert results[0].author_anomaly is True
        assert mock_sleep.call_count == 2

    @patch("agentclaw.community.core.skill_center.services.skill_batch_sync_service.time.sleep")
    def test_polls_timeout(self, mock_sleep, mock_sc_client: MagicMock):
        mock_sc_client.query_publish_status.return_value = {
            "success": True,
            "data": {"status": "SECURITY_SCANNING"},
        }
        svc = SkillBatchSyncService(skill_center_client=mock_sc_client, oss=MockObjectStoragePlugin(), skill_repo=MagicMock())
        results = svc._batch_poll([("test-skill", False)])
        assert len(results) == 1
        assert results[0].success is False
        assert "pending" in results[0].error.lower()


# ---------------------------------------------------------------------------
# Full run
# ---------------------------------------------------------------------------

class TestRun:
    def test_full_run_all_new(self, skills_root: Path, mock_sc_client: MagicMock, mock_oss: MagicMock):
        """幂等检查查不到已发布版本 → 全部走 publish + poll。"""
        mock_sc_client.query_publish_status.side_effect = [
            # 幂等检查阶段：3 个 skill 都查不到（异常 = 没发布过）
            Exception("not found"), Exception("not found"), Exception("not found"),
            # Phase B 轮询阶段：3 个 skill 都立即 PUBLISHED
            {"success": True, "data": {"status": "PUBLISHED"}},
            {"success": True, "data": {"status": "PUBLISHED"}},
            {"success": True, "data": {"status": "PUBLISHED"}},
        ]
        svc = SkillBatchSyncService(skill_center_client=mock_sc_client, oss=mock_oss, skill_repo=MagicMock())
        report = svc.run(skills_root)
        assert report.total == 3
        assert report.success == 3
        assert report.failed == 0

    def test_full_run_idempotency_skip(self, skills_root: Path, mock_sc_client: MagicMock, mock_oss: MagicMock):
        """幂等检查发现已发布且版本一致 → 全部 skip。"""
        mock_sc_client.query_publish_status.return_value = {
            "success": True,
            "data": {"status": "PUBLISHED", "versionNumber": "1.0.0"},
        }
        svc = SkillBatchSyncService(skill_center_client=mock_sc_client, oss=mock_oss, skill_repo=MagicMock())
        report = svc.run(skills_root)
        assert report.total == 3
        assert report.skipped >= 2  # alarm-inspector 和 code-review 版本都是 1.0.0
        assert report.failed == 0

    def test_skill_codes_filter(self, skills_root: Path, mock_sc_client: MagicMock, mock_oss: MagicMock):
        """skill_codes 参数只同步指定的 skill。"""
        svc = SkillBatchSyncService(skill_center_client=mock_sc_client, oss=mock_oss, skill_repo=MagicMock())
        report = svc.run(skills_root, skill_codes=["alarm-inspector"])
        assert report.total == 1

    def test_nonexistent_dir_raises(self, tmp_path: Path, mock_sc_client: MagicMock):
        svc = SkillBatchSyncService(skill_center_client=mock_sc_client, oss=MockObjectStoragePlugin(), skill_repo=MagicMock())
        with pytest.raises(FileNotFoundError):
            svc.run(tmp_path / "nope")


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

class TestGenerateReport:
    def test_report_with_failures(self, tmp_path: Path):
        report = BatchSyncReport(total=2, success=1, failed=1, results=[
            SyncResult(skill_code="good", success=True, status="PUBLISHED"),
            SyncResult(skill_code="bad", success=False, status="PUBLISH_FAILED", error="some error"),
        ])
        out = generate_report(report, tmp_path / "report.md")
        content = out.read_text()
        assert "bad" in content
        assert "PUBLISH_FAILED" in content
        assert "good" not in content.split("## Failed")[1] if "## Failed" in content else True

    def test_report_with_anomalies(self, tmp_path: Path):
        report = BatchSyncReport(total=1, success=1, results=[
            SyncResult(skill_code="no-author", success=True, status="PUBLISHED", author_anomaly=True),
        ])
        out = generate_report(report, tmp_path / "report.md")
        content = out.read_text()
        assert "no-author" in content
        assert "Author Anomalies" in content
