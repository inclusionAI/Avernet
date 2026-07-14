"""
Tests for Retrieval Mode

Worker Profile Retrieval & Fusion Simulation Baseline

测试范围：
- RetrievalMode: 检索模式枚举，对齐 fusion_mode
"""

from __future__ import annotations

import pytest


class TestRetrievalMode:
    """测试 RetrievalMode 枚举"""

    def test_retrieval_mode_values(self):
        """测试枚举值定义，对齐 fusion_mode"""
        from src.domain.models.retrieval_mode import RetrievalMode

        # 对齐 fusion_mode 的三个值
        assert RetrievalMode.AGENT == "agent"
        assert RetrievalMode.CONFLICT_ALIGNMENT == "conflict_alignment"
        assert RetrievalMode.EXPERT_DIAGNOSIS == "expert_diagnosis"

        # 内部通用检索模式
        assert RetrievalMode.GENERAL == "general"

    def test_retrieval_mode_from_string(self):
        """测试从字符串创建枚举"""
        from src.domain.models.retrieval_mode import RetrievalMode

        assert RetrievalMode("agent") == RetrievalMode.AGENT
        assert RetrievalMode("conflict_alignment") == RetrievalMode.CONFLICT_ALIGNMENT
        assert RetrievalMode("expert_diagnosis") == RetrievalMode.EXPERT_DIAGNOSIS
        assert RetrievalMode("general") == RetrievalMode.GENERAL

    def test_retrieval_mode_invalid_value(self):
        """测试无效枚举值"""
        from src.domain.models.retrieval_mode import RetrievalMode

        with pytest.raises(ValueError):
            RetrievalMode("invalid_mode")

    def test_retrieval_mode_is_string_enum(self):
        """测试是字符串枚举"""
        from src.domain.models.retrieval_mode import RetrievalMode

        # 应该可以当作字符串使用
        assert RetrievalMode.AGENT.value == "agent"
        assert isinstance(RetrievalMode.AGENT, str)

    def test_retrieval_mode_fusion_modes(self):
        """测试 fusion 相关的模式列表"""
        from src.domain.models.retrieval_mode import RetrievalMode

        fusion_modes = RetrievalMode.fusion_modes()

        # 应该包含 agent, conflict_alignment, expert_diagnosis
        assert RetrievalMode.AGENT in fusion_modes
        assert RetrievalMode.CONFLICT_ALIGNMENT in fusion_modes
        assert RetrievalMode.EXPERT_DIAGNOSIS in fusion_modes

        # 不应该包含 general
        assert RetrievalMode.GENERAL not in fusion_modes

    def test_retrieval_mode_from_fusion_mode(self):
        """测试从 fusion_mode 字符串转换"""
        from src.domain.models.retrieval_mode import RetrievalMode

        # 对齐 fusion_request 中的 fusion_mode 值
        assert RetrievalMode.from_fusion_mode("agent") == RetrievalMode.AGENT
        assert RetrievalMode.from_fusion_mode("conflict_alignment") == RetrievalMode.CONFLICT_ALIGNMENT
        assert RetrievalMode.from_fusion_mode("expert_diagnosis") == RetrievalMode.EXPERT_DIAGNOSIS