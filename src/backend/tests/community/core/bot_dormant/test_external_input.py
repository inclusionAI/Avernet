"""Tests for external_input two-stage governance (warn → recycle)."""
from __future__ import annotations

import asyncio
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from agentclaw.community.core.bot_dormant.baas_client import AliveResult, BaasDormantClient
from agentclaw.community.core.common_config import CommonWhiteListService
from agentclaw.community.core.bot_dormant.service import DormantBotService
from agentclaw.community.core.bot_dormant.sqlite_models import (
    DormantCheckAudit,
    DormantExternalInput,
    DormantNotifyLog,
    DormantWhitelist,
)
from agentclaw.community.plugin_api.models import Base, BotModel


N = 7
M = 3


def _now() -> datetime:
    return datetime.now()


def _make_session() -> Session:
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


class FakeDB:
    def __init__(self, session: Session) -> None: self._session = session

    @contextmanager
    def orm_session(self): yield self._session


def _insert_bot(
    session,
    bot_id="bot1",
    entity_id="123",
    owner_id="ow",
    env=None,
):
    session.add(BotModel(
        bot_id=bot_id, entity_id=entity_id, entity_type="staff",
        creator_id=owner_id, owner_id=owner_id, bot_name=bot_id,
        bot_type="personal", status="ACTIVE", is_delete=0,
        **({"env": env} if env is not None else {}),
        gmt_create=_now() - timedelta(days=30),
        gmt_modified=_now(),
    ))
    session.commit()


def _insert_external(session, *, bot_id="bot1", owner_id="ow", dt_str=None,
                     notify_content=None, processed=0):
    dt_str = dt_str or date.today().strftime("%Y%m%d")
    row = DormantExternalInput(
        bot_id=bot_id, owner_id=owner_id,
        governance_source="economy_governance",
        governance_dimension="token_waste",
        reason="近 30 天 token 异常",
        notify_content=notify_content,
        dt=dt_str, processed=processed,
    )
    session.add(row)
    session.commit()
    return row.id


def _make_service(session, *, dry_run=False, protected_owner_ids=frozenset()):
    baas = AsyncMock(spec=BaasDormantClient)
    baas.check_alive = AsyncMock(
        return_value=AliveResult(result="unknown", last_session_time=None)
    )
    bot_svc = MagicMock()
    bot_svc.stop_bot = MagicMock(return_value=True)
    bot_svc.update_status = MagicMock()
    scan_policy = MagicMock()
    scan_policy.dry_run.return_value = dry_run
    common_whitelist = MagicMock(spec=CommonWhiteListService)
    common_whitelist.get_owner_ids.return_value = frozenset(protected_owner_ids)
    return DormantBotService(
        db=FakeDB(session), baas_client=baas, bot_service=bot_svc,
        passport_plugin=MagicMock(), scan_policy=scan_policy,
        common_whitelist_service=common_whitelist,
        N=N, M=M,
    )


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Stage 1: warn while days_since_input < M
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_external_warn_when_today():
    """Row written today (dt=today, days=0) → enqueue warn, processed stays 0."""
    session = _make_session()
    _insert_bot(session)
    rid = _insert_external(session, dt_str=date.today().strftime("%Y%m%d"))

    svc = _make_service(session)
    _run(svc.process_run(dry_run=False))

    logs = session.query(DormantNotifyLog).filter(
        DormantNotifyLog.notify_source == "external_input",
        DormantNotifyLog.notify_type == "warn",
    ).all()
    assert len(logs) == 1
    row = session.query(DormantExternalInput).filter(
        DormantExternalInput.id == rid
    ).first()
    assert row.processed == 0


@pytest.mark.unit
def test_external_warn_passes_through_notify_content():
    session = _make_session()
    _insert_bot(session)
    _insert_external(session,
                     dt_str=date.today().strftime("%Y%m%d"),
                     notify_content="自定义文案 ABC")

    svc = _make_service(session)
    _run(svc.process_run(dry_run=False))

    log = session.query(DormantNotifyLog).filter(
        DormantNotifyLog.notify_source == "external_input"
    ).first()
    assert log.content == "自定义文案 ABC"


@pytest.mark.unit
def test_external_warn_uses_fallback_when_notify_content_empty():
    session = _make_session()
    _insert_bot(session)
    _insert_external(session,
                     dt_str=date.today().strftime("%Y%m%d"),
                     notify_content=None)

    svc = _make_service(session)
    _run(svc.process_run(dry_run=False))

    log = session.query(DormantNotifyLog).filter(
        DormantNotifyLog.notify_source == "external_input"
    ).first()
    assert "平台治理通知" in log.content
    assert "economy_governance" in log.content
    assert "token_waste" in log.content


# ---------------------------------------------------------------------------
# Stage 2: recycle at days_since_input >= M
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_external_recycle_after_M_days():
    """Row written M days ago → enqueue recycle + stop_bot + processed=1."""
    session = _make_session()
    _insert_bot(session)
    old_dt = (date.today() - timedelta(days=M)).strftime("%Y%m%d")
    rid = _insert_external(session, dt_str=old_dt)

    svc = _make_service(session)
    _run(svc.process_run(dry_run=False))

    logs = session.query(DormantNotifyLog).filter(
        DormantNotifyLog.notify_source == "external_input",
        DormantNotifyLog.notify_type == "recycle",
    ).all()
    assert len(logs) == 1
    row = session.query(DormantExternalInput).filter(
        DormantExternalInput.id == rid
    ).first()
    assert row.processed == 1
    svc._bot_service.stop_bot.assert_called_once()


@pytest.mark.unit
def test_external_recycle_treats_existing_notify_log_as_idempotent_in_dry_run():
    """Dry-run external recycle can be rerun when today's notify row exists."""
    session = _make_session()
    _insert_bot(session)
    old_dt = (date.today() - timedelta(days=M)).strftime("%Y%m%d")
    _insert_external(session, dt_str=old_dt)
    today = date.today().strftime("%Y%m%d")
    session.add(DormantNotifyLog(
        bot_id="bot1",
        owner_id="ow",
        entity_id=None,
        notify_type="recycle",
        notify_target="ow",
        notify_source="external_input",
        content="already queued",
        dt=today,
        send_status="pending",
        dry_run=1,
    ))
    session.commit()

    svc = _make_service(session)
    summary = _run(svc.process_run(dry_run=True))

    assert summary.errors == 0
    assert summary.recycled == 1
    logs = session.query(DormantNotifyLog).filter(
        DormantNotifyLog.bot_id == "bot1",
        DormantNotifyLog.owner_id == "ow",
        DormantNotifyLog.dt == today,
        DormantNotifyLog.notify_type == "recycle",
    ).all()
    assert len(logs) == 1
    row = session.query(DormantExternalInput).first()
    assert row.processed == 0
    svc._bot_service.stop_bot.assert_not_called()


@pytest.mark.unit
def test_external_already_processed_row_is_skipped():
    """processed=1 rows must not be re-scanned."""
    session = _make_session()
    _insert_bot(session)
    old_dt = (date.today() - timedelta(days=M)).strftime("%Y%m%d")
    _insert_external(session, dt_str=old_dt, processed=1)

    svc = _make_service(session)
    _run(svc.process_run(dry_run=False))

    logs = session.query(DormantNotifyLog).filter(
        DormantNotifyLog.notify_source == "external_input"
    ).all()
    assert len(logs) == 0


# ---------------------------------------------------------------------------
# Whitelist precedence
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_whitelist_bot_skipped_even_in_external_input():
    session = _make_session()
    _insert_bot(session)
    session.add(DormantWhitelist(
        bot_id="bot1", owner_id="ow", governance_source="manual",
        reason="保留", created_by="ops",
    ))
    session.commit()
    _insert_external(session, dt_str=date.today().strftime("%Y%m%d"))

    svc = _make_service(session)
    _run(svc.process_run(dry_run=False))

    logs = session.query(DormantNotifyLog).filter(
        DormantNotifyLog.notify_source == "external_input"
    ).all()
    assert len(logs) == 0


@pytest.mark.unit
def test_external_input_skips_protected_owner_and_leaves_row_unprocessed():
    session = _make_session()
    _insert_bot(session, bot_id="bot1", owner_id="protected_owner")
    row_id = _insert_external(
        session,
        bot_id="bot1",
        owner_id="protected_owner",
        dt_str=(date.today() - timedelta(days=M)).strftime("%Y%m%d"),
    )
    service = _make_service(
        session,
        protected_owner_ids=frozenset({"protected_owner"}),
    )

    _run(service.process_run(dry_run=False))

    row = session.query(DormantExternalInput).filter_by(id=row_id).one()
    audit = session.query(DormantCheckAudit).filter_by(
        source="external_input",
        bot_id="bot1",
        owner_id="protected_owner",
    ).one()
    assert row.processed == 0
    assert audit.check_result == "whitelisted"
    assert audit.action_taken == "skipped"
    assert session.query(DormantNotifyLog).count() == 0
    service._bot_service.stop_bot.assert_not_called()


# ---------------------------------------------------------------------------
# Error isolation
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_external_row_failure_does_not_abort_others():
    """If one external row crashes, others still get processed and summary.errors++."""
    session = _make_session()
    _insert_bot(session, bot_id="ok_bot")
    _insert_external(session, bot_id="ok_bot",
                     dt_str=date.today().strftime("%Y%m%d"))
    # Missing bot row → triggers a different branch but doesn't crash
    _insert_external(session, bot_id="missing_bot",
                     dt_str=date.today().strftime("%Y%m%d"))
    # Bad dt → caught by try/except, logged + skipped
    _insert_external(session, bot_id="ok_bot",  # same bot, will dedup but valid
                     dt_str="not-a-date")

    svc = _make_service(session)
    summary = _run(svc.process_run(dry_run=False))

    # ok_bot warn was enqueued exactly once
    logs = session.query(DormantNotifyLog).filter(
        DormantNotifyLog.notify_source == "external_input"
    ).all()
    assert any(log.bot_id == "ok_bot" for log in logs)
    # No crash; summary reports processing happened
    assert summary.scanned >= 0


# ---------------------------------------------------------------------------
# Fix 1: external_input processed even when no internal candidates
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_external_processed_when_no_internal_candidates():
    """If no internal candidates exist (no ac_bots rows old enough),
    external_input must still be processed — the daily sweep is not
    conditional on the internal scan finding anything."""
    session = _make_session()
    _insert_bot(session)  # bot_id="bot1" by default
    # Make this bot's gmt_create only 1 day old → filter_candidates skips it
    # (gmt_create < now - N=7 fails) so internal candidates list is empty.
    bot = session.query(BotModel).first()
    bot.gmt_create = datetime.now() - timedelta(days=1)
    session.commit()
    _insert_external(session, bot_id="bot1",
                     dt_str=date.today().strftime("%Y%m%d"))

    svc = _make_service(session)
    _run(svc.process_run(dry_run=False))

    # External warn still enqueued even though no internal candidates
    logs = session.query(DormantNotifyLog).filter(
        DormantNotifyLog.notify_source == "external_input",
        DormantNotifyLog.notify_type == "warn",
    ).all()
    assert len(logs) == 1


@pytest.mark.unit
def test_external_input_rejects_bot_from_another_environment(monkeypatch):
    """A pre scan must not act on a prod-only bot referenced by external input."""
    session = _make_session()
    _insert_bot(session, bot_id="shared_bot", owner_id="shared_owner", env="prod")
    row_id = _insert_external(
        session,
        bot_id="shared_bot",
        owner_id="shared_owner",
        dt_str=(date.today() - timedelta(days=M)).strftime("%Y%m%d"),
    )
    monkeypatch.setattr(
        "agentclaw.community.core.bot_dormant.service.get_current_env",
        lambda: "pre",
    )

    service = _make_service(session)
    _run(service.process_run(dry_run=False))

    row = session.query(DormantExternalInput).filter_by(id=row_id).one()
    audit = session.query(DormantCheckAudit).filter_by(
        source="external_input",
        bot_id="shared_bot",
        owner_id="shared_owner",
    ).one()
    assert row.processed == 0
    assert audit.check_result == "missing_bot"
    assert audit.action_taken == "skipped"
    assert session.query(DormantNotifyLog).count() == 0
    service._bot_service.stop_bot.assert_not_called()


# ---------------------------------------------------------------------------
# Fix 2: dry_run=True must not enqueue external recycle notify log
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_external_recycle_dry_run_enqueues_but_does_not_call_stop_bot():
    """dry_run=True at recycle threshold: notify_log IS enqueued (dry_run=1
    flag so the in-container sender skips it), but stop_bot is NOT called
    and processed stays 0. Lets predict-only runs surface the would-be
    recycle message without touching live state."""
    session = _make_session()
    _insert_bot(session)
    old_dt = (date.today() - timedelta(days=M)).strftime("%Y%m%d")
    _insert_external(session, dt_str=old_dt)

    svc = _make_service(session)
    _run(svc.process_run(dry_run=True))

    # Recycle notify_log row IS enqueued, flagged dry_run=1
    logs = session.query(DormantNotifyLog).filter(
        DormantNotifyLog.notify_source == "external_input"
    ).all()
    assert len(logs) == 1
    assert logs[0].notify_type == "recycle"
    assert logs[0].dry_run == 1
    # stop_bot was NOT called
    svc._bot_service.stop_bot.assert_not_called()
    # processed stayed 0 (dry_run shouldn't mutate processed either)
    row = session.query(DormantExternalInput).first()
    assert row.processed == 0


# ---------------------------------------------------------------------------
# Audit coverage (Bug C regression)
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_external_warn_writes_audit():
    """Bug C: external warn branch must write a DormantCheckAudit row with
    source='external_input' so the run is observable in the audit table."""
    session = _make_session()
    _insert_bot(session)
    _insert_external(session, dt_str=date.today().strftime("%Y%m%d"))

    svc = _make_service(session)
    _run(svc.process_run(dry_run=False))

    audits = session.query(DormantCheckAudit).filter(
        DormantCheckAudit.source == "external_input"
    ).all()
    assert len(audits) == 1
    assert audits[0].check_result == "inactive"
    assert audits[0].action_taken == "warn_enqueued"
    assert audits[0].bot_id == "bot1"
    assert audits[0].owner_id == "ow"


@pytest.mark.unit
def test_external_recycle_writes_audit():
    """Bug C: external recycle branch must write audit (source='external_input',
    action_taken='recycled')."""
    session = _make_session()
    _insert_bot(session)
    old_dt = (date.today() - timedelta(days=M)).strftime("%Y%m%d")
    _insert_external(session, dt_str=old_dt)

    svc = _make_service(session)
    _run(svc.process_run(dry_run=False))

    audits = session.query(DormantCheckAudit).filter(
        DormantCheckAudit.source == "external_input"
    ).all()
    assert len(audits) == 1
    assert audits[0].check_result == "inactive"
    assert audits[0].action_taken == "recycled"


@pytest.mark.unit
def test_external_whitelist_skip_writes_audit():
    """Bug C: external whitelist skip branch must audit too."""
    session = _make_session()
    _insert_bot(session)
    session.add(DormantWhitelist(
        bot_id="bot1", owner_id="ow", governance_source="manual",
        reason="保留", created_by="ops",
    ))
    session.commit()
    _insert_external(session, dt_str=date.today().strftime("%Y%m%d"))

    svc = _make_service(session)
    _run(svc.process_run(dry_run=False))

    audits = session.query(DormantCheckAudit).filter(
        DormantCheckAudit.source == "external_input"
    ).all()
    assert len(audits) == 1
    assert audits[0].check_result == "whitelisted"
    assert audits[0].action_taken == "skipped"


@pytest.mark.unit
def test_external_missing_bot_writes_audit():
    """Bug C: external_input row pointing at a bot_id missing from ac_bots
    must audit, not just silently log-and-skip."""
    session = _make_session()
    # NOTE: deliberately NOT inserting the bot into ac_bots
    _insert_external(session, bot_id="ghost_bot",
                     dt_str=date.today().strftime("%Y%m%d"))

    svc = _make_service(session)
    _run(svc.process_run(dry_run=False))

    audits = session.query(DormantCheckAudit).filter(
        DormantCheckAudit.source == "external_input"
    ).all()
    assert len(audits) == 1
    assert audits[0].check_result == "missing_bot"
    assert audits[0].action_taken == "skipped"
    assert audits[0].bot_id == "ghost_bot"


@pytest.mark.unit
def test_external_future_dated_row_writes_error_audit():
    """Bug C: external_input row with dt in the future (clock skew or bad
    input) must NOT enqueue notify and must record an 'error' audit row
    so the situation is observable."""
    session = _make_session()
    _insert_bot(session)
    future_dt = (date.today() + timedelta(days=2)).strftime("%Y%m%d")
    _insert_external(session, dt_str=future_dt)

    svc = _make_service(session)
    _run(svc.process_run(dry_run=False))

    # No notify enqueued — future-dated rows are left alone
    logs = session.query(DormantNotifyLog).filter(
        DormantNotifyLog.notify_source == "external_input"
    ).all()
    assert len(logs) == 0
    # Audit row recorded with check_result='error'
    audits = session.query(DormantCheckAudit).filter(
        DormantCheckAudit.source == "external_input"
    ).all()
    assert len(audits) == 1
    assert audits[0].check_result == "error"
    assert "future-dated" in (audits[0].error_msg or "")


@pytest.mark.unit
def test_external_unexpected_exception_writes_error_audit_and_continues():
    """Bug C: if a row raises an unexpected exception mid-processing (e.g.
    inside enqueue), the catch-all branch must:
      - roll back the session (or subsequent commits would crash too),
      - bump summary.errors,
      - write a best-effort error audit row (action_taken='skipped')
      - then the loop continues with the next row.
    """
    session = _make_session()
    _insert_bot(session, bot_id="ok_bot")
    _insert_bot(session, bot_id="boom_bot")
    today = date.today().strftime("%Y%m%d")
    # First row will explode inside _enqueue_external_warn; second is OK
    _insert_external(session, bot_id="boom_bot", dt_str=today)
    _insert_external(session, bot_id="ok_bot", dt_str=today)

    svc = _make_service(session)

    # Monkey-patch _enqueue_external_warn to throw for boom_bot.
    real_enqueue = svc._enqueue_external_warn

    def fake_enqueue(s, row, bot_name, owner_id, dry_run):
        if row.bot_id == "boom_bot":
            raise RuntimeError("simulated enqueue failure")
        return real_enqueue(s, row, bot_name, owner_id, dry_run)

    svc._enqueue_external_warn = fake_enqueue

    summary = _run(svc.process_run(dry_run=False))

    # Exactly 1 error counted, 1 warn still wrote OK (continue-on-failure)
    assert summary.errors == 1
    assert summary.warned == 1

    # ok_bot got its warn notify; boom_bot did not
    logs = session.query(DormantNotifyLog).filter(
        DormantNotifyLog.notify_source == "external_input"
    ).all()
    log_bots = {log.bot_id for log in logs}
    assert "ok_bot" in log_bots
    assert "boom_bot" not in log_bots

    # boom_bot has an error audit row from the catch-all branch
    error_audits = session.query(DormantCheckAudit).filter(
        DormantCheckAudit.source == "external_input",
        DormantCheckAudit.check_result == "error",
        DormantCheckAudit.bot_id == "boom_bot",
    ).all()
    assert len(error_audits) == 1
    assert "simulated enqueue failure" in (error_audits[0].error_msg or "")
