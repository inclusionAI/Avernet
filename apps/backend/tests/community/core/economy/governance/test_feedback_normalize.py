"""Unit tests for v2 feedback_payload normalization helpers.

Covers Task 1 pure functions: _normalize_response + _compute_consistency_flag.
No DB, no side effects — pure mapping logic.
"""
from __future__ import annotations

import pytest

from agentclaw.community.core.economy.governance.services.feedback_service import (
    _compute_consistency_flag,
    _normalize_response,
)


class TestNormalizeResponse:
    """raw response → overall.decision mapping."""

    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("optimized", "accepted"),
            ("need_time", "deferred"),
            ("dispute", "rejected"),
            ("whitelist", "whitelist"),
        ],
    )
    def test_four_formal_responses_map(self, raw, expected):
        assert _normalize_response(raw) == expected

    def test_unknown_response_raises(self):
        with pytest.raises(ValueError):
            _normalize_response("bogus")


class TestComputeConsistencyFlag:
    """overall vs per-item decision consistency."""

    def test_all_items_same_as_overall_is_consistent(self):
        assert _compute_consistency_flag("accepted", ["accepted", "accepted"]) == "consistent"

    def test_no_item_feedback_is_overall_dominates(self):
        assert _compute_consistency_flag("accepted", []) == "overall_dominates"
        assert _compute_consistency_flag("rejected", []) == "overall_dominates"

    def test_overall_accepted_with_some_rejected_is_partial_mix(self):
        assert _compute_consistency_flag("accepted", ["accepted", "rejected"]) == "partial_mix"
        assert _compute_consistency_flag("accepted", ["partial"]) == "partial_mix"

    def test_overall_accepted_all_accepted_consistent(self):
        # unique == {"accepted"} == overall → consistent (not partial_mix)
        assert _compute_consistency_flag("accepted", ["accepted"]) == "consistent"

    def test_overall_rejected_with_mix_when_not_all_rejected(self):
        # overall=rejected, items=[rejected, accepted] → unique≠{rejected}, not accepted-dom
        # → falls through to consistent (no partial_mix branch for non-accepted overall)
        assert _compute_consistency_flag("rejected", ["rejected", "accepted"]) == "consistent"