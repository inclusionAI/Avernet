# tests/unit/skill_center/test_skill_center_sync_service.py
from unittest.mock import MagicMock
import pytest
import zipfile

pytestmark = pytest.mark.unit


def _make_zip(tmp_dir):
    """创建测试用 skill ZIP。"""
    skill_dir = tmp_dir / "test-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("# Test")
    (skill_dir / "main.py").write_text("print('hi')")
    zip_path = tmp_dir / "test.zip"
    with zipfile.ZipFile(zip_path, "w") as z:
        z.write(skill_dir / "SKILL.md", arcname="SKILL.md")
        z.write(skill_dir / "main.py", arcname="main.py")
    return zip_path


def test_force_sync_downloads_and_extracts(tmp_path):
    """force_sync 应下载 ZIP 并解压到 NAS 目录。"""
    from agentclaw.community.core.skill_center.services.skill_center_sync_service import SkillCenterSyncService
    mock_client = MagicMock()
    mock_client.list_versions.return_value = [{"versionNumber": "1.0.0"}]
    zip_path = _make_zip(tmp_path)
    mock_client.get_download_url.return_value = {
        "success": True,
        "data": {"intranetDownloadUrl": f"file://{zip_path}"},
    }

    mock_repo = MagicMock()
    mock_repo.find_latest.return_value = None

    svc = SkillCenterSyncService(
        skill_center_client=mock_client,
        sync_log_repo=mock_repo,
        skill_repo=MagicMock(),
        cache_plugin=MagicMock(),
        skill_scan_service_provider=lambda: MagicMock(),
        nas_root=str(tmp_path / "nas"),
    )

    result = svc.force_sync("test-uuid", "dev", version="1.0.0")

    assert result is True
    skill_dir = tmp_path / "nas" / "test-uuid" / "1.0.0"
    assert skill_dir.exists()
    assert (skill_dir / "SKILL.md").exists()
    mock_repo.create.assert_called_once()
    mock_repo.mark_success.assert_called_once()


def test_force_sync_skips_if_already_synced(tmp_path):
    """已同步且版本相同则跳过下载。"""
    from agentclaw.community.core.skill_center.services.skill_center_sync_service import SkillCenterSyncService
    mock_client = MagicMock()
    mock_repo = MagicMock()
    mock_repo.find_latest.return_value = {"status": "success", "version": "1.0.0"}

    # 预先创建目录模拟已同步
    nas = tmp_path / "nas" / "test-skip"
    nas.mkdir(parents=True)

    svc = SkillCenterSyncService(
        skill_center_client=mock_client,
        sync_log_repo=mock_repo,
        skill_repo=MagicMock(),
        cache_plugin=MagicMock(),
        skill_scan_service_provider=lambda: MagicMock(),
        nas_root=str(tmp_path / "nas"),
    )

    result = svc.force_sync("test-skip", "dev", version="1.0.0")
    assert result is True
    mock_client.get_download_url.assert_not_called()


def test_force_sync_raises_on_download_failure(tmp_path):
    """SC 返回失败时应抛 SkillSyncError。"""
    from agentclaw.community.core.skill_center.services.skill_center_sync_service import SkillCenterSyncService, SkillSyncError
    mock_client = MagicMock()
    mock_client.list_versions.return_value = [{"versionNumber": "1.0.0"}]
    mock_client.get_download_url.return_value = {"success": False, "error": "not found"}

    mock_repo = MagicMock()
    mock_repo.find_latest.return_value = None

    svc = SkillCenterSyncService(
        skill_center_client=mock_client,
        sync_log_repo=mock_repo,
        skill_repo=MagicMock(),
        cache_plugin=MagicMock(),
        skill_scan_service_provider=lambda: MagicMock(),
        nas_root=str(tmp_path / "nas"),
    )

    with pytest.raises(SkillSyncError):
        svc.force_sync("test-uuid", "dev", version="1.0.0")


def test_cleanup_stale_preserves_current_and_recent(tmp_path):
    """cleanup_stale 应保留 current + 最近 keep 个版本。"""
    from agentclaw.community.core.skill_center.services.skill_center_sync_service import SkillCenterSyncService
    nas = tmp_path / "uuid-clean"
    nas.mkdir(parents=True)
    (nas / "v1").mkdir()
    (nas / "v2").mkdir()
    (nas / "v3").mkdir()
    (nas / "current").symlink_to("v2")

    mock_repo = MagicMock()
    svc = SkillCenterSyncService(
        skill_center_client=MagicMock(),
        sync_log_repo=mock_repo,
        skill_repo=MagicMock(),
        cache_plugin=MagicMock(),
        skill_scan_service_provider=lambda: MagicMock(),
        nas_root=str(tmp_path),
    )

    removed = svc.cleanup_stale("uuid-clean", keep=1)

    assert removed == 1
    assert not (nas / "v1").exists()
    assert (nas / "v2").exists()
    assert (nas / "v3").exists()


def test_is_synced_returns_true_when_log_success(tmp_path):
    """is_synced 应在 log success 时返回 True。"""
    from agentclaw.community.core.skill_center.services.skill_center_sync_service import SkillCenterSyncService
    mock_client = MagicMock()
    mock_repo = MagicMock()
    mock_repo.find_latest.return_value = {"status": "success", "version": "1.0.0"}

    nas = tmp_path / "nas" / "uuid-synced" / "1.0.0"
    nas.mkdir(parents=True)

    svc = SkillCenterSyncService(
        skill_center_client=mock_client,
        sync_log_repo=mock_repo,
        skill_repo=MagicMock(),
        cache_plugin=MagicMock(),
        skill_scan_service_provider=lambda: MagicMock(),
        nas_root=str(tmp_path / "nas"),
    )

    assert svc.is_synced("uuid-synced", "dev") is True


def test_is_synced_returns_false_when_no_record(tmp_path):
    """is_synced 应在无记录时返回 False。"""
    from agentclaw.community.core.skill_center.services.skill_center_sync_service import SkillCenterSyncService
    mock_client = MagicMock()
    mock_repo = MagicMock()
    mock_repo.find_latest.return_value = None

    svc = SkillCenterSyncService(
        skill_center_client=mock_client,
        sync_log_repo=mock_repo,
        skill_repo=MagicMock(),
        cache_plugin=MagicMock(),
        skill_scan_service_provider=lambda: MagicMock(),
        nas_root=str(tmp_path / "nas"),
    )

    assert svc.is_synced("uuid-no-record", "dev") is False


# =============================================================================
# 回归保护：bootstrap / periodic sync 必须用 list_published_center_skills，
# 不能用 list_skills（后者默认 bolt_id='default' 会漏 bot_id 非 default 的 skill）
# =============================================================================

def _make_sync_svc_for_bootstrap(tmp_path, mock_skill_repo):
    from agentclaw.community.core.skill_center.services.skill_center_sync_service import SkillCenterSyncService
    return SkillCenterSyncService(
        skill_center_client=MagicMock(),
        sync_log_repo=MagicMock(),
        skill_repo=mock_skill_repo,
        cache_plugin=MagicMock(),
        skill_scan_service_provider=lambda: MagicMock(),
        nas_root=str(tmp_path / "nas"),
    )


@pytest.mark.asyncio
async def test_bootstrap_uses_list_published_center_skills_not_list_skills(tmp_path):
    """bootstrap 必须调 list_published_center_skills；不能用 list_skills（bolt_id 过滤 bug）。"""
    mock_repo = MagicMock()
    mock_repo.list_published_center_skills.return_value = []

    svc = _make_sync_svc_for_bootstrap(tmp_path, mock_repo)
    await svc._bootstrap_inner(env="pre")

    mock_repo.list_published_center_skills.assert_called_once()
    mock_repo.list_skills.assert_not_called()


@pytest.mark.asyncio
async def test_bootstrap_picks_skills_with_non_default_bolt_id(tmp_path):
    """bolt_id 是具体 bot_xxx 的 center:// skill 也必须被 bootstrap 同步（bug 回归）。"""
    mock_repo = MagicMock()
    # 模拟 list_published_center_skills 已经只返回 PUBLISHED + center://，含各种 bolt_id
    mock_repo.list_published_center_skills.return_value = [
        {"skill_uuid": "u-default", "git_path": "center://u-default", "status": "PUBLISHED", "bolt_id": "default"},
        {"skill_uuid": "u-bot-xxx", "git_path": "center://u-bot-xxx", "status": "PUBLISHED", "bolt_id": "bot_xxx"},
        {"skill_uuid": "u-null", "git_path": "center://u-null", "status": "PUBLISHED", "bolt_id": None},
    ]

    svc = _make_sync_svc_for_bootstrap(tmp_path, mock_repo)
    # mock force_sync 避免真去拉 SC
    svc.force_sync = MagicMock(return_value=True)

    result = await svc._bootstrap_inner(env="pre")

    # 3 个 skill 都应被同步（含 bolt_id='bot_xxx' 这种）
    assert svc.force_sync.call_count == 3
    called_uuids = {call.kwargs.get("skill_uuid") or call.args[0] for call in svc.force_sync.call_args_list}
    assert called_uuids == {"u-default", "u-bot-xxx", "u-null"}
    assert result["synced"] == 3
    assert result["failed"] == 0


def test_periodic_sync_uses_list_published_center_skills_not_list_skills(tmp_path):
    """_sync_all_center_skills 必须调 list_published_center_skills，不能用 list_skills。"""
    mock_repo = MagicMock()
    mock_repo.list_published_center_skills.return_value = []

    svc = _make_sync_svc_for_bootstrap(tmp_path, mock_repo)
    svc._sync_all_center_skills()

    mock_repo.list_published_center_skills.assert_called_once()
    mock_repo.list_skills.assert_not_called()
