"""Unit tests for feedback_verdict — user⊗admin 成对裁决派生(Task 1)。

覆盖:
- 纯函数 compute_feedback_verdict 全分支(pending_review_* / 双流成对 / admin_only_* /
  awaiting_user_feedback / other)
- GovernanceTicket.feedback_verdict @property 委托纯函数
- 离线复用契约:纯函数无 self/session/DB 依赖,可独立 import 调用
"""
from __future__ import annotations

from agentclaw.community.core.economy.governance.domain.enums import GovernanceStatus
from agentclaw.community.core.economy.governance.domain.ticket import (
    GovernanceTicket,
    MutableSnapshot,
    compute_feedback_verdict,
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
        ticket_id="T-001", worker_id="o:b", bot_id="b", owner_id="o", owner_name=None, bot_name="B",
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


class TestComputeFeedbackVerdictPureFunction:
    """纯函数:输入三标量,无在线依赖(离线可复用)。"""

    def test_no_response_no_review(self):
        assert compute_feedback_verdict(None, None, "open") == "awaiting_user_feedback"
        assert compute_feedback_verdict(None, None, GovernanceStatus.WAITING_REVIEW) == "awaiting_user_feedback"

    def test_review_absent_pending_by_user_decision(self):
        assert compute_feedback_verdict("optimized", None, "waiting_review") == "pending_review_optimized"
        assert compute_feedback_verdict("whitelist", None, "open") == "pending_review_whitelist"
        assert compute_feedback_verdict("dispute", None, "scheduled") == "pending_review_dispute"
        assert compute_feedback_verdict("need_time", None, "waiting_review") == "pending_review_need_time"

    def test_pair_confirmed(self):
        assert compute_feedback_verdict("optimized", "approve_close", "closed") == "confirmed"

    def test_pair_whitelist_confirmed(self):
        assert compute_feedback_verdict("whitelist", "approve_whitelist", "closed") == "whitelist_confirmed"

    def test_pair_admin_overroled_whitelist(self):
        assert compute_feedback_verdict("optimized", "approve_whitelist", "closed") == "admin_overroled_whitelist"

    def test_pair_dispute_rejected(self):
        # 用户申诉 + 管理员驳回重开 = 驳回申诉
        assert compute_feedback_verdict("dispute", "reject_for_reopen", "closed") == "dispute_rejected"

    def test_pair_dispute_accepted(self):
        # 用户申诉 + 管理员 approve_close(采纳申诉关单)= 强误判信号
        assert compute_feedback_verdict("dispute", "approve_close", "closed") == "dispute_accepted"

    def test_pair_whitelist_denied(self):
        # 用户加白 + 管理员 reject_for_reopen(驳回加白)
        assert compute_feedback_verdict("whitelist", "reject_for_reopen", "closed") == "whitelist_denied"

    def test_pair_schedule_confirmed(self):
        """need_time + approve_scheduled(同意排期)→ schedule_confirmed(ticket-review 新对)。"""
        assert compute_feedback_verdict("need_time", "approve_scheduled", "closed") == "schedule_confirmed"

    def test_pair_schedule_rejected(self):
        """need_time + reject_for_reopen(驳回排期)→ schedule_rejected。"""
        assert compute_feedback_verdict("need_time", "reject_for_reopen", "closed") == "schedule_rejected"

    def test_pair_optimized_rejected(self):
        """optimized + reject_for_reopen(不认可'已优化')→ optimized_rejected。"""
        assert compute_feedback_verdict("optimized", "reject_for_reopen", "closed") == "optimized_rejected"

    def test_pair_whitelist_dismissed(self):
        """whitelist + approve_close(不加白直接关=静默驳回加白)→ whitelist_dismissed。"""
        assert compute_feedback_verdict("whitelist", "approve_close", "closed") == "whitelist_dismissed"

    def test_pair_need_time_approve_close_is_other(self):
        """need_time + approve_close 落 other(approve_close 不在 need_time 下发集,非正常路径)。"""
        assert compute_feedback_verdict("need_time", "approve_close", "closed") == "other"

    def test_admin_only(self):
        # 用户未反馈 + 管理员直接裁
        assert compute_feedback_verdict(None, "approve_close", "closed") == "admin_only_approve_close"
        assert compute_feedback_verdict(None, "approve_whitelist", "closed") == "admin_only_approve_whitelist"

    def test_other_on_unknown_response(self):
        assert compute_feedback_verdict("bogus", "approve_close", "closed") == "other"
        assert compute_feedback_verdict("bogus", None, "open") == "other"

    def test_other_on_unmapped_pair(self):
        # dispute + approve_scheduled 未在 _VERDICT_PAIR → other
        assert compute_feedback_verdict("dispute", "approve_scheduled", "closed") == "other"

    def test_no_online_dependencies(self):
        """纯函数仅三标量入参、可在无 DB/session 上下文下独立调用(离线复用契约)。"""
        import inspect
        params = list(inspect.signature(compute_feedback_verdict).parameters)
        assert params == ["response", "review_decision", "governance_status"]
        # 仅传字符串/None 即可算出结果,不需 ticket 实例 / DB / session
        assert compute_feedback_verdict("whitelist", "reject_for_reopen", "closed") == "whitelist_denied"


class TestTicketFeedbackVerdictProperty:
    """@property 委托纯函数,输入 GovernanceTicket 既有字段。"""

    def test_pending_review_on_waiting_review_no_admin(self):
        t = _make_ticket(user_feedback="whitelist", review_decision=None,
                         governance_status=GovernanceStatus.WAITING_REVIEW)
        assert t.feedback_verdict == "pending_review_whitelist"

    def test_whitelist_denied_after_review(self):
        t = _make_ticket(user_feedback="whitelist", review_decision="reject_for_reopen",
                         governance_status=GovernanceStatus.CLOSED,
                         review_remark="加白理由不充分")
        assert t.feedback_verdict == "whitelist_denied"

    def test_dispute_accepted_after_review(self):
        t = _make_ticket(user_feedback="dispute", review_decision="approve_close",
                         governance_status=GovernanceStatus.CLOSED)
        assert t.feedback_verdict == "dispute_accepted"

    def test_awaiting_when_no_feedback_no_review(self):
        t = _make_ticket(user_feedback=None, review_decision=None,
                         governance_status=GovernanceStatus.OPEN)
        assert t.feedback_verdict == "awaiting_user_feedback"

    def test_admin_only_when_user_silent_admin_reviewed(self):
        t = _make_ticket(user_feedback=None, review_decision="approve_whitelist",
                         governance_status=GovernanceStatus.CLOSED)
        assert t.feedback_verdict == "admin_only_approve_whitelist"

    def test_property_equals_pure_function(self):
        """@property 与纯函数结果一致(委托正确)。"""
        t = _make_ticket(user_feedback="optimized", review_decision="approve_close",
                         governance_status=GovernanceStatus.CLOSED)
        assert t.feedback_verdict == compute_feedback_verdict(
            "optimized", "approve_close", GovernanceStatus.CLOSED,
        )