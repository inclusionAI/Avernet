"""Tests for NotifyRenderService — pure rendering, no DB / no notify_sender.

收口自 scan `_render_reminder_md` / `_build_tc_card_payload` 与
record_process `_render_notification_md`。本套测试验证:
  1. 首通通知 Markdown(标准 / 重新治理模板)与原 record_process 行为一致。
  2. 提醒通知 Markdown 与原 scan `_render_reminder_md` 行为一致(overdue_days 推算)。
  3. `build_send_payload` 正常构建 TC 卡片产物(body/deep_link/extra)。
  4. `build_send_payload` 在 builder 抛异常时返 None(降级信号,与原
     `_build_tc_card_payload` 返回 None 语义一致)。
  5. 空字段(record.bot_name=None / ticket 字段缺省)不致渲染崩溃。
"""
from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from agentclaw.community.core.economy.governance.domain.enums import (
    NotifyType,
)
from agentclaw.community.core.economy.governance.domain.notification import (
    FrozenSnapshot,
    GovernanceNotification,
)
from agentclaw.community.core.economy.governance.domain.record import (
    GovernanceRecord,
)
from agentclaw.community.core.economy.governance.domain.ticket import (
    GovernanceTicket,
    MutableSnapshot,
)
from agentclaw.community.core.economy.governance.services.notify_render_service import (
    NotifyRenderService,
    SendPayload,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def render_svc() -> NotifyRenderService:
    return NotifyRenderService()


def _sample_record(**overrides) -> GovernanceRecord:
    """镜像 test_record_process_service._sample_record 的最小 record。"""
    base = dict(
        owner_id="staff-001",
        bot_id="bot-001",
        bot_name="TestBot",
        governance_decision="actionable",
        dt_version="20260705",
        hit_dimensions="token_usage",
        hit_dimensions_count=3,
        governance_max_priority="high",
        expected_token_saving=1000,
        saving_ratio=0.5,
        task_summary="Token saving opportunity",
        notification_structured=None,
        analysis_status="completed",
    )
    base.update(overrides)
    return GovernanceRecord(**base)


def _sample_ticket(*, last_sync_at: datetime | None = None) -> GovernanceTicket:
    snapshot = MutableSnapshot(
        dt_version="20260705",
        initial_decision="actionable",
        current_decision="actionable",
        triggered_dimensions="token_usage",
        hit_dimensions_count=3,
        severity="high",
        estimated_saving_tokens=1000,
        saving_ratio=0.5,
        task_summary="Token saving opportunity",
        notification_structured=None,
        analysis_status="completed",
        consecutive_normal_days=0,
        last_decision_dt_version=None,
        last_seen_at=last_sync_at,
        last_sync_at=last_sync_at,
    )
    return GovernanceTicket.create(
        ticket_id="t-1",
        worker_id="staff-001:bot-001",
        bot_id="bot-001",
        owner_id="staff-001",
        owner_name=None,
        bot_name="TestBot",
        snapshot=snapshot,
    )


def _sample_notification(**overrides) -> GovernanceNotification:
    snapshot = FrozenSnapshot(
        dt_version="20260705",
        decision_at_create="actionable",
        triggered_dimensions="token_usage",
        hit_dimensions_count=3,
        severity="high",
        estimated_saving_tokens=1000,
        saving_ratio=0.5,
        notification_md="#### 🔔 Bot 治理通知",
        notification_structured=None,
    )
    base = dict(
        notification_id="n-1",
        ticket_id="t-1",
        bot_id="bot-001",
        bot_name="TestBot",
        owner_id="staff-001",
        worker_id="staff-001:bot-001",
        snapshot=snapshot,
        notify_type=NotifyType.FIRST_SEND,
        notify_source="online_cron",
        channel="tc_card",
    )
    base.update(overrides)
    return GovernanceNotification.create(**base)


def _config(**overrides):
    return SimpleNamespace(
        tc_card_id="card-1",
        tc_card_preview_url="https://preview.example.com",
        iframe_callback_url="https://cb.example.com",
        **overrides,
    )


# ---------------------------------------------------------------------------
# render_first_notification_md
# ---------------------------------------------------------------------------


class TestRenderFirstNotificationMd:
    def test_standard_template_uses_builder(self, render_svc):
        rec = _sample_record()
        md = render_svc.render_first_notification_md(rec, dt_version="20260705")
        assert "TestBot" in md
        # builder 把 dt_version 格式化为采样日期(如 2026-07-05),断言月份片段
        assert "2026" in md
        # builder 产出的非空正文
        assert md.strip() != ""

    def test_reopen_template_with_ref_time(self, render_svc):
        rec = _sample_record()
        ref = datetime(2026, 7, 1, 10, 30)
        md = render_svc.render_first_notification_md(
            rec,
            dt_version="20260705",
            use_reopen_template=True,
            reopen_ref_time=ref,
        )
        assert "🔄 重新治理通知" in md
        assert "2026-07-01 10:30" in md
        assert "20260705" in md
        assert "token_usage" in md

    def test_reopen_template_without_ref_time_falls_back_to_zhanci(self, render_svc):
        rec = _sample_record()
        md = render_svc.render_first_notification_md(
            rec,
            dt_version="20260705",
            use_reopen_template=True,
            reopen_ref_time=None,
        )
        assert "之前" in md

    def test_bot_name_none_does_not_crash(self, render_svc):
        rec = _sample_record(bot_name=None)
        md = render_svc.render_first_notification_md(rec, dt_version="20260705")
        assert isinstance(md, str)

    def test_reopen_template_bot_name_none_shows_unknown(self, render_svc):
        rec = _sample_record(bot_name=None)
        md = render_svc.render_first_notification_md(
            rec,
            dt_version="20260705",
            use_reopen_template=True,
            reopen_ref_time=datetime(2026, 7, 1),
        )
        assert "未知Bot" in md


# ---------------------------------------------------------------------------
# render_reminder_md
# ---------------------------------------------------------------------------


class TestRenderReminderMd:
    def test_with_last_sync_at_computes_overdue_days(self, render_svc):
        now = datetime(2026, 7, 5)
        last_sync = now - timedelta(days=3)
        ticket = _sample_ticket(last_sync_at=last_sync)
        md = render_svc.render_reminder_md(ticket, now=now)
        # overdue_days=3 → builder 输出含 bot name
        assert "TestBot" in md

    def test_without_last_sync_at_days_zero(self, render_svc):
        ticket = _sample_ticket(last_sync_at=None)
        md = render_svc.render_reminder_md(ticket, now=datetime(2026, 7, 5))
        assert isinstance(md, str)
        assert md.strip() != ""


# ---------------------------------------------------------------------------
# build_send_payload
# ---------------------------------------------------------------------------


class TestBuildSendPayload:
    def test_success_returns_payload_with_extras(self, render_svc):
        notify = _sample_notification()
        payload = render_svc.build_send_payload(
            notify, user_id="staff-001", config=_config(),
        )
        assert payload is not None
        assert isinstance(payload, SendPayload)
        assert payload.body  # 简化 reason 非空
        assert payload.deep_link  # 详情链接非空
        assert payload.extra["bot_id"] == "bot-001"
        assert payload.extra["card_id"] == "card-1"
        assert "notification_data" in payload.extra
        assert payload.extra["out_track_id_prefix"] == "gov-notify"

    def test_failure_returns_none_for_degradation(self, render_svc, monkeypatch):
        """builder 抛异常 → build_send_payload 返 None(调用方降级 markdown 信号)。"""
        from agentclaw.community.core.economy.governance.services import (
            notify_render_service as mod,
        )

        def _boom(*a, **kw):
            raise RuntimeError("builder exploded")

        monkeypatch.setattr(mod, "build_governance_reason", _boom)
        notify = _sample_notification()
        payload = render_svc.build_send_payload(
            notify, user_id="staff-001", config=_config(),
        )
        assert payload is None

    def test_empty_fields_does_not_crash(self, render_svc):
        notify = _sample_notification(
            bot_name=None,
        )
        # 不应抛
        payload = render_svc.build_send_payload(
            notify, user_id="staff-001", config=_config(),
        )
        # bot_name=None 时 builder 内部有兜底,仍应成功
        assert payload is not None
