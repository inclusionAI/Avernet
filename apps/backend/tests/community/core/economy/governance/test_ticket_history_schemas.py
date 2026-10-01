"""Tests for TicketHistoryItem / TicketHistoryByWorkerResponse schemas.

验证按 worker 查工单历史的响应序列化:from_ticket 从 GovernanceTicket.to_dict()
取值、datetime→ISO、空字段安全;响应壳默认值与回显字段。
"""
from __future__ import annotations

from datetime import datetime

from agentclaw.community.adapters.http.economy.schemas import (
    TicketHistoryByWorkerResponse,
    TicketHistoryItem,
)
from agentclaw.community.core.economy.governance.domain.enums import (
    GovernanceStatus,
)

from tests.community.core.economy.governance.test_review_schemas import (
    _make_ticket,
)


class TestTicketHistoryItemFromTicket:
    def test_full_field_mapping(self) -> None:
        t = _make_ticket(
            governance_status=GovernanceStatus.CLOSED,
            close_reason="admin_closed",
            closed_at=datetime(2026, 7, 15, 9, 0, 0),
            close_conclusion="false_positive",
            close_payload='{"remark": "误报"}',
            cooldown_until=datetime(2026, 7, 29, 9, 0, 0),
            review_decision="approve_close",
            reviewed_by="admin-1",
            reviewed_at=datetime(2026, 7, 15, 9, 5, 0),
            review_remark="confirmed fp",
            review_reason="user_disputed",
        )
        item = TicketHistoryItem.from_ticket(t)

        # 身份
        assert item.ticket_id == "T-001"
        assert item.worker_id == "owner-1:bot-1"
        assert item.bot_id == "bot-1"
        assert item.owner_id == "owner-1"
        assert item.bot_name == "TestBot"
        # 治理态(enum→str)
        assert item.governance_status == "closed"
        # 关单历史(决策核心)
        assert item.close_reason == "admin_closed"
        assert item.close_conclusion == "false_positive"
        assert item.close_payload == '{"remark": "误报"}'
        # datetime → ISO
        assert item.closed_at == "2026-07-15T09:00:00"
        assert item.cooldown_until == "2026-07-29T09:00:00"
        assert item.gmt_create == "2026-07-09T08:00:00"
        # 用户反馈
        assert item.response == "dispute"
        assert item.response_remark == "user disputed"
        assert item.response_source == "http_api"
        assert item.response_at == "2026-07-10T08:00:00"
        # 审批记录
        assert item.review_decision == "approve_close"
        assert item.reviewed_by == "admin-1"
        assert item.reviewed_at == "2026-07-15T09:05:00"
        assert item.review_remark == "confirmed fp"
        # 命中维度快照(字段名跟 to_dict 原始列名)
        assert item.hit_dimensions == "token_usage,cost"
        assert item.hit_dimensions_count == 2
        assert item.governance_max_priority == "P1"
        assert item.saving_ratio == 0.5
        assert item.token_baseline == 1500000
        assert item.dt_version == "20260710"

    def test_empty_close_fields_none_safe(self) -> None:
        """未关单 / 无审批 / 无反馈的字段应为 None(非 to_dict 的其他列被忽略)。"""
        t = _make_ticket(
            governance_status=GovernanceStatus.OPEN,
            user_feedback=None,
            close_reason=None,
            close_conclusion=None,
            close_payload=None,
            review_decision=None,
        )
        item = TicketHistoryItem.from_ticket(t)
        assert item.close_reason is None
        assert item.close_conclusion is None
        assert item.close_payload is None
        assert item.closed_at is None
        assert item.response is None
        assert item.review_decision is None
        # 命中维度类回退到 snapshot 默认
        assert item.hit_dimensions == "token_usage,cost"
        assert item.saving_ratio == 0.5

    def test_ignores_to_dict_only_fields(self) -> None:
        """from_ticket 只取本 schema 声明的键 — remind_at / active_worker 不在 schema。"""
        t = _make_ticket()
        item = TicketHistoryItem.from_ticket(t)
        dumped = item.model_dump()
        # schema 不含这些决策非关键列
        assert "remind_at" not in dumped
        assert "active_worker" not in dumped
        assert "remind_count" not in dumped


class TestTicketHistoryByWorkerResponse:
    def test_defaults(self) -> None:
        resp = TicketHistoryByWorkerResponse()
        assert resp.items == []
        assert resp.limit == 5
        assert resp.worker_id is None
        assert resp.owner_id is None
        assert resp.bot_id is None

    def test_with_items_and_echo(self) -> None:
        t = _make_ticket()
        resp = TicketHistoryByWorkerResponse(
            worker_id="owner-1:bot-1",
            owner_id="owner-1",
            bot_id="bot-1",
            items=[TicketHistoryItem.from_ticket(t)],
            limit=5,
        )
        assert resp.worker_id == "owner-1:bot-1"
        assert len(resp.items) == 1
        assert resp.items[0].ticket_id == "T-001"
        # JSON 序列化往返(确认 datetime 已是 ISO 字符串)
        dumped = resp.model_dump(mode="json")
        assert isinstance(dumped["items"][0]["gmt_create"], str)
        assert dumped["items"][0]["closed_at"] is None
