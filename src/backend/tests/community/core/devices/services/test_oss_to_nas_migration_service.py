"""Tests for OssToNasMigrationService."""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from agentclaw.community.core.devices.services.oss_to_nas_migration_service import (
    OssToNasMigrationService,
)
from agentclaw.community.di.config import OssToNasConfig

LOG_NAME = "start"


def _make_service(oss_root: str, nas_root: str) -> OssToNasMigrationService:
    """Build the service from explicit roots via its injected config."""
    return OssToNasMigrationService(
        OssToNasConfig(oss_root=oss_root, nas_root=nas_root)
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _capture_logs(caplog: pytest.LogCaptureFixture) -> None:
    """Ensure caplog captures INFO+ from the service logger."""
    log = logging.getLogger(LOG_NAME)
    log.propagate = True
    caplog.set_level(logging.INFO, logger=LOG_NAME)


@pytest.fixture()
def service(tmp_path: Path) -> OssToNasMigrationService:
    """Service with tmp_path as both oss_root and nas_root."""
    return _make_service(str(tmp_path / "oss"), str(tmp_path / "nas"))


@pytest.fixture()
def oss_dir(tmp_path: Path) -> Path:
    """Create a valid OSS source directory and return its root."""
    oss_root = tmp_path / "oss"
    bot_dir = oss_root / "aidesktop_pre" / "bolt_data" / "staff_100013" / "20260401_r5j3w8lv" / "openclaw"
    bot_dir.mkdir(parents=True)
    (bot_dir / "config.json").write_text('{"name": "test"}')
    return oss_root


@pytest.fixture()
def nas_dir(tmp_path: Path) -> Path:
    """Create a valid NAS source directory and return its root.

    NAS 目录结构包含 .openclaw 子目录:
    nas/prod/pre_staff_100013_openclaw_20260401_r5j3w8lv/.openclaw/
    """
    nas_root = tmp_path / "nas"
    openclaw_dir = nas_root / "prod" / "pre_staff_100013_openclaw_20260401_r5j3w8lv" / ".openclaw"
    openclaw_dir.mkdir(parents=True)
    (openclaw_dir / "config.json").write_text('{"name": "test"}')
    return nas_root


# ---------------------------------------------------------------------------
# Path construction
# ---------------------------------------------------------------------------

class TestResolveEnvDir:
    def test_prod(self) -> None:
        assert OssToNasMigrationService._resolve_env_dir("prod") == "aidesktop_prod"

    def test_pre(self) -> None:
        assert OssToNasMigrationService._resolve_env_dir("pre") == "aidesktop_pre"

    def test_dev(self) -> None:
        assert OssToNasMigrationService._resolve_env_dir("dev") == "aidesktop_dev"


class TestGetOssPath:
    def test_path_structure(self, service: OssToNasMigrationService, tmp_path: Path) -> None:
        path = service._get_oss_path("pre", "staff", "100013", "openclaw", "20260401_r5j3w8lv")
        expected = tmp_path / "oss" / "aidesktop_pre" / "bolt_data" / "staff_100013" / "20260401_r5j3w8lv" / "openclaw"
        assert path == expected


class TestGetNasPath:
    def test_path_structure(self, service: OssToNasMigrationService, tmp_path: Path) -> None:
        path = service._get_nas_path("pre", "staff", "100013", "openclaw", "20260401_r5j3w8lv")
        expected = tmp_path / "nas" / "prod" / "pre_staff_100013_openclaw_20260401_r5j3w8lv"
        assert path == expected


# ---------------------------------------------------------------------------
# migrate()
# ---------------------------------------------------------------------------

class TestMigrate:
    def test_returns_false_when_oss_path_missing(
        self, service: OssToNasMigrationService, caplog: pytest.LogCaptureFixture
    ) -> None:
        result = service.migrate("pre", "staff", "100013", "openclaw", "20260401_r5j3w8lv")
        assert result is False
        assert "源路径不存在" in caplog.text

    def test_returns_true_on_successful_rsync(
        self, tmp_path: Path, oss_dir: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        svc = _make_service(str(oss_dir), str(tmp_path / "nas"))
        with patch("agentclaw.community.core.devices.services.oss_to_nas_migration_service.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            result = svc.migrate("pre", "staff", "100013", "openclaw", "20260401_r5j3w8lv")

        assert result is True
        # verify rsync command
        args = mock_run.call_args[0][0]
        assert args[0] == "rsync"
        assert "-av" in args
        assert "--delete" in args
        # 源路径以 / 结尾，目标路径包含 .openclaw 且以 / 结尾
        assert args[-2].endswith("openclaw/")
        assert ".openclaw/" in args[-1]
        assert "pre_staff_100013_openclaw_20260401_r5j3w8lv" in args[-1]
        assert "收到迁移请求" in caplog.text
        assert "开始迁移" in caplog.text
        assert "迁移完成" in caplog.text

    def test_returns_false_on_rsync_failure(
        self, tmp_path: Path, oss_dir: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        svc = _make_service(str(oss_dir), str(tmp_path / "nas"))
        with patch("agentclaw.community.core.devices.services.oss_to_nas_migration_service.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="rsync: error")
            result = svc.migrate("pre", "staff", "100013", "openclaw", "20260401_r5j3w8lv")

        assert result is False
        assert "rsync 命令执行失败" in caplog.text

    def test_returns_false_on_timeout(
        self, tmp_path: Path, oss_dir: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        svc = _make_service(str(oss_dir), str(tmp_path / "nas"))
        with patch("agentclaw.community.core.devices.services.oss_to_nas_migration_service.subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.TimeoutExpired(cmd="rsync", timeout=600)
            result = svc.migrate("pre", "staff", "100013", "openclaw", "20260401_r5j3w8lv")

        assert result is False
        assert "rsync 命令超时" in caplog.text

    def test_returns_false_on_unexpected_exception(
        self, tmp_path: Path, oss_dir: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        svc = _make_service(str(oss_dir), str(tmp_path / "nas"))
        with patch("agentclaw.community.core.devices.services.oss_to_nas_migration_service.subprocess.run") as mock_run:
            mock_run.side_effect = OSError("permission denied")
            result = svc.migrate("pre", "staff", "100013", "openclaw", "20260401_r5j3w8lv")

        assert result is False
        assert "迁移异常" in caplog.text

    def test_creates_nas_openclaw_directory(
        self, tmp_path: Path, oss_dir: Path
    ) -> None:
        nas_root = tmp_path / "nas"
        svc = _make_service(str(oss_dir), str(nas_root))
        with patch("agentclaw.community.core.devices.services.oss_to_nas_migration_service.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            svc.migrate("pre", "staff", "100013", "openclaw", "20260401_r5j3w8lv")

        # 验证 .openclaw 目录被创建
        openclaw_dir = nas_root / "prod" / "pre_staff_100013_openclaw_20260401_r5j3w8lv" / ".openclaw"
        assert openclaw_dir.is_dir()

    def test_returns_false_when_mkdir_fails(
        self, tmp_path: Path, oss_dir: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        svc = _make_service(str(oss_dir), str(tmp_path / "nas"))
        with patch.object(Path, "mkdir", side_effect=OSError("read-only filesystem")):
            result = svc.migrate("pre", "staff", "100013", "openclaw", "20260401_r5j3w8lv")

        assert result is False
        assert "创建 NAS 目标目录失败" in caplog.text or "目标目录失败" in caplog.text


class TestMigrateNasToOss:
    """Tests for migrate() with direction='nas_to_oss'."""

    def test_returns_false_when_nas_path_missing(
        self, service: OssToNasMigrationService, caplog: pytest.LogCaptureFixture
    ) -> None:
        result = service.migrate(
            "pre", "staff", "100013", "openclaw", "20260401_r5j3w8lv",
            direction="nas_to_oss",
        )
        assert result is False
        assert "NAS 源路径不存在" in caplog.text

    def test_returns_true_on_successful_rsync(
        self, tmp_path: Path, nas_dir: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        svc = _make_service(str(tmp_path / "oss"), str(nas_dir))
        with patch("agentclaw.community.core.devices.services.oss_to_nas_migration_service.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            result = svc.migrate(
                "pre", "staff", "100013", "openclaw", "20260401_r5j3w8lv",
                direction="nas_to_oss",
            )

        assert result is True
        args = mock_run.call_args[0][0]
        assert args[0] == "rsync"
        # 源路径是 NAS 的 .openclaw 目录，以 / 结尾；目标路径是 OSS 的 openclaw 目录，以 / 结尾
        assert ".openclaw/" in args[-2]
        assert "pre_staff_100013_openclaw_20260401_r5j3w8lv" in args[-2]
        assert args[-1].endswith("openclaw/")
        assert "迁移完成" in caplog.text

    def test_creates_oss_parent_directory(
        self, tmp_path: Path, nas_dir: Path
    ) -> None:
        oss_root = tmp_path / "oss"
        svc = _make_service(str(oss_root), str(nas_dir))
        with patch("agentclaw.community.core.devices.services.oss_to_nas_migration_service.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            svc.migrate(
                "pre", "staff", "100013", "openclaw", "20260401_r5j3w8lv",
                direction="nas_to_oss",
            )

        # OSS parent dir should have been created
        expected_parent = oss_root / "aidesktop_pre" / "bolt_data" / "staff_100013" / "20260401_r5j3w8lv"
        assert expected_parent.is_dir()
