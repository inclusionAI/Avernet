"""
DomainCoverage Model Unit Tests

Stage 4: G5 real-context deepening / candidate recommendation 正式接入
"""

from __future__ import annotations

import pytest

from src.domain.models.domain_coverage import DomainCoverage


class TestDomainCoverageModel:
    """DomainCoverage 模型测试"""

    def test_domain_coverage_minimal(self):
        """测试最小字段"""
        coverage = DomainCoverage()

        assert coverage.required_domains == []
        assert coverage.covered_domains == []
        assert coverage.missing_domains == []
        assert coverage.coverage_score == 0.0
        assert coverage.domain_distribution is None

    def test_domain_coverage_all_fields(self):
        """测试所有字段"""
        coverage = DomainCoverage(
            required_domains=["security", "legal", "database"],
            covered_domains=["security", "legal"],
            missing_domains=["database"],
            coverage_score=0.67,
            domain_distribution={"security": 2, "legal": 1},
        )

        assert coverage.required_domains == ["security", "legal", "database"]
        assert coverage.covered_domains == ["security", "legal"]
        assert coverage.missing_domains == ["database"]
        assert coverage.coverage_score == 0.67
        assert coverage.domain_distribution == {"security": 2, "legal": 1}

    def test_coverage_score_calculation(self):
        """测试覆盖分数计算"""
        # 完全覆盖
        full_coverage = DomainCoverage(
            required_domains=["security", "legal"],
            covered_domains=["security", "legal"],
            missing_domains=[],
            coverage_score=1.0,
        )
        assert full_coverage.coverage_score == 1.0

        # 部分覆盖
        partial_coverage = DomainCoverage(
            required_domains=["security", "legal", "database"],
            covered_domains=["security"],
            missing_domains=["legal", "database"],
            coverage_score=0.33,
        )
        assert partial_coverage.coverage_score == 0.33

    def test_missing_domains_detection(self):
        """测试缺失领域检测"""
        coverage = DomainCoverage(
            required_domains=["security", "legal", "database"],
            covered_domains=["security"],
        )

        missing = coverage.calculate_missing_domains()
        assert set(missing) == {"legal", "database"}

        # 确保 missing_domains 字段更新
        coverage.missing_domains = missing
        assert set(coverage.missing_domains) == {"legal", "database"}

    def test_is_fully_covered(self):
        """测试是否完全覆盖"""
        # 完全覆盖
        full = DomainCoverage(
            required_domains=["security", "legal"],
            covered_domains=["security", "legal"],
            missing_domains=[],
        )
        assert full.is_fully_covered is True

        # 部分覆盖
        partial = DomainCoverage(
            required_domains=["security", "legal"],
            covered_domains=["security"],
            missing_domains=["legal"],
        )
        assert partial.is_fully_covered is False

    def test_coverage_ratio(self):
        """测试覆盖比例"""
        # 无需领域
        no_required = DomainCoverage(required_domains=[])
        assert no_required.coverage_ratio == 1.0

        # 完全覆盖
        full = DomainCoverage(
            required_domains=["security", "legal"],
            covered_domains=["security", "legal"],
        )
        assert full.coverage_ratio == 1.0

        # 部分覆盖
        partial = DomainCoverage(
            required_domains=["security", "legal", "database"],
            covered_domains=["security"],
        )
        assert abs(partial.coverage_ratio - 0.333) < 0.01

    def test_domain_distribution_optional(self):
        """测试领域分布是可选的"""
        # 不提供 domain_distribution
        coverage1 = DomainCoverage(
            required_domains=["security"],
            covered_domains=["security"],
        )
        assert coverage1.domain_distribution is None

        # 提供 domain_distribution
        coverage2 = DomainCoverage(
            required_domains=["security"],
            covered_domains=["security"],
            domain_distribution={"security": 2},
        )
        assert coverage2.domain_distribution == {"security": 2}

    def test_model_dump(self):
        """测试模型序列化"""
        coverage = DomainCoverage(
            required_domains=["security"],
            covered_domains=["security"],
            missing_domains=[],
            coverage_score=1.0,
        )

        data = coverage.model_dump()
        assert "required_domains" in data
        assert "covered_domains" in data
        assert "missing_domains" in data
        assert "coverage_score" in data

    def test_model_validate(self):
        """测试模型验证"""
        data = {
            "required_domains": ["security", "legal"],
            "covered_domains": ["security"],
            "missing_domains": ["legal"],
            "coverage_score": 0.5,
        }

        coverage = DomainCoverage.model_validate(data)
        assert coverage.required_domains == ["security", "legal"]
        assert coverage.covered_domains == ["security"]
        assert coverage.missing_domains == ["legal"]
        assert coverage.coverage_score == 0.5

    def test_extra_fields_forbidden(self):
        """测试禁止额外字段"""
        with pytest.raises(Exception):  # Pydantic ValidationError
            DomainCoverage(
                required_domains=["security"],
                unknown_field="not_allowed",
            )

    def test_coverage_score_bounds(self):
        """测试覆盖分数边界"""
        # 最小值
        min_score = DomainCoverage(coverage_score=0.0)
        assert min_score.coverage_score == 0.0

        # 最大值
        max_score = DomainCoverage(coverage_score=1.0)
        assert max_score.coverage_score == 1.0

        # 超出边界应失败
        with pytest.raises(Exception):
            DomainCoverage(coverage_score=-0.1)

        with pytest.raises(Exception):
            DomainCoverage(coverage_score=1.1)