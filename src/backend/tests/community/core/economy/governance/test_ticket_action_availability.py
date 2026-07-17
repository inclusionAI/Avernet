"""Unit tests for available_actions — 按用户反馈下发 review 动作(Task 1)。

覆盖:
- compute_available_actions 四反馈动作集 + label 差异化 + remark_required
- GovernanceTicket.available_actions @property 委托纯函数
- 纯函数无在线依赖(可离线复用)
"""
from __future__ import annotations

from agentclaw.community.core.economy.governance.domain.enums import (
    GovernanceStatus,
    TicketAction,
)
from agentclaw.community.core.economy.governance.domain.ticket import (
    GovernanceTicket,
    MutableSnapshot,
    compute_available_actions,
)
from datetime import datetime


def _make_ticket(**overrides) -> GovernanceTicket:
    snapshot = overrides.pop("snapshot", None) or MutableSnapshot(
        dt_version="20260710", initial_decision="actionable", current_decision="actionable",
        triggered_dimensions="token_usage", hit_dimensions_count=1, severity="P1",
        estimated_saving_tokens=5000, saving_ratio=0.5, task_summary="s",
        notification_structured="{}", analysis_status="done", consecutive_normal_days=0,
        last_decision_dt_version=None, last_seen_at=None, last_sync_at=None,
    )
    defaults = dict(
        ticket_id="T-001", worker_id="o:b", bot_id="b", owner_id="o", bot_name="B",
        _snapshot=snapshot, governance_status=GovernanceStatus.WAITING_REVIEW,
        assignee=None, user_feedback=None, feedback_at=None, feedback_remark=None,
        feedback_source=None, close_reason=None, closed_at=None, cooldown_until=None,
        review_reason=None, review_decision=None, reviewed_by=None, reviewed_at=None,
        review_remark=None, repair_deadline=None, resume_at=None, remind_at=None,
        remind_count=0, feedback_payload=None, actor_id=None,
        gmt_create=None, gmt_modified=None,
    )
    defaults.update(overrides)
    return GovernanceTicket(**defaults)


REVIEW_ENDPOINT = "POST /api/economy/governance/workflow/tickets/review"


class TestComputeAvailableActions:
    """纯函数:按反馈返回动作集。"""

    def test_optimized(self):
        actions = compute_available_actions("optimized")
        assert [a["value"] for a in actions] == [
            TicketAction.APPROVE_CLOSE.value, TicketAction.REJECT_FOR_REOPEN.value,
        ]
        assert actions[0]["label"] == "确认已优化"
        assert actions[1]["label"] == "不认可,重开"

    def test_dispute(self):
        actions = compute_available_actions("dispute")
        assert [a["value"] for a in actions] == [
            TicketAction.APPROVE_CLOSE.value, TicketAction.REJECT_FOR_REOPEN.value,
        ]
        assert actions[0]["label"] == "采纳申诉"
        assert actions[1]["label"] == "驳回申诉,重开"

    def test_whitelist(self):
        actions = compute_available_actions("whitelist")
        assert [a["value"] for a in actions] == [
            TicketAction.APPROVE_WHITELIST.value, TicketAction.REJECT_FOR_REOPEN.value,
        ]
        assert actions[0]["label"] == "同意加白"
        assert actions[1]["label"] == "驳回加白,重开"

    def test_need_time(self):
        actions = compute_available_actions("need_time")
        assert [a["value"] for a in actions] == [
            TicketAction.APPROVE_SCHEDULED.value, TicketAction.REJECT_FOR_REOPEN.value,
        ]
        assert actions[0]["label"] == "同意排期"
        assert actions[1]["label"] == "不认可,重开"

    def test_no_feedback_returns_empty(self):
        assert compute_available_actions(None) == []

    def test_unknown_feedback_returns_empty(self):
        assert compute_available_actions("bogus") == []

    def test_remark_required_only_on_reject(self):
        for fb in ("optimized", "dispute", "whitelist", "need_time"):
            actions = compute_available_actions(fb)
            # 同意动作(approve 类)remark_required=False
            assert actions[0]["remark_required"] is False
            # reject 必填
            assert actions[1]["remark_required"] is True

    def test_all_actions_carry_review_endpoint(self):
        for fb in ("optimized", "dispute", "whitelist", "need_time"):
            for a in compute_available_actions(fb):
                assert a["endpoint"] == REVIEW_ENDPOINT

    def test_no_online_dependencies(self):
        """纯函数仅标量入参、可独立调用(离线复用契约)。"""
        import inspect
        params = list(inspect.signature(compute_available_actions).parameters)
        assert params == ["user_feedback"]
        assert compute_available_actions("whitelist")[0]["value"] == "approve_whitelist"


class TestTicketAvailableActionsProperty:
    """@property 委托纯函数,输入 GovernanceTicket.user_feedback。"""

    def test_optimized_ticket(self):
        t = _make_ticket(user_feedback="optimized")
        assert [a["value"] for a in t.available_actions] == [
            "approve_close", "reject_for_reopen",
        ]

    def test_need_time_ticket(self):
        t = _make_ticket(user_feedback="need_time")
        assert [a["value"] for a in t.available_actions] == [
            "approve_scheduled", "reject_for_reopen",
        ]

    def test_whitelist_ticket(self):
        t = _make_ticket(user_feedback="whitelist")
        assert [a["value"] for a in t.available_actions] == [
            "approve_whitelist", "reject_for_reopen",
        ]

    def test_no_feedback_empty(self):
        t = _make_ticket(user_feedback=None)
        assert t.available_actions == []

    def test_property_equals_pure_function(self):
        t = _make_ticket(user_feedback="dispute")
        assert t.available_actions == compute_available_actions("dispute")