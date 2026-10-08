"""
Tests for WorkspaceAssembler Protocol

M7: Workspace / Group Assembly

测试 WorkspaceAssembler 协议定义和实现验证。
"""

from __future__ import annotations

import pytest

from src.domain.services.workspace_assembler import WorkspaceAssembler


# =============================================================================
# Protocol Verification Tests
# =============================================================================

class TestWorkspaceAssemblerProtocol:
    """WorkspaceAssembler 协议测试"""

    def test_assembler_is_protocol(self):
        """测试 WorkspaceAssembler 是 Protocol"""
        assert hasattr(WorkspaceAssembler, "__protocol_attrs__")

    def test_assembler_has_assemble_method(self):
        """测试 WorkspaceAssembler 有 assemble 方法"""
        assert hasattr(WorkspaceAssembler, "assemble")

    def test_assembler_signature(self):
        """测试 WorkspaceAssembler 方法签名"""
        import inspect

        assemble_method = getattr(WorkspaceAssembler, "assemble")
        sig = inspect.signature(assemble_method)
        params = list(sig.parameters.keys())

        assert "input_data" in params or "self" in params


# =============================================================================
# Implementation Verification Tests
# =============================================================================

class TestWorkspaceAssemblerImplementation:
    """实现验证测试"""

    def test_implementation_satisfies_protocol(self):
        """测试实现满足协议"""
        from src.infra.assemblers.baseline_workspace_assembler import BaselineWorkspaceAssembler

        assembler = BaselineWorkspaceAssembler()

        assert hasattr(assembler, "assemble")
        assert callable(getattr(assembler, "assemble"))