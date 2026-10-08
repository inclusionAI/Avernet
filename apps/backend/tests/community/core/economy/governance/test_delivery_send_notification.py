"""Tests for GovernanceDeliveryService.send_notification — 投递域统一发送出口。

covers tickets-remind-content-divergence SDD Task 2:验证单条发送出口的
branch behavior(tc_card 成功 / tc_card 降级 markdown / markdown 直发)与
title 口径(按 notify_type 区分 FIRST_SEND/REMINDER)。

该出口是三条投递路径(create_and_send_reminder / _run_delivery / scan cron)
的统一发送点;Task 4 在此基础上断言"手动补发 == cron reminder 字段一致"。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from agentclaw.community.core.economy.governance.domain.enums import NotifyType
from agentclaw.community.core.economy.governance.domain.notification import (
    FrozenSnapshot,
    GovernanceNotification,
)
from agentclaw.community.core.economy.governance.services.delivery_service import (
    GovernanceDeliveryService,
    SendResult,
)
from agentclaw.community.core.economy.governance.services.notify_render_service import (
    NotifyRenderService,
)
from agentclaw.community.plugin_api.notify_sender import NotifyMessage

from .conftest import FakeDB, FakeGovernanceConfig
from sqlalchemy.orm import sessionmaker
from agentclaw.community.core.repository.implementations.governance.audit import GovernanceAuditRepository
from agentclaw.community.core.repository.implementations.governance.notify_log import NotifyLogRepository
from agentclaw.community.core.repository.implementations.governance.task_record import TaskRecordRepository
from agentclaw.community.core.economy.governance.services.lifecycle_service import (
    GovernanceLifecycleService,
)


# ---------------------------------------------------------------------------
# Test fakes
# ---------------------------------------------------------------------------


@dataclass
class _CapturingSender:
    """Capture every NotifyMessage sent; configurable return id.

    ``sent`` accumulates ``(msg, channel)`` tuples so tests can assert the
    exact title/body/deep_link/extra/channel that hit the sender.
    """

    return_id: str | None = "ext-1"
    sent: list[tuple[NotifyMessage, str]] = field(default_factory=list)

    @property
    def channels(self) -> frozenset[str]:
        return frozenset({"markdown", "tc_card"})

    def send(self, message: NotifyMessage, *, channel: str = "markdown") -> str | None:
        self.sent.append((message, channel))
        return self.return_id


class _NoopRender(NotifyRenderService):
    """Render service whose build_send_payload always returns None.

    Used to exercise the tc_card → markdown degrade branch of
    send_notification (build_send_payload returning None means "card
    construction failed → degrade").
    """

    def build_send_payload(self, *args: Any, **kwargs: Any):  # type: ignore[override]
        return None


def _make_notify(
    *,
    notify_type: NotifyType,
    channel: str,
    notification_md: str = "## body-md",
    notification_structured: str | None = None,
    bot_name: str = "bot-x",
    owner_id: str = "owner-x",
) -> GovernanceNotification:
    """Build a GovernanceNotification domain model without touching the DB."""
    return GovernanceNotification.create(
        notification_id="nid-1",
        ticket_id="tkt-1",
        bot_id="bot-x",
        bot_name=bot_name,
        owner_id=owner_id,
        worker_id=f"{owner_id}:bot-x",
        snapshot=FrozenSnapshot(
            dt_version="20260720",
            decision_at_create="actionable",
            triggered_dimensions="low_efficiency",
            hit_dimensions_count=1,
            severity="P3",
            estimated_saving_tokens=1000,
            saving_ratio=0.2,
            notification_md=notification_md,
            notification_structured=notification_structured,
        ),
        notify_type=notify_type,
        notify_source="test",
        channel=channel,
    )


def _build_svc(
    engine,
    *,
    sender: _CapturingSender,
    config: FakeGovernanceConfig,
    render_svc: NotifyRenderService | None = None,
) -> GovernanceDeliveryService:
    """Construct GovernanceDeliveryService with injected fakes (no DB needed)."""
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    db = FakeDB(lambda: Session(bind=engine))
    notify_repo = NotifyLogRepository(db=db)
    task_repo = TaskRecordRepository(db=db)
    audit_repo = GovernanceAuditRepository(db=db)
    lifecycle_svc = GovernanceLifecycleService(
        task_repo=task_repo, notify_repo=notify_repo, audit_repo=audit_repo,
    )
    return GovernanceDeliveryService(
        notify_repo=notify_repo,
        audit_repo=audit_repo,
        task_repo=task_repo,
        config=config,
        notify_sender=sender,
        render_svc=render_svc or NotifyRenderService(),
        lifecycle_svc=lifecycle_svc,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestSendNotificationChannels:
    """send_notification 三分支 + title 口径。"""

    def test_markdown_channel_uses_notification_md(self, engine):
        """markdown 配置:body=notification_md、deep_link 空、extra 空、title 按 type。"""
        sender = _CapturingSender(return_id="ext-md")
        svc = _build_svc(engine, sender=sender, config=FakeGovernanceConfig(notify_channel="markdown"))
        notify = _make_notify(notify_type=NotifyType.REMINDER, channel="markdown")

        result = svc.send_notification(notify)

        assert isinstance(result, SendResult)
        assert result.success is True
        assert result.external_message_id == "ext-md"
        assert result.actual_channel == "markdown"

        assert len(sender.sent) == 1
        msg, channel = sender.sent[0]
        assert channel == "markdown"
        assert msg.title == "⚠️ 治理通知提醒"
        assert msg.body == "## body-md"
        assert msg.deep_link == ""
        assert msg.extra == {}
        assert msg.recipient == "owner-x"

    def test_first_send_title(self, engine):
        """FIRST_SEND title=🔔 Bot 治理通知。"""
        sender = _CapturingSender(return_id="ext-md")
        svc = _build_svc(engine, sender=sender, config=FakeGovernanceConfig(notify_channel="markdown"))
        notify = _make_notify(notify_type=NotifyType.FIRST_SEND, channel="markdown")

        svc.send_notification(notify)

        msg, _ = sender.sent[0]
        assert msg.title == "🔔 Bot 治理通知"

    def test_tc_card_success(self, engine):
        """tc_card 配置:走卡片壳渲染,deep_link 非空、extra 含卡片字段、channel=tc_card。"""
        sender = _CapturingSender(return_id="ext-card")
        svc = _build_svc(
            engine, sender=sender,
            config=FakeGovernanceConfig(notify_channel="tc_card"),
        )
        notify = _make_notify(
            notify_type=NotifyType.FIRST_SEND, channel="tc_card",
            notification_structured='{"meta": {"botName": "bot-x"}}',
        )

        result = svc.send_notification(notify)

        assert result.success is True
        assert result.actual_channel == "tc_card"
        assert len(sender.sent) == 1
        msg, channel = sender.sent[0]
        assert channel == "tc_card"
        assert msg.deep_link != ""
        assert msg.extra.get("bot_id") == "bot-x"
        assert "card_id" in msg.extra
        assert "notification_data" in msg.extra
        # tc_card body 是卡片壳 reason(非 notification_md)
        assert msg.body != "## body-md"

    def test_tc_card_degrade_to_markdown_on_build_failure(self, engine):
        """tc_card 但 build_send_payload 返 None → 降级 markdown,body=notification_md。"""
        sender = _CapturingSender(return_id="ext-md")
        svc = _build_svc(
            engine, sender=sender,
            config=FakeGovernanceConfig(notify_channel="tc_card"),
            render_svc=_NoopRender(),
        )
        notify = _make_notify(
            notify_type=NotifyType.REMINDER, channel="tc_card",
            notification_md="## degraded-body",
        )

        result = svc.send_notification(notify)

        assert result.success is True
        assert result.actual_channel == "markdown"
        msg, channel = sender.sent[0]
        assert channel == "markdown"
        assert msg.body == "## degraded-body"
        assert msg.deep_link == ""

    def test_send_failure_returns_unsuccessful(self, engine):
        """sender.send 返 None → SendResult.success=False,无 external id。"""
        sender = _CapturingSender(return_id=None)
        svc = _build_svc(engine, sender=sender, config=FakeGovernanceConfig(notify_channel="markdown"))
        notify = _make_notify(notify_type=NotifyType.REMINDER, channel="markdown")

        result = svc.send_notification(notify)

        assert result.success is False
        assert result.external_message_id is None

    def test_override_recipient(self, engine):
        """override_recipient 覆盖收件人(deliver_by_worker 用口径)。"""
        sender = _CapturingSender(return_id="ext-md")
        svc = _build_svc(engine, sender=sender, config=FakeGovernanceConfig(notify_channel="markdown"))
        notify = _make_notify(notify_type=NotifyType.REMINDER, channel="markdown", owner_id="owner-x")

        svc.send_notification(notify, override_recipient="override-staff")

        msg, _ = sender.sent[0]
        assert msg.recipient == "override-staff"