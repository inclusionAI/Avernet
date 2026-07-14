"""
Tests for ExpertContextPack

Stage 3: Worker Profile-Driven Expert Execution Preparation

测试 G5 LLM 输入上下文模型。
"""

from __future__ import annotations

import pytest

from src.domain.models.expert_context_pack import ExpertContextPack


class TestExpertContextPackModel:
    """ExpertContextPack 模型测试"""

    def test_minimal_fields(self):
        """测试最小字段"""
        pack = ExpertContextPack(
            question="How to optimize database queries?",
            expert_id="staff_001:default",
            profile_key="staff_001:default:2026-03-23",
            domain="database",
            expertise_summary="Expert in database optimization",
            relevant_skills=["SQL", "Index Optimization"],
            context_highlights=["Has experience with MySQL"],
            task_context="Optimize slow queries in production",
        )

        assert pack.question == "How to optimize database queries?"
        assert pack.expert_id == "staff_001:default"
        assert pack.profile_key == "staff_001:default:2026-03-23"
        assert pack.domain == "database"
        assert pack.expertise_summary == "Expert in database optimization"
        assert pack.relevant_skills == ["SQL", "Index Optimization"]
        assert pack.context_highlights == ["Has experience with MySQL"]
        assert pack.task_context == "Optimize slow queries in production"

    def test_all_fields_optional_defaults(self):
        """测试可选字段默认值"""
        pack = ExpertContextPack(
            question="Test question",
            expert_id="staff_001:default",
            profile_key="staff_001:default:2026-03-23",
            domain="tech",
            expertise_summary="Expert summary",
            relevant_skills=[],
            context_highlights=[],
            task_context="",
        )

        assert pack.relevant_skills == []
        assert pack.context_highlights == []
        assert pack.task_context == ""

    def test_expert_id_format(self):
        """测试 expert_id 格式"""
        # 标准 format
        pack = ExpertContextPack(
            question="Test",
            expert_id="staff_001:default",
            profile_key="staff_001:default:2026-03-23",
            domain="tech",
            expertise_summary="Summary",
            relevant_skills=[],
            context_highlights=[],
            task_context="",
        )
        assert "staff_001" in pack.expert_id
        assert "default" in pack.expert_id

    def test_profile_key_traceability(self):
        """测试 profile_key 可追溯性"""
        pack = ExpertContextPack(
            question="Test",
            expert_id="staff_001:default",
            profile_key="staff_001:default:2026-03-23:v1",
            domain="tech",
            expertise_summary="Summary",
            relevant_skills=[],
            context_highlights=[],
            task_context="",
        )

        # profile_key 应包含 expert_id 信息
        assert "staff_001" in pack.profile_key
        assert "default" in pack.profile_key

    def test_domain_values(self):
        """测试 domain 字段"""
        valid_domains = ["security", "legal", "database", "ops", "tech", "architecture"]

        for domain in valid_domains:
            pack = ExpertContextPack(
                question="Test",
                expert_id="staff_001:default",
                profile_key="staff_001:default:2026-03-23",
                domain=domain,
                expertise_summary="Summary",
                relevant_skills=[],
                context_highlights=[],
                task_context="",
            )
            assert pack.domain == domain

    def test_relevant_skills_list(self):
        """测试 relevant_skills 列表"""
        pack = ExpertContextPack(
            question="Test",
            expert_id="staff_001:default",
            profile_key="staff_001:default:2026-03-23",
            domain="tech",
            expertise_summary="Summary",
            relevant_skills=["Python", "FastAPI", "PostgreSQL"],
            context_highlights=[],
            task_context="",
        )

        assert len(pack.relevant_skills) == 3
        assert "Python" in pack.relevant_skills

    def test_context_highlights_list(self):
        """测试 context_highlights 列表"""
        pack = ExpertContextPack(
            question="Test",
            expert_id="staff_001:default",
            profile_key="staff_001:default:2026-03-23",
            domain="tech",
            expertise_summary="Summary",
            relevant_skills=[],
            context_highlights=[
                "5 years of experience in Python",
                "Contributed to FastAPI project",
                "Expert in API design",
            ],
            task_context="",
        )

        assert len(pack.context_highlights) == 3

    def test_model_dump(self):
        """测试 model_dump 序列化"""
        pack = ExpertContextPack(
            question="Test question",
            expert_id="staff_001:default",
            profile_key="staff_001:default:2026-03-23",
            domain="tech",
            expertise_summary="Expert summary",
            relevant_skills=["Python"],
            context_highlights=["Highlight 1"],
            task_context="Task context",
        )

        data = pack.model_dump()

        assert data["question"] == "Test question"
        assert data["expert_id"] == "staff_001:default"
        assert data["profile_key"] == "staff_001:default:2026-03-23"
        assert data["domain"] == "tech"
        assert data["relevant_skills"] == ["Python"]

    def test_model_validate(self):
        """测试 model_validate 反序列化"""
        data = {
            "question": "Test question",
            "expert_id": "staff_001:default",
            "profile_key": "staff_001:default:2026-03-23",
            "domain": "tech",
            "expertise_summary": "Expert summary",
            "relevant_skills": ["Python"],
            "context_highlights": ["Highlight 1"],
            "task_context": "Task context",
        }

        pack = ExpertContextPack.model_validate(data)

        assert pack.question == "Test question"
        assert pack.expert_id == "staff_001:default"
        assert pack.relevant_skills == ["Python"]

    def test_extra_fields_forbidden(self):
        """测试额外字段被禁止"""
        with pytest.raises(Exception):
            ExpertContextPack(
                question="Test",
                expert_id="staff_001:default",
                profile_key="staff_001:default:2026-03-23",
                domain="tech",
                expertise_summary="Summary",
                relevant_skills=[],
                context_highlights=[],
                task_context="",
                unknown_field="should fail",  # type: ignore
            )