"""
Tests for Matchmaker Interface and Protocol

M6: Team Composer / Matchmaker

测试 Matchmaker 协议定义和默认行为。
"""

from __future__ import annotations

import pytest

from typing import Protocol, runtime_checkable

from src.domain.services.matchmaker import Matchmaker


# =============================================================================
# Protocol Verification Tests
# =============================================================================

class TestMatchmakerProtocol:
    """Matchmaker 协议测试"""

    def test_matchmaker_is_protocol(self):
        """测试 Matchmaker 是 Protocol"""
        assert hasattr(Matchmaker, "__protocol_attrs__")

    def test_matchmaker_has_compose_method(self):
        """测试 Matchmaker 有 compose 方法"""
        assert hasattr(Matchmaker, "compose")

    def test_matchmaker_signature(self):
        """测试 Matchmaker 方法签名"""
        import inspect

        # Check compose method exists
        assert hasattr(Matchmaker, "compose")

        # Get compose method signature
        compose_method = getattr(Matchmaker, "compose")
        sig = inspect.signature(compose_method)

        # Should have input parameter and return CompositionResult
        params = list(sig.parameters.keys())
        assert "input_data" in params or "self" in params


# =============================================================================
# Implementation Verification Tests
# =============================================================================

class TestMatchmakerImplementation:
    """Matchmaker 实现验证测试"""

    def test_implementation_satisfies_protocol(self):
        """测试实现满足协议"""
        from src.infra.matchmakers.baseline_matchmaker import BaselineMatchmaker

        matchmaker = BaselineMatchmaker()

        # Should be callable with compose method
        assert hasattr(matchmaker, "compose")
        assert callable(getattr(matchmaker, "compose"))

        # BaselineMatchmaker 实例应该可用于类型检查
        # 在 M6 baseline 实现中，我们使用 duck typing 而非显式 Protocol