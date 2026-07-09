"""Tests for Markdown notification templates."""
from __future__ import annotations

from agentclaw.community.core.economy.governance.services.notify_builder_service import (
    render_governance_notify,
    render_governance_remind,
)


class TestGovernanceNotifyTemplate:
    """Tests for render_governance_notify()."""

    def test_renders_all_fields(self):
        md = render_governance_notify(
            bot_name="TestBot",
            dt_version="20260629",
            hit_dimensions='["cost", "frequency"]',
            governance_max_priority="P0",
            expected_token_saving=1000,
            saving_ratio=0.15,
            task_summary="Reduce unnecessary calls",
            bot_id="bot-123",
        )
        assert "TestBot" in md
        assert "20260629" in md
        assert "cost" in md
        assert "P0" in md
        assert "1,000" in md or "1000" in md
        assert "15.0%" in md
        assert "Reduce unnecessary calls" in md

    def test_renders_with_bot_id(self):
        """bot_id is accepted but not rendered in simple template text
        (action_link is empty until frontend routes exist)."""
        md = render_governance_notify(
            bot_name="Bot",
            dt_version="20260629",
            hit_dimensions="[]",
            bot_id="bot-456",
        )
        assert "Bot" in md
        assert "20260629" in md

    def test_handles_none_values(self):
        md = render_governance_notify(
            bot_name="Bot",
            dt_version="20260629",
            hit_dimensions=None,
            governance_max_priority=None,
            expected_token_saving=None,
            saving_ratio=None,
            task_summary=None,
            bot_id=None,
        )
        assert "N/A" in md
        assert "Bot" in md

    def test_saving_ratio_as_percentage(self):
        md = render_governance_notify(
            bot_name="Bot",
            dt_version="20260629",
            hit_dimensions="[]",
            saving_ratio=0.23,
        )
        assert "23.0%" in md


class TestGovernanceRemindTemplate:
    """Tests for render_governance_remind()."""

    def test_renders_reminder(self):
        md = render_governance_remind(
            bot_name="TestBot",
            dt_version="20260629",
            hit_dimensions='["cost"]',
            governance_max_priority="P1",
            remind_count=0,
            days_since_create=3,
            bot_id="bot-123",
        )
        assert "TestBot" in md
        assert "提醒" in md

    def test_overdue_prefix(self):
        md = render_governance_remind(
            bot_name="TestBot",
            dt_version="20260629",
            hit_dimensions="[]",
            days_since_create=6,
            remind_count=1,
            bot_id="bot-1",
        )
        assert "超期" in md
