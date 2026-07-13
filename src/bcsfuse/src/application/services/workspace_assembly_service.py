"""
WorkspaceAssemblyService

M7: Workspace / Group Assembly

工作空间组装服务，负责编排组装流程并返回结果。

职责：
- 接收 WorkspaceAssemblyInput
- 调用 WorkspaceAssembler 执行组装
- 汇总并返回 WorkspaceAssemblyResult

不负责：
- 具体组装逻辑
- 任务执行
- 线程管理
"""

from __future__ import annotations

from src.domain.services.workspace_assembler import WorkspaceAssembler
from src.domain.models.workspace_assembly_input import WorkspaceAssemblyInput
from src.domain.models.workspace_assembly_result import WorkspaceAssemblyResult


class WorkspaceAssemblyService:
    """
    工作空间组装服务

    负责编排工作空间组装流程，调用 WorkspaceAssembler 执行组装，
    并返回完整的 WorkspaceAssemblyResult。

    Fields:
        assembler: WorkspaceAssembler 实例
    """

    def __init__(self, assembler: WorkspaceAssembler):
        """
        初始化 WorkspaceAssemblyService

        Args:
            assembler: WorkspaceAssembler 实例，用于执行工作空间组装
        """
        self._assembler = assembler

    @property
    def assembler(self) -> WorkspaceAssembler:
        """获取 assembler"""
        return self._assembler

    def assemble(self, input_data: WorkspaceAssemblyInput) -> WorkspaceAssemblyResult:
        """
        执行工作空间组装

        Args:
            input_data: 组装输入，包含 TaskSpec、PlanDraft、TeamSpec、CandidateBundle 和提示

        Returns:
            WorkspaceAssemblyResult: 组装结果，包含 Workspace、警告、错误、解释和挂载信息
        """
        # 直接调用 assembler 执行组装
        # assembler 负责所有的组装、挂载和解释生成
        result = self._assembler.assemble(input_data)

        return result


__all__ = ["WorkspaceAssemblyService"]