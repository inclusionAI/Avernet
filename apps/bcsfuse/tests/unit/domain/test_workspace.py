"""
Tests for Workspace Domain Model

M7: Workspace / Group Assembly

测试 Workspace 模型的构造、字段校验和行为。
"""

from __future__ import annotations

from datetime import datetime

import pytest

from src.domain.models.workspace import Workspace, WorkspaceStatus, WorkspaceEvent
from src.domain.models.team_spec import TeamSpec, RoleAssignment


# =============================================================================
# Fixtures
# =============================================================================

@pytest.fixture
def sample_team_spec() -> TeamSpec:
    """示例 TeamSpec"""
    return TeamSpec(
        team_id="team_architecture_001",
        members=["wrk_architect_001", "wrk_developer_001"],
        role_assignments=[
            RoleAssignment(
                worker_id="wrk_architect_001",
                role="architect",
                objective="Design system architecture",
            ),
            RoleAssignment(
                worker_id="wrk_developer_001",
                role="developer",
                objective="Implement features",
            ),
        ],
        selected_skills=["web_search"],
        selected_resources=["res_wiki_001"],
        composition_rationale=["Best match for the task"],
        gaps=["Missing security reviewer"],
    )


@pytest.fixture
def sample_workspace_event() -> WorkspaceEvent:
    """示例 WorkspaceEvent"""
    return WorkspaceEvent(
        type="workspace_created",
        at=datetime(2026, 3, 21, 10, 0, 0),
        payload={"source": "assembly"},
    )


# =============================================================================
# WorkspaceStatus Tests
# =============================================================================

class TestWorkspaceStatus:
    """WorkspaceStatus 枚举测试"""

    def test_status_values(self):
        """测试状态枚举值"""
        assert WorkspaceStatus.DRAFT == "draft"
        assert WorkspaceStatus.ASSEMBLED == "assembled"
        assert WorkspaceStatus.HANDED_OFF == "handed_off"
        assert WorkspaceStatus.CLOSED == "closed"

    def test_status_is_string_enum(self):
        """测试状态是字符串枚举"""
        assert isinstance(WorkspaceStatus.DRAFT, str)
        assert WorkspaceStatus.DRAFT.value == "draft"

    def test_all_required_statuses_exist(self):
        """测试所有必需状态存在"""
        required_statuses = ["draft", "assembled", "handed_off", "closed"]
        for status in required_statuses:
            assert any(s.value == status for s in WorkspaceStatus)


# =============================================================================
# WorkspaceEvent Tests
# =============================================================================

class TestWorkspaceEvent:
    """WorkspaceEvent 测试"""

    def test_create_event_with_required_fields(self):
        """测试创建事件"""
        event = WorkspaceEvent(
            type="member_joined",
            at=datetime.now(),
        )

        assert event.type == "member_joined"
        assert event.at is not None
        assert event.payload == {}

    def test_create_event_with_payload(self):
        """测试带载荷的事件"""
        event = WorkspaceEvent(
            type="resource_mounted",
            at=datetime.now(),
            payload={"resource_id": "res_001", "mount_path": "/data"},
        )

        assert event.type == "resource_mounted"
        assert event.payload["resource_id"] == "res_001"
        assert event.payload["mount_path"] == "/data"

    def test_event_extra_fields_forbidden(self):
        """测试禁止额外字段"""
        with pytest.raises(Exception):  # ValidationError
            WorkspaceEvent(
                type="test",
                at=datetime.now(),
                extra_field="invalid",  # type: ignore
            )

    def test_event_required_fields(self):
        """测试事件必填字段"""
        with pytest.raises(Exception):  # ValidationError
            WorkspaceEvent(type="test")  # type: ignore


# =============================================================================
# Workspace Tests
# =============================================================================

class TestWorkspace:
    """Workspace 测试"""

    def test_create_workspace_with_required_fields(
        self,
        sample_team_spec: TeamSpec,
    ):
        """测试创建 Workspace"""
        workspace = Workspace(
            id="wsp_001",
            task_id="tsk_001",
            team_spec=sample_team_spec,
            status=WorkspaceStatus.DRAFT,
        )

        assert workspace.id == "wsp_001"
        assert workspace.task_id == "tsk_001"
        assert workspace.team_spec == sample_team_spec
        assert workspace.status == "draft"

    def test_workspace_default_values(
        self,
        sample_team_spec: TeamSpec,
    ):
        """测试 Workspace 默认值"""
        workspace = Workspace(
            id="wsp_001",
            task_id="tsk_001",
            team_spec=sample_team_spec,
            status=WorkspaceStatus.DRAFT,
        )

        assert workspace.knowledge_mounts == []
        assert workspace.resource_mounts == []
        assert workspace.artifacts == []
        assert workspace.events == []

    def test_workspace_with_mounts(
        self,
        sample_team_spec: TeamSpec,
    ):
        """测试带挂载的 Workspace"""
        workspace = Workspace(
            id="wsp_001",
            task_id="tsk_001",
            team_spec=sample_team_spec,
            knowledge_mounts=["kno_001", "kno_002"],
            resource_mounts=["res_001", "res_002"],
            status=WorkspaceStatus.ASSEMBLED,
        )

        assert len(workspace.knowledge_mounts) == 2
        assert len(workspace.resource_mounts) == 2
        assert "kno_001" in workspace.knowledge_mounts

    def test_workspace_with_events(
        self,
        sample_team_spec: TeamSpec,
        sample_workspace_event: WorkspaceEvent,
    ):
        """测试带事件的 Workspace"""
        workspace = Workspace(
            id="wsp_001",
            task_id="tsk_001",
            team_spec=sample_team_spec,
            events=[sample_workspace_event],
            status=WorkspaceStatus.DRAFT,
        )

        assert len(workspace.events) == 1
        assert workspace.events[0].type == "workspace_created"

    def test_workspace_with_artifacts(
        self,
        sample_team_spec: TeamSpec,
    ):
        """测试带工件的 Workspace"""
        workspace = Workspace(
            id="wsp_001",
            task_id="tsk_001",
            team_spec=sample_team_spec,
            artifacts=["artifact_001", "artifact_002"],
            status=WorkspaceStatus.ASSEMBLED,
        )

        assert len(workspace.artifacts) == 2

    def test_workspace_id_pattern(
        self,
        sample_team_spec: TeamSpec,
    ):
        """测试 workspace ID 格式"""
        # Valid patterns
        valid_ids = ["wsp_001", "wsp_abc123", "wsp_test-workspace_001"]
        for wid in valid_ids:
            workspace = Workspace(
                id=wid,
                task_id="tsk_001",
                team_spec=sample_team_spec,
                status=WorkspaceStatus.DRAFT,
            )
            assert workspace.id == wid

        # Invalid pattern
        with pytest.raises(Exception):  # ValidationError
            Workspace(
                id="invalid_id",  # Missing wsp_ prefix
                task_id="tsk_001",
                team_spec=sample_team_spec,
                status=WorkspaceStatus.DRAFT,
            )

    def test_workspace_status_variations(
        self,
        sample_team_spec: TeamSpec,
    ):
        """测试不同状态的 Workspace"""
        for status in WorkspaceStatus:
            workspace = Workspace(
                id="wsp_001",
                task_id="tsk_001",
                team_spec=sample_team_spec,
                status=status,
            )
            assert workspace.status == status.value

    def test_workspace_extra_fields_forbidden(
        self,
        sample_team_spec: TeamSpec,
    ):
        """测试禁止额外字段"""
        with pytest.raises(Exception):  # ValidationError
            Workspace(
                id="wsp_001",
                task_id="tsk_001",
                team_spec=sample_team_spec,
                status=WorkspaceStatus.DRAFT,
                extra_field="invalid",  # type: ignore
            )

    def test_workspace_required_fields(self):
        """测试 Workspace 必填字段"""
        with pytest.raises(Exception):  # ValidationError
            Workspace(id="wsp_001")  # type: ignore


# =============================================================================
# Schema Alignment Tests
# =============================================================================

class TestWorkspaceSchemaAlignment:
    """Workspace 与 schema 一致性测试"""

    def test_workspace_has_required_fields(
        self,
        sample_team_spec: TeamSpec,
    ):
        """测试 Workspace 有 schema 定义的必需字段"""
        workspace = Workspace(
            id="wsp_001",
            task_id="tsk_001",
            team_spec=sample_team_spec,
            status=WorkspaceStatus.DRAFT,
        )

        # Schema required fields
        assert hasattr(workspace, "id")
        assert hasattr(workspace, "task_id")
        assert hasattr(workspace, "team_spec")
        assert hasattr(workspace, "knowledge_mounts")
        assert hasattr(workspace, "resource_mounts")
        assert hasattr(workspace, "artifacts")
        assert hasattr(workspace, "events")
        assert hasattr(workspace, "status")

    def test_workspace_field_types_match_schema(
        self,
        sample_team_spec: TeamSpec,
        sample_workspace_event: WorkspaceEvent,
    ):
        """测试 Workspace 字段类型与 schema 匹配"""
        workspace = Workspace(
            id="wsp_001",
            task_id="tsk_001",
            team_spec=sample_team_spec,
            knowledge_mounts=["kno_001"],
            resource_mounts=["res_001"],
            artifacts=["artifact_001"],
            events=[sample_workspace_event],
            status=WorkspaceStatus.ASSEMBLED,
        )

        assert isinstance(workspace.id, str)
        assert isinstance(workspace.task_id, str)
        assert isinstance(workspace.team_spec, TeamSpec)
        assert isinstance(workspace.knowledge_mounts, list)
        assert isinstance(workspace.resource_mounts, list)
        assert isinstance(workspace.artifacts, list)
        assert isinstance(workspace.events, list)
        assert isinstance(workspace.status, str)


# =============================================================================
# Integration Tests
# =============================================================================

class TestWorkspaceIntegration:
    """Workspace 集成测试"""

    def test_workspace_contains_full_team_spec(
        self,
        sample_team_spec: TeamSpec,
    ):
        """测试 Workspace 包含完整 TeamSpec"""
        workspace = Workspace(
            id="wsp_001",
            task_id="tsk_001",
            team_spec=sample_team_spec,
            status=WorkspaceStatus.ASSEMBLED,
        )

        # 验证可以通过 team_spec 获取团队信息
        assert workspace.team_spec.team_id == "team_architecture_001"
        assert len(workspace.team_spec.members) == 2
        assert len(workspace.team_spec.role_assignments) == 2

    def test_workspace_members_accessible_via_team_spec(
        self,
        sample_team_spec: TeamSpec,
    ):
        """测试成员通过 team_spec 访问"""
        workspace = Workspace(
            id="wsp_001",
            task_id="tsk_001",
            team_spec=sample_team_spec,
            status=WorkspaceStatus.ASSEMBLED,
        )

        # 成员来自 team_spec.members
        members = workspace.team_spec.members
        assert "wrk_architect_001" in members
        assert "wrk_developer_001" in members

    def test_workspace_selected_resources_mountable(
        self,
        sample_team_spec: TeamSpec,
    ):
        """测试 TeamSpec 中选中的资源可挂载"""
        workspace = Workspace(
            id="wsp_001",
            task_id="tsk_001",
            team_spec=sample_team_spec,
            resource_mounts=sample_team_spec.selected_resources,
            status=WorkspaceStatus.ASSEMBLED,
        )

        assert "res_wiki_001" in workspace.resource_mounts

    def test_workspace_selected_skills_recorded(
        self,
        sample_team_spec: TeamSpec,
    ):
        """测试 TeamSpec 中选中的技能可记录"""
        # 技能不直接在 Workspace schema 中，但可以通过 team_spec 访问
        workspace = Workspace(
            id="wsp_001",
            task_id="tsk_001",
            team_spec=sample_team_spec,
            status=WorkspaceStatus.ASSEMBLED,
        )

        assert "web_search" in workspace.team_spec.selected_skills

    def test_workspace_gaps_accessible(
        self,
        sample_team_spec: TeamSpec,
    ):
        """测试缺口可通过 team_spec 访问"""
        workspace = Workspace(
            id="wsp_001",
            task_id="tsk_001",
            team_spec=sample_team_spec,
            status=WorkspaceStatus.ASSEMBLED,
        )

        assert len(workspace.team_spec.gaps) > 0
        assert "Missing security reviewer" in workspace.team_spec.gaps