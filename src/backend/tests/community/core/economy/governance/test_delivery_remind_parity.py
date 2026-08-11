"""Contract test: manual remind (create_and_send_reminder) and the unified
send outlet (send_notification, which cron reminder will also route through
after Task 5) emit byte-identical NotifyMessages for the same ticket.

This nails the tickets-remind-content-divergence SDD core acceptance: the
manual remind endpoint must produce the same channel / card shell /
detailLink / extra as the first-send / cron reminder, not a divergent
bare-markdown message.

Approach: seed one active ticket; run create_and_send_reminder with a
capturing sender (captures msg_A); then build the "cron reminder caliber"
notify_row from the same ticket (same channel=config.notify_channel,
notify_type=REMINDER, snapshot from ticket fields + render_reminder_md)
and send it directly via send_notification (captures msg_B). Assert
msg_A and msg_B are field-equal across both tc_card and markdown configs.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy.orm import sessionmaker

from agentclaw.community.core.economy.governance.domain.enums import NotifyType
from agentclaw.community.core.economy.governance.domain.notification import (
    FrozenSnapshot,
    GovernanceNotification,
)
from agentclaw.community.core.repository.implementations.governance.audit import GovernanceAuditRepository
from agentclaw.community.core.repository.implementations.governance.notify_log import NotifyLogRepository
from agentclaw.community.core.economy.governance.orm import GovernanceTicketOrm
from agentclaw.community.core.repository.implementations.governance.task_record import TaskRecordRepository
from agentclaw.community.core.economy.governance.services.delivery_service import (
    GovernanceDeliveryService,
)
from agentclaw.community.core.economy.governance.services.lifecycle_service import (
    GovernanceLifecycleService,
)
from agentclaw.community.core.economy.governance.services.notify_render_service import (
    NotifyRenderService,
)
from agentclaw.community.plugin_api.notify_sender import NotifyMessage

from .conftest import FakeDB, FakeGovernanceConfig


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


@dataclass
class _CapturingSender:
    """Capture every (msg, channel) sent; deterministic return id."""

    return_id: str | None = "ext-1"
    sent: list[tuple[NotifyMessage, str]] = field(default_factory=list)

    @property
    def channels(self) -> frozenset[str]:
        return frozenset({"markdown", "tc_card"})

    def send(self, message: NotifyMessage, *, channel: str = "markdown") -> str | None:
        self.sent.append((message, channel))
        return self.return_id


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_WORKER_ID = "owner-par:bot-par"
_BOT_ID = "bot-par"
_OWNER_ID = "owner-par"
_TICKET_ID = "tkt-parity-1"

# A non-trivial notification_structured payload so tc_card rendering exercises
# the real card shell + detailLink + extra branches (not the empty fallback).
_NOTIFICATION_STRUCTURED = (
    '{"meta":{"botName":"Parity Bot","hit_dimensions":["low_efficiency"],'
    '"daily_tokens":120000},"title":"Parity","problem_summary":"p","action_items":[]}'
)


def _build_svc(engine, *, sender: _CapturingSender, config: FakeGovernanceConfig) -> GovernanceDeliveryService:
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
        render_svc=NotifyRenderService(),
        lifecycle_svc=lifecycle_svc,
    )


def _seed_active_ticket(engine) -> None:
    """Insert one active ticket with a rich snapshot for parity testing."""
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    db = FakeDB(lambda: Session(bind=engine))
    task_repo = TaskRecordRepository(db=db)
    row = GovernanceTicketOrm(
        worker_id=_WORKER_ID,
        bot_id=_BOT_ID,
        owner_id=_OWNER_ID,
        owner_name="Parity Owner",
        dt_version="20260720",
        governance_decision="actionable",
        bot_name="Parity Bot",
        hit_dimensions="low_efficiency",
        hit_dimensions_count=1,
        governance_max_priority="P3",
        expected_token_saving=1000,
        saving_ratio=0.2,
        task_summary="parity summary",
        notification_structured=_NOTIFICATION_STRUCTURED,
        analysis_status="done",
        last_sync_at=datetime.now(),
        ticket_id=_TICKET_ID,
        active_worker=_WORKER_ID,
        governance_status="open",
    )
    task_repo.insert_ticket(row)


def _build_cron_caliber_notify(svc: GovernanceDeliveryService) -> GovernanceNotification:
    """Build the 'cron reminder' caliber notify_row for the seeded ticket.

    Mirrors what scan_service cron reminder builds: channel from config,
    notify_type=REMINDER, snapshot from ticket fields, notification_md via
    render_reminder_md. This is the reference the unified outlet must match.
    """
    ticket = svc._task_repo.find_active_ticket(_WORKER_ID)
    assert ticket is not None, "seed ticket not found"
    notification_md = svc._render_svc.render_reminder_md(ticket, now=datetime.now())
    return GovernanceNotification.create(
        notification_id="nid-cron-ref",
        ticket_id=ticket.ticket_id,
        bot_id=ticket.bot_id,
        bot_name=ticket.bot_name,
        owner_id=ticket.owner_id,
        worker_id=ticket.worker_id,
        snapshot=FrozenSnapshot(
            dt_version=ticket.dt_version,
            decision_at_create=ticket.current_decision or "actionable",
            triggered_dimensions=ticket.triggered_dimensions,
            hit_dimensions_count=ticket.hit_dimensions_count,
            severity=ticket.severity,
            estimated_saving_tokens=ticket.estimated_saving_tokens,
            saving_ratio=ticket.saving_ratio,
            notification_md=notification_md,
            notification_structured=ticket.notification_structured,
        ),
        notify_type=NotifyType.REMINDER,
        notify_source="online_cron",
        channel=svc._config.notify_channel,
    )


def _assert_msgs_equal(a: NotifyMessage, b: NotifyMessage, *, channel: str) -> None:
    """Assert two captured NotifyMessages are field-identical.

    For tc_card, ``deep_link`` embeds the per-notification id (base64-encoded
    into the URL), so two legitimately different notification ids yield
    different link literals — we assert same structure (non-empty, same
    prefix) + same body/title/recipient and same extra *shape* (card_id /
    bot_id equal; notification_data present) rather than byte-identical
    deep_link/extra. For markdown, deep_link is empty and extra is empty,
    so strict equality holds.
    """
    assert a.title == b.title, f"title mismatch: {a.title!r} vs {b.title!r}"
    assert a.body == b.body, "body mismatch"
    assert a.recipient == b.recipient, "recipient mismatch"
    if channel == "tc_card":
        # Same card-shell structure, not byte-identical (notification_id differs).
        assert a.deep_link and b.deep_link, "tc_card deep_link must be non-empty"
        assert a.deep_link.split("?")[0] == b.deep_link.split("?")[0]
        assert a.extra.get("bot_id") == b.extra.get("bot_id")
        assert a.extra.get("card_id") == b.extra.get("card_id")
        assert "notification_data" in a.extra and "notification_data" in b.extra
    else:
        assert a.deep_link == b.deep_link, "deep_link mismatch"
        assert a.extra == b.extra, f"extra mismatch: {a.extra} vs {b.extra}"


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestRemindParity:
    """手动补发与 cron reminder 统一口径 — 字段级一致。"""

    def test_markdown_config_remind_matches_cron_caliber(self, engine, tables):
        """markdown 配置:手动补发 msg == cron reminder caliber msg。"""
        _seed_active_ticket(engine)
        sender = _CapturingSender(return_id="ext-md")
        svc = _build_svc(engine, sender=sender, config=FakeGovernanceConfig(notify_channel="markdown"))

        # Path A: manual remind (creates notify_row internally + sends via outlet)
        svc.create_and_send_reminder(_WORKER_ID, operator="op-1")
        msg_manual = sender.sent[-1][0]

        # Path B: cron-reminder caliber notify_row sent via the same outlet
        sender.sent.clear()
        cron_notify = _build_cron_caliber_notify(svc)
        svc.send_notification(cron_notify)
        msg_cron = sender.sent[-1][0]
        channel = sender.sent[-1][1]

        assert channel == "markdown"
        _assert_msgs_equal(msg_manual, msg_cron, channel=channel)
        # explicit baseline assertions for the regression this spec fixes
        assert msg_manual.title == "⚠️ 治理通知提醒"
        assert msg_manual.deep_link == ""
        assert msg_manual.extra == {}

    def test_tc_card_config_remind_matches_cron_caliber(self, engine, tables):
        """tc_card 配置:手动补发 msg == cron reminder caliber msg(卡片壳 + detailLink 非空)。"""
        _seed_active_ticket(engine)
        sender = _CapturingSender(return_id="ext-card")
        svc = _build_svc(engine, sender=sender, config=FakeGovernanceConfig(notify_channel="tc_card"))

        svc.create_and_send_reminder(_WORKER_ID, operator="op-1")
        msg_manual = sender.sent[-1][0]
        manual_channel = sender.sent[-1][1]

        sender.sent.clear()
        cron_notify = _build_cron_caliber_notify(svc)
        svc.send_notification(cron_notify)
        msg_cron = sender.sent[-1][0]

        # Both must have gone out on tc_card (the regression: manual used to
        # always send bare markdown even when configured tc_card).
        assert manual_channel == "tc_card"
        assert sender.sent[-1][1] == "tc_card"
        _assert_msgs_equal(msg_manual, msg_cron, channel="tc_card")
        # explicit baseline: manual remind now carries a real detailLink + extra
        assert msg_manual.deep_link != ""
        assert msg_manual.extra.get("bot_id") == _BOT_ID
        assert "card_id" in msg_manual.extra
        assert "notification_data" in msg_manual.extra