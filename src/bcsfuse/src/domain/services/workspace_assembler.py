"""
WorkspaceAssembler Protocol

M7: Workspace / Group Assembly

定义工作空间组装器接口，用于从 TaskSpec、PlanDraft、TeamSpec 和 CandidateBundle 组装 Workspace。

WorkspaceAssembler 的职责：
- 接收 WorkspaceAssemblyInput
- 执行组装逻辑（成员映射、资源挂载、知识挂载）
- 返回 WorkspaceAssemblyResult

WorkspaceAssembler 不负责：
- 执行任务
- 调度
- OpenClaw 相关逻辑
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from src.domain.models.workspace_assembly_input import WorkspaceAssemblyInput
from src.domain.models.workspace_assembly_result import WorkspaceAssemblyResult


@runtime_checkable
class WorkspaceAssembler(Protocol):
    """
    工作空间组装器协议

    定义从输入数据组装 Workspace 的接口。

    方法：
        assemble: 执行组装，返回 WorkspaceAssemblyResult
    """

    def assemble(self, input_data: WorkspaceAssemblyInput) -> WorkspaceAssemblyResult:
        """
        执行工作空间组装

        Args:
            input_data: 组装输入，包含 TaskSpec、PlanDraft、TeamSpec、CandidateBundle 和提示

        Returns:
            WorkspaceAssemblyResult: 组装结果，包含 Workspace、警告、错误、解释和挂载信息
        """
        ...


__all__ = ["WorkspaceAssembler"]