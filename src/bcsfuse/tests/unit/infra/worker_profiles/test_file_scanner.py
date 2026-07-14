"""
Tests for File Scanner

Worker Profile Ingestion Baseline

测试范围：
- FileScanner: 文件目录扫描器
- staff_xxx 识别
- default/bot 目录识别
- backup 目录过滤
- 多 root 扫描
"""

from __future__ import annotations

import os
import tempfile
import pytest


class TestFileScanner:
    """测试 FileScanner"""

    def test_scan_single_staff_default(self):
        """测试扫描单个员工的默认目录"""
        from src.infra.worker_profiles.scanners.file_scanner import (
            FileScanner,
        )
        from src.infra.worker_profiles.config.worker_profile_settings import (
            WorkerProfileSettings,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            # 创建目录结构
            staff_path = os.path.join(tmpdir, "staff_260065", "default", "openclaw")
            os.makedirs(staff_path)

            settings = WorkerProfileSettings(roots=[tmpdir])
            scanner = FileScanner(settings)
            entries = scanner.scan()

            assert len(entries) == 1
            assert entries[0].staff_id == "260065"
            assert entries[0].profile_id == "default"
            assert entries[0].profile_type == "default"
            assert entries[0].source_root == tmpdir
            assert entries[0].openclaw_path == staff_path

    def test_scan_staff_with_bot(self):
        """测试扫描员工的 bot 目录"""
        from src.infra.worker_profiles.scanners.file_scanner import (
            FileScanner,
        )
        from src.infra.worker_profiles.config.worker_profile_settings import (
            WorkerProfileSettings,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            # 创建 default 和 bot 目录
            staff_path = os.path.join(tmpdir, "staff_260065")
            default_path = os.path.join(staff_path, "default", "openclaw")
            bot_path = os.path.join(staff_path, "20260319_qjmzo9k6", "openclaw")
            os.makedirs(default_path)
            os.makedirs(bot_path)

            settings = WorkerProfileSettings(roots=[tmpdir], scan_bots=True)
            scanner = FileScanner(settings)
            entries = scanner.scan()

            assert len(entries) == 2

            # 按 profile_type 排序验证
            default_entry = next(e for e in entries if e.profile_type == "default")
            bot_entry = next(e for e in entries if e.profile_type == "bot")

            assert default_entry.staff_id == "260065"
            assert bot_entry.staff_id == "260065"
            assert bot_entry.profile_id == "20260319_qjmzo9k6"

    def test_scan_skip_backup_directory(self):
        """测试跳过备份目录"""
        from src.infra.worker_profiles.scanners.file_scanner import (
            FileScanner,
        )
        from src.infra.worker_profiles.config.worker_profile_settings import (
            WorkerProfileSettings,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            # 创建 default 和 default_bak 目录
            staff_path = os.path.join(tmpdir, "staff_260065")
            default_path = os.path.join(staff_path, "default", "openclaw")
            bak_path = os.path.join(staff_path, "default_bak", "openclaw")
            os.makedirs(default_path)
            os.makedirs(bak_path)

            settings = WorkerProfileSettings(roots=[tmpdir], include_backup=False)
            scanner = FileScanner(settings)
            entries = scanner.scan()

            # 只扫描 default，跳过 default_bak
            assert len(entries) == 1
            assert entries[0].profile_id == "default"

    def test_scan_include_backup_directory(self):
        """测试包含备份目录"""
        from src.infra.worker_profiles.scanners.file_scanner import (
            FileScanner,
        )
        from src.infra.worker_profiles.config.worker_profile_settings import (
            WorkerProfileSettings,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            staff_path = os.path.join(tmpdir, "staff_260065")
            default_path = os.path.join(staff_path, "default", "openclaw")
            bak_path = os.path.join(staff_path, "default_bak", "openclaw")
            os.makedirs(default_path)
            os.makedirs(bak_path)

            settings = WorkerProfileSettings(roots=[tmpdir], include_backup=True)
            scanner = FileScanner(settings)
            entries = scanner.scan()

            # 包含备份目录
            assert len(entries) == 2
            profile_ids = {e.profile_id for e in entries}
            assert "default" in profile_ids
            assert "default_bak" in profile_ids

    def test_scan_skip_bots_when_disabled(self):
        """测试禁用 bot 扫描"""
        from src.infra.worker_profiles.scanners.file_scanner import (
            FileScanner,
        )
        from src.infra.worker_profiles.config.worker_profile_settings import (
            WorkerProfileSettings,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            staff_path = os.path.join(tmpdir, "staff_260065")
            default_path = os.path.join(staff_path, "default", "openclaw")
            bot_path = os.path.join(staff_path, "20260319_abc12345", "openclaw")
            os.makedirs(default_path)
            os.makedirs(bot_path)

            settings = WorkerProfileSettings(roots=[tmpdir], scan_bots=False)
            scanner = FileScanner(settings)
            entries = scanner.scan()

            # 只扫描 default
            assert len(entries) == 1
            assert entries[0].profile_id == "default"

    def test_scan_multiple_staffs(self):
        """测试扫描多个员工"""
        from src.infra.worker_profiles.scanners.file_scanner import (
            FileScanner,
        )
        from src.infra.worker_profiles.config.worker_profile_settings import (
            WorkerProfileSettings,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            # 创建多个员工目录
            for staff_id in ["001", "002", "003"]:
                staff_path = os.path.join(tmpdir, f"staff_{staff_id}", "default", "openclaw")
                os.makedirs(staff_path)

            settings = WorkerProfileSettings(roots=[tmpdir])
            scanner = FileScanner(settings)
            entries = scanner.scan()

            assert len(entries) == 3
            staff_ids = {e.staff_id for e in entries}
            assert staff_ids == {"001", "002", "003"}

    def test_scan_multiple_roots(self):
        """测试扫描多个根目录"""
        from src.infra.worker_profiles.scanners.file_scanner import (
            FileScanner,
        )
        from src.infra.worker_profiles.config.worker_profile_settings import (
            WorkerProfileSettings,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            # 创建两个根目录
            root1 = os.path.join(tmpdir, "root1")
            root2 = os.path.join(tmpdir, "root2")
            os.makedirs(root1)
            os.makedirs(root2)

            # 在 root1 创建 staff_001
            staff1_path = os.path.join(root1, "staff_001", "default", "openclaw")
            os.makedirs(staff1_path)

            # 在 root2 创建 staff_002
            staff2_path = os.path.join(root2, "staff_002", "default", "openclaw")
            os.makedirs(staff2_path)

            settings = WorkerProfileSettings(roots=[root1, root2])
            scanner = FileScanner(settings)
            entries = scanner.scan()

            assert len(entries) == 2
            root1_entries = [e for e in entries if e.source_root == root1]
            root2_entries = [e for e in entries if e.source_root == root2]

            assert len(root1_entries) == 1
            assert root1_entries[0].staff_id == "001"
            assert len(root2_entries) == 1
            assert root2_entries[0].staff_id == "002"

    def test_scan_duplicate_profiles(self):
        """测试重复 profile 处理（不同 root）- 方案 B：保留第一条，忽略后续重复项"""
        from src.infra.worker_profiles.scanners.file_scanner import (
            FileScanner,
        )
        from src.infra.worker_profiles.config.worker_profile_settings import (
            WorkerProfileSettings,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            # 创建两个根目录
            root1 = os.path.join(tmpdir, "root1")
            root2 = os.path.join(tmpdir, "root2")
            os.makedirs(root1)
            os.makedirs(root2)

            # 在两个 root 都创建 staff_001
            for root in [root1, root2]:
                staff_path = os.path.join(root, "staff_001", "default", "openclaw")
                os.makedirs(staff_path)

            settings = WorkerProfileSettings(roots=[root1, root2])
            scanner = FileScanner(settings)
            entries, warnings = scanner.scan_with_warnings()

            # 方案 B：只保留第一条（来自 root1），忽略 root2 的重复项
            assert len(entries) == 1
            assert entries[0].source_root == root1

            # 应该有重复警告
            assert len(warnings) >= 1
            assert any("duplicate" in w.message.lower() for w in warnings)

    def test_scan_non_staff_directory_ignored(self):
        """测试忽略非 staff 目录"""
        from src.infra.worker_profiles.scanners.file_scanner import (
            FileScanner,
        )
        from src.infra.worker_profiles.config.worker_profile_settings import (
            WorkerProfileSettings,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            # 创建非 staff 目录
            other_path = os.path.join(tmpdir, "other_directory", "default", "openclaw")
            os.makedirs(other_path)

            # 创建 staff 目录
            staff_path = os.path.join(tmpdir, "staff_001", "default", "openclaw")
            os.makedirs(staff_path)

            settings = WorkerProfileSettings(roots=[tmpdir])
            scanner = FileScanner(settings)
            entries = scanner.scan()

            # 只扫描 staff 目录
            assert len(entries) == 1
            assert entries[0].staff_id == "001"

    def test_scan_empty_root(self):
        """测试空根目录"""
        from src.infra.worker_profiles.scanners.file_scanner import (
            FileScanner,
        )
        from src.infra.worker_profiles.config.worker_profile_settings import (
            WorkerProfileSettings,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            settings = WorkerProfileSettings(roots=[tmpdir])
            scanner = FileScanner(settings)
            entries = scanner.scan()

            assert entries == []

    def test_scan_nonexistent_root(self):
        """测试不存在的根目录"""
        from src.infra.worker_profiles.scanners.file_scanner import (
            FileScanner,
        )
        from src.infra.worker_profiles.config.worker_profile_settings import (
            WorkerProfileSettings,
        )

        settings = WorkerProfileSettings(roots=["/nonexistent/path"])
        scanner = FileScanner(settings)
        entries, warnings = scanner.scan_with_warnings()

        # 不存在的目录应被忽略并有警告
        assert entries == []
        assert len(warnings) >= 1
        assert any("not found" in w.message.lower() or "not exist" in w.message.lower()
                   for w in warnings)


class TestScanEntry:
    """测试 ScanEntry 数据类"""

    def test_scan_entry_properties(self):
        """测试 ScanEntry 属性"""
        from src.infra.worker_profiles.scanners.file_scanner import ScanEntry

        entry = ScanEntry(
            staff_id="260065",
            profile_id="default",
            profile_type="default",
            source_root="/data/bolt_data",
            openclaw_path="/data/bolt_data/staff_260065/default/openclaw",
        )

        assert entry.staff_id == "260065"
        assert entry.profile_id == "default"
        assert entry.profile_type == "default"
        assert entry.skills_path == "/data/bolt_data/staff_260065/default/openclaw/skills"


class TestFileScannerBotPattern:
    """测试 Bot 目录模式识别"""

    def test_valid_bot_directory_patterns(self):
        """测试有效的 bot 目录模式"""
        from src.infra.worker_profiles.scanners.file_scanner import (
            FileScanner,
        )
        from src.infra.worker_profiles.config.worker_profile_settings import (
            WorkerProfileSettings,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            staff_path = os.path.join(tmpdir, "staff_001")

            # 创建多个 bot 目录
            valid_bot_names = [
                "20260319_qjmzo9k6",
                "20250101_abc12345",
                "20251231_zzz99999",
            ]

            for bot_name in valid_bot_names:
                bot_path = os.path.join(staff_path, bot_name, "openclaw")
                os.makedirs(bot_path)

            settings = WorkerProfileSettings(roots=[tmpdir], scan_bots=True)
            scanner = FileScanner(settings)
            entries = scanner.scan()

            # 所有有效 bot 都应被扫描
            bot_entries = [e for e in entries if e.profile_type == "bot"]
            assert len(bot_entries) == 3
            bot_ids = {e.profile_id for e in bot_entries}
            assert bot_ids == set(valid_bot_names)

    def test_invalid_bot_directory_patterns(self):
        """测试无效的 bot 目录模式"""
        from src.infra.worker_profiles.scanners.file_scanner import (
            FileScanner,
        )
        from src.infra.worker_profiles.config.worker_profile_settings import (
            WorkerProfileSettings,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            staff_path = os.path.join(tmpdir, "staff_001")
            os.makedirs(staff_path)

            # 创建无效目录（不应被识别为 bot）
            invalid_names = [
                "bot_20260319",      # 错误前缀
                "20260319abc123",    # 缺少下划线
                "202603-19_abc123",  # 格式错误
                "random_name",       # 随机名称
            ]

            for name in invalid_names:
                dir_path = os.path.join(staff_path, name)
                os.makedirs(dir_path)

            # 也创建一个 default 目录
            default_path = os.path.join(staff_path, "default", "openclaw")
            os.makedirs(default_path)

            settings = WorkerProfileSettings(roots=[tmpdir], scan_bots=True)
            scanner = FileScanner(settings)
            entries = scanner.scan()

            # 只有 default 被扫描
            assert len(entries) == 1
            assert entries[0].profile_type == "default"