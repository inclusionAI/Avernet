"""
Tests for LLMExpertPerspective

Stage 3: Worker Profile-Driven Expert Execution Preparation

测试 LLM 生成的专家视角模型。
"""

from __future__ import annotations

import pytest

from src.domain.models.llm_expert_perspective import (
    LLMExpertPerspective,
    RiskLevelLiteral,
)


class TestLLMExpertPerspectiveModel:
    """LLMExpertPerspective 模型测试"""

    def test_minimal_fields(self):
        """测试最小字段"""
        perspective = LLMExpertPerspective(
            summary="This is an expert perspective on the question.",
            confidence=0.85,
            key_points=["Point 1", "Point 2"],
            concerns=["Concern 1"],
            risk_level="medium",
            rationale_summary="Based on the provided context and expertise.",
            evidence_summary=["Evidence 1", "Evidence 2"],
        )

        assert perspective.summary == "This is an expert perspective on the question."
        assert perspective.confidence == 0.85
        assert perspective.key_points == ["Point 1", "Point 2"]
        assert perspective.concerns == ["Concern 1"]
        assert perspective.risk_level == "medium"
        assert perspective.rationale_summary == "Based on the provided context and expertise."
        assert perspective.evidence_summary == ["Evidence 1", "Evidence 2"]

    def test_confidence_range(self):
        """测试置信度范围"""
        # 最小值
        perspective_min = LLMExpertPerspective(
            summary="Test",
            confidence=0.0,
            key_points=[],
            concerns=[],
            risk_level="low",
            rationale_summary="Test",
            evidence_summary=[],
        )
        assert perspective_min.confidence == 0.0

        # 最大值
        perspective_max = LLMExpertPerspective(
            summary="Test",
            confidence=1.0,
            key_points=[],
            concerns=[],
            risk_level="low",
            rationale_summary="Test",
            evidence_summary=[],
        )
        assert perspective_max.confidence == 1.0

    def test_confidence_invalid_range(self):
        """测试无效置信度范围"""
        with pytest.raises(Exception):
            LLMExpertPerspective(
                summary="Test",
                confidence=1.5,  # 超出范围
                key_points=[],
                concerns=[],
                risk_level="low",
                rationale_summary="Test",
                evidence_summary=[],
            )

        with pytest.raises(Exception):
            LLMExpertPerspective(
                summary="Test",
                confidence=-0.1,  # 负数
                key_points=[],
                concerns=[],
                risk_level="low",
                rationale_summary="Test",
                evidence_summary=[],
            )

    def test_risk_level_values(self):
        """测试 risk_level 有效值"""
        valid_levels: list[RiskLevelLiteral] = ["low", "medium", "high", "critical"]

        for level in valid_levels:
            perspective = LLMExpertPerspective(
                summary="Test",
                confidence=0.5,
                key_points=[],
                concerns=[],
                risk_level=level,
                rationale_summary="Test",
                evidence_summary=[],
            )
            assert perspective.risk_level == level

    def test_risk_level_invalid(self):
        """测试无效 risk_level"""
        with pytest.raises(Exception):
            LLMExpertPerspective(
                summary="Test",
                confidence=0.5,
                key_points=[],
                concerns=[],
                risk_level="unknown",  # type: ignore
                rationale_summary="Test",
                evidence_summary=[],
            )

    def test_key_points_list(self):
        """测试 key_points 列表"""
        perspective = LLMExpertPerspective(
            summary="Test",
            confidence=0.8,
            key_points=[
                "API design should follow RESTful principles",
                "Database indexing is crucial for performance",
                "Security should be a priority from the start",
            ],
            concerns=[],
            risk_level="low",
            rationale_summary="Test",
            evidence_summary=[],
        )

        assert len(perspective.key_points) == 3
        assert "API design should follow RESTful principles" in perspective.key_points

    def test_concerns_list(self):
        """测试 concerns 列表"""
        perspective = LLMExpertPerspective(
            summary="Test",
            confidence=0.6,
            key_points=[],
            concerns=[
                "Missing security review",
                "Performance not tested at scale",
            ],
            risk_level="medium",
            rationale_summary="Test",
            evidence_summary=[],
        )

        assert len(perspective.concerns) == 2
        assert "Missing security review" in perspective.concerns

    def test_evidence_summary_list(self):
        """测试 evidence_summary 列表"""
        perspective = LLMExpertPerspective(
            summary="Test",
            confidence=0.9,
            key_points=[],
            concerns=[],
            risk_level="low",
            rationale_summary="Based on established practices",
            evidence_summary=[
                "5 years of experience in API design",
                "Contributed to security guidelines",
                "Previous similar project completed successfully",
            ],
        )

        assert len(perspective.evidence_summary) == 3

    def test_empty_lists_allowed(self):
        """测试空列表允许"""
        perspective = LLMExpertPerspective(
            summary="Test",
            confidence=0.5,
            key_points=[],
            concerns=[],
            risk_level="low",
            rationale_summary="Test",
            evidence_summary=[],
        )

        assert perspective.key_points == []
        assert perspective.concerns == []
        assert perspective.evidence_summary == []

    def test_rationale_summary_not_reasoning(self):
        """测试 rationale_summary 字段名（避免 reasoning）"""
        perspective = LLMExpertPerspective(
            summary="Test",
            confidence=0.8,
            key_points=[],
            concerns=[],
            risk_level="low",
            rationale_summary="This is the diagnostic basis for the perspective.",
            evidence_summary=[],
        )

        # 确保字段名是 rationale_summary 而不是 reasoning
        assert hasattr(perspective, "rationale_summary")
        assert not hasattr(perspective, "reasoning")

    def test_model_dump(self):
        """测试 model_dump 序列化"""
        perspective = LLMExpertPerspective(
            summary="Expert summary",
            confidence=0.85,
            key_points=["Point 1"],
            concerns=["Concern 1"],
            risk_level="high",
            rationale_summary="Diagnostic basis",
            evidence_summary=["Evidence 1"],
        )

        data = perspective.model_dump()

        assert data["summary"] == "Expert summary"
        assert data["confidence"] == 0.85
        assert data["key_points"] == ["Point 1"]
        assert data["concerns"] == ["Concern 1"]
        assert data["risk_level"] == "high"
        assert data["rationale_summary"] == "Diagnostic basis"
        assert data["evidence_summary"] == ["Evidence 1"]

    def test_model_validate(self):
        """测试 model_validate 反序列化"""
        data = {
            "summary": "Expert summary",
            "confidence": 0.75,
            "key_points": ["Point 1", "Point 2"],
            "concerns": ["Concern 1"],
            "risk_level": "medium",
            "rationale_summary": "Diagnostic basis",
            "evidence_summary": ["Evidence 1"],
        }

        perspective = LLMExpertPerspective.model_validate(data)

        assert perspective.summary == "Expert summary"
        assert perspective.confidence == 0.75
        assert perspective.risk_level == "medium"

    def test_extra_fields_forbidden(self):
        """测试额外字段被禁止"""
        with pytest.raises(Exception):
            LLMExpertPerspective(
                summary="Test",
                confidence=0.5,
                key_points=[],
                concerns=[],
                risk_level="low",
                rationale_summary="Test",
                evidence_summary=[],
                unknown_field="should fail",  # type: ignore
            )

    def test_realistic_expert_perspective(self):
        """测试真实的专家视角样例"""
        perspective = LLMExpertPerspective(
            summary="Based on my expertise in database optimization, I recommend implementing connection pooling and query caching for the reported slow query issue.",
            confidence=0.88,
            key_points=[
                "Connection pooling can reduce connection overhead by 80%",
                "Query caching is effective for read-heavy workloads",
                "Index optimization on the user_id column is critical",
            ],
            concerns=[
                "Connection pooling requires careful configuration to avoid connection leaks",
                "Cache invalidation strategy needs to be defined",
            ],
            risk_level="low",
            rationale_summary="Analysis based on 5 years of database optimization experience and similar performance issues resolved in production.",
            evidence_summary=[
                "Previous optimization reduced query time by 60%",
                "Similar architecture successfully deployed in 3 projects",
            ],
        )

        assert perspective.confidence > 0.8
        assert len(perspective.key_points) == 3
        assert len(perspective.concerns) == 2
        assert perspective.risk_level == "low"