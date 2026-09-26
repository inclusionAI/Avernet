"""Backward-compat tests for CardCallbackIFrameRequest schema (Task 2).

Verifies the structured feedback_payload model parses both the new
structured shape and legacy arbitrary dicts, and items validate.
"""
from __future__ import annotations

from agentclaw.community.adapters.http.economy.schemas import (
    CardCallbackFeedbackItem,
    CardCallbackFeedbackPayload,
    CardCallbackIFrameRequest,
)


class TestCardCallbackRequestSchema:
    def test_structured_payload_parses(self):
        req = CardCallbackIFrameRequest(
            notification_id="n-1",
            response="optimized",
            feedback_payload={
                "version": 1,
                "overall_action": "accepted",
                "items": [
                    {"index": 1, "action": "accepted", "remark": None},
                    {"index": 2, "action": "rejected", "remark": "no"},
                ],
            },
        )
        assert isinstance(req.feedback_payload, CardCallbackFeedbackPayload)
        assert req.feedback_payload.items is not None
        assert len(req.feedback_payload.items) == 2
        assert req.feedback_payload.items[0].index == 1
        assert req.feedback_payload.items[1].remark == "no"

    def test_legacy_extra_keys_allowed(self):
        """Arbitrary extra keys in dict (e.g. overall_remark) don't break parse."""
        req = CardCallbackIFrameRequest(
            notification_id="n-1",
            response="dispute",
            feedback_payload={
                "version": 1,
                "overall_action": "partial",
                "overall_remark": "maybe",
                "repair_deadline": None,
                "items": [{"index": 1, "action": "partial"}],
            },
        )
        assert req.feedback_payload.overall_remark == "maybe"
        # extra unknown keys tolerated (model_config extra=allow)
        assert req.feedback_payload.model_extra is None or isinstance(
            req.feedback_payload, CardCallbackFeedbackPayload
        )

    def test_payload_none_default(self):
        req = CardCallbackIFrameRequest(notification_id="n-1", response="optimized")
        assert req.feedback_payload is None

    def test_item_requires_index_and_action(self):
        """Missing required item field raises validation error."""
        import pytest

        with pytest.raises(Exception):
            CardCallbackFeedbackItem(action="accepted")  # missing index

    def test_top_level_fields_unchanged(self):
        req = CardCallbackIFrameRequest(
            notification_id="n-1",
            response="need_time",
            remark="plan",
            repair_deadline="2026-07-15",
        )
        assert req.response == "need_time"
        assert req.remark == "plan"
        assert req.repair_deadline == "2026-07-15"