"""端到端：force_sync → NAS 目录验证 + log 审计。"""
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch
import zipfile
import sys
from contextlib import contextmanager

pytestmark = pytest.mark.integration


class InMemorySqliteDB:
    """In-memory SQLite DB for integration testing — does not touch backend.db."""

    def __init__(self):
        import importlib.util
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        from sqlalchemy.ext.declarative import declarative_base

        # Create a working Base and patch it into agentclaw.community.core.base directly
        WorkingBase = declarative_base()
        import agentclaw.community.core.base as core_base

        self._orig_base = core_base.Base
        core_base.Base = WorkingBase

        # Now safe to import the model via the target module's file path
        import agentclaw.community.core.models.skill_center_sync_log as target_module
        file_path = target_module.__file__
        spec = importlib.util.spec_from_file_location("skill_center_sync_log", file_path)
        # Create a fresh module to avoid double-execution from the import above
        fresh_module = importlib.util.module_from_spec(spec)
        # Replace the sys.modules entry so subsequent imports get the fresh module
        sys.modules["skill_center_sync_log"] = fresh_module
        spec.loader.exec_module(fresh_module)
        SkillCenterSyncLog = fresh_module.SkillCenterSyncLog

        # Create engine and tables
        self._engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
        )
        self._session_factory = sessionmaker(bind=self._engine, autoflush=True)
        try:
            SkillCenterSyncLog.__table__.create(self._engine, checkfirst=True)
        except Exception:
            # Index already exists — safe to ignore for in-memory DB per fixture call
            pass

    def restore(self):
        """Restore the original Base after test completes."""
        import agentclaw.community.core.base as core_base
        core_base.Base = self._orig_base

    @contextmanager
    def session(self):
        db = self._session_factory()
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    # Unified repo uses orm_session(); mirror session() (commit on clean exit).
    orm_session = session


@pytest.fixture
def sync_e2e(tmp_path):
    """构造完整的 SkillCenterSyncService（mock SC client + 真 NAS）。"""
    from agentclaw.community.core.skill_center.services.skill_center_sync_service import SkillCenterSyncService
    from agentclaw.community.core.repository.implementations.skill_center.sync_log import SkillCenterSyncLogRepository

    # Capture original sys.modules state before any pollution
    original_modules = set(sys.modules.keys())

    db = InMemorySqliteDB()
    log_repo = SkillCenterSyncLogRepository(db)

    mock_client = MagicMock()
    # nas_sync_enabled resolves from sofa.sofa_config (B2). Pin the offline
    # (single-box) value False so the resolution is deterministic regardless of
    # which YAML overlay the test profile loaded.
    cfg = MagicMock()
    cfg.user_config = {"skill_center": {"nas_sync_enabled": False}}
    with patch("agentclaw.community.core.config.sofa.sofa_config", cfg):
        svc = SkillCenterSyncService(
            skill_center_client=mock_client,
            sync_log_repo=log_repo,
            skill_repo=MagicMock(),
            cache_plugin=MagicMock(),
            skill_scan_service_provider=lambda: MagicMock(),
            nas_root=str(tmp_path / "nas"),
        )
    yield svc, mock_client, tmp_path
    # Restore the original Base so other tests are not affected
    db.restore()
    # Cleanup: remove all keys added by this fixture
    for key in list(sys.modules.keys()):
        if key not in original_modules:
            if "skill_center_sync" in key or "skill_center" in key:
                del sys.modules[key]
            elif key.startswith("agentclaw.community.core.models.skill_center"):
                del sys.modules[key]


def _make_skill_zip(dst: Path) -> Path:
    """创建测试用 skill ZIP，返回 ZIP 路径。"""
    skill_dir = dst / "test-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("# Test Skill")
    (skill_dir / "main.py").write_text("print('hello')")
    zip_path = dst / "test-skill.zip"
    with zipfile.ZipFile(zip_path, "w") as z:
        z.write(skill_dir / "SKILL.md", arcname="SKILL.md")
        z.write(skill_dir / "main.py", arcname="main.py")
    return zip_path


def test_force_sync_downloads_and_extracts(sync_e2e):
    svc, mock_client, tmp_path = sync_e2e
    zip_path = _make_skill_zip(tmp_path)
    mock_client.list_versions.return_value = [{"versionNumber": "1.0.0"}]
    mock_client.get_download_url.return_value = {
        "success": True,
        "data": {"intranetDownloadUrl": f"file://{zip_path}"},
    }

    result = svc.force_sync("e2e-uuid", "dev", version="1.0.0")

    assert result is True
    skill_dir = tmp_path / "nas" / "e2e-uuid" / "1.0.0"
    assert skill_dir.exists()
    assert (skill_dir / "SKILL.md").exists()
    assert (skill_dir / "main.py").exists()
    log = svc._log_repo.find_latest("e2e-uuid", "dev")
    assert log["status"] == "success"


def test_force_sync_skips_existing(sync_e2e):
    svc, mock_client, tmp_path = sync_e2e
    mock_client.list_versions.return_value = [{"versionNumber": "1.0.0"}]
    mock_client.get_download_url.return_value = {
        "success": True,
        "data": {"intranetDownloadUrl": f"file:///nonexistent.zip"},
    }

    # 预先写入 success log AND 创建 skill_dir（模拟已同步状态）
    svc._log_repo.create({
        "skill_uuid": "e2e-skip",
        "version": "1.0.0",
        "env": "dev",
        "status": "success",
    })
    skill_dir = tmp_path / "nas" / "e2e-skip" / "1.0.0"
    skill_dir.mkdir(parents=True)

    result = svc.force_sync("e2e-skip", "dev", version="1.0.0")
    assert result is True
    mock_client.get_download_url.assert_not_called()


def test_nas_sync_skipped_when_flag_disabled(sync_e2e):
    """Single box 下 nas_sync_enabled 默认关闭，_sync_to_nas_all 应被显式 skip。"""
    svc, _, _ = sync_e2e
    assert svc._nas_sync_enabled is False
    result = svc._sync_to_nas_all()
    assert result.get("skipped") is True
    assert result.get("reason") == "nas_sync_enabled=False"


def test_cleanup_stale_preserves_current_and_recent(sync_e2e):
    svc, _, tmp_path = sync_e2e
    nas = tmp_path / "nas" / "e2e-cleanup"
    nas.mkdir(parents=True)
    (nas / "v1").mkdir()
    (nas / "v2").mkdir()
    (nas / "v3").mkdir()
    (nas / "current").symlink_to("v2")

    removed = svc.cleanup_stale("e2e-cleanup", keep=1)

    assert removed == 1
    assert not (nas / "v1").exists()
    assert (nas / "v2").exists()
    assert (nas / "v3").exists()
