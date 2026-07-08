"""Integration tests for bot_dormant ORM models.

Tests table creation and unique constraint enforcement.
"""
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.exc import IntegrityError

from agentclaw.community.plugin_api.models import Base
from agentclaw.community.core.bot_dormant.sqlite_models import (
    DormantNotifyLog,
    DormantWhitelist,
    DormantExternalInput,
    DormantCheckAudit,
)


@pytest.mark.integration
def test_dormant_tables_create_all():
    """Test that all dormant tables can be created via Base.metadata.create_all()."""
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)

    # Verify tables exist by querying sqlite_master
    with engine.connect() as conn:
        result = conn.execute(
            text("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'ac_bot_dormant%'")
        )
        tables = {row[0] for row in result.fetchall()}

    expected = {
        "ac_bot_dormant_notify_log",
        "ac_bot_dormant_whitelist",
        "ac_bot_dormant_external_input",
        "ac_bot_dormant_check_audit",
    }
    assert tables == expected, f"Expected {expected}, got {tables}"


@pytest.mark.integration
def test_dormant_notify_log_unique_constraint():
    """uk_bot_owner_dt_type: same (bot_id, owner_id, dt, notify_type) conflicts;
    different owner_id with same other fields must NOT conflict — bot_id='default'
    is per-owner so two owners' default bots must both be writable on the same dt."""
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)

    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()

    try:
        log1 = DormantNotifyLog(
            bot_id="default",
            owner_id="owner1",
            notify_type="warn",
            dt="20240101",
            send_status="pending",
        )
        session.add(log1)
        session.commit()

        # Same bot_id + different owner_id → allowed
        log2 = DormantNotifyLog(
            bot_id="default",
            owner_id="owner2",
            notify_type="warn",
            dt="20240101",
            send_status="pending",
        )
        session.add(log2)
        session.commit()

        # Exact duplicate → fails
        log3 = DormantNotifyLog(
            bot_id="default",
            owner_id="owner1",
            notify_type="warn",
            dt="20240101",
            send_status="pending",
        )
        session.add(log3)
        with pytest.raises(IntegrityError):
            session.commit()
    finally:
        session.close()


@pytest.mark.integration
def test_dormant_whitelist_unique_constraint():
    """uk_wl_bot_owner: same (bot_id, owner_id) conflicts; different owner allowed."""
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)

    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()

    try:
        wl1 = DormantWhitelist(bot_id="default", owner_id="owner1", reason="test")
        session.add(wl1)
        session.commit()

        # Same bot_id + different owner → allowed
        wl2 = DormantWhitelist(bot_id="default", owner_id="owner2", reason="test2")
        session.add(wl2)
        session.commit()

        # Exact duplicate → fails
        wl3 = DormantWhitelist(bot_id="default", owner_id="owner1", reason="dup")
        session.add(wl3)
        with pytest.raises(IntegrityError):
            session.commit()
    finally:
        session.close()


@pytest.mark.integration
def test_dormant_external_input_insert():
    """Test DormantExternalInput can be inserted without constraint violations."""
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)

    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()

    try:
        ext_input = DormantExternalInput(
            bot_id="bot1",
            owner_id="owner1",
            governance_source="auto",
            dt="20240101",
            processed=0,
        )
        session.add(ext_input)
        session.commit()

        # Verify insertion succeeded
        count = session.query(DormantExternalInput).filter_by(bot_id="bot1").count()
        assert count == 1
    finally:
        session.close()


@pytest.mark.integration
def test_dormant_check_audit_insert():
    """Test DormantCheckAudit can be inserted."""
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)

    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()

    try:
        audit = DormantCheckAudit(
            run_id="run001",
            bot_id="bot1",
            check_result="dormant",
            days_inactive=30,
            action_taken="notify",
            source="auto",
            dry_run=0,
        )
        session.add(audit)
        session.commit()

        # Verify insertion succeeded
        count = session.query(DormantCheckAudit).filter_by(bot_id="bot1").count()
        assert count == 1
    finally:
        session.close()
