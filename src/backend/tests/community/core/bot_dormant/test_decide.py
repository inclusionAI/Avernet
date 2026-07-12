"""Unit tests for DormantBotService.process_run (single-signal /alive).

Decision branches tested:
  B1 - check_alive returns unknown → candidate skipped
  B3 - alive.result='true' → active, no action
  B4 - days_inactive in [N, N+M) → warn enqueued, DormantNotifyLog inserted
  B5 - days_inactive >= N+M → recycled, stop_bot + update_status called
  B6 - dry_run=True and days_inactive >= N+M → recycled action skipped, audit still written
  B5b - days_inactive >= N+M, dry_run=False → DormantNotifyLog with notify_type='recycle' inserted
  B6b - dry_run=True and days_inactive >= N+M → recycle NotifyLog NOT inserted

Dual-signal (openapi-invocation) has been removed per limo decision 2026-06-22:
  - OpenAPI invocations land in session records, queryable via /alive last_session_time.
  - /alive is the sole activity signal; query_invocations is no longer called.

Constants used: N=7, M=3 (so warn window [7,10), recycle >=10 days inactive)
"""
from __future__ import annotations

import asyncio
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from agentclaw.community.core.bot_dormant.baas_client import (
    AliveResult,
    BaasDormantClient,
)
from agentclaw.community.core.bot_dormant.ops_service import DormantOpsService
from agentclaw.community.core.bot_dormant.service import (
    Candidate,
    DormantBotService,
    RunSummary,
)
from agentclaw.community.core.bot_dormant.sqlite_models import (
    DormantCheckAudit,
    DormantNotifyLog,
)
from agentclaw.community.plugin_api.models import Base, BotModel


# ---------------------------------------------------------------------------
# Frozen DateTime Helper (for cooldown tests)
# ---------------------------------------------------------------------------

class _FrozenDateTime:
    """Freeze datetime.now() / datetime.utcnow() for cooldown tests."""
    def __init__(self, fixed):
        self._fixed = fixed
    def now(self, tz=None):
        return self._fixed if tz is None else self._fixed.replace(tzinfo=tz)
    def utcnow(self):
        return self._fixed
    def __getattr__(self, name):
        # delegate everything else (strptime, etc.) to the real module
        import datetime as _dt
        return getattr(_dt.datetime, name)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

N = 7   # dormant threshold days
M = 3   # additional days before recycle (warn window = [N, N+M))


def _make_session() -> Session:
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    return SessionLocal()


class FakeDB:
    """Minimal DatabasePlugin fake whose orm_session() yields the given session."""

    def __init__(self, session: Session) -> None:
        self._session = session

    @contextmanager
    def orm_session(self):
        yield self._session

    def session(self):
        return self.orm_session()


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _make_candidate(
    bot_id: str = "bot1",
    entity_id: str = "123",
    owner_id: str = "owner1",
    days_old: int = 60,
) -> Candidate:
    gmt_create = _now() - timedelta(days=days_old)
    return Candidate(
        bot_id=bot_id,
        entity_id=entity_id,
        owner_id=owner_id,
        bot_name="Test Bot",
        gmt_create=gmt_create,
    )


def _make_service(
    session: Session,
    baas_client: BaasDormantClient | None = None,
    bot_service=None,
    passport_plugin=None,
) -> DormantBotService:
    """Build a DormantBotService with all dependencies injected as mocks."""
    if baas_client is None:
        baas_client = AsyncMock(spec=BaasDormantClient)
    if bot_service is None:
        bot_service = MagicMock()
        bot_service.stop_bot = MagicMock(return_value=True)
        bot_service.update_status = MagicMock()
    if passport_plugin is None:
        passport_plugin = MagicMock()
    scan_policy = MagicMock()
    scan_policy.dry_run.return_value = False
    return DormantBotService(
        db=FakeDB(session),
        baas_client=baas_client,
        bot_service=bot_service,
        passport_plugin=passport_plugin,
        scan_policy=scan_policy,
        N=N,
        M=M,
    )


def _insert_audit(
    session,
    *,
    bot_id: str,
    owner_id: str,
    action_taken: str,
    gmt_create,
    dry_run: int = 0,
    source: str = "dormant_bot_service",
    check_result: str = "inactive",
) -> DormantCheckAudit:
    """Insert a fake DormantCheckAudit row for first_warn_dt history tests.

    The new N/M semantics looks up MIN(gmt_create) where
    action_taken='warn_enqueued' AND dry_run=0 AND gmt_create > last_active
    — tests need to plant exact historical rows to exercise cooldown branches.
    """
    row = DormantCheckAudit(
        run_id="hist",
        bot_id=bot_id,
        owner_id=owner_id,
        check_result=check_result,
        action_taken=action_taken,
        source=source,
        dry_run=dry_run,
        gmt_create=gmt_create,
    )
    session.add(row)
    session.commit()
    return row


def _run(coro):
    """Execute a coroutine synchronously."""
    return asyncio.run(coro)


def _insert_bot_record(
    session: Session,
    *,
    bot_id: str,
    owner_id: str,
    entity_id: str = "123",
    status: str = "ACTIVE",
    bot_type: str = "personal",
    bot_name: str = "Test Bot",
    gmt_create: datetime | None = None,
) -> BotModel:
    bot = BotModel(
        bot_id=bot_id,
        entity_id=entity_id,
        entity_type="user",
        creator_id=owner_id,
        owner_id=owner_id,
        bot_name=bot_name,
        status=status,
        is_delete=0,
        bot_type=bot_type,
        gmt_create=gmt_create or (_now() - timedelta(days=30)),
        gmt_modified=_now(),
    )
    session.add(bot)
    session.commit()
    return bot


@pytest.mark.unit
def test_manual_recycle_one_reuses_recycle_side_effects_and_writes_audit():
    """Manual ops recycle should execute the same stop/update/freeze chain."""
    session = _make_session()
    _insert_bot_record(session, bot_id="ops_bot", owner_id="owner1")
    bot_service = MagicMock()
    bot_service.stop_bot.return_value = True
    bot_service.update_status = MagicMock()
    passport = MagicMock()
    service = _make_service(
        session,
        bot_service=bot_service,
        passport_plugin=passport,
    )
    ops_service = DormantOpsService(service, passport)

    result = ops_service.recycle_one(
        bot_id="ops_bot",
        owner_id="owner1",
        dry_run=False,
        reason="prepub regression",
    )

    assert result["status"] == "recycled"
    assert result["dry_run"] is False
    bot_service.stop_bot.assert_called_once_with(
        bot_id="ops_bot",
        user_id="owner1",
        release_reason="dormant_recycle",
    )
    bot_service.update_status.assert_called_once_with(
        bot_id="ops_bot",
        user_id="owner1",
        status="RECYCLED",
    )
    passport.freeze_agent_passport.assert_called_once_with(
        bot_id="ops_bot",
        owner_workno="owner1",
        reason="dormant recycle",
    )
    audits = session.query(DormantCheckAudit).all()
    assert len(audits) == 1
    assert audits[0].action_taken == "recycled"
    assert audits[0].source == "manual_ops"
    logs = session.query(DormantNotifyLog).all()
    assert len(logs) == 1
    assert logs[0].notify_type == "recycle"
    assert logs[0].dry_run == 0


@pytest.mark.unit
def test_manual_recycle_one_dry_run_skips_side_effects_but_records_intent():
    """dry_run keeps the same observable audit/notify trail without stopping."""
    session = _make_session()
    _insert_bot_record(session, bot_id="ops_bot", owner_id="owner1")
    bot_service = MagicMock()
    passport = MagicMock()
    service = _make_service(
        session,
        bot_service=bot_service,
        passport_plugin=passport,
    )
    ops_service = DormantOpsService(service, passport)

    result = ops_service.recycle_one(
        bot_id="ops_bot",
        owner_id="owner1",
        dry_run=True,
        reason="prepub dry run",
    )

    assert result["status"] == "dry_run_recycled"
    bot_service.stop_bot.assert_not_called()
    bot_service.update_status.assert_not_called()
    passport.freeze_agent_passport.assert_not_called()
    audit = session.query(DormantCheckAudit).one()
    assert audit.action_taken == "recycled"
    assert audit.dry_run == 1


@pytest.mark.unit
def test_manual_recycle_one_rejects_missing_bot():
    """Manual ops recycle should fail clearly when bot_id + owner_id misses."""
    session = _make_session()
    ops_service = DormantOpsService(_make_service(session), MagicMock())

    with pytest.raises(ValueError, match="bot not found"):
        ops_service.recycle_one(
            bot_id="missing_bot",
            owner_id="owner1",
            dry_run=False,
        )


@pytest.mark.unit
def test_manual_recycle_one_rejects_non_active_bot():
    """Manual ops recycle should only accept ACTIVE bots."""
    session = _make_session()
    _insert_bot_record(
        session,
        bot_id="ops_bot",
        owner_id="owner1",
        status="RECYCLED",
    )
    ops_service = DormantOpsService(_make_service(session), MagicMock())

    with pytest.raises(ValueError, match="only ACTIVE bot"):
        ops_service.recycle_one(
            bot_id="ops_bot",
            owner_id="owner1",
            dry_run=False,
        )


@pytest.mark.unit
def test_manual_recycle_one_rejects_non_personal_bot():
    """Manual ops recycle should keep the same personal-bot scope as the scan."""
    session = _make_session()
    _insert_bot_record(
        session,
        bot_id="ops_bot",
        owner_id="owner1",
        bot_type="team",
    )
    ops_service = DormantOpsService(_make_service(session), MagicMock())

    with pytest.raises(ValueError, match="only personal bot"):
        ops_service.recycle_one(
            bot_id="ops_bot",
            owner_id="owner1",
            dry_run=False,
        )


# ---------------------------------------------------------------------------
# B1: unknown alive → skip
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_unknown_alive_skips_candidate():
    """B1: check_alive='unknown' → candidate skipped, audit written with action='skipped'."""
    session = _make_session()
    candidate = _make_candidate(bot_id="bot_unknown", days_old=60)

    baas = AsyncMock(spec=BaasDormantClient)
    baas.check_alive = AsyncMock(
        return_value=AliveResult(result="unknown", last_session_time=None)
    )

    svc = _make_service(session, baas_client=baas)

    # Seed a BotModel so filter_candidates finds it
    bot = BotModel(
        bot_id=candidate.bot_id,
        entity_id=candidate.entity_id,
        entity_type="user",
        creator_id="u1",
        owner_id=candidate.owner_id,
        bot_name=candidate.bot_name,
        status="ACTIVE",
        is_delete=0,
        bot_type="personal",
        gmt_create=candidate.gmt_create,
        gmt_modified=_now(),
    )
    session.add(bot)
    session.commit()

    summary: RunSummary = _run(svc.process_run(dry_run=True))

    assert summary.skipped == 1
    assert summary.warned == 0
    assert summary.recycled == 0
    assert summary.errors == 0

    audits = session.query(DormantCheckAudit).all()
    assert len(audits) == 1
    assert audits[0].action_taken == "skipped"
    assert audits[0].check_result == "unknown"

    # /alive must have been called exactly once (and nothing else on baas)
    baas.check_alive.assert_called_once()


@pytest.mark.unit
def test_alive_true_active_no_action():
    """B3: alive.result='true' → active, no warn/recycle, audit action='none'."""
    session = _make_session()
    candidate = _make_candidate(bot_id="bot_alive", days_old=60)

    baas = AsyncMock(spec=BaasDormantClient)
    baas.check_alive = AsyncMock(
        return_value=AliveResult(result="true", last_session_time=None)
    )

    bot = BotModel(
        bot_id=candidate.bot_id,
        entity_id=candidate.entity_id,
        entity_type="user",
        creator_id="u1",
        owner_id=candidate.owner_id,
        bot_name=candidate.bot_name,
        status="ACTIVE",
        is_delete=0,
        bot_type="personal",
        gmt_create=candidate.gmt_create,
        gmt_modified=_now(),
    )
    session.add(bot)
    session.commit()

    svc = _make_service(session, baas_client=baas)
    summary: RunSummary = _run(svc.process_run(dry_run=False))

    assert summary.warned == 0
    assert summary.recycled == 0
    audits = session.query(DormantCheckAudit).all()
    assert audits[0].action_taken == "none"
    assert audits[0].check_result == "active"


# ---------------------------------------------------------------------------
# B4: warn window
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_warn_window_enqueues_notify_log():
    """B4: days_inactive in [N, N+M) → warn enqueued, DormantNotifyLog inserted."""
    session = _make_session()
    days_inactive = N + 1
    candidate = Candidate(
        bot_id="bot_warn",
        entity_id="456",
        owner_id="owner_warn",
        bot_name="Warn Bot",
        gmt_create=_now() - timedelta(days=days_inactive),
    )

    baas = AsyncMock(spec=BaasDormantClient)
    baas.check_alive = AsyncMock(
        return_value=AliveResult(result="false", last_session_time=None)
    )

    bot = BotModel(
        bot_id=candidate.bot_id,
        entity_id=candidate.entity_id,
        entity_type="user",
        creator_id="u1",
        owner_id=candidate.owner_id,
        bot_name=candidate.bot_name,
        status="ACTIVE",
        is_delete=0,
        bot_type="personal",
        gmt_create=candidate.gmt_create,
        gmt_modified=_now(),
    )
    session.add(bot)
    session.commit()

    svc = _make_service(session, baas_client=baas)
    summary: RunSummary = _run(svc.process_run(dry_run=False))

    assert summary.warned == 1
    assert summary.recycled == 0

    logs = session.query(DormantNotifyLog).all()
    assert len(logs) == 1
    assert logs[0].bot_id == "bot_warn"
    assert logs[0].notify_type == "warn"
    assert logs[0].send_status == "pending"
    assert logs[0].notify_source == "internal_scan"
    # Verify template-rendered content contains key phrases
    assert "沉寂预警" in logs[0].content
    assert "天后被自动回收" in logs[0].content

    audits = session.query(DormantCheckAudit).all()
    assert audits[0].action_taken == "warn_enqueued"
    assert audits[0].check_result == "inactive"
    assert audits[0].days_inactive == days_inactive


@pytest.mark.unit
def test_warn_window_treats_existing_notify_log_as_idempotent():
    """Existing same-day warn notify_log should not fail the candidate."""
    session = _make_session()
    days_inactive = N + 1
    candidate = Candidate(
        bot_id="bot_warn",
        entity_id="456",
        owner_id="owner_warn",
        bot_name="Warn Bot",
        gmt_create=_now() - timedelta(days=days_inactive),
    )
    today = _now().strftime("%Y%m%d")
    session.add(DormantNotifyLog(
        bot_id=candidate.bot_id,
        owner_id=candidate.owner_id,
        entity_id=candidate.entity_id,
        notify_type="warn",
        notify_target=candidate.owner_id,
        notify_source="internal_scan",
        content="already queued",
        dt=today,
        send_status="pending",
        dry_run=1,
    ))
    session.add(BotModel(
        bot_id=candidate.bot_id,
        entity_id=candidate.entity_id,
        entity_type="user",
        creator_id="u1",
        owner_id=candidate.owner_id,
        bot_name=candidate.bot_name,
        status="ACTIVE",
        is_delete=0,
        bot_type="personal",
        gmt_create=candidate.gmt_create,
        gmt_modified=_now(),
    ))
    session.commit()

    baas = AsyncMock(spec=BaasDormantClient)
    baas.check_alive = AsyncMock(
        return_value=AliveResult(result="false", last_session_time=None)
    )

    svc = _make_service(session, baas_client=baas)
    summary: RunSummary = _run(svc.process_run(dry_run=True))

    assert summary.errors == 0
    assert summary.warned == 1
    logs = session.query(DormantNotifyLog).filter(
        DormantNotifyLog.bot_id == candidate.bot_id,
        DormantNotifyLog.owner_id == candidate.owner_id,
        DormantNotifyLog.dt == today,
        DormantNotifyLog.notify_type == "warn",
    ).all()
    assert len(logs) == 1
    audits = session.query(DormantCheckAudit).all()
    assert audits[0].check_result == "inactive"
    assert audits[0].action_taken == "warn_enqueued"


# ---------------------------------------------------------------------------
# B5: recycle threshold
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_recycle_threshold_calls_stop_and_update_status():
    """B5: days_inactive >= N+M, dry_run=False → stop_bot + update_status called."""
    session = _make_session()
    days_inactive = N + M  # exactly at recycle threshold
    candidate = Candidate(
        bot_id="bot_recycle",
        entity_id="789",
        owner_id="owner_recycle",
        bot_name="Recycle Bot",
        gmt_create=_now() - timedelta(days=days_inactive),
    )

    baas = AsyncMock(spec=BaasDormantClient)
    baas.check_alive = AsyncMock(
        return_value=AliveResult(result="false", last_session_time=None)
    )

    bot_svc = MagicMock()
    bot_svc.stop_bot = MagicMock(return_value=True)
    bot_svc.update_status = MagicMock()

    bot = BotModel(
        bot_id=candidate.bot_id,
        entity_id=candidate.entity_id,
        entity_type="user",
        creator_id="u1",
        owner_id=candidate.owner_id,
        bot_name=candidate.bot_name,
        status="ACTIVE",
        is_delete=0,
        bot_type="personal",
        gmt_create=candidate.gmt_create,
        gmt_modified=_now(),
    )
    session.add(bot)
    session.commit()

    svc = _make_service(session, baas_client=baas, bot_service=bot_svc)

    # New N/M semantics: recycle requires a prior warn with cooldown >= M.
    # Plant a warn_enqueued audit row that is M+1 days old (> last_active which
    # equals gmt_create = now - N+M days, so audit.gmt_create > last_active is
    # satisfied since M+1 < N+M).
    _insert_audit(
        session,
        bot_id=candidate.bot_id,
        owner_id=candidate.owner_id,
        action_taken="warn_enqueued",
        gmt_create=_now() - timedelta(days=svc._M + 1),
        dry_run=0,
    )

    summary: RunSummary = _run(svc.process_run(dry_run=False))

    assert summary.recycled == 1
    assert summary.warned == 0

    bot_svc.stop_bot.assert_called_once_with(
        bot_id=candidate.bot_id,
        user_id=candidate.owner_id,
        release_reason="dormant_recycle",
    )
    bot_svc.update_status.assert_called_once_with(
        bot_id=candidate.bot_id, user_id=candidate.owner_id, status="RECYCLED"
    )

    audits = session.query(DormantCheckAudit).filter(
        DormantCheckAudit.action_taken == "recycled"
    ).all()
    assert len(audits) == 1
    assert audits[0].action_taken == "recycled"
    assert audits[0].check_result == "inactive"
    assert audits[0].days_inactive == days_inactive


# ---------------------------------------------------------------------------
# B6: dry_run
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_dry_run_skips_stop_bot_but_writes_audit():
    """B6: dry_run=True and days_inactive >= N+M → stop_bot NOT called, audit written."""
    session = _make_session()
    days_inactive = N + M + 5
    candidate = Candidate(
        bot_id="bot_dry",
        entity_id="321",
        owner_id="owner_dry",
        bot_name="Dry Bot",
        gmt_create=_now() - timedelta(days=days_inactive),
    )

    baas = AsyncMock(spec=BaasDormantClient)
    baas.check_alive = AsyncMock(
        return_value=AliveResult(result="false", last_session_time=None)
    )

    bot_svc = MagicMock()
    bot_svc.stop_bot = MagicMock(return_value=True)
    bot_svc.update_status = MagicMock()

    bot = BotModel(
        bot_id=candidate.bot_id,
        entity_id=candidate.entity_id,
        entity_type="user",
        creator_id="u1",
        owner_id=candidate.owner_id,
        bot_name=candidate.bot_name,
        status="ACTIVE",
        is_delete=0,
        bot_type="personal",
        gmt_create=candidate.gmt_create,
        gmt_modified=_now(),
    )
    session.add(bot)
    session.commit()

    svc = _make_service(session, baas_client=baas, bot_service=bot_svc)

    # New N/M semantics: recycle requires a prior warn with cooldown >= M.
    # Plant a warn_enqueued audit row that is M+1 days old (satisfies
    # gmt_create > last_active and cooldown_days >= M).
    _insert_audit(
        session,
        bot_id=candidate.bot_id,
        owner_id=candidate.owner_id,
        action_taken="warn_enqueued",
        gmt_create=_now() - timedelta(days=svc._M + 1),
        dry_run=0,
    )

    summary: RunSummary = _run(svc.process_run(dry_run=True))

    # dry_run: stop_bot must NOT be called
    bot_svc.stop_bot.assert_not_called()
    bot_svc.update_status.assert_not_called()

    # audit is still written with action='recycled'
    audits = session.query(DormantCheckAudit).filter(
        DormantCheckAudit.action_taken == "recycled"
    ).all()
    assert len(audits) == 1
    assert audits[0].action_taken == "recycled"
    assert audits[0].dry_run == 1

    assert summary.recycled == 1


# ---------------------------------------------------------------------------
# B5b — recycle path enqueues recycle NotifyLog
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_recycle_enqueues_notify_log():
    """B5b: days_inactive >= N+M, dry_run=False → DormantNotifyLog with notify_type='recycle' inserted."""
    session = _make_session()
    days_inactive = N + M  # exactly at recycle threshold
    candidate = Candidate(
        bot_id="bot_recycle_notify",
        entity_id="999",
        owner_id="owner_recycle_notify",
        bot_name="Recycle Notify Bot",
        gmt_create=_now() - timedelta(days=days_inactive),
    )

    baas = AsyncMock(spec=BaasDormantClient)
    baas.check_alive = AsyncMock(
        return_value=AliveResult(result="false", last_session_time=None)
    )

    bot_svc = MagicMock()
    bot_svc.stop_bot = MagicMock(return_value=True)
    bot_svc.update_status = MagicMock()

    bot = BotModel(
        bot_id=candidate.bot_id,
        entity_id=candidate.entity_id,
        entity_type="user",
        creator_id="u1",
        owner_id=candidate.owner_id,
        bot_name=candidate.bot_name,
        status="ACTIVE",
        is_delete=0,
        bot_type="personal",
        gmt_create=candidate.gmt_create,
        gmt_modified=_now(),
    )
    session.add(bot)
    session.commit()

    svc = _make_service(session, baas_client=baas, bot_service=bot_svc)

    # New N/M semantics: recycle requires a prior warn with cooldown >= M.
    # Plant a warn_enqueued audit row that is M+1 days old (satisfies
    # gmt_create > last_active and cooldown_days >= M).
    _insert_audit(
        session,
        bot_id=candidate.bot_id,
        owner_id=candidate.owner_id,
        action_taken="warn_enqueued",
        gmt_create=_now() - timedelta(days=svc._M + 1),
        dry_run=0,
    )

    summary: RunSummary = _run(svc.process_run(dry_run=False))

    assert summary.recycled == 1

    # A DormantNotifyLog with notify_type='recycle' must be enqueued
    logs = session.query(DormantNotifyLog).all()
    assert len(logs) == 1
    log = logs[0]
    assert log.bot_id == candidate.bot_id
    assert log.notify_type == "recycle"
    assert log.send_status == "pending"
    assert log.notify_target == candidate.owner_id
    # entity_id must be owner_id so NotifySender.get_bot can resolve the bot
    assert log.entity_id == candidate.owner_id
    assert log.dry_run == 0
    assert log.notify_source == "internal_scan"
    # Verify template-rendered content contains key phrases
    assert "已回收" in log.content
    assert "激活" in log.content
    assert "次日" in log.content

    # stop_bot must also be called with release_reason='dormant_recycle'
    bot_svc.stop_bot.assert_called_once_with(
        bot_id=candidate.bot_id,
        user_id=candidate.owner_id,
        release_reason="dormant_recycle",
    )


# ---------------------------------------------------------------------------
# B6b — dry_run=True → recycle notify NOT enqueued
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_dry_run_still_enqueues_recycle_notify_with_dry_run_flag():
    """B6b (post-fix): dry_run=True still enqueues recycle NotifyLog so the
    predict-only run can surface the would-be message; the row carries
    dry_run=1 so the in-container sender's default query (which filters out
    dry_run=1) won't pick it up and send a real ding-talk."""
    session = _make_session()
    days_inactive = N + M + 2
    candidate = Candidate(
        bot_id="bot_dry_notify",
        entity_id="111",
        owner_id="owner_dry_notify",
        bot_name="Dry Notify Bot",
        gmt_create=_now() - timedelta(days=days_inactive),
    )

    baas = AsyncMock(spec=BaasDormantClient)
    baas.check_alive = AsyncMock(
        return_value=AliveResult(result="false", last_session_time=None)
    )

    bot_svc = MagicMock()
    bot_svc.stop_bot = MagicMock(return_value=True)
    bot_svc.update_status = MagicMock()

    bot = BotModel(
        bot_id=candidate.bot_id,
        entity_id=candidate.entity_id,
        entity_type="user",
        creator_id="u1",
        owner_id=candidate.owner_id,
        bot_name=candidate.bot_name,
        status="ACTIVE",
        is_delete=0,
        bot_type="personal",
        gmt_create=candidate.gmt_create,
        gmt_modified=_now(),
    )
    session.add(bot)
    session.commit()

    svc = _make_service(session, baas_client=baas, bot_service=bot_svc)

    # New N/M semantics: recycle requires a prior warn with cooldown >= M.
    # Plant a warn_enqueued audit row that is M+1 days old (satisfies
    # gmt_create > last_active and cooldown_days >= M).
    _insert_audit(
        session,
        bot_id=candidate.bot_id,
        owner_id=candidate.owner_id,
        action_taken="warn_enqueued",
        gmt_create=_now() - timedelta(days=svc._M + 1),
        dry_run=0,
    )

    summary: RunSummary = _run(svc.process_run(dry_run=True))

    assert summary.recycled == 1
    # dry_run=True → recycle notify log IS inserted, but flagged dry_run=1
    logs = session.query(DormantNotifyLog).all()
    assert len(logs) == 1
    assert logs[0].notify_type == "recycle"
    assert logs[0].dry_run == 1
    # stop_bot must NOT have been called in dry_run mode
    bot_svc.stop_bot.assert_not_called()


# ---------------------------------------------------------------------------
# I-2: unexpected alive.result values → treated as unknown (skipped)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_unexpected_alive_result_skips_candidate():
    """I-2: alive.result='error' (or any non-true/false value) → skipped, NOT recycled."""
    session = _make_session()
    # Bot is old enough that it would be recycled if alive.result were 'false'
    days_old = N + M + 10
    candidate = _make_candidate(bot_id="bot_error", days_old=days_old)

    baas = AsyncMock(spec=BaasDormantClient)
    baas.check_alive = AsyncMock(
        return_value=AliveResult(result="error", last_session_time=None)
    )

    bot = BotModel(
        bot_id=candidate.bot_id,
        entity_id=candidate.entity_id,
        entity_type="user",
        creator_id="u1",
        owner_id=candidate.owner_id,
        bot_name=candidate.bot_name,
        status="ACTIVE",
        is_delete=0,
        bot_type="personal",
        gmt_create=candidate.gmt_create,
        gmt_modified=_now(),
    )
    session.add(bot)
    session.commit()

    svc = _make_service(session, baas_client=baas)
    summary: RunSummary = _run(svc.process_run(dry_run=False))

    # Must be skipped — NOT recycled, NOT warned
    assert summary.skipped == 1
    assert summary.recycled == 0
    assert summary.warned == 0

    # Audit row must record unknown/skipped
    audits = session.query(DormantCheckAudit).all()
    assert len(audits) == 1
    assert audits[0].check_result == "unknown"
    assert audits[0].action_taken == "skipped"

    # No recycle or warn notification enqueued
    logs = session.query(DormantNotifyLog).all()
    assert len(logs) == 0

    # check_alive was called exactly once
    baas.check_alive.assert_called_once()


# ---------------------------------------------------------------------------
# Resilience: a single candidate's failure must NOT abort the whole run
# ---------------------------------------------------------------------------


def _insert_bot(session, candidate: Candidate, bot_type: str = "personal") -> None:
    bot = BotModel(
        bot_id=candidate.bot_id,
        entity_id=candidate.entity_id,
        entity_type="staff",
        creator_id=candidate.owner_id,
        owner_id=candidate.owner_id,
        bot_name=candidate.bot_name,
        bot_type=bot_type,
        status="ACTIVE",
        is_delete=0,
        gmt_create=candidate.gmt_create,
        gmt_modified=_now(),
    )
    session.add(bot)
    session.commit()


@pytest.mark.unit
def test_process_run_continues_after_single_candidate_exception():
    """One candidate raising must not abort the run; the next one must still be processed,
    summary.errors must reflect the failure, and an 'error' audit row must be written."""
    session = _make_session()

    # Three candidates (all old enough to survive filter_candidates):
    # bot1 — check_alive raises
    # bot2 — alive=true (active, no action)
    # bot3 — alive=false + days_inactive >= N+M → recycle path
    c1 = _make_candidate(bot_id="bot1", days_old=60)
    c2 = _make_candidate(bot_id="bot2", days_old=60)
    c3 = _make_candidate(bot_id="bot3", days_old=60)
    for c in (c1, c2, c3):
        _insert_bot(session, c)

    # check_alive: c1 raises; c2 returns true; c3 returns false (recycle path).
    async def side_effect(*, bot_id, entity_id, minutes):
        if bot_id == "bot1":
            raise RuntimeError("transient BaaS hiccup")
        if bot_id == "bot2":
            return AliveResult(result="true", last_session_time=None)
        return AliveResult(
            result="false",
            last_session_time=(_now() - timedelta(days=60)).strftime("%Y-%m-%d %H:%M:%S"),
        )
    baas = AsyncMock(spec=BaasDormantClient)
    baas.check_alive = AsyncMock(side_effect=side_effect)

    bot_service = MagicMock()
    bot_service.stop_bot = MagicMock(return_value=True)
    bot_service.update_status = MagicMock()

    svc = _make_service(session, baas_client=baas, bot_service=bot_service)

    # New N/M semantics: recycle requires a prior warn with cooldown >= M.
    # bot3 has last_active = now - 60 days; plant a warn_enqueued row that is
    # M+1 days old (gmt_create > last_active satisfied; cooldown_days >= M).
    _insert_audit(
        session,
        bot_id="bot3",
        owner_id="owner1",
        action_taken="warn_enqueued",
        gmt_create=_now() - timedelta(days=svc._M + 1),
        dry_run=0,
    )

    summary: RunSummary = _run(svc.process_run(dry_run=False))

    # All three candidates were scanned, no early abort.
    assert summary.scanned == 3
    assert summary.errors == 1            # bot1
    assert summary.recycled == 1          # bot3 still processed
    assert baas.check_alive.call_count == 3

    # bot3 went through the recycle flow (stop_bot + update_status) — proves loop
    # didn't break at bot1.
    bot_service.stop_bot.assert_called_once_with(
        bot_id="bot3", user_id="owner1", release_reason="dormant_recycle"
    )

    # An 'error' audit row was written for bot1 with error_msg populated.
    err_rows = (
        session.query(DormantCheckAudit)
        .filter(DormantCheckAudit.bot_id == "bot1")
        .all()
    )
    assert len(err_rows) == 1
    assert err_rows[0].check_result == "error"
    assert err_rows[0].action_taken == "skipped"
    assert err_rows[0].error_msg and "transient BaaS hiccup" in err_rows[0].error_msg


@pytest.mark.unit
def test_recycle_release_failure_does_not_advance_to_recycled():
    """stop_bot returning False (device release failed) must NOT result in
    status=RECYCLED; the candidate is logged as 'error' so the next cron
    run can retry, instead of pretending the bot is recycled while resources
    keep accruing charges."""
    session = _make_session()
    c = _make_candidate(bot_id="bot_release_fail", days_old=60)
    _insert_bot(session, c)

    baas = AsyncMock(spec=BaasDormantClient)
    baas.check_alive = AsyncMock(
        return_value=AliveResult(
            result="false",
            last_session_time=(_now() - timedelta(days=60)).strftime("%Y-%m-%d %H:%M:%S"),
        )
    )

    bot_service = MagicMock()
    bot_service.stop_bot = MagicMock(return_value=False)   # device release failed
    bot_service.update_status = MagicMock()

    svc = _make_service(session, baas_client=baas, bot_service=bot_service)

    # New N/M semantics: recycle requires a prior warn with cooldown >= M.
    # bot_release_fail has last_active = now - 60 days; plant a warn row M+1 days old.
    _insert_audit(
        session,
        bot_id=c.bot_id,
        owner_id=c.owner_id,
        action_taken="warn_enqueued",
        gmt_create=_now() - timedelta(days=svc._M + 1),
        dry_run=0,
    )

    summary: RunSummary = _run(svc.process_run(dry_run=False))

    # bot was scanned and marked errors=1, NOT recycled.
    assert summary.scanned == 1
    assert summary.errors == 1
    assert summary.recycled == 0

    # Critically: update_status MUST NOT have been called with RECYCLED.
    bot_service.update_status.assert_not_called()

    # An 'error' audit row was written for this bot.
    err_rows = (
        session.query(DormantCheckAudit)
        .filter(
            DormantCheckAudit.bot_id == "bot_release_fail",
            DormantCheckAudit.check_result == "error",
        )
        .all()
    )
    assert len(err_rows) == 1
    assert err_rows[0].check_result == "error"


@pytest.mark.unit
def test_process_run_swallows_secondary_audit_failure():
    """Even if the error-audit write itself fails, the loop must not abort."""
    session = _make_session()
    c1 = _make_candidate(bot_id="bot_a", days_old=60)
    c2 = _make_candidate(bot_id="bot_b", days_old=60)
    _insert_bot(session, c1)
    _insert_bot(session, c2)

    # Both check_alive raise → both go through the error-audit branch.
    baas = AsyncMock(spec=BaasDormantClient)
    baas.check_alive = AsyncMock(side_effect=RuntimeError("boom"))

    svc = _make_service(session, baas_client=baas)
    # Force _write_audit to fail; the catch-all must swallow it.
    svc._write_audit = MagicMock(side_effect=RuntimeError("audit write failed"))

    summary: RunSummary = _run(svc.process_run(dry_run=False))
    assert summary.scanned == 2
    assert summary.errors == 2          # both counted despite audit failure
    assert baas.check_alive.call_count == 2


# ---------------------------------------------------------------------------
# Case 1: Legacy long-inactive bot should warn on first scan, not recycle
# ---------------------------------------------------------------------------

@pytest.mark.unit
@pytest.mark.asyncio
async def test_legacy_long_inactive_bot_warns_on_first_scan(monkeypatch):
    """Case 1（spec): 存量 56 天 bot 第一次扫到, audit 表无 warn 历史
    → 应当 enqueue warn 而非直接 recycle, 因为冷静期还没起算。
    """
    session = _make_session()
    candidate = Candidate(
        bot_id="default",
        entity_id="123",
        owner_id="434422",
        bot_name="Default Bot",
        gmt_create=datetime(2026, 1, 1),
    )

    # Insert bot into database
    bot = BotModel(
        bot_id=candidate.bot_id,
        entity_id=candidate.entity_id,
        entity_type="user",
        creator_id="u1",
        owner_id=candidate.owner_id,
        bot_name=candidate.bot_name,
        status="ACTIVE",
        is_delete=0,
        bot_type="personal",
        gmt_create=candidate.gmt_create,
        gmt_modified=_now(),
    )
    session.add(bot)
    session.commit()

    # alive=false, last_session 是 56 天前
    baas_client = AsyncMock(spec=BaasDormantClient)
    baas_client.check_alive = AsyncMock(return_value=AliveResult(
        result="false",
        last_session_time="2026-05-01 10:29:04",
    ))

    bot_service = MagicMock()
    bot_service.stop_bot = MagicMock(return_value=True)
    bot_service.update_status = MagicMock()

    service = _make_service(session, baas_client=baas_client, bot_service=bot_service)

    # 冻结 today=2026-06-26
    monkeypatch.setattr(
        "agentclaw.community.core.bot_dormant.service.datetime",
        _FrozenDateTime(datetime(2026, 6, 26, 11, 30, 0)),
    )

    summary = await service.process_run(dry_run=True)

    assert summary.warned == 1, "存量老 bot 首次扫到应进 warn, 不应直接 recycle"
    assert summary.recycled == 0
    bot_service.stop_bot.assert_not_called()
    # audit 写一行 warn_enqueued
    audits = session.query(DormantCheckAudit).filter_by(
        bot_id="default", owner_id="434422"
    ).all()
    assert len([
        a
        for a in audits
        if a.action_taken == "warn_enqueued"
    ]) == 1


# ---------------------------------------------------------------------------
# Case 2: 刚跨过 N 阈值的新沉寂 bot
# ---------------------------------------------------------------------------

@pytest.mark.unit
@pytest.mark.asyncio
async def test_just_threshold_bot_first_warn(monkeypatch):
    """Case 2: days_inactive=7 (恰好阈值), 跟存量老 bot 一样走 warn 分支。"""
    session = _make_session()
    candidate = Candidate(
        bot_id="b1",
        entity_id="111",
        owner_id="o1",
        bot_name="Bot1",
        gmt_create=datetime(2026, 1, 1),
    )
    bot = BotModel(
        bot_id=candidate.bot_id,
        entity_id=candidate.entity_id,
        entity_type="user",
        creator_id="u1",
        owner_id=candidate.owner_id,
        bot_name=candidate.bot_name,
        status="ACTIVE",
        is_delete=0,
        bot_type="personal",
        gmt_create=candidate.gmt_create,
        gmt_modified=_now(),
    )
    session.add(bot)
    session.commit()

    baas_client = AsyncMock(spec=BaasDormantClient)
    baas_client.check_alive = AsyncMock(return_value=AliveResult(
        result="false",
        last_session_time="2026-06-19 10:00:00",  # 7 天前
    ))
    bot_service = MagicMock()
    bot_service.stop_bot = MagicMock(return_value=True)
    bot_service.update_status = MagicMock()

    service = _make_service(session, baas_client=baas_client, bot_service=bot_service)

    monkeypatch.setattr(
        "agentclaw.community.core.bot_dormant.service.datetime",
        _FrozenDateTime(datetime(2026, 6, 26, 11, 30, 0)),
    )
    summary = await service.process_run(dry_run=True)
    assert summary.warned == 1 and summary.recycled == 0


# ---------------------------------------------------------------------------
# Case 3: 预警期内回归再沉寂, 重新 3 天预警
# ---------------------------------------------------------------------------

@pytest.mark.unit
@pytest.mark.asyncio
async def test_user_return_resets_cooldown(monkeypatch):
    """Case 3: 旧 warn 在用户回归之前; gmt_create > last_active 过滤掉旧 warn,
    再次扫到时视为 first_warn=NULL → 重新进 warn 分支。"""
    session = _make_session()
    candidate = Candidate(
        bot_id="b3",
        entity_id="333",
        owner_id="o3",
        bot_name="Bot3",
        gmt_create=datetime(2026, 1, 1),
    )
    bot = BotModel(
        bot_id=candidate.bot_id,
        entity_id=candidate.entity_id,
        entity_type="user",
        creator_id="u1",
        owner_id=candidate.owner_id,
        bot_name=candidate.bot_name,
        status="ACTIVE",
        is_delete=0,
        bot_type="personal",
        gmt_create=candidate.gmt_create,
        gmt_modified=_now(),
    )
    session.add(bot)
    session.commit()

    # 旧的 warn 在 6-26 写入, 用户 6-28 回归活跃了, 7-5 再扫
    # _first_warn_dt 过滤: gmt_create(6-26) > last_active(6-28) → False → 旧 warn 被排除
    _insert_audit(session, bot_id="b3", owner_id="o3",
                  action_taken="warn_enqueued",
                  gmt_create=datetime(2026, 6, 26), dry_run=0)

    baas_client = AsyncMock(spec=BaasDormantClient)
    baas_client.check_alive = AsyncMock(return_value=AliveResult(
        result="false",
        last_session_time="2026-06-28 10:00:00",  # 比旧 warn 晚 → 应过滤旧 warn
    ))
    bot_service = MagicMock()
    bot_service.stop_bot = MagicMock(return_value=True)
    bot_service.update_status = MagicMock()

    service = _make_service(session, baas_client=baas_client, bot_service=bot_service)

    monkeypatch.setattr(
        "agentclaw.community.core.bot_dormant.service.datetime",
        _FrozenDateTime(datetime(2026, 7, 5, 11, 30, 0)),
    )
    summary = await service.process_run(dry_run=True)
    assert summary.warned == 1 and summary.recycled == 0, \
        "用户回归后旧 warn 应被过滤, 重新走 first warn"


# ---------------------------------------------------------------------------
# Case 4: cron 漏跑 1-2 天，从首次扫到起算
# ---------------------------------------------------------------------------

@pytest.mark.unit
@pytest.mark.asyncio
async def test_cron_skipped_days_count_from_first_actual_scan(monkeypatch):
    """Case 4: cron 漏跑后第一次扫到也是 first_warn = today, 走 warn 而非 recycle。"""
    session = _make_session()
    candidate = Candidate(
        bot_id="b4",
        entity_id="444",
        owner_id="o4",
        bot_name="Bot4",
        gmt_create=datetime(2026, 1, 1),
    )
    bot = BotModel(
        bot_id=candidate.bot_id,
        entity_id=candidate.entity_id,
        entity_type="user",
        creator_id="u1",
        owner_id=candidate.owner_id,
        bot_name=candidate.bot_name,
        status="ACTIVE",
        is_delete=0,
        bot_type="personal",
        gmt_create=candidate.gmt_create,
        gmt_modified=_now(),
    )
    session.add(bot)
    session.commit()

    # audit 表里没任何记录(cron 没跑过)
    baas_client = AsyncMock(spec=BaasDormantClient)
    baas_client.check_alive = AsyncMock(return_value=AliveResult(
        result="false",
        last_session_time="2026-05-01 10:00:00",  # 56 天前
    ))
    bot_service = MagicMock()
    bot_service.stop_bot = MagicMock(return_value=True)
    bot_service.update_status = MagicMock()

    service = _make_service(session, baas_client=baas_client, bot_service=bot_service)

    monkeypatch.setattr(
        "agentclaw.community.core.bot_dormant.service.datetime",
        _FrozenDateTime(datetime(2026, 6, 26, 11, 30, 0)),
    )
    summary = await service.process_run(dry_run=True)
    assert summary.warned == 1 and summary.recycled == 0


# ---------------------------------------------------------------------------
# Case 5: dry_run 期 warn 不计入真实模式 cooldown
# ---------------------------------------------------------------------------

@pytest.mark.unit
@pytest.mark.asyncio
async def test_dry_run_warns_excluded_from_real_cooldown(monkeypatch):
    """Case 5: 之前 3 天 dry_run=1 写过 warn, 真实模式切换日 dry_run=0
    应当走 first warn(NULL), 而不是直接 recycle。"""
    session = _make_session()
    candidate = Candidate(
        bot_id="b5",
        entity_id="555",
        owner_id="o5",
        bot_name="Bot5",
        gmt_create=datetime(2026, 1, 1),
    )
    bot = BotModel(
        bot_id=candidate.bot_id,
        entity_id=candidate.entity_id,
        entity_type="user",
        creator_id="u1",
        owner_id=candidate.owner_id,
        bot_name=candidate.bot_name,
        status="ACTIVE",
        is_delete=0,
        bot_type="personal",
        gmt_create=candidate.gmt_create,
        gmt_modified=_now(),
    )
    session.add(bot)
    session.commit()

    # 3 天 dry_run warn 历史(dry_run=1, 应被过滤)
    for d in (23, 24, 25):
        _insert_audit(session, bot_id="b5", owner_id="o5",
                      action_taken="warn_enqueued",
                      gmt_create=datetime(2026, 6, d), dry_run=1)

    baas_client = AsyncMock(spec=BaasDormantClient)
    baas_client.check_alive = AsyncMock(return_value=AliveResult(
        result="false",
        last_session_time="2026-05-01 10:00:00",  # 56 天前
    ))
    bot_service = MagicMock()
    bot_service.stop_bot = MagicMock(return_value=True)
    bot_service.update_status = MagicMock()

    service = _make_service(session, baas_client=baas_client, bot_service=bot_service)

    monkeypatch.setattr(
        "agentclaw.community.core.bot_dormant.service.datetime",
        _FrozenDateTime(datetime(2026, 6, 26, 11, 30, 0)),
    )
    summary = await service.process_run(dry_run=False)
    assert summary.warned == 1 and summary.recycled == 0, \
        "dry_run 期 warn 不应计入真实 cooldown"


# ---------------------------------------------------------------------------
# Case 6: cooldown ≥ M 时 recycle
# ---------------------------------------------------------------------------

@pytest.mark.unit
@pytest.mark.asyncio
async def test_cooldown_satisfied_triggers_recycle(monkeypatch):
    """Case 6: 3 天前已经发过 warn(dry_run=0), today 扫到 → cooldown=3 >= M, 应 recycle。"""
    session = _make_session()
    candidate = Candidate(
        bot_id="b6",
        entity_id="666",
        owner_id="o6",
        bot_name="Bot6",
        gmt_create=datetime(2026, 1, 1),
    )
    bot = BotModel(
        bot_id=candidate.bot_id,
        entity_id=candidate.entity_id,
        entity_type="user",
        creator_id="u1",
        owner_id=candidate.owner_id,
        bot_name=candidate.bot_name,
        status="ACTIVE",
        is_delete=0,
        bot_type="personal",
        gmt_create=candidate.gmt_create,
        gmt_modified=_now(),
    )
    session.add(bot)
    session.commit()

    _insert_audit(session, bot_id="b6", owner_id="o6",
                  action_taken="warn_enqueued",
                  gmt_create=datetime(2026, 6, 23), dry_run=0)

    baas_client = AsyncMock(spec=BaasDormantClient)
    baas_client.check_alive = AsyncMock(return_value=AliveResult(
        result="false",
        last_session_time="2026-05-01 10:00:00",
    ))
    bot_service = MagicMock()
    bot_service.stop_bot = MagicMock(return_value=True)
    bot_service.update_status = MagicMock()

    service = _make_service(session, baas_client=baas_client, bot_service=bot_service)

    monkeypatch.setattr(
        "agentclaw.community.core.bot_dormant.service.datetime",
        _FrozenDateTime(datetime(2026, 6, 26, 11, 30, 0)),
    )
    summary = await service.process_run(dry_run=False)
    assert summary.recycled == 1 and summary.warned == 0
    bot_service.stop_bot.assert_called_once()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_cooldown_not_yet_satisfied_continues_warning(monkeypatch):
    """Case 6b: 1 天前发过 warn(dry_run=0), today 扫到 → cooldown=1 < M=3,
    应继续 warn(不 recycle), 每日续推冷静期剩余天数文案。
    覆盖 `cooldown_days < self._M` 分支(service.py:961-962)。"""
    session = _make_session()
    candidate = Candidate(
        bot_id="b6b",
        entity_id="666",
        owner_id="o6b",
        bot_name="Bot6b",
        gmt_create=datetime(2026, 1, 1),
    )
    bot = BotModel(
        bot_id=candidate.bot_id,
        entity_id=candidate.entity_id,
        entity_type="user",
        creator_id="u1",
        owner_id=candidate.owner_id,
        bot_name=candidate.bot_name,
        status="ACTIVE",
        is_delete=0,
        bot_type="personal",
        gmt_create=candidate.gmt_create,
        gmt_modified=_now(),
    )
    session.add(bot)
    session.commit()

    # 1 天前发过 warn (cooldown=1, < M=3)
    _insert_audit(session, bot_id="b6b", owner_id="o6b",
                  action_taken="warn_enqueued",
                  gmt_create=datetime(2026, 6, 25), dry_run=0)

    baas_client = AsyncMock(spec=BaasDormantClient)
    baas_client.check_alive = AsyncMock(return_value=AliveResult(
        result="false",
        last_session_time="2026-05-01 10:00:00",
    ))
    bot_service = MagicMock()
    bot_service.stop_bot = MagicMock(return_value=True)
    bot_service.update_status = MagicMock()

    service = _make_service(session, baas_client=baas_client, bot_service=bot_service)

    monkeypatch.setattr(
        "agentclaw.community.core.bot_dormant.service.datetime",
        _FrozenDateTime(datetime(2026, 6, 26, 11, 30, 0)),
    )
    summary = await service.process_run(dry_run=False)
    assert summary.warned == 1 and summary.recycled == 0, \
        "cooldown 未满 M 天时应继续 warn, 不应 recycle"
    bot_service.stop_bot.assert_not_called()
