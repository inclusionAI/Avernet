"""Unit tests for PublishOperationRunner against the real ledger repo (SQLite).

Covers open/resume, adopt-by-query (landed / already-terminal / no-match /
pre-ledger fence / type fence / ambiguous), the creation path, and crash-resume
(by interrupting the real ``record_workflow`` seam).
"""
import asyncio
from contextlib import contextmanager

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from agentclaw.community.core.service_bot.repository.models import (  # noqa: F401
    PublishOperationKind,
    PublishOperationModel,
    PublishOperationState,
)
from agentclaw.community.plugins.publish_operation_repository import (
    OrmPublishOperationRepository as PublishOperationRepository,
)
from agentclaw.community.core.service_bot.services.publish_flow.operation_runner import (
    PublishOperationError,
    PublishOperationRunner,
    TargetBotGoneError,
    acquire_deploy_workflow,
)
from agentclaw.community.core.service_bot.types import PublishStage


class InMemorySqliteDB:
    def __init__(self, engine):
        self._f = sessionmaker(bind=engine, autoflush=False)

    @contextmanager
    def orm_session(self):
        db = self._f()
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()


class FakeBaas:
    """Minimal BaaS stand-in: a per-bot workflow list that ``issue`` appends to."""

    def __init__(self):
        self.workflows = {}
        self._next = 1000

    def list_bot_publishes(self, bot_uuid):
        return [dict(w) for w in self.workflows.get(bot_uuid, [])]

    def issue(self, bot_uuid, publish_type="UPDATE", status="ACTIVE"):
        self._next += 1
        wid = self._next
        self.workflows.setdefault(bot_uuid, []).append(
            {"id": wid, "publish_type": publish_type, "status": status, "gmt_create": "t"}
        )
        return wid

    def seed(self, bot_uuid, wid, publish_type="UPDATE", status="SUCCESS"):
        self.workflows.setdefault(bot_uuid, []).append(
            {"id": wid, "publish_type": publish_type, "status": status, "gmt_create": "t"}
        )


@pytest.fixture
def ledger():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    from agentclaw.community.core.base import Base

    Base.metadata.create_all(engine)
    return PublishOperationRepository(InMemorySqliteDB(engine))


@pytest.fixture
def baas():
    return FakeBaas()


def _runner(ledger, baas):
    return PublishOperationRunner(
        ledger=ledger,
        baas_service=baas,
    )


UPGRADE = PublishOperationKind.UPGRADE.value
CREATE = PublishOperationKind.FIRST_RELEASE.value


def run(coro):
    return asyncio.run(coro)


# ── open / resume ─────────────────────────────────────────────────────────────
def test_open_creates_pending_then_resumes(ledger, baas):
    r = _runner(ledger, baas)
    op1 = r.open_operation(publish_id=1, kind=UPGRADE, stage=PublishStage.ONLINE, bot_uuid="b")
    assert op1.state == PublishOperationState.PENDING.value
    assert op1.attempt == 1
    op2 = r.open_operation(publish_id=1, kind=UPGRADE, stage=PublishStage.ONLINE, bot_uuid="b")
    assert op2.id == op1.id  # resumed the in-flight op


def test_open_after_terminal_opens_next_attempt(ledger, baas):
    r = _runner(ledger, baas)
    op1 = r.open_operation(publish_id=1, kind=UPGRADE, stage=PublishStage.ONLINE, bot_uuid="b")
    ledger.abandon(op1.id, "superseded")
    op2 = r.open_operation(publish_id=1, kind=UPGRADE, stage=PublishStage.ONLINE, bot_uuid="b")
    assert op2.id != op1.id
    assert op2.attempt == 2


# ── acquire: already recorded ───────────────────────────────────────────────
def test_acquire_noop_when_already_recorded(ledger, baas):
    r = _runner(ledger, baas)
    op = r.open_operation(publish_id=1, kind=UPGRADE, stage=PublishStage.ONLINE, bot_uuid="b")
    ledger.record_workflow(op.id, baas_publish_id=777, bot_uuid="b")
    op = ledger.get_by_id(op.id)
    calls = []

    async def issue():
        calls.append(1)
        return {"publish_id": 1}

    out = run(r.acquire_workflow(op, issue))
    assert out.baas_publish_id == 777
    assert calls == []  # never issued


# ── acquire: creation path ──────────────────────────────────────────────────
def test_acquire_creation_issues_and_records_bot_uuid(ledger, baas):
    r = _runner(ledger, baas)
    op = r.open_operation(publish_id=1, kind=CREATE, stage=PublishStage.ONLINE)  # no bot_uuid
    assert op.bot_uuid is None  # creation kind: no target bot yet → NULL, not ""

    async def issue():
        wid = baas.issue("new-bot", publish_type="CREATE")
        return {"publish_id": wid, "bot_uuid": "new-bot"}

    out = run(r.acquire_workflow(op, issue))
    assert out.state == PublishOperationState.ID_RECORDED.value
    assert out.bot_uuid == "new-bot"
    assert out.baas_publish_id is not None


# ── acquire: existing-bot first time issues ─────────────────────────────────
def test_acquire_existing_bot_first_issue(ledger, baas):
    r = _runner(ledger, baas)
    baas.seed("b", 500, publish_type="UPDATE", status="SUCCESS")  # pre-existing history
    op = r.open_operation(publish_id=1, kind=UPGRADE, stage=PublishStage.ONLINE, bot_uuid="b")
    issued = []

    async def issue():
        wid = baas.issue("b", publish_type="UPDATE")
        issued.append(wid)
        return {"publish_id": wid}

    out = run(r.acquire_workflow(op, issue))
    assert len(issued) == 1
    assert out.baas_publish_id == issued[0]


# ── crash after issue, before record → resume adopts (not re-issue) ─────────
def test_crash_after_issue_resume_adopts(ledger, baas):
    r = _runner(ledger, baas)
    op = r.open_operation(publish_id=1, kind=UPGRADE, stage=PublishStage.ONLINE, bot_uuid="b")

    issue_count = []

    async def issue():
        wid = baas.issue("b", publish_type="UPDATE")
        issue_count.append(wid)
        return {"publish_id": wid}

    # Crash right after the BaaS call landed but before the ledger records the id:
    # record_workflow raises on its first call, then behaves normally on resume.
    real_record = ledger.record_workflow
    crashed = []

    def record_once_then_crash(*a, **k):
        if not crashed:
            crashed.append(1)
            raise RuntimeError("pod died after issue")
        return real_record(*a, **k)

    ledger.record_workflow = record_once_then_crash

    with pytest.raises(RuntimeError):
        run(r.acquire_workflow(op, issue))

    # Resume: fresh runner (no crash), reload op from ledger.
    r2 = _runner(ledger, baas)
    reopened = r2.open_operation(publish_id=1, kind=UPGRADE, stage=PublishStage.ONLINE, bot_uuid="b")
    out = run(r2.acquire_workflow(reopened, issue))

    assert len(issue_count) == 1  # issued exactly once across both runs
    assert out.baas_publish_id == issue_count[0]  # adopted the in-doubt workflow


def test_adopt_already_terminal_workflow(ledger, baas):
    # baseline persisted, then a SUCCESS workflow appears above it (unclaimed).
    r = _runner(ledger, baas)
    op = r.open_operation(publish_id=1, kind=UPGRADE, stage=PublishStage.ONLINE, bot_uuid="b")
    # simulate first-acquire baseline snapshot (max=0), then a landed terminal wf.
    op = r._persist_baseline(op, 0)
    baas.seed("b", 900, publish_type="UPDATE", status="SUCCESS")

    async def issue():
        raise AssertionError("must not issue — should adopt")

    out = run(r.acquire_workflow(op, issue))
    assert out.baas_publish_id == 900


def test_no_match_issues(ledger, baas):
    r = _runner(ledger, baas)
    op = r.open_operation(publish_id=1, kind=UPGRADE, stage=PublishStage.ONLINE, bot_uuid="b")
    op = r._persist_baseline(op, 900)  # nothing above baseline
    baas.seed("b", 900, status="SUCCESS")  # equal to baseline, excluded
    issued = []

    async def issue():
        wid = baas.issue("b", publish_type="UPDATE")
        issued.append(wid)
        return {"publish_id": wid}

    out = run(r.acquire_workflow(op, issue))
    assert len(issued) == 1
    assert out.baas_publish_id == issued[0]


def test_pre_ledger_fence_excludes_old_workflow(ledger, baas):
    r = _runner(ledger, baas)
    op = r.open_operation(publish_id=1, kind=UPGRADE, stage=PublishStage.ONLINE, bot_uuid="b")
    # A pre-ledger workflow (id 50) below the baseline snapshot; our landed one above.
    baas.seed("b", 50, publish_type="UPDATE", status="SUCCESS")
    op = r._persist_baseline(op, 50)
    baas.seed("b", 120, publish_type="UPDATE", status="ACTIVE")

    async def issue():
        raise AssertionError("must not issue")

    out = run(r.acquire_workflow(op, issue))
    assert out.baas_publish_id == 120  # only the post-baseline one adopted


def test_type_fence_ignores_wrong_type(ledger, baas):
    r = _runner(ledger, baas)
    op = r.open_operation(publish_id=1, kind=UPGRADE, stage=PublishStage.ONLINE, bot_uuid="b")
    op = r._persist_baseline(op, 0)
    baas.seed("b", 130, publish_type="SCALE_UP", status="ACTIVE")  # wrong type
    issued = []

    async def issue():
        wid = baas.issue("b", publish_type="UPDATE")
        issued.append(wid)
        return {"publish_id": wid}

    out = run(r.acquire_workflow(op, issue))
    assert len(issued) == 1  # scale workflow ignored → issued our own


def test_ambiguous_multiple_matches_fail_loudly(ledger, baas):
    r = _runner(ledger, baas)
    op = r.open_operation(publish_id=1, kind=UPGRADE, stage=PublishStage.ONLINE, bot_uuid="b")
    op = r._persist_baseline(op, 0)
    baas.seed("b", 140, publish_type="UPDATE", status="ACTIVE")
    baas.seed("b", 141, publish_type="UPDATE", status="ACTIVE")

    async def issue():
        raise AssertionError("must not issue")

    with pytest.raises(PublishOperationError):
        run(r.acquire_workflow(op, issue))
    assert ledger.get_by_id(op.id).state == PublishOperationState.FAILED.value


def test_known_ids_excluded_from_adoption(ledger, baas):
    # A prior op recorded workflow 200 for bot b; a new op must not re-adopt it.
    r = _runner(ledger, baas)
    prior = r.open_operation(publish_id=1, kind="restart", stage=PublishStage.ONLINE, bot_uuid="b")
    ledger.record_workflow(prior.id, baas_publish_id=200)
    baas.seed("b", 200, publish_type="UPDATE", status="SUCCESS")

    op = r.open_operation(publish_id=2, kind=UPGRADE, stage=PublishStage.ONLINE, bot_uuid="b")
    op = r._persist_baseline(op, 0)
    issued = []

    async def issue():
        wid = baas.issue("b", publish_type="UPDATE")
        issued.append(wid)
        return {"publish_id": wid}

    out = run(r.acquire_workflow(op, issue))
    assert len(issued) == 1  # 200 is claimed → not adopted → issued fresh
    assert out.baas_publish_id != 200


# ── finalize ────────────────────────────────────────────────────────────────
def test_finalize_transitions(ledger, baas):
    r = _runner(ledger, baas)
    op = r.open_operation(publish_id=1, kind=UPGRADE, stage=PublishStage.ONLINE, bot_uuid="b")
    ledger.record_workflow(op.id, baas_publish_id=1)
    assert r.complete_operation(op).state == PublishOperationState.COMPLETED.value

    op2 = r.open_operation(publish_id=2, kind=UPGRADE, stage=PublishStage.ONLINE, bot_uuid="b")
    assert r.fail_operation(op2, "err").state == PublishOperationState.FAILED.value

    op3 = r.open_operation(publish_id=3, kind=UPGRADE, stage=PublishStage.ONLINE, bot_uuid="b")
    assert r.abandon_operation(op3, "sup").state == PublishOperationState.ABANDONED.value


def test_record_step_result_merges(ledger, baas):
    r = _runner(ledger, baas)
    op = r.open_operation(publish_id=1, kind=UPGRADE, stage=PublishStage.ONLINE, bot_uuid="b")
    op = r.record_step_result(op, {"binding_id": 5})
    op = r.record_step_result(op, {"draft_id": 9})
    assert op.result["binding_id"] == 5
    assert op.result["draft_id"] == 9


# ── acquire_deploy_workflow (the deploy atom) ─────────────────────────────────
def test_atom_happy_path_records_workflow(ledger, baas):
    r = _runner(ledger, baas)

    async def issue():
        wid = baas.issue("BOT-live", "UPDATE")
        return {"publish_id": wid, "success": True}

    op = run(acquire_deploy_workflow(
        r, publish_id=1, kind=PublishOperationKind.UPGRADE,
        stage=PublishStage.ONLINE, operator="op", issue=issue, bot_uuid="BOT-live",
    ))
    assert op.baas_publish_id == 1001
    # Returned un-completed: follow-up steps + complete stay with the caller.
    assert op.state == PublishOperationState.ID_RECORDED.value


def test_atom_bot_not_found_abandons_with_reason_and_raises(ledger, baas):
    r = _runner(ledger, baas)

    async def issue():
        return {"success": False, "error_code": "BOT_NOT_FOUND"}

    with pytest.raises(TargetBotGoneError):
        run(acquire_deploy_workflow(
            r, publish_id=1, kind=PublishOperationKind.UPGRADE,
            stage=PublishStage.ONLINE, operator="op", issue=issue,
            bot_uuid="BOT-gone", bot_gone_reason="BOT_NOT_FOUND -> first release",
        ))
    op = ledger.get_latest_by_kind(1, UPGRADE, "online")
    assert op.state == PublishOperationState.ABANDONED.value
    assert op.last_error == "BOT_NOT_FOUND -> first release"


def test_atom_creation_requires_bot_uuid(ledger, baas):
    r = _runner(ledger, baas)

    async def issue():
        # Creation that "succeeds" but fails to return the new bot's uuid.
        return {"publish_id": 999}

    with pytest.raises(PublishOperationError):
        run(acquire_deploy_workflow(
            r, publish_id=1, kind=PublishOperationKind.FIRST_RELEASE,
            stage=PublishStage.ONLINE, operator="op", issue=issue,
        ))


def test_atom_never_skips_issue_for_current_deployment(ledger, baas):
    """The point-2 guard: the atom has no skip-if-current-deployment logic —
    a deploy on a bot whose release is live and current still reaches issue
    (a restart must always re-deploy via BaaS)."""
    r = _runner(ledger, baas)

    async def land(kind):
        async def issue():
            wid = baas.issue("BOT-live", "UPDATE")
            return {"publish_id": wid, "success": True}
        op = await acquire_deploy_workflow(
            r, publish_id=1, kind=kind,
            stage=PublishStage.ONLINE, operator="op", issue=issue,
            bot_uuid="BOT-live",
        )
        r.complete_operation(op)
        return op

    run(land(PublishOperationKind.UPGRADE))       # the live, current release
    op2 = run(land(PublishOperationKind.RESTART))  # restart still issues
    assert op2.baas_publish_id == 1002
    assert len(baas.list_bot_publishes("BOT-live")) == 2


def test_atom_crash_before_record_resumes_via_adopt(ledger, baas):
    """Existing-bot crash window: issue landed, record_workflow died. The re-run
    resumes the SAME op and adopts the in-doubt workflow (no second issue)."""
    r = _runner(ledger, baas)
    issued = []

    async def issue():
        wid = baas.issue("BOT-live", "UPDATE")
        issued.append(wid)
        return {"publish_id": wid, "success": True}

    real = ledger.record_workflow
    state = {"crashed": False}

    def crash_once(*a, **kw):
        if not state["crashed"]:
            state["crashed"] = True
            raise RuntimeError("pod died after issue, before record")
        return real(*a, **kw)

    ledger.record_workflow = crash_once

    kwargs = dict(
        publish_id=1, kind=PublishOperationKind.UPGRADE,
        stage=PublishStage.ONLINE, operator="op", issue=issue, bot_uuid="BOT-live",
    )
    # First call establishes the baseline fence and issues, then crashes on record.
    with pytest.raises(RuntimeError):
        run(acquire_deploy_workflow(r, **kwargs))
    assert len(issued) == 1

    op = run(acquire_deploy_workflow(r, **kwargs))
    assert len(issued) == 1                      # adopted, not re-issued
    assert op.baas_publish_id == issued[0]


def test_atom_crash_before_record_reissues_for_creation(ledger, baas):
    """Creation crash window: no bot to adopt from → the re-run re-issues
    (the accepted Option-C orphan), and the op records the second workflow."""
    r = _runner(ledger, baas)
    issued = []

    async def issue():
        wid = baas.issue("BOT-new", "CREATE")
        issued.append(wid)
        return {"publish_id": wid, "bot_uuid": "BOT-new", "success": True}

    real = ledger.record_workflow
    state = {"crashed": False}

    def crash_once(*a, **kw):
        if not state["crashed"]:
            state["crashed"] = True
            raise RuntimeError("pod died after issue, before record")
        return real(*a, **kw)

    ledger.record_workflow = crash_once

    kwargs = dict(
        publish_id=1, kind=PublishOperationKind.FIRST_RELEASE,
        stage=PublishStage.ONLINE, operator="op", issue=issue,
    )
    with pytest.raises(RuntimeError):
        run(acquire_deploy_workflow(r, **kwargs))
    op = run(acquire_deploy_workflow(r, **kwargs))
    assert len(issued) == 2
    assert op.baas_publish_id == issued[1]
    assert op.bot_uuid == "BOT-new"
