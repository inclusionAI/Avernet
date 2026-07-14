"""
BaselineWorkspaceAssembler

M7: Workspace / Group Assembly

基准工作空间组装器实现。

职责：
- 从 WorkspaceAssemblyInput 组装 Workspace
- 挂载知识项（根据 hints.include_all_knowledge）
- 挂载资源（根据 hints.include_all_resources）
- 生成初始事件
- 生成解释和挂载信息

不负责：
- 线程创建（M8+）
- 任务执行调度
- Handoff 逻辑
"""

from __future__ import annotations

import uuid
from datetime import datetime

from src.domain.models.workspace import Workspace, WorkspaceStatus, WorkspaceEvent
from src.domain.models.workspace_assembly_input import WorkspaceAssemblyInput, AssemblyHints
from src.domain.models.workspace_assembly_result import (
    WorkspaceAssemblyResult,
    AssemblyExplanation,
    MountInfo,
)
from src.domain.services.workspace_assembler import WorkspaceAssembler


class BaselineWorkspaceAssembler(WorkspaceAssembler):
    """
    基准工作空间组装器

    实现基础的 Workspace 组装逻辑。
    """

    def assemble(self, input_data: WorkspaceAssemblyInput) -> WorkspaceAssemblyResult:
        """
        执行工作空间组装

        Args:
            input_data: 组装输入，包含 TaskSpec、PlanDraft、TeamSpec、CandidateBundle 和提示

        Returns:
            WorkspaceAssemblyResult: 组装结果
        """
        hints = input_data.hints
        explanations: list[AssemblyExplanation] = []
        mount_info: list[MountInfo] = []

        # 1. 收集知识挂载
        knowledge_mounts = self._collect_knowledge_mounts(
            input_data, hints, mount_info, explanations
        )

        # 2. 收集资源挂载
        resource_mounts = self._collect_resource_mounts(
            input_data, hints, mount_info, explanations
        )

        # 3. 生成工作空间 ID
        workspace_id = self._generate_workspace_id()

        # 4. 生成初始事件
        now = datetime.now()
        initial_event = WorkspaceEvent(
            type="workspace_created",
            at=now,
            payload={
                "task_id": input_data.task_spec.id,
                "team_id": input_data.team_spec.team_id,
                "assembled_at": now.isoformat(),
            },
        )

        # 5. 创建 Workspace
        workspace = Workspace(
            id=workspace_id,
            task_id=input_data.task_spec.id,
            team_spec=input_data.team_spec,
            knowledge_mounts=knowledge_mounts,
            resource_mounts=resource_mounts,
            artifacts=[],
            events=[initial_event],
            status=WorkspaceStatus.ASSEMBLED,
        )

        # 6. 添加组装完成解释
        explanations.append(
            AssemblyExplanation(
                subject="assembly_complete",
                description="Workspace assembled successfully",
                details={
                    "workspace_id": workspace_id,
                    "knowledge_count": len(knowledge_mounts),
                    "resource_count": len(resource_mounts),
                    "team_size": len(input_data.team_spec.members),
                },
            )
        )

        return WorkspaceAssemblyResult(
            workspace=workspace,
            warnings=[],
            errors=[],
            explanations=explanations,
            mount_info=mount_info,
        )

    def _generate_workspace_id(self) -> str:
        """生成工作空间 ID"""
        unique_id = uuid.uuid4().hex[:12]
        return f"wsp_{unique_id}"

    def _collect_knowledge_mounts(
        self,
        input_data: WorkspaceAssemblyInput,
        hints: AssemblyHints,
        mount_info: list[MountInfo],
        explanations: list[AssemblyExplanation],
    ) -> list[str]:
        """
        收集知识挂载点

        根据 hints.include_all_knowledge 决定是否挂载所有知识项。
        """
        if not hints.include_all_knowledge:
            explanations.append(
                AssemblyExplanation(
                    subject="knowledge_mounts",
                    description="Knowledge mounting skipped per hints",
                    details={"include_all_knowledge": False},
                )
            )
            return []

        knowledge_ids: list[str] = []
        bundle = input_data.candidate_bundle

        for item in bundle.knowledge_items:
            knowledge_ids.append(item.id)
            mount_info.append(
                MountInfo(
                    id=item.id,
                    type="knowledge",
                    mount_reason=f"Knowledge item from candidate bundle: {item.title}",
                )
            )

        if knowledge_ids:
            explanations.append(
                AssemblyExplanation(
                    subject="knowledge_mounts",
                    description=f"Mounted {len(knowledge_ids)} knowledge items",
                    details={"count": len(knowledge_ids), "ids": knowledge_ids},
                )
            )

        return knowledge_ids

    def _collect_resource_mounts(
        self,
        input_data: WorkspaceAssemblyInput,
        hints: AssemblyHints,
        mount_info: list[MountInfo],
        explanations: list[AssemblyExplanation],
    ) -> list[str]:
        """
        收集资源挂载点

        优先从 CandidateBundle 挂载，其次从 TeamSpec.selected_resources 挂载。
        """
        resource_ids: list[str] = []
        seen_ids: set[str] = set()
        bundle = input_data.candidate_bundle

        # 1. 从 CandidateBundle 挂载资源
        if hints.include_all_resources:
            for resource in bundle.resources:
                if resource.id not in seen_ids:
                    resource_ids.append(resource.id)
                    seen_ids.add(resource.id)
                    mount_info.append(
                        MountInfo(
                            id=resource.id,
                            type="resource",
                            mount_reason=f"Resource from candidate bundle: {resource.name}",
                        )
                    )

        # 2. 从 TeamSpec.selected_resources 挂载资源
        for resource_id in input_data.team_spec.selected_resources:
            if resource_id not in seen_ids:
                resource_ids.append(resource_id)
                seen_ids.add(resource_id)
                mount_info.append(
                    MountInfo(
                        id=resource_id,
                        type="resource",
                        mount_reason="Resource selected by team composition",
                    )
                )

        if resource_ids:
            explanations.append(
                AssemblyExplanation(
                    subject="resource_mounts",
                    description=f"Mounted {len(resource_ids)} resources",
                    details={"count": len(resource_ids), "ids": resource_ids},
                )
            )

        return resource_ids


__all__ = ["BaselineWorkspaceAssembler"]