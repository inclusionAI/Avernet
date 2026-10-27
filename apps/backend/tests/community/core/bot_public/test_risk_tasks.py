from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from agentclaw.community.core.bot_public_risk.repository import (
    BotPublicRiskRepository,
    RiskCheck,
    RiskCheckModel,
)
from agentclaw.community.core.bot_public_risk.tasks import RiskTasks, SecurityReport


class Db:
    def __init__(self):
        self.engine = create_engine("sqlite:///:memory:")
        RiskCheckModel.__table__.create(self.engine)
        self.sessions = sessionmaker(bind=self.engine)

    @contextmanager
    def transactional_orm_session(self):
        with self.sessions.begin() as session:
            yield session

    orm_session = transactional_orm_session


class Queue:
    def __init__(self):
        self.entries = []

    def enqueue(self, *args, **kwargs):
        self.entries.append((args, kwargs))


class Provider:
    def __init__(self):
        self.submissions = []
        self.queries = []
        self.report = SecurityReport("DETECTING")

    def submit_agent_scan(self, payload):
        self.submissions.append(payload)
        return "check-1"

    def get_agent_check_report(self, check_id):
        self.queries.append(check_id)
        return self.report


def make():
    now = [datetime(2026, 10, 28, 12)]
    repo, queue, provider = BotPublicRiskRepository(Db()), Queue(), Provider()
    tasks = RiskTasks(repo, queue, provider, lambda: now[0])
    record = RiskCheck(
        risk_check_id="a",
        env="dev",
        bot_pk=7,
        owner_id="owner",
        operator_id="operator",
        client_scope="new",
        client_request_id="req",
        public_scope="user",
        target_json="{}",
        request_hash="hash",
        input_json='{"agent_info": {}}',
        input_hash="input",
        approval_biz_id="biz",
    )
    return tasks, repo, queue, provider, now, record


def test_submit_once_and_timeout_pauses_without_pass():
    tasks, repo, queue, provider, now, record = make()
    tasks.start(record)
    tasks.submit(env="dev", risk_check_id="a")
    tasks.submit(env="dev", risk_check_id="a")
    assert len(provider.submissions) == 1
    first = repo.get(env="dev", risk_check_id="a")
    assert first.check_id == "check-1" and first.auto_poll_until == now[0] + timedelta(
        minutes=5
    )
    now[0] += timedelta(minutes=5)
    tasks.poll(env="dev", risk_check_id="a")
    after = repo.get(env="dev", risk_check_id="a")
    assert after.phase == "CHECKING" and after.auto_poll_paused
    assert after.security_status == "DETECTING" and provider.queries == []


def test_reject_window_stays_fixed_and_manual_refresh_queries_original():
    tasks, repo, queue, provider, now, record = make()
    tasks.start(record)
    tasks.submit(env="dev", risk_check_id="a")
    now[0] += timedelta(seconds=30)
    provider.report = SecurityReport("REJECT", "https://example.test/report")
    assert tasks.poll(env="dev", risk_check_id="a") == "UPDATED"
    first = repo.get(env="dev", risk_check_id="a")
    assert first.auto_poll_until == now[0] + timedelta(hours=1)
    now[0] += timedelta(minutes=5)
    tasks.poll(env="dev", risk_check_id="a")
    assert (
        repo.get(env="dev", risk_check_id="a").auto_poll_until == first.auto_poll_until
    )
    now[0] = first.auto_poll_until
    tasks.poll(env="dev", risk_check_id="a")
    assert repo.get(env="dev", risk_check_id="a").auto_poll_paused
    provider.report = SecurityReport("PASS", valid_pass=True)
    assert tasks.poll(env="dev", risk_check_id="a", manual=True) == "UPDATED"
    assert provider.queries == ["check-1"] * 3
    assert repo.get(env="dev", risk_check_id="a").phase == "ADVANCING"
    assert queue.entries[-1][0][0] == "bot_public_risk_advance"


def test_unverified_pass_requires_attention():
    tasks, repo, _, provider, now, record = make()
    tasks.start(record)
    tasks.submit(env="dev", risk_check_id="a")
    provider.report = SecurityReport("PASS")
    assert tasks.poll(env="dev", risk_check_id="a", manual=True) == "UPDATED"
    assert repo.get(env="dev", risk_check_id="a").phase == "NEEDS_ATTENTION"


def test_unknown_submission_and_reclaimed_intent_never_post_again():
    tasks, repo, _, provider, _, record = make()
    tasks.start(record)

    def fail(_):
        provider.submissions.append("unknown")
        raise TimeoutError()

    provider.submit_agent_scan = fail
    tasks.submit(env="dev", risk_check_id="a")
    tasks.submit(env="dev", risk_check_id="a")
    assert provider.submissions == ["unknown"]
    assert repo.get(env="dev", risk_check_id="a").phase == "NEEDS_ATTENTION"

    tasks, repo, _, provider, _, record = make()
    tasks.start(record)
    initial = repo.get(env="dev", risk_check_id="a")
    repo.update_current(
        env="dev",
        risk_check_id="a",
        row_version=initial.row_version,
        changes={"workflow_json": '{"submission_intent": true}'},
    )
    tasks.submit(env="dev", risk_check_id="a")
    assert provider.submissions == []
    assert repo.get(env="dev", risk_check_id="a").phase == "NEEDS_ATTENTION"


def test_late_report_cannot_advance_replaced_record():
    tasks, repo, queue, provider, now, record = make()
    tasks.start(record)
    tasks.submit(env="dev", risk_check_id="a")

    def delayed(_):
        repo.create_or_replay(
            replace(record, risk_check_id="b", client_request_id="req-b")
        )
        return SecurityReport("PASS", valid_pass=True)

    provider.get_agent_check_report = delayed
    tasks.poll(env="dev", risk_check_id="a", manual=True)
    assert repo.get(env="dev", risk_check_id="a").phase == "CHECKING"
    assert not any(entry[0][0] == "bot_public_risk_advance" for entry in queue.entries)


def test_queue_failure_leaves_recoverable_record():
    tasks, repo, queue, _, now, record = make()
    original = queue.enqueue

    def fail(*args, **kwargs):
        raise RuntimeError("unavailable")

    queue.enqueue = fail
    import pytest

    with pytest.raises(RuntimeError):
        tasks.start(record)
    queue.enqueue = original
    tasks.recover(env="dev", now=now[0])
    assert queue.entries[-1][0][0] == "bot_public_risk_submit"


def test_queue_moves_reject_to_distinct_task_and_reschedules_detecting():
    from agentclaw.community.core.bot_public_risk.tasks import (
        DETECTING,
        REJECT,
        RiskTaskHandler,
    )
    from agentclaw.community.core.task_queue.types import Complete, Reschedule

    tasks, repo, queue, provider, now, record = make()
    tasks.start(record)
    tasks.submit(env="dev", risk_check_id="a")
    payload = {"tenant": "teamclaw", "env": "dev", "risk_check_id": "a"}
    handler = RiskTaskHandler(tasks, DETECTING, lambda: "dev")
    assert isinstance(handler.handle(payload), Reschedule)
    assert provider.queries == []
    now[0] += timedelta(seconds=30)
    provider.report = SecurityReport("REJECT")
    assert isinstance(handler.handle(payload), Complete)
    assert queue.entries[-1][0][0] == REJECT
    assert queue.entries[-1][1]["delay_seconds"] == 300


def test_advance_enqueue_failure_is_recoverable():
    tasks, repo, queue, provider, now, record = make()
    tasks.start(record)
    tasks.submit(env="dev", risk_check_id="a")
    provider.report = SecurityReport("PASS", valid_pass=True)
    original = queue.enqueue

    def fail(*args, **kwargs):
        raise RuntimeError("unavailable")

    queue.enqueue = fail
    import pytest

    with pytest.raises(RuntimeError):
        tasks.poll(env="dev", risk_check_id="a", manual=True)
    assert repo.get(env="dev", risk_check_id="a").phase == "ADVANCING"
    queue.enqueue = original
    tasks.recover(env="dev", now=now[0])
    assert queue.entries[-1][0][0] == "bot_public_risk_advance"


def test_detecting_after_reject_uses_original_window_and_refresh_does_not_extend():
    tasks, repo, _, provider, now, record = make()
    tasks.start(record)
    tasks.submit(env="dev", risk_check_id="a")
    original_deadline = now[0] + timedelta(minutes=5)
    now[0] += timedelta(seconds=30)
    provider.report = SecurityReport("REJECT")
    tasks.poll(env="dev", risk_check_id="a")
    reject_deadline = now[0] + timedelta(hours=1)
    now[0] += timedelta(minutes=6)
    provider.report = SecurityReport("DETECTING")
    tasks.poll(env="dev", risk_check_id="a", manual=True)
    row = repo.get(env="dev", risk_check_id="a")
    assert row.security_status == "DETECTING"
    assert row.auto_poll_until == original_deadline
    assert row.auto_poll_paused and row.next_action_at is None
    now[0] += timedelta(minutes=1)
    tasks.poll(env="dev", risk_check_id="a", manual=True)
    assert repo.get(env="dev", risk_check_id="a").auto_poll_until == original_deadline
    provider.report = SecurityReport("REJECT")
    tasks.poll(env="dev", risk_check_id="a", manual=True)
    row = repo.get(env="dev", risk_check_id="a")
    assert row.auto_poll_until == reject_deadline
    assert not row.auto_poll_paused


def test_real_queue_reclaims_crashed_submission_without_second_post():
    import pytest
    from sqlalchemy import func
    from agentclaw.community.core.repository.implementations.platform.task_queue import (
        TaskQueueRepository,
    )
    from agentclaw.community.core.task_queue.repository.models import TaskQueueModel
    from agentclaw.community.core.task_queue.types import Complete
    from agentclaw.community.core.bot_public_risk.tasks import RiskTaskHandler, SUBMIT

    tasks, repo, _, provider, _, record = make()
    TaskQueueModel.__table__.create(repo._db.engine)
    queue_repo = TaskQueueRepository(repo._db)

    class RealQueue:
        def enqueue(
            self,
            kind,
            payload,
            deadline_seconds,
            *,
            delay_seconds=0,
            idempotency_key=None,
        ):
            return queue_repo.enqueue(
                task_type=kind,
                payload=payload,
                env="dev",
                app="agentclaw",
                deadline_seconds=deadline_seconds,
                delay_seconds=delay_seconds,
                idempotency_key=idempotency_key,
            )

    tasks.queue = RealQueue()
    tasks.start(record)
    claimed = queue_repo.claim_batch(
        worker_id="first", env="dev", app="agentclaw", limit=1, lease_seconds=30
    )
    assert len(claimed) == 1

    class ProcessCrash(BaseException):
        pass

    def crash(payload):
        provider.submissions.append(payload)
        raise ProcessCrash()

    provider.submit_agent_scan = crash
    handler = RiskTaskHandler(tasks, SUBMIT, lambda: "dev")
    with pytest.raises(ProcessCrash):
        handler.handle(claimed[0].payload)
    # Expire the real queue lease without sleeping; a second worker reclaims it.
    with repo._db.orm_session() as session:
        session.query(TaskQueueModel).filter_by(id=claimed[0].id).update(
            {TaskQueueModel.lease_expires_at: func.datetime("now", "-1 minute")},
            synchronize_session=False,
        )
    reclaimed = queue_repo.claim_batch(
        worker_id="second", env="dev", app="agentclaw", limit=1, lease_seconds=30
    )
    assert len(reclaimed) == 1 and reclaimed[0].id == claimed[0].id
    assert isinstance(handler.handle(reclaimed[0].payload), Complete)
    assert len(provider.submissions) == 1
    assert repo.get(env="dev", risk_check_id="a").phase == "NEEDS_ATTENTION"
    assert not queue_repo.complete(task_id=claimed[0].id, worker_id="first")
    assert queue_repo.complete(task_id=claimed[0].id, worker_id="second")
