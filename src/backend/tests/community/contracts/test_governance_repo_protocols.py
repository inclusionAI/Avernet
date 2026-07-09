"""Rule 25 conformance — governance Repository Protocols.

Verifies structural subtyping (``issubclass``) for the 4 governance
Repository Protocols and their concrete implementations.  This is the
minimum bar: every method on the Protocol must exist on the concrete
class with a compatible signature.

Additionally, the dataclass I/O types (``EmergencyState``,
``TicketActionOutcome``, ``BulkOperationResult``) are verified to
serialize via ``to_dict()`` without error, and the ``@runtime_checkable``
check passes for every pair.

Why no integration round-trip?
  The governance repos require SQLite tables that are created by the
  ORM migrations (``ac_governance_task_record_daily``,
  ``ac_governance_notify_log``, etc.).  The generic ``world`` fixture
  uses a schema-less StaticPool.  Full round-trip testing belongs in
  the service-level integration suite; the contract test pins the
  *method surface* — the thing that breaks silently without structural
  checks.
"""
from __future__ import annotations

import inspect

import pytest

from agentclaw.community.core.economy.governance.domain.protocols import (
    AuditRepositoryProtocol,
    NotifyLogRepositoryProtocol,
    TaskRecordRepositoryProtocol,
    WhitelistRepositoryProtocol,
)
from agentclaw.community.core.economy.governance.repositories.audit_repo import (
    GovernanceAuditRepository,
)
from agentclaw.community.core.economy.governance.repositories.notify_log_repo import (
    NotifyLogRepository,
)
from agentclaw.community.core.economy.governance.repositories.task_record_repo import (
    TaskRecordRepository,
)
from agentclaw.community.core.economy.governance.repositories.whitelist_repo import (
    GovernanceWhitelistRepository,
)


# ---------------------------------------------------------------------------
# Protocol ↔ Concrete pairs
# ---------------------------------------------------------------------------

_PROTO_CONCRETE_PAIRS = [
    (TaskRecordRepositoryProtocol, TaskRecordRepository),
    (NotifyLogRepositoryProtocol, NotifyLogRepository),
    (AuditRepositoryProtocol, GovernanceAuditRepository),
    (WhitelistRepositoryProtocol, GovernanceWhitelistRepository),
]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "proto_cls, concrete_cls",
    _PROTO_CONCRETE_PAIRS,
    ids=[p.__name__ for p, _ in _PROTO_CONCRETE_PAIRS],
)
def test_runtime_checkable_subclass(
    proto_cls: type,
    concrete_cls: type,
) -> None:
    """Concrete class satisfies its Protocol via structural subtyping."""
    assert issubclass(concrete_cls, proto_cls), (
        f"{concrete_cls.__name__} does not satisfy {proto_cls.__name__}"
    )


@pytest.mark.parametrize(
    "proto_cls, concrete_cls",
    _PROTO_CONCRETE_PAIRS,
    ids=[p.__name__ for p, _ in _PROTO_CONCRETE_PAIRS],
)
def test_protocol_method_surface(
    proto_cls: type,
    concrete_cls: type,
) -> None:
    """Every public Protocol method exists on the concrete class."""
    proto_methods = {
        name
        for name, _ in inspect.getmembers(proto_cls, predicate=inspect.isfunction)
        if not name.startswith("_")
    }
    concrete_methods = {
        name
        for name, _ in inspect.getmembers(concrete_cls, predicate=inspect.isfunction)
        if not name.startswith("_")
    }
    missing = proto_methods - concrete_methods
    assert not missing, (
        f"{concrete_cls.__name__} missing methods: {sorted(missing)}"
    )


def test_emergency_state_to_dict() -> None:
    """EmergencyState serialises all fields via to_dict()."""
    from agentclaw.community.core.economy.governance.services.admin_service import (
        EmergencyState,
    )

    state = EmergencyState(
        paused=True,
        reason="test",
        operator="op",
        paused_at="2026-01-01T00:00:00",
        pending_count=5,
        open_count=3,
        whitelist_count=1,
    )
    d = state.to_dict()
    assert d["paused"] is True
    assert d["pending_count"] == 5
    assert len(d) == 7  # all fields serialised


def test_ticket_action_outcome_to_dict() -> None:
    """TicketActionOutcome serialises GovernanceStatus enum value."""
    from agentclaw.community.core.economy.governance.domain.enums import (
        GovernanceStatus,
    )
    from agentclaw.community.core.economy.governance.services.admin_service import (
        TicketActionOutcome,
    )

    outcome = TicketActionOutcome(
        ticket_id="t-1",
        status=GovernanceStatus.CLOSED,
        close_reason="emergency_closed",
    )
    d = outcome.to_dict()
    assert d["governance_status"] == "closed"
    assert d["ticket_id"] == "t-1"
    assert d["close_reason"] == "emergency_closed"


def test_ticket_action_outcome_error_path() -> None:
    """TicketActionOutcome with error fields serialises correctly."""
    from agentclaw.community.core.economy.governance.domain.enums import (
        GovernanceStatus,
    )
    from agentclaw.community.core.economy.governance.services.admin_service import (
        TicketActionOutcome,
    )

    outcome = TicketActionOutcome(
        ticket_id="t-2",
        status=GovernanceStatus.OPEN,
        error="Not found",
        error_code="NOT_FOUND",
    )
    d = outcome.to_dict()
    assert d["error"] == "Not found"
    assert d["error_code"] == "NOT_FOUND"


def test_bulk_operation_result_to_dict() -> None:
    """BulkOperationResult uses label as dict key."""
    from agentclaw.community.core.economy.governance.services.admin_service import (
        BulkOperationResult,
    )

    r = BulkOperationResult(affected=10, label="cancelled")
    assert r.to_dict() == {"cancelled": 10}

    r2 = BulkOperationResult(affected=5, label="closed", extra={"whitelisted": 3})
    d2 = r2.to_dict()
    assert d2 == {"closed": 5, "whitelisted": 3}


def test_governance_status_enum_str_compat() -> None:
    """str(GovernanceStatus.OPEN) == 'open' — ORM backward compat."""
    from agentclaw.community.core.economy.governance.domain.enums import (
        GovernanceStatus,
    )

    assert str(GovernanceStatus.OPEN) == "open"
    assert str(GovernanceStatus.CLOSED) == "closed"
    assert str(GovernanceStatus.WAITING_REVIEW) == "waiting_review"


def test_orm_business_properties() -> None:
    """GovernanceTicketOrm ORM business properties map DB columns to business names."""
    from datetime import datetime

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from agentclaw.community.core.economy.governance.repositories.orm import (
        GovernanceTicketOrm,
        Base,
    )
    from agentclaw.community.core.economy.governance.domain.enums import (
        GovernanceStatus,
    )

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    s = Session()

    ticket = GovernanceTicketOrm(
        worker_id="o1:b1",
        bot_id="b1",
        owner_id="o1",
        bot_name="TestBot",
        dt_version="20260101",
        governance_decision="actionable",
        hit_dimensions="cost,risk",
        governance_max_priority="high",
        expected_token_saving=1000,
        saving_ratio=0.5,
        task_summary="test",
        notification_structured="{}",
        analysis_status="completed",
        last_sync_at=datetime.now(),
        ticket_id="t-1",
        active_worker="o1:b1",
        governance_status="open",
        response="optimized",
        latest_decision="actionable",
    )
    s.add(ticket)
    s.commit()
    s.expire_on_commit = False

    t = s.query(GovernanceTicketOrm).first()
    assert t.initial_decision == "actionable"
    assert t.current_decision == "actionable"
    assert t.assignee == "o1:b1"
    assert t.severity == "high"
    assert t.triggered_dimensions == "cost,risk"
    assert t.estimated_saving_tokens == 1000
    assert t.user_feedback == "optimized"
    assert t.is_open is True
    assert t.is_active is True
    assert t.has_feedback is True
    assert t.can_accept_feedback() is False

    s.close()