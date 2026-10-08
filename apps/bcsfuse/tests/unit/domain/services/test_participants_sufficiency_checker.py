"""
ParticipantsSufficiencyChecker Unit Tests

Stage 4: G5 real-context deepening / candidate recommendation 正式接入

规则（钉死）：
1. participants is None → 不足
2. len(explicit_participants) < min_experts → 不足
3. required_domains 未覆盖 → 不足
4. 其余 → 充足
"""

from __future__ import annotations

import pytest

from src.domain.services.participants_sufficiency_checker import (
    ParticipantsSufficiencyChecker,
    SufficiencyCheckResult,
)


class TestParticipantsSufficiencyChecker:
    """ParticipantsSufficiencyChecker 测试"""

    def test_default_min_experts(self):
        """测试默认最小专家数"""
        checker = ParticipantsSufficiencyChecker()
        assert checker.min_experts == 3

    def test_custom_min_experts(self):
        """测试自定义最小专家数"""
        checker = ParticipantsSufficiencyChecker(min_experts=5)
        assert checker.min_experts == 5

    # =========================================================================
    # 规则 1: participants is None → 不足
    # =========================================================================

    def test_none_participants_not_sufficient(self):
        """测试 participants=None 时不足"""
        checker = ParticipantsSufficiencyChecker()
        result = checker.check(participants=None)

        assert result.is_sufficient is False
        assert result.reason == "no_participants_given"
        assert result.participant_count == 0

    def test_none_participants_with_required_domains(self):
        """测试 participants=None 且有 required_domains"""
        checker = ParticipantsSufficiencyChecker()
        result = checker.check(
            participants=None,
            required_domains=["security", "legal"],
        )

        assert result.is_sufficient is False
        assert result.reason == "no_participants_given"
        assert result.uncovered_domains == ["security", "legal"]

    # =========================================================================
    # 规则 2: len(explicit_participants) < min_experts → 不足
    # =========================================================================

    def test_insufficient_count_not_sufficient(self):
        """测试参与者数量不足"""
        checker = ParticipantsSufficiencyChecker(min_experts=3)

        # 0 个参与者
        result = checker.check(participants=[])
        assert result.is_sufficient is False
        assert result.reason == "insufficient_count"

        # 1 个参与者
        result = checker.check(participants=["staff_001:default"])
        assert result.is_sufficient is False
        assert result.reason == "insufficient_count"
        assert result.participant_count == 1

        # 2 个参与者
        result = checker.check(participants=["staff_001:default", "staff_002:default"])
        assert result.is_sufficient is False
        assert result.reason == "insufficient_count"
        assert result.participant_count == 2

    def test_meets_min_experts_count_sufficient(self):
        """测试参与者数量达到最小值"""
        checker = ParticipantsSufficiencyChecker(min_experts=3)

        result = checker.check(participants=[
            "staff_001:default",
            "staff_002:default",
            "staff_003:default",
        ])

        assert result.is_sufficient is True
        assert result.participant_count == 3

    def test_exceeds_min_experts_count_sufficient(self):
        """测试参与者数量超过最小值"""
        checker = ParticipantsSufficiencyChecker(min_experts=3)

        result = checker.check(participants=[
            "staff_001:default",
            "staff_002:default",
            "staff_003:default",
            "staff_004:default",
        ])

        assert result.is_sufficient is True
        assert result.participant_count == 4

    # =========================================================================
    # 规则 3: required_domains 未覆盖 → 不足
    # =========================================================================

    def test_domains_not_covered_not_sufficient(self):
        """测试领域未覆盖"""
        checker = ParticipantsSufficiencyChecker(min_experts=3)

        result = checker.check(
            participants=["staff_001:default", "staff_002:default", "staff_003:default"],
            covered_domains=["security"],
            required_domains=["security", "legal", "database"],
        )

        assert result.is_sufficient is False
        assert result.reason == "domains_not_covered"
        assert set(result.uncovered_domains) == {"legal", "database"}

    def test_partial_domain_coverage_not_sufficient(self):
        """测试部分领域覆盖"""
        checker = ParticipantsSufficiencyChecker(min_experts=3)

        result = checker.check(
            participants=["staff_001:default", "staff_002:default", "staff_003:default"],
            covered_domains=["security", "legal"],
            required_domains=["security", "legal", "database"],
        )

        assert result.is_sufficient is False
        assert result.reason == "domains_not_covered"
        assert result.uncovered_domains == ["database"]

    def test_full_domain_coverage_sufficient(self):
        """测试完全领域覆盖"""
        checker = ParticipantsSufficiencyChecker(min_experts=3)

        result = checker.check(
            participants=["staff_001:default", "staff_002:default", "staff_003:default"],
            covered_domains=["security", "legal", "database"],
            required_domains=["security", "legal", "database"],
        )

        assert result.is_sufficient is True
        assert result.uncovered_domains == []

    def test_no_required_domains_means_sufficient(self):
        """测试无 required_domains 时视为覆盖"""
        checker = ParticipantsSufficiencyChecker(min_experts=3)

        result = checker.check(
            participants=["staff_001:default", "staff_002:default", "staff_003:default"],
            covered_domains=["security"],
            required_domains=[],  # 无需领域
        )

        assert result.is_sufficient is True

    # =========================================================================
    # 规则 4: 其余 → 充足
    # =========================================================================

    def test_all_conditions_met_sufficient(self):
        """测试所有条件满足时充足"""
        checker = ParticipantsSufficiencyChecker(min_experts=3)

        result = checker.check(
            participants=["staff_001:default", "staff_002:default", "staff_003:default"],
            covered_domains=["security", "legal", "database"],
            required_domains=["security", "legal"],
        )

        assert result.is_sufficient is True
        assert result.reason is None
        assert result.participant_count == 3
        assert result.uncovered_domains == []

    # =========================================================================
    # 边界情况
    # =========================================================================

    def test_empty_covered_domains_with_required_domains(self):
        """测试 covered_domains 为空但有 required_domains"""
        checker = ParticipantsSufficiencyChecker(min_experts=3)

        result = checker.check(
            participants=["staff_001:default", "staff_002:default", "staff_003:default"],
            covered_domains=[],
            required_domains=["security"],
        )

        assert result.is_sufficient is False
        assert result.reason == "domains_not_covered"

    def test_more_covered_than_required(self):
        """测试 covered_domains 超过 required_domains"""
        checker = ParticipantsSufficiencyChecker(min_experts=3)

        result = checker.check(
            participants=["staff_001:default", "staff_002:default", "staff_003:default"],
            covered_domains=["security", "legal", "database", "ops"],
            required_domains=["security"],  # 只需要 security
        )

        assert result.is_sufficient is True

    def test_min_experts_one(self):
        """测试 min_experts=1 的边界"""
        checker = ParticipantsSufficiencyChecker(min_experts=1)

        # 1 个足够
        result = checker.check(participants=["staff_001:default"])
        assert result.is_sufficient is True

        # 0 个不足
        result = checker.check(participants=[])
        assert result.is_sufficient is False

    # =========================================================================
    # is_sufficient 方法
    # =========================================================================

    def test_is_sufficient_method_returns_bool(self):
        """测试 is_sufficient 方法返回布尔值"""
        checker = ParticipantsSufficiencyChecker()

        assert checker.is_sufficient(None) is False
        assert checker.is_sufficient([]) is False
        assert checker.is_sufficient(["p1"]) is False
        assert checker.is_sufficient(["p1", "p2", "p3"]) is True

    # =========================================================================
    # SufficiencyCheckResult
    # =========================================================================

    def test_sufficiency_check_result_default_uncovered_domains(self):
        """测试 SufficiencyCheckResult 默认 uncovered_domains"""
        result = SufficiencyCheckResult(
            is_sufficient=True,
            participant_count=3,
        )

        assert result.uncovered_domains == []

    def test_sufficiency_check_result_all_fields(self):
        """测试 SufficiencyCheckResult 所有字段"""
        result = SufficiencyCheckResult(
            is_sufficient=False,
            reason="domains_not_covered",
            participant_count=3,
            min_required=3,
            uncovered_domains=["legal"],
        )

        assert result.is_sufficient is False
        assert result.reason == "domains_not_covered"
        assert result.participant_count == 3
        assert result.min_required == 3
        assert result.uncovered_domains == ["legal"]