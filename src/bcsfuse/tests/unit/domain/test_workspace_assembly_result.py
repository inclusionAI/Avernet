"""
Tests for WorkspaceAssemblyResult Domain Model

M7: Workspace / Group Assembly

测试 WorkspaceAssemblyResult 模型的构造、字段校验和行为。
"""

from __future__ import annotations

from datetime import datetime

import pytest

from src.domain.models.workspace_assembly_result import (
    WorkspaceAssemblyResult,
    AssemblyExplanation,
    AssemblyWarning,
    AssemblyError,
    MountInfo,
)
from src.domain.models.workspace import Workspace, WorkspaceStatus
from src.domain.models.team_spec import TeamSpec, RoleAssignment


# =============================================================================
# Fixtures
# =============================================================================

@pytest.fixture
def sample_team_spec() -> TeamSpec:
    """示例 TeamSpec"""
    return TeamSpec(
        team_id="team_architecture_001",
        members=["wrk_architect_001"],
        role_assignments=[
            RoleAssignment(
                worker_id="wrk_architect_001",
                role="architect",
                objective="Design architecture",
            ),
        ],
        selected_skills=["web_search"],
        selected_resources=["res_wiki_001"],
        composition_rationale=["Best match"],
        gaps=[],
    )


@pytest.fixture
def sample_workspace(sample_team_spec: TeamSpec) -> Workspace:
    """示例 Workspace"""
    return Workspace(
        id="wsp_001",
        task_id="tsk_001",
        team_spec=sample_team_spec,
        knowledge_mounts=["kno_001"],
        resource_mounts=["res_001"],
        status=WorkspaceStatus.ASSEMBLED,
    )


# =============================================================================
# MountInfo Tests
# =============================================================================

class TestMountInfo:
    """MountInfo 测试"""

    def test_create_mount_info(self):
        """测试创建挂载信息"""
        mount = MountInfo(
            id="res_001",
            type="resource",
            mount_reason="Required for task",
        )

        assert mount.id == "res_001"
        assert mount.type == "resource"
        assert mount.mount_reason == "Required for task"

    def test_mount_info_default_values(self):
        """测试默认值"""
        mount = MountInfo(
            id="kno_001",
            type="knowledge",
        )

        assert mount.mount_reason is None
        assert mount.custom_path is None

    def test_mount_info_with_custom_path(self):
        """测试自定义路径"""
        mount = MountInfo(
            id="res_001",
            type="resource",
            custom_path="/data/external",
        )

        assert mount.custom_path == "/data/external"

    def test_mount_info_extra_fields_forbidden(self):
        """测试禁止额外字段"""
        with pytest.raises(Exception):  # ValidationError
            MountInfo(
                id="res_001",
                type="resource",
                extra_field="invalid",  # type: ignore
            )


# =============================================================================
# AssemblyExplanation Tests
# =============================================================================

class TestAssemblyExplanation:
    """AssemblyExplanation 测试"""

    def test_create_explanation(self):
        """测试创建解释"""
        explanation = AssemblyExplanation(
            subject="workspace_assembly",
            description="Created workspace with 2 members",
        )

        assert explanation.subject == "workspace_assembly"
        assert explanation.description == "Created workspace with 2 members"

    def test_explanation_default_values(self):
        """测试默认值"""
        explanation = AssemblyExplanation(
            subject="test",
            description="Test",
        )

        assert explanation.details == {}

    def test_explanation_with_details(self):
        """测试带详情的解释"""
        explanation = AssemblyExplanation(
            subject="knowledge_mount",
            description="Mounted 3 knowledge items",
            details={"count": 3, "sources": ["wiki", "docs"]},
        )

        assert explanation.details["count"] == 3
        assert "wiki" in explanation.details["sources"]

    def test_explanation_extra_fields_forbidden(self):
        """测试禁止额外字段"""
        with pytest.raises(Exception):  # ValidationError
            AssemblyExplanation(
                subject="test",
                description="Test",
                extra_field="invalid",  # type: ignore
            )


# =============================================================================
# AssemblyWarning Tests
# =============================================================================

class TestAssemblyWarning:
    """AssemblyWarning 测试"""

    def test_create_warning(self):
        """测试创建警告"""
        warning = AssemblyWarning(
            code="INCOMPLETE_MOUNTS",
            message="Some resources could not be mounted",
            details={"missing": ["res_001"]},
        )

        assert warning.code == "INCOMPLETE_MOUNTS"
        assert warning.message == "Some resources could not be mounted"
        assert warning.details == {"missing": ["res_001"]}

    def test_warning_default_details(self):
        """测试默认详情"""
        warning = AssemblyWarning(
            code="TEST",
            message="Test warning",
        )

        assert warning.details == {}

    def test_warning_extra_fields_forbidden(self):
        """测试禁止额外字段"""
        with pytest.raises(Exception):  # ValidationError
            AssemblyWarning(
                code="TEST",
                message="Test",
                extra_field="invalid",  # type: ignore
            )


# =============================================================================
# AssemblyError Tests
# =============================================================================

class TestAssemblyError:
    """AssemblyError 测试"""

    def test_create_error(self):
        """测试创建错误"""
        error = AssemblyError(
            code="NO_TEAM_SPEC",
            message="TeamSpec is required for assembly",
            details={"task_id": "tsk_001"},
        )

        assert error.code == "NO_TEAM_SPEC"
        assert error.message == "TeamSpec is required for assembly"
        assert error.details == {"task_id": "tsk_001"}

    def test_error_default_details(self):
        """测试默认详情"""
        error = AssemblyError(
            code="TEST",
            message="Test error",
        )

        assert error.details == {}

    def test_error_extra_fields_forbidden(self):
        """测试禁止额外字段"""
        with pytest.raises(Exception):  # ValidationError
            AssemblyError(
                code="TEST",
                message="Test",
                extra_field="invalid",  # type: ignore
            )


# =============================================================================
# WorkspaceAssemblyResult Tests
# =============================================================================

class TestWorkspaceAssemblyResult:
    """WorkspaceAssemblyResult 测试"""

    def test_create_result_with_workspace(
        self,
        sample_workspace: Workspace,
    ):
        """测试创建成功的结果"""
        result = WorkspaceAssemblyResult(
            workspace=sample_workspace,
        )

        assert result.workspace == sample_workspace
        assert result.warnings == []
        assert result.errors == []
        assert result.explanations == []
        assert result.mount_info == []
        assert result.is_success is True

    def test_result_with_warnings(
        self,
        sample_workspace: Workspace,
    ):
        """测试带警告的结果"""
        warnings = [
            AssemblyWarning(
                code="INCOMPLETE_MOUNTS",
                message="Some items not mounted",
            )
        ]

        result = WorkspaceAssemblyResult(
            workspace=sample_workspace,
            warnings=warnings,
        )

        assert len(result.warnings) == 1
        assert result.is_success is True

    def test_result_with_errors(self):
        """测试带错误的结果"""
        errors = [
            AssemblyError(
                code="ASSEMBLY_FAILED",
                message="Could not assemble workspace",
            )
        ]

        result = WorkspaceAssemblyResult(
            workspace=None,
            errors=errors,
        )

        assert result.workspace is None
        assert len(result.errors) == 1
        assert result.is_success is False

    def test_result_with_explanations(
        self,
        sample_workspace: Workspace,
    ):
        """测试带解释的结果"""
        explanations = [
            AssemblyExplanation(
                subject="assembly_complete",
                description="Workspace assembled successfully",
            )
        ]

        result = WorkspaceAssemblyResult(
            workspace=sample_workspace,
            explanations=explanations,
        )

        assert len(result.explanations) == 1

    def test_result_with_mount_info(
        self,
        sample_workspace: Workspace,
    ):
        """测试带挂载信息的结果"""
        mount_info = [
            MountInfo(
                id="kno_001",
                type="knowledge",
                mount_reason="Required for task",
            ),
            MountInfo(
                id="res_001",
                type="resource",
                mount_reason="Selected by team",
            ),
        ]

        result = WorkspaceAssemblyResult(
            workspace=sample_workspace,
            mount_info=mount_info,
        )

        assert len(result.mount_info) == 2

    def test_result_default_values(self):
        """测试默认值"""
        result = WorkspaceAssemblyResult(workspace=None)

        assert result.workspace is None
        assert result.warnings == []
        assert result.errors == []
        assert result.explanations == []
        assert result.mount_info == []
        assert result.is_success is False

    def test_result_extra_fields_forbidden(
        self,
        sample_workspace: Workspace,
    ):
        """测试禁止额外字段"""
        with pytest.raises(Exception):  # ValidationError
            WorkspaceAssemblyResult(
                workspace=sample_workspace,
                extra_field="invalid",  # type: ignore
            )


# =============================================================================
# IsSuccess Logic Tests
# =============================================================================

class TestWorkspaceAssemblyResultIsSuccess:
    """is_success 逻辑测试"""

    def test_success_with_workspace(
        self,
        sample_workspace: Workspace,
    ):
        """测试有 Workspace 时为成功"""
        result = WorkspaceAssemblyResult(workspace=sample_workspace)

        assert result.is_success is True

    def test_failure_without_workspace(self):
        """测试无 Workspace 时为失败"""
        result = WorkspaceAssemblyResult(workspace=None)

        assert result.is_success is False

    def test_failure_with_errors(
        self,
        sample_workspace: Workspace,
    ):
        """测试有错误时为失败"""
        result = WorkspaceAssemblyResult(
            workspace=sample_workspace,
            errors=[
                AssemblyError(code="ERR", message="Error")
            ],
        )

        assert result.is_success is False

    def test_success_with_warnings(
        self,
        sample_workspace: Workspace,
    ):
        """测试有警告但仍成功"""
        result = WorkspaceAssemblyResult(
            workspace=sample_workspace,
            warnings=[
                AssemblyWarning(code="WARN", message="Warning")
            ],
        )

        assert result.is_success is True


# =============================================================================
# Edge Case Tests
# =============================================================================

class TestWorkspaceAssemblyResultEdgeCases:
    """边界情况测试"""

    def test_empty_workspace(
        self,
        sample_team_spec: TeamSpec,
    ):
        """测试空 Workspace（最小有效）"""
        workspace = Workspace(
            id="wsp_minimal",
            task_id="tsk_001",
            team_spec=sample_team_spec,
            status=WorkspaceStatus.DRAFT,
        )

        result = WorkspaceAssemblyResult(workspace=workspace)

        assert result.is_success is True
        assert len(result.workspace.knowledge_mounts) == 0
        assert len(result.workspace.resource_mounts) == 0

    def test_multiple_warnings_and_errors(self):
        """测试多个警告和错误"""
        result = WorkspaceAssemblyResult(
            workspace=None,
            warnings=[
                AssemblyWarning(code="WARN1", message="Warning 1"),
                AssemblyWarning(code="WARN2", message="Warning 2"),
            ],
            errors=[
                AssemblyError(code="ERR1", message="Error 1"),
                AssemblyError(code="ERR2", message="Error 2"),
            ],
        )

        assert len(result.warnings) == 2
        assert len(result.errors) == 2
        assert result.is_success is False


# =============================================================================
# Schema Alignment Tests
# =============================================================================

class TestWorkspaceAssemblyResultSchemaAlignment:
    """一致性测试"""

    def test_result_has_required_fields(
        self,
        sample_workspace: Workspace,
    ):
        """测试结果有必需字段"""
        result = WorkspaceAssemblyResult(workspace=sample_workspace)

        assert hasattr(result, "workspace")
        assert hasattr(result, "warnings")
        assert hasattr(result, "errors")
        assert hasattr(result, "explanations")
        assert hasattr(result, "mount_info")
        assert hasattr(result, "is_success")

    def test_mount_info_has_required_fields(self):
        """测试 MountInfo 有必需字段"""
        mount = MountInfo(id="test", type="resource")

        assert hasattr(mount, "id")
        assert hasattr(mount, "type")
        assert hasattr(mount, "mount_reason")
        assert hasattr(mount, "custom_path")