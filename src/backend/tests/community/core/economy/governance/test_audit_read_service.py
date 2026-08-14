"""Unit tests for GovernanceAuditReadService — worker_id parsing + read delegation.

The service is a thin read layer: parses composite worker_id (owner:bot),
delegates to GovernanceAuditRepository.list_by_subject, serializes rows via
to_dict(). Tests exercise parser correctness/precedence + real-SQLite read.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest
from sqlalchemy.orm import sessionmaker

from agentclaw.community.core.repository.implementations.governance.audit import GovernanceAuditRepository
from agentclaw.community.core.economy.governance.orm import AuditLogOrm
from agentclaw.community.core.economy.governance.services.audit_read_service import (
    GovernanceAuditReadService,
)

# Re-use the shared FakeDB from conftest.
from tests.community.core.economy.governance.conftest import FakeDB


_ENV_PATCH = (
    "agentclaw.community.core.repository.implementations.governance.audit.get_current_env"
)


def _build_svc(engine) -> tuple[GovernanceAuditReadService, GovernanceAuditRepository]:
    db = FakeDB(lambda: sessionmaker(bind=engine, expire_on_commit=False)())
    audit_repo = GovernanceAuditRepository(db=db)
    return GovernanceAuditReadService(audit_repo=audit_repo), audit_repo


def _seed(engine, rows_spec):
    """Seed audit rows as (bot_id, owner_id, action_taken, run_id) at env=dev."""
    with patch(_ENV_PATCH, return_value="dev"):
        Session = sessionmaker(bind=engine, expire_on_commit=False)
        s = Session()
        try:
            for bot_id, owner_id, action, run_id in rows_spec:
                s.add(AuditLogOrm(
                    run_id=run_id, bot_id=bot_id, owner_id=owner_id,
                    action_taken=action, source="daily_scan", env="dev",
                ))
            s.commit()
        finally:
            s.close()


# ── worker_id parsing ─────────────────────────────────────────────────


class TestParseWorkerId:
    def test_valid_owner_bot_split(self):
        owner, bot = GovernanceAuditReadService._parse_worker_id("owner-1:bot-a")
        assert owner == "owner-1" and bot == "bot-a"

    @pytest.mark.parametrize("bad", ["no-colon", ":bot-a", "owner-1:", "owner-1: :x", "  :  "])
    def test_invalid_raises(self, bad):
        with pytest.raises(ValueError):
            GovernanceAuditReadService._parse_worker_id(bad)

    def test_strips_whitespace(self):
        owner, bot = GovernanceAuditReadService._parse_worker_id("  owner-1 : bot-a  ")
        assert owner == "owner-1" and bot == "bot-a"


# ── list_audit_by_worker delegation ────────────────────────────────────


class TestListAuditByWorker:
    def test_worker_id_parses_and_filters(self, engine, tables):
        svc, _ = _build_svc(engine)
        _seed(engine, [
            ("bot-a", "owner-1", "admin_whitelisted", "r1"),
            ("bot-b", "owner-1", "enqueued", "r2"),
            ("bot-a", "owner-2", "admin_whitelisted", "r3"),
        ])
        with patch(_ENV_PATCH, return_value="dev"):
            items, total = svc.list_audit_by_worker(worker_id="owner-1:bot-a")
        assert total == 1
        assert items[0]["run_id"] == "r1"
        assert items[0]["bot_id"] == "bot-a" and items[0]["owner_id"] == "owner-1"

    def test_worker_id_overrides_independent_params(self, engine, tables):
        svc, _ = _build_svc(engine)
        _seed(engine, [
            ("bot-a", "owner-1", "admin_whitelisted", "r1"),
            ("bot-z", "owner-9", "admin_whitelisted", "r2"),
        ])
        with patch(_ENV_PATCH, return_value="dev"):
            # worker_id owner-1:bot-a should win over the bogus independent params.
            items, total = svc.list_audit_by_worker(
                worker_id="owner-1:bot-a", owner_id="owner-9", bot_id="bot-z",
            )
        assert total == 1 and items[0]["run_id"] == "r1"

    def test_independent_owner_only(self, engine, tables):
        svc, _ = _build_svc(engine)
        _seed(engine, [
            ("bot-a", "owner-1", "enqueued", "r1"),
            ("bot-b", "owner-1", "enqueued", "r2"),
            ("bot-c", "owner-2", "enqueued", "r3"),
        ])
        with patch(_ENV_PATCH, return_value="dev"):
            items, total = svc.list_audit_by_worker(owner_id="owner-1")
        assert total == 2
        assert all(it["owner_id"] == "owner-1" for it in items)

    def test_action_filter(self, engine, tables):
        svc, _ = _build_svc(engine)
        _seed(engine, [
            ("bot-a", "owner-1", "admin_whitelisted", "r1"),
            ("bot-a", "owner-1", "enqueued", "r2"),
        ])
        with patch(_ENV_PATCH, return_value="dev"):
            items, total = svc.list_audit_by_worker(
                worker_id="owner-1:bot-a", action="admin_whitelisted",
            )
        assert total == 1 and items[0]["run_id"] == "r1"

    def test_all_empty_raises(self, engine, tables):
        svc, _ = _build_svc(engine)
        with patch(_ENV_PATCH, return_value="dev"):
            with pytest.raises(ValueError):
                svc.list_audit_by_worker()

    def test_invalid_worker_id_raises(self, engine, tables):
        svc, _ = _build_svc(engine)
        with patch(_ENV_PATCH, return_value="dev"):
            with pytest.raises(ValueError):
                svc.list_audit_by_worker(worker_id="no-colon")

    def test_pagination(self, engine, tables):
        svc, _ = _build_svc(engine)
        _seed(engine, [
            (f"bot-{i}", "owner-1", "enqueued", f"r{i}") for i in range(5)
        ])
        with patch(_ENV_PATCH, return_value="dev"):
            page, total = svc.list_audit_by_worker(owner_id="owner-1", limit=2, offset=0)
            next_page, _ = svc.list_audit_by_worker(owner_id="owner-1", limit=2, offset=2)
        assert total == 5
        assert len(page) == 2 and len(next_page) == 2
        assert {it["run_id"] for it in page}.isdisjoint({it["run_id"] for it in next_page})

    def test_items_are_dicts_with_audit_fields(self, engine, tables):
        svc, _ = _build_svc(engine)
        _seed(engine, [("bot-a", "owner-1", "enqueued", "r1")])
        with patch(_ENV_PATCH, return_value="dev"):
            items, _ = svc.list_audit_by_worker(owner_id="owner-1")
        item = items[0]
        for key in ("run_id", "bot_id", "owner_id", "action_taken",
                    "actor_id", "source", "env", "gmt_create"):
            assert key in item, f"missing audit field {key}"
