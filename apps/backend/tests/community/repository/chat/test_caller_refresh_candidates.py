"""Real-store due selection and keyset pagination for hourly Caller refresh."""
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta, timezone

import pytest

from sqlalchemy import MetaData, create_engine
from sqlalchemy.orm import sessionmaker

from agentclaw.community.core.expert_chat.sqlite_models import AcExpertChatInstance
from agentclaw.community.core.repository.implementations.chat.expert_chat_instance import ExpertChatInstanceRepository
from agentclaw.community.plugin_api.models import BotModel


class _CandidateDatabase:
    def __init__(self, engine):
        self._sessions = sessionmaker(bind=engine)

    @contextmanager
    def orm_session(self):
        with self._sessions.begin() as db:
            yield db


@pytest.fixture
def repo(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'caller_refresh.db'}")
    schema = MetaData()
    AcExpertChatInstance.__table__.to_metadata(schema)
    BotModel.__table__.to_metadata(schema)
    schema.create_all(engine)
    try:
        yield ExpertChatInstanceRepository(_CandidateDatabase(engine))
    finally:
        engine.dispose()


@pytest.fixture(autouse=True)
def environment(monkeypatch):
    monkeypatch.setattr(
        "agentclaw.community.core.repository.implementations.chat.expert_chat_instance.get_current_env",
        lambda: "prod",
    )


def insert(repo, user, *, status="success", end=None, env="prod"):
    row = repo.upsert_instance(user, "bot", "owner", status=status, ext={"binding_id": 7})
    with repo._db.orm_session() as db:
        record = db.query(repo.Model).filter(repo.Model.id == row["id"]).one()
        record.caller_token_expires_at = end
        record.env = env
    return row["id"]


def test_due_candidates_include_exact_cutoff_unknown_and_expired(repo, caplog):
    caplog.set_level("INFO")
    now = datetime.now(UTC).replace(tzinfo=None)
    cutoff = now + timedelta(hours=24)
    ids = [
        insert(repo, "unknown", status="init"),
        insert(repo, "expired", status="active", end=now - timedelta(hours=1)),
        insert(repo, "boundary", end=cutoff),
    ]
    insert(repo, "future", end=cutoff + timedelta(microseconds=1))
    insert(repo, "released", status="release")
    insert(repo, "failed", status="failed")
    insert(repo, "other-env", env="pre")
    rows = repo.list_caller_token_refresh_candidates(expires_before=cutoff.replace(tzinfo=UTC))
    assert [row["id"] for row in rows] == ids
    assert rows[0]["ext"] == {"binding_id": 7}
    assert rows[0]["env"] == "prod"
    assert rows[0]["owner_id"] == "owner"
    assert rows[0]["caller_token_expires_at"] is None
    assert rows[-1]["caller_token_expires_at"] == cutoff.replace(tzinfo=UTC).isoformat()
    assert "caller_refresh_candidates_requested" in caplog.text
    assert "caller_refresh_candidates_succeeded" in caplog.text


def test_keyset_page_cursor_survives_refreshed_rows_leaving_due_set(repo):
    cutoff = datetime.now(UTC) + timedelta(hours=24)
    ids = [insert(repo, f"user-{index}") for index in range(4)]
    first = repo.list_caller_token_refresh_candidates(expires_before=cutoff, limit=2)
    assert [row["id"] for row in first] == ids[:2]
    with repo._db.orm_session() as db:
        db.query(repo.Model).filter(repo.Model.id.in_(ids[:2])).update(
            {repo.Model.caller_token_expires_at: (cutoff + timedelta(days=1)).replace(tzinfo=None)},
            synchronize_session=False,
        )
    second = repo.list_caller_token_refresh_candidates(expires_before=cutoff, after_id=first[-1]["id"], limit=2)
    assert [row["id"] for row in second] == ids[2:]
    assert repo.list_caller_token_refresh_candidates(expires_before=cutoff, after_id=second[-1]["id"]) == []


def test_cutoff_is_normalized_to_utc(repo):
    end = datetime(2030, 1, 1, 1, 0)
    expected = insert(repo, "boundary", end=end)
    cutoff = datetime(2030, 1, 1, 9, 0, tzinfo=timezone(timedelta(hours=8)))
    assert [row["id"] for row in repo.list_caller_token_refresh_candidates(expires_before=cutoff)] == [expected]


@pytest.mark.parametrize("kwargs", [
    {"expires_before": datetime(2030, 1, 1)},
    {"expires_before": "2030-01-01"},
    {"after_id": -1}, {"after_id": True}, {"after_id": "1"},
    {"limit": 0}, {"limit": 1001}, {"limit": True}, {"limit": "1"},
])
def test_invalid_candidate_query_inputs_rejected(repo, kwargs):
    params = {"expires_before": datetime.now(UTC), **kwargs}
    with pytest.raises(ValueError):
        repo.list_caller_token_refresh_candidates(**params)


def test_candidate_query_failure_logs_safe_error_type(repo, monkeypatch, caplog):
    from contextlib import contextmanager

    @contextmanager
    def broken():
        raise RuntimeError("secret-material")
        yield

    monkeypatch.setattr(repo._db, "orm_session", broken)
    with pytest.raises(RuntimeError):
        repo.list_caller_token_refresh_candidates(expires_before=datetime.now(UTC))
    assert "caller_refresh_candidates_failed" in caplog.text
    assert "RuntimeError" in caplog.text
    assert "secret-material" not in caplog.text
