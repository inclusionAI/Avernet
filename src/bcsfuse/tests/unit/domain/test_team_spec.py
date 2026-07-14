"""
Tests for TeamSpec Domain Model

M6: Team Composer / Matchmaker

测试 TeamSpec 模型的构造、字段校验和行为。
"""

from __future__ import annotations

import pytest

from src.domain.models.team_spec import TeamSpec, RoleAssignment


# =============================================================================
# RoleAssignment Tests
# =============================================================================

class TestRoleAssignment:
    """RoleAssignment 测试"""

    def test_create_role_assignment(self):
        """测试创建角色分配"""
        assignment = RoleAssignment(
            worker_id="wrk_architect_001",
            role="architect",
            objective="Design system architecture",
        )

        assert assignment.worker_id == "wrk_architect_001"
        assert assignment.role == "architect"
        assert assignment.objective == "Design system architecture"

    def test_role_assignment_required_fields(self):
        """测试角色分配的必填字段"""
        with pytest.raises(Exception):  # ValidationError
            RoleAssignment(worker_id="wrk_001")  # type: ignore

        with pytest.raises(Exception):  # ValidationError
            RoleAssignment(worker_id="wrk_001", role="architect")  # type: ignore

    def test_role_assignment_extra_fields_forbidden(self):
        """测试角色分配禁止额外字段"""
        with pytest.raises(Exception):  # ValidationError
            RoleAssignment(
                worker_id="wrk_001",
                role="architect",
                objective="Design",
                extra_field="invalid",  # type: ignore
            )


# =============================================================================
# TeamSpec Tests
# =============================================================================

class TestTeamSpec:
    """TeamSpec 测试"""

    def test_create_team_spec_with_required_fields(self):
        """测试使用必填字段创建 TeamSpec"""
        team = TeamSpec(
            team_id="team_001",
            members=["wrk_architect_001", "wrk_developer_001"],
            role_assignments=[
                RoleAssignment(
                    worker_id="wrk_architect_001",
                    role="architect",
                    objective="Design architecture",
                ),
                RoleAssignment(
                    worker_id="wrk_developer_001",
                    role="developer",
                    objective="Implement features",
                ),
            ],
            composition_rationale=["Selected architect for design expertise"],
        )

        assert team.team_id == "team_001"
        assert len(team.members) == 2
        assert len(team.role_assignments) == 2

    def test_team_spec_with_selected_skills(self):
        """测试 TeamSpec 包含选中的技能"""
        team = TeamSpec(
            team_id="team_001",
            members=["wrk_001"],
            role_assignments=[
                RoleAssignment(worker_id="wrk_001", role="developer", objective="Code"),
            ],
            selected_skills=["web_search", "code_generator"],
            composition_rationale=["Has required skills"],
        )

        assert team.selected_skills == ["web_search", "code_generator"]

    def test_team_spec_with_selected_resources(self):
        """测试 TeamSpec 包含选中的资源"""
        team = TeamSpec(
            team_id="team_001",
            members=["wrk_001"],
            role_assignments=[
                RoleAssignment(worker_id="wrk_001", role="developer", objective="Code"),
            ],
            selected_resources=["res_wiki_001", "res_repo_001"],
            composition_rationale=["Has resource access"],
        )

        assert team.selected_resources == ["res_wiki_001", "res_repo_001"]

    def test_team_spec_with_gaps(self):
        """测试 TeamSpec 包含缺口"""
        team = TeamSpec(
            team_id="team_001",
            members=["wrk_001"],
            role_assignments=[
                RoleAssignment(worker_id="wrk_001", role="developer", objective="Code"),
            ],
            composition_rationale=["Partial coverage"],
            gaps=["Missing security reviewer", "No production DB access"],
        )

        assert len(team.gaps) == 2
        assert "Missing security reviewer" in team.gaps

    def test_team_spec_default_values(self):
        """测试 TeamSpec 默认值"""
        team = TeamSpec(
            team_id="team_001",
            members=["wrk_001"],
            role_assignments=[
                RoleAssignment(worker_id="wrk_001", role="dev", objective="Code"),
            ],
            composition_rationale=["Minimal team"],
        )

        assert team.selected_skills == []
        assert team.selected_resources == []
        assert team.gaps == []

    def test_team_spec_team_id_pattern(self):
        """测试 team_id 模式校验"""
        # Valid patterns
        team = TeamSpec(
            team_id="team_abc123",
            members=["wrk_001"],
            role_assignments=[
                RoleAssignment(worker_id="wrk_001", role="dev", objective="Code"),
            ],
            composition_rationale=["Valid ID"],
        )
        assert team.team_id == "team_abc123"

        # Invalid pattern
        with pytest.raises(Exception):  # ValidationError
            TeamSpec(
                team_id="invalid_id",  # Missing 'team_' prefix
                members=["wrk_001"],
                role_assignments=[
                    RoleAssignment(worker_id="wrk_001", role="dev", objective="Code"),
                ],
                composition_rationale=["Invalid ID"],
            )

    def test_team_spec_requires_at_least_one_member(self):
        """测试 TeamSpec 至少需要一个成员"""
        with pytest.raises(Exception):  # ValidationError
            TeamSpec(
                team_id="team_001",
                members=[],  # Empty members
                role_assignments=[],
                composition_rationale=["Empty team"],
            )

    def test_team_spec_requires_at_least_one_role_assignment(self):
        """测试 TeamSpec 至少需要一个角色分配"""
        with pytest.raises(Exception):  # ValidationError
            TeamSpec(
                team_id="team_001",
                members=["wrk_001"],
                role_assignments=[],  # Empty role assignments
                composition_rationale=["No roles"],
            )

    def test_team_spec_requires_at_least_one_rationale(self):
        """测试 TeamSpec 至少需要一个 rationale"""
        with pytest.raises(Exception):  # ValidationError
            TeamSpec(
                team_id="team_001",
                members=["wrk_001"],
                role_assignments=[
                    RoleAssignment(worker_id="wrk_001", role="dev", objective="Code"),
                ],
                composition_rationale=[],  # Empty rationale
            )

    def test_team_spec_extra_fields_forbidden(self):
        """测试 TeamSpec 禁止额外字段"""
        with pytest.raises(Exception):  # ValidationError
            TeamSpec(
                team_id="team_001",
                members=["wrk_001"],
                role_assignments=[
                    RoleAssignment(worker_id="wrk_001", role="dev", objective="Code"),
                ],
                composition_rationale=["Minimal team"],
                extra_field="invalid",  # type: ignore
            )


# =============================================================================
# Schema Alignment Tests
# =============================================================================

class TestTeamSpecSchemaAlignment:
    """TeamSpec 与 schema 一致性测试"""

    def test_team_spec_has_required_fields(self):
        """测试 TeamSpec 有 schema 定义的必需字段"""
        team = TeamSpec(
            team_id="team_001",
            members=["wrk_001"],
            role_assignments=[
                RoleAssignment(worker_id="wrk_001", role="dev", objective="Code"),
            ],
            composition_rationale=["Test"],
        )

        # Schema required fields
        assert hasattr(team, "team_id")
        assert hasattr(team, "members")
        assert hasattr(team, "role_assignments")
        assert hasattr(team, "selected_skills")
        assert hasattr(team, "selected_resources")
        assert hasattr(team, "composition_rationale")
        assert hasattr(team, "gaps")

    def test_team_spec_field_types_match_schema(self):
        """测试 TeamSpec 字段类型与 schema 匹配"""
        team = TeamSpec(
            team_id="team_001",
            members=["wrk_001"],
            role_assignments=[
                RoleAssignment(worker_id="wrk_001", role="dev", objective="Code"),
            ],
            selected_skills=["skill_1"],
            selected_resources=["res_1"],
            composition_rationale=["Test"],
            gaps=["gap_1"],
        )

        assert isinstance(team.team_id, str)
        assert isinstance(team.members, list)
        assert isinstance(team.role_assignments, list)
        assert isinstance(team.selected_skills, list)
        assert isinstance(team.selected_resources, list)
        assert isinstance(team.composition_rationale, list)
        assert isinstance(team.gaps, list)


# =============================================================================
# Integration Tests
# =============================================================================

class TestTeamSpecIntegration:
    """TeamSpec 集成测试"""

    def test_team_spec_represents_composition_result(self):
        """测试 TeamSpec 表示组合结果"""
        # 模拟一个完整的团队组合结果
        team = TeamSpec(
            team_id="team_architecture_001",
            members=["wrk_architect_001", "wrk_developer_001", "wrk_reviewer_001"],
            role_assignments=[
                RoleAssignment(
                    worker_id="wrk_architect_001",
                    role="architect",
                    objective="Design overall system architecture",
                ),
                RoleAssignment(
                    worker_id="wrk_developer_001",
                    role="developer",
                    objective="Implement core features",
                ),
                RoleAssignment(
                    worker_id="wrk_reviewer_001",
                    role="reviewer",
                    objective="Review code and architecture",
                ),
            ],
            selected_skills=["web_search", "code_generator"],
            selected_resources=["res_wiki_001", "res_repo_001"],
            composition_rationale=[
                "Architect has system_design capability at expert level",
                "Developer has coding capability at advanced level",
                "Reviewer provides code review capability",
            ],
            gaps=[
                "No dedicated security expert",
                "Missing production database access",
            ],
        )

        # Verification
        assert team.team_id == "team_architecture_001"
        assert len(team.members) == 3
        assert len(team.role_assignments) == 3
        assert len(team.selected_skills) == 2
        assert len(team.selected_resources) == 2
        assert len(team.composition_rationale) == 3
        assert len(team.gaps) == 2

    def test_role_assignments_match_members(self):
        """测试角色分配与成员匹配"""
        members = ["wrk_001", "wrk_002"]
        role_assignments = [
            RoleAssignment(worker_id="wrk_001", role="dev", objective="Code"),
            RoleAssignment(worker_id="wrk_002", role="reviewer", objective="Review"),
        ]

        team = TeamSpec(
            team_id="team_001",
            members=members,
            role_assignments=role_assignments,
            composition_rationale=["Test"],
        )

        # 每个 member 应该有对应的 role_assignment
        assigned_workers = {ra.worker_id for ra in team.role_assignments}
        for member in team.members:
            assert member in assigned_workers