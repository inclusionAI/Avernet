"""Tests for review-router Pydantic schemas — from_ticket / from_outcome 序列化。

验证「领域模型流转」约束:schema 从 GovernanceTicket / TicketActionOutcome
显式构造,字段映射 + datetime→ISO + enum→str + None 安全。
"""
from __future__ import annotations

from datetime import datetime

from agentclaw.community.adapters.http.economy.schemas import (
    WorkflowReviewResponse,
    ReviewTicketDetailResponse,
    ReviewTicketItem,
    ReviewTicketListResponse,
)
from agentclaw.community.core.economy.governance.domain.enums import GovernanceStatus
from agentclaw.community.core.economy.governance.domain.ticket import MutableSnapshot
from agentclaw.community.core.economy.governance.domain.ticket import GovernanceTicket
from agentclaw.community.core.economy.governance.services.admin_service import (
    TicketActionOutcome,
)


def _make_ticket(**overrides) -> GovernanceTicket:
    """构造 GovernanceTicket(复用领域构造路径,带完整 snapshot)。"""
    snapshot = overrides.pop("snapshot", None) or MutableSnapshot(
        dt_version="20260710",
        initial_decision="actionable",
        current_decision="actionable",
        triggered_dimensions="token_usage,cost",
        hit_dimensions_count=2,
        severity="P1",
        estimated_saving_tokens=5000,
        saving_ratio=0.5,
        task_summary="Bot cost high",
        notification_structured='{"dims": ["cost"]}',
        analysis_status="done",
        consecutive_normal_days=3,
        last_decision_dt_version=None,
        last_seen_at=None,
        last_sync_at=datetime(2026, 7, 10, 12, 0, 0),
    )
    defaults = dict(
        ticket_id="T-001",
        worker_id="owner-1:bot-1",
        bot_id="bot-1",
        owner_id="owner-1",
        bot_name="TestBot",
        _snapshot=snapshot,
        governance_status=GovernanceStatus.WAITING_REVIEW,
        assignee="owner-1:bot-1",
        user_feedback="dispute",
        feedback_at=datetime(2026, 7, 10, 8, 0, 0),
        feedback_remark="user disputed",
        feedback_source="http_api",
        close_reason=None,
        closed_at=None,
        cooldown_until=None,
        review_reason="user_disputed",
        review_decision=None,
        reviewed_by=None,
        reviewed_at=None,
        review_remark=None,
        repair_deadline=None,
        resume_at=datetime(2026, 7, 12, 0, 0, 0),
        remind_at=None,
        remind_count=2,
        feedback_payload='{"reason": "false positive"}',
        actor_id="owner-1",
        gmt_create=datetime(2026, 7, 9, 8, 0, 0),
        gmt_modified=datetime(2026, 7, 10, 8, 0, 0),
    )
    defaults.update(overrides)
    return GovernanceTicket(**defaults)


class TestReviewTicketItem:
    def test_from_ticket_field_mapping(self) -> None:
        t = _make_ticket()
        item = ReviewTicketItem.from_ticket(t)
        assert item.ticket_id == "T-001"
        assert item.bot_name == "TestBot"
        assert item.owner_id == "owner-1"
        # enum → str
        assert item.governance_status == "waiting_review"
        # snapshot 委托属性
        assert item.latest_decision == "actionable"
        assert item.hit_dimensions == "token_usage,cost"
        assert item.saving_ratio == 0.5
        # 实体字段
        assert item.response == "dispute"
        assert item.review_reason == "user_disputed"
        # datetime → ISO 字符串
        assert item.gmt_create == "2026-07-09T08:00:00"

    def test_from_ticket_none_safe(self) -> None:
        """新建态工单:大量字段为 None,不抛错。"""
        t = _make_ticket(
            user_feedback=None,
            review_reason=None,
            gmt_create=None,
            gmt_modified=None,
            feedback_at=None,
        )
        item = ReviewTicketItem.from_ticket(t)
        assert item.response is None
        assert item.review_reason is None
        assert item.gmt_create is None


class TestReviewTicketListResponse:
    def test_from_tickets_pagination_and_serialize(self) -> None:
        tickets = [_make_ticket(ticket_id=f"T-{i}") for i in range(3)]
        resp = ReviewTicketListResponse.from_tickets(
            tickets, total=42, limit=3, offset=6, status_filter=["waiting_review"],
        )
        assert resp.total == 42
        assert resp.limit == 3
        assert resp.offset == 6
        assert resp.status_filter == ["waiting_review"]
        assert len(resp.items) == 3
        assert [i.ticket_id for i in resp.items] == ["T-0", "T-1", "T-2"]
        # 可 JSON 序列化
        data = resp.model_dump()
        assert data["total"] == 42

    def test_empty_list(self) -> None:
        resp = ReviewTicketListResponse.from_tickets(
            [], total=0, limit=50, offset=0, status_filter=[],
        )
        assert resp.items == []
        assert resp.total == 0


class TestReviewTicketDetailResponse:
    def test_from_ticket_full_mapping(self) -> None:
        t = _make_ticket()
        d = ReviewTicketDetailResponse.from_ticket(t)
        # 基础信息
        assert d.ticket_id == "T-001"
        assert d.worker_id == "owner-1:bot-1"
        assert d.bot_id == "bot-1"
        assert d.dt_version == "20260710"
        assert d.task_summary == "Bot cost high"
        assert d.governance_max_priority == "P1"
        # 治理态
        assert d.governance_status == "waiting_review"
        assert d.latest_decision == "actionable"
        assert d.saving_ratio == 0.5
        assert d.consecutive_normal_days == 3
        # 反馈
        assert d.response == "dispute"
        assert d.response_remark == "user disputed"
        assert d.response_at == "2026-07-10T08:00:00"
        assert d.feedback_payload == '{"reason": "false positive"}'
        # v1-style payload (no ticket_ref) → no feedback_notification_id surfaced
        assert d.feedback_notification_id is None
        # 生命周期
        assert d.review_reason == "user_disputed"
        assert d.mute_until == "2026-07-12T00:00:00"
        assert d.remind_count == 2
        # 元信息
        assert d.gmt_create == "2026-07-09T08:00:00"
        assert d.gmt_modified == "2026-07-10T08:00:00"

    def test_from_ticket_none_safe(self) -> None:
        t = _make_ticket(
            closed_at=None, close_reason=None, reviewed_at=None,
            gmt_create=None,
        )
        d = ReviewTicketDetailResponse.from_ticket(t)
        assert d.closed_at is None
        assert d.close_reason is None
        assert d.reviewed_at is None
        assert d.gmt_create is None

    def test_feedback_notification_id_extracted_from_v2_payload(self) -> None:
        """v2 feedback_payload 的 ticket_ref.notification_id 透出到详情顶层。"""
        import json as _json
        v2 = _json.dumps({
            "feedback_schema_version": 2,
            "ticket_ref": {
                "notification_id": "n-callback-xyz",
                "worker_id": "owner-1:bot-1",
                "dt_version": "20260710",
                "ticket_id": "T-001",
            },
        })
        t = _make_ticket(feedback_payload=v2)
        d = ReviewTicketDetailResponse.from_ticket(t)
        assert d.feedback_notification_id == "n-callback-xyz"

    def test_feedback_notification_id_degrades_on_garbage(self) -> None:
        """损坏 / 缺 ticket_ref / 空 payload → None,不抛。"""
        t = _make_ticket(feedback_payload="{not json")
        assert ReviewTicketDetailResponse.from_ticket(t).feedback_notification_id is None
        t2 = _make_ticket(feedback_payload=None)
        assert ReviewTicketDetailResponse.from_ticket(t2).feedback_notification_id is None
        t3 = _make_ticket(feedback_payload='{"no_ref": true}')
        assert ReviewTicketDetailResponse.from_ticket(t3).feedback_notification_id is None


class TestWorkflowReviewResponse:
    def test_from_outcome_success(self) -> None:
        outcome = TicketActionOutcome(
            ticket_id="T-001",
            status=GovernanceStatus.CLOSED,
            close_reason="user_disputed_approved",
        )
        resp = WorkflowReviewResponse.from_outcome(outcome)
        assert resp.ticket_id == "T-001"
        assert resp.governance_status == "closed"
        assert resp.close_reason == "user_disputed_approved"

    def test_from_outcome_serializable(self) -> None:
        outcome = TicketActionOutcome(
            ticket_id="T-002", status=GovernanceStatus.WAITING_REVIEW,
        )
        data = WorkflowReviewResponse.from_outcome(outcome).model_dump()
        assert data["governance_status"] == "waiting_review"
        assert data["close_reason"] is None