from contextlib import contextmanager
from dataclasses import replace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from agentclaw.community.core.bot_public_risk.repository import (
    BotPublicRiskRepository,
    RequestConflict,
    RiskCheck,
    RiskCheckModel,
)


class Database:
    def __init__(self):
        self.engine = create_engine("sqlite:///:memory:")
        RiskCheckModel.__table__.create(self.engine)
        self.sessions = sessionmaker(bind=self.engine)

    @contextmanager
    def transactional_orm_session(self):
        with self.sessions.begin() as session:
            yield session

    orm_session = transactional_orm_session


@pytest.fixture
def repo():
    return BotPublicRiskRepository(Database())


def record(identifier="a", request="req-a", **changes):
    return replace(
        RiskCheck(
            risk_check_id=identifier,
            env="dev",
            bot_pk=7,
            owner_id="owner",
            operator_id="operator",
            client_scope="new:operator",
            client_request_id=request,
            public_scope="user",
            target_json="{}",
            request_hash="hash",
            input_json="{}",
            input_hash="input",
            approval_biz_id="biz-" + identifier,
        ),
        **changes,
    )


def test_replay_conflict_and_replacement_do_not_restore_old_current(repo):
    a, created = repo.create_or_replay(record())
    assert created and a.row_version == 1
    b, created = repo.create_or_replay(record("b", "req-b"))
    assert (
        created
        and repo.get_current(env="dev", bot_pk=7, public_scope="user").risk_check_id
        == "b"
    )
    replay, created = repo.create_or_replay(record())
    assert not created and replay.risk_check_id == "a"
    assert (
        repo.get_current(env="dev", bot_pk=7, public_scope="user").risk_check_id == "b"
    )
    with pytest.raises(RequestConflict):
        repo.create_or_replay(record(bot_pk=8))
    with pytest.raises(RequestConflict):
        repo.create_or_replay(record(request_hash="other"))


def test_cas_cannot_update_replaced_record_or_stale_version(repo):
    a, _ = repo.create_or_replay(record())
    assert repo.update_current(
        env="dev",
        risk_check_id="a",
        row_version=a.row_version,
        changes={"phase": "CHECKING"},
    )
    assert not repo.update_current(
        env="dev",
        risk_check_id="a",
        row_version=a.row_version,
        changes={"phase": "ADVANCING"},
    )
    a = repo.get(env="dev", risk_check_id="a")
    repo.create_or_replay(record("b", "req-b"))
    assert not repo.update_current(
        env="dev",
        risk_check_id="a",
        row_version=a.row_version,
        changes={"phase": "ADVANCING"},
    )
    assert repo.get(env="dev", risk_check_id="a").phase == "CHECKING"


def test_tenant_and_environment_isolation(repo):
    from agentclaw.community.utils.avernet_tenant import avernet_tenant_scope

    repo.create_or_replay(record())
    with avernet_tenant_scope("other"):
        assert repo.get(env="dev", risk_check_id="a") is None
        assert repo.get_current(env="dev", bot_pk=7, public_scope="user") is None
        repo.create_or_replay(record("other-id"))
        assert (
            repo.get_current(env="dev", bot_pk=7, public_scope="user").risk_check_id
            == "other-id"
        )
    assert repo.get(env="prod", risk_check_id="a") is None
    assert repo.get_current(env="prod", bot_pk=7, public_scope="user") is None
    assert (
        repo.get_current(env="dev", bot_pk=7, public_scope="user").risk_check_id == "a"
    )


def test_failed_insert_rolls_back_replacement(repo):
    repo.create_or_replay(record())
    # Duplicate primary key with a different idempotency scope triggers a write error.
    with pytest.raises(Exception):
        repo.create_or_replay(record("a", "new-request"))
    assert (
        repo.get_current(env="dev", bot_pk=7, public_scope="user").risk_check_id == "a"
    )


def test_concurrent_first_creates_leave_one_current(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    db = Database.__new__(Database)
    db.engine = create_engine(
        f"sqlite:///{tmp_path / 'risk.db'}", connect_args={"timeout": 10}
    )
    RiskCheckModel.__table__.create(db.engine)
    db.sessions = sessionmaker(bind=db.engine)
    repo = BotPublicRiskRepository(db)
    barrier = Barrier(2)

    def create(row):
        barrier.wait()
        return repo.create_or_replay(row)

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(create, record("a", "req-a")),
            executor.submit(create, record("b", "req-b")),
        ]
        assert all(f.result()[1] for f in futures)
    current = repo.get_current(env="dev", bot_pk=7, public_scope="user")
    assert current.risk_check_id in {"a", "b"}
    other = "b" if current.risk_check_id == "a" else "a"
    assert repo.get(env="dev", risk_check_id=other).current_target_key is None


def test_withdrawal_and_recovery_exclude_paused_and_replaced(repo):
    from datetime import datetime, timedelta

    now = datetime(2026, 10, 28, 12)
    a, _ = repo.create_or_replay(record())
    assert repo.update_current(
        env="dev",
        risk_check_id="a",
        row_version=a.row_version,
        changes={"next_action_at": now - timedelta(seconds=1)},
    )
    assert [x.risk_check_id for x in repo.list_due(env="dev", now=now)] == ["a"]
    a = repo.get(env="dev", risk_check_id="a")
    assert repo.update_current(
        env="dev",
        risk_check_id="a",
        row_version=a.row_version,
        changes={"auto_poll_paused": True},
    )
    assert repo.list_due(env="dev", now=now) == ()
    assert repo.invalidate_current(env="dev", bot_pk=7, public_scope="user")
    assert not repo.invalidate_current(env="dev", bot_pk=7, public_scope="user")
    assert repo.get(env="dev", risk_check_id="a").current_target_key is None


def test_concurrent_same_request_replays_one_record(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    db = Database.__new__(Database)
    db.engine = create_engine(
        f"sqlite:///{tmp_path / 'replay.db'}", connect_args={"timeout": 10}
    )
    RiskCheckModel.__table__.create(db.engine)
    db.sessions = sessionmaker(bind=db.engine)
    repo = BotPublicRiskRepository(db)
    barrier = Barrier(2)

    def create(row):
        barrier.wait()
        return repo.create_or_replay(row)

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(create, record("a")),
            executor.submit(create, record("b")),
        ]
        results = [f.result() for f in futures]
    assert sorted(created for _, created in results) == [False, True]
    assert results[0][0].risk_check_id == results[1][0].risk_check_id
