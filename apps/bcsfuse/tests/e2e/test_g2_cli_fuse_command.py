"""
Tests for G2 CLI Fuse Command

G2: Conflict Alignment Layer

测试 G2 CLI 的 E2E 行为。
"""

from __future__ import annotations

import pytest
from unittest.mock import Mock, patch

from src.domain.models.fusion_result import Perspective
from src.interfaces.cli.bcs_cli import (
    fuse_command,
    parse_args,
    ExitCode,
)


class TestG2CLIParseArgs:
    """G2 CLI 参数解析测试"""

    def test_mode_conflict_alignment_accepted(self):
        """测试 --mode conflict_alignment 被接受"""
        args = parse_args([
            "fuse",
            "--group", "grp-001",
            "--question", "test",
            "--participants", "a,b",
            "--mode", "conflict_alignment",
        ])

        assert args.fusion_mode == "conflict_alignment"

    def test_mode_agent_explicit(self):
        """测试 --mode agent 显式指定"""
        args = parse_args([
            "fuse",
            "--group", "grp-001",
            "--question", "test",
            "--participants", "a,b",
            "--mode", "agent",
        ])

        assert args.fusion_mode == "agent"

    def test_mode_default_is_agent(self):
        """测试 --mode 默认是 agent"""
        args = parse_args([
            "fuse",
            "--group", "grp-001",
            "--question", "test",
            "--participants", "a,b",
        ])

        assert args.fusion_mode == "agent"

    def test_invalid_mode_rejected(self):
        """测试无效 mode 被拒绝"""
        with pytest.raises(SystemExit):
            parse_args([
                "fuse",
                "--group", "grp-001",
                "--question", "test",
                "--participants", "a,b",
                "--mode", "invalid_mode",
            ])


class TestG2CLIFuseCommand:
    """G2 CLI fuse 命令测试"""

    def test_g2_cli_happy_path(self):
        """测试 G2 CLI happy path"""
        # 创建 mock service
        mock_service = Mock()
        mock_result = Mock()
        mock_result.group_id = "grp-001"
        mock_result.fusion_id = "fus-001"
        mock_result.question = "如何协调代码与PRD的超时时间冲突？"
        mock_result.driver_bot_id = "zhangsan"
        mock_result.perspectives = [
            Perspective(
                participant_id="zhangsan",
                participant_type="bot",
                role="driver",
                summary="开发者视角",
                status="completed",
            ),
            Perspective(
                participant_id="lisi",
                participant_type="bot",
                role="consultant",
                summary="PM视角",
                status="completed",
            ),
        ]
        mock_result.recommendation = Mock(
            summary="建议协调",
            decision="conditional_yes",
            risks=[],
            next_actions=[],
        )
        mock_result.partial_success = False
        mock_result.warnings = []
        mock_result.errors = []
        mock_result.timing = Mock()
        mock_result.fusion_mode = "conflict_alignment"
        mock_result.conflicts = [Mock(
            parties=["zhangsan", "lisi"],
            issue="关注点不同",
            positions=["zhangsan: 开发", "lisi: 产品"],
            severity="low",
        )]
        mock_result.alignment_points = []
        mock_result.key_insights = ["存在分歧"]
        mock_result.model_dump = Mock(return_value={
            "group_id": "grp-001",
            "fusion_id": "fus-001",
            "question": "如何协调代码与PRD的超时时间冲突？",
            "driver_bot_id": "zhangsan",
            "perspectives": [],
            "recommendation": {"summary": "建议协调", "decision": "conditional_yes", "risks": [], "next_actions": []},
            "partial_success": False,
            "warnings": [],
            "errors": [],
            "timing": {},
            "fusion_mode": "conflict_alignment",
            "conflicts": [{"parties": ["zhangsan", "lisi"], "issue": "关注点不同", "positions": [], "severity": "low"}],
            "alignment_points": [],
            "key_insights": ["存在分歧"],
        })

        mock_service.fuse.return_value = mock_result

        # 执行命令
        result = fuse_command(
            group_id="grp-001",
            question="如何协调代码与PRD的超时时间冲突？",
            participants=["zhangsan", "lisi"],
            driver_bot_id="zhangsan",
            fusion_mode="conflict_alignment",
            json_output=True,
            service=mock_service,
        )

        # 验证 service 被正确调用
        mock_service.fuse.assert_called_once()
        call_args = mock_service.fuse.call_args
        request = call_args[0][0]
        assert request.fusion_mode == "conflict_alignment"
        assert request.question == "如何协调代码与PRD的超时时间冲突？"

    def test_g2_cli_includes_perspectives(self):
        """测试 G2 CLI 输出包含 perspectives"""
        mock_service = Mock()
        mock_result = Mock()
        mock_result.group_id = "grp-001"
        mock_result.fusion_id = "fus-001"
        mock_result.question = "test"
        mock_result.driver_bot_id = None
        mock_result.perspectives = [
            Perspective(
                participant_id="dev",
                participant_type="bot",
                role="consultant",
                summary="可行",
                status="completed",
            ),
        ]
        mock_result.recommendation = None
        mock_result.partial_success = False
        mock_result.warnings = []
        mock_result.errors = []
        mock_result.timing = Mock()
        mock_result.fusion_mode = "conflict_alignment"
        mock_result.conflicts = []
        mock_result.alignment_points = []
        mock_result.key_insights = []
        mock_result.model_dump = Mock(return_value={})

        mock_service.fuse.return_value = mock_result

        result = fuse_command(
            group_id="grp-001",
            question="test",
            participants=["dev"],
            fusion_mode="conflict_alignment",
            service=mock_service,
        )

        # 验证 perspectives 存在
        assert len(result.perspectives) == 1
        assert result.perspectives[0].participant_id == "dev"

    def test_g2_cli_includes_conflicts(self):
        """测试 G2 CLI 输出包含 conflicts"""
        mock_service = Mock()
        mock_result = Mock()
        mock_result.group_id = "grp-001"
        mock_result.fusion_id = "fus-001"
        mock_result.question = "test"
        mock_result.driver_bot_id = None
        mock_result.perspectives = []
        mock_result.recommendation = None
        mock_result.partial_success = False
        mock_result.warnings = []
        mock_result.errors = []
        mock_result.timing = Mock()
        mock_result.fusion_mode = "conflict_alignment"

        # 创建冲突
        mock_conflict = Mock()
        mock_conflict.parties = ["a", "b"]
        mock_conflict.issue = "立场不一致"
        mock_conflict.positions = ["a: 支持", "b: 反对"]
        mock_conflict.severity = "high"
        mock_result.conflicts = [mock_conflict]
        mock_result.alignment_points = []
        mock_result.key_insights = ["存在分歧"]
        mock_result.model_dump = Mock(return_value={})

        mock_service.fuse.return_value = mock_result

        result = fuse_command(
            group_id="grp-001",
            question="test",
            participants=["a", "b"],
            fusion_mode="conflict_alignment",
            service=mock_service,
        )

        # 验证 conflicts 存在
        assert len(result.conflicts) == 1
        assert result.conflicts[0].issue == "立场不一致"

    def test_g2_cli_includes_recommendation(self):
        """测试 G2 CLI 输出包含 recommendation"""
        mock_service = Mock()
        mock_result = Mock()
        mock_result.group_id = "grp-001"
        mock_result.fusion_id = "fus-001"
        mock_result.question = "test"
        mock_result.driver_bot_id = None
        mock_result.perspectives = []
        mock_result.recommendation = Mock(
            summary="综合建议",
            decision="yes",
            risks=[],
            next_actions=["下一步行动"],
        )
        mock_result.partial_success = False
        mock_result.warnings = []
        mock_result.errors = []
        mock_result.timing = Mock()
        mock_result.fusion_mode = "conflict_alignment"
        mock_result.conflicts = []
        mock_result.alignment_points = []
        mock_result.key_insights = []
        mock_result.model_dump = Mock(return_value={})

        mock_service.fuse.return_value = mock_result

        result = fuse_command(
            group_id="grp-001",
            question="test",
            participants=["a"],
            fusion_mode="conflict_alignment",
            service=mock_service,
        )

        # 验证 recommendation 存在
        assert result.recommendation is not None
        assert result.recommendation.summary == "综合建议"

    def test_g2_cli_json_output(self, capsys):
        """测试 G2 CLI JSON 输出"""
        mock_service = Mock()
        mock_result = Mock()
        mock_result.group_id = "grp-001"
        mock_result.fusion_id = "fus-001"
        mock_result.question = "test"
        mock_result.driver_bot_id = None
        mock_result.perspectives = []
        mock_result.recommendation = None
        mock_result.partial_success = False
        mock_result.warnings = []
        mock_result.errors = []
        mock_result.timing = Mock()
        mock_result.fusion_mode = "conflict_alignment"
        mock_result.conflicts = []
        mock_result.alignment_points = []
        mock_result.key_insights = []
        mock_result.model_dump = Mock(return_value={
            "fusion_mode": "conflict_alignment",
        })

        mock_service.fuse.return_value = mock_result

        fuse_command(
            group_id="grp-001",
            question="test",
            participants=["a"],
            fusion_mode="conflict_alignment",
            json_output=True,
            service=mock_service,
        )

        captured = capsys.readouterr()
        # 验证 JSON 输出包含 fusion_mode
        assert "conflict_alignment" in captured.out

    def test_g2_cli_pretty_output(self, capsys):
        """测试 G2 CLI pretty 输出"""
        mock_service = Mock()
        mock_result = Mock()
        mock_result.group_id = "grp-001"
        mock_result.fusion_id = "fus-001"
        mock_result.question = "如何协调冲突？"
        mock_result.driver_bot_id = None
        mock_result.perspectives = [
            Perspective(
                participant_id="dev",
                participant_type="bot",
                role="consultant",
                summary="可行",
                status="completed",
            ),
        ]
        mock_result.recommendation = Mock(
            summary="建议",
            decision="yes",
            risks=[],
            next_actions=[],
        )
        mock_result.partial_success = False
        mock_result.warnings = []
        mock_result.errors = []
        mock_result.timing = Mock()
        mock_result.fusion_mode = "conflict_alignment"
        mock_result.conflicts = []
        mock_result.alignment_points = []
        mock_result.key_insights = ["关键洞察"]
        mock_result.model_dump = Mock(return_value={})

        mock_service.fuse.return_value = mock_result

        fuse_command(
            group_id="grp-001",
            question="如何协调冲突？",
            participants=["dev"],
            fusion_mode="conflict_alignment",
            service=mock_service,
        )

        captured = capsys.readouterr()
        # 验证 pretty 输出包含关键信息
        assert "grp-001" in captured.out
        assert "conflict_alignment" in captured.out
        assert "Perspectives" in captured.out


class TestG2CLIG1G2Isolation:
    """G2 CLI G1/G2 隔离测试"""

    def test_g1_mode_not_affected_by_g2(self):
        """测试 G1 模式不受 G2 影响"""
        mock_service = Mock()
        mock_result = Mock()
        mock_result.group_id = "grp-001"
        mock_result.fusion_id = "fus-001"
        mock_result.question = "test"
        mock_result.driver_bot_id = None
        mock_result.perspectives = []
        mock_result.recommendation = None
        mock_result.partial_success = False
        mock_result.warnings = []
        mock_result.errors = []
        mock_result.timing = Mock()
        mock_result.fusion_mode = "agent"
        mock_result.conflicts = []
        mock_result.alignment_points = []
        mock_result.key_insights = []
        mock_result.model_dump = Mock(return_value={})

        mock_service.fuse.return_value = mock_result

        result = fuse_command(
            group_id="grp-001",
            question="test",
            participants=["a"],
            fusion_mode="agent",  # G1
            service=mock_service,
        )

        # G1 模式下 G2 字段为空
        assert result.fusion_mode == "agent"
        assert result.conflicts == []
        assert result.alignment_points == []
        assert result.key_insights == []

    def test_g2_mode_produces_g2_fields(self):
        """测试 G2 模式产生 G2 字段"""
        mock_service = Mock()
        mock_result = Mock()
        mock_result.group_id = "grp-001"
        mock_result.fusion_id = "fus-001"
        mock_result.question = "test"
        mock_result.driver_bot_id = None
        mock_result.perspectives = []
        mock_result.recommendation = None
        mock_result.partial_success = False
        mock_result.warnings = []
        mock_result.errors = []
        mock_result.timing = Mock()
        mock_result.fusion_mode = "conflict_alignment"
        mock_result.conflicts = [Mock(parties=["a", "b"], issue="test", positions=[], severity="low")]
        mock_result.alignment_points = []
        mock_result.key_insights = ["存在分歧"]
        mock_result.model_dump = Mock(return_value={})

        mock_service.fuse.return_value = mock_result

        result = fuse_command(
            group_id="grp-001",
            question="test",
            participants=["a", "b"],
            fusion_mode="conflict_alignment",  # G2
            service=mock_service,
        )

        # G2 模式
        assert result.fusion_mode == "conflict_alignment"
        assert len(result.conflicts) == 1


class TestG2CLIEnterpriseScenario:
    """G2 CLI 企业级场景测试"""

    def test_timeout_conflict_scenario(self):
        """
        测试超时冲突场景（企业级样例）

        场景：代码实现 60 分钟超时 vs PRD 要求 30 分钟超时
        """
        mock_service = Mock()

        # 模拟三方视角
        perspectives = [
            Perspective(
                participant_id="zhangsan",
                participant_type="bot",
                role="driver",
                summary="开发者视角：当前代码实现为60分钟超时",
                key_points=["兼容旧系统", "避免大规模重构"],
                concerns=["改造成本"],
                flexibility="愿意分阶段改造",
                status="completed",
            ),
            Perspective(
                participant_id="lisi",
                participant_type="bot",
                role="consultant",
                summary="PM视角：PRD要求30分钟超时",
                key_points=["用户体验"],
                concerns=["用户等待焦虑"],
                flexibility="理解兼容性考虑",
                status="completed",
            ),
            Perspective(
                participant_id="anquan",
                participant_type="bot",
                role="consultant",
                summary="安全视角：60分钟存在会话劫持风险",
                key_points=["安全合规"],
                concerns=["会话安全"],
                flexibility="如果必须60分钟，需加二次确认",
                status="completed",
            ),
        ]

        mock_result = Mock()
        mock_result.group_id = "grp-fusion-001"
        mock_result.fusion_id = "fus-001"
        mock_result.question = "如何协调代码与PRD的超时时间冲突？"
        mock_result.driver_bot_id = "zhangsan"
        mock_result.perspectives = perspectives
        mock_result.recommendation = Mock(
            summary="建议分阶段协调",
            decision="conditional_yes",
            risks=["安全风险"],
            next_actions=["评估改造范围"],
        )
        mock_result.partial_success = False
        mock_result.warnings = []
        mock_result.errors = []
        mock_result.timing = Mock()
        mock_result.fusion_mode = "conflict_alignment"
        mock_result.conflicts = [
            Mock(parties=["zhangsan", "lisi"], issue="关注点存在差异", positions=[], severity="low"),
        ]
        mock_result.alignment_points = []
        mock_result.key_insights = ["存在部分分歧，可通过协商解决"]
        mock_result.model_dump = Mock(return_value={})

        mock_service.fuse.return_value = mock_result

        result = fuse_command(
            group_id="grp-fusion-001",
            question="如何协调代码与PRD的超时时间冲突？",
            participants=["zhangsan", "lisi", "anquan"],
            driver_bot_id="zhangsan",
            fusion_mode="conflict_alignment",
            service=mock_service,
        )

        # 验证企业级场景结果
        assert len(result.perspectives) == 3
        assert len(result.conflicts) >= 1
        assert result.recommendation is not None
        assert result.recommendation.decision == "conditional_yes"

    def test_consensus_scenario(self):
        """
        测试共识场景（企业级样例）

        场景：开发与PM都支持方案
        """
        mock_service = Mock()

        perspectives = [
            Perspective(
                participant_id="dev",
                participant_type="bot",
                role="consultant",
                summary="方案可行",
                status="completed",
            ),
            Perspective(
                participant_id="pm",
                participant_type="bot",
                role="consultant",
                summary="方案可行",
                status="completed",
            ),
        ]

        mock_result = Mock()
        mock_result.group_id = "grp-001"
        mock_result.fusion_id = "fus-001"
        mock_result.question = "方案是否可行？"
        mock_result.driver_bot_id = None
        mock_result.perspectives = perspectives
        mock_result.recommendation = Mock(
            summary="各方一致支持",
            decision="yes",
            risks=[],
            next_actions=[],
        )
        mock_result.partial_success = False
        mock_result.warnings = []
        mock_result.errors = []
        mock_result.timing = Mock()
        mock_result.fusion_mode = "conflict_alignment"
        mock_result.conflicts = []
        mock_result.alignment_points = [
            Mock(summary="dev, pm 都持 支持 态度", participants=["dev", "pm"]),
        ]
        mock_result.key_insights = ["各方立场一致"]
        mock_result.model_dump = Mock(return_value={})

        mock_service.fuse.return_value = mock_result

        result = fuse_command(
            group_id="grp-001",
            question="方案是否可行？",
            participants=["dev", "pm"],
            fusion_mode="conflict_alignment",
            service=mock_service,
        )

        # 验证共识场景结果
        assert len(result.conflicts) == 0
        assert len(result.alignment_points) >= 1
        assert result.recommendation.decision == "yes"