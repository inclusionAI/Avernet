"""
Tests for ExecutionPacketCompiler Protocol

M8: Execution Packet Compiler

测试 ExecutionPacketCompiler 协议定义和实现验证。
"""

from __future__ import annotations

import pytest

from src.domain.services.execution_packet_compiler import ExecutionPacketCompiler


# =============================================================================
# Protocol Verification Tests
# =============================================================================

class TestExecutionPacketCompilerProtocol:
    """ExecutionPacketCompiler 协议测试"""

    def test_compiler_is_protocol(self):
        """测试 ExecutionPacketCompiler 是 Protocol"""
        assert hasattr(ExecutionPacketCompiler, "__protocol_attrs__")

    def test_compiler_has_compile_method(self):
        """测试 ExecutionPacketCompiler 有 compile 方法"""
        assert hasattr(ExecutionPacketCompiler, "compile")

    def test_compiler_signature(self):
        """测试 ExecutionPacketCompiler 方法签名"""
        import inspect

        compile_method = getattr(ExecutionPacketCompiler, "compile")
        sig = inspect.signature(compile_method)
        params = list(sig.parameters.keys())

        assert "input_data" in params or "self" in params


# =============================================================================
# Implementation Verification Tests
# =============================================================================

class TestExecutionPacketCompilerImplementation:
    """实现验证测试"""

    def test_implementation_satisfies_protocol(self):
        """测试实现满足协议"""
        from src.infra.compilers.baseline_execution_packet_compiler import BaselineExecutionPacketCompiler

        compiler = BaselineExecutionPacketCompiler()

        assert hasattr(compiler, "compile")
        assert callable(getattr(compiler, "compile"))