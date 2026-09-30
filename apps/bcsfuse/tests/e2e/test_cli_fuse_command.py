"""
Tests for CLI Fuse Command (E2E)

G1: Fusion Entry Layer

测试 bcs-cli fuse 命令的端到端行为。

注意：CLI 直接调用 GroupFusionService，不经过 HTTP。
"""

from __future__ import annotations

import pytest
import sys
from io import StringIO
from unittest.mock import patch, MagicMock

from src.domain.models.fusion_result import Perspective
from src.domain.services.perspective_provider import PerspectiveProvider, PerspectiveContext


# =============================================================================
# Test Fixtures
# =============================================================================

class MockPerspectiveProvider:
    """测试用 Mock Provider"""

    def __init__(self, perspectives: dict[str, Perspective] | None = None):
        self.perspectives = perspectives or {}

    def collect(self, context: PerspectiveContext) -> Perspective:
        if context.participant_id in self.perspectives:
            return self.perspectives[context.participant_id]
        return Perspective(
            participant_id=context.participant_id,
            participant_type="bot",
            role="consultant",
            summary=f"From {context.participant_id}: looks good",
            confidence=0.8,
            status="completed",
        )


# =============================================================================
# Module Tests
# =============================================================================

class TestCLIFuseModule:
    """CLI 模块存在性测试"""

    def test_cli_module_exists(self):
        """测试 CLI 模块存在"""
        import importlib

        module = importlib.import_module("src.interfaces.cli.bcs_cli")
        assert module is not None

    def test_cli_fuse_function_exists(self):
        """测试 fuse 命令函数存在"""
        from src.interfaces.cli.bcs_cli import fuse_command

        assert fuse_command is not None


# =============================================================================
# CLI Argument Tests
# =============================================================================

class TestCLIFuseArguments:
    """CLI 参数解析测试"""

    def test_minimal_arguments(self):
        """测试最小参数"""
        from src.interfaces.cli.bcs_cli import parse_args

        args = parse_args([
            "fuse",
            "--group", "grp-001",
            "--question", "test question",
            "--participants", "dba,security",
        ])

        assert args.group == "grp-001"
        assert args.question == "test question"
        assert args.participants == "dba,security"

    def test_full_arguments(self):
        """测试完整参数"""
        from src.interfaces.cli.bcs_cli import parse_args

        args = parse_args([
            "fuse",
            "--group", "grp-001",
            "--question", "test question",
            "--participants", "dba,security",
            "--driver", "zhangsan",
            "--timeout", "30000",
            "--json",
        ])

        assert args.driver == "zhangsan"
        assert args.timeout == 30000
        assert args.json_output is True

    def test_no_parallel_flag(self):
        """测试 --no-parallel 标志"""
        from src.interfaces.cli.bcs_cli import parse_args

        args = parse_args([
            "fuse",
            "--group", "grp-001",
            "--question", "test",
            "--participants", "dba",
            "--no-parallel",
        ])

        # --no-parallel 设置 no_parallel=True，parallel 保持默认值
        assert args.no_parallel is True

    def test_no_strict_participants_flag(self):
        """测试 --no-strict-participants 标志"""
        from src.interfaces.cli.bcs_cli import parse_args

        args = parse_args([
            "fuse",
            "--group", "grp-001",
            "--question", "test",
            "--participants", "dba",
            "--no-strict-participants",
        ])

        # --no-strict-participants 设置 no_strict_participants=True
        assert args.no_strict_participants is True


# =============================================================================
# CLI Output Tests
# =============================================================================

class TestCLIFuseOutput:
    """CLI 输出测试"""

    def test_json_output(self, capsys):
        """测试 JSON 输出"""
        from src.interfaces.cli.bcs_cli import fuse_command
        from src.application.services.group_fusion_service import GroupFusionService

        mock_provider = MockPerspectiveProvider()
        service = GroupFusionService(provider=mock_provider)

        # 捕获 stdout
        captured = StringIO()
        with patch("sys.stdout", captured):
            fuse_command(
                group_id="grp-001",
                question="test question",
                participants=["dba", "security"],
                json_output=True,
                service=service,
            )

        output = captured.getvalue()
        assert '"group_id"' in output
        assert '"fusion_id"' in output
        assert '"perspectives"' in output

    def test_pretty_output(self, capsys):
        """测试格式化输出"""
        from src.interfaces.cli.bcs_cli import fuse_command
        from src.application.services.group_fusion_service import GroupFusionService

        mock_provider = MockPerspectiveProvider()
        service = GroupFusionService(provider=mock_provider)

        captured = StringIO()
        with patch("sys.stdout", captured):
            fuse_command(
                group_id="grp-001",
                question="test question",
                participants=["dba"],
                json_output=False,
                service=service,
            )

        output = captured.getvalue()
        assert "Group:" in output or "grp-001" in output

    def test_output_includes_perspectives(self):
        """测试输出包含 perspectives"""
        from src.interfaces.cli.bcs_cli import fuse_command
        from src.application.services.group_fusion_service import GroupFusionService

        perspectives = {
            "dba": Perspective(
                participant_id="dba",
                participant_type="bot",
                role="consultant",
                summary="From DBA: looks good",
                confidence=0.85,
                status="completed",
            ),
        }

        mock_provider = MockPerspectiveProvider(perspectives)
        service = GroupFusionService(provider=mock_provider)

        captured = StringIO()
        with patch("sys.stdout", captured):
            fuse_command(
                group_id="grp-001",
                question="test question",
                participants=["dba"],
                json_output=True,
                service=service,
            )

        output = captured.getvalue()
        assert "dba" in output
        assert "From DBA" in output


# =============================================================================
# CLI Service Integration Tests
# =============================================================================

class TestCLIFuseServiceIntegration:
    """CLI 服务集成测试"""

    def test_cli_uses_same_service_as_http(self):
        """测试 CLI 使用与 HTTP 相同的服务"""
        from src.interfaces.cli.bcs_cli import fuse_command
        from src.application.services.group_fusion_service import GroupFusionService

        # 创建 service
        mock_provider = MockPerspectiveProvider()
        service = GroupFusionService(provider=mock_provider)

        captured = StringIO()
        with patch("sys.stdout", captured):
            result = fuse_command(
                group_id="grp-001",
                question="test question",
                participants=["dba"],
                json_output=True,
                service=service,
            )

        # 验证返回值是 FusionResult
        from src.domain.models.fusion_result import FusionResult
        assert isinstance(result, FusionResult)


# =============================================================================
# CLI Exit Code Tests
# =============================================================================

class TestCLIFuseExitCodes:
    """CLI 退出码测试"""

    def test_success_exit_code(self):
        """测试成功退出码"""
        from src.interfaces.cli.bcs_cli import fuse_command, ExitCode
        from src.application.services.group_fusion_service import GroupFusionService

        mock_provider = MockPerspectiveProvider()
        service = GroupFusionService(provider=mock_provider)

        captured = StringIO()
        with patch("sys.stdout", captured):
            exit_code = fuse_command(
                group_id="grp-001",
                question="test",
                participants=["dba"],
                json_output=True,
                service=service,
                return_exit_code=True,
            )

        assert exit_code == ExitCode.SUCCESS

    def test_partial_success_exit_code(self):
        """测试部分成功退出码"""
        from src.interfaces.cli.bcs_cli import fuse_command, ExitCode
        from src.application.services.group_fusion_service import GroupFusionService

        perspectives = {
            "dba": Perspective(
                participant_id="dba",
                participant_type="bot",
                role="consultant",
                summary="",
                status="failed",
            ),
        }

        mock_provider = MockPerspectiveProvider(perspectives)
        service = GroupFusionService(provider=mock_provider)

        captured = StringIO()
        with patch("sys.stdout", captured):
            exit_code = fuse_command(
                group_id="grp-001",
                question="test",
                participants=["dba"],
                json_output=True,
                service=service,
                return_exit_code=True,
            )

        # MVP: partial success 仍返回 0
        assert exit_code == ExitCode.SUCCESS


# =============================================================================
# CLI Error Handling Tests
# =============================================================================

class TestCLIFuseErrorHandling:
    """CLI 错误处理测试"""

    def test_missing_required_args_shows_help(self):
        """测试缺少必填参数显示帮助"""
        from src.interfaces.cli.bcs_cli import parse_args

        with pytest.raises(SystemExit):
            parse_args(["fuse"])  # 缺少必填参数

    def test_invalid_timeout_value(self):
        """测试无效超时值"""
        from src.interfaces.cli.bcs_cli import parse_args

        with pytest.raises(SystemExit):
            parse_args([
                "fuse",
                "--group", "grp-001",
                "--question", "test",
                "--participants", "dba",
                "--timeout", "not_a_number",
            ])