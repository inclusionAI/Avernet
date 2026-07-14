"""
ExecutionPacketCompiler Protocol

M8: Execution Packet Compiler

定义执行包编译器接口，用于将 CompilerInput 编译为 ExecutionPacket。

ExecutionPacketCompiler 的职责：
- 接收 CompilerInput
- 执行编译逻辑（快照、裁剪、打包）
- 返回 CompilerResult

ExecutionPacketCompiler 不负责：
- 文件落盘（M9 OpenClaw Adapter）
- OpenClaw workspace 生成（M9）
- 任务执行
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from src.domain.models.compiler_input import CompilerInput
from src.domain.models.compiler_result import CompilerResult


@runtime_checkable
class ExecutionPacketCompiler(Protocol):
    """
    执行包编译器协议

    定义从输入数据编译 ExecutionPacket 的接口。

    方法：
        compile: 执行编译，返回 CompilerResult
    """

    def compile(self, input_data: CompilerInput) -> CompilerResult:
        """
        执行包编译

        Args:
            input_data: 编译输入，包含 TaskSpec、PlanDraft、TeamSpec、CandidateBundle、Workspace 和提示

        Returns:
            CompilerResult: 编译结果，包含 ExecutionPacket、警告、错误和解释
        """
        ...


__all__ = ["ExecutionPacketCompiler"]