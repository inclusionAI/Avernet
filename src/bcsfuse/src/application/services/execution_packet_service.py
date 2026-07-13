"""
ExecutionPacketService

M8: Execution Packet Compiler

执行包编译服务，负责编排编译流程并返回结果。

职责：
- 接收 CompilerInput
- 调用 ExecutionPacketCompiler 执行编译
- 汇总并返回 CompilerResult

不负责：
- 具体编译逻辑
- 文件落盘（M9 OpenClaw Adapter）
- 任务执行
"""

from __future__ import annotations

from src.domain.services.execution_packet_compiler import ExecutionPacketCompiler
from src.domain.models.compiler_input import CompilerInput
from src.domain.models.compiler_result import CompilerResult


class ExecutionPacketService:
    """
    执行包编译服务

    负责编排执行包编译流程，调用 ExecutionPacketCompiler 执行编译，
    并返回完整的 CompilerResult。

    Fields:
        compiler: ExecutionPacketCompiler 实例
    """

    def __init__(self, compiler: ExecutionPacketCompiler):
        """
        初始化 ExecutionPacketService

        Args:
            compiler: ExecutionPacketCompiler 实例，用于执行包编译
        """
        self._compiler = compiler

    @property
    def compiler(self) -> ExecutionPacketCompiler:
        """获取 compiler"""
        return self._compiler

    def compile(self, input_data: CompilerInput) -> CompilerResult:
        """
        执行包编译

        Args:
            input_data: 编译输入，包含 TaskSpec、PlanDraft、TeamSpec、CandidateBundle、Workspace 和提示

        Returns:
            CompilerResult: 编译结果，包含 ExecutionPacket、警告、错误和解释
        """
        # 直接调用 compiler 执行编译
        # compiler 负责所有的快照、裁剪和打包逻辑
        result = self._compiler.compile(input_data)

        return result


__all__ = ["ExecutionPacketService"]